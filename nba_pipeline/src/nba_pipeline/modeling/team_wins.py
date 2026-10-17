"""Team win prediction: pre-season baseline plus in-season trajectory.

Trained on the five completed seasons before the target season. Two ridge
regressions do the work:

* The **pre-season** model learns how a season's record follows from the
  previous season's efficiency profile, and is applied once to produce a locked
  82-game projection for every team.
* The **profile** model learns how an in-season efficiency profile maps onto a
  full-season win percentage, and is re-applied as the target season unfolds.

Projections are reconciled to the league's fixed 1,230 total wins, because one
team's win is always another's loss.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd
from sklearn.linear_model import RidgeCV
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from nba_pipeline.config import (
    GAMES_PER_SEASON,
    LEAGUE_TOTAL_WINS,
    Settings,
    settings,
    training_seasons_for,
)
from nba_pipeline.extract.player_stats import load_curated_player_stats
from nba_pipeline.extract.team_stats import load_curated_team_stats_many
from nba_pipeline.modeling.features import (
    PRESEASON_FEATURES,
    PROFILE_FEATURES,
    build_profile_frame,
    build_transition_frame,
    feature_matrix,
)

logger = logging.getLogger(__name__)

# Ridge penalties spanning four orders of magnitude; RidgeCV picks per fit.
ALPHAS = np.logspace(-2, 3, 50)

# Games after which an in-season profile fully outweighs the pre-season prior.
# Team efficiency stabilises around the quarter-season mark.
PROFILE_TRUST_GAMES = 40


def _estimator() -> Pipeline:
    """Standardised ridge regression with penalty chosen by internal CV."""
    return Pipeline(
        [
            ("scale", StandardScaler()),
            ("ridge", RidgeCV(alphas=ALPHAS)),
        ]
    )


def reconcile_to_league_total(
    predictions: np.ndarray,
    total: float = LEAGUE_TOTAL_WINS,
) -> np.ndarray:
    """Shift projections so they sum to the league's fixed win total.

    An additive shift is used rather than a multiplicative rescale so the gaps
    between teams — the part the model actually learned — survive untouched.
    """
    values = np.asarray(predictions, dtype="float64")
    if values.size == 0:
        return values
    shifted = values + (total - values.sum()) / values.size
    return np.clip(shifted, 0.0, GAMES_PER_SEASON)


@dataclass
class ModelMetrics:
    """Accuracy of one sub-model, measured in wins."""

    name: str
    rows: int
    features: int
    mae: float | None = None
    rmse: float | None = None
    r2: float | None = None
    alpha: float | None = None
    folds: dict[str, float] = field(default_factory=dict)
    coefficients: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def _score(actual: np.ndarray, predicted: np.ndarray) -> tuple[float, float, float]:
    mae = float(mean_absolute_error(actual, predicted))
    rmse = float(np.sqrt(np.mean((np.asarray(actual) - np.asarray(predicted)) ** 2)))
    # R^2 needs at least two distinct observations to mean anything.
    r2 = float(r2_score(actual, predicted)) if len(actual) > 1 else float("nan")
    return mae, rmse, r2


def _leave_one_season_out(
    frame: pd.DataFrame,
    features: list[str],
    target: str,
    name: str,
) -> ModelMetrics:
    """Score the model by holding out one whole season at a time.

    Seasons are the natural fold boundary: league-wide pace and officiating
    shift year to year, so a random split would leak same-season context.
    """
    metrics = ModelMetrics(name=name, rows=len(frame), features=len(features))
    seasons = sorted(frame["SEASON"].unique())
    if len(seasons) < 2:
        logger.warning("%s: need two or more seasons to cross-validate", name)
        return metrics

    actual: list[float] = []
    predicted: list[float] = []
    for holdout in seasons:
        train = frame[frame["SEASON"] != holdout]
        test = frame[frame["SEASON"] == holdout]
        if train.empty or test.empty:
            continue
        model = _estimator()
        model.fit(feature_matrix(train, features), train[target].to_numpy())
        fold_pred = reconcile_to_league_total(
            model.predict(feature_matrix(test, features))
        )
        fold_actual = test[target].to_numpy()
        metrics.folds[holdout] = float(mean_absolute_error(fold_actual, fold_pred))
        actual.extend(fold_actual.tolist())
        predicted.extend(fold_pred.tolist())

    if actual:
        metrics.mae, metrics.rmse, metrics.r2 = _score(
            np.asarray(actual), np.asarray(predicted)
        )
    return metrics


def _fit_full(
    frame: pd.DataFrame,
    features: list[str],
    target: str,
    metrics: ModelMetrics,
) -> Pipeline:
    model = _estimator()
    matrix = feature_matrix(frame, features)
    model.fit(matrix, frame[target].to_numpy())
    ridge = model.named_steps["ridge"]
    metrics.alpha = float(ridge.alpha_)
    metrics.coefficients = {
        feature: float(weight) for feature, weight in zip(features, ridge.coef_)
    }
    return model


@dataclass
class TrainedWinModel:
    """Both fitted sub-models plus the accuracy they achieved in training."""

    target_season: str
    training_seasons: list[str]
    preseason_model: Pipeline | None
    profile_model: Pipeline | None
    preseason_metrics: ModelMetrics
    profile_metrics: ModelMetrics

    def metrics_dict(self) -> dict:
        return {
            "target_season": self.target_season,
            "training_seasons": self.training_seasons,
            "preseason_model": self.preseason_metrics.as_dict(),
            "profile_model": self.profile_metrics.as_dict(),
        }


def load_training_frames(
    target_season: str,
    *,
    cfg: Settings | None = None,
    n: int = 5,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Curated team profiles for the training seasons, and the target season."""
    cfg = cfg or settings
    train_seasons = training_seasons_for(target_season, n)

    team_stats = load_curated_team_stats_many(
        [*train_seasons, target_season], cfg=cfg
    )
    if team_stats.empty:
        raise FileNotFoundError(
            "No curated team_season_stats found; run `refresh --dataset "
            "team_season_stats --all` first."
        )

    player_frames = []
    for season in [*train_seasons, target_season]:
        try:
            player_frames.append(load_curated_player_stats(season, cfg=cfg))
        except FileNotFoundError:
            logger.info("player_season_stats missing for %s; skipping roster feature", season)
    player_stats = (
        pd.concat(player_frames, ignore_index=True) if player_frames else pd.DataFrame()
    )

    profiles = build_profile_frame(team_stats, player_stats)
    training = profiles[profiles["SEASON"].isin(train_seasons)].copy()
    target = profiles[profiles["SEASON"] == target_season].copy()
    return training, target


