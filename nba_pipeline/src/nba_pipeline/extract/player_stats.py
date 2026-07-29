"""Extract season player stats via LeagueDashPlayerStats."""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd
from nba_api.stats.endpoints import leaguedashplayerstats

from nba_pipeline.client import fetch_dataframe
from nba_pipeline.config import Settings, settings
from nba_pipeline.load.parquet import (
    append_refresh_log,
    previous_row_count,
    replace_season_partition,
    write_parquet,
)
from nba_pipeline.transform.clean import clean_player_season_stats, warn_row_count_drop

logger = logging.getLogger(__name__)


def _fetch_league_dash(season: str, per_mode: str, cfg: Settings) -> pd.DataFrame:
    return fetch_dataframe(
        lambda: leaguedashplayerstats.LeagueDashPlayerStats(
            season=season,
            season_type_all_star="Regular Season",
            per_mode_detailed=per_mode,
            measure_type_detailed_defense="Base",
            timeout=cfg.request_timeout,
        ),
        cfg=cfg,
        label=f"LeagueDashPlayerStats[{season}:{per_mode}]",
    )


def _fetch_season_stats(season: str, cfg: Settings) -> pd.DataFrame:
    """Per-100 rate stats with total minutes merged from Totals mode.

    Per100Possessions reports a scaled MIN (~48) that is useless for a
    season minutes filter; Totals supplies real cumulative minutes.
    """
    per100 = _fetch_league_dash(season, "Per100Possessions", cfg)
    totals = _fetch_league_dash(season, "Totals", cfg)
    if totals.empty or "MIN" not in totals.columns:
        logger.warning("Totals pull missing MIN for %s; keeping Per100 MIN", season)
        return per100

    min_map = (
        totals[["PLAYER_ID", "TEAM_ID", "MIN"]]
        .rename(columns={"MIN": "MIN_TOTAL"})
        .drop_duplicates(subset=["PLAYER_ID", "TEAM_ID"])
    )
    merged = per100.merge(min_map, on=["PLAYER_ID", "TEAM_ID"], how="left")
    if "MIN" in merged.columns:
        merged = merged.drop(columns=["MIN"])
    merged = merged.rename(columns={"MIN_TOTAL": "MIN"})
    return merged


def extract_player_season_stats(
    seasons: list[str] | None = None,
    *,
    cfg: Settings | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, int]:
    """Refresh player season stats partitions. Returns season → row count."""
    cfg = cfg or settings
    cfg.ensure_dirs()
    seasons = seasons or cfg.seasons
    counts: dict[str, int] = {}

    for season in seasons:
        partition = cfg.curated_dir / "player_season_stats" / f"season={season}"
        if partition.exists() and not force and not dry_run:
            existing = list(partition.glob("*.parquet"))
            if existing:
                logger.info("Skipping player_season_stats %s (exists; use --force)", season)
                counts[season] = sum(len(pd.read_parquet(p)) for p in existing)
                continue

        logger.info("Fetching player_season_stats for %s", season)
        try:
            raw = _fetch_season_stats(season, cfg)
        except Exception as exc:  # noqa: BLE001
            append_refresh_log(
                dataset="player_season_stats",
                season=season,
                row_count=0,
                status="error",
                message=str(exc),
                cfg=cfg,
            )
            raise

        if dry_run:
            logger.info("[dry-run] player_season_stats %s: %s rows", season, len(raw))
            counts[season] = len(raw)
            continue

        pull_date = date.today().isoformat()
        raw_path = (
            cfg.raw_dir
            / "player_season_stats"
            / f"season={season}"
            / f"pull_date={pull_date}"
            / "stats.parquet"
        )
        write_parquet(raw, raw_path)

        curated = clean_player_season_stats(raw, season)
        body = curated.drop(columns=["SEASON"], errors="ignore")
        prev = previous_row_count("player_season_stats", season, cfg=cfg)
        replace_season_partition(body, cfg.curated_dir / "player_season_stats", season)
        warn_row_count_drop("player_season_stats", season, len(body), prev)
        append_refresh_log(
            dataset="player_season_stats",
            season=season,
            row_count=len(body),
            status="ok",
            cfg=cfg,
        )
        counts[season] = len(body)
        logger.info("Wrote player_season_stats %s: %s rows", season, len(body))

    return counts


def load_curated_player_stats(season: str, cfg: Settings | None = None) -> pd.DataFrame:
    cfg = cfg or settings
    path = cfg.curated_dir / "player_season_stats" / f"season={season}" / "data.parquet"
    if not path.exists():
        raise FileNotFoundError(f"Missing curated player_season_stats for {season}: {path}")
    df = pd.read_parquet(path)
    df["SEASON"] = season
    return df


def eligible_player_ids(
    season: str,
    *,
    min_minutes: int | None = None,
    cfg: Settings | None = None,
) -> list[int]:
    cfg = cfg or settings
    min_minutes = cfg.min_minutes if min_minutes is None else min_minutes
    df = load_curated_player_stats(season, cfg=cfg)
    if "MIN" not in df.columns:
        raise ValueError("player_season_stats missing MIN column")
    eligible = df.loc[df["MIN"] >= min_minutes, "PLAYER_ID"].dropna().astype(int).unique()
    return sorted(int(x) for x in eligible)
