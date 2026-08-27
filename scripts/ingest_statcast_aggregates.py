#!/usr/bin/env python3
"""Manual CLI wrapper around ``model_prediction.statcast_aggregates``.

The build itself lives in the package so the daily pipeline can import it;
this script preserves the old manual invocation (repo-local data tree).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from model_prediction.config import PROJECT_ROOT
from model_prediction.statcast_aggregates import build_statcast_game_aggregates

if __name__ == "__main__":
    pitchers, batters = build_statcast_game_aggregates(PROJECT_ROOT / "data")
    print(f"pitcher rows: {len(pitchers)}, batter rows: {len(batters)}")
