"""Extract shot chart detail for eligible players."""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd
from nba_api.stats.endpoints import shotchartdetail

from nba_pipeline.client import fetch_dataframe
from nba_pipeline.config import Settings, settings
from nba_pipeline.extract.player_stats import eligible_player_ids
from nba_pipeline.load.parquet import (
    append_refresh_log,
    previous_row_count,
    replace_season_partition,
    write_parquet,
)
from nba_pipeline.transform.clean import (
    clean_shot_charts,
    validate_shot_charts,
    warn_row_count_drop,
)

logger = logging.getLogger(__name__)


def _fetch_player_shots(player_id: int, season: str, cfg: Settings) -> pd.DataFrame:
    return fetch_dataframe(
        lambda: shotchartdetail.ShotChartDetail(
            team_id=0,
            player_id=player_id,
            context_measure_simple="FGA",
            season_nullable=season,
            season_type_all_star="Regular Season",
            timeout=cfg.request_timeout,
        ),
        cfg=cfg,
        label=f"ShotChartDetail[{season}:{player_id}]",
    )


def extract_shot_charts(
    seasons: list[str] | None = None,
    *,
    cfg: Settings | None = None,
    force: bool = False,
    dry_run: bool = False,
    min_minutes: int | None = None,
    max_players: int | None = None,
) -> dict[str, int]:
    """Refresh shot chart partitions for players meeting the minutes filter."""
    cfg = cfg or settings
    cfg.ensure_dirs()
    seasons = seasons or cfg.seasons
    min_minutes = cfg.min_minutes if min_minutes is None else min_minutes
    counts: dict[str, int] = {}

    for season in seasons:
        partition = cfg.curated_dir / "shot_charts" / f"season={season}"
        if partition.exists() and not force and not dry_run:
            existing = list(partition.glob("*.parquet"))
            if existing:
                logger.info("Skipping shot_charts %s (exists; use --force)", season)
                counts[season] = sum(len(pd.read_parquet(p)) for p in existing)
                continue

        player_ids = eligible_player_ids(season, min_minutes=min_minutes, cfg=cfg)
        if max_players is not None:
            player_ids = player_ids[:max_players]

        logger.info(
            "Fetching shot_charts for %s (%s players, min_minutes=%s)",
            season,
            len(player_ids),
            min_minutes,
        )

        if dry_run:
            logger.info("[dry-run] would pull %s players for shot_charts %s", len(player_ids), season)
            counts[season] = 0
            continue

        frames: list[pd.DataFrame] = []
        errors = 0
        for i, player_id in enumerate(player_ids, start=1):
            try:
                raw = _fetch_player_shots(player_id, season, cfg)
                if not raw.empty:
                    frames.append(raw)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                logger.warning("Shot chart failed for player %s (%s): %s", player_id, season, exc)
            if i % 25 == 0:
                logger.info("  progress %s/%s players for %s", i, len(player_ids), season)

        if not frames:
            append_refresh_log(
                dataset="shot_charts",
                season=season,
                row_count=0,
                status="error",
                message=f"no shot rows; errors={errors}",
                cfg=cfg,
            )
            counts[season] = 0
            logger.error("No shot chart data for %s", season)
            continue

        raw_all = pd.concat(frames, ignore_index=True)
        pull_date = date.today().isoformat()
        write_parquet(
            raw_all,
            cfg.raw_dir
            / "shot_charts"
            / f"season={season}"
            / f"pull_date={pull_date}"
            / "shots.parquet",
        )

        curated = clean_shot_charts(raw_all, season)
        for warning in validate_shot_charts(curated):
            logger.warning(warning)

        body = curated.drop(columns=["SEASON"], errors="ignore")
        prev = previous_row_count("shot_charts", season, cfg=cfg)
        replace_season_partition(body, cfg.curated_dir / "shot_charts", season)
        warn_row_count_drop("shot_charts", season, len(body), prev)
        append_refresh_log(
            dataset="shot_charts",
            season=season,
            row_count=len(body),
            status="ok",
            message=f"players={len(player_ids)}; errors={errors}",
            cfg=cfg,
        )
        counts[season] = len(body)
        logger.info("Wrote shot_charts %s: %s rows (%s errors)", season, len(body), errors)

    return counts
