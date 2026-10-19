"""Checker tests over synthetic frames; nothing here touches stats.nba.com."""

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from nba_pipeline.config import Settings
from nba_pipeline.load.parquet import append_refresh_log, replace_season_partition, write_static_table
from nba_pipeline.quality import (
    check_player_season_stats,
    check_players,
    check_refresh_log,
    check_rosters,
    check_row_count_drop,
    check_shot_charts,
    check_team_season_stats,
    check_teams,
    check_warehouse,
    parse_since,
)

SEASON = "2025-26"


def players_frame(rows=1200):
    return pd.DataFrame(
        {
            "PLAYER_ID": range(1, rows + 1),
            "FULL_NAME": [f"Player {i}" for i in range(1, rows + 1)],
            "IS_ACTIVE": True,
        }
    )


def teams_frame(rows=30):
    return pd.DataFrame(
        {
            "TEAM_ID": range(1, rows + 1),
            "FULL_NAME": [f"Team {i}" for i in range(1, rows + 1)],
            "ABBREVIATION": [f"T{i:02d}" for i in range(1, rows + 1)],
        }
    )


def stats_frame(rows=500):
    return pd.DataFrame(
        {
            "PLAYER_ID": range(1, rows + 1),
            "PLAYER_NAME": [f"Player {i}" for i in range(1, rows + 1)],
            "TEAM_ID": [1] * rows,
            "MIN": [600.0] * rows,
            "PTS": [20.0] * rows,
        }
    )


def team_stats_frame(rows=30, games=82):
    wins = [games // 2] * rows
    return pd.DataFrame(
        {
            "TEAM_ID": range(1, rows + 1),
            "TEAM_NAME": [f"Team {i}" for i in range(1, rows + 1)],
            "TEAM_ABBREVIATION": [f"T{i:02d}" for i in range(1, rows + 1)],
            "GP": [games] * rows,
            "W": wins,
            "L": [games - w for w in wins],
            "W_PCT": [w / games if games else None for w in wins],
            "OFF_RATING": [114.0] * rows,
            "DEF_RATING": [114.0] * rows,
            "NET_RATING": [0.0] * rows,
        }
    )


def shots_frame(rows=1000):
    return pd.DataFrame(
        {
            "PLAYER_ID": [1] * rows,
            "PLAYER_NAME": ["Player 1"] * rows,
            "GAME_ID": [f"002250{i:04d}" for i in range(rows)],
            "SHOT_MADE_FLAG": [i % 2 for i in range(rows)],
            "SHOT_DISTANCE": [12] * rows,
            "LOC_X": [10] * rows,
            "LOC_Y": [80] * rows,
        }
    )


def messages(findings):
    return " | ".join(f.message for f in findings)


def test_clean_frames_produce_no_findings():
    assert check_players(players_frame()) == []
    assert check_teams(teams_frame()) == []
    assert check_player_season_stats(stats_frame(), SEASON) == []
    assert check_team_season_stats(team_stats_frame(), SEASON) == []
    assert check_shot_charts(shots_frame(), SEASON) == []


def test_team_stats_accept_an_unplayed_season():
    # Before opening night every team sits at zero games; that is not junk.
    assert check_team_season_stats(team_stats_frame(games=0), SEASON) == []


def test_team_stats_reject_impossible_records():
    short = team_stats_frame(rows=12)
    assert "at least 30" in messages(check_team_season_stats(short, SEASON))

    mismatched = team_stats_frame()
    mismatched.loc[0, "W"] = 70
    assert "W + L does not equal GP" in messages(
        check_team_season_stats(mismatched, SEASON)
    )

    overrun = team_stats_frame(games=90)
    assert "more than 82 games" in messages(check_team_season_stats(overrun, SEASON))

    negative = team_stats_frame()
    negative.loc[0, ["W", "L"]] = [-1, 42]
    assert "negative GP/W/L" in messages(check_team_season_stats(negative, SEASON))

    duped = team_stats_frame()
    duped.loc[0, "TEAM_ID"] = duped.loc[1, "TEAM_ID"]
    assert "duplicate TEAM_ID" in messages(check_team_season_stats(duped, SEASON))

    bad_pct = team_stats_frame()
    bad_pct.loc[0, "W_PCT"] = 1.4
    assert "W_PCT outside" in messages(check_team_season_stats(bad_pct, SEASON))


def test_short_pulls_fail_the_row_floors():
    assert "at least 1000" in messages(check_players(players_frame(50)))
    assert "at least 30" in messages(check_teams(teams_frame(5)))
    assert "empty" in messages(check_player_season_stats(stats_frame(0), SEASON))
    assert "empty" in messages(check_shot_charts(shots_frame(0), SEASON))


def test_duplicate_and_null_keys_are_junk():
    dupes = players_frame()
    dupes.loc[0, "PLAYER_ID"] = dupes.loc[1, "PLAYER_ID"]
    assert "duplicate PLAYER_ID" in messages(check_players(dupes))

    nameless = players_frame()
    nameless.loc[0, "FULL_NAME"] = None
    assert "null FULL_NAME" in messages(check_players(nameless))

    dupe_teams = teams_frame()
    dupe_teams.loc[0, "TEAM_ID"] = dupe_teams.loc[1, "TEAM_ID"]
    assert "duplicate TEAM_ID" in messages(check_teams(dupe_teams))

    dupe_stats = stats_frame()
    dupe_stats.loc[0, "PLAYER_ID"] = dupe_stats.loc[1, "PLAYER_ID"]
    assert "duplicate PLAYER_ID" in messages(check_player_season_stats(dupe_stats, SEASON))


def test_impossible_values_are_junk():
    negative_minutes = stats_frame()
    negative_minutes.loc[0, "MIN"] = -5
    assert "negative MIN" in messages(check_player_season_stats(negative_minutes, SEASON))

    bad_flag = shots_frame()
    bad_flag.loc[0, "SHOT_MADE_FLAG"] = 7
    assert "SHOT_MADE_FLAG not 0/1" in messages(check_shot_charts(bad_flag, SEASON))

    negative_distance = shots_frame()
    negative_distance.loc[0, "SHOT_DISTANCE"] = -1
    assert "negative SHOT_DISTANCE" in messages(check_shot_charts(negative_distance, SEASON))

    no_game = shots_frame()
    no_game.loc[0, "GAME_ID"] = None
    assert "null GAME_ID" in messages(check_shot_charts(no_game, SEASON))


def test_court_bounds_tolerate_heaves_but_not_corruption():
    # A season's worth of genuine full-court heaves sits near 0.1% of shots.
    heaves = shots_frame()
    heaves.loc[:1, "LOC_Y"] = 650
    assert check_shot_charts(heaves, SEASON) == []

    corrupted = shots_frame()
    corrupted.loc[:499, "LOC_Y"] = 9999
    assert "LOC_Y outside" in messages(check_shot_charts(corrupted, SEASON))

    sideways = shots_frame()
    sideways.loc[:499, "LOC_X"] = -4000
    assert "LOC_X outside" in messages(check_shot_charts(sideways, SEASON))


def fake_log(rows):
    return pd.DataFrame(rows)


def log_row(dataset, season, row_count, status, timestamp, message=""):
    return {
        "dataset": dataset,
        "season": season,
        "row_count": row_count,
        "status": status,
        "message": message,
        "timestamp": timestamp.isoformat(),
    }


def test_refresh_log_flags_failures_and_stale_runs():
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)

    assert "never run" in messages(check_refresh_log(None, "players", None, None))

    empty = fake_log([log_row("teams", None, 30, "ok", now)])
    assert "no refresh log entry" in messages(check_refresh_log(empty, "players", None, None))

    failed = fake_log([log_row("shot_charts", SEASON, 0, "error", now, "HTTP 500")])
    found = messages(check_refresh_log(failed, "shot_charts", SEASON, None))
    assert "status 'error'" in found and "HTTP 500" in found

    stale = fake_log([log_row("players", None, 5000, "ok", yesterday)])
    cutoff = now - timedelta(hours=1)
    assert "no successful refresh since" in messages(
        check_refresh_log(stale, "players", None, cutoff)
    )
    assert check_refresh_log(stale, "players", None, yesterday - timedelta(hours=1)) == []


