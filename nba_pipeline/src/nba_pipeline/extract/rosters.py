"""Extract current team rosters via CommonAllPlayers.

The endpoint only knows where players are *now*, so a roster snapshot can only
be taken for the season underway. Completed seasons take their rosters from
`player_season_stats` instead (see `load_season_roster`).
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd
from nba_api.stats.endpoints import commonallplayers

from nba_pipeline.client import fetch_dataframe
from nba_pipeline.config import Settings, settings
from nba_pipeline.extract.player_stats import load_curated_player_stats
from nba_pipeline.load.parquet import (
    append_refresh_log,
    previous_row_count,
    replace_season_partition,
    write_parquet,
)
from nba_pipeline.transform.clean import clean_rosters, warn_row_count_drop

logger = logging.getLogger(__name__)

ROSTER_COLUMNS = ["PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "TEAM_ABBREVIATION"]


def _fetch_current_rosters(season: str, cfg: Settings) -> pd.DataFrame:
    return fetch_dataframe(
        lambda: commonallplayers.CommonAllPlayers(
            season=season,
            is_only_current_season=1,
            league_id="00",
            timeout=cfg.request_timeout,
        ),
        cfg=cfg,
        label=f"CommonAllPlayers[{season}]",
    )


def extract_rosters(
    seasons: list[str] | None = None,
    *,
    cfg: Settings | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Snapshot rosters for the target season. Returns season → row count."""
    cfg = cfg or settings
    cfg.ensure_dirs()
    seasons = seasons or [cfg.target_season]
    counts: dict[str, int] = {}

    for season in seasons:
        if season != cfg.target_season:
            logger.info(
                "Skipping rosters %s: only the current season can be snapshotted", season
            )
            continue

        logger.info("Fetching rosters for %s", season)
        try:
            raw = _fetch_current_rosters(season, cfg)
        except Exception as exc:  # noqa: BLE001
            append_refresh_log(
                dataset="rosters",
                season=season,
                row_count=0,
                status="error",
                message=str(exc),
                cfg=cfg,
            )
            raise

        if dry_run:
            logger.info("[dry-run] rosters %s: %s rows", season, len(raw))
            counts[season] = len(raw)
            continue

        pull_date = date.today().isoformat()
        write_parquet(
            raw,
            cfg.raw_dir
            / "rosters"
            / f"season={season}"
            / f"pull_date={pull_date}"
            / "rosters.parquet",
        )

        body = clean_rosters(raw)
        prev = previous_row_count("rosters", season, cfg=cfg)
        replace_season_partition(body, cfg.curated_dir / "rosters", season)
        warn_row_count_drop("rosters", season, len(body), prev)
        append_refresh_log(
            dataset="rosters",
            season=season,
            row_count=len(body),
            status="ok",
            cfg=cfg,
        )
        counts[season] = len(body)
        logger.info("Wrote rosters %s: %s players", season, len(body))

    return counts


def load_curated_rosters(season: str, cfg: Settings | None = None) -> pd.DataFrame:
    cfg = cfg or settings
    path = cfg.curated_dir / "rosters" / f"season={season}" / "data.parquet"
    if not path.exists():
        raise FileNotFoundError(f"Missing curated rosters for {season}: {path}")
    return pd.read_parquet(path)


def load_season_roster(season: str, cfg: Settings | None = None) -> pd.DataFrame:
    """Which team each player belongs to in `season`.

    A roster snapshot wins when one exists. Otherwise the team a player finished
    the season with is read from `player_season_stats`, which lists each player
    once under his last team. Returns an empty frame when neither is on disk.
    """
    cfg = cfg or settings
    try:
        roster = load_curated_rosters(season, cfg=cfg)
    except FileNotFoundError:
        try:
            roster = load_curated_player_stats(season, cfg=cfg)
        except FileNotFoundError:
            return pd.DataFrame(columns=["SEASON", *ROSTER_COLUMNS])
    roster = roster.reindex(columns=ROSTER_COLUMNS).dropna(subset=["PLAYER_ID", "TEAM_ID"])
    roster = roster[roster["TEAM_ID"] != 0].copy()
    roster["PLAYER_ID"] = roster["PLAYER_ID"].astype("int64")
    roster["TEAM_ID"] = roster["TEAM_ID"].astype("int64")
    roster.insert(0, "SEASON", season)
    return roster.reset_index(drop=True)
