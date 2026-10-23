"""Automated Zero-Window Daemon & Scheduler Watchdog.

Provides continuous unattended background service reliability:
1. Health heartbeats tracking between short-cycle auto-buyer, daily forecast, and dashboard.
2. Memory profiling for 24/7 background processes (prevents RAM leaks).
3. Automatic periodic log maintenance and SQLite compaction triggering.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .domain import iso_utc, utc_now
from .log_maintenance import compact_sqlite_databases, prune_stale_logs
from .runtime_paths import RuntimePaths

logger = logging.getLogger("model_prediction.watchdog")

DEFAULT_MAX_SILENCE_SECONDS: dict[str, float] = {
    "auto_buyer": 1800.0,  # 30 minutes
    "dashboard": 300.0,  # 5 minutes
    "daily_forecast": 86400.0 * 2,  # 48 hours
}


@dataclass
class ComponentHeartbeat:
    """Recorded heartbeat for a background subsystem."""

    component: str
    last_seen_utc: str
    last_seen_epoch: float
    status: str = "ok"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SystemHealthReport:
    """Comprehensive health and memory audit report."""

    is_healthy: bool
    evaluated_at_utc: str
    alerts: list[str]
    components: dict[str, dict[str, Any]]
    process_memory_mb: float
    maintenance_summary: dict[str, Any]


class ServiceWatchdog:
    """Manages subsystem heartbeats, process memory, and scheduled maintenance."""

    def __init__(self, heartbeat_file: Path | str | None = None) -> None:
        if heartbeat_file is None:
            paths = RuntimePaths.resolve(require_external_runtime=False)
            heartbeat_file = paths.data_root / "logs" / "heartbeats.json"
        self.heartbeat_file = Path(heartbeat_file)
        self.heartbeats: dict[str, ComponentHeartbeat] = {}
        self.load()

    def load(self) -> None:
        """Load heartbeats from disk."""
        if not self.heartbeat_file.exists():
            return
        try:
            data = json.loads(self.heartbeat_file.read_text(encoding="utf-8"))
            for comp, item in data.get("heartbeats", {}).items():
                self.heartbeats[comp] = ComponentHeartbeat(**item)
        except (OSError, ValueError, TypeError):
            pass

    def save(self) -> None:
        """Atomically persist heartbeats to disk."""
        self.heartbeat_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.heartbeat_file.with_suffix(".tmp")
        payload = {
            "version": "heartbeat_v1",
            "updated_at_utc": iso_utc(utc_now()),
            "heartbeats": {comp: asdict(hb) for comp, hb in self.heartbeats.items()},
        }
        temp_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp_file.replace(self.heartbeat_file)

    def beat(
        self,
        component: str,
        status: str = "ok",
        metadata: dict[str, Any] | None = None,
        persist: bool = True,
    ) -> ComponentHeartbeat:
        """Record a heartbeat from an active subsystem."""
        now = time.time()
        hb = ComponentHeartbeat(
            component=component,
            last_seen_utc=iso_utc(utc_now()),
            last_seen_epoch=now,
            status=status,
            metadata=metadata or {},
        )
        self.heartbeats[component] = hb
        if persist:
            self.save()
        return hb

    def get_process_memory_mb(self) -> float:
        """Read current process memory in megabytes."""
        try:
            if sys.platform == "win32":
                import ctypes
                from ctypes import wintypes

                class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                    _fields_ = [
                        ("cb", wintypes.DWORD),
                        ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t),
                    ]

                psapi = ctypes.windll.psapi
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.GetCurrentProcess()
                counters = PROCESS_MEMORY_COUNTERS()
                counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
                if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                    return round(counters.WorkingSetSize / (1024 * 1024), 2)
            else:
                import resource

                usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                return round(usage / 1024.0, 2)
        except (AttributeError, OSError):
            pass
        return 0.0

    def evaluate_health(
        self,
        max_silence_seconds: dict[str, float] | None = None,
        run_maintenance: bool = True,
    ) -> SystemHealthReport:
        """Audit heartbeats, memory usage, and execute maintenance."""
        limits = max_silence_seconds or DEFAULT_MAX_SILENCE_SECONDS
        now = time.time()
        alerts: list[str] = []

        # 1. Check subsystem heartbeats
        for comp, max_silence in limits.items():
            hb = self.heartbeats.get(comp)
            if hb is None:
                alerts.append(f"MISSING_HEARTBEAT: Subsystem '{comp}' has never reported a heartbeat.")
            else:
                silence = now - hb.last_seen_epoch
                if silence > max_silence:
                    alerts.append(
                        f"STALE_HEARTBEAT: Subsystem '{comp}' silent for {silence:.1f}s (limit {max_silence:.1f}s)."
                    )
                if hb.status != "ok":
                    alerts.append(f"DEGRADED_STATUS: Subsystem '{comp}' reported status '{hb.status}'.")

        # 2. Check process memory
        mem_mb = self.get_process_memory_mb()
        if mem_mb > 500.0:
            alerts.append(f"HIGH_MEMORY_USAGE: Process consuming {mem_mb} MB (threshold 500 MB).")

        # 3. Optional log maintenance and compaction
        maint_summary: dict[str, Any] = {}
        if run_maintenance:
            try:
                prune_res = prune_stale_logs(max_age_days=14)
                compact_res = compact_sqlite_databases()
                maint_summary = {
                    "logs_pruned": prune_res,
                    "databases_compacted": compact_res,
                }
            except (OSError, RuntimeError) as exc:
                maint_summary = {"error": str(exc)}

        is_healthy = len(alerts) == 0
        return SystemHealthReport(
            is_healthy=is_healthy,
            evaluated_at_utc=iso_utc(utc_now()),
            alerts=alerts,
            components={comp: asdict(hb) for comp, hb in self.heartbeats.items()},
            process_memory_mb=mem_mb,
            maintenance_summary=maint_summary,
        )
