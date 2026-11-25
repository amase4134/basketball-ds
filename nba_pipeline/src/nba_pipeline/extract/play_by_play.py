"""Extract regular-season play-by-play via PlayByPlayV2.

Phase 2 dataset: one row per PBP event, keyed by GAME_ID. Depends on curated
``game_logs`` for the season's game index. Not pulled by the default daily
refresh; request explicitly with ``--dataset play_by_play`` or
``--include-phase2``.

A full season is ~1,230 games and one API call per game — prefer
``--max-pbp-games`` when testing, and respect client rate limits.
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd
from nba_api.stats.endpoints import playbyplayv2

from nba_pipeline.client import fetch_dataframe
from nba_pipeline.config import Settings, settings
from nba_pipeline.extract.game_logs import extract_game_logs, unique_game_ids
from nba_pipeline.load.parquet import (
    append_refresh_log,
    previous_row_count,
    replace_season_partition,
    write_parquet,
)
from nba_pipeline.transform.clean import clean_play_by_play, warn_row_count_drop

logger = logging.getLogger(__name__)


def _fetch_game_pbp(game_id: str, cfg: Settings) -> pd.DataFrame:
    return fetch_dataframe(
        lambda: playbyplayv2.PlayByPlayV2(
            game_id=game_id,
            timeout=cfg.request_timeout,
        ),
        cfg=cfg,
        label=f"PlayByPlayV2[{game_id}]",
    )


def extract_play_by_play(
    seasons: list[str] | None = None,
    *,
    cfg: Settings | None = None,
    force: bool = False,
    dry_run: bool = False,
    max_games: int | None = None,
) -> dict[str, int]:
    """Refresh play-by-play partitions for games listed in curated game_logs."""
    cfg = cfg or settings
    cfg.ensure_dirs()
    seasons = seasons or cfg.seasons
    counts: dict[str, int] = {}

    for season in seasons:
        partition = cfg.curated_dir / "play_by_play" / f"season={season}"
        if partition.exists() and not force and not dry_run:
            existing = list(partition.glob("*.parquet"))
            if existing:
                logger.info("Skipping play_by_play %s (exists; use --force)", season)
                counts[season] = sum(len(pd.read_parquet(p)) for p in existing)
                continue

        game_log_path = (
            cfg.curated_dir / "game_logs" / f"season={season}" / "data.parquet"
        )
        if not game_log_path.exists():
            logger.info(
                "game_logs missing for %s; extracting game index before PBP", season
            )
            extract_game_logs([season], cfg=cfg, force=force, dry_run=dry_run)

        try:
            game_ids = unique_game_ids(season, cfg=cfg)
        except FileNotFoundError:
            append_refresh_log(
                dataset="play_by_play",
                season=season,
                row_count=0,
                status="skipped",
                message="no curated game_logs for this season",
                cfg=cfg,
            )
            counts[season] = 0
            logger.info("No play_by_play for %s: game_logs unavailable", season)
            continue

        if max_games is not None:
            game_ids = game_ids[:max_games]

        logger.info(
            "Fetching play_by_play for %s (%s games)",
            season,
            len(game_ids),
        )

        if dry_run:
            logger.info(
                "[dry-run] would pull %s games for play_by_play %s",
                len(game_ids),
                season,
            )
            counts[season] = 0
            continue

        if not game_ids:
            append_refresh_log(
                dataset="play_by_play",
                season=season,
                row_count=0,
                status="skipped",
                message="no games in game_logs yet",
                cfg=cfg,
            )
            counts[season] = 0
            logger.info("No play_by_play for %s yet: empty game index", season)
            continue

        frames: list[pd.DataFrame] = []
        errors = 0
        for i, game_id in enumerate(game_ids, start=1):
            try:
                raw = _fetch_game_pbp(game_id, cfg)
                if not raw.empty:
                    frames.append(raw)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                logger.warning(
                    "Play-by-play failed for game %s (%s): %s", game_id, season, exc
                )
            if i % 50 == 0:
                logger.info(
                    "  progress %s/%s games for %s", i, len(game_ids), season
                )

        if not frames:
            append_refresh_log(
                dataset="play_by_play",
                season=season,
                row_count=0,
                status="error",
                message=f"no PBP rows for {len(game_ids)} games; errors={errors}",
                cfg=cfg,
            )
            counts[season] = 0
            logger.error("No play_by_play data for %s", season)
            continue

        raw_all = pd.concat(frames, ignore_index=True)
        pull_date = date.today().isoformat()
        write_parquet(
            raw_all,
            cfg.raw_dir
            / "play_by_play"
            / f"season={season}"
            / f"pull_date={pull_date}"
            / "pbp.parquet",
        )

        curated = clean_play_by_play(raw_all, season)
        body = curated.drop(columns=["SEASON"], errors="ignore")
        prev = previous_row_count("play_by_play", season, cfg=cfg)
        replace_season_partition(body, cfg.curated_dir / "play_by_play", season)
        warn_row_count_drop("play_by_play", season, len(body), prev)
        append_refresh_log(
            dataset="play_by_play",
            season=season,
            row_count=len(body),
            status="ok",
            message=f"games={len(game_ids)}; errors={errors}",
            cfg=cfg,
        )
        counts[season] = len(body)
        logger.info(
            "Wrote play_by_play %s: %s rows (%s errors)", season, len(body), errors
        )

    return counts


def load_curated_play_by_play(season: str, cfg: Settings | None = None) -> pd.DataFrame:
    cfg = cfg or settings
    path = cfg.curated_dir / "play_by_play" / f"season={season}" / "data.parquet"
    if not path.exists():
        raise FileNotFoundError(f"Missing curated play_by_play for {season}: {path}")
    df = pd.read_parquet(path)
    df["SEASON"] = season
    return df
