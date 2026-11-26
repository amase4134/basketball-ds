"""Unit tests for Phase 2 game_logs / play_by_play scaffolding (no live API)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from nba_pipeline.config import (
    ALL_DATASETS,
    DATASETS,
    PHASE2_DATASETS,
    Settings,
)
from nba_pipeline.extract.game_logs import game_logs_from_pbpstats_games
from nba_pipeline.load.duckdb_views import PARTITIONED_DATASETS, connect
from nba_pipeline.load.parquet import replace_season_partition
from nba_pipeline.quality import check_game_logs, check_play_by_play, check_warehouse
from nba_pipeline.transform.clean import clean_game_logs, clean_play_by_play

SEASON = "2025-26"


def game_logs_raw() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "GAME_ID": ["0022500001", "0022500001", "0022500002", "0022500002"],
            "GAME_DATE": ["2025-10-22", "2025-10-22", "2025-10-23", "2025-10-23"],
            "TEAM_ID": [1610612737, 1610612738, 1610612737, 1610612744],
            "TEAM_ABBREVIATION": ["ATL", "BOS", "ATL", "GSW"],
            "TEAM_NAME": ["Atlanta Hawks", "Boston Celtics", "Atlanta Hawks", "Golden State"],
            "MATCHUP": ["ATL vs. BOS", "BOS @ ATL", "ATL @ GSW", "GSW vs. ATL"],
            "WL": ["W", "L", "L", "W"],
            "PTS": [110, 105, 98, 112],
            "PLUS_MINUS": [5, -5, -14, 14],
        }
    )


def pbp_raw() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "GAME_ID": ["0022500001", "0022500001", "0022500001", "0022500001"],
            "EVENTNUM": [1, 2, 3, 4],
            "EVENTMSGTYPE": [12, 1, 1, 1],
            "EVENTMSGACTIONTYPE": [0, 1, 1, 1],
            "PERIOD": [1, 1, 1, 1],
            "PCTIMESTRING": ["12:00", "11:40", "11:20", "11:00"],
            "HOMEDESCRIPTION": [None, "Make", None, "Make"],
            "NEUTRALDESCRIPTION": ["Start Period", None, None, None],
            "VISITORDESCRIPTION": [None, None, "Make", None],
            "SCORE": [None, "0 - 2", "2 - 2", "2 - 4"],
            "SCOREMARGIN": [None, "2", "TIE", "2"],
            "PLAYER1_ID": [0, 1, 2, 3],
            "PLAYER1_NAME": [None, "Home Scorer", "Away Scorer", "Home Scorer"],
            "PLAYER1_TEAM_ID": [None, 1610612737, 1610612738, 1610612737],
            "PLAYER1_TEAM_ABBREVIATION": [None, "ATL", "BOS", "ATL"],
            "PLAYER2_ID": [None, None, None, None],
            "PLAYER2_NAME": [None, None, None, None],
            "PLAYER2_TEAM_ID": [None, None, None, None],
            "PLAYER2_TEAM_ABBREVIATION": [None, None, None, None],
        }
    )


def test_phase2_datasets_are_opt_in_not_default():
    assert PHASE2_DATASETS == ("game_logs", "play_by_play")
    assert set(PHASE2_DATASETS).isdisjoint(DATASETS)
    assert ALL_DATASETS[-2:] == PHASE2_DATASETS
    assert "game_logs" in PARTITIONED_DATASETS
    assert "play_by_play" in PARTITIONED_DATASETS


def test_pbpstats_games_map_to_two_team_rows():
    games = [
        {
            "GameId": "0022600001",
            "Date": "2026-10-20",
            "HomeTeamId": 1610612765,
            "AwayTeamId": 1610612738,
            "HomePoints": 121,
            "AwayPoints": 113,
            "HomeTeamAbbreviation": "DET",
            "AwayTeamAbbreviation": "BOS",
        }
    ]
    raw = game_logs_from_pbpstats_games(
        games, team_names={1610612765: "Detroit Pistons", 1610612738: "Boston Celtics"}
    )
    curated = clean_game_logs(raw, SEASON)
    assert len(curated) == 2
    home = curated.loc[curated["IS_HOME"]].iloc[0]
    away = curated.loc[~curated["IS_HOME"]].iloc[0]
    assert home["TEAM_ABBREVIATION"] == "DET"
    assert home["WL"] == "W"
    assert away["TEAM_ABBREVIATION"] == "BOS"
    assert away["WL"] == "L"
    assert home["PTS"] == 121
    assert away["PLUS_MINUS"] == -8


def test_clean_game_logs_marks_home_and_dedupes():
    curated = clean_game_logs(game_logs_raw(), SEASON)
    assert list(curated["IS_HOME"]) == [True, False, False, True]
    assert curated["SEASON"].eq(SEASON).all()
    assert curated["GAME_ID"].astype(str).tolist() == [
        "0022500001",
        "0022500001",
        "0022500002",
        "0022500002",
    ]
    # Duplicate team-game should collapse.
    doubled = pd.concat([game_logs_raw(), game_logs_raw()], ignore_index=True)
    assert len(clean_game_logs(doubled, SEASON)) == 4


def test_clean_play_by_play_keeps_score_fields():
    curated = clean_play_by_play(pbp_raw(), SEASON)
    assert "SCOREMARGIN" in curated.columns
    assert "PCTIMESTRING" in curated.columns
    assert curated.loc[1, "SCOREMARGIN"] == "2"
    assert curated.loc[2, "SCOREMARGIN"] == "TIE"
    assert curated["SEASON"].eq(SEASON).all()


def test_check_game_logs_flags_bad_wl_and_nulls():
    good = clean_game_logs(game_logs_raw(), SEASON).drop(columns=["SEASON"])
    assert check_game_logs(good, SEASON) == []

    bad_wl = good.copy()
    bad_wl.loc[0, "WL"] = "X"
    assert any("WL" in f.message for f in check_game_logs(bad_wl, SEASON))

    null_id = good.copy()
    null_id.loc[0, "GAME_ID"] = None
    assert any("null GAME_ID" in f.message for f in check_game_logs(null_id, SEASON))


def test_check_play_by_play_requires_scoremargin():
    good = clean_play_by_play(pbp_raw(), SEASON).drop(columns=["SEASON"])
    assert check_play_by_play(good, SEASON) == []

    blank = good.copy()
    blank["SCOREMARGIN"] = None
    assert any("SCOREMARGIN" in f.message for f in check_play_by_play(blank, SEASON))


def test_check_warehouse_ignores_missing_phase2_and_validates_when_present(
    tmp_path: Path,
):
    """Missing Phase 2 partitions are fine; present ones are checked."""
    from nba_pipeline.load.parquet import append_refresh_log, write_static_table
    from tests.test_quality import (
        players_frame,
        shots_frame,
        stats_frame,
        team_stats_frame,
        teams_frame,
    )

    # Leave target_season as "current" so completed-season checks skip rosters.
    cfg = Settings(project_root=tmp_path, seasons=[SEASON])
    cfg.ensure_dirs()
    write_static_table(players_frame(), cfg.curated_dir / "players", "players")
    write_static_table(teams_frame(), cfg.curated_dir / "teams", "teams")
    replace_season_partition(stats_frame(), cfg.curated_dir / "player_season_stats", SEASON)
    replace_season_partition(
        team_stats_frame(), cfg.curated_dir / "team_season_stats", SEASON
    )
    replace_season_partition(shots_frame(), cfg.curated_dir / "shot_charts", SEASON)
    for dataset, season, count in (
        ("players", None, 1200),
        ("teams", None, 30),
        ("player_season_stats", SEASON, 500),
        ("team_season_stats", SEASON, 30),
        ("shot_charts", SEASON, 1000),
    ):
        append_refresh_log(
            dataset=dataset, season=season, row_count=count, status="ok", cfg=cfg
        )

    # No Phase 2 on disk → warehouse still healthy.
    findings = check_warehouse(cfg=cfg, season=SEASON)
    assert findings == []
    assert not any(f.dataset in PHASE2_DATASETS for f in findings)

    replace_season_partition(
        clean_game_logs(game_logs_raw(), SEASON).drop(columns=["SEASON"]),
        cfg.curated_dir / "game_logs",
        SEASON,
    )
    bad_pbp = clean_play_by_play(pbp_raw(), SEASON).drop(columns=["SEASON"])
    bad_pbp["SCOREMARGIN"] = None
    replace_season_partition(bad_pbp, cfg.curated_dir / "play_by_play", SEASON)

    findings = check_warehouse(cfg=cfg, season=SEASON)
    assert any(
        f.dataset == "play_by_play" and "SCOREMARGIN" in f.message for f in findings
    )
    assert not any(f.dataset == "game_logs" for f in findings)


def test_duckdb_registers_phase2_views(tmp_path: Path):
    cfg = Settings(project_root=tmp_path, seasons=[SEASON], target_season=SEASON)
    cfg.ensure_dirs()
    replace_season_partition(
        clean_game_logs(game_logs_raw(), SEASON).drop(columns=["SEASON"]),
        cfg.curated_dir / "game_logs",
        SEASON,
    )
    replace_season_partition(
        clean_play_by_play(pbp_raw(), SEASON).drop(columns=["SEASON"]),
        cfg.curated_dir / "play_by_play",
        SEASON,
    )
    con = connect(cfg=cfg)
    games = con.execute("SELECT COUNT(*) FROM game_logs").fetchone()[0]
    events = con.execute("SELECT COUNT(*) FROM play_by_play").fetchone()[0]
    assert games == 4
    assert events == 4
