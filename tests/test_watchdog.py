"""Micro-test for ServiceWatchdog heartbeat and health audits."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from model_prediction.watchdog import ServiceWatchdog


def test_watchdog_heartbeat_lifecycle():
    with tempfile.TemporaryDirectory() as tmp_dir:
        hb_file = Path(tmp_dir) / "heartbeats.json"
        dog = ServiceWatchdog(heartbeat_file=hb_file)

        # 1. Record healthy heartbeat
        dog.beat("dashboard", status="ok", metadata={"port": 8765})
        dog.beat("auto_buyer", status="ok", metadata={"mode": "paper"})
        dog.beat("daily_forecast", status="ok")

        # 2. Check health (all fresh)
        report = dog.evaluate_health(run_maintenance=False)
        assert report.is_healthy is True
        assert len(report.alerts) == 0
        assert "dashboard" in report.components

        # 3. Simulate stale subsystem
        limits = {"dashboard": 0.01, "auto_buyer": 100.0, "daily_forecast": 100.0}
        time.sleep(0.05)
        stale_report = dog.evaluate_health(max_silence_seconds=limits, run_maintenance=False)
        assert stale_report.is_healthy is False
        assert any("STALE_HEARTBEAT" in a and "dashboard" in a for a in stale_report.alerts)
