"""Render the my-model-versus-Opta comparison as a standalone HTML page and CSV."""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from nba_pipeline.config import GAMES_PER_SEASON, Settings, settings
from nba_pipeline.modeling import opta_comparison
from nba_pipeline.reporting import theme

# Broadcast defaults — charts also read CSS variables when opened in-browser.
MY_COLOR = "#2ec4e0"
OPTA_COLOR = "#f05a28"

CSV_COLUMNS = [
    "conference",
    "TEAM_ABBREVIATION",
    "TEAM_NAME",
    "my_proj_wins",
    "opta_proj_wins",
    "win_diff",
    "my_conf_rank",
    "opta_conf_rank",
    "conf_rank_diff",
    "my_league_rank",
    "opta_league_rank",
    "my_tier",
    "opta_tier",
    "opta_playoff_pct",
    "opta_tracr_rank",
    "games_played",
    "actual_wins",
    "actual_losses",
]


@dataclass
class ComparisonReport:
    season: str
    as_of: str
    html_body: str
    table: pd.DataFrame

    def archive(self, cfg: Settings | None = None) -> tuple[Path, Path]:
        cfg = cfg or settings
        cfg.reports_dir.mkdir(parents=True, exist_ok=True)
        stem = f"opta_comparison_{self.season}_{self.as_of}"
        html_path = cfg.reports_dir / f"{stem}.html"
        csv_path = cfg.reports_dir / f"{stem}.csv"
        html_path.write_text(self.html_body)
        self.table[[c for c in CSV_COLUMNS if c in self.table.columns]].to_csv(
            csv_path, index=False, float_format="%.1f"
        )
        theme.archive_touched(cfg)
        return html_path, csv_path


def build(
    comparison: pd.DataFrame,
    summary: dict,
    *,
    season: str,
    pulled_from: str | None = None,
) -> ComparisonReport:
    as_of = date.today().isoformat()
    body = _render(comparison, summary, season, as_of, pulled_from)
    return ComparisonReport(season=season, as_of=as_of, html_body=body, table=comparison)


def _e(value) -> str:
    return html.escape(str(value))


def _card(label: str, value: str, note: str = "") -> str:
    note_html = f'<div class="note">{_e(note)}</div>' if note else ""
    return (
        f'<div class="report-card"><div class="label">{_e(label)}</div>'
        f'<div class="value">{_e(value)}</div>{note_html}</div>'
    )


def _cards(summary: dict) -> str:
    playoff = summary["playoff_overlap"]
    play_in = summary["play_in_overlap"]
    cards = [
        _card(
            "Average gap",
            f"{summary['mean_abs_gap']:.1f} wins",
            f"{summary['within_3_wins']} of {summary['teams']} teams within 3 wins",
        ),
        _card(
            "Rank agreement",
            f"ρ = {summary['spearman_rho']:.2f}",
            f"Pearson r = {summary['pearson_r']:.2f}",
        ),
        _card(
            "Spread (std dev)",
            f"{summary['my_spread']:.1f} vs {summary['opta_spread']:.1f}",
            "Mine vs Opta, in wins",
        ),
        _card(
            "Same top-6 teams",
            f"East {playoff.get('East', 0)}/6 · West {playoff.get('West', 0)}/6",
            f"Top-10: East {play_in.get('East', 0)}/10 · West {play_in.get('West', 0)}/10",
        ),
        _card(
            "Same tier",
            f"{summary['tier_agreement']} / {summary['teams']}",
            "Playoffs, play-in, or lottery",
        ),
    ]
    if summary.get("season_started"):
        cards.append(
            _card(
                "Closer to 82-game pace",
                f"Mine {summary['my_mae_vs_pace']:.1f} · Opta {summary['opta_mae_vs_pace']:.1f}",
                f"MAE through {summary['games_played_avg']:.1f} games",
            )
        )
    return '<div class="report-cards">' + "".join(cards) + "</div>"


