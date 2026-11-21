"""Shared plumbing for the NBA v10 research backfills (quick-261120-nbf).

Research-only: every writer here is pinned to the research root
``E:/MP/data/research/nba_v10`` and refuses any other destination. Serving v9 computes
its scoring baseline and Elo from all loaded history, so a backfilled game landing in
``data/historical/`` or ``data/processed/nba/`` would silently change live predictions.

The fetcher is deliberately cache-free (unlike ``ESPNClient``, whose ``_get`` keeps every
payload) so a ~10k-request backfill stays at a flat memory footprint, and it sleeps
through the 00:20-01:20 ET window in which the host's scheduled runs have crashed on low
memory.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from datetime import time as dtime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

_logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
RESEARCH_ROOT = Path("E:/MP/data/research/nba_v10")
USER_AGENT = "Mozilla/5.0"
QUIET_START_ET = dtime(0, 20)
QUIET_END_ET = dtime(1, 20)


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_out_root(path: str | Path | None = None) -> Path:
    """The research root, or fail. Only ``E:/MP/data/research/nba_v10`` (or a child) is allowed.

    ``NBA_V10_ALLOW_ANY_ROOT=1`` lifts the guard for tests writing to a tmp dir.
    """
    root = Path(path) if path is not None else RESEARCH_ROOT
    resolved = root.resolve()
    if os.environ.get("NBA_V10_ALLOW_ANY_ROOT") == "1":
        return resolved
    allowed = RESEARCH_ROOT.resolve()
    if resolved != allowed and allowed not in resolved.parents:
        raise SystemExit(f"refusing to write outside {allowed}: {resolved}")
    return resolved


def in_quiet_window(now: datetime | None = None) -> bool:
    current = (now or datetime.now(UTC)).astimezone(ET).time()
    return QUIET_START_ET <= current < QUIET_END_ET


def wait_out_quiet_window(now_fn: Any = None) -> None:
    """Sleep while the host's 00:30/01:00 ET scheduled runs need the memory."""
    announced = False
    while in_quiet_window(now_fn() if now_fn else None):
        if not announced:
            _logger.warning("quiet window 00:20-01:20 ET: pausing")
            announced = True
        time.sleep(60)
    if announced:
        _logger.warning("quiet window over: resuming")


class Fetcher:
    """Single-worker, paced, backoff-on-429/5xx GET client with no response cache."""

    def __init__(
        self,
        interval: float = 0.4,
        *,
        max_retries: int = 6,
        backoff_base: float = 2.0,
        backoff_cap: float = 90.0,
        timeout: float = 30.0,
    ) -> None:
        self.interval = interval
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True)
        self._last = 0.0
        self.requests = 0
        self.retries = 0

    def _pace(self) -> None:
        wait_out_quiet_window()
        wait = self.interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def get(self, url: str, *, head: bool = False) -> tuple[int | None, httpx.Response | None]:
        """(status, response). status None = failed after retries; 4xx other than 429 returns at once."""
        for attempt in range(self.max_retries + 1):
            self._pace()
            self.requests += 1
            retry_after = 0.0
            try:
                response = self.client.head(url) if head else self.client.get(url)
            except httpx.HTTPError as exc:
                _logger.warning("GET error %s attempt %d: %s", url, attempt, exc)
            else:
                code = response.status_code
                if code == 429 or code >= 500:
                    try:
                        retry_after = float(response.headers.get("Retry-After") or 0)
                    except ValueError:
                        retry_after = 0.0
                    _logger.warning("HTTP %d %s attempt %d", code, url, attempt)
                else:
                    return code, response
            if attempt == self.max_retries:
                break
            self.retries += 1
            time.sleep(min(self.backoff_cap, max(retry_after, self.backoff_base * (2**attempt))))
        return None, None

    def get_json(self, url: str) -> tuple[int | None, dict[str, Any] | None]:
        status, response = self.get(url)
        if status != 200 or response is None:
            return status, None
        try:
            return 200, response.json()
        except ValueError:
            _logger.warning("bad json %s", url)
            return None, None


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return default


def write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def american_to_implied(value: Any) -> float | None:
    """A sportsbook moneyline converted to its raw implied probability (vig included).

    Source files carry American prices; the project never stores or shows them, so every
    price is converted here at ingest and the original is dropped.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        price = float(str(value).strip())
    except ValueError:
        return None
    if price >= 100:
        return round(100.0 / (price + 100.0), 6)
    if price <= -100:
        return round(-price / (-price + 100.0), 6)
    return None


def devig_pair(p_a: float | None, p_b: float | None) -> tuple[float | None, float | None]:
    if p_a is None or p_b is None or p_a + p_b <= 0:
        return None, None
    s = p_a + p_b
    return round(p_a / s, 6), round(p_b / s, 6)
