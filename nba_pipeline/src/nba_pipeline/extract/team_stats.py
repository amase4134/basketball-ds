"""Extract team season stats via LeagueDashTeamStats (Base + Advanced)."""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd
from nba_api.stats.endpoints import leaguedashteamstats

from nba_pipeline.client import fetch_dataframe
from nba_pipeline.config import TEAM_STATS_COLUMNS, Settings, settings
from nba_pipeline.load.parquet import (
    append_refresh_log,
    previous_row_count,
    replace_season_partition,
    write_parquet,
)
from nba_pipeline.transform.clean import clean_team_season_stats, warn_row_count_drop

logger = logging.getLogger(__name__)


def _fetch_league_dash(season: str, measure_type: str, cfg: Settings) -> pd.DataFrame:
    return fetch_dataframe(
        lambda: leaguedashteamstats.LeagueDashTeamStats(
            season=season,
            season_type_all_star="Regular Season",
            per_mode_detailed="Per100Possessions",
            measure_type_detailed_defense=measure_type,
            timeout=cfg.request_timeout,
        ),
        cfg=cfg,
        label=f"LeagueDashTeamStats[{season}:{measure_type}]",
    )


def _fetch_season_stats(season: str, cfg: Settings) -> pd.DataFrame:
    """Merge per-100 box score rates with efficiency ratings and four factors."""
    base = _fetch_league_dash(season, "Base", cfg)
    advanced = _fetch_league_dash(season, "Advanced", cfg)
    if base.empty:
        return base

    if advanced.empty:
        logger.warning("Advanced pull empty for %s; keeping Base columns only", season)
        return base

    # Both frames carry identity columns; keep Base's and take ratings from Advanced.
    overlap = [c for c in advanced.columns if c in base.columns and c != "TEAM_ID"]
    return base.merge(advanced.drop(columns=overlap), on="TEAM_ID", how="left")


def _read_teams(cfg: Settings) -> pd.DataFrame:
    teams_path = cfg.curated_dir / "teams" / "teams.parquet"
    if not teams_path.exists():
        logger.warning("curated teams.parquet missing; team identity is unavailable")
        return pd.DataFrame()
    return pd.read_parquet(teams_path)


def _attach_abbreviations(df: pd.DataFrame, cfg: Settings) -> pd.DataFrame:
    """LeagueDashTeamStats omits TEAM_ABBREVIATION; the teams directory has it."""
    if df.empty or "TEAM_ABBREVIATION" in df.columns:
        return df
    teams = _read_teams(cfg)
    if teams.empty or "ABBREVIATION" not in teams.columns:
        return df
    lookup = teams[["TEAM_ID", "ABBREVIATION"]].rename(
        columns={"ABBREVIATION": "TEAM_ABBREVIATION"}
    )
    return df.merge(lookup, on="TEAM_ID", how="left")


def preseason_skeleton(cfg: Settings) -> pd.DataFrame:
    """A 30-team frame at zero games played, for a season that has not tipped off.

    The API returns no rows at all before opening night, but the tracker still
    needs every team present so pre-season projections have somewhere to land.
    """
    teams = _read_teams(cfg)
    if teams.empty:
        return pd.DataFrame()
    frame = pd.DataFrame(
        {
            "TEAM_ID": teams["TEAM_ID"].astype("int64"),
            "TEAM_NAME": teams.get("FULL_NAME"),
            "TEAM_ABBREVIATION": teams.get("ABBREVIATION"),
            "GP": 0,
            "W": 0,
            "L": 0,
            "W_PCT": float("nan"),
        }
    )
    # Carry the full schema so the partition stacks with completed seasons.
    for column in TEAM_STATS_COLUMNS:
        if column not in frame.columns and column != "SEASON":
            frame[column] = float("nan")
    return frame.sort_values("TEAM_ID").reset_index(drop=True)


def extract_team_season_stats(
    seasons: list[str] | None = None,
    *,
    cfg: Settings | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, int]:
    """Refresh team season stats partitions. Returns season → row count."""
    cfg = cfg or settings
    cfg.ensure_dirs()
    seasons = seasons or cfg.seasons
    counts: dict[str, int] = {}

    for season in seasons:
        partition = cfg.curated_dir / "team_season_stats" / f"season={season}"
        if partition.exists() and not force and not dry_run:
            existing = list(partition.glob("*.parquet"))
            if existing:
                logger.info("Skipping team_season_stats %s (exists; use --force)", season)
                counts[season] = sum(len(pd.read_parquet(p)) for p in existing)
                continue

        logger.info("Fetching team_season_stats for %s", season)
        try:
            raw = _fetch_season_stats(season, cfg)
        except Exception as exc:  # noqa: BLE001
            append_refresh_log(
                dataset="team_season_stats",
                season=season,
                row_count=0,
                status="error",
                message=str(exc),
                cfg=cfg,
            )
            raise

        if raw.empty:
            raw = preseason_skeleton(cfg)
            logger.info(
                "team_season_stats %s has not tipped off; writing %s teams at 0 games",
                season,
                len(raw),
            )

        if dry_run:
            logger.info("[dry-run] team_season_stats %s: %s rows", season, len(raw))
            counts[season] = len(raw)
            continue

        pull_date = date.today().isoformat()
        raw_path = (
            cfg.raw_dir
            / "team_season_stats"
            / f"season={season}"
            / f"pull_date={pull_date}"
            / "stats.parquet"
        )
        write_parquet(raw, raw_path)

        curated = clean_team_season_stats(_attach_abbreviations(raw, cfg), season)
        body = curated.drop(columns=["SEASON"], errors="ignore")
        prev = previous_row_count("team_season_stats", season, cfg=cfg)
        replace_season_partition(body, cfg.curated_dir / "team_season_stats", season)
        warn_row_count_drop("team_season_stats", season, len(body), prev)
        append_refresh_log(
            dataset="team_season_stats",
            season=season,
            row_count=len(body),
            status="ok",
            cfg=cfg,
        )
        counts[season] = len(body)
        logger.info("Wrote team_season_stats %s: %s rows", season, len(body))

    return counts


def load_curated_team_stats(season: str, cfg: Settings | None = None) -> pd.DataFrame:
    cfg = cfg or settings
    path = cfg.curated_dir / "team_season_stats" / f"season={season}" / "data.parquet"
    if not path.exists():
        raise FileNotFoundError(f"Missing curated team_season_stats for {season}: {path}")
    df = pd.read_parquet(path)
    df["SEASON"] = season
    return df


def load_curated_team_stats_many(
    seasons: list[str],
    cfg: Settings | None = None,
) -> pd.DataFrame:
    """Stack several season partitions, skipping any that are not on disk yet."""
    frames = []
    for season in seasons:
        try:
            frames.append(load_curated_team_stats(season, cfg=cfg))
        except FileNotFoundError:
            logger.warning("team_season_stats missing for %s; skipping", season)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)
