"""Side-by-side comparison of the locked pre-season projection with Opta's.

Opta's win totals are averages over thousands of season simulations, so they
regress harder toward .500 than a single point estimate does. Spread is
reported next to the head-to-head gaps for that reason: two forecasts can
agree on the order of teams while disagreeing on how far apart they sit.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from nba_pipeline.config import GAMES_PER_SEASON

PLAYOFF_SEEDS = 6
PLAY_IN_SEEDS = 10

PREDICTION_COLUMNS = [
    "TEAM_ID",
    "preseason_pred_wins",
    "games_played",
    "actual_wins",
    "actual_losses",
    "current_82_pace",
]


def build_comparison(predictions: pd.DataFrame, opta: pd.DataFrame) -> pd.DataFrame:
    """One row per team with both projections, ranks, and the gap between them."""
    mine = predictions[[c for c in PREDICTION_COLUMNS if c in predictions.columns]].copy()
    mine["TEAM_ID"] = mine["TEAM_ID"].astype("int64")
    frame = opta.merge(mine, on="TEAM_ID", how="inner", validate="one_to_one")
    if len(frame) != len(opta):
        missing = sorted(set(opta["TEAM_NAME"]) - set(frame["TEAM_NAME"]))
        raise ValueError(f"No stored projection for: {missing}")

    frame = frame.rename(columns={"preseason_pred_wins": "my_proj_wins"})
    frame["win_diff"] = frame["my_proj_wins"] - frame["opta_proj_wins"]
    frame["my_league_rank"] = (
        frame["my_proj_wins"].rank(ascending=False, method="min").astype("int64")
    )
    frame["my_conf_rank"] = (
        frame.groupby("conference")["my_proj_wins"]
        .rank(ascending=False, method="min")
        .astype("int64")
    )
    # Positive means my model ranks the team higher (a smaller rank number).
    frame["conf_rank_diff"] = frame["opta_conf_rank"] - frame["my_conf_rank"]
    frame["my_tier"] = frame["my_conf_rank"].map(_tier)
    frame["opta_tier"] = frame["opta_conf_rank"].map(_tier)
    return frame.sort_values(["conference", "opta_conf_rank"]).reset_index(drop=True)


def _tier(conf_rank: int) -> str:
    if conf_rank <= PLAYOFF_SEEDS:
        return "Playoffs"
    if conf_rank <= PLAY_IN_SEEDS:
        return "Play-in"
    return "Lottery"


def _overlap(frame: pd.DataFrame, cutoff: int) -> dict[str, int]:
    out = {}
    for conference, group in frame.groupby("conference"):
        mine = set(group.loc[group["my_conf_rank"] <= cutoff, "TEAM_ID"])
        theirs = set(group.loc[group["opta_conf_rank"] <= cutoff, "TEAM_ID"])
        out[conference] = len(mine & theirs)
    return out


def summarise(comparison: pd.DataFrame) -> dict:
    mine = comparison["my_proj_wins"]
    opta = comparison["opta_proj_wins"]
    diff = comparison["win_diff"]
    rho = spearmanr(mine, opta).statistic

    summary = {
        "teams": int(len(comparison)),
        "mean_abs_gap": float(diff.abs().mean()),
        "rmse_gap": float(np.sqrt((diff**2).mean())),
        "pearson_r": float(np.corrcoef(mine, opta)[0, 1]),
        "spearman_rho": None if pd.isna(rho) else float(rho),
        "my_spread": float(mine.std()),
        "opta_spread": float(opta.std()),
        "my_range": (float(mine.min()), float(mine.max())),
        "opta_range": (float(opta.min()), float(opta.max())),
        "within_3_wins": int(diff.abs().le(3).sum()),
        "playoff_overlap": _overlap(comparison, PLAYOFF_SEEDS),
        "play_in_overlap": _overlap(comparison, PLAY_IN_SEEDS),
        "tier_agreement": int((comparison["my_tier"] == comparison["opta_tier"]).sum()),
        "season_started": bool(comparison.get("games_played", pd.Series(0)).gt(0).any()),
    }

    if summary["season_started"]:
        played = comparison[comparison["games_played"] > 0]
        pace = played["current_82_pace"]
        summary["my_mae_vs_pace"] = float((played["my_proj_wins"] - pace).abs().mean())
        summary["opta_mae_vs_pace"] = float((played["opta_proj_wins"] - pace).abs().mean())
        summary["games_played_avg"] = float(comparison["games_played"].mean())
        summary["season_progress_pct"] = summary["games_played_avg"] / GAMES_PER_SEASON
    return summary


def biggest_gaps(comparison: pd.DataFrame, count: int = 5) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Teams my model likes most and least relative to Opta."""
    ordered = comparison.sort_values("win_diff", ascending=False)
    return ordered.head(count), ordered.tail(count).iloc[::-1]
