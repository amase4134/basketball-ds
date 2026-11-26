"""Extract regular-season team game logs (game index) via LeagueGameLog.

Phase 2 dataset: one row per team-game. Provides GAME_ID ↔ teams / outcomes for
joining play-by-play. Not pulled by the default daily refresh; request explicitly
with ``--dataset game_logs`` or ``--include-phase2``.

When ``stats.nba.com`` is unreachable (common from some cloud egress paths),
falls back to ``api.pbpstats.com/get-games`` and maps into the same curated
schema so the game index can still land.
"""

from __future__ import annotations

import logging
import time
from datetime import date

import pandas as pd
import requests
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

PBPSTATS_GAMES_URL = "https://api.pbpstats.com/get-games/nba"


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


def _team_name_lookup(cfg: Settings) -> dict[int, str]:
    path = cfg.curated_dir / "teams" / "teams.parquet"
    if not path.exists():
        return {}
    teams = pd.read_parquet(path)
    if not {"TEAM_ID", "FULL_NAME"} <= set(teams.columns):
        return {}
    return {
        int(team_id): str(name)
        for team_id, name in zip(teams["TEAM_ID"], teams["FULL_NAME"])
    }


def game_logs_from_pbpstats_games(
    games: list[dict],
    *,
    team_names: dict[int, str] | None = None,
) -> pd.DataFrame:
    """Map pbpstats ``get-games`` rows into LeagueGameLog-shaped team-game rows."""
    team_names = team_names or {}
    rows: list[dict] = []
    for game in games:
        game_id = str(game["GameId"])
        game_date = str(game["Date"])
        home_id = int(game["HomeTeamId"])
        away_id = int(game["AwayTeamId"])
        home_abbr = str(game["HomeTeamAbbreviation"])
        away_abbr = str(game["AwayTeamAbbreviation"])
        home_pts = int(game["HomePoints"])
        away_pts = int(game["AwayPoints"])
        rows.append(
            {
                "GAME_ID": game_id,
                "GAME_DATE": game_date,
                "TEAM_ID": home_id,
                "TEAM_ABBREVIATION": home_abbr,
                "TEAM_NAME": team_names.get(home_id, home_abbr),
                "MATCHUP": f"{home_abbr} vs. {away_abbr}",
                "WL": "W" if home_pts > away_pts else "L",
                "PTS": home_pts,
                "PLUS_MINUS": home_pts - away_pts,
            }
        )
        rows.append(
            {
                "GAME_ID": game_id,
                "GAME_DATE": game_date,
                "TEAM_ID": away_id,
                "TEAM_ABBREVIATION": away_abbr,
                "TEAM_NAME": team_names.get(away_id, away_abbr),
                "MATCHUP": f"{away_abbr} @ {home_abbr}",
                "WL": "W" if away_pts > home_pts else "L",
                "PTS": away_pts,
                "PLUS_MINUS": away_pts - home_pts,
            }
        )
    return pd.DataFrame(rows)


def _fetch_pbpstats_game_index(season: str, cfg: Settings) -> pd.DataFrame:
    """Fallback game index when LeagueGameLog cannot reach stats.nba.com."""
    params = {"Season": season, "SeasonType": "Regular Season"}
    headers = {
        "User-Agent": "nba-pipeline/0.1 (+phase2-fallback)",
        "Accept": "application/json",
    }
    logger.warning(
        "LeagueGameLog unavailable; falling back to pbpstats get-games for %s",
        season,
    )
    response = requests.get(
        PBPSTATS_GAMES_URL,
        params=params,
        headers=headers,
        timeout=cfg.request_timeout,
    )
    response.raise_for_status()
    payload = response.json()
    games = payload.get("results") or []
    time.sleep(cfg.request_sleep_seconds)
    return game_logs_from_pbpstats_games(games, team_names=_team_name_lookup(cfg))


def _fetch_game_logs_with_fallback(season: str, cfg: Settings) -> tuple[pd.DataFrame, str]:
    # Fail over quickly: a blocked stats.nba.com path otherwise burns ~5 minutes
    # of retries before pbpstats can serve the game index.
    from dataclasses import replace

    from nba_pipeline.client import probe_stats_nba

    probe_error = probe_stats_nba(timeout=min(20.0, cfg.request_timeout))
    if probe_error:
        logger.warning(
            "stats.nba.com probe failed (%s); skipping LeagueGameLog for %s",
            probe_error,
            season,
        )
        raw = _fetch_pbpstats_game_index(season, cfg)
        return raw, f"pbpstats.get-games (stats.nba.com: {probe_error})"

    quick = replace(
        cfg,
        max_retries=min(cfg.max_retries, 2),
        request_timeout=min(cfg.request_timeout, 45.0),
    )
    try:
        raw = _fetch_team_game_log(season, quick)
        return raw, "nba_api.LeagueGameLog"
    except Exception as primary:  # noqa: BLE001
        logger.warning("LeagueGameLog failed for %s: %s", season, primary)
        try:
            raw = _fetch_pbpstats_game_index(season, cfg)
            return raw, "pbpstats.get-games"
        except Exception as fallback:  # noqa: BLE001
            raise RuntimeError(
                f"game_logs unavailable for {season}: "
                f"LeagueGameLog={primary}; pbpstats={fallback}"
            ) from fallback


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
            raw, source = _fetch_game_logs_with_fallback(season, cfg)
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
            logger.info(
                "[dry-run] game_logs %s: %s rows (source=%s)",
                season,
                len(raw),
                source,
            )
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
            message=f"source={source}",
            cfg=cfg,
        )
        counts[season] = len(body)
        logger.info(
            "Wrote game_logs %s: %s team-game rows (source=%s)",
            season,
            len(body),
            source,
        )

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
