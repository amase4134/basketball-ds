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


# Curated datasets partitioned by season=YYYY-YY, exposed as hive-partitioned views.
PARTITIONED_DATASETS = (
    "player_season_stats",
    "team_season_stats",
    "shot_charts",
    "win_predictions",
    "win_prediction_history",
)


def register_views(con: duckdb.DuckDBPyConnection, cfg: Settings | None = None) -> None:
    cfg = cfg or settings
    curated = cfg.curated_dir

    players = curated / "players" / "players.parquet"
    teams = curated / "teams" / "teams.parquet"
    refresh_log = cfg.meta_dir / "refresh_log.parquet"

    if players.exists():
        con.execute(
            f"CREATE OR REPLACE VIEW players AS SELECT * FROM read_parquet('{_escape(players)}')"
        )
    if teams.exists():
        con.execute(
            f"CREATE OR REPLACE VIEW teams AS SELECT * FROM read_parquet('{_escape(teams)}')"
        )

    for dataset in PARTITIONED_DATASETS:
        directory = curated / dataset
        if not any(directory.rglob("*.parquet")):
            continue
        glob = str(directory / "**" / "*.parquet")
        # union_by_name keeps a season readable when its columns differ, which
        # happens for a target season that has not tipped off yet.
        con.execute(
            f"CREATE OR REPLACE VIEW {dataset} AS "
            f"SELECT * FROM read_parquet('{_escape(glob)}', "
            "hive_partitioning = true, union_by_name = true)"
        )

    if refresh_log.exists():
        con.execute(
            f"CREATE OR REPLACE VIEW refresh_log AS SELECT * FROM read_parquet('{_escape(refresh_log)}')"
        )


def _escape(path: Path | str) -> str:
    return str(path).replace("'", "''")
