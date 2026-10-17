"""Accuracy scoring and the daily snapshot log for win predictions.

Two different questions get answered here:

* *Is the model right so far?* — compare each team's banked wins against the
  wins each model expected by this point in the schedule.
* *Is it getting better or worse?* — append a dated snapshot every run so the
  whole season's accuracy can be charted, and compare against last week.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from nba_pipeline.config import GAMES_PER_SEASON, Settings, settings
from nba_pipeline.load.parquet import replace_season_partition, write_parquet
from nba_pipeline.modeling.team_wins import TrainedWinModel

logger = logging.getLogger(__name__)

# Conference playoff cutoffs: the top 6 seeds qualify directly, 7–10 play in.
PLAYOFF_SEEDS = 6
PLAY_IN_SEEDS = 10

SNAPSHOT_KEYS = ["as_of_date", "TEAM_ID"]


def _mae(actual: pd.Series, predicted: pd.Series) -> float | None:
    pair = pd.DataFrame({"a": actual, "p": predicted}).dropna()
    if pair.empty:
        return None
    return float((pair["a"] - pair["p"]).abs().mean())


def _rmse(actual: pd.Series, predicted: pd.Series) -> float | None:
    pair = pd.DataFrame({"a": actual, "p": predicted}).dropna()
    if pair.empty:
        return None
    return float(np.sqrt(((pair["a"] - pair["p"]) ** 2).mean()))


def _spearman(actual: pd.Series, predicted: pd.Series) -> float | None:
    pair = pd.DataFrame({"a": actual, "p": predicted}).dropna()
    # Rank correlation needs at least three teams and some spread to exist.
    if len(pair) < 3 or pair["a"].nunique() < 2 or pair["p"].nunique() < 2:
        return None
    rho = spearmanr(pair["p"], pair["a"]).statistic
    return None if pd.isna(rho) else float(rho)


def _tier_accuracy(predictions: pd.DataFrame, cutoff: int) -> float | None:
    """Share of the predicted top-`cutoff` league seeds that actually hold one.

    Conference assignment is not in the curated warehouse, so this is measured
    league-wide against twice the conference cutoff.
    """
    pair = predictions[["updated_pred_wins", "actual_win_pct"]].dropna()
    if len(pair) < cutoff * 2:
        return None
    size = cutoff * 2
    predicted_top = set(
        pair["updated_pred_wins"].nlargest(size, keep="all").index[:size]
    )
    actual_top = set(pair["actual_win_pct"].nlargest(size, keep="all").index[:size])
    return len(predicted_top & actual_top) / size


def summarise(predictions: pd.DataFrame, model: TrainedWinModel | None = None) -> dict:
    """League-wide accuracy of both models at the current point in the season."""
    games = predictions["games_played"]
    started = bool(games.gt(0).any())
    summary: dict = {
        "season": predictions["season"].iloc[0] if not predictions.empty else None,
        "as_of_date": date.today().isoformat(),
        "teams": int(len(predictions)),
        "season_started": started,
        "games_played_avg": float(games.mean()) if not predictions.empty else 0.0,
        "games_played_max": float(games.max()) if not predictions.empty else 0.0,
        "season_progress_pct": (
            float(games.mean() / GAMES_PER_SEASON) if not predictions.empty else 0.0
        ),
        "total_games_played": float(games.sum()) if not predictions.empty else 0.0,
    }

    if started:
        actual = predictions["actual_wins"]
        summary.update(
            {
                "mae_preseason_at_gp": _mae(
                    actual, predictions["preseason_expected_wins_at_gp"]
                ),
                "rmse_preseason_at_gp": _rmse(
                    actual, predictions["preseason_expected_wins_at_gp"]
                ),
                "mae_inseason_at_gp": _mae(
                    actual, predictions["inseason_expected_wins_at_gp"]
                ),
                "rmse_inseason_at_gp": _rmse(
                    actual, predictions["inseason_expected_wins_at_gp"]
                ),
                "mae_pace_82_preseason": _mae(
                    predictions["current_82_pace"], predictions["preseason_pred_wins"]
                ),
                "rmse_pace_82_preseason": _rmse(
                    predictions["current_82_pace"], predictions["preseason_pred_wins"]
                ),
                "mae_pace_82_updated": _mae(
                    predictions["current_82_pace"], predictions["updated_pred_wins"]
                ),
                "spearman_rho": _spearman(
                    predictions["actual_win_pct"], predictions["updated_pred_wins"]
                ),
                "spearman_rho_preseason": _spearman(
                    predictions["actual_win_pct"], predictions["preseason_pred_wins"]
                ),
                "playoff_tier_accuracy": _tier_accuracy(predictions, PLAYOFF_SEEDS),
                "play_in_tier_accuracy": _tier_accuracy(predictions, PLAY_IN_SEEDS),
            }
        )
        summary["overperformers"] = _extremes(predictions, ascending=False)
        summary["underperformers"] = _extremes(predictions, ascending=True)
    else:
        for key in (
            "mae_preseason_at_gp",
            "rmse_preseason_at_gp",
            "mae_inseason_at_gp",
            "rmse_inseason_at_gp",
            "mae_pace_82_preseason",
            "rmse_pace_82_preseason",
            "mae_pace_82_updated",
            "spearman_rho",
            "spearman_rho_preseason",
            "playoff_tier_accuracy",
            "play_in_tier_accuracy",
        ):
            summary[key] = None
        summary["overperformers"] = []
        summary["underperformers"] = []

    if model is not None:
        summary["training_seasons"] = model.training_seasons
        summary["cv_mae_preseason"] = model.preseason_metrics.mae
        summary["cv_rmse_preseason"] = model.preseason_metrics.rmse
        summary["cv_r2_preseason"] = model.preseason_metrics.r2
        summary["cv_mae_profile"] = model.profile_metrics.mae
        summary["cv_rmse_profile"] = model.profile_metrics.rmse
        summary["cv_r2_profile"] = model.profile_metrics.r2
    return summary


def _extremes(predictions: pd.DataFrame, *, ascending: bool, count: int = 5) -> list[dict]:
    """Teams furthest from their pre-season expectation, in either direction."""
    columns = [
        "TEAM_ID",
        "TEAM_ABBREVIATION",
        "TEAM_NAME",
        "games_played",
        "actual_wins",
        "actual_losses",
        "preseason_pred_wins",
        "preseason_expected_wins_at_gp",
        "preseason_win_error_at_gp",
        "updated_pred_wins",
        "current_82_pace",
    ]
    present = [c for c in columns if c in predictions.columns]
    ranked = predictions.dropna(subset=["preseason_win_error_at_gp"]).sort_values(
        "preseason_win_error_at_gp", ascending=ascending
    )
    return ranked.head(count)[present].to_dict("records")


def write_predictions(
    predictions: pd.DataFrame,
    season: str,
    *,
    cfg: Settings | None = None,
) -> Path:
    """Replace the curated win_predictions partition for a season."""
    cfg = cfg or settings
    cfg.ensure_dirs()
    body = predictions.drop(columns=["season"], errors="ignore")
    return replace_season_partition(body, cfg.curated_dir / "win_predictions", season)


def load_predictions(season: str, *, cfg: Settings | None = None) -> pd.DataFrame:
    cfg = cfg or settings
    path = cfg.curated_dir / "win_predictions" / f"season={season}" / "data.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"No win predictions for {season}; run `train-wins --target-season {season}`."
        )
    frame = pd.read_parquet(path)
    frame["season"] = season
    return frame


def write_metrics(model: TrainedWinModel, *, cfg: Settings | None = None) -> Path:
    """Persist training accuracy and feature weights for later inspection."""
    cfg = cfg or settings
    cfg.meta_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.meta_dir / "win_model_metrics.json"
    payload = model.metrics_dict()
    payload["trained_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


def load_metrics(cfg: Settings | None = None) -> dict | None:
    cfg = cfg or settings
    path = cfg.meta_dir / "win_model_metrics.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        logger.warning("win_model_metrics.json is unreadable; ignoring")
        return None


def snapshot_rows(predictions: pd.DataFrame, summary: dict) -> pd.DataFrame:
    """Flatten a run into one dated row per team, with the league metrics joined on."""
    frame = predictions.copy()
    frame["as_of_date"] = summary["as_of_date"]
    frame["recorded_at"] = datetime.now(timezone.utc).isoformat()
    scalar_metrics = {
        key: value
        for key, value in summary.items()
        if key
        not in {"overperformers", "underperformers", "training_seasons", "as_of_date", "season"}
        and not isinstance(value, (list, dict))
    }
    for key, value in scalar_metrics.items():
        frame[key] = value
    return frame


def append_snapshot(
    predictions: pd.DataFrame,
    summary: dict,
    season: str,
    *,
    cfg: Settings | None = None,
) -> Path:
    """Append today's snapshot, replacing any earlier run from the same day."""
    cfg = cfg or settings
    cfg.ensure_dirs()
    path = (
        cfg.curated_dir
        / "win_prediction_history"
        / f"season={season}"
        / "history.parquet"
    )
    fresh = snapshot_rows(predictions, summary)

    if path.exists():
        existing = pd.read_parquet(path)
        combined = pd.concat([existing, fresh], ignore_index=True)
        # Latest run for a given day wins, so re-running is always safe.
        combined = combined.drop_duplicates(subset=SNAPSHOT_KEYS, keep="last")
    else:
        combined = fresh

    combined = combined.sort_values(SNAPSHOT_KEYS).reset_index(drop=True)
    write_parquet(combined.drop(columns=["season"], errors="ignore"), path)
    return path