def train(
    target_season: str,
    *,
    cfg: Settings | None = None,
    n: int = 5,
) -> tuple[TrainedWinModel, pd.DataFrame, pd.DataFrame]:
    """Fit both sub-models on the `n` seasons preceding `target_season`."""
    cfg = cfg or settings
    training, target = load_training_frames(target_season, cfg=cfg, n=n)
    train_seasons = training_seasons_for(target_season, n)

    # Pre-season model: previous season's profile against this season's wins.
    transitions = build_transition_frame(training, train_seasons)
    if transitions.empty:
        preseason_metrics = ModelMetrics(
            name="preseason", rows=0, features=len(PRESEASON_FEATURES)
        )
        preseason_model = None
        logger.warning("No consecutive season pairs available; pre-season model skipped")
    else:
        preseason_metrics = _leave_one_season_out(
            transitions, PRESEASON_FEATURES, "W", "preseason"
        )
        preseason_model = _fit_full(
            transitions, PRESEASON_FEATURES, "W", preseason_metrics
        )

    # Profile model: a season's own efficiency profile against its wins.
    profile_metrics = _leave_one_season_out(training, PROFILE_FEATURES, "W", "profile")
    profile_model = _fit_full(training, PROFILE_FEATURES, "W", profile_metrics)

    model = TrainedWinModel(
        target_season=target_season,
        training_seasons=train_seasons,
        preseason_model=preseason_model,
        profile_model=profile_model,
        preseason_metrics=preseason_metrics,
        profile_metrics=profile_metrics,
    )
    return model, training, target


