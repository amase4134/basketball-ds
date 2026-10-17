"""Column selection, typing, and light validation."""

from __future__ import annotations

import logging

import pandas as pd

from nba_pipeline.config import (
    PLAYER_STATS_KEEP_PREFIXES,
    SHOT_CHART_COLUMNS,
    TEAM_STATS_COLUMNS,
)

logger = logging.getLogger(__name__)

# Court coordinates in tenths of a foot. Full-court heaves do clear the LOC_Y
# bound legitimately, so callers gating on this should allow a small share.
SHOT_LOCATION_BOUNDS = {"LOC_X": (-300, 300), "LOC_Y": (-100, 500)}


def clean_players(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    rename = {
        "id": "PLAYER_ID",
        "full_name": "FULL_NAME",
        "first_name": "FIRST_NAME",
        "last_name": "LAST_NAME",
        "is_active": "IS_ACTIVE",
    }
    out = out.rename(columns={k: v for k, v in rename.items() if k in out.columns})
    if "PLAYER_ID" in out.columns:
        out["PLAYER_ID"] = out["PLAYER_ID"].astype("int64")
    cols = [c for c in ["PLAYER_ID", "FULL_NAME", "FIRST_NAME", "LAST_NAME", "IS_ACTIVE"] if c in out.columns]
    out = out[cols].drop_duplicates(subset=["PLAYER_ID"]).sort_values("PLAYER_ID")
    return out.reset_index(drop=True)


def clean_teams(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    rename = {
        "id": "TEAM_ID",
        "full_name": "FULL_NAME",
        "abbreviation": "ABBREVIATION",
        "nickname": "NICKNAME",
        "city": "CITY",
        "state": "STATE",
        "year_founded": "YEAR_FOUNDED",
    }
    out = out.rename(columns={k: v for k, v in rename.items() if k in out.columns})
    if "TEAM_ID" in out.columns:
        out["TEAM_ID"] = out["TEAM_ID"].astype("int64")
    cols = [
        c
        for c in [
            "TEAM_ID",
            "FULL_NAME",
            "ABBREVIATION",
            "NICKNAME",
            "CITY",
            "STATE",
            "YEAR_FOUNDED",
        ]
        if c in out.columns
    ]
    out = out[cols].drop_duplicates(subset=["TEAM_ID"]).sort_values("TEAM_ID")
    return out.reset_index(drop=True)


def clean_player_season_stats(df: pd.DataFrame, season: str) -> pd.DataFrame:
    out = df.copy()
    out["SEASON"] = season
    keep = ["SEASON"] + [c for c in PLAYER_STATS_KEEP_PREFIXES if c in out.columns]
    # Also keep common rate columns that may appear with PCT / RANK suffixes we care about.
    extras = [
        c
        for c in out.columns
        if c.endswith("_PCT") or c in {"CFID", "CFPARAMS"}
    ]
    # Drop CFID/CFPARAMS noise if present; keep PCT columns already filtered via keep.
    keep = list(dict.fromkeys(keep + [c for c in extras if not c.startswith("CF")]))
    out = out[[c for c in keep if c in out.columns]]
    if "PLAYER_ID" in out.columns:
        out["PLAYER_ID"] = out["PLAYER_ID"].astype("int64")
    if "TEAM_ID" in out.columns:
        out["TEAM_ID"] = pd.to_numeric(out["TEAM_ID"], errors="coerce").astype("Int64")
    if "MIN" in out.columns:
        out["MIN"] = pd.to_numeric(out["MIN"], errors="coerce")
    return out.reset_index(drop=True)


def clean_team_season_stats(df: pd.DataFrame, season: str) -> pd.DataFrame:
    out = df.copy()
    out["SEASON"] = season
    if "TEAM_ID" in out.columns:
        out["TEAM_ID"] = out["TEAM_ID"].astype("int64")
    # The Advanced measure type omits FTA_RATE, which is simply FTA per FGA.
    if "FTA_RATE" not in out.columns and {"FTA", "FGA"} <= set(out.columns):
        attempts = pd.to_numeric(out["FGA"], errors="coerce")
        out["FTA_RATE"] = pd.to_numeric(out["FTA"], errors="coerce") / attempts.where(
            attempts != 0
        )
    for col in ("GP", "W", "L"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").astype("Int64")
    numeric = [
        c
        for c in TEAM_STATS_COLUMNS
        if c in out.columns
        and c not in {"SEASON", "TEAM_ID", "TEAM_NAME", "TEAM_ABBREVIATION", "GP", "W", "L"}
    ]
    for col in numeric:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    cols = [c for c in TEAM_STATS_COLUMNS if c in out.columns]
    out = out[cols].drop_duplicates(subset=["TEAM_ID"]).sort_values("TEAM_ID")
    return out.reset_index(drop=True)


def clean_shot_charts(df: pd.DataFrame, season: str) -> pd.DataFrame:
    out = df.copy()
    out["SEASON"] = season
    for col in ("PLAYER_ID", "TEAM_ID"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").astype("Int64")
    for col in ("LOC_X", "LOC_Y", "SHOT_DISTANCE", "SHOT_MADE_FLAG"):
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    cols = [c for c in SHOT_CHART_COLUMNS if c in out.columns]
    out = out[cols]
    return out.reset_index(drop=True)


def validate_players(df: pd.DataFrame) -> list[str]:
    warnings: list[str] = []
    if df.empty:
        warnings.append("players table is empty")
        return warnings
    if df["PLAYER_ID"].duplicated().any():
        warnings.append("duplicate PLAYER_ID values in players")
    return warnings


def out_of_bounds_shot_counts(df: pd.DataFrame) -> dict[str, int]:
    """Count shots outside the court coordinate bounds, per location column."""
    counts: dict[str, int] = {}
    for column, (low, high) in SHOT_LOCATION_BOUNDS.items():
        if column in df.columns:
            counts[column] = int(((df[column] < low) | (df[column] > high)).sum())
    return counts


def validate_shot_charts(df: pd.DataFrame) -> list[str]:
    warnings: list[str] = []
    if df.empty:
        warnings.append("shot_charts partition is empty")
        return warnings
    for column, count in out_of_bounds_shot_counts(df).items():
        if count:
            low, high = SHOT_LOCATION_BOUNDS[column]
            warnings.append(f"{count} shots with {column} outside [{low}, {high}]")
    return warnings


def row_count_drop_message(
    dataset: str,
    season: str | None,
    new_count: int,
    previous: int | None,
    *,
    threshold: float = 0.5,
) -> str | None:
    """Describe a sharp row-count drop, or None when the counts look sane."""
    if previous is None or previous == 0:
        return None
    if new_count >= previous * threshold:
        return None
    return (
        f"{dataset} season={season} row count dropped sharply: "
        f"{previous} → {new_count}"
    )


def warn_row_count_drop(
    dataset: str,
    season: str | None,
    new_count: int,
    previous: int | None,
    *,
    threshold: float = 0.5,
) -> None:
    message = row_count_drop_message(
        dataset, season, new_count, previous, threshold=threshold
    )
    if message:
        logger.warning("%s", message)
