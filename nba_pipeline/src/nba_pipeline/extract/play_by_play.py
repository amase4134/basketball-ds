"""Extract regular-season play-by-play via PlayByPlayV2.

Phase 2 dataset: one row per PBP event, keyed by GAME_ID. Depends on curated
``game_logs`` for the season's game index. Not pulled by the default daily
refresh; request explicitly with ``--dataset play_by_play`` or
``--include-phase2``.

A full season is ~1,230 games and one API call per game — prefer
``--max-pbp-games`` when testing, and respect client rate limits.

Resume: without ``--force``, games already present in the curated partition are
skipped so a stalled pull can continue. Partitions are rewritten after each
batch of successful fetches.
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd
from nba_api.stats.endpoints import playbyplayv2

from nba_pipeline.client import fetch_dataframe, probe_stats_nba
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

# Persist progress every N successful new games so a mid-season abort is resumable.
_CHECKPOINT_EVERY = 25


def _fetch_game_pbp(game_id: str, cfg: Settings) -> pd.DataFrame:
    return fetch_dataframe(
        lambda: playbyplayv2.PlayByPlayV2(
            game_id=game_id,
            timeout=cfg.request_timeout,
        ),
        cfg=cfg,
        label=f"PlayByPlayV2[{game_id}]",
    )


def _load_existing_pbp(season: str, cfg: Settings) -> pd.DataFrame:
    path = cfg.curated_dir / "play_by_play" / f"season={season}" / "data.parquet"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


def _write_pbp_partition(
    body: pd.DataFrame,
    *,
    season: str,
    cfg: Settings,
    game_count: int,
    errors: int,
    status: str = "ok",
) -> None:
    prev = previous_row_count("play_by_play", season, cfg=cfg)
    replace_season_partition(body, cfg.curated_dir / "play_by_play", season)
    warn_row_count_drop("play_by_play", season, len(body), prev)
    append_refresh_log(
        dataset="play_by_play",
        season=season,
        row_count=len(body),
        status=status,
        message=f"games={game_count}; errors={errors}",
        cfg=cfg,
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
        game_log_path = (
            cfg.curated_dir / "game_logs" / f"season={season}" / "data.parquet"
        )
        if not game_log_path.exists():
            logger.info(
                "game_logs missing for %s; extracting game index before PBP", season
            )
            extract_game_logs([season], cfg=cfg, force=force, dry_run=dry_run)

        try:
            all_game_ids = unique_game_ids(season, cfg=cfg)
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

        existing = pd.DataFrame() if force else _load_existing_pbp(season, cfg)
        have: set[str] = set()
        if not existing.empty and "GAME_ID" in existing.columns:
            have = set(existing["GAME_ID"].astype(str))

        pending = [gid for gid in all_game_ids if gid not in have]
        if max_games is not None:
            pending = pending[:max_games]

        if not pending and not existing.empty and not force:
            logger.info(
                "play_by_play %s already complete (%s games on disk)",
                season,
                len(have),
            )
            counts[season] = len(existing)
            continue

        logger.info(
            "Fetching play_by_play for %s (%s pending of %s games; have=%s)",
            season,
            len(pending),
            len(all_game_ids),
            len(have),
        )

        if pending and not dry_run:
            probe_error = probe_stats_nba(timeout=min(20.0, cfg.request_timeout))
            if probe_error:
                message = (
                    f"stats.nba.com unreachable ({probe_error}); "
                    f"cannot fetch PlayByPlayV2. Resume later from a network "
                    f"that can reach stats.nba.com — pending={len(pending)} "
                    f"have={len(have)}"
                )
                append_refresh_log(
                    dataset="play_by_play",
                    season=season,
                    row_count=len(existing),
                    status="error",
                    message=message,
                    cfg=cfg,
                )
                counts[season] = len(existing)
                logger.error("%s", message)
                continue

        if dry_run:
            logger.info(
                "[dry-run] would pull %s games for play_by_play %s",
                len(pending),
                season,
            )
            counts[season] = len(existing)
            continue

        if not pending and existing.empty:
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

        frames: list[pd.DataFrame] = [] if existing.empty else [existing]
        errors = 0
        fetched = 0
        for i, game_id in enumerate(pending, start=1):
            try:
                raw = _fetch_game_pbp(game_id, cfg)
                if not raw.empty:
                    frames.append(raw)
                    fetched += 1
            except Exception as exc:  # noqa: BLE001
                errors += 1
                logger.warning(
                    "Play-by-play failed for game %s (%s): %s", game_id, season, exc
                )
            if i % 50 == 0:
                logger.info(
                    "  progress %s/%s pending games for %s",
                    i,
                    len(pending),
                    season,
                )
            # Checkpoint so a later timeout does not discard successful games.
            if fetched and fetched % _CHECKPOINT_EVERY == 0:
                raw_all = pd.concat(frames, ignore_index=True)
                curated = clean_play_by_play(raw_all, season)
                body = curated.drop(columns=["SEASON"], errors="ignore")
                _write_pbp_partition(
                    body,
                    season=season,
                    cfg=cfg,
                    game_count=body["GAME_ID"].nunique() if "GAME_ID" in body else 0,
                    errors=errors,
                )
                frames = [body]
                logger.info(
                    "Checkpointed play_by_play %s: %s rows so far",
                    season,
                    len(body),
                )

        if not frames or (existing.empty and fetched == 0 and errors == len(pending)):
            append_refresh_log(
                dataset="play_by_play",
                season=season,
                row_count=len(existing),
                status="error",
                message=(
                    f"no new PBP rows; pending={len(pending)}; errors={errors}"
                ),
                cfg=cfg,
            )
            counts[season] = len(existing)
            logger.error("No play_by_play data fetched for %s", season)
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
        game_count = int(body["GAME_ID"].nunique()) if "GAME_ID" in body.columns else 0
        status = "ok" if errors == 0 else "ok"
        message_extra = f"games={game_count}; errors={errors}; fetched={fetched}"
        prev = previous_row_count("play_by_play", season, cfg=cfg)
        replace_season_partition(body, cfg.curated_dir / "play_by_play", season)
        warn_row_count_drop("play_by_play", season, len(body), prev)
        append_refresh_log(
            dataset="play_by_play",
            season=season,
            row_count=len(body),
            status=status,
            message=message_extra,
            cfg=cfg,
        )
        counts[season] = len(body)
        logger.info(
            "Wrote play_by_play %s: %s rows across %s games (%s errors, %s newly fetched)",
            season,
            len(body),
            game_count,
            errors,
            fetched,
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
