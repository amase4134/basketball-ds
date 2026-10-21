"""Scrape Opta Analyst's pre-season NBA win projections.

The article lists every team as a short block of label/value lines —
``1. New York Knicks / Projected Wins: / 51.5 / Playoff Percentage: / 88.0% /
Preseason TRACR Rank: / 3`` — grouped under Eastern and Western Conference
headings. The page is server-rendered, so the visible text is enough; no
JavaScript has to run.
"""

from __future__ import annotations

import logging
import re
import urllib.request
from datetime import date
from html.parser import HTMLParser

import pandas as pd
from nba_api.stats.static import teams as static_teams

from nba_pipeline.config import LEAGUE_TEAMS, Settings, settings
from nba_pipeline.load.parquet import replace_season_partition

logger = logging.getLogger(__name__)

OPTA_URL = (
    "https://theanalyst.com/articles/2026-27-nba-predictions-win-totals-finals-chances"
)

# The site serves a bot-wall to clients without a browser user agent.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

CONFERENCES = {"Eastern Conference": "East", "Western Conference": "West"}

TEAM_HEADING = re.compile(r"^(\d{1,2})\.\s+(.+)$")
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")

OPTA_COLUMNS = [
    "TEAM_ID",
    "TEAM_NAME",
    "TEAM_ABBREVIATION",
    "conference",
    "opta_conf_rank",
    "opta_proj_wins",
    "opta_playoff_pct",
    "opta_tracr_rank",
]


class _TextLines(HTMLParser):
    """Collect visible text nodes, one stripped line per node."""

    SKIP = {"script", "style", "noscript", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth:
            return
        text = " ".join(data.split())
        if text:
            self.lines.append(text)


def page_lines(page_html: str) -> list[str]:
    parser = _TextLines()
    parser.feed(page_html)
    parser.close()
    return parser.lines


def _value_after(lines: list[str], start: int, label: str, window: int = 6) -> float | None:
    """The first number on the line after `label`, searching a few lines ahead."""
    for i in range(start, min(start + window, len(lines))):
        if lines[i].lower().startswith(label.lower()):
            # The value is usually on the next line, but tolerate it sharing the label's.
            for candidate in (lines[i][len(label):], *lines[i + 1 : i + 2]):
                match = NUMBER.search(candidate.split(":", 1)[-1])
                if match:
                    return float(match.group())
    return None


def _team_directory() -> dict[str, dict]:
    """Full names and nicknames from nba_api's bundled directory (no network)."""
    directory: dict[str, dict] = {}
    for team in static_teams.get_teams():
        directory[team["full_name"].lower()] = team
        directory[team["nickname"].lower()] = team
    # Opta and the NBA disagree on how to write the Clippers.
    directory["la clippers"] = directory["los angeles clippers"]
    return directory


def parse_projections(page_html: str) -> pd.DataFrame:
    """Turn the article into one row per team."""
    lines = page_lines(page_html)
    directory = _team_directory()

    rows: list[dict] = []
    conference: str | None = None
    for i, line in enumerate(lines):
        if line in CONFERENCES:
            conference = CONFERENCES[line]
            continue
        heading = TEAM_HEADING.match(line)
        if not heading or conference is None:
            continue
        if not (i + 1 < len(lines) and lines[i + 1].startswith("Projected Wins")):
            continue

        name = heading.group(2).strip()
        team = directory.get(name.lower()) or directory.get(name.split()[-1].lower())
        if team is None:
            raise ValueError(f"Opta team '{name}' does not match any NBA team")

        rows.append(
            {
                "TEAM_ID": int(team["id"]),
                "TEAM_NAME": team["full_name"],
                "TEAM_ABBREVIATION": team["abbreviation"],
                "conference": conference,
                "opta_conf_rank": int(heading.group(1)),
                "opta_proj_wins": _value_after(lines, i + 1, "Projected Wins"),
                "opta_playoff_pct": _value_after(lines, i + 1, "Playoff Percentage"),
                "opta_tracr_rank": _value_after(lines, i + 1, "Preseason TRACR Rank"),
            }
        )

    frame = pd.DataFrame(rows, columns=OPTA_COLUMNS)
    _validate(frame)
    frame["opta_playoff_pct"] = frame["opta_playoff_pct"] / 100.0
    frame["opta_tracr_rank"] = frame["opta_tracr_rank"].astype("int64")
    frame["opta_league_rank"] = (
        frame["opta_proj_wins"].rank(ascending=False, method="min").astype("int64")
    )
    return frame.sort_values("opta_proj_wins", ascending=False).reset_index(drop=True)


def _validate(frame: pd.DataFrame) -> None:
    if len(frame) != LEAGUE_TEAMS:
        raise ValueError(
            f"Expected {LEAGUE_TEAMS} teams in the Opta article, parsed {len(frame)}; "
            "the page layout may have changed."
        )
    if frame["TEAM_ID"].duplicated().any():
        dupes = frame.loc[frame["TEAM_ID"].duplicated(), "TEAM_NAME"].tolist()
        raise ValueError(f"Opta article lists these teams twice: {dupes}")
    missing = frame[["opta_proj_wins", "opta_playoff_pct", "opta_tracr_rank"]].isna()
    if missing.any().any():
        teams = frame.loc[missing.any(axis=1), "TEAM_NAME"].tolist()
        raise ValueError(f"Opta article is missing a projection for: {teams}")


def fetch_article(url: str = OPTA_URL, *, cfg: Settings | None = None) -> str:
    cfg = cfg or settings
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=cfg.request_timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def raw_article_path(season: str, pull_date: str, cfg: Settings | None = None):
    cfg = cfg or settings
    return (
        cfg.raw_dir
        / "opta_projections"
        / f"season={season}"
        / f"pull_date={pull_date}"
        / "article.html"
    )


def latest_raw_article(season: str, cfg: Settings | None = None):
    cfg = cfg or settings
    pulls = sorted((cfg.raw_dir / "opta_projections" / f"season={season}").glob(
        "pull_date=*/article.html"
    ))
    return pulls[-1] if pulls else None


def extract_opta_projections(
    season: str,
    *,
    cfg: Settings | None = None,
    url: str = OPTA_URL,
    offline: bool = False,
) -> pd.DataFrame:
    """Scrape (or re-parse the last saved copy of) the article into curated Parquet."""
    cfg = cfg or settings
    if offline:
        path = latest_raw_article(season, cfg)
        if path is None:
            raise FileNotFoundError(
                f"No saved Opta article for {season}; run `compare-opta` without --offline."
            )
        page_html = path.read_text()
        logger.info("Parsing saved Opta article %s", path)
    else:
        logger.info("Fetching %s", url)
        page_html = fetch_article(url, cfg=cfg)
        path = raw_article_path(season, date.today().isoformat(), cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(page_html)

    frame = parse_projections(page_html)
    frame["source_url"] = url
    frame["pulled_from"] = path.parent.name.removeprefix("pull_date=")
    replace_season_partition(frame, cfg.curated_dir / "opta_projections", season)
    logger.info("Wrote %s Opta projections for %s", len(frame), season)
    return frame


def load_opta_projections(season: str, *, cfg: Settings | None = None) -> pd.DataFrame:
    cfg = cfg or settings
    path = cfg.curated_dir / "opta_projections" / f"season={season}" / "data.parquet"
    if not path.exists():
        raise FileNotFoundError(f"No Opta projections for {season}; run `compare-opta`.")
    return pd.read_parquet(path)
