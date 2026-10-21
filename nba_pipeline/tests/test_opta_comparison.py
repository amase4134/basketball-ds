"""Opta scraper and comparison tests over a synthetic article; no network access."""

from __future__ import annotations

import pandas as pd
import pytest
from nba_api.stats.static import teams as static_teams

from nba_pipeline.config import Settings
from nba_pipeline.extract import opta_projections
from nba_pipeline.modeling import opta_comparison
from nba_pipeline.reporting import opta_comparison_report

TARGET = "2026-27"

EAST = [
    "Boston Celtics", "Brooklyn Nets", "New York Knicks", "Philadelphia 76ers",
    "Toronto Raptors", "Chicago Bulls", "Cleveland Cavaliers", "Detroit Pistons",
    "Indiana Pacers", "Milwaukee Bucks", "Atlanta Hawks", "Charlotte Hornets",
    "Miami Heat", "Orlando Magic", "Washington Wizards",
]
WEST = [
    "Denver Nuggets", "Minnesota Timberwolves", "Oklahoma City Thunder",
    "Portland Trail Blazers", "Utah Jazz", "Golden State Warriors", "LA Clippers",
    "Los Angeles Lakers", "Phoenix Suns", "Sacramento Kings", "Dallas Mavericks",
    "Houston Rockets", "Memphis Grizzlies", "New Orleans Pelicans", "San Antonio Spurs",
]


def _team_block(rank: int, name: str, wins: float, first: bool) -> str:
    label = "Projected Wins (an average of thousands of simulations:)" if first else "Projected Wins:"
    return (
        f"<h3>{rank}. {name}</h3>"
        f"<p><strong>{label}</strong> {wins}<br>"
        f"<strong>Playoff Percentage:</strong> {wins + 20:.1f}%<br>"
        f"<strong>Preseason TRACR Rank:</strong> {rank}</p>"
        "<p>Some commentary that mentions 2. Not A Team in passing.</p>"
    )


def _article(teams_by_conf: dict[str, list[str]]) -> str:
    body = ["<html><head><script>var x = '1. Fake Team';</script></head><body>"]
    body.append("<p>1. This intro list item is not a team</p>")
    for heading, names in teams_by_conf.items():
        body.append(f"<h2>{heading}</h2>")
        for rank, name in enumerate(names, start=1):
            body.append(_team_block(rank, name, 60 - rank * 2.5, first=rank == 1))
    body.append("</body></html>")
    return "".join(body)


ARTICLE = _article({"Eastern Conference": EAST, "Western Conference": WEST})


def test_parser_reads_every_team_block():
    frame = opta_projections.parse_projections(ARTICLE)

    assert len(frame) == 30
    assert frame["TEAM_ID"].is_unique
    assert set(frame["conference"]) == {"East", "West"}
    knicks = frame.set_index("TEAM_ABBREVIATION").loc["NYK"]
    assert knicks["conference"] == "East"
    assert knicks["opta_conf_rank"] == 3
    assert knicks["opta_proj_wins"] == pytest.approx(52.5)
    assert knicks["opta_playoff_pct"] == pytest.approx(0.725)
    assert knicks["opta_tracr_rank"] == 3


def test_parser_maps_the_clippers_spelling():
    frame = opta_projections.parse_projections(ARTICLE)
    assert "LAC" in set(frame["TEAM_ABBREVIATION"])


def test_parser_rejects_a_partial_article():
    partial = _article({"Eastern Conference": EAST, "Western Conference": WEST[:-1]})
    with pytest.raises(ValueError, match="Expected 30 teams"):
        opta_projections.parse_projections(partial)


def test_parser_rejects_an_unknown_team():
    renamed = _article(
        {"Eastern Conference": EAST, "Western Conference": [*WEST[:-1], "Seattle Expansion"]}
    )
    with pytest.raises(ValueError, match="does not match"):
        opta_projections.parse_projections(renamed)


def test_offline_run_reparses_the_saved_article(tmp_path):
    cfg = Settings(project_root=tmp_path, target_season=TARGET)
    with pytest.raises(FileNotFoundError):
        opta_projections.extract_opta_projections(TARGET, cfg=cfg, offline=True)

    saved = opta_projections.raw_article_path(TARGET, "2026-10-14", cfg)
    saved.parent.mkdir(parents=True)
    saved.write_text(ARTICLE)

    frame = opta_projections.extract_opta_projections(TARGET, cfg=cfg, offline=True)
    assert frame["pulled_from"].iloc[0] == "2026-10-14"
    stored = opta_projections.load_opta_projections(TARGET, cfg=cfg)
    assert len(stored) == 30


def _my_predictions(opta: pd.DataFrame, shift: dict[str, float]) -> pd.DataFrame:
    abbr = {t["id"]: t["abbreviation"] for t in static_teams.get_teams()}
    frame = opta[["TEAM_ID", "opta_proj_wins"]].copy()
    frame["preseason_pred_wins"] = [
        wins + shift.get(abbr[team], 0.0)
        for team, wins in zip(frame["TEAM_ID"], frame["opta_proj_wins"])
    ]
    frame["games_played"] = 0.0
    frame["actual_wins"] = 0.0
    frame["actual_losses"] = 0.0
    frame["current_82_pace"] = float("nan")
    return frame.drop(columns="opta_proj_wins")


def test_comparison_reports_gaps_and_conference_ranks():
    opta = opta_projections.parse_projections(ARTICLE)
    # Lift the East's 15th seed into first; everyone else matches Opta exactly.
    mine = _my_predictions(opta, {"WAS": 40.0})

    comparison = opta_comparison.build_comparison(mine, opta)
    wiz = comparison.set_index("TEAM_ABBREVIATION").loc["WAS"]
    assert wiz["win_diff"] == pytest.approx(40.0)
    assert wiz["my_conf_rank"] == 1
    assert wiz["conf_rank_diff"] == 14
    assert wiz["my_tier"] == "Playoffs" and wiz["opta_tier"] == "Lottery"

    summary = opta_comparison.summarise(comparison)
    assert summary["mean_abs_gap"] == pytest.approx(40.0 / 30)
    assert summary["within_3_wins"] == 29
    assert summary["playoff_overlap"] == {"East": 5, "West": 6}
    assert summary["season_started"] is False

    higher, lower = opta_comparison.biggest_gaps(comparison)
    assert higher.iloc[0]["TEAM_ABBREVIATION"] == "WAS"


def test_comparison_requires_a_projection_for_every_team():
    opta = opta_projections.parse_projections(ARTICLE)
    mine = _my_predictions(opta, {}).iloc[1:]
    with pytest.raises(ValueError, match="No stored projection"):
        opta_comparison.build_comparison(mine, opta)


def test_report_archives_html_and_csv(tmp_path):
    cfg = Settings(project_root=tmp_path, target_season=TARGET)
    opta = opta_projections.parse_projections(ARTICLE)
    opta["source_url"] = opta_projections.OPTA_URL
    comparison = opta_comparison.build_comparison(_my_predictions(opta, {"BOS": 4.0}), opta)
    summary = opta_comparison.summarise(comparison)

    report = opta_comparison_report.build(comparison, summary, season=TARGET)
    html_path, csv_path = report.archive(cfg)

    assert "Eastern Conference" in html_path.read_text()
    assert "<svg" in html_path.read_text()
    csv = pd.read_csv(csv_path)
    assert len(csv) == 30
    assert csv.set_index("TEAM_ABBREVIATION").loc["BOS", "win_diff"] == pytest.approx(4.0)
