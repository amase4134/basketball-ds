"""Parquet write helpers and refresh logging."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from nba_pipeline.config import Settings, settings


def write_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def replace_season_partition(
    df: pd.DataFrame,
    dataset_dir: Path,
    season: str,
    *,
    filename: str = "data.parquet",
) -> Path:
    """Replace a season=YYYY-YY partition directory with a fresh Parquet file."""
    partition_dir = dataset_dir / f"season={season}"
    if partition_dir.exists():
        shutil.rmtree(partition_dir)
    out = partition_dir / filename
    write_parquet(df, out)
    return out


def write_static_table(df: pd.DataFrame, dataset_dir: Path, name: str) -> Path:
    """Write a non-partitioned static curated table."""
    out = dataset_dir / f"{name}.parquet"
    write_parquet(df, out)
    return out


def append_refresh_log(
    *,
    dataset: str,
    season: str | None,
    row_count: int,
    status: str,
    message: str = "",
    cfg: Settings | None = None,
) -> None:
    cfg = cfg or settings
    cfg.meta_dir.mkdir(parents=True, exist_ok=True)
    log_path = cfg.meta_dir / "refresh_log.parquet"
    row = pd.DataFrame(
        [
            {
                "dataset": dataset,
                "season": season,
                "row_count": row_count,
                "status": status,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ]
    )
    if log_path.exists():
        existing = pd.read_parquet(log_path)
        combined = pd.concat([existing, row], ignore_index=True)
    else:
        combined = row
    combined.to_parquet(log_path, index=False)


def previous_row_count(
    dataset: str,
    season: str | None,
    *,
    cfg: Settings | None = None,
) -> int | None:
    cfg = cfg or settings
    log_path = cfg.meta_dir / "refresh_log.parquet"
    if not log_path.exists():
        return None
    log = pd.read_parquet(log_path)
    mask = (log["dataset"] == dataset) & (log["status"] == "ok")
    if season is None:
        mask &= log["season"].isna()
    else:
        mask &= log["season"] == season
    matches = log.loc[mask]
    if matches.empty:
        return None
    return int(matches.iloc[-1]["row_count"])
