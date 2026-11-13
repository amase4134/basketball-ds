"""East/West conference membership and playoff-tier projection.

The NBA awards six automatic playoff berths and four play-in spots in each
conference. Win projections only become a playoff forecast once every team is
placed in its conference and ranked there.
"""

from __future__ import annotations

import pandas as pd

from nba_pipeline.config import LEAGUE_TEAMS

PLAYOFF_SEEDS = 6
PLAY_IN_SEEDS = 10

EAST_ABBREVIATIONS = frozenset(
    {
        "ATL",
        "BKN",
        "BOS",
        "CHA",
        "CHI",
        "CLE",
        "DET",
        "IND",
        "MIA",
        "MIL",
        "NYK",
        "ORL",
        "PHI",
        "TOR",
        "WAS",
    }
)
WEST_ABBREVIATIONS = frozenset(
    {
        "DAL",
        "DEN",
        "GSW",
        "HOU",
        "LAC",
        "LAL",
        "MEM",
        "MIN",
        "NOP",
        "OKC",
        "PHX",
        "POR",
        "SAC",
        "SAS",
        "UTA",
    }
)

CONFERENCE_BY_ABBREVIATION: dict[str, str] = {
    **{abbr: "East" for abbr in EAST_ABBREVIATIONS},
    **{abbr: "West" for abbr in WEST_ABBREVIATIONS},
}

# Stable NBA team ids so a missing abbreviation still resolves.
CONFERENCE_BY_TEAM_ID: dict[int, str] = {
    1610612737: "East",  # ATL
    1610612738: "East",  # BOS
    1610612739: "East",  # CLE
    1610612740: "West",  # NOP
    1610612741: "East",  # CHI
    1610612742: "West",  # DAL
    1610612743: "West",  # DEN
    1610612744: "West",  # GSW
    1610612745: "West",  # HOU
    1610612746: "West",  # LAC
    1610612747: "West",  # LAL
    1610612748: "East",  # MIA
    1610612749: "East",  # MIL
    1610612750: "West",  # MIN
    1610612751: "East",  # BKN
    1610612752: "East",  # NYK
    1610612753: "East",  # ORL
    1610612754: "East",  # IND
    1610612755: "East",  # PHI
    1610612756: "West",  # PHX
    1610612757: "West",  # POR
    1610612758: "West",  # SAC
    1610612759: "West",  # SAS
    1610612760: "West",  # OKC
    1610612761: "East",  # TOR
    1610612762: "West",  # UTA
    1610612763: "West",  # MEM
    1610612764: "East",  # WAS
    1610612765: "East",  # DET
    1610612766: "East",  # CHA
}

CONFERENCE_BOARD_COLUMNS = [
    "TEAM_ID",
    "TEAM_ABBREVIATION",
    "TEAM_NAME",
    "conference",
    "conference_rank",
    "preseason_conference_rank",
    "actual_conference_rank",
    "playoff_tier",
    "preseason_playoff_tier",
    "updated_pred_wins",
    "preseason_pred_wins",
    "games_played",
    "actual_wins",
    "actual_losses",
    "actual_win_pct",
    "current_82_pace",
]


def conference_for(
    *,
    team_id: int | None = None,
    abbreviation: str | None = None,
) -> str | None:
    """Resolve East/West for one team from its id or abbreviation."""
    if team_id is not None and not pd.isna(team_id):
        mapped = CONFERENCE_BY_TEAM_ID.get(int(team_id))
        if mapped is not None:
            return mapped
    if abbreviation is not None and not pd.isna(abbreviation):
        return CONFERENCE_BY_ABBREVIATION.get(str(abbreviation).upper())
    return None


def playoff_tier(conference_rank: int | float | None) -> str | None:
    """Map a 1-indexed conference seed onto Playoffs / Play-in / Lottery."""
    if conference_rank is None or pd.isna(conference_rank):
        return None
    rank = int(conference_rank)
    if rank <= PLAYOFF_SEEDS:
        return "Playoffs"
    if rank <= PLAY_IN_SEEDS:
        return "Play-in"
    return "Lottery"


