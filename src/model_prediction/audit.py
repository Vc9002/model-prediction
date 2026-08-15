from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from .domain import iso_utc, utc_now

AUDIT_SCHEMA_VERSION = "1"

# fcntl.flock(LOCK_EX) blocks indefinitely with no built-in timeout -- a hung
# holder (not crashed; POSIX auto-releases on process death) would otherwise
# wedge every other appender forever with no diagnostic.
LOCK_TIMEOUT_SECONDS = 30
LOCK_POLL_INTERVAL_SECONDS = 0.1


class AuditLockTimeout(TimeoutError):
    """Raised when the audit log lock can't be acquired within the timeout."""


def _acquire_exclusive_lock(fileno: int, path: Path, timeout: float = LOCK_TIMEOUT_SECONDS) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fileno, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise AuditLockTimeout(
                    f"could not acquire lock on {path} within {timeout}s -- "
                    "another process may be hung while holding it"
                ) from None
            time.sleep(LOCK_POLL_INTERVAL_SECONDS)


class AuditLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+") as lock:
            _acquire_exclusive_lock(lock.fileno(), self.lock_path)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def append(
        self,
        event_type: str,
        subject_id: str,
        payload: dict[str, Any],
        occurred_at: datetime | None = None,
    ) -> dict[str, Any]:
        with self._lock():
            previous_hash = "0" * 64
            if self.path.exists():
                with self.path.open("rb") as handle:
                    lines = [line for line in handle if line.strip()]
                if lines:
                    previous_hash = json.loads(lines[-1])["event_hash"]
            event = {
                "event_id": uuid.uuid4().hex,
                "event_type": event_type,
                "occurred_at_utc": iso_utc(occurred_at or utc_now()),
                "schema_version": AUDIT_SCHEMA_VERSION,
                "subject_id": subject_id,
                "payload": payload,
                "previous_hash": previous_hash,
            }
            canonical = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            event["event_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
                )
                handle.flush()
                os.fsync(handle.fileno())
            return event