def profile_trust(games_played: pd.Series) -> pd.Series:
    """Weight on the in-season profile, ramping from 0 to 1 over 40 games."""
    games = pd.to_numeric(games_played, errors="coerce").fillna(0.0).astype("float64")
    return (games / PROFILE_TRUST_GAMES).clip(lower=0.0, upper=1.0)


def predict(
    model: TrainedWinModel,
    training: pd.DataFrame,
    target: pd.DataFrame,
) -> pd.DataFrame:
    """Project target-season wins and measure the models against results so far.

    Produces one row per team carrying the locked pre-season projection, the
    live profile-based projection, the blended final estimate, and the error of
    each model against the wins actually banked to date.
    """
    if target.empty:
        raise ValueError(f"No curated team stats for season {model.target_season}")

    out = target.copy().reset_index(drop=True)
    identity = [
        c
        for c in ("TEAM_ID", "TEAM_NAME", "TEAM_ABBREVIATION")
        if c in out.columns
    ]
    frame = out[identity].copy()
    frame["season"] = model.target_season

    games = pd.to_numeric(out["GP"], errors="coerce").fillna(0.0).astype("float64")
    wins = pd.to_numeric(out["W"], errors="coerce").fillna(0.0).astype("float64")
    losses = pd.to_numeric(out["L"], errors="coerce").fillna(0.0).astype("float64")
    frame["games_played"] = games
    frame["actual_wins"] = wins
    frame["actual_losses"] = losses
    frame["actual_win_pct"] = (wins / games.where(games > 0)).astype("float64")
    frame["current_82_pace"] = frame["actual_win_pct"] * GAMES_PER_SEASON
    frame["net_rating"] = pd.to_numeric(out.get("NET_RATING"), errors="coerce")

    frame["preseason_pred_wins"] = _preseason_projection(model, training, out)
    frame["inseason_talent_wins_82"] = _profile_projection(model, out, games)

    # Expected wins by this point in the schedule, under each model's pace.
    per_game_preseason = frame["preseason_pred_wins"] / GAMES_PER_SEASON
    per_game_profile = frame["inseason_talent_wins_82"] / GAMES_PER_SEASON
    frame["preseason_expected_wins_at_gp"] = per_game_preseason * games
    frame["inseason_expected_wins_at_gp"] = per_game_profile * games
    frame["preseason_win_error_at_gp"] = wins - frame["preseason_expected_wins_at_gp"]
    frame["inseason_win_error_at_gp"] = wins - frame["inseason_expected_wins_at_gp"]

    # Banked wins are certain; only the remaining schedule is projected.
    trust = profile_trust(games)
    blended_rate = (1 - trust) * per_game_preseason + trust * per_game_profile
    remaining = (GAMES_PER_SEASON - games).clip(lower=0.0)
    frame["profile_trust"] = trust
    frame["updated_pred_wins"] = (wins + remaining * blended_rate).clip(
        lower=0.0, upper=GAMES_PER_SEASON
    )

    frame["preseason_rank"] = frame["preseason_pred_wins"].rank(
        ascending=False, method="min"
    )
    frame["updated_rank"] = frame["updated_pred_wins"].rank(
        ascending=False, method="min"
    )
    # Before any games are played every team is tied, so there is no rank yet.
    frame["actual_rank"] = (
        frame["actual_win_pct"].rank(ascending=False, method="min")
        if games.gt(0).any()
        else np.nan
    )
    return frame.sort_values("updated_pred_wins", ascending=False).reset_index(drop=True)


