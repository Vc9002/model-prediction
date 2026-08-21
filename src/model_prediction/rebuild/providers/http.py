"""Shared HTTPX transport with bounded retry and provider rate limiting."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Self

import httpx

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _parse_retry_after(value: str | None, *, now: datetime) -> float | None:
    """Seconds to wait per a `Retry-After` header, or None if absent/unparseable.

    Retry-After is either a delay in seconds or an HTTP-date -- both are real,
    per RFC 9110 -- but a server's own header is untrusted input: bound the
    result at the call site, never sleep for whatever an incorrect or
    malicious value says.
    """
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - now).total_seconds())


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0
    jitter_seconds: float = 0.25
    # Retry-After (429/503) can request an arbitrarily long wait; this is the
    # hard ceiling regardless of what the header says, so an incorrect or
    # malicious server value can never sleep this client for hours.
    max_retry_after_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("retry attempts must be at least 1")


@dataclass(frozen=True)
class HttpFetch:
    request_time_utc: str
    retrieved_at_utc: str
    status_code: int
    headers: dict[str, str]
    body: bytes
    attempts: int
    latency_seconds: float


class HttpProviderClient:
    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        timeout: httpx.Timeout | None = None,
        retry: RetryPolicy | None = None,
        min_interval_seconds: float = 0.0,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=timeout or httpx.Timeout(20.0, connect=10.0), follow_redirects=True
        )
        self.retry = retry or RetryPolicy()
        self.min_interval_seconds = min_interval_seconds
        self._sleep = sleep
        self._monotonic = monotonic
        self._jitter = jitter
        self._last_request_at: float | None = None

    def _throttle(self) -> None:
        if self._last_request_at is None:
            return
        remaining = self.min_interval_seconds - (self._monotonic() - self._last_request_at)
        if remaining > 0:
            self._sleep(remaining)

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpFetch:
        request_time = datetime.now(UTC).isoformat()
        started = self._monotonic()
        last_error: httpx.RequestError | None = None
        for attempt in range(1, self.retry.attempts + 1):
            self._throttle()
            self._last_request_at = self._monotonic()
            retry_after_delay: float | None = None
            try:
                response = self.client.get(url, params=params, headers=headers)
            except httpx.RequestError as exc:
                last_error = exc
                if attempt == self.retry.attempts:
                    raise
            else:
                if response.status_code not in RETRYABLE_STATUS or attempt == self.retry.attempts:
                    return HttpFetch(
                        request_time_utc=request_time,
                        retrieved_at_utc=datetime.now(UTC).isoformat(),
                        status_code=response.status_code,
                        headers={key.lower(): value for key, value in response.headers.items()},
                        body=response.content,
                        attempts=attempt,
                        latency_seconds=self._monotonic() - started,
                    )
                if response.status_code in {429, 503}:
                    parsed = _parse_retry_after(response.headers.get("Retry-After"), now=datetime.now(UTC))
                    if parsed is not None:
                        retry_after_delay = min(parsed, self.retry.max_retry_after_seconds)
            if retry_after_delay is not None:
                self._sleep(retry_after_delay + self.retry.jitter_seconds * self._jitter())
            else:
                delay = min(
                    self.retry.base_delay_seconds * (2 ** (attempt - 1)), self.retry.max_delay_seconds
                )
                self._sleep(delay + self.retry.jitter_seconds * self._jitter())
        if last_error is not None:
            raise last_error
        raise RuntimeError("HTTP retry loop exited without a response")

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
