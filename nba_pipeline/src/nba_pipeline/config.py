"""Pipeline configuration: seasons, paths, rate limits."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


def default_seasons(n: int = 5, as_of: date | None = None) -> list[str]:
    """Return the last `n` NBA season labels ending at the current season.

    NBA seasons span two calendar years and are labeled like '2023-24'.
    The season year flips in July (approx. start of free agency / offseason).
    """
    today = as_of or date.today()
    # NBA seasons run roughly Oct–Jun. Use Oct as the flip so July–Sep
    # still treat the season that just ended as current (has stats data).
    start_year = today.year if today.month >= 10 else today.year - 1
    seasons: list[str] = []
    for i in range(n - 1, -1, -1):
        y = start_year - i
        seasons.append(f"{y}-{str(y + 1)[-2:]}")
    return seasons


@dataclass
class Settings:
    """Runtime settings for extract/transform/load."""

    project_root: Path = field(
        default_factory=lambda: Path(__file__).resolve().parents[2]
    )
    seasons: list[str] = field(default_factory=lambda: default_seasons(5))
    min_minutes: int = 500
    request_sleep_seconds: float = 0.8
    max_retries: int = 5
    retry_backoff_base: float = 2.0
    request_timeout: float = 60.0

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

    def ensure_dirs(self) -> None:
        for path in (
            self.raw_dir,
            self.curated_dir / "players",
            self.curated_dir / "teams",
            self.curated_dir / "player_season_stats",
            self.curated_dir / "shot_charts",
            self.meta_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


DATASETS = ("players", "teams", "player_season_stats", "shot_charts")

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

settings = Settings()
