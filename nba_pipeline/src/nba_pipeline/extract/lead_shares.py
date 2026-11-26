"""Team lead-share aggregates from pbpstats score-margin breakdown.

Adds ``POSS_AHEAD_SHARE`` / ``POSS_BEHIND_SHARE`` / ``POSS_TIED_SHARE`` — the
fraction of possessions that begin with the team ahead, behind, or tied. Used
as a durable stand-in for clock-weighted time-ahead % until curated play-by-play
is available.
"""

from __future__ import annotations

import logging
import time
from typing import Iterable

import pandas as pd
import requests

from nba_pipeline.config import TEAM_LEAD_SHARE_COLUMNS, Settings, settings

logger = logging.getLogger(__name__)

PBPSTATS_MARGIN_URL = "https://api.pbpstats.com/get-score-margin-breakdown/nba"


def parse_margin_label(label: str) -> int | None:
    """Parse a pbpstats margin bucket label into a signed integer.

    Labels look like ``"3"``, ``"-12"``, ``"20+"``, or ``"-20+"``. The ``+``
    buckets are treated as that signed magnitude (still ahead/behind).
    """
    text = str(label).strip()
    if text.endswith("+"):
        text = text[:-1]
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def shares_from_margin_buckets(margins: Iterable[dict]) -> dict[str, float]:
    """Sum possession frequencies into ahead / behind / tied shares."""
    ahead = behind = tied = 0.0
    for row in margins:
        margin = parse_margin_label(row.get("margin", ""))
        try:
            freq = float(row.get("frequency", 0.0))
        except (TypeError, ValueError):
            continue
        if margin is None:
            continue
        if margin > 0:
            ahead += freq
        elif margin < 0:
            behind += freq
        else:
            tied += freq
    return {
        "POSS_AHEAD_SHARE": ahead,
        "POSS_BEHIND_SHARE": behind,
        "POSS_TIED_SHARE": tied,
    }


def _fetch_team_margins(team_id: int, season: str, cfg: Settings) -> list[dict]:
    response = requests.get(
        PBPSTATS_MARGIN_URL,
        params={
            "Season": season,
            "SeasonType": "Regular Season",
            "TeamId": str(team_id),
        },
        headers={
            "User-Agent": "nba-pipeline/0.1 (+team-lead-shares)",
            "Accept": "application/json",
        },
        timeout=cfg.request_timeout,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("margins") or []


def fetch_lead_shares(
    team_ids: Iterable[int],
    season: str,
    *,
    cfg: Settings | None = None,
) -> pd.DataFrame:
    """One row per team with POSS_*_SHARE columns for ``season``."""
    cfg = cfg or settings
    rows: list[dict] = []
    errors = 0
    ids = [int(tid) for tid in team_ids]
    for i, team_id in enumerate(ids, start=1):
        try:
            margins = _fetch_team_margins(team_id, season, cfg)
            shares = shares_from_margin_buckets(margins)
            rows.append({"TEAM_ID": team_id, **shares})
        except Exception as exc:  # noqa: BLE001
            errors += 1
            logger.warning(
                "Lead-share pull failed for team %s (%s): %s", team_id, season, exc
            )
            rows.append(
                {
                    "TEAM_ID": team_id,
                    **{col: float("nan") for col in TEAM_LEAD_SHARE_COLUMNS},
                }
            )
        time.sleep(cfg.request_sleep_seconds)
        if i % 10 == 0:
            logger.info("  lead-share progress %s/%s teams for %s", i, len(ids), season)

    frame = pd.DataFrame(rows)
    if errors:
        logger.warning(
            "Lead shares for %s: %s/%s teams failed", season, errors, len(ids)
        )
    return frame


def attach_lead_shares(
    team_stats: pd.DataFrame,
    season: str,
    *,
    cfg: Settings | None = None,
) -> pd.DataFrame:
    """Left-join lead-share columns onto a team-stats frame."""
    if team_stats.empty or "TEAM_ID" not in team_stats.columns:
        out = team_stats.copy()
        for col in TEAM_LEAD_SHARE_COLUMNS:
            if col not in out.columns:
                out[col] = float("nan")
        return out

    cfg = cfg or settings
    team_ids = (
        pd.to_numeric(team_stats["TEAM_ID"], errors="coerce").dropna().astype("int64")
    )
    unique_ids = sorted(set(team_ids.tolist()))
    logger.info(
        "Fetching lead-share aggregates for %s (%s teams)", season, len(unique_ids)
    )
    shares = fetch_lead_shares(unique_ids, season, cfg=cfg)
    out = team_stats.drop(columns=TEAM_LEAD_SHARE_COLUMNS, errors="ignore").merge(
        shares, on="TEAM_ID", how="left"
    )
    return out
