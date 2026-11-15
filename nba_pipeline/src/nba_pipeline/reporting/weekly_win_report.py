"""Weekly digest of how the win predictor is performing.

Renders the same content as HTML (for the email body) and plain text (for
clients that refuse HTML, and for the Mail.app fallback), then archives the
HTML alongside the warehouse metadata.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from nba_pipeline.config import GAMES_PER_SEASON, Settings, settings
from nba_pipeline.modeling import tracking
from nba_pipeline.modeling.tracking import OVERPERFORMER_COUNT, UNDERPERFORMER_COUNT
from nba_pipeline.reporting import theme

logger = logging.getLogger(__name__)

# Metrics where a decrease is an improvement, used to label week-over-week moves.
LOWER_IS_BETTER = {
    "mae_preseason_at_gp",
    "rmse_preseason_at_gp",
    "mae_inseason_at_gp",
    "rmse_inseason_at_gp",
    "mae_pace_82_preseason",
    "mae_pace_82_updated",
}

METRIC_LABELS = {
    "mae_preseason_at_gp": "Pre-season model MAE (wins)",
    "rmse_preseason_at_gp": "Pre-season model RMSE (wins)",
    "mae_inseason_at_gp": "In-season model MAE (wins)",
    "rmse_inseason_at_gp": "In-season model RMSE (wins)",
    "mae_pace_82_preseason": "82-game pace MAE, pre-season (wins)",
    "mae_pace_82_updated": "82-game pace MAE, updated (wins)",
    "spearman_rho": "Standings rank correlation",
    "playoff_tier_accuracy": "Playoff tier accuracy",
}

IN_SEASON_TABLE_COLUMNS = [
    ("TEAM_ABBREVIATION", "Team", "{}"),
    ("conference", "Conf", "{}"),
    ("conference_rank", "Seed", "{:.0f}"),
    ("playoff_tier", "Tier", "{}"),
    ("games_played", "GP", "{:.0f}"),
    ("record", "W-L", "{}"),
    ("preseason_expected_wins_at_gp", "Exp W", "{:.1f}"),
    ("preseason_win_error_at_gp", "Err", "{:+.1f}"),
    ("preseason_pred_wins", "Pre 82", "{:.1f}"),
    ("inseason_talent_wins_82", "In 82", "{:.1f}"),
    ("updated_pred_wins", "Proj 82", "{:.1f}"),
    ("current_82_pace", "Pace", "{:.1f}"),
    ("net_rating", "Net", "{:+.1f}"),
]

# Before tip-off the in-season columns carry no information worth showing.
PRESEASON_TABLE_COLUMNS = [
    ("TEAM_ABBREVIATION", "Team", "{}"),
    ("conference", "Conf", "{}"),
    ("conference_rank", "Seed", "{:.0f}"),
    ("playoff_tier", "Tier", "{}"),
    ("preseason_rank", "Rank", "{:.0f}"),
    ("preseason_pred_wins", "Proj W", "{:.1f}"),
    ("preseason_proj_losses", "Proj L", "{:.1f}"),
]


def table_columns(started: bool) -> list[tuple[str, str, str]]:
    return IN_SEASON_TABLE_COLUMNS if started else PRESEASON_TABLE_COLUMNS


def _num(value) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        return None
    return float(value)


def _fmt(value, spec: str = "{:.2f}", missing: str = "n/a") -> str:
    number = _num(value)
    if number is None:
        return missing
    try:
        return spec.format(number)
    except (ValueError, TypeError):
        return str(value)


@dataclass
class WeeklyReport:
    """A rendered report, ready to send and to archive."""

    season: str
    as_of: str
    subject: str
    text_body: str
    html_body: str

    def archive(self, cfg: Settings | None = None) -> Path:
        cfg = cfg or settings
        cfg.reports_dir.mkdir(parents=True, exist_ok=True)
        path = cfg.reports_dir / f"weekly_win_report_{self.as_of}.html"
        path.write_text(self.html_body)
        theme.archive_touched(cfg)
        return path


def _record(row: pd.Series) -> str:
    wins, losses = _num(row.get("actual_wins")), _num(row.get("actual_losses"))
    if wins is None or losses is None:
        return "—"
    return f"{wins:.0f}-{losses:.0f}"


def _table_frame(predictions: pd.DataFrame) -> pd.DataFrame:
    frame = predictions.copy()
    frame["record"] = frame.apply(_record, axis=1)
    frame["preseason_proj_losses"] = GAMES_PER_SEASON - frame["preseason_pred_wins"]
    return frame.sort_values("updated_pred_wins", ascending=False)


def _delta_phrase(key: str, value: float) -> str:
    """Describe a week-over-week move in plain language."""
    if abs(value) < 0.005:
        return "unchanged"
    improved = value < 0 if key in LOWER_IS_BETTER else value > 0
    return f"{value:+.2f} ({'better' if improved else 'worse'})"


def _subject(season: str, as_of: str, summary: dict) -> str:
    if not summary.get("season_started"):
        return f"NBA {season} Win Predictor — pre-season projection ({as_of})"
    mae = _fmt(summary.get("mae_preseason_at_gp"), "{:.2f}")
    games = _fmt(summary.get("games_played_avg"), "{:.0f}")
    return (
        f"NBA {season} Win Predictor — {mae} win MAE through {games} games ({as_of})"
    )


def build(
    predictions: pd.DataFrame,
    summary: dict,
    *,
    history: pd.DataFrame | None = None,
    metrics: dict | None = None,
    cfg: Settings | None = None,
) -> WeeklyReport:
    """Render the weekly digest from a scored prediction frame."""
    cfg = cfg or settings
    season = summary.get("season") or cfg.target_season
    as_of = summary.get("as_of_date") or date.today().isoformat()
    deltas = (
        tracking.week_over_week(history, as_of)
        if history is not None and not history.empty
        else None
    )
    table = _table_frame(predictions)

    return WeeklyReport(
        season=season,
        as_of=as_of,
        subject=_subject(season, as_of, summary),
        text_body=_render_text(season, as_of, summary, table, deltas, metrics),
        html_body=_render_html(season, as_of, summary, table, deltas, metrics),
    )


def _training_line(summary: dict, metrics: dict | None) -> str:
    seasons = summary.get("training_seasons") or (
        (metrics or {}).get("training_seasons") or []
    )
    if not seasons:
        return "Training window unavailable."
    return (
        f"Trained on {len(seasons)} completed seasons: {seasons[0]} through {seasons[-1]}."
    )


def _render_text(
    season: str,
    as_of: str,
    summary: dict,
    table: pd.DataFrame,
    deltas: dict | None,
    metrics: dict | None,
) -> str:
    lines = [
        f"NBA {season} WIN PREDICTOR — WEEKLY REPORT",
        f"Generated {as_of}",
        "",
        _training_line(summary, metrics),
    ]

    if summary.get("season_started"):
        lines += [
            f"Season progress: {_fmt(summary.get('games_played_avg'), '{:.1f}')} of "
            f"{GAMES_PER_SEASON} games played on average "
            f"({_fmt(summary.get('season_progress_pct', 0) * 100 if summary.get('season_progress_pct') is not None else None, '{:.1f}')}%).",
            "",
            "MODEL ACCURACY (versus wins banked so far)",
            f"  Pre-season model   MAE {_fmt(summary.get('mae_preseason_at_gp'))} wins"
            f"   RMSE {_fmt(summary.get('rmse_preseason_at_gp'))}",
            f"  In-season model    MAE {_fmt(summary.get('mae_inseason_at_gp'))} wins"
            f"   RMSE {_fmt(summary.get('rmse_inseason_at_gp'))}",
            f"  82-game pace       pre-season MAE {_fmt(summary.get('mae_pace_82_preseason'))}"
            f"   updated MAE {_fmt(summary.get('mae_pace_82_updated'))}",
            f"  Standings rank     rho {_fmt(summary.get('spearman_rho'))}"
            f"   playoff tier {_fmt(summary.get('playoff_tier_accuracy'))}",
        ]
    else:
        lines += [
            f"{season} has not tipped off yet. The pre-season projection below is",
            "locked in and will be scored against results once games are played.",
        ]

    lines += [
        "",
        "CROSS-VALIDATED TRAINING ACCURACY",
        f"  Pre-season model   MAE {_fmt(summary.get('cv_mae_preseason'))} wins"
        f"   R2 {_fmt(summary.get('cv_r2_preseason'))}",
        f"  In-season model    MAE {_fmt(summary.get('cv_mae_profile'))} wins"
        f"   R2 {_fmt(summary.get('cv_r2_profile'))}",
    ]

    if deltas:
        lines += ["", f"WEEK OVER WEEK (versus {deltas['compared_to']})"]
        for key, label in METRIC_LABELS.items():
            if key in deltas:
                lines.append(f"  {label:<36} {_delta_phrase(key, deltas[key])}")
    else:
        lines += ["", "WEEK OVER WEEK", "  No snapshot from a week ago to compare against yet."]

    if summary.get("overperformers"):
        lines += ["", f"TOP {OVERPERFORMER_COUNT} OVERPERFORMERS"]
        lines += [f"  {line}" for line in _surprise_lines(summary["overperformers"])]
    if summary.get("underperformers"):
        lines += ["", f"BOTTOM {UNDERPERFORMER_COUNT} UNDERPERFORMERS"]
        lines += [f"  {line}" for line in _surprise_lines(summary["underperformers"])]

    started = bool(summary.get("season_started"))
    lines += _conference_board_lines(summary, started=started)
    heading = "FULL PROJECTION TABLE" if started else "PRE-SEASON PROJECTION, ALL 30 TEAMS"
    lines += ["", heading, _text_table(table, table_columns(started))]
    if started:
        lines += [
            "",
            "Pre 82 = locked pre-season projection. In 82 = projection from current",
            "efficiency. Proj 82 = banked wins plus the projected remainder.",
            "Err = wins above or below the pre-season pace at this point.",
            "Seed / Tier = projected conference standings (top 6 playoffs, 7–10 play-in).",
        ]
    return "\n".join(lines)


def _conference_board_lines(summary: dict, *, started: bool) -> list[str]:
    boards = summary.get("conferences") or {}
    if not any(boards.get(name) for name in ("East", "West")):
        return []
    win_key = "updated_pred_wins" if started else "preseason_pred_wins"
    lines = ["", "PROJECTED PLAYOFF PICTURE"]
    for conference in ("East", "West"):
        rows = boards.get(conference) or []
        if not rows:
            continue
        lines.append(f"  {conference}ern Conference")
        for row in rows:
            seed = _fmt(row.get("conference_rank"), "{:.0f}")
            team = row.get("TEAM_ABBREVIATION") or row.get("TEAM_NAME") or "?"
            wins = _fmt(row.get(win_key), "{:.1f}")
            tier = row.get("playoff_tier") or ""
            lines.append(f"    {seed:>2}. {team:<4} {wins} wins  {tier}")
    return lines


def _surprise_lines(rows: list[dict]) -> list[str]:
    out = []
    for row in rows:
        team = row.get("TEAM_ABBREVIATION") or row.get("TEAM_NAME") or "?"
        out.append(
            f"{team:<4} {_fmt(row.get('actual_wins'), '{:.0f}')}-"
            f"{_fmt(row.get('actual_losses'), '{:.0f}')}  "
            f"expected {_fmt(row.get('preseason_expected_wins_at_gp'), '{:.1f}')} wins  "
            f"({_fmt(row.get('preseason_win_error_at_gp'), '{:+.1f}')})  "
            f"now projecting {_fmt(row.get('updated_pred_wins'), '{:.1f}')}"
        )
    return out


def _text_table(table: pd.DataFrame, columns: list[tuple[str, str, str]]) -> str:
    widths = [max(len(label), 7) for _, label, _ in columns]
    header = "  ".join(
        label.ljust(width) for (_, label, _), width in zip(columns, widths)
    )
    rows = [header, "-" * len(header)]
    for _, row in table.iterrows():
        cells = []
        for (key, _, spec), width in zip(columns, widths):
            value = row.get(key)
            text = value if spec == "{}" else _fmt(value, spec, missing="—")
            cells.append(str(text if text is not None else "—").ljust(width))
        rows.append("  ".join(cells))
    return "\n".join(rows)


def _card(label: str, value: str, note: str = "") -> str:
    note_html = f'<div class="note">{html.escape(note)}</div>' if note else ""
    return (
        f'<div class="report-card"><div class="label">{html.escape(label)}</div>'
        f'<div class="value">{html.escape(value)}</div>{note_html}</div>'
    )


def _render_html(
    season: str,
    as_of: str,
    summary: dict,
    table: pd.DataFrame,
    deltas: dict | None,
    metrics: dict | None,
) -> str:
    started = bool(summary.get("season_started"))
    progress = summary.get("season_progress_pct")
    progress_note = (
        f"{_fmt(progress * 100 if progress is not None else None, '{:.1f}')}% of the season"
        if started
        else "Season has not started"
    )

    cards = [
        _card(
            "Season progress",
            f"{_fmt(summary.get('games_played_avg'), '{:.1f}')} / {GAMES_PER_SEASON}",
            progress_note,
        ),
        _card(
            "Pre-season MAE",
            f"{_fmt(summary.get('mae_preseason_at_gp'))}" + (" wins" if started else ""),
            "vs wins banked to date" if started else "awaiting first games",
        ),
        _card(
            "In-season MAE",
            f"{_fmt(summary.get('mae_inseason_at_gp'))}" + (" wins" if started else ""),
            "efficiency-based model" if started else "awaiting first games",
        ),
        _card(
            "Rank correlation",
            _fmt(summary.get("spearman_rho")),
            "projected vs actual standings",
        ),
    ]

    sections = [
        _html_header(season, as_of, summary, metrics),
        f'<div class="report-cards">{"".join(cards)}</div>',
        _html_training_block(summary),
        _html_deltas(deltas),
    ]

    if started:
        sections.append(
            _html_surprises(
                f"Top {OVERPERFORMER_COUNT} overperformers",
                summary.get("overperformers") or [],
                positive=True,
            )
        )
        sections.append(
            _html_surprises(
                f"Bottom {UNDERPERFORMER_COUNT} underperformers",
                summary.get("underperformers") or [],
                positive=False,
            )
        )
    sections.append(_html_conference_boards(summary, started=started))
    sections.append(_html_table(table, started))
    sections.append(_html_footer(started))

    body = "\n".join(s for s in sections if s)
    return theme.wrap_report_document(
        title=_subject(season, as_of, summary),
        body_html=body,
    )


def _html_header(season: str, as_of: str, summary: dict, metrics: dict | None) -> str:
    return (
        '<section class="report-hero">'
        f'<p class="report-kicker">Weekly report · {html.escape(as_of)}</p>'
        f'<h1 class="report-title">NBA {html.escape(season)} Win Predictor</h1>'
        f'<p class="report-lede">{html.escape(_training_line(summary, metrics))}</p>'
        "</section>"
    )


def _html_training_block(summary: dict) -> str:
    rows = [
        ("Pre-season model", summary.get("cv_mae_preseason"), summary.get("cv_r2_preseason")),
        ("In-season model", summary.get("cv_mae_profile"), summary.get("cv_r2_profile")),
    ]
    cells = "".join(
        "<tr>"
        f"<td>{html.escape(name)}</td>"
        f'<td class="num">{_fmt(mae)} wins</td>'
        f'<td class="num">{_fmt(r2)}</td>'
        "</tr>"
        for name, mae, r2 in rows
    )
    return (
        '<section class="report-section">'
        "<h2>Cross-validated training accuracy</h2>"
        '<p class="report-section-note">Leave-one-season-out across the training window.</p>'
        '<div class="report-table-wrap"><table class="report-table">'
        "<thead><tr><th>Model</th><th class=\"num\">MAE</th>"
        '<th class="num">R²</th></tr></thead>'
        f"<tbody>{cells}</tbody></table></div></section>"
    )


def _html_deltas(deltas: dict | None) -> str:
    if not deltas:
        return (
            '<section class="report-section"><div class="report-panel">'
            "<h2>Week over week</h2>"
            '<p class="report-section-note" style="margin:0;">'
            "No snapshot from a week ago to compare against yet.</p>"
            "</div></section>"
        )
    rows = []
    for key, label in METRIC_LABELS.items():
        if key not in deltas:
            continue
        value = deltas[key]
        improved = value < 0 if key in LOWER_IS_BETTER else value > 0
        if abs(value) < 0.005:
            cls = "muted"
        else:
            cls = "pos" if improved else "neg"
        rows.append(
            f"<tr><td>{html.escape(label)}</td>"
            f'<td class="num {cls}">{value:+.2f}</td></tr>'
        )
    return (
        '<section class="report-section">'
        "<h2>Week over week</h2>"
        f'<p class="report-section-note">Compared with '
        f"{html.escape(str(deltas['compared_to']))}. Green is an improvement.</p>"
        '<div class="report-table-wrap"><table class="report-table">'
        f"<tbody>{''.join(rows)}</tbody></table></div></section>"
    )


def _html_conference_boards(summary: dict, *, started: bool) -> str:
    boards = summary.get("conferences") or {}
    if not any(boards.get(name) for name in ("East", "West")):
        return ""
    win_key = "updated_pred_wins" if started else "preseason_pred_wins"
    columns = "".join(
        _html_conference_column(conference, boards.get(conference) or [], win_key)
        for conference in ("East", "West")
    )
    return (
        '<section class="report-section">'
        "<h2>Projected playoff picture</h2>"
        '<p class="report-section-note">'
        "Top 6 in each conference make the playoffs; seeds 7–10 play in.</p>"
        f'<div class="report-boards">{columns}</div></section>'
    )


def _html_conference_column(
    conference: str, rows: list[dict], win_key: str
) -> str:
    if not rows:
        return (
            f'<div class="report-panel"><h3>{html.escape(conference)}ern</h3></div>'
        )
    cells = []
    for row in rows:
        tier = str(row.get("playoff_tier") or "")
        tier_cls = {
            "Playoffs": "tier-playoffs",
            "Play-in": "tier-play-in",
        }.get(tier, "")
        cells.append(
            f'<tr class="{tier_cls}">'
            f'<td class="num">{_fmt(row.get("conference_rank"), "{:.0f}")}</td>'
            f'<td class="team">{html.escape(str(row.get("TEAM_ABBREVIATION") or "?"))}</td>'
            f'<td class="num">{_fmt(row.get(win_key), "{:.1f}")}</td>'
            f'<td class="muted">{html.escape(tier)}</td></tr>'
        )
    return (
        f'<div class="report-panel"><h3>{html.escape(conference)}ern Conference</h3>'
        '<div class="report-table-wrap"><table class="report-table">'
        f"<tbody>{''.join(cells)}</tbody></table></div></div>"
    )


def _html_surprises(title: str, rows: list[dict], *, positive: bool) -> str:
    if not rows:
        return ""
    cls = "pos" if positive else "neg"
    cells = "".join(
        "<tr>"
        f'<td class="team">{html.escape(str(row.get("TEAM_ABBREVIATION") or row.get("TEAM_NAME") or "?"))}</td>'
        f"<td>{_fmt(row.get('actual_wins'), '{:.0f}')}-"
        f"{_fmt(row.get('actual_losses'), '{:.0f}')}</td>"
        f'<td class="num">{_fmt(row.get("preseason_expected_wins_at_gp"), "{:.1f}")}</td>'
        f'<td class="num {cls}">{_fmt(row.get("preseason_win_error_at_gp"), "{:+.1f}")}</td>'
        f'<td class="num">{_fmt(row.get("updated_pred_wins"), "{:.1f}")}</td>'
        "</tr>"
        for row in rows
    )
    return (
        f'<section class="report-section"><h2>{html.escape(title)}</h2>'
        '<div class="report-table-wrap"><table class="report-table"><thead><tr>'
        "<th>Team</th><th>Record</th><th class=\"num\">Expected W</th>"
        '<th class="num">Error</th><th class="num">Proj 82</th>'
        f"</tr></thead><tbody>{cells}</tbody></table></div></section>"
    )


def _html_table(table: pd.DataFrame, started: bool) -> str:
    columns = table_columns(started)
    header = "".join(
        '<th{cls}>{label}</th>'.format(
            cls="" if key == "TEAM_ABBREVIATION" else ' class="num"',
            label=html.escape(label),
        )
        for key, label, _ in columns
    )
    body = []
    for _, row in table.iterrows():
        cells = []
        for key, _, spec in columns:
            value = row.get(key)
            text = str(value) if spec == "{}" else _fmt(value, spec, missing="—")
            classes: list[str] = []
            if key != "TEAM_ABBREVIATION":
                classes.append("num")
            if key == "TEAM_ABBREVIATION":
                classes.append("team")
            if key in {"updated_pred_wins", "preseason_pred_wins"}:
                classes.append("mine")
            if key == "preseason_win_error_at_gp":
                number = _num(value)
                if number is not None and abs(number) >= 0.05:
                    classes.append("pos" if number > 0 else "neg")
            class_attr = f' class="{" ".join(classes)}"' if classes else ""
            cells.append(f"<td{class_attr}>{html.escape(text)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    title = "All 30 teams" if started else "Pre-season projection, all 30 teams"
    return (
        f'<section class="report-section"><h2>{html.escape(title)}</h2>'
        '<div class="report-table-wrap"><table class="report-table">'
        f"<thead><tr>{header}</tr></thead>"
        f"<tbody>{''.join(body)}</tbody></table></div></section>"
    )


def _html_footer(started: bool) -> str:
    glossary = (
        "<strong>Pre 82</strong> is the locked pre-season projection. "
        "<strong>In 82</strong> projects from current efficiency alone. "
        "<strong>Proj 82</strong> blends banked wins with the projected remainder. "
        "<strong>Err</strong> is wins above or below the pre-season pace at this "
        "point in the schedule.<br>"
        if started
        else "Projections are reconciled so the league totals 1,230 wins.<br>"
    )
    return (
        f'<footer class="report-footer">{glossary}'
        "Generated locally by nba_pipeline."
        "</footer>"
    )
