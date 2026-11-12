from __future__ import annotations

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

from model_prediction.filelock import lock_exclusive, unlock

from .domain import iso_utc, utc_now

AUDIT_SCHEMA_VERSION = "1"

# fcntl.flock(LOCK_EX) blocks indefinitely with no built-in timeout -- a hung
# holder (not crashed; POSIX auto-releases on process death) would otherwise
# wedge every other appender forever with no diagnostic.
LOCK_TIMEOUT_SECONDS = 120  # bumped from 30: parallel ingest jobs (7 sports) can queue up
LOCK_POLL_INTERVAL_SECONDS = 0.1


class AuditLockTimeout(TimeoutError):
    """Raised when the audit log lock can't be acquired within the timeout."""


def _acquire_exclusive_lock(fileno: int, path: Path, timeout: float = LOCK_TIMEOUT_SECONDS) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            lock_exclusive(fileno, blocking=False)
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
                unlock(lock.fileno())

    def events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def reversed_events(self) -> Iterator[dict[str, Any]]:
        """Yield parsed events newest-first via a bounded backward-chunked read.

        Unlike events(), never materializes or parses the whole file before
        the caller sees the first item -- callers that only need the most
        recent N events (a dashboard tail) or the first match scanning
        backward (a real-money double-buy guard) should use this instead of
        reversed(events()), which pays to parse the entire log regardless of
        how little of it the caller actually needs. Independent of append()'s
        own backward-chunk tail-read for the hash-chain previous-hash lookup
        -- that logic is untouched by this method.
        """
        if not self.path.exists():
            return
        with self.path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            pos = handle.tell()
            chunk_size = 65536
            buffer = b""
            while pos > 0:
                read_size = min(chunk_size, pos)
                pos -= read_size
                handle.seek(pos)
                buffer = handle.read(read_size) + buffer
                segments = buffer.split(b"\n")
                if pos == 0:
                    batch = segments
                elif len(segments) > 1:
                    buffer = segments[0]
                    batch = segments[1:]
                else:
                    continue
                for segment in reversed(batch):
                    stripped = segment.strip()
                    if not stripped:
                        continue
                    try:
                        yield json.loads(stripped)
                    except json.JSONDecodeError:
                        continue

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
                # Tail-read only: events.jsonl can be hundreds of MB; reading the
                # whole file under the lock blocks parallel ingest jobs past the
                # timeout. Read backwards in chunks to robustly find the complete
                # last line, accommodating events of arbitrary size (>8 KB).
                with self.path.open("rb") as handle:
                    handle.seek(0, os.SEEK_END)
                    size = handle.tell()
                    if size > 0:
                        chunk_size = 65536
                        pos = size
                        buffer = b""
                        last_line = None
                        while pos > 0:
                            read_size = min(chunk_size, pos)
                            pos -= read_size
                            handle.seek(pos)
                            buffer = handle.read(read_size) + buffer
                            lines = [line for line in buffer.splitlines() if line.strip()]
                            if pos == 0 or len(lines) > 1:
                                if lines:
                                    last_line = lines[-1]
                                break
                        if last_line:
                            previous_hash = json.loads(last_line.decode("utf-8"))["event_hash"]
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