def load_history(season: str, *, cfg: Settings | None = None) -> pd.DataFrame:
    cfg = cfg or settings
    path = (
        cfg.curated_dir
        / "win_prediction_history"
        / f"season={season}"
        / "history.parquet"
    )
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_parquet(path)
    frame["season"] = season
    return frame


ACCURACY_SERIES_COLUMNS = [
    "games_played_avg",
    "season_progress_pct",
    "mae_preseason_at_gp",
    "rmse_preseason_at_gp",
    "mae_inseason_at_gp",
    "rmse_inseason_at_gp",
    "mae_pace_82_preseason",
    "mae_pace_82_updated",
    "spearman_rho",
    "spearman_rho_preseason",
    "playoff_tier_accuracy",
    "play_in_tier_accuracy",
]


def accuracy_timeline(history: pd.DataFrame) -> pd.DataFrame:
    """Collapse the per-team history into one accuracy row per snapshot date."""
    if history.empty or "as_of_date" not in history.columns:
        return pd.DataFrame(columns=["as_of_date", *ACCURACY_SERIES_COLUMNS])
    columns = [c for c in ACCURACY_SERIES_COLUMNS if c in history.columns]
    timeline = (
        history.groupby("as_of_date", as_index=False)[columns]
        .first()
        .sort_values("as_of_date")
        .reset_index(drop=True)
    )
    return timeline


