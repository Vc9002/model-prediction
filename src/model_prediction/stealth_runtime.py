"""Stealth & Background Resource Throttling Runtime.

Ensures 24/7 background prediction and market evaluation services run with
minimal system footprint, yielding 100% CPU and I/O slices to foreground user
activities (gaming, schoolwork, development) without stutter or frame drops.
"""

from __future__ import annotations

import logging
import os
import sys
import time

logger = logging.getLogger("model_prediction.stealth")

# Windows Win32 Priority Constants
_PROCESS_MODE_BACKGROUND_BEGIN = 0x00100000
_PROCESS_MODE_BACKGROUND_END = 0x00200000
_BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
_IDLE_PRIORITY_CLASS = 0x00000040


def enable_background_priority(idle_only: bool = False) -> bool:
    """Demote current process to background/idle scheduling priority.

    On Windows, uses PROCESS_MODE_BACKGROUND_BEGIN to reduce both CPU scheduling
    quantum and disk/network I/O priority so games and user applications experience
    zero lag.
    On Unix/macOS, lowers scheduling priority via os.nice(10).
    """
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            kernel32.SetPriorityClass.restype = ctypes.c_bool

            handle = kernel32.GetCurrentProcess()
            priority = _IDLE_PRIORITY_CLASS if idle_only else _PROCESS_MODE_BACKGROUND_BEGIN

            success = bool(kernel32.SetPriorityClass(handle, priority))
            if not success and not idle_only:
                # Fallback to BELOW_NORMAL if background mode fails
                success = bool(kernel32.SetPriorityClass(handle, _BELOW_NORMAL_PRIORITY_CLASS))

            if success:
                logger.info("Enabled background OS priority on Windows (stealth mode active)")
            return success
        except (OSError, RuntimeError, AttributeError, ValueError) as exc:
            logger.debug("Failed to set Windows background priority: %s", exc)
            return False

    if hasattr(os, "nice"):
        try:
            target_nice = 19 if idle_only else 10
            os.nice(target_nice)
            logger.info("Enabled background nice priority (%d) on POSIX", target_nice)
            return True
        except (OSError, RuntimeError, ValueError) as exc:
            logger.debug("Failed to set nice priority: %s", exc)
            return False

    return False


def cooperative_sleep(seconds: float, step: float = 0.5) -> None:
    """Sleep in non-blocking increments, allowing rapid shutdown and thread yielding."""
    end = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < end:
        time.sleep(min(step, max(0.01, end - time.monotonic())))