def _dumbbell(comparison: pd.DataFrame) -> str:
    """Horizontal dumbbell: one row per team, my dot and Opta's joined by a bar."""
    rows = comparison.sort_values("opta_proj_wins", ascending=False).reset_index(drop=True)
    lo = 10 * (min(rows["my_proj_wins"].min(), rows["opta_proj_wins"].min()) // 10)
    hi = 10 * -(-max(rows["my_proj_wins"].max(), rows["opta_proj_wins"].max()) // 10)
    left, right, top, row_h = 70, 30, 30, 22
    width = 820
    plot_w = width - left - right
    height = top + row_h * len(rows) + 30

    def x(value: float) -> float:
        return left + (value - lo) / (hi - lo) * plot_w

    parts = [f'<svg viewBox="0 0 {width} {height}" class="report-chart" role="img">']
    tick = lo
    while tick <= hi:
        tx = x(tick)
        parts.append(
            f'<line class="grid-line" x1="{tx:.1f}" y1="{top - 8}" x2="{tx:.1f}" '
            f'y2="{height - 24}"/>'
            f'<text x="{tx:.1f}" y="{height - 8}" class="axis">{tick:.0f}</text>'
        )
        tick += 10
    mid = x(GAMES_PER_SEASON / 2)
    parts.append(
        f'<line class="mid-line" x1="{mid:.1f}" y1="{top - 8}" x2="{mid:.1f}" '
        f'y2="{height - 24}"/>'
        f'<text x="{mid:.1f}" y="{top - 14}" class="axis">.500</text>'
    )
    for i, row in rows.iterrows():
        cy = top + i * row_h + row_h / 2
        mx, ox = x(row["my_proj_wins"]), x(row["opta_proj_wins"])
        title = (
            f"{row['TEAM_NAME']}: mine {row['my_proj_wins']:.1f}, "
            f"Opta {row['opta_proj_wins']:.1f} ({row['win_diff']:+.1f})"
        )
        parts.append(
            f"<g><title>{_e(title)}</title>"
            f'<text x="{left - 10}" y="{cy + 4:.1f}" class="team">'
            f"{_e(row['TEAM_ABBREVIATION'])}</text>"
            f'<line class="connector" x1="{min(mx, ox):.1f}" y1="{cy:.1f}" '
            f'x2="{max(mx, ox):.1f}" y2="{cy:.1f}"/>'
            f'<circle cx="{ox:.1f}" cy="{cy:.1f}" r="6" fill="{OPTA_COLOR}"/>'
            f'<circle cx="{mx:.1f}" cy="{cy:.1f}" r="6" fill="{MY_COLOR}"/></g>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _diff_cell(value: float, spec: str = "{:+.1f}") -> str:
    if abs(value) < 0.05:
        cls = ""
    else:
        cls = "pos" if value > 0 else "neg"
    return f'<td class="num {cls}">{spec.format(value)}</td>'


def _conference_table(group: pd.DataFrame, conference: str) -> str:
    rows = []
    for _, row in group.sort_values("opta_conf_rank").iterrows():
        tier_cls = "" if row["my_tier"] == row["opta_tier"] else ' class="tier-miss"'
        rows.append(
            "<tr>"
            f'<td class="num">{row["opta_conf_rank"]}</td>'
            f'<td><span class="team">{_e(row["TEAM_ABBREVIATION"])}</span> '
            f'<span class="muted">{_e(row["TEAM_NAME"])}</span></td>'
            f'<td class="num mine">{row["my_proj_wins"]:.1f}</td>'
            f'<td class="num opta">{row["opta_proj_wins"]:.1f}</td>'
            + _diff_cell(row["win_diff"])
            + f'<td class="num">{row["my_conf_rank"]}</td>'
            + _diff_cell(row["conf_rank_diff"], "{:+.0f}")
            + f"<td{tier_cls}>{_e(row['my_tier'])} / {_e(row['opta_tier'])}</td>"
            f'<td class="num">{row["opta_playoff_pct"] * 100:.1f}%</td>'
            f'<td class="num">{row["opta_tracr_rank"]}</td>'
            "</tr>"
        )
    return (
        f'<section class="report-section"><h2>{_e(conference)}ern Conference</h2>'
        '<div class="report-table-wrap"><table class="report-table"><thead><tr>'
        '<th class="num">Opta #</th><th>Team</th>'
        '<th class="num">My wins</th><th class="num">Opta wins</th>'
        '<th class="num">Gap</th><th class="num">My #</th><th class="num">Rank Δ</th>'
        "<th>Tier (mine / Opta)</th>"
        '<th class="num">Opta playoff %</th><th class="num">TRACR #</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div></section>"
    )


def _gap_list(frame: pd.DataFrame) -> str:
    items = "".join(
        f"<li><b>{_e(r['TEAM_ABBREVIATION'])}</b> {r['win_diff']:+.1f} "
        f'<span class="muted">(mine {r["my_proj_wins"]:.1f}, Opta {r["opta_proj_wins"]:.1f})</span></li>'
        for _, r in frame.iterrows()
    )
    return f"<ol>{items}</ol>"


def _render(
    comparison: pd.DataFrame,
    summary: dict,
    season: str,
    as_of: str,
    pulled_from: str | None,
) -> str:
    higher, lower = opta_comparison.biggest_gaps(comparison)
    tables = "".join(
        _conference_table(group, conference)
        for conference, group in comparison.groupby("conference")
    )
    my_lo, my_hi = summary["my_range"]
    op_lo, op_hi = summary["opta_range"]
    source = f"pulled {pulled_from}" if pulled_from else ""
    body = f"""
<section class="report-hero">
  <p class="report-kicker">Model comparison · {_e(as_of)}</p>
  <h1 class="report-title">{_e(season)} win projections: my model vs Opta</h1>
  <p class="report-lede">Opta Analyst projections {_e(source)}. My model ranges
  {my_lo:.1f}–{my_hi:.1f} wins, Opta {op_lo:.1f}–{op_hi:.1f}.</p>
</section>
{_cards(summary)}

<section class="report-section">
  <h2>Every team, sorted by Opta projection</h2>
  <div class="report-panel">
    <div class="report-legend">
      <span class="mine"><i></i>My model</span>
      <span class="opta"><i></i>Opta</span>
    </div>
    {_dumbbell(comparison)}
  </div>
</section>

<section class="report-section">
  <h2>Biggest disagreements</h2>
  <div class="report-gaps">
    <div class="report-panel"><b>My model is higher</b>{_gap_list(higher)}</div>
    <div class="report-panel"><b>My model is lower</b>{_gap_list(lower)}</div>
  </div>
</section>

{tables}

<footer class="report-footer">
Gap = my projected wins minus Opta's. Rank Δ is positive when my model seeds the team higher
in its conference. Highlighted tiers are where the two forecasts put a team in a different
bucket (top 6 = playoffs, 7–10 = play-in). Opta's totals average thousands of simulations, so
they cluster closer to 41 wins than a single point forecast. Source:
<a href="{_e(comparison['source_url'].iloc[0]) if 'source_url' in comparison else '#'}">Opta Analyst</a>.
</footer>
"""
    return theme.wrap_report_document(
        title=f"{season} pre-season projections: my model vs Opta",
        body_html=body,
    )