def test_row_count_drop_compares_the_two_latest_successes():
    now = datetime.now(timezone.utc)
    halved = fake_log(
        [
            log_row("shot_charts", SEASON, 200_000, "ok", now - timedelta(days=1)),
            log_row("shot_charts", SEASON, 4_000, "ok", now),
        ]
    )
    assert "dropped sharply" in messages(check_row_count_drop(halved, "shot_charts", SEASON))

    steady = fake_log(
        [
            log_row("shot_charts", SEASON, 200_000, "ok", now - timedelta(days=1)),
            log_row("shot_charts", SEASON, 203_000, "ok", now),
        ]
    )
    assert check_row_count_drop(steady, "shot_charts", SEASON) == []

    first_ever = fake_log([log_row("shot_charts", SEASON, 100, "ok", now)])
    assert check_row_count_drop(first_ever, "shot_charts", SEASON) == []


def test_parse_since_defaults_to_utc():
    assert parse_since("2026-10-09T08:00:00Z").tzinfo is not None
    assert parse_since("2026-10-09T08:00:00").tzinfo == timezone.utc
    with pytest.raises(ValueError):
        parse_since("not a timestamp")


@pytest.fixture
def warehouse(tmp_path):
    cfg = Settings(project_root=tmp_path, seasons=[SEASON])
    cfg.ensure_dirs()
    write_static_table(players_frame(), cfg.curated_dir / "players", "players")
    write_static_table(teams_frame(), cfg.curated_dir / "teams", "teams")
    replace_season_partition(
        stats_frame(), cfg.curated_dir / "player_season_stats", SEASON
    )
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
    return cfg


def test_healthy_warehouse_passes_end_to_end(warehouse):
    assert check_warehouse(cfg=warehouse, season=SEASON) == []


def test_rosters_must_cover_every_team_once():
    healthy = pd.DataFrame(
        {"PLAYER_ID": range(1, 30 * 15 + 1), "TEAM_ID": [t for t in range(1, 31) for _ in range(15)]}
    )
    assert check_rosters(healthy, "2026-27") == []

    short = healthy[healthy["TEAM_ID"] != 30]
    assert "29 teams rostered" in messages(check_rosters(short, "2026-27"))

    doubled = pd.concat([healthy, healthy.iloc[:1].assign(TEAM_ID=2)])
    assert "two teams" in messages(check_rosters(doubled, "2026-27"))
    assert check_rosters(pd.DataFrame(), "2026-27")


def test_missing_season_partition_is_incomplete(warehouse):
    assert check_warehouse(cfg=warehouse, season="2026-27")


def test_warehouse_detects_a_deleted_dataset(warehouse):
    (warehouse.curated_dir / "players" / "players.parquet").unlink()
    assert "missing" in messages(check_warehouse(cfg=warehouse, season=SEASON))


def test_warehouse_requires_a_refresh_newer_than_the_cutoff(warehouse):
    cutoff = datetime.now(timezone.utc) + timedelta(minutes=5)
    findings = check_warehouse(cfg=warehouse, season=SEASON, since=cutoff)
    assert len(findings) == 5
    assert all("no successful refresh since" in f.message for f in findings)
