"""Win-prediction tests over a synthetic warehouse; nothing here touches stats.nba.com."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from nba_pipeline.config import (
    GAMES_PER_SEASON,
    LEAGUE_TOTAL_WINS,
    Settings,
    current_season,
    season_start_year,
    training_seasons_for,
    warehouse_seasons,
)
from nba_pipeline.extract.rosters import load_season_roster
from nba_pipeline.extract.team_stats import preseason_skeleton
from nba_pipeline.load.parquet import replace_season_partition, write_static_table
from nba_pipeline.modeling import team_wins, tracking
from nba_pipeline.modeling.features import (
    TEAM_SEASON_MINUTES,
    build_profile_frame,
    build_transition_frame,
    minute_weighted_plus_minus,
    projected_roster_strength,
    pythagorean_win_pct,
    roster_strength_by_season,
)
from nba_pipeline.transform.clean import clean_rosters
from nba_pipeline.reporting import email_sender, weekly_win_report

TARGET = "2026-27"
TRAINING = ["2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]
TEAMS = 30


# --- Season windows -------------------------------------------------------


def test_training_window_excludes_the_target_season():
    assert training_seasons_for(TARGET, 5) == TRAINING
    assert TARGET not in training_seasons_for(TARGET, 5)
    assert warehouse_seasons(TARGET, 5) == [*TRAINING, TARGET]


def test_season_labels_roll_over_the_century_correctly():
    assert training_seasons_for("2000-01", 2) == ["1998-99", "1999-00"]
    assert season_start_year("2026-27") == 2026
    with pytest.raises(ValueError):
        season_start_year("not-a-season")


def test_current_season_flips_in_october():
    from datetime import date

    assert current_season(date(2026, 10, 17)) == "2026-27"
    assert current_season(date(2026, 9, 30)) == "2025-26"


def test_training_window_needs_at_least_one_season():
    with pytest.raises(ValueError):
        training_seasons_for(TARGET, 0)


# --- Features -------------------------------------------------------------


def test_pythagorean_win_pct_is_centred_and_monotonic():
    scored = pd.Series([110.0, 120.0, 100.0])
    allowed = pd.Series([110.0, 100.0, 120.0])
    result = pythagorean_win_pct(scored, allowed)
    assert result.iloc[0] == pytest.approx(0.5)
    assert result.iloc[1] > 0.5 > result.iloc[2]
    # A better offence against the same defence can only help.
    better = pythagorean_win_pct(pd.Series([118.0, 114.0]), pd.Series([110.0, 110.0]))
    assert better.iloc[0] > better.iloc[1] > 0.5


def test_pythagorean_win_pct_rejects_impossible_ratings():
    result = pythagorean_win_pct(pd.Series([0.0, -5.0, np.nan]), pd.Series([110.0, 110.0, 110.0]))
    assert result.isna().all()


def test_minute_weighted_plus_minus_follows_the_rotation():
    players = pd.DataFrame(
        {
            "TEAM_ID": [1, 1, 2, 2],
            # A starter's impact should outweigh a deep-bench cameo.
            "MIN": [2000.0, 50.0, 1000.0, 1000.0],
            "PLUS_MINUS": [10.0, -30.0, 4.0, 0.0],
        }
    )
    result = minute_weighted_plus_minus(players).set_index("TEAM_ID")
    assert result.loc[1, "MINUTE_WEIGHTED_PLUS_MINUS"] == pytest.approx(
        (2000 * 10 + 50 * -30) / 2050
    )
    assert result.loc[2, "MINUTE_WEIGHTED_PLUS_MINUS"] == pytest.approx(2.0)


def test_minute_weighted_plus_minus_ignores_unplayed_rows():
    players = pd.DataFrame(
        {
            "TEAM_ID": [1, 1, 1],
            "MIN": [0.0, np.nan, 500.0],
            "PLUS_MINUS": [99.0, 99.0, 3.0],
        }
    )
    result = minute_weighted_plus_minus(players)
    assert result.loc[0, "MINUTE_WEIGHTED_PLUS_MINUS"] == pytest.approx(3.0)
    assert minute_weighted_plus_minus(pd.DataFrame()).empty


def test_roster_strength_follows_a_player_to_his_new_team():
    last_season = pd.DataFrame(
        {
            "PLAYER_ID": [1, 2, 3],
            "TEAM_ID": [10, 10, 20],
            "MIN": [2500.0, 1500.0, 2000.0],
            "PLUS_MINUS": [12.0, -2.0, -4.0],
        }
    )
    # Player 1 moved from team 10 to team 20 over the summer; 4 is a rookie.
    roster = pd.DataFrame({"PLAYER_ID": [1, 2, 3, 4], "TEAM_ID": [20, 10, 20, 10]})
    result = projected_roster_strength(roster, last_season).set_index("TEAM_ID")

    assert result.loc[20, "ROSTER_PLUS_MINUS"] == pytest.approx(
        (2500 * 12 + 2000 * -4) / 4500
    )
    # The rookie has no NBA minutes, so team 10 is rated on player 2 alone.
    assert result.loc[10, "ROSTER_PLUS_MINUS"] == pytest.approx(-2.0)
    assert result.loc[20, "ROSTER_PROVEN_MINUTES"] == pytest.approx(
        4500 / TEAM_SEASON_MINUTES
    )
    assert result.loc[10, "ROSTER_PROVEN_MINUTES"] == pytest.approx(
        1500 / TEAM_SEASON_MINUTES
    )


def test_a_roster_of_newcomers_has_no_proven_minutes():
    last_season = pd.DataFrame(
        {"PLAYER_ID": [1], "TEAM_ID": [10], "MIN": [2000.0], "PLUS_MINUS": [3.0]}
    )
    roster = pd.DataFrame({"PLAYER_ID": [1, 7, 8], "TEAM_ID": [10, 30, 30]})
    result = projected_roster_strength(roster, last_season).set_index("TEAM_ID")
    assert np.isnan(result.loc[30, "ROSTER_PLUS_MINUS"])
    assert result.loc[30, "ROSTER_PROVEN_MINUTES"] == 0.0

    # Without any prior season the roster is unknown, not empty.
    unknown = projected_roster_strength(roster, pd.DataFrame())
    assert unknown[["ROSTER_PLUS_MINUS", "ROSTER_PROVEN_MINUTES"]].isna().all().all()
    assert projected_roster_strength(pd.DataFrame(), pd.DataFrame()).empty


def test_roster_strength_rates_each_season_with_the_one_before():
    players = pd.concat(
        [player_frame(season, offset=i) for i, season in enumerate(TRAINING)],
        ignore_index=True,
    )
    rosters = players[["SEASON", "PLAYER_ID", "TEAM_ID"]]
    strength = roster_strength_by_season(rosters, players)

    assert set(strength.columns) == {
        "SEASON",
        "TEAM_ID",
        "ROSTER_PLUS_MINUS",
        "ROSTER_PROVEN_MINUTES",
    }
    assert sorted(strength["SEASON"].unique()) == TRAINING
    # The earliest season has no prior season on disk, which is unknown rather
    # than a roster of rookies, so both features stay missing.
    first = strength[strength["SEASON"] == TRAINING[0]]
    assert first[["ROSTER_PLUS_MINUS", "ROSTER_PROVEN_MINUTES"]].isna().all().all()

    # 2022-23 must be rated with 2021-22 numbers, never its own.
    second = strength[strength["SEASON"] == TRAINING[1]].set_index("TEAM_ID")
    expected = minute_weighted_plus_minus(
        players[players["SEASON"] == TRAINING[0]]
    ).set_index("TEAM_ID")
    assert second.loc[5, "ROSTER_PLUS_MINUS"] == pytest.approx(
        expected.loc[5, "MINUTE_WEIGHTED_PLUS_MINUS"]
    )


def test_clean_rosters_drops_free_agents_and_duplicates():
    raw = pd.DataFrame(
        {
            "PERSON_ID": [1, 2, 2, 3],
            "DISPLAY_FIRST_LAST": ["A", "B", "B", "C"],
            "TEAM_ID": [10, 20, 20, 0],
            "TEAM_ABBREVIATION": ["AAA", "BBB", "BBB", ""],
            "ROSTERSTATUS": [1, 1, 1, 0],
        }
    )
    cleaned = clean_rosters(raw)
    assert list(cleaned.columns) == ["PLAYER_ID", "PLAYER_NAME", "TEAM_ID", "TEAM_ABBREVIATION"]
    assert cleaned["PLAYER_ID"].tolist() == [1, 2]


def test_season_roster_prefers_a_snapshot_over_player_stats(preseason):
    # The fixture snapshots the target season and leaves past seasons to stats.
    target = load_season_roster(TARGET, cfg=preseason)
    assert len(target) == TEAMS * 8
    assert (target["SEASON"] == TARGET).all()

    past = load_season_roster(TRAINING[-1], cfg=preseason)
    assert len(past) == TEAMS * 8
    assert load_season_roster("1999-00", cfg=preseason).empty


def test_transition_frame_pairs_consecutive_seasons_only():
    profiles = build_profile_frame(
        pd.concat([team_frame(season) for season in TRAINING], ignore_index=True)
    )
    pairs = build_transition_frame(profiles, TRAINING)
    # Five seasons yield four consecutive pairs.
    assert len(pairs) == TEAMS * (len(TRAINING) - 1)
    assert sorted(pairs["SEASON"].unique()) == TRAINING[1:]
    for season, prior in zip(TRAINING[1:], TRAINING):
        assert (pairs.loc[pairs["SEASON"] == season, "PRIOR_SEASON"] == prior).all()


def test_transition_frame_skips_teams_missing_a_prior_season():
    profiles = build_profile_frame(
        pd.concat(
            [team_frame(TRAINING[0]).iloc[:5], team_frame(TRAINING[1])], ignore_index=True
        )
    )
    pairs = build_transition_frame(profiles, TRAINING[:2])
    assert len(pairs) == 5


# --- Synthetic warehouse --------------------------------------------------


def team_frame(season: str, games: int = GAMES_PER_SEASON, seed: int | None = None) -> pd.DataFrame:
    """A 30-team season whose records follow directly from its efficiency.

    Wins are derived from net rating so the model has real signal to learn,
    and the league always banks exactly half the games it played.
    """
    rng = np.random.default_rng(seed if seed is not None else season_start_year(season))
    # A spread of net ratings resembling a real league, centred on zero.
    net = np.linspace(-10.0, 10.0, TEAMS) + rng.normal(0, 0.4, TEAMS)
    net = net - net.mean()
    win_pct = np.clip(0.5 + net / 40.0, 0.05, 0.95)
    wins = np.round(win_pct * games).astype(int)
    # Force the league to bank exactly half of the games it played.
    target_total = TEAMS * games // 2
    while wins.sum() != target_total:
        step = 1 if wins.sum() < target_total else -1
        index = int(rng.integers(0, TEAMS))
        if 0 <= wins[index] + step <= games:
            wins[index] += step

    offence = 112.0 + net / 2 + rng.normal(0, 0.3, TEAMS)
    return pd.DataFrame(
        {
            "TEAM_ID": np.arange(1, TEAMS + 1),
            "TEAM_NAME": [f"Team {i}" for i in range(1, TEAMS + 1)],
            "TEAM_ABBREVIATION": [f"T{i:02d}" for i in range(1, TEAMS + 1)],
            "GP": games,
            "W": wins,
            "L": games - wins,
            "W_PCT": wins / games if games else np.nan,
            "OFF_RATING": offence,
            "DEF_RATING": offence - net,
            "NET_RATING": net,
            "PACE": 99.0 + rng.normal(0, 1.0, TEAMS),
            "EFG_PCT": 0.54 + net / 400,
            "TM_TOV_PCT": 0.135 - net / 2000,
            "OREB_PCT": 0.29 + net / 900,
            "DREB_PCT": 0.71 + net / 900,
            "FTA_RATE": 0.26 + rng.normal(0, 0.01, TEAMS),
            "TS_PCT": 0.575 + net / 400,
            "PIE": 0.5 + net / 200,
            "PLUS_MINUS": net,
            "SEASON": season,
        }
    )


def player_frame(season: str, offset: int = 0) -> pd.DataFrame:
    """Eight players per team, with minutes and impact tracking team strength."""
    rows = []
    for team in range(1, TEAMS + 1):
        strength = (team - TEAMS / 2) / 3.0 + offset * 0.01
        for slot in range(8):
            rows.append(
                {
                    "SEASON": season,
                    "PLAYER_ID": team * 100 + slot,
                    "PLAYER_NAME": f"Player {team}-{slot}",
                    "TEAM_ID": team,
                    "MIN": 2200.0 - slot * 230.0,
                    "PLUS_MINUS": strength - slot * 0.4,
                    "PTS": 25.0 - slot,
                }
            )
    return pd.DataFrame(rows)


def build_warehouse(
    tmp_path,
    *,
    target_games: int = 0,
    target_seed: int | None = None,
) -> Settings:
    """A curated warehouse with five finished seasons and a target season."""
    cfg = Settings(project_root=tmp_path, target_season=TARGET, training_season_count=5)
    cfg.ensure_dirs()

    teams = pd.DataFrame(
        {
            "TEAM_ID": np.arange(1, TEAMS + 1),
            "FULL_NAME": [f"Team {i}" for i in range(1, TEAMS + 1)],
            "ABBREVIATION": [f"T{i:02d}" for i in range(1, TEAMS + 1)],
        }
    )
    write_static_table(teams, cfg.curated_dir / "teams", "teams")

    for season in TRAINING:
        body = team_frame(season).drop(columns=["SEASON"])
        replace_season_partition(body, cfg.curated_dir / "team_season_stats", season)
        replace_season_partition(
            player_frame(season).drop(columns=["SEASON"]),
            cfg.curated_dir / "player_season_stats",
            season,
        )

    if target_games > 0:
        target = team_frame(TARGET, games=target_games, seed=target_seed)
        replace_season_partition(
            target.drop(columns=["SEASON"]), cfg.curated_dir / "team_season_stats", TARGET
        )
        replace_season_partition(
            player_frame(TARGET).drop(columns=["SEASON"]),
            cfg.curated_dir / "player_season_stats",
            TARGET,
        )
    else:
        skeleton = preseason_skeleton(cfg)
        replace_season_partition(
            skeleton, cfg.curated_dir / "team_season_stats", TARGET
        )
    # Opening-night rosters: every player stays where he finished last season.
    roster = player_frame(TARGET)[["PLAYER_ID", "PLAYER_NAME", "TEAM_ID"]]
    replace_season_partition(roster, cfg.curated_dir / "rosters", TARGET)
    return cfg


@pytest.fixture
def preseason(tmp_path):
    """The real situation in October 2026: trained, projected, no games played."""
    return build_warehouse(tmp_path, target_games=0)


@pytest.fixture
def midseason(tmp_path):
    return build_warehouse(tmp_path, target_games=30, target_seed=99)


# --- Pre-season skeleton --------------------------------------------------


def test_preseason_skeleton_covers_every_team_at_zero_games(preseason):
    skeleton = preseason_skeleton(preseason)
    assert len(skeleton) == TEAMS
    assert (skeleton["GP"] == 0).all()
    assert (skeleton["W"] == 0).all()
    assert skeleton["TEAM_ABBREVIATION"].notna().all()
    # The full schema keeps the partition stackable with completed seasons.
    assert "NET_RATING" in skeleton.columns


# --- Reconciliation -------------------------------------------------------


def test_projections_reconcile_to_the_league_win_total():
    reconciled = team_wins.reconcile_to_league_total(np.full(TEAMS, 10.0))
    assert reconciled.sum() == pytest.approx(LEAGUE_TOTAL_WINS)
    assert reconciled.std() == pytest.approx(0.0)


def test_reconciliation_preserves_the_gaps_between_teams():
    raw = np.linspace(20.0, 60.0, TEAMS)
    reconciled = team_wins.reconcile_to_league_total(raw)
    assert reconciled.sum() == pytest.approx(LEAGUE_TOTAL_WINS)
    assert np.allclose(np.diff(reconciled), np.diff(raw))


def test_reconciliation_clamps_to_a_possible_record():
    reconciled = team_wins.reconcile_to_league_total(np.full(TEAMS, -500.0))
    assert (reconciled >= 0).all()
    assert (reconciled <= GAMES_PER_SEASON).all()
    assert team_wins.reconcile_to_league_total(np.array([])).size == 0


def test_profile_trust_ramps_over_the_first_forty_games():
    trust = team_wins.profile_trust(pd.Series([0, 10, 20, 40, 60, 82]))
    assert trust.tolist() == [0.0, 0.25, 0.5, 1.0, 1.0, 1.0]


# --- Training -------------------------------------------------------------


def test_training_uses_five_seasons_and_learns_real_signal(preseason):
    model, training, target = team_wins.train(TARGET, cfg=preseason)

    assert model.training_seasons == TRAINING
    assert len(training) == TEAMS * len(TRAINING)
    assert len(target) == TEAMS

    # Each season is held out once, and the pre-season model loses the first.
    assert set(model.profile_metrics.folds) == set(TRAINING)
    assert set(model.preseason_metrics.folds) == set(TRAINING[1:])
    assert model.profile_metrics.rows == TEAMS * len(TRAINING)
    assert model.preseason_metrics.rows == TEAMS * (len(TRAINING) - 1)

    # Wins were generated from efficiency, so the profile model must find it.
    assert model.profile_metrics.mae < 3.0
    assert model.profile_metrics.r2 > 0.9
    assert model.preseason_metrics.mae < 12.0


def test_training_requires_curated_team_stats(tmp_path):
    empty = Settings(project_root=tmp_path, target_season=TARGET)
    empty.ensure_dirs()
    with pytest.raises(FileNotFoundError):
        team_wins.train(TARGET, cfg=empty)


# --- Pre-season prediction ------------------------------------------------


def test_preseason_prediction_has_no_in_season_information(preseason):
    model, training, target = team_wins.train(TARGET, cfg=preseason)
    predictions = team_wins.predict(model, training, target)

    assert len(predictions) == TEAMS
    assert (predictions["games_played"] == 0).all()
    assert (predictions["actual_wins"] == 0).all()
    assert predictions["actual_win_pct"].isna().all()
    assert predictions["current_82_pace"].isna().all()
    # With nothing played there is no standings order to rank.
    assert predictions["actual_rank"].isna().all()

    assert predictions["preseason_pred_wins"].sum() == pytest.approx(LEAGUE_TOTAL_WINS)
    # No profile exists yet, so the blended projection is the pre-season one.
    assert (predictions["profile_trust"] == 0).all()
    assert predictions["updated_pred_wins"].to_numpy() == pytest.approx(
        predictions["preseason_pred_wins"].to_numpy()
    )
    assert (predictions["inseason_talent_wins_82"] == GAMES_PER_SEASON / 2).all()
    assert predictions["preseason_rank"].nunique() == TEAMS


def test_an_offseason_trade_moves_the_preseason_projection(preseason):
    model, training, target = team_wins.train(TARGET, cfg=preseason)
    before = team_wins.predict(model, training, target).set_index("TEAM_ID")

    # Team 30's two best players are traded to team 1 over the summer.
    roster = pd.read_parquet(
        preseason.curated_dir / "rosters" / f"season={TARGET}" / "data.parquet"
    )
    roster.loc[roster["PLAYER_ID"].isin([3000, 3001]), "TEAM_ID"] = 1
    replace_season_partition(roster, preseason.curated_dir / "rosters", TARGET)

    model, training, target = team_wins.train(TARGET, cfg=preseason)
    after = team_wins.predict(model, training, target).set_index("TEAM_ID")

    assert model.preseason_metrics.coefficients["ROSTER_PLUS_MINUS"] > 0
    assert after.loc[1, "preseason_pred_wins"] > before.loc[1, "preseason_pred_wins"]
    assert after.loc[30, "preseason_pred_wins"] < before.loc[30, "preseason_pred_wins"]


def test_preseason_summary_reports_no_accuracy_yet(preseason):
    model, training, target = team_wins.train(TARGET, cfg=preseason)
    summary = tracking.summarise(team_wins.predict(model, training, target), model)

    assert summary["season_started"] is False
    assert summary["games_played_avg"] == 0.0
    assert summary["season_progress_pct"] == 0.0
    for key in ("mae_preseason_at_gp", "mae_inseason_at_gp", "spearman_rho"):
        assert summary[key] is None
    assert summary["overperformers"] == []
    assert summary["underperformers"] == []
    # Training accuracy is known even before a game is played.
    assert summary["cv_mae_profile"] == pytest.approx(model.profile_metrics.mae)
    assert summary["training_seasons"] == TRAINING


# --- In-season tracking ---------------------------------------------------


def test_midseason_prediction_scores_both_models(midseason):
    model, training, target = team_wins.train(TARGET, cfg=midseason)
    predictions = team_wins.predict(model, training, target)
    games = 30

    assert (predictions["games_played"] == games).all()
    assert predictions["actual_wins"].sum() == pytest.approx(TEAMS * games / 2)
    assert predictions["current_82_pace"].to_numpy() == pytest.approx(
        (predictions["actual_wins"] / games * GAMES_PER_SEASON).to_numpy()
    )
    # Expected wins follow each model's pace over the games actually played.
    assert predictions["preseason_expected_wins_at_gp"].to_numpy() == pytest.approx(
        (predictions["preseason_pred_wins"] / GAMES_PER_SEASON * games).to_numpy()
    )
    assert predictions["preseason_win_error_at_gp"].to_numpy() == pytest.approx(
        (predictions["actual_wins"] - predictions["preseason_expected_wins_at_gp"]).to_numpy()
    )
    assert predictions["profile_trust"].to_numpy() == pytest.approx(0.75)
    # Banked wins are certain, so no projection may fall below them.
    assert (predictions["updated_pred_wins"] >= predictions["actual_wins"] - 1e-9).all()
    assert (predictions["updated_pred_wins"] <= GAMES_PER_SEASON).all()
    assert predictions["inseason_talent_wins_82"].sum() == pytest.approx(LEAGUE_TOTAL_WINS)


def test_midseason_summary_populates_every_accuracy_metric(midseason):
    model, training, target = team_wins.train(TARGET, cfg=midseason)
    summary = tracking.summarise(team_wins.predict(model, training, target), model)

    assert summary["season_started"] is True
    assert summary["games_played_avg"] == 30.0
    assert summary["season_progress_pct"] == pytest.approx(30 / GAMES_PER_SEASON)
    for key in (
        "mae_preseason_at_gp",
        "rmse_preseason_at_gp",
        "mae_inseason_at_gp",
        "mae_pace_82_preseason",
        "mae_pace_82_updated",
        "spearman_rho",
        "playoff_tier_accuracy",
    ):
        assert summary[key] is not None, key
    # RMSE penalises large misses, so it can never sit below MAE.
    assert summary["rmse_preseason_at_gp"] >= summary["mae_preseason_at_gp"]
    assert -1.0 <= summary["spearman_rho"] <= 1.0
    assert len(summary["overperformers"]) == 5
    assert len(summary["underperformers"]) == 5
    assert summary["overperformers"][0]["preseason_win_error_at_gp"] >= 0
    assert summary["underperformers"][0]["preseason_win_error_at_gp"] <= 0


def test_in_season_model_beats_the_preseason_model_on_the_same_games(midseason):
    """Reading the season being played should beat a forecast made before it."""
    model, training, target = team_wins.train(TARGET, cfg=midseason)
    summary = tracking.summarise(team_wins.predict(model, training, target), model)
    assert summary["mae_inseason_at_gp"] < summary["mae_preseason_at_gp"]
    assert summary["mae_pace_82_updated"] < summary["mae_pace_82_preseason"]


def test_a_finished_season_is_projected_to_its_actual_record(tmp_path):
    cfg = build_warehouse(tmp_path, target_games=GAMES_PER_SEASON, target_seed=7)
    model, training, target = team_wins.train(TARGET, cfg=cfg)
    predictions = team_wins.predict(model, training, target)
    # With no games left, the projection is the record.
    assert predictions["updated_pred_wins"].to_numpy() == pytest.approx(
        predictions["actual_wins"].to_numpy()
    )
    assert predictions["current_82_pace"].to_numpy() == pytest.approx(
        predictions["actual_wins"].to_numpy()
    )


def test_rescore_preserves_a_locked_preseason_forecast(midseason):
    model, training, target = team_wins.train(TARGET, cfg=midseason)
    predictions = team_wins.predict(model, training, target)

    locked = predictions.copy()
    locked["preseason_pred_wins"] = 41.0
    rescored = team_wins.rescore_with_preseason(locked)

    games = rescored["games_played"]
    assert rescored["preseason_expected_wins_at_gp"].to_numpy() == pytest.approx(
        (41.0 / GAMES_PER_SEASON * games).to_numpy()
    )
    assert rescored["preseason_win_error_at_gp"].to_numpy() == pytest.approx(
        (rescored["actual_wins"] - rescored["preseason_expected_wins_at_gp"]).to_numpy()
    )
    # Every team shared the same forecast, so the ranking collapses to a tie.
    assert rescored["preseason_rank"].nunique() == 1


def test_predict_rejects_a_season_with_no_teams(preseason):
    model, training, _ = team_wins.train(TARGET, cfg=preseason)
    with pytest.raises(ValueError):
        team_wins.predict(model, training, pd.DataFrame())


# --- Persistence and snapshots -------------------------------------------


def test_predictions_and_metrics_round_trip(preseason):
    model, training, target = team_wins.train(TARGET, cfg=preseason)
    predictions = team_wins.predict(model, training, target)

    tracking.write_predictions(predictions, TARGET, cfg=preseason)
    reloaded = tracking.load_predictions(TARGET, cfg=preseason)
    assert len(reloaded) == TEAMS
    assert reloaded["preseason_pred_wins"].sum() == pytest.approx(LEAGUE_TOTAL_WINS)

    tracking.write_metrics(model, cfg=preseason)
    stored = tracking.load_metrics(preseason)
    assert stored["training_seasons"] == TRAINING
    assert stored["profile_model"]["mae"] == pytest.approx(model.profile_metrics.mae)
    assert stored["trained_at"]


def test_missing_predictions_raise_a_helpful_error(preseason):
    with pytest.raises(FileNotFoundError, match="train-wins"):
        tracking.load_predictions("1999-00", cfg=preseason)
    assert tracking.load_metrics(preseason) is None


def test_snapshots_accumulate_one_row_per_team_per_day(midseason):
    model, training, target = team_wins.train(TARGET, cfg=midseason)
    predictions = team_wins.predict(model, training, target)

    for day in ("2026-11-07", "2026-11-14", "2026-11-21"):
        summary = tracking.summarise(predictions, model)
        summary["as_of_date"] = day
        tracking.append_snapshot(predictions, summary, TARGET, cfg=midseason)

    history = tracking.load_history(TARGET, cfg=midseason)
    assert len(history) == TEAMS * 3
    assert sorted(history["as_of_date"].unique()) == ["2026-11-07", "2026-11-14", "2026-11-21"]


def test_rerunning_the_same_day_replaces_rather_than_duplicates(midseason):
    model, training, target = team_wins.train(TARGET, cfg=midseason)
    predictions = team_wins.predict(model, training, target)
    summary = tracking.summarise(predictions, model)
    summary["as_of_date"] = "2026-11-07"

    tracking.append_snapshot(predictions, summary, TARGET, cfg=midseason)
    bumped = predictions.copy()
    bumped["actual_wins"] = bumped["actual_wins"] + 1
    tracking.append_snapshot(bumped, summary, TARGET, cfg=midseason)

    history = tracking.load_history(TARGET, cfg=midseason)
    assert len(history) == TEAMS
    # The later run is the one that survives.
    assert history["actual_wins"].sum() == pytest.approx(
        predictions["actual_wins"].sum() + TEAMS
    )


def test_history_is_empty_before_the_first_snapshot(preseason):
    assert tracking.load_history(TARGET, cfg=preseason).empty
    assert tracking.accuracy_timeline(pd.DataFrame()).empty
    assert tracking.team_timeline(pd.DataFrame(), 1).empty
    assert tracking.week_over_week(pd.DataFrame(), "2026-11-07") is None


def test_timelines_collapse_history_to_one_row_per_date(midseason):
    history = seeded_history(midseason)
    timeline = tracking.accuracy_timeline(history)
    assert len(timeline) == 3
    assert list(timeline["as_of_date"]) == ["2026-11-07", "2026-11-14", "2026-11-21"]
    assert timeline["games_played_avg"].is_monotonic_increasing

    team = tracking.team_timeline(history, 1)
    assert len(team) == 3
    assert list(team["as_of_date"]) == ["2026-11-07", "2026-11-14", "2026-11-21"]
    assert tracking.team_timeline(history, 999).empty


def test_week_over_week_compares_against_the_nearest_prior_snapshot(midseason):
    history = seeded_history(midseason)
    deltas = tracking.week_over_week(history, "2026-11-21")
    assert deltas["compared_to"] == "2026-11-14"
    assert deltas["mae_preseason_at_gp"] == pytest.approx(-1.0)
    assert deltas["games_played_avg"] == pytest.approx(5.0)

    # The first snapshot has nothing a week older to compare with.
    assert tracking.week_over_week(history, "2026-11-07") is None


def seeded_history(cfg: Settings) -> pd.DataFrame:
    """Three weekly snapshots with steadily improving accuracy."""
    model, training, target = team_wins.train(TARGET, cfg=cfg)
    predictions = team_wins.predict(model, training, target)
    for index, day in enumerate(("2026-11-07", "2026-11-14", "2026-11-21")):
        summary = tracking.summarise(predictions, model)
        summary["as_of_date"] = day
        summary["games_played_avg"] = 20.0 + index * 5
        summary["mae_preseason_at_gp"] = 5.0 - index
        tracking.append_snapshot(predictions, summary, TARGET, cfg=cfg)
    return tracking.load_history(TARGET, cfg=cfg)


# --- Weekly report --------------------------------------------------------


def report_for(cfg: Settings, as_of: str = "2026-11-21") -> weekly_win_report.WeeklyReport:
    model, training, target = team_wins.train(TARGET, cfg=cfg)
    predictions = team_wins.predict(model, training, target)
    summary = tracking.summarise(predictions, model)
    # Report as of the newest seeded snapshot so the weekly delta has a pair.
    summary["as_of_date"] = as_of
    history = seeded_history(cfg)
    return weekly_win_report.build(predictions, summary, history=history, cfg=cfg)


def test_preseason_report_explains_that_nothing_has_been_played(preseason):
    report = report_for(preseason)
    assert "pre-season projection" in report.subject
    assert "has not tipped off" in report.text_body
    assert "PRE-SEASON PROJECTION" in report.text_body
    # In-season columns carry no information yet, so they stay out.
    assert "Exp W" not in report.text_body
    assert "Proj W" in report.text_body
    assert report.html_body.startswith("<!DOCTYPE html>")


def test_midseason_report_leads_with_accuracy(midseason):
    report = report_for(midseason)
    assert "win MAE through 30 games" in report.subject
    assert "MODEL ACCURACY" in report.text_body
    assert "BIGGEST OVERPERFORMERS" in report.text_body
    assert "BIGGEST UNDERPERFORMERS" in report.text_body
    assert "CROSS-VALIDATED TRAINING ACCURACY" in report.text_body
    assert "FULL PROJECTION TABLE" in report.text_body
    # Every team appears in the table.
    for abbreviation in (f"T{i:02d}" for i in range(1, TEAMS + 1)):
        assert abbreviation in report.text_body
        assert abbreviation in report.html_body


def test_report_shows_the_week_over_week_move(midseason):
    report = report_for(midseason)
    assert "WEEK OVER WEEK (versus 2026-11-14)" in report.text_body
    # Error fell by a win, which is an improvement.
    assert "-1.00 (better)" in report.text_body


def test_report_without_history_says_so(midseason):
    model, training, target = team_wins.train(TARGET, cfg=midseason)
    predictions = team_wins.predict(model, training, target)
    summary = tracking.summarise(predictions, model)
    report = weekly_win_report.build(predictions, summary, history=pd.DataFrame(), cfg=midseason)
    assert "No snapshot from a week ago" in report.text_body


def test_delta_phrasing_follows_each_metric_direction():
    # Lower error is better; higher rank correlation is better.
    assert "better" in weekly_win_report._delta_phrase("mae_preseason_at_gp", -0.4)
    assert "worse" in weekly_win_report._delta_phrase("mae_preseason_at_gp", 0.4)
    assert "better" in weekly_win_report._delta_phrase("spearman_rho", 0.1)
    assert "worse" in weekly_win_report._delta_phrase("spearman_rho", -0.1)
    assert weekly_win_report._delta_phrase("spearman_rho", 0.0) == "unchanged"


def test_report_html_is_balanced_and_archived(preseason):
    from html.parser import HTMLParser

    class Balance(HTMLParser):
        VOID = {"meta", "br", "img", "hr", "input", "link"}

        def __init__(self):
            super().__init__()
            self.stack: list[str] = []
            self.errors: list[str] = []

        def handle_starttag(self, tag, attrs):
            if tag not in self.VOID:
                self.stack.append(tag)

        def handle_endtag(self, tag):
            if tag in self.VOID:
                return
            if not self.stack or self.stack[-1] != tag:
                self.errors.append(tag)
            else:
                self.stack.pop()

    report = report_for(preseason)
    parser = Balance()
    parser.feed(report.html_body)
    assert parser.errors == []
    assert parser.stack == []

    archived = report.archive(preseason)
    assert archived.exists()
    assert archived.name == f"weekly_win_report_{report.as_of}.html"
    assert archived.read_text() == report.html_body


# --- Email transport ------------------------------------------------------


def test_dotenv_is_parsed_and_overridden_by_the_environment(tmp_path, monkeypatch):
    cfg = Settings(project_root=tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "# a comment",
                "",
                "SMTP_USER=file@example.com",
                "SMTP_APP_PASSWORD='abcd efgh ijkl mnop'",
                'NBA_REPORT_TO="file@example.com"',
                "malformed line",
            ]
        )
    )
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_APP_PASSWORD", raising=False)
    monkeypatch.delenv("NBA_REPORT_TO", raising=False)

    config = email_sender.smtp_config(cfg)
    assert config.user == "file@example.com"
    # Gmail shows app passwords in groups of four; the spaces are not part of it.
    assert config.password == "abcdefghijklmnop"
    assert config.host == "smtp.gmail.com"
    assert config.port == 465
    assert config.uses_starttls is False
    assert email_sender.recipient(cfg) == "file@example.com"

    monkeypatch.setenv("SMTP_USER", "env@example.com")
    assert email_sender.smtp_config(cfg).user == "env@example.com"


def test_starttls_is_used_on_port_587(tmp_path, monkeypatch):
    cfg = Settings(project_root=tmp_path)
    (tmp_path / ".env").write_text("SMTP_APP_PASSWORD=secret\nSMTP_PORT=587\n")
    for key in ("SMTP_APP_PASSWORD", "SMTP_PORT", "SMTP_USER"):
        monkeypatch.delenv(key, raising=False)
    assert email_sender.smtp_config(cfg).uses_starttls is True


def test_without_an_app_password_there_is_no_smtp_config(tmp_path, monkeypatch):
    cfg = Settings(project_root=tmp_path)
    monkeypatch.delenv("SMTP_APP_PASSWORD", raising=False)
    assert email_sender.smtp_config(cfg) is None
    # The configured recipient is still known.
    monkeypatch.delenv("NBA_REPORT_TO", raising=False)
    assert email_sender.recipient(cfg) == "amase4134@gmail.com"
    assert email_sender.recipient(cfg, "other@example.com") == "other@example.com"


def test_message_carries_both_a_text_and_html_part():
    message = email_sender.build_message(
        subject="Subject",
        to="to@example.com",
        sender="from@example.com",
        text_body="plain",
        html_body="<p>rich</p>",
    )
    assert message["To"] == "to@example.com"
    assert message["From"] == "from@example.com"
    types = {part.get_content_type() for part in message.walk()}
    assert {"text/plain", "text/html"} <= types


def test_smtp_delivery_uses_the_configured_transport(tmp_path, monkeypatch):
    cfg = Settings(project_root=tmp_path)
    (tmp_path / ".env").write_text(
        "SMTP_APP_PASSWORD=secret\nSMTP_USER=sender@example.com\n"
    )
    for key in ("SMTP_APP_PASSWORD", "SMTP_USER", "SMTP_PORT", "NBA_REPORT_TO"):
        monkeypatch.delenv(key, raising=False)

    sent: list = []
    monkeypatch.setattr(
        email_sender, "send_via_smtp", lambda message, config: sent.append((message, config))
    )
    monkeypatch.setattr(
        email_sender,
        "send_via_mail_app",
        lambda **kwargs: pytest.fail("Mail.app should not be used when SMTP works"),
    )

    outcome = email_sender.send_report(
        subject="Weekly", text_body="plain", html_body="<p>rich</p>", cfg=cfg
    )
    assert "amase4134@gmail.com" in outcome
    assert "smtp.gmail.com" in outcome
    message, config = sent[0]
    assert message["To"] == "amase4134@gmail.com"
    assert config.user == "sender@example.com"


def test_mail_app_takes_over_when_smtp_is_unconfigured(tmp_path, monkeypatch):
    cfg = Settings(project_root=tmp_path)
    monkeypatch.delenv("SMTP_APP_PASSWORD", raising=False)
    monkeypatch.delenv("NBA_REPORT_TO", raising=False)

    handled: list = []
    monkeypatch.setattr(
        email_sender, "send_via_mail_app", lambda **kwargs: handled.append(kwargs)
    )
    outcome = email_sender.send_report(
        subject="Weekly", text_body="plain", html_body="<p>rich</p>", cfg=cfg
    )
    assert "Mail.app" in outcome
    assert handled[0]["to"] == "amase4134@gmail.com"
    assert handled[0]["text_body"] == "plain"


def test_mail_app_is_the_fallback_when_smtp_fails(tmp_path, monkeypatch):
    import smtplib

    cfg = Settings(project_root=tmp_path)
    (tmp_path / ".env").write_text("SMTP_APP_PASSWORD=secret\n")
    for key in ("SMTP_APP_PASSWORD", "NBA_REPORT_TO"):
        monkeypatch.delenv(key, raising=False)

    def explode(message, config):
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    handled: list = []
    monkeypatch.setattr(email_sender, "send_via_smtp", explode)
    monkeypatch.setattr(
        email_sender, "send_via_mail_app", lambda **kwargs: handled.append(kwargs)
    )

    outcome = email_sender.send_report(
        subject="Weekly", text_body="plain", html_body="<p>rich</p>", cfg=cfg
    )
    assert "Mail.app" in outcome
    assert len(handled) == 1


def test_dry_run_sends_nothing(tmp_path, monkeypatch):
    cfg = Settings(project_root=tmp_path)
    monkeypatch.setattr(
        email_sender,
        "send_via_smtp",
        lambda *a, **k: pytest.fail("dry run must not send"),
    )
    monkeypatch.setattr(
        email_sender,
        "send_via_mail_app",
        lambda **k: pytest.fail("dry run must not send"),
    )
    outcome = email_sender.send_report(
        subject="Weekly",
        text_body="plain",
        html_body="<p>rich</p>",
        cfg=cfg,
        dry_run=True,
    )
    assert "dry-run" in outcome
