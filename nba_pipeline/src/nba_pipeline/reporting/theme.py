"""Shared dark report chrome for archived HTML and the local index."""

from __future__ import annotations

import html
import re
from pathlib import Path

from nba_pipeline.config import Settings, settings

ASSETS_DIR = Path(__file__).resolve().parent / "assets"
THEME_CSS_NAME = "report-theme.css"
INDEX_NAME = "index.html"
DEFAULT_ACCENT = "broadcast"
ACCENT_WAYS = ("broadcast", "scoreboard")

WEEKLY_RE = re.compile(r"^weekly_win_report_(\d{4}-\d{2}-\d{2})\.html$")
OPTA_RE = re.compile(r"^opta_comparison_(\d{4}-\d{2})_(\d{4}-\d{2}-\d{2})\.html$")


def theme_css_path() -> Path:
    return ASSETS_DIR / THEME_CSS_NAME


def load_theme_css() -> str:
    return theme_css_path().read_text()


def sync_theme_css(cfg: Settings | None = None) -> Path:
    """Copy the canonical stylesheet into the reports archive directory."""
    cfg = cfg or settings
    cfg.reports_dir.mkdir(parents=True, exist_ok=True)
    destination = cfg.reports_dir / THEME_CSS_NAME
    destination.write_text(load_theme_css())
    return destination


def wrap_report_document(
    *,
    title: str,
    body_html: str,
    accent: str = DEFAULT_ACCENT,
    embed_css: bool = True,
) -> str:
    """Assemble a self-contained dark-themed report page.

    Archived digests embed the stylesheet so email clients and
    open-from-disk both work without a network fetch of the CSS file.
    """
    if accent not in ACCENT_WAYS:
        accent = DEFAULT_ACCENT
    style_block = f"<style>\n{load_theme_css()}\n</style>" if embed_css else (
        f'<link rel="stylesheet" href="./{THEME_CSS_NAME}">'
    )
    accent_script = """
<script>
(function () {
  var root = document.documentElement;
  var key = "nba-report-accent";
  var stored = localStorage.getItem(key);
  if (stored === "broadcast" || stored === "scoreboard") {
    root.setAttribute("data-accent", stored);
  }
  function setAccent(next) {
    root.setAttribute("data-accent", next);
    localStorage.setItem(key, next);
    document.querySelectorAll("[data-accent-choice]").forEach(function (btn) {
      var active = btn.getAttribute("data-accent-choice") === next;
      btn.setAttribute("aria-pressed", active ? "true" : "false");
      btn.classList.toggle("is-active", active);
    });
  }
  document.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-accent-choice]");
    if (!btn) return;
    setAccent(btn.getAttribute("data-accent-choice"));
  });
  setAccent(root.getAttribute("data-accent") || "broadcast");
})();
</script>
"""
    brand = f"""
<header class="report-brand">
  <div class="report-brand-mark">
    <span class="report-brand-name">Basketball Data Science</span>
    <span class="report-brand-sub">Prediction reports</span>
  </div>
  <div class="report-accent-toggle" role="group" aria-label="Accent colorway">
    <button type="button" data-accent-choice="broadcast" aria-pressed="true">Broadcast</button>
    <button type="button" data-accent-choice="scoreboard" aria-pressed="false">Scoreboard</button>
  </div>
</header>
"""
    return f"""<!DOCTYPE html>
<html lang="en" data-accent="{html.escape(accent)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
{style_block}
</head>
<body class="report-body">
<main class="report-shell">
{brand}
{body_html}
</main>
{accent_script}
</body>
</html>
"""


def classify_report(filename: str) -> dict | None:
    """Return metadata for a known archived report filename, else None."""
    name = Path(filename).name
    if name in {INDEX_NAME, THEME_CSS_NAME}:
        return None
    if not name.endswith(".html"):
        return None
    weekly = WEEKLY_RE.match(name)
    if weekly:
        return {
            "filename": name,
            "kind": "weekly_win",
            "label": "Weekly win predictor",
            "season": None,
            "as_of": weekly.group(1),
        }
    opta = OPTA_RE.match(name)
    if opta:
        return {
            "filename": name,
            "kind": "opta_comparison",
            "label": "Opta comparison",
            "season": opta.group(1),
            "as_of": opta.group(2),
        }
    return {
        "filename": name,
        "kind": "other",
        "label": "Report",
        "season": None,
        "as_of": None,
    }


def list_archived_reports(cfg: Settings | None = None) -> list[dict]:
    cfg = cfg or settings
    reports_dir = cfg.reports_dir
    if not reports_dir.exists():
        return []
    items: list[dict] = []
    for path in sorted(reports_dir.glob("*.html"), reverse=True):
        meta = classify_report(path.name)
        if meta is None:
            continue
        meta["size_bytes"] = path.stat().st_size
        items.append(meta)
    items.sort(key=lambda item: (item.get("as_of") or "", item["filename"]), reverse=True)
    return items


def write_reports_index(cfg: Settings | None = None) -> Path:
    """Refresh the browsable index beside archived report HTML files."""
    cfg = cfg or settings
    sync_theme_css(cfg)
    items = list_archived_reports(cfg)
    if items:
        rows = []
        for item in items:
            when = item.get("as_of") or "—"
            season = f" · {html.escape(item['season'])}" if item.get("season") else ""
            rows.append(
                "<li>"
                f'<a href="./{html.escape(item["filename"])}">'
                f'<span><span class="kind">{html.escape(item["label"])}</span>{season}'
                f'<div>{html.escape(item["filename"])}</div></span>'
                f'<span class="when">{html.escape(when)}</span>'
                "</a></li>"
            )
        listing = f'<ul class="report-index-list">{"".join(rows)}</ul>'
    else:
        listing = (
            '<p class="report-empty">No archived reports yet. Run '
            "<code>python -m nba_pipeline send-win-report --dry-run</code> or "
            "<code>python -m nba_pipeline compare-opta</code> to create one.</p>"
        )

    body = f"""
<section class="report-hero">
  <p class="report-kicker">Local archive</p>
  <h1 class="report-title">NBA prediction reports</h1>
  <p class="report-lede">Weekly win-predictor digests and Opta comparisons generated by nba_pipeline. Toggle Broadcast or Scoreboard accents above.</p>
</section>
<section class="report-section">
  <h2>Archived reports</h2>
  <p class="report-section-note">Open any file below, or browse the same archive from the Local Explorer Reports page.</p>
  {listing}
</section>
<footer class="report-footer">
  Shared dark theme tokens live in report-theme.css. Accent choice is remembered in this browser.
</footer>
"""
    document = wrap_report_document(
        title="NBA prediction reports",
        body_html=body,
        embed_css=False,
    )
    path = cfg.reports_dir / INDEX_NAME
    path.write_text(document)
    return path


def archive_touched(cfg: Settings | None = None) -> Path:
    """Keep CSS + index current after any report write."""
    sync_theme_css(cfg)
    return write_reports_index(cfg)
