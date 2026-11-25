"""Extract regular-season team game logs (game index) via LeagueGameLog.

Phase 2 dataset: one row per team-game. Provides GAME_ID ↔ teams / outcomes for
joining play-by-play. Not pulled by the default daily refresh; request explicitly
with ``--dataset game_logs`` or ``--include-phase2``.
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd
from nba_api.stats.endpoints import leaguegamelog

from nba_pipeline.client import fetch_dataframe
from nba_pipeline.config import Settings, settings
from nba_pipeline.load.parquet import (
    append_refresh_log,
    previous_row_count,
    replace_season_partition,
    write_parquet,
)
from nba_pipeline.transform.clean import clean_game_logs, warn_row_count_drop

logger = logging.getLogger(__name__)


def _fetch_team_game_log(season: str, cfg: Settings) -> pd.DataFrame:
    return fetch_dataframe(
        lambda: leaguegamelog.LeagueGameLog(
            season=season,
            season_type_all_star="Regular Season",
            player_or_team_abbreviation="T",
            timeout=cfg.request_timeout,
        ),
        cfg=cfg,
        label=f"LeagueGameLog[{season}:T]",
    )


def extract_game_logs(
    seasons: list[str] | None = None,
    *,
    cfg: Settings | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, int]:
    """Refresh team game-log partitions. Returns season → row count."""
    cfg = cfg or settings
    cfg.ensure_dirs()
    seasons = seasons or cfg.seasons
    counts: dict[str, int] = {}

    for season in seasons:
        partition = cfg.curated_dir / "game_logs" / f"season={season}"
        if partition.exists() and not force and not dry_run:
            existing = list(partition.glob("*.parquet"))
            if existing:
                logger.info("Skipping game_logs %s (exists; use --force)", season)
                counts[season] = sum(len(pd.read_parquet(p)) for p in existing)
                continue

        logger.info("Fetching game_logs for %s", season)
        try:
            raw = _fetch_team_game_log(season, cfg)
        except Exception as exc:  # noqa: BLE001
            append_refresh_log(
                dataset="game_logs",
                season=season,
                row_count=0,
                status="error",
                message=str(exc),
                cfg=cfg,
            )
            raise

        if dry_run:
            logger.info("[dry-run] game_logs %s: %s rows", season, len(raw))
            counts[season] = len(raw)
            continue

        if raw.empty:
            # Pre-opening-night seasons have no games yet; treat like a settled skip.
            append_refresh_log(
                dataset="game_logs",
                season=season,
                row_count=0,
                status="skipped",
                message="no regular-season games yet",
                cfg=cfg,
            )
            counts[season] = 0
            logger.info("No game_logs for %s yet", season)
            continue

        pull_date = date.today().isoformat()
        write_parquet(
            raw,
            cfg.raw_dir
            / "game_logs"
            / f"season={season}"
            / f"pull_date={pull_date}"
            / "games.parquet",
        )

        curated = clean_game_logs(raw, season)
        body = curated.drop(columns=["SEASON"], errors="ignore")
        prev = previous_row_count("game_logs", season, cfg=cfg)
        replace_season_partition(body, cfg.curated_dir / "game_logs", season)
        warn_row_count_drop("game_logs", season, len(body), prev)
        append_refresh_log(
            dataset="game_logs",
            season=season,
            row_count=len(body),
            status="ok",
            cfg=cfg,
        )
        counts[season] = len(body)
        logger.info("Wrote game_logs %s: %s team-game rows", season, len(body))

    return counts


def load_curated_game_logs(season: str, cfg: Settings | None = None) -> pd.DataFrame:
    cfg = cfg or settings
    path = cfg.curated_dir / "game_logs" / f"season={season}" / "data.parquet"
    if not path.exists():
        raise FileNotFoundError(f"Missing curated game_logs for {season}: {path}")
    df = pd.read_parquet(path)
    df["SEASON"] = season
    return df


def unique_game_ids(season: str, cfg: Settings | None = None) -> list[str]:
    """Distinct GAME_IDs for a season, sorted for stable PBP pulls."""
    logs = load_curated_game_logs(season, cfg=cfg)
    if logs.empty or "GAME_ID" not in logs.columns:
        return []
    ids = logs["GAME_ID"].astype(str).drop_duplicates().sort_values()
    return ids.tolist()
