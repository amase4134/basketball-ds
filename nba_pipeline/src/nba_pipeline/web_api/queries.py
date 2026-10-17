"""Validated, parameterized query helpers for the local Explorer API."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from nba_pipeline.config import Settings
from nba_pipeline.load.duckdb_views import connect

PLAYER_COLUMNS = "PLAYER_ID, PLAYER_NAME, TEAM_ID, TEAM_ABBREVIATION, AGE, GP, MIN, FGM, FGA, FG_PCT, FG3M, FG3A, FG3_PCT, REB, AST, TOV, STL, BLK, PTS, PLUS_MINUS, season"
PLAYER_SORTS = {c: c for c in "PLAYER_NAME TEAM_ABBREVIATION GP MIN PTS FGM FGA FG_PCT FG3M FG3A FG3_PCT REB AST TOV STL BLK PLUS_MINUS AGE".split()}
SHOT_SORTS = {c: c for c in "GAME_DATE PLAYER_NAME EVENT_TYPE SHOT_MADE_FLAG SHOT_TYPE SHOT_DISTANCE LOC_X LOC_Y".split()}
PLAYER_RANGES = {c: c for c in "AGE GP MIN FGM FGA FG_PCT FG3M FG3A FG3_PCT REB AST TOV STL BLK PTS PLUS_MINUS".split()}
PLAYER_LIKES = {c: c for c in "PLAYER_NAME TEAM_ABBREVIATION".split()}
SHOT_RANGES = {c: c for c in "SHOT_DISTANCE LOC_X LOC_Y".split()}
MAX_SELECTED_IDS = 12

PREDICTION_COLUMNS = (
    "TEAM_ID, TEAM_NAME, TEAM_ABBREVIATION, games_played, actual_wins, actual_losses, "
    "actual_win_pct, current_82_pace, net_rating, preseason_pred_wins, "
    "inseason_talent_wins_82, preseason_expected_wins_at_gp, "
    "inseason_expected_wins_at_gp, preseason_win_error_at_gp, "
    "inseason_win_error_at_gp, profile_trust, updated_pred_wins, preseason_rank, "
    "updated_rank, actual_rank, season"
)
PREDICTION_SORTS = {
    c: c
    for c in (
        "TEAM_ABBREVIATION TEAM_NAME games_played actual_wins actual_losses "
        "actual_win_pct current_82_pace net_rating preseason_pred_wins "
        "inseason_talent_wins_82 preseason_expected_wins_at_gp "
        "inseason_expected_wins_at_gp preseason_win_error_at_gp "
        "inseason_win_error_at_gp updated_pred_wins preseason_rank updated_rank"
    ).split()
}
DATASET_NAMES = (
    "players",
    "teams",
    "player_season_stats",
    "team_season_stats",
    "shot_charts",
    "win_predictions",
)

class InvalidFilter(ValueError):
    """A user-supplied selection or column range could not be validated."""

class DataUnavailable(Exception):
    def __init__(self, dataset: str, detail: str | None = None):
        self.dataset, self.detail = dataset, detail or f"The {dataset} dataset is unavailable."

def available(cfg: Settings, dataset: str) -> bool:
    paths = {
        "players": cfg.curated_dir / "players" / "players.parquet",
        "teams": cfg.curated_dir / "teams" / "teams.parquet",
        "player_season_stats": cfg.curated_dir / "player_season_stats",
        "team_season_stats": cfg.curated_dir / "team_season_stats",
        "shot_charts": cfg.curated_dir / "shot_charts",
        "win_predictions": cfg.curated_dir / "win_predictions",
        "win_prediction_history": cfg.curated_dir / "win_prediction_history",
    }
    path = paths[dataset]
    return path.exists() if path.suffix else any(path.rglob("*.parquet"))

def require(cfg: Settings, dataset: str) -> None:
    if not available(cfg, dataset): raise DataUnavailable(dataset)

def db(cfg: Settings): return connect(cfg)

def rows(cursor) -> list[dict[str, Any]]:
    columns = [c[0] for c in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]

def jsonable(value: Any) -> Any:
    """Replace NaN/Inf with None so pandas-derived payloads can serialise.

    DuckDB hands back None for SQL NULL, but a frame read straight from Parquet
    carries NaN, which is not valid JSON.
    """
    if isinstance(value, dict): return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [jsonable(v) for v in value]
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, (np.integer,)): return int(value)
    if isinstance(value, (np.floating,)):
        number = float(value)
        return None if math.isnan(number) or math.isinf(number) else number
    if isinstance(value, np.bool_): return bool(value)
    if value is pd.NaT or (value is not None and value is pd.NA): return None
    return value

def frame_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """A DataFrame as JSON-safe records."""
    return jsonable(frame.to_dict("records"))

def id_list(raw: str | None, limit: int = MAX_SELECTED_IDS) -> list[int]:
    """Parse a comma-separated selection of integer ids, preserving first-seen order."""
    if not raw: return []
    try: ids = [int(part) for part in (p.strip() for p in raw.split(",")) if part]
    except ValueError as exc: raise InvalidFilter("Selected ids must be integers") from exc
    unique = list(dict.fromkeys(ids))
    if len(unique) > limit: raise InvalidFilter(f"At most {limit} ids may be selected at once")
    return unique

def parse_bounds(entries: list[str] | None, allowed: dict[str, str]) -> dict[str, float]:
    """Parse repeated `COLUMN:VALUE` parameters into an allow-listed column/value mapping."""
    parsed: dict[str, float] = {}
    for entry in entries or []:
        column, _, value = entry.partition(":")
        column = column.strip().upper()
        if column not in allowed: raise InvalidFilter(f"{column or entry} is not a filterable column")
        try: parsed[column] = float(value)
        except ValueError as exc: raise InvalidFilter(f"{column} filter needs a numeric value") from exc
    return parsed

def contains_where(entries: list[str] | None, allowed: dict[str, str]) -> tuple[list[str], list[str]]:
    """Build case-insensitive substring predicates from repeated `COLUMN:TEXT` parameters."""
    clauses, params = [], []
    for entry in entries or []:
        column, _, text = entry.partition(":")
        column = column.strip().upper()
        if column not in allowed: raise InvalidFilter(f"{column or entry} is not a searchable column")
        if not text.strip(): continue
        if len(text) > 80: raise InvalidFilter(f"{column} search text is too long")
        clauses.append(f"lower({allowed[column]}) LIKE lower(?)"); params.append(f"%{text.strip()}%")
    return clauses, params

def range_where(minimums: dict[str, float], maximums: dict[str, float], allowed: dict[str, str]) -> tuple[list[str], list[float]]:
    """Build numeric predicates, rejecting any column whose minimum exceeds its maximum."""
    conflict = next((c for c in minimums if c in maximums and minimums[c] > maximums[c]), None)
    if conflict: raise InvalidFilter(f"{conflict} minimum cannot exceed its maximum")
    clauses, params = [], []
    for column, value in minimums.items(): clauses.append(f"{allowed[column]} >= ?"); params.append(value)
    for column, value in maximums.items(): clauses.append(f"{allowed[column]} <= ?"); params.append(value)
    return clauses, params

def shot_where(season: str, player_ids: list[int] | None = None, team_ids: list[int] | None = None, result: str | None = None, shot_type: str | None = None, min_distance: int | None = None, max_distance: int | None = None) -> tuple[str, list[Any]]:
    clauses, params = ["season = ?"], [season]
    if player_ids: clauses.append(f"PLAYER_ID IN ({', '.join('?' * len(player_ids))})"); params.extend(player_ids)
    if team_ids: clauses.append(f"TEAM_ID IN ({', '.join('?' * len(team_ids))})"); params.extend(team_ids)
    if result: clauses.append("SHOT_MADE_FLAG = ?"); params.append(1 if result == "made" else 0)
    if shot_type: clauses.append("SHOT_TYPE = ?"); params.append(shot_type)
    if min_distance is not None: clauses.append("SHOT_DISTANCE >= ?"); params.append(min_distance)
    if max_distance is not None: clauses.append("SHOT_DISTANCE <= ?"); params.append(max_distance)
    return " WHERE " + " AND ".join(clauses), params

def safe_execute(con, sql: str, params: list[Any]):
    try: return con.execute(sql, params)
    except Exception as exc: raise DataUnavailable("curated data", "Data is being refreshed or cannot be read; retry shortly.") from exc
