"""Shared nba_api call helpers with sleep, timeout, and retries."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

import pandas as pd

from nba_pipeline.config import Settings, settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


def with_retry(
    fn: Callable[[], T],
    *,
    cfg: Settings | None = None,
    label: str = "nba_api",
) -> T:
    """Call `fn` with exponential backoff on transient failures."""
    cfg = cfg or settings
    last_exc: Exception | None = None
    for attempt in range(1, cfg.max_retries + 1):
        try:
            result = fn()
            time.sleep(cfg.request_sleep_seconds)
            return result
        except Exception as exc:  # noqa: BLE001 — API can raise many types
            last_exc = exc
            wait = cfg.retry_backoff_base ** attempt
            logger.warning(
                "%s failed (attempt %s/%s): %s — sleeping %.1fs",
                label,
                attempt,
                cfg.max_retries,
                exc,
                wait,
            )
            time.sleep(wait)
    assert last_exc is not None
    raise last_exc


def fetch_dataframe(
    endpoint_factory: Callable[[], object],
    *,
    frame_index: int = 0,
    cfg: Settings | None = None,
    label: str = "nba_api",
) -> pd.DataFrame:
    """Instantiate an nba_api endpoint and return one of its DataFrames."""

    def _call() -> pd.DataFrame:
        endpoint = endpoint_factory()
        frames = endpoint.get_data_frames()
        if not frames:
            return pd.DataFrame()
        return frames[frame_index].copy()

    return with_retry(_call, cfg=cfg, label=label)
