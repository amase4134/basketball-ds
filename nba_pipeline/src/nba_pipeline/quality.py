"""Completeness and junk checks over the curated warehouse.

Reads curated Parquet and the refresh log only; never calls the NBA API.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from nba_pipeline.config import GAMES_PER_SEASON, Settings, settings
from nba_pipeline.transform.clean import (
    SHOT_LOCATION_BOUNDS,
    out_of_bounds_shot_counts,
    row_count_drop_message,
    validate_players,
)

logger = logging.getLogger(__name__)

# A pull that lands under these counts is incomplete, not merely small.
MIN_ROWS: dict[str, int] = {
    "players": 1000,
    "teams": 30,
    "player_season_stats": 1,
    "team_season_stats": 30,
    "shot_charts": 1,
}

# A full season carries ~0.1% genuine full-court heaves past the court bounds;
# only a much larger share means the coordinates themselves are junk.
OUT_OF_BOUNDS_TOLERANCE = 0.01


@dataclass(frozen=True)
class Finding:
    """One failed expectation about the curated data."""

    dataset: str
    season: str | None
    message: str

    def format(self) -> str:
        scope = self.dataset if self.season is None else f"{self.dataset} {self.season}"
        return f"{scope}: {self.message}"


def parse_since(value: str) -> datetime:
    """Parse an ISO timestamp, treating a naive value as UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def read_refresh_log(cfg: Settings) -> pd.DataFrame | None:
    log_path = cfg.meta_dir / "refresh_log.parquet"
    if not log_path.exists():
        return None
    return pd.read_parquet(log_path)


def _read_parquet_dir(directory: Path) -> pd.DataFrame | None:
    """Concatenate every Parquet file in a partition directory."""
    files = sorted(directory.glob("*.parquet"))
    if not files:
        return None
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def _log_rows(log: pd.DataFrame, dataset: str, season: str | None) -> pd.DataFrame:
    mask = log["dataset"] == dataset
    if season is None:
        mask &= log["season"].isna()
    else:
        mask &= log["season"] == season
    return log.loc[mask]


def check_refresh_log(
    log: pd.DataFrame | None,
    dataset: str,
    season: str | None,
    since: datetime | None,
) -> list[Finding]:
    """Flag a failed last run, or a run that never recorded a fresh success."""
    if log is None:
        return [Finding(dataset, season, "no refresh log; the pipeline has never run")]

    rows = _log_rows(log, dataset, season)
    if rows.empty:
        return [Finding(dataset, season, "no refresh log entry")]

    findings: list[Finding] = []
    latest = rows.iloc[-1]
    if latest["status"] != "ok":
        detail = str(latest.get("message") or "").strip()
        suffix = f": {detail}" if detail else ""
        findings.append(
            Finding(dataset, season, f"last refresh status '{latest['status']}'{suffix}")
        )

    if since is not None:
        succeeded = rows[rows["status"] == "ok"]
        timestamps = pd.to_datetime(succeeded["timestamp"], utc=True, errors="coerce")
        if not (timestamps >= pd.Timestamp(since)).any():
            findings.append(
                Finding(
                    dataset,
                    season,
                    f"no successful refresh since {since.isoformat()}",
                )
            )
    return findings


def check_row_count_drop(
    log: pd.DataFrame | None,
    dataset: str,
    season: str | None,
) -> list[Finding]:
    """Compare the newest successful pull against the one before it."""
    if log is None:
        return []
    succeeded = _log_rows(log, dataset, season)
    succeeded = succeeded[succeeded["status"] == "ok"]
    if len(succeeded) < 2:
        return []
    latest = int(succeeded.iloc[-1]["row_count"])
    previous = int(succeeded.iloc[-2]["row_count"])
    message = row_count_drop_message(dataset, season, latest, previous)
    return [Finding(dataset, season, message)] if message else []


def _check_min_rows(dataset: str, season: str | None, count: int) -> list[Finding]:
    floor = MIN_ROWS[dataset]
    if count < floor:
        return [Finding(dataset, season, f"only {count} rows, expected at least {floor}")]
    return []


def check_players(df: pd.DataFrame) -> list[Finding]:
    findings = [Finding("players", None, w) for w in validate_players(df)]
    if df.empty:
        return findings
    findings += _check_min_rows("players", None, len(df))
    if df["PLAYER_ID"].isna().any():
        findings.append(Finding("players", None, "null PLAYER_ID values"))
    if "FULL_NAME" in df.columns and df["FULL_NAME"].isna().any():
        findings.append(Finding("players", None, "null FULL_NAME values"))
    return findings


def check_teams(df: pd.DataFrame) -> list[Finding]:
    if df.empty:
        return [Finding("teams", None, "teams table is empty")]
    findings = _check_min_rows("teams", None, len(df))
    if df["TEAM_ID"].duplicated().any():
        findings.append(Finding("teams", None, "duplicate TEAM_ID values"))
    if df["TEAM_ID"].isna().any():
        findings.append(Finding("teams", None, "null TEAM_ID values"))
    if "FULL_NAME" in df.columns and df["FULL_NAME"].isna().any():
        findings.append(Finding("teams", None, "null FULL_NAME values"))
    return findings


