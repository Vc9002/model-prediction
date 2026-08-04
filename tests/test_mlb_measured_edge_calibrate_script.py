"""Pure-function tests for scripts/mlb_measured_edge_calibrate.py's
write_artifact -- the JSON artifact writer used to promote a new Measured
Edge margin/totals calibration.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def _load_script_module():
    spec = importlib.util.spec_from_file_location(
        "mlb_measured_edge_calibrate", PROJECT_ROOT / "scripts" / "mlb_measured_edge_calibrate.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def calibrate():
    return _load_script_module()


def _diagnostic(scale: float = 0.5, offset: float = 0.25) -> dict:
    return {
        "games": 100,
        "scale": scale,
        "offset": offset,
        "correlation": 0.2,
        "flat_110_diagnostic": {"picks": 10, "hit_rate": 0.6, "units": 1.0},
    }


def test_main_stamps_model_version_from_the_output_filename_not_a_hardcoded_literal(
    tmp_path, monkeypatch, calibrate
) -> None:
    """Real bug found 2026-08-04 (P1-17): main() used to call write_artifact
    with a hardcoded literal ("measured-edge-margin-v2"/"measured-edge-totals-v2")
    as the model_version argument regardless of the actual --output-margin/
    --output-totals filename -- write_artifact itself just writes whatever
    model_version it's given, so calling it directly with the right value
    (as an earlier, weaker version of this test did) never exercised the
    real bug, which lived one level up at the main() call site. Every future
    promotion (e.g. this session's v2->v3) would have silently written an
    artifact whose OWN model_version field still said v2 even though the
    file was named v3.json -- MeasuredEdge{Margin,Totals}Model's
    _load_artifact strictly requires model_version to equal the live
    MARGIN_MODEL_VERSION/TOTALS_MODEL_VERSION constant, so this would have
    hard-failed the moment those constants were bumped to match the new
    file. Fixed by deriving model_version from the output path's own stem
    (main() now passes Path(args.output_margin).stem). This test drives the
    real main() entry point end to end (with calibrate_market monkeypatched
    to a fixed fit so it doesn't need real matching odds/game data) against
    a deliberately unconventional --output-margin/--output-totals filename
    and asserts the written model_version tracks it.
    """
    fixed = _diagnostic()
    monkeypatch.setattr(calibrate, "calibrate_market", lambda *a, **k: dict(fixed))
    monkeypatch.setattr(calibrate, "diagnostic_window_rows", lambda *a, **k: [])
    monkeypatch.setattr(calibrate, "real_market_window_rows", lambda *a, **k: [])
    margin_out = tmp_path / "measured-edge-margin-v99.json"
    totals_out = tmp_path / "measured-edge-totals-v99.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mlb_measured_edge_calibrate.py",
            "--formula", str(PROJECT_ROOT / "config/models/mlb-analyst-poisson-trend-v0.3.yaml"),
            "--feature-cache", str(tmp_path / "no_such_cache.jsonl"),
            "--odds-end", "2026-08-04",
            "--output-margin", str(margin_out),
            "--output-totals", str(totals_out),
        ],
    )
    calibrate.main()
    margin_written = json.loads(margin_out.read_text(encoding="utf-8"))
    totals_written = json.loads(totals_out.read_text(encoding="utf-8"))
    assert margin_written["model_version"] == "measured-edge-margin-v99"
    assert margin_written["calibration_version"] == "measured-edge-margin-v99"
    assert totals_written["model_version"] == "measured-edge-totals-v99"
    assert totals_written["calibration_version"] == "measured-edge-totals-v99"


def test_write_artifact_hash_is_self_consistent(tmp_path, calibrate):
    output_path = tmp_path / "measured-edge-totals-v99.json"
    calibrate.write_artifact(
        str(output_path),
        "Measured Edge Totals",
        "measured-edge-totals-v99",
        "mlb-analyst-poisson-trend-v0.3",
        _diagnostic(),
        {"games": 0},
    )
    written = json.loads(output_path.read_text(encoding="utf-8"))
    canonical = {key: value for key, value in written.items() if key != "artifact_hash"}
    import hashlib

    expected_hash = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert written["artifact_hash"] == expected_hash
