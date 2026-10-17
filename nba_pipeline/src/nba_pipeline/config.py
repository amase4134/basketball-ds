"""Pipeline configuration: seasons, paths, rate limits."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

GAMES_PER_SEASON = 82
LEAGUE_TEAMS = 30
# Every game has one winner, so the league always banks half its games as wins.
LEAGUE_TOTAL_WINS = LEAGUE_TEAMS * GAMES_PER_SEASON // 2


def season_label(start_year: int) -> str:
    """Format the NBA season label that begins in `start_year`, e.g. '2026-27'."""
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def season_start_year(season: str) -> int:
    """Parse the starting calendar year out of a season label like '2026-27'."""
    try:
        return int(season.split("-")[0])
    except (AttributeError, IndexError, ValueError) as exc:
        raise ValueError(f"Unrecognised season label: {season!r}") from exc


def current_season(as_of: date | None = None) -> str:
    """The season currently underway (or the one that just ended, in summer)."""
    today = as_of or date.today()
    # NBA seasons run roughly Oct–Jun. Use Oct as the flip so July–Sep
    # still treat the season that just ended as current (has stats data).
    return season_label(today.year if today.month >= 10 else today.year - 1)


def default_seasons(n: int = 5, as_of: date | None = None) -> list[str]:
    """Return the last `n` NBA season labels ending at the current season.

    NBA seasons span two calendar years and are labeled like '2023-24'.
    The season year flips in July (approx. start of free agency / offseason).
    """
    start_year = season_start_year(current_season(as_of))
    return [season_label(start_year - i) for i in range(n - 1, -1, -1)]


def training_seasons_for(target_season: str, n: int = 5) -> list[str]:
    """The `n` completed seasons immediately before `target_season`.

    Predicting 2026-27 wins trains on 2021-22 … 2025-26: the target season is
    still in progress, so it can never be part of its own training set.
    """
    if n < 1:
        raise ValueError("Need at least one training season")
    target_start = season_start_year(target_season)
    return [season_label(target_start - i) for i in range(n, 0, -1)]


def warehouse_seasons(target_season: str | None = None, n: int = 5) -> list[str]:
    """Training seasons plus the target season, in chronological order."""
    target_season = target_season or current_season()
    return training_seasons_for(target_season, n) + [target_season]


@dataclass
class Settings:
    """Runtime settings for extract/transform/load."""

    project_root: Path = field(
        default_factory=lambda: Path(__file__).resolve().parents[2]
    )
    seasons: list[str] = field(default_factory=lambda: warehouse_seasons())
    target_season: str = field(default_factory=current_season)
    training_season_count: int = 5
    min_minutes: int = 500
    request_sleep_seconds: float = 0.8
    max_retries: int = 5
    retry_backoff_base: float = 2.0
    request_timeout: float = 60.0
    report_recipient: str = "amase4134@gmail.com"

    @property
    def data_dir(self) -> Path:
        return self.project_root / "data"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def curated_dir(self) -> Path:
        return self.data_dir / "curated"

    @property
    def meta_dir(self) -> Path:
        return self.data_dir / "meta"

    @property
    def reports_dir(self) -> Path:
        return self.meta_dir / "reports"

    @property
    def training_seasons(self) -> list[str]:
        return training_seasons_for(self.target_season, self.training_season_count)

    def ensure_dirs(self) -> None:
        for path in (
            self.raw_dir,
            self.curated_dir / "players",
            self.curated_dir / "teams",
            self.curated_dir / "player_season_stats",
            self.curated_dir / "team_season_stats",
            self.curated_dir / "shot_charts",
            self.curated_dir / "win_predictions",
            self.curated_dir / "win_prediction_history",
            self.meta_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


DATASETS = (
    "players",
    "teams",
    "player_season_stats",
    "team_season_stats",
    "shot_charts",
)

# Columns kept for curated shot charts (keys + modeling fields).
SHOT_CHART_COLUMNS = [
    "SEASON",
    "PLAYER_ID",
    "PLAYER_NAME",
    "TEAM_ID",
    "GAME_ID",
    "GAME_DATE",
    "EVENT_TYPE",
    "SHOT_MADE_FLAG",
    "SHOT_TYPE",
    "SHOT_DISTANCE",
    "LOC_X",
    "LOC_Y",
]

# Core columns for archetype / filtering work on player season stats.
PLAYER_STATS_KEEP_PREFIXES = (
    "PLAYER_ID",
    "PLAYER_NAME",
    "NICKNAME",
    "TEAM_ID",
    "TEAM_ABBREVIATION",
    "AGE",
    "GP",
    "W",
    "L",
    "MIN",
    "FGM",
    "FGA",
    "FG_PCT",
    "FG3M",
    "FG3A",
    "FG3_PCT",
    "FTM",
    "FTA",
    "FT_PCT",
    "OREB",
    "DREB",
    "REB",
    "AST",
    "TOV",
    "STL",
    "BLK",
    "BLKA",
    "PF",
    "PFD",
    "PTS",
    "PLUS_MINUS",
    "NBA_FANTASY_PTS",
    "DD2",
    "TD3",
)

# Team identity and standings, carried from the Base measure type.
TEAM_IDENTITY_COLUMNS = [
    "SEASON",
    "TEAM_ID",
    "TEAM_NAME",
    "TEAM_ABBREVIATION",
    "GP",
    "W",
    "L",
    "W_PCT",
    "MIN",
]

# Per-100-possession box score rates from LeagueDashTeamStats(Base).
TEAM_BASE_COLUMNS = [
    "FGM",
    "FGA",
    "FG_PCT",
    "FG3M",
    "FG3A",
    "FG3_PCT",
    "FTM",
    "FTA",
    "FT_PCT",
    "OREB",
    "DREB",
    "REB",
    "AST",
    "TOV",
    "STL",
    "BLK",
    "PTS",
    "PLUS_MINUS",
]

# Efficiency ratings and four factors from LeagueDashTeamStats(Advanced).
# FTA_RATE is absent from that measure type; it is derived as FTA / FGA.
TEAM_ADVANCED_COLUMNS = [
    "OFF_RATING",
    "DEF_RATING",
    "NET_RATING",
    "PACE",
    "EFG_PCT",
    "TM_TOV_PCT",
    "OREB_PCT",
    "DREB_PCT",
    "FTA_RATE",
    "TS_PCT",
    "PIE",
]

TEAM_STATS_COLUMNS = TEAM_IDENTITY_COLUMNS + TEAM_BASE_COLUMNS + TEAM_ADVANCED_COLUMNS

settings = Settings()
