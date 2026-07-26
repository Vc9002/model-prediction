"""Cross-process singleton guard for the complete daily writer workflow."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

LOCK_BUSY_EXIT = 75


def acquire_lock(path: str | Path) -> IO[str] | None:
    """Acquire and retain a non-blocking exclusive lock, or return ``None``."""
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(
        json.dumps(
            {
                "pid": os.getpid(),
                "acquired_at_utc": datetime.now(UTC).isoformat(),
            }
        )
        + "\n"
    )
    handle.flush()
    return handle


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="model-prediction-daily-lock")
    root.add_argument("--lock", required=True)
    root.add_argument("command", nargs=argparse.REMAINDER)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        raise ValueError("a command is required after --")
    handle = acquire_lock(args.lock)
    if handle is None:
        print(
            json.dumps(
                {
                    "status": "refused",
                    "reason": "DAILY_WRITER_ALREADY_RUNNING",
                    "lock": args.lock,
                }
            )
        )
        return LOCK_BUSY_EXIT
    # Python opens files close-on-exec by default. Make this descriptor
    # inheritable so bash and every child keep the lock until the workflow exits.
    os.set_inheritable(handle.fileno(), True)
    os.execvpe(command[0], command, os.environ)
    return 127  # pragma: no cover - os.execvpe never returns on success


if __name__ == "__main__":
    raise SystemExit(main())
