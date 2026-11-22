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

# PROCESS_INFORMATION_CLASS.ProcessMemoryPriority and MEMORY_PRIORITY_LOW.
_PROCESS_MEMORY_PRIORITY = 0
_MEMORY_PRIORITY_LOW = 2


def _set_low_memory_priority() -> bool:
    """Best-effort: mark this process's pages as low memory priority (Windows).

    Returns True only when the call succeeded; never raises.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        class _MemoryPriorityInformation(ctypes.Structure):
            _fields_ = [("MemoryPriority", ctypes.c_ulong)]

        # Private WinDLL instance so these argtypes do not leak into other callers
        # of the shared ctypes.windll.kernel32 in this process.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.SetProcessInformation.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_ulong,
        ]
        kernel32.SetProcessInformation.restype = ctypes.c_int

        info = _MemoryPriorityInformation(_MEMORY_PRIORITY_LOW)
        ok = kernel32.SetProcessInformation(
            kernel32.GetCurrentProcess(),
            _PROCESS_MEMORY_PRIORITY,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            logger.debug("SetProcessInformation(MemoryPriority) failed: error %s", ctypes.get_last_error())
            return False
        return True
    except (OSError, AttributeError, ValueError, TypeError) as exc:
        logger.debug("Failed to set low memory priority: %s", exc)
        return False


def enable_background_priority(idle_only: bool = False) -> bool:
    """Demote current process to BELOW_NORMAL (or IDLE) priority plus low memory priority.

    On Windows, sets the BELOW_NORMAL (or IDLE) priority class and, best-effort,
    MEMORY_PRIORITY_LOW so games and user applications win CPU and page contention.
    Background mode (PROCESS_MODE_BACKGROUND_BEGIN) is deliberately not used; see below.
    On Unix/macOS, lowers scheduling priority via os.nice(10).
    """
    if sys.platform == "win32":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            kernel32.SetPriorityClass.restype = ctypes.c_bool

            handle = kernel32.GetCurrentProcess()
            # Use BELOW_NORMAL_PRIORITY_CLASS rather than PROCESS_MODE_BACKGROUND_BEGIN.
            # PROCESS_MODE_BACKGROUND_BEGIN forces I/O priority to Very Low (priority 0),
            # which starves disk reads when scanning multi-MB ledger workbooks and snapshot
            # files, turning an 8s cycle into a 15-minute freeze.
            priority = _IDLE_PRIORITY_CLASS if idle_only else _BELOW_NORMAL_PRIORITY_CLASS

            success = bool(kernel32.SetPriorityClass(handle, priority))
            if not success and not idle_only:
                # Fallback to BELOW_NORMAL if background mode fails
                success = bool(kernel32.SetPriorityClass(handle, _BELOW_NORMAL_PRIORITY_CLASS))

            # Low memory priority makes the memory manager trim and repurpose this
            # process's standby pages before a foreground app's, a footprint lever
            # CPU priority does not touch. Return value ignored on purpose.
            _set_low_memory_priority()

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