def rescore_with_preseason(predictions: pd.DataFrame) -> pd.DataFrame:
    """Recompute every derived column after `preseason_pred_wins` was replaced.

    Used when a locked pre-season forecast is restored over a freshly fitted
    one, so the error and blend columns describe the forecast actually made.
    """
    frame = predictions.copy()
    games = frame["games_played"].astype("float64")
    wins = frame["actual_wins"].astype("float64")

    per_game_preseason = frame["preseason_pred_wins"] / GAMES_PER_SEASON
    per_game_profile = frame["inseason_talent_wins_82"] / GAMES_PER_SEASON
    frame["preseason_expected_wins_at_gp"] = per_game_preseason * games
    frame["inseason_expected_wins_at_gp"] = per_game_profile * games
    frame["preseason_win_error_at_gp"] = wins - frame["preseason_expected_wins_at_gp"]
    frame["inseason_win_error_at_gp"] = wins - frame["inseason_expected_wins_at_gp"]

    trust = profile_trust(games)
    blended_rate = (1 - trust) * per_game_preseason + trust * per_game_profile
    remaining = (GAMES_PER_SEASON - games).clip(lower=0.0)
    frame["profile_trust"] = trust
    frame["updated_pred_wins"] = (wins + remaining * blended_rate).clip(
        lower=0.0, upper=GAMES_PER_SEASON
    )

    frame["preseason_rank"] = frame["preseason_pred_wins"].rank(
        ascending=False, method="min"
    )
    frame["updated_rank"] = frame["updated_pred_wins"].rank(
        ascending=False, method="min"
    )
    return frame.sort_values("updated_pred_wins", ascending=False).reset_index(drop=True)


def _preseason_projection(
    model: TrainedWinModel,
    training: pd.DataFrame,
    target: pd.DataFrame,
) -> pd.Series:
    """Apply the transition model to the season immediately before the target."""
    index = target.index
    if model.preseason_model is None:
        return pd.Series(
            np.full(len(target), LEAGUE_TOTAL_WINS / len(target)), index=index
        )

    prior_season = model.training_seasons[-1]
    prior = training[training["SEASON"] == prior_season]
    if prior.empty:
        logger.warning("No %s profile available for the pre-season model", prior_season)
        return pd.Series(
            np.full(len(target), LEAGUE_TOTAL_WINS / len(target)), index=index
        )

    pairs = build_transition_frame(
        pd.concat([prior, target.assign(SEASON=model.target_season)], ignore_index=True),
        [prior_season, model.target_season],
    )
    if pairs.empty:
        return pd.Series(
            np.full(len(target), LEAGUE_TOTAL_WINS / len(target)), index=index
        )

    predicted = reconcile_to_league_total(
        model.preseason_model.predict(feature_matrix(pairs, PRESEASON_FEATURES))
    )
    by_team = dict(zip(pairs["TEAM_ID"].astype("int64"), predicted))
    # A team with no prior-season counterpart falls back to a .500 baseline.
    fallback = LEAGUE_TOTAL_WINS / len(target)
    return pd.Series(
        [by_team.get(int(t), fallback) for t in target["TEAM_ID"]], index=index
    )


def _profile_projection(
    model: TrainedWinModel,
    target: pd.DataFrame,
    games: pd.Series,
) -> pd.Series:
    """Apply the profile model to the target season's efficiency to date."""
    index = target.index
    if model.profile_model is None or not games.gt(0).any():
        # With no games played there is no profile to read; stay at .500.
        return pd.Series(
            np.full(len(target), LEAGUE_TOTAL_WINS / len(target)), index=index
        )
    predicted = reconcile_to_league_total(
        model.profile_model.predict(feature_matrix(target, PROFILE_FEATURES))
    )
    return pd.Series(predicted, index=index)