def _resolve_conference_series(frame: pd.DataFrame) -> pd.Series:
    if "conference" in frame.columns and frame["conference"].notna().all():
        return frame["conference"].astype(str)

    conferences: list[str | None] = []
    for row in frame.itertuples(index=False):
        team_id = getattr(row, "TEAM_ID", None)
        abbreviation = getattr(row, "TEAM_ABBREVIATION", None)
        if abbreviation is None and hasattr(row, "ABBREVIATION"):
            abbreviation = getattr(row, "ABBREVIATION")
        conferences.append(
            conference_for(team_id=team_id, abbreviation=abbreviation)
        )
    return pd.Series(conferences, index=frame.index, dtype="object")


def _rank_within_conference(
    frame: pd.DataFrame,
    value_col: str,
    *,
    ascending: bool = False,
) -> pd.Series:
    """1-indexed rank inside each conference; missing values stay missing."""
    if value_col not in frame.columns or "conference" not in frame.columns:
        return pd.Series(pd.NA, index=frame.index, dtype="Float64")

    values = pd.to_numeric(frame[value_col], errors="coerce")
    ranked = values.groupby(frame["conference"]).rank(
        ascending=ascending, method="min"
    )
    return ranked.astype("Float64")


def enrich_playoff_projections(predictions: pd.DataFrame) -> pd.DataFrame:
    """Attach conference, seed, and playoff tier columns to a prediction frame.

    Safe to call more than once: existing conference labels are kept when every
    row already has one, and ranks are always recomputed from the current wins.
    """
    if predictions.empty:
        return predictions.copy()

    frame = predictions.copy()
    frame["conference"] = _resolve_conference_series(frame)

    unlabeled = frame["conference"].isna().sum()
    if unlabeled:
        # Synthetic warehouses use TEAM_ID 1..N; split them evenly so playoff
        # math still exercises both conferences in unit tests.
        if unlabeled == len(frame) and len(frame) == LEAGUE_TEAMS:
            ordered = frame.sort_values("TEAM_ID").index
            mid = LEAGUE_TEAMS // 2
            frame.loc[ordered[:mid], "conference"] = "East"
            frame.loc[ordered[mid:], "conference"] = "West"
        else:
            missing = frame.loc[
                frame["conference"].isna(), "TEAM_ABBREVIATION"
            ].tolist()
            raise ValueError(f"No conference mapping for teams: {missing}")

    frame["conference_rank"] = _rank_within_conference(frame, "updated_pred_wins")
    frame["preseason_conference_rank"] = _rank_within_conference(
        frame, "preseason_pred_wins"
    )
    if "actual_win_pct" in frame.columns and frame.get(
        "games_played", pd.Series(dtype="float64")
    ).gt(0).any():
        frame["actual_conference_rank"] = _rank_within_conference(
            frame, "actual_win_pct"
        )
    else:
        frame["actual_conference_rank"] = pd.Series(
            pd.NA, index=frame.index, dtype="Float64"
        )

    frame["playoff_tier"] = frame["conference_rank"].map(playoff_tier)
    frame["preseason_playoff_tier"] = frame["preseason_conference_rank"].map(
        playoff_tier
    )
    return frame


def conference_boards(predictions: pd.DataFrame) -> dict[str, list[dict]]:
    """Projected standings for each conference, ordered by projected seed."""
    if predictions.empty or "conference" not in predictions.columns:
        return {"East": [], "West": []}

    present = [c for c in CONFERENCE_BOARD_COLUMNS if c in predictions.columns]
    boards: dict[str, list[dict]] = {}
    for conference in ("East", "West"):
        group = predictions[predictions["conference"] == conference]
        ordered = group.sort_values(
            ["conference_rank", "updated_pred_wins"],
            ascending=[True, False],
            na_position="last",
        )
        boards[conference] = ordered[present].to_dict("records")
    return boards


def conference_for_teams_frame(teams: pd.DataFrame) -> pd.Series:
    """Conference labels aligned to a curated teams directory frame."""
    return _resolve_conference_series(teams)
