"""Pin the once-daily combined settlement and forecast schedule."""

from __future__ import annotations

import plistlib
from pathlib import Path


def test_daily_worker_runs_once_without_run_at_load() -> None:
    plist_path = Path(__file__).parents[1] / "ops" / "launchd" / "com.modelprediction.daily.plist"
    with plist_path.open("rb") as handle:
        config = plistlib.load(handle)

    assert "RunAtLoad" not in config
    assert config["StartCalendarInterval"] == [
        {"Hour": 8, "Minute": 30},
        {"Hour": 12, "Minute": 0},
    ]
