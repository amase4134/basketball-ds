"""Extract static player and team directories from nba_api."""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd
from nba_api.stats.static import players as static_players
from nba_api.stats.static import teams as static_teams

from nba_pipeline.client import with_retry
from nba_pipeline.config import Settings, settings
from nba_pipeline.load.parquet import (
    append_refresh_log,
    previous_row_count,
    write_parquet,
    write_static_table,
)
from nba_pipeline.transform.clean import clean_players, clean_teams, validate_players, warn_row_count_drop

logger = logging.getLogger(__name__)


def extract_players(*, cfg: Settings | None = None, dry_run: bool = False) -> pd.DataFrame:
    cfg = cfg or settings
    cfg.ensure_dirs()

    raw = with_retry(
        lambda: pd.DataFrame(static_players.get_players()),
        cfg=cfg,
        label="static.players",
    )
    if dry_run:
        logger.info("[dry-run] players: %s rows", len(raw))
        return raw

    pull_date = date.today().isoformat()
    write_parquet(raw, cfg.raw_dir / "players" / f"pull_date={pull_date}" / "players.parquet")

    curated = clean_players(raw)
    for warning in validate_players(curated):
        logger.warning(warning)

    prev = previous_row_count("players", None, cfg=cfg)
    write_static_table(curated, cfg.curated_dir / "players", "players")
    warn_row_count_drop("players", None, len(curated), prev)
    append_refresh_log(
        dataset="players",
        season=None,
        row_count=len(curated),
        status="ok",
        cfg=cfg,
    )
    logger.info("Wrote %s players", len(curated))
    return curated


def extract_teams(*, cfg: Settings | None = None, dry_run: bool = False) -> pd.DataFrame:
    cfg = cfg or settings
    cfg.ensure_dirs()

    raw = with_retry(
        lambda: pd.DataFrame(static_teams.get_teams()),
        cfg=cfg,
        label="static.teams",
    )
    if dry_run:
        logger.info("[dry-run] teams: %s rows", len(raw))
        return raw

    pull_date = date.today().isoformat()
    write_parquet(raw, cfg.raw_dir / "teams" / f"pull_date={pull_date}" / "teams.parquet")

    curated = clean_teams(raw)
    prev = previous_row_count("teams", None, cfg=cfg)
    write_static_table(curated, cfg.curated_dir / "teams", "teams")
    warn_row_count_drop("teams", None, len(curated), prev)
    append_refresh_log(
        dataset="teams",
        season=None,
        row_count=len(curated),
        status="ok",
        cfg=cfg,
    )
    logger.info("Wrote %s teams", len(curated))
    return curated