def team_timeline(history: pd.DataFrame, team_id: int) -> pd.DataFrame:
    """One team's actual-versus-expected win trajectory across the season."""
    if history.empty or "TEAM_ID" not in history.columns:
        return pd.DataFrame()
    columns = [
        c
        for c in (
            "as_of_date",
            "games_played",
            "actual_wins",
            "actual_losses",
            "preseason_expected_wins_at_gp",
            "inseason_expected_wins_at_gp",
            "preseason_win_error_at_gp",
            "preseason_pred_wins",
            "updated_pred_wins",
            "current_82_pace",
        )
        if c in history.columns
    ]
    team = history[history["TEAM_ID"] == team_id]
    return team[columns].sort_values("as_of_date").reset_index(drop=True)


def week_over_week(history: pd.DataFrame, as_of: str, days: int = 7) -> dict | None:
    """Change in each accuracy metric versus the closest snapshot a week back.

    Snapshots only exist for days the job ran, so the nearest prior date at or
    before the cutoff is used rather than requiring an exact seven-day match.
    """
    timeline = accuracy_timeline(history)
    if timeline.empty:
        return None

    dates = pd.to_datetime(timeline["as_of_date"], errors="coerce")
    cutoff = pd.Timestamp(as_of) - pd.Timedelta(days=days)
    eligible = timeline[dates <= cutoff]
    if eligible.empty:
        return None

    previous = eligible.iloc[-1]
    current_rows = timeline[timeline["as_of_date"] == as_of]
    current = current_rows.iloc[-1] if not current_rows.empty else timeline.iloc[-1]

    deltas: dict = {"compared_to": previous["as_of_date"]}
    for column in ACCURACY_SERIES_COLUMNS:
        if column not in timeline.columns:
            continue
        now, before = current.get(column), previous.get(column)
        if pd.isna(now) or pd.isna(before):
            continue
        deltas[column] = float(now) - float(before)
    return deltas
