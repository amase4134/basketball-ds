"""Feature engineering for team win prediction.

Two feature sets are derived from curated `team_season_stats`:

* `PRESEASON_FEATURES` describe a team's *previous* season and drive the
  locked pre-season projection for the target season.
* `PROFILE_FEATURES` describe a team's efficiency profile *within* a season and
  map that profile onto a full-season win percentage.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from nba_pipeline.config import GAMES_PER_SEASON

# Pythagorean exponent for basketball. Daryl Morey's original fit is ~13.91;
# 14 is the value in common use and the difference is immaterial here.
PYTHAGOREAN_EXPONENT = 14.0

# Efficiency and four-factor columns shared by both feature sets.
PROFILE_FEATURES = [
    "NET_RATING",
    "OFF_RATING",
    "DEF_RATING",
    "PYTH_WIN_PCT",
    "EFG_PCT",
    "TM_TOV_PCT",
    "OREB_PCT",
    "FTA_RATE",
    "TS_PCT",
    "PLUS_MINUS",
]

# The same signal measured a season earlier, plus last season's actual record
# and roster continuity, is what we know before a season tips off.
PRESEASON_FEATURES = [
    "PRIOR_W_PCT",
    "PRIOR_NET_RATING",
    "PRIOR_OFF_RATING",
    "PRIOR_DEF_RATING",
    "PRIOR_PYTH_WIN_PCT",
    "PRIOR_EFG_PCT",
    "PRIOR_TM_TOV_PCT",
    "PRIOR_OREB_PCT",
    "PRIOR_FTA_RATE",
    "PRIOR_TS_PCT",
    "PRIOR_MINUTE_WEIGHTED_PLUS_MINUS",
]

PRIOR_PREFIX = "PRIOR_"


def pythagorean_win_pct(
    offensive_rating: pd.Series,
    defensive_rating: pd.Series,
    exponent: float = PYTHAGOREAN_EXPONENT,
) -> pd.Series:
    """Expected win share implied by points scored and allowed per 100.

    Ratings are already per-100-possession, so the usual points-for /
    points-against formulation applies directly.
    """
    scored = pd.to_numeric(offensive_rating, errors="coerce").astype("float64")
    allowed = pd.to_numeric(defensive_rating, errors="coerce").astype("float64")
    scored = scored.where(scored > 0)
    allowed = allowed.where(allowed > 0)
    scored_pow = np.power(scored, exponent)
    allowed_pow = np.power(allowed, exponent)
    denominator = scored_pow + allowed_pow
    return scored_pow / denominator.where(denominator > 0)


def add_derived_columns(team_stats: pd.DataFrame) -> pd.DataFrame:
    """Attach Pythagorean win% and a games-played-safe win percentage."""
    out = team_stats.copy()
    if {"OFF_RATING", "DEF_RATING"} <= set(out.columns):
        out["PYTH_WIN_PCT"] = pythagorean_win_pct(
            out["OFF_RATING"], out["DEF_RATING"]
        )
    else:
        out["PYTH_WIN_PCT"] = np.nan

    games = pd.to_numeric(out.get("GP"), errors="coerce").astype("float64")
    wins = pd.to_numeric(out.get("W"), errors="coerce").astype("float64")
    out["GP"] = games
    out["W"] = wins
    out["L"] = pd.to_numeric(out.get("L"), errors="coerce").astype("float64")
    out["ACTUAL_WIN_PCT"] = wins / games.where(games > 0)
    out["PACE_82"] = out["ACTUAL_WIN_PCT"] * GAMES_PER_SEASON
    return out


def minute_weighted_plus_minus(player_stats: pd.DataFrame) -> pd.DataFrame:
    """Roster strength per team: per-100 PLUS_MINUS weighted by minutes played.

    A team's returning talent is better described by its rotation players than
    by a flat average over everyone who logged a possession, so each player's
    contribution is weighted by the share of minutes they actually played.
    """
    needed = {"TEAM_ID", "MIN", "PLUS_MINUS"}
    if player_stats.empty or not needed <= set(player_stats.columns):
        return pd.DataFrame(columns=["TEAM_ID", "MINUTE_WEIGHTED_PLUS_MINUS"])

    frame = player_stats[["TEAM_ID", "MIN", "PLUS_MINUS"]].copy()
    frame["MIN"] = pd.to_numeric(frame["MIN"], errors="coerce")
    frame["PLUS_MINUS"] = pd.to_numeric(frame["PLUS_MINUS"], errors="coerce")
    frame = frame.dropna(subset=["TEAM_ID", "MIN", "PLUS_MINUS"])
    frame = frame[frame["MIN"] > 0]
    if frame.empty:
        return pd.DataFrame(columns=["TEAM_ID", "MINUTE_WEIGHTED_PLUS_MINUS"])

    frame["WEIGHTED"] = frame["MIN"] * frame["PLUS_MINUS"]
    grouped = frame.groupby("TEAM_ID", as_index=False).agg(
        WEIGHTED=("WEIGHTED", "sum"), MINUTES=("MIN", "sum")
    )
    grouped["MINUTE_WEIGHTED_PLUS_MINUS"] = grouped["WEIGHTED"] / grouped["MINUTES"]
    grouped["TEAM_ID"] = grouped["TEAM_ID"].astype("int64")
    return grouped[["TEAM_ID", "MINUTE_WEIGHTED_PLUS_MINUS"]]


def roster_strength_by_season(player_stats: pd.DataFrame) -> pd.DataFrame:
    """Minute-weighted team PLUS_MINUS for every season in `player_stats`."""
    if player_stats.empty or "SEASON" not in player_stats.columns:
        return pd.DataFrame(
            columns=["SEASON", "TEAM_ID", "MINUTE_WEIGHTED_PLUS_MINUS"]
        )
    frames = []
    for season, group in player_stats.groupby("SEASON"):
        weighted = minute_weighted_plus_minus(group)
        if weighted.empty:
            continue
        weighted.insert(0, "SEASON", season)
        frames.append(weighted)
    if not frames:
        return pd.DataFrame(
            columns=["SEASON", "TEAM_ID", "MINUTE_WEIGHTED_PLUS_MINUS"]
        )
    return pd.concat(frames, ignore_index=True)


def build_profile_frame(
    team_stats: pd.DataFrame,
    player_stats: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """One row per team-season with every in-season feature attached."""
    frame = add_derived_columns(team_stats)
    if player_stats is not None and not player_stats.empty:
        roster = roster_strength_by_season(player_stats)
        if not roster.empty:
            frame["TEAM_ID"] = frame["TEAM_ID"].astype("int64")
            frame = frame.merge(roster, on=["SEASON", "TEAM_ID"], how="left")
    if "MINUTE_WEIGHTED_PLUS_MINUS" not in frame.columns:
        frame["MINUTE_WEIGHTED_PLUS_MINUS"] = np.nan
    for column in PROFILE_FEATURES:
        if column not in frame.columns:
            frame[column] = np.nan
    return frame


def build_transition_frame(profiles: pd.DataFrame, seasons: list[str]) -> pd.DataFrame:
    """Pair each season with the one before it: features from `t-1`, wins at `t`.

    Only teams present in both seasons contribute a training row, so a
    relocation or expansion franchise never produces a half-populated pair.
    """
    ordered = [s for s in seasons if s in set(profiles["SEASON"])]
    rows: list[pd.DataFrame] = []
    for prior_season, season in zip(ordered, ordered[1:]):
        prior = profiles[profiles["SEASON"] == prior_season]
        current = profiles[profiles["SEASON"] == season]
        if prior.empty or current.empty:
            continue
        rows.append(_pair_seasons(prior, current, season, prior_season))
    if not rows:
        return pd.DataFrame()
    return pd.concat(rows, ignore_index=True)


def _pair_seasons(
    prior: pd.DataFrame,
    current: pd.DataFrame,
    season: str,
    prior_season: str,
) -> pd.DataFrame:
    source_columns = [
        "W_PCT",
        "NET_RATING",
        "OFF_RATING",
        "DEF_RATING",
        "PYTH_WIN_PCT",
        "EFG_PCT",
        "TM_TOV_PCT",
        "OREB_PCT",
        "FTA_RATE",
        "TS_PCT",
        "MINUTE_WEIGHTED_PLUS_MINUS",
    ]
    available = [c for c in source_columns if c in prior.columns]
    lagged = prior[["TEAM_ID", *available]].rename(
        columns={c: f"{PRIOR_PREFIX}{c}" for c in available}
    )
    target_columns = [
        c
        for c in ("TEAM_ID", "TEAM_NAME", "TEAM_ABBREVIATION", "GP", "W", "L", "W_PCT")
        if c in current.columns
    ]
    paired = current[target_columns].merge(lagged, on="TEAM_ID", how="inner")
    paired["SEASON"] = season
    paired["PRIOR_SEASON"] = prior_season
    for column in PRESEASON_FEATURES:
        if column not in paired.columns:
            paired[column] = np.nan
    return paired


def feature_matrix(frame: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    """Numeric feature block with league-median imputation for gaps.

    A missing four-factor column should pull a team toward the league average
    rather than drop it from the projection entirely.
    """
    block = frame.reindex(columns=features).apply(
        pd.to_numeric, errors="coerce"
    )
    medians = block.median(numeric_only=True)
    # A column that is entirely absent has no median; neutralise it with zero.
    return block.fillna(medians).fillna(0.0)
