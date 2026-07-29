"""DuckDB view registration over curated Parquet datasets."""

from __future__ import annotations

from pathlib import Path

import duckdb

from nba_pipeline.config import Settings, settings


def connect(cfg: Settings | None = None, database: str = ":memory:") -> duckdb.DuckDBPyConnection:
    """Open DuckDB and register curated dataset views."""
    cfg = cfg or settings
    con = duckdb.connect(database)
    register_views(con, cfg)
    return con


def register_views(con: duckdb.DuckDBPyConnection, cfg: Settings | None = None) -> None:
    cfg = cfg or settings
    curated = cfg.curated_dir

    players = curated / "players" / "players.parquet"
    teams = curated / "teams" / "teams.parquet"
    stats_glob = str(curated / "player_season_stats" / "**" / "*.parquet")
    shots_glob = str(curated / "shot_charts" / "**" / "*.parquet")

    if players.exists():
        con.execute(
            f"CREATE OR REPLACE VIEW players AS SELECT * FROM read_parquet('{_escape(players)}')"
        )
    if teams.exists():
        con.execute(
            f"CREATE OR REPLACE VIEW teams AS SELECT * FROM read_parquet('{_escape(teams)}')"
        )

    if any((curated / "player_season_stats").rglob("*.parquet")):
        con.execute(
            "CREATE OR REPLACE VIEW player_season_stats AS "
            f"SELECT * FROM read_parquet('{_escape(stats_glob)}', hive_partitioning = true)"
        )

    if any((curated / "shot_charts").rglob("*.parquet")):
        con.execute(
            "CREATE OR REPLACE VIEW shot_charts AS "
            f"SELECT * FROM read_parquet('{_escape(shots_glob)}', hive_partitioning = true)"
        )


def _escape(path: Path | str) -> str:
    return str(path).replace("'", "''")
