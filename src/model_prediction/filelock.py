"""Cross-platform exclusive file locks.

POSIX uses ``fcntl.flock``; Windows uses ``msvcrt.locking`` on a single byte
parked at a high offset. The offset keeps the locked byte out of the file's
real content, so other processes can still read the file (e.g. the PID or
JSON written into a lock file) while the lock is held.
"""

from __future__ import annotations

import os
import sys
import time
from typing import IO

_LOCK_OFFSET = 0x7FFFFFFF
_BLOCKING_POLL_SECONDS = 0.1

if sys.platform == "win32":
    import msvcrt

    def _fd(target: IO[str] | int) -> int:
        return target if isinstance(target, int) else target.fileno()

    def _try_lock(fd: int) -> None:
        saved = os.lseek(fd, 0, os.SEEK_CUR)
        os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            # Normalise to BlockingIOError, matching fcntl.flock's failure.
            raise BlockingIOError(str(exc)) from exc
        finally:
            os.lseek(fd, saved, os.SEEK_SET)

    def _unlock(fd: int) -> None:
        saved = os.lseek(fd, 0, os.SEEK_CUR)
        os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.lseek(fd, saved, os.SEEK_SET)

else:
    import fcntl

    def _fd(target: IO[str] | int) -> int:
        return target if isinstance(target, int) else target.fileno()

    def _try_lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def lock_exclusive(target: IO[str] | int, *, blocking: bool = True) -> None:
    """Take an exclusive lock on ``target``.

    With ``blocking=False`` a held lock raises ``BlockingIOError`` immediately.
    With ``blocking=True`` the call waits until the lock is free.
    """
    fd = _fd(target)
    if not blocking:
        _try_lock(fd)
        return
    while True:
        try:
            _try_lock(fd)
            return
        except BlockingIOError:
            time.sleep(_BLOCKING_POLL_SECONDS)


def unlock(target: IO[str] | int) -> None:
    """Release a lock previously taken with :func:`lock_exclusive`."""
    _unlock(_fd(target))
