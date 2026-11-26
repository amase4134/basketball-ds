"""Shared nba_api call helpers with sleep, timeout, and retries."""

from __future__ import annotations

import logging
import socket
import time
from collections.abc import Callable
from typing import TypeVar

import pandas as pd

from nba_pipeline.config import Settings, settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Prefer IPv4. Some cloud / container networks resolve stats.nba.com to IPv6
# first, then hang (errno 101 Network unreachable) while IPv4 still connects.
_IPV4_PREFERRED = False


def prefer_ipv4() -> None:
    """Force urllib3 / requests / nba_api to open IPv4 sockets only."""
    global _IPV4_PREFERRED
    if _IPV4_PREFERRED:
        return
    try:
        import urllib3.util.connection as urllib3_connection
    except ImportError:  # pragma: no cover - urllib3 always ships with requests
        return

    def _create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, socket_options=None):  # noqa: ANN001
        host, port = address
        err: OSError | None = None
        for family, socktype, proto, _canon, sockaddr in socket.getaddrinfo(
            host, port, socket.AF_INET, socket.SOCK_STREAM
        ):
            sock = None
            try:
                sock = socket.socket(family, socktype, proto)
                if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                    sock.settimeout(timeout)
                if source_address:
                    sock.bind(source_address)
                if socket_options:
                    for opt in socket_options:
                        sock.setsockopt(*opt)
                sock.connect(sockaddr)
                return sock
            except OSError as exc:
                err = exc
                if sock is not None:
                    sock.close()
        if err is not None:
            raise err
        raise OSError(f"No IPv4 address for {host!r}")

    urllib3_connection.create_connection = _create_connection  # type: ignore[assignment]
    _IPV4_PREFERRED = True
    logger.info("Preferring IPv4 for outbound HTTP (stats.nba.com / nba_api)")


prefer_ipv4()


def probe_stats_nba(*, timeout: float = 20.0) -> str | None:
    """Return None when stats.nba.com answers; otherwise a short error string.

    Used to fail fast before a multi-hundred-game PlayByPlayV2 loop when the
    host is reachable at TCP/TLS but never returns HTTP body bytes (Akamai
    blackhole) or is otherwise blocked from this network.
    """
    prefer_ipv4()
    try:
        import requests
    except ImportError:  # pragma: no cover
        return "requests not installed"

    url = "https://stats.nba.com/stats/commonteamyears"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.nba.com/",
        "Origin": "https://www.nba.com",
        "x-nba-stats-origin": "stats",
        "x-nba-stats-token": "true",
        "Accept": "application/json, text/plain, */*",
    }
    try:
        response = requests.get(
            url,
            params={"LeagueID": "00"},
            headers=headers,
            timeout=timeout,
        )
        if response.status_code >= 400:
            return f"HTTP {response.status_code}"
        if not response.content:
            return "empty response body"
        return None
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


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