def check_player_season_stats(df: pd.DataFrame, season: str) -> list[Finding]:
    if df.empty:
        return [Finding("player_season_stats", season, "partition is empty")]
    findings = _check_min_rows("player_season_stats", season, len(df))
    if df["PLAYER_ID"].isna().any():
        findings.append(Finding("player_season_stats", season, "null PLAYER_ID values"))
    if df["PLAYER_ID"].duplicated().any():
        findings.append(
            Finding("player_season_stats", season, "duplicate PLAYER_ID values")
        )
    if "PLAYER_NAME" in df.columns and df["PLAYER_NAME"].isna().any():
        findings.append(Finding("player_season_stats", season, "null PLAYER_NAME values"))
    if "MIN" in df.columns:
        negative = int((df["MIN"] < 0).sum())
        if negative:
            findings.append(
                Finding("player_season_stats", season, f"{negative} rows with negative MIN")
            )
    return findings


def check_team_season_stats(df: pd.DataFrame, season: str) -> list[Finding]:
    if df.empty:
        return [Finding("team_season_stats", season, "partition is empty")]
    findings = _check_min_rows("team_season_stats", season, len(df))
    if df["TEAM_ID"].isna().any():
        findings.append(Finding("team_season_stats", season, "null TEAM_ID values"))
    if df["TEAM_ID"].duplicated().any():
        findings.append(Finding("team_season_stats", season, "duplicate TEAM_ID values"))

    if {"GP", "W", "L"} <= set(df.columns):
        games = df[["GP", "W", "L"]].astype("float64")
        # Wins and losses must account for exactly the games a team has played.
        mismatched = int((games["W"] + games["L"] != games["GP"]).sum())
        if mismatched:
            findings.append(
                Finding(
                    "team_season_stats",
                    season,
                    f"{mismatched} teams where W + L does not equal GP",
                )
            )
        overrun = int((games["GP"] > GAMES_PER_SEASON).sum())
        if overrun:
            findings.append(
                Finding(
                    "team_season_stats",
                    season,
                    f"{overrun} teams with more than {GAMES_PER_SEASON} games played",
                )
            )
        negative = int((games < 0).any(axis=1).sum())
        if negative:
            findings.append(
                Finding("team_season_stats", season, f"{negative} teams with negative GP/W/L")
            )

    if "W_PCT" in df.columns:
        pct = pd.to_numeric(df["W_PCT"], errors="coerce").dropna()
        if ((pct < 0) | (pct > 1)).any():
            findings.append(Finding("team_season_stats", season, "W_PCT outside 0–1"))
    return findings


def check_shot_charts(df: pd.DataFrame, season: str) -> list[Finding]:
    if df.empty:
        return [Finding("shot_charts", season, "partition is empty")]
    findings = _check_min_rows("shot_charts", season, len(df))
    for column, count in out_of_bounds_shot_counts(df).items():
        if count > len(df) * OUT_OF_BOUNDS_TOLERANCE:
            low, high = SHOT_LOCATION_BOUNDS[column]
            findings.append(
                Finding(
                    "shot_charts",
                    season,
                    f"{count} shots with {column} outside [{low}, {high}]",
                )
            )
    for column in ("PLAYER_ID", "GAME_ID"):
        if column in df.columns and df[column].isna().any():
            findings.append(Finding("shot_charts", season, f"null {column} values"))
    if "SHOT_MADE_FLAG" in df.columns:
        bad = int((~df["SHOT_MADE_FLAG"].isin([0, 1])).sum())
        if bad:
            findings.append(
                Finding("shot_charts", season, f"{bad} rows with SHOT_MADE_FLAG not 0/1")
            )
    if "SHOT_DISTANCE" in df.columns:
        negative = int((df["SHOT_DISTANCE"] < 0).sum())
        if negative:
            findings.append(
                Finding("shot_charts", season, f"{negative} shots with negative SHOT_DISTANCE")
            )
    return findings


def check_warehouse(
    *,
    cfg: Settings | None = None,
    season: str | None = None,
    since: datetime | None = None,
) -> list[Finding]:
    """Run every completeness and junk check; empty result means healthy."""
    cfg = cfg or settings
    season = season or cfg.seasons[-1]
    log = read_refresh_log(cfg)
    findings: list[Finding] = []

    players_path = cfg.curated_dir / "players" / "players.parquet"
    if players_path.exists():
        findings += check_players(pd.read_parquet(players_path))
    else:
        findings.append(Finding("players", None, "curated players.parquet is missing"))

    teams_path = cfg.curated_dir / "teams" / "teams.parquet"
    if teams_path.exists():
        findings += check_teams(pd.read_parquet(teams_path))
    else:
        findings.append(Finding("teams", None, "curated teams.parquet is missing"))

    stats = _read_parquet_dir(cfg.curated_dir / "player_season_stats" / f"season={season}")
    if stats is None:
        findings.append(
            Finding("player_season_stats", season, "no curated partition for this season")
        )
    else:
        findings += check_player_season_stats(stats, season)

    team_stats = _read_parquet_dir(cfg.curated_dir / "team_season_stats" / f"season={season}")
    if team_stats is None:
        findings.append(
            Finding("team_season_stats", season, "no curated partition for this season")
        )
    else:
        findings += check_team_season_stats(team_stats, season)

    shots = _read_parquet_dir(cfg.curated_dir / "shot_charts" / f"season={season}")
    if shots is None:
        findings.append(
            Finding("shot_charts", season, "no curated partition for this season")
        )
    else:
        findings += check_shot_charts(shots, season)

    for dataset, dataset_season in (
        ("players", None),
        ("teams", None),
        ("player_season_stats", season),
        ("team_season_stats", season),
        ("shot_charts", season),
    ):
        findings += check_refresh_log(log, dataset, dataset_season, since)
        findings += check_row_count_drop(log, dataset, dataset_season)

    return findings
