"""Unit tests for team lead-share aggregates (no live API)."""

from __future__ import annotations

import pandas as pd
import pytest

from nba_pipeline.config import TEAM_LEAD_SHARE_COLUMNS, TEAM_STATS_COLUMNS
from nba_pipeline.extract.lead_shares import (
    parse_margin_label,
    shares_from_margin_buckets,
)
from nba_pipeline.quality import check_team_season_stats
from nba_pipeline.transform.clean import clean_team_season_stats


def test_lead_share_columns_are_on_team_stats_schema():
    for col in TEAM_LEAD_SHARE_COLUMNS:
        assert col in TEAM_STATS_COLUMNS


def test_parse_margin_labels():
    assert parse_margin_label("3") == 3
    assert parse_margin_label("-12") == -12
    assert parse_margin_label("20+") == 20
    assert parse_margin_label("-20+") == -20
    assert parse_margin_label("TIE") is None


def test_shares_from_margin_buckets_sum_to_one():
    margins = [
        {"margin": "2", "frequency": 0.40},
        {"margin": "-1", "frequency": 0.35},
        {"margin": "0", "frequency": 0.10},
        {"margin": "20+", "frequency": 0.10},
        {"margin": "-20+", "frequency": 0.05},
    ]
    shares = shares_from_margin_buckets(margins)
    assert shares["POSS_AHEAD_SHARE"] == pytest.approx(0.50)
    assert shares["POSS_BEHIND_SHARE"] == pytest.approx(0.40)
    assert shares["POSS_TIED_SHARE"] == pytest.approx(0.10)
    assert sum(shares.values()) == pytest.approx(1.0)


def test_clean_team_season_stats_keeps_lead_shares():
    raw = pd.DataFrame(
        {
            "TEAM_ID": [1, 2],
            "TEAM_NAME": ["A", "B"],
            "TEAM_ABBREVIATION": ["AA", "BB"],
            "GP": [10, 10],
            "W": [6, 4],
            "L": [4, 6],
            "W_PCT": [0.6, 0.4],
            "POSS_AHEAD_SHARE": [0.55, 0.40],
            "POSS_BEHIND_SHARE": [0.35, 0.50],
            "POSS_TIED_SHARE": [0.10, 0.10],
        }
    )
    curated = clean_team_season_stats(raw, "2026-27")
    for col in TEAM_LEAD_SHARE_COLUMNS:
        assert col in curated.columns
    assert curated.loc[0, "POSS_AHEAD_SHARE"] == 0.55


def test_check_team_season_stats_flags_bad_lead_shares():
    frame = pd.DataFrame(
        {
            "TEAM_ID": range(1, 31),
            "TEAM_NAME": [f"T{i}" for i in range(1, 31)],
            "TEAM_ABBREVIATION": [f"T{i:02d}" for i in range(1, 31)],
            "GP": [10] * 30,
            "W": [5] * 30,
            "L": [5] * 30,
            "W_PCT": [0.5] * 30,
            "POSS_AHEAD_SHARE": [0.5] * 30,
            "POSS_BEHIND_SHARE": [0.4] * 30,
            "POSS_TIED_SHARE": [0.1] * 30,
        }
    )
    assert check_team_season_stats(frame, "2026-27") == []

    bad = frame.copy()
    bad.loc[0, "POSS_AHEAD_SHARE"] = 1.5
    messages = [f.message for f in check_team_season_stats(bad, "2026-27")]
    assert any("lead-share" in m for m in messages)
