"""Validated, parameterized query helpers for the local Explorer API."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from nba_pipeline.config import Settings
from nba_pipeline.load.duckdb_views import connect

PLAYER_COLUMNS = "PLAYER_ID, PLAYER_NAME, TEAM_ID, TEAM_ABBREVIATION, AGE, GP, MIN, FGM, FGA, FG_PCT, FG3M, FG3A, FG3_PCT, REB, AST, TOV, STL, BLK, PTS, PLUS_MINUS, season"
PLAYER_SORTS = {c: c for c in "PLAYER_NAME TEAM_ABBREVIATION GP MIN PTS FGA FG_PCT FG3A FG3_PCT REB AST TOV STL BLK PLUS_MINUS AGE".split()}
SHOT_SORTS = {c: c for c in "GAME_DATE PLAYER_NAME EVENT_TYPE SHOT_MADE_FLAG SHOT_TYPE SHOT_DISTANCE LOC_X LOC_Y".split()}

class DataUnavailable(Exception):
    def __init__(self, dataset: str, detail: str | None = None):
        self.dataset, self.detail = dataset, detail or f"The {dataset} dataset is unavailable."

def available(cfg: Settings, dataset: str) -> bool:
    paths = {"players": cfg.curated_dir / "players" / "players.parquet", "teams": cfg.curated_dir / "teams" / "teams.parquet", "player_season_stats": cfg.curated_dir / "player_season_stats", "shot_charts": cfg.curated_dir / "shot_charts"}
    path = paths[dataset]
    return path.exists() if path.suffix else any(path.rglob("*.parquet"))

def require(cfg: Settings, dataset: str) -> None:
    if not available(cfg, dataset): raise DataUnavailable(dataset)

def db(cfg: Settings): return connect(cfg)

def rows(cursor) -> list[dict[str, Any]]:
    columns = [c[0] for c in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]

def shot_where(season: str, player_id: int | None = None, team_id: int | None = None, result: str | None = None, shot_type: str | None = None, min_distance: int | None = None, max_distance: int | None = None) -> tuple[str, list[Any]]:
    clauses, params = ["season = ?"], [season]
    if player_id is not None: clauses.append("PLAYER_ID = ?"); params.append(player_id)
    if team_id is not None: clauses.append("TEAM_ID = ?"); params.append(team_id)
    if result: clauses.append("SHOT_MADE_FLAG = ?"); params.append(1 if result == "made" else 0)
    if shot_type: clauses.append("SHOT_TYPE = ?"); params.append(shot_type)
    if min_distance is not None: clauses.append("SHOT_DISTANCE >= ?"); params.append(min_distance)
    if max_distance is not None: clauses.append("SHOT_DISTANCE <= ?"); params.append(max_distance)
    return " WHERE " + " AND ".join(clauses), params

def safe_execute(con, sql: str, params: list[Any]):
    try: return con.execute(sql, params)
    except Exception as exc: raise DataUnavailable("curated data", "Data is being refreshed or cannot be read; retry shortly.") from exc
