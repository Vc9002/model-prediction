"""cfb-structural-v6.1 research challenger: bit-exact tracker parity, point-in-time buffer, inertness.

v6.1 is frozen v6 plus the walk-forward level tracker (quick-261115-183). The parity fixture was produced by
``scripts/cfb_totals_lib.py::tracker_offsets``; the src port must reproduce its offsets and totals exactly.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import model_prediction
from model_prediction.config import PROJECT_ROOT
from model_prediction.models.cfb_structural_v6 import (
    MIN_CALIBRATED_TOTAL,
    cfb_structural_v6_model,
    load_cfb_structural_v6_artifact,
)
from model_prediction.models.cfb_structural_v61 import (
    CFB_V61_MODEL_VERSION,
    CFBStructuralV61Model,
    ResidualFeed,
    cfb_structural_v61_model,
    level_offset,
    load_cfb_structural_v61_artifact,
)
from model_prediction.production_registry import compute_artifact_hash

FIXTURE = PROJECT_ROOT / "outputs" / "ncaaf" / "cfb_v61_parity_fixture.json"
SPEC = PROJECT_ROOT / "outputs" / "ncaaf" / "cfb_v61_spec.json"
FROZEN_FIT = PROJECT_ROOT / "outputs" / "ncaaf" / "cfb_v6_frozen_fit.json"
V6_PARAMS_SHA = "6e38b14ca486acfd836c1259e5c4fd4fb8e9b5894a06f367322b1eb0c7b13e70"
DAY = 86400
TRACKER = {
    "window_kind": "days",
    "window_size": 90,
    "result_buffer_hours": 5.0,
    "day_window_min_games": 50,
    "day_window_fallback_games": 150,
}
T0 = int(datetime(2026, 11, 14, 17, 0, tzinfo=UTC).timestamp())


def _canon_sha(obj: object) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ----------------------------------------------------------------------------- parity against the research rows
def test_parity_fixture_is_bit_exact() -> None:
    fixture = _json(FIXTURE)
    hist = fixture["history"]
    feed = ResidualFeed(hist["t_s"], np.asarray(hist["actual"], float) - np.asarray(hist["v6_total"], float))
    model = cfb_structural_v61_model()
    assert len(fixture["rows"]) == 200
    worst = 0.0
    for row in fixture["rows"]:
        offset, n_used = feed.offset_at(row["t_s"], model.tracker)
        assert n_used == row["n_used"]
        assert offset == row["offset_cumsum"], row["event_id"]  # exact, not approx
        worst = max(worst, abs(offset - row["offset_cumsum"]))
        # the served total is the unfloored research total, floored exactly as v6 floors it
        model.set_offset(offset)
        total = model.calibrate_means(0.0, row["raw_total"])[1]
        assert total == max(MIN_CALIBRATED_TOTAL, row["v6_1_total"]), row["event_id"]
    assert worst == 0.0


def test_artifact_equals_the_committed_spec_and_the_fixture_header() -> None:
    spec = _json(SPEC)
    assert _canon_sha({k: v for k, v in spec.items() if k != "spec_sha256"}) == spec["spec_sha256"]
    art = load_cfb_structural_v61_artifact()
    tr = spec["tracker"]
    assert art["spec_sha256"] == spec["spec_sha256"]
    assert art["rule_sha256"] == spec["rule_sha256"]
    assert art["v6_frozen_params_sha256"] == spec["v6_frozen_params_sha256"] == V6_PARAMS_SHA
    assert art["tracker"] == {
        "window_kind": tr["window"]["kind"],
        "window_size": tr["window"]["size"],
        "result_buffer_hours": tr["result_buffer_hours"],
        "day_window_min_games": tr["day_window_min_games"],
        "day_window_fallback_games": tr["day_window_fallback_games"],
    }
    assert art["components"] == [] and spec["components"] == []
    fixture = _json(FIXTURE)
    assert fixture["spec_sha256"] == spec["spec_sha256"]
    assert fixture["tracker_window"] == tr["window"]
    assert fixture["result_buffer_seconds"] == int(art["tracker"]["result_buffer_hours"] * 3600)


def test_artifact_hash_and_base_hash_are_verified(tmp_path: Path) -> None:
    art = load_cfb_structural_v61_artifact()
    bad = dict(art, tracker=dict(art["tracker"], window_size=89))
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="artifact_hash mismatch"):
        load_cfb_structural_v61_artifact(path)
    wrong_base = dict(art, base_artifact_hash="0" * 64)
    wrong_base["artifact_hash"] = compute_artifact_hash(wrong_base)
    with pytest.raises(ValueError, match="different frozen v6 artifact"):
        CFBStructuralV61Model.from_artifact(wrong_base)
    with_component = dict(art, components=[{"source": "x"}])
    with_component["artifact_hash"] = compute_artifact_hash(with_component)
    with pytest.raises(ValueError, match="components"):
        CFBStructuralV61Model.from_artifact(with_component)


def test_frozen_v6_params_sha_is_unchanged() -> None:
    frozen = _json(FROZEN_FIT)
    blob = {k: frozen[k] for k in sorted(frozen) if k not in ("params_sha256", "generated_at_utc")}
    assert frozen["params_sha256"] == _canon_sha(blob) == V6_PARAMS_SHA
    v6 = load_cfb_structural_v6_artifact()
    v61 = cfb_structural_v61_model()
    assert v61.total_calibration == v6["total_calibration"]
    assert v61.margin_calibration == v6["margin_calibration"]
    assert v61.version == CFB_V61_MODEL_VERSION != cfb_structural_v6_model().version


# ----------------------------------------------------------------------------- point-in-time buffer, window, fallback
def test_result_buffer_is_inclusive_and_flips_exactly_at_start_plus_five_hours() -> None:
    start = T0
    feed = ResidualFeed([start], [7.0])
    clock = start + 5 * 3600 - 3  # an advancing clock, one decision per second across the boundary
    seen = []
    for _ in range(6):
        seen.append(feed.offset_at(clock, TRACKER))
        clock += 1
    # the game feeds the tracker only once start + 5 h <= decision time (v6's inclusive D-RES-BUFFER)
    assert seen == [(0.0, 0)] * 3 + [(7.0, 1)] * 3
    assert level_offset([start], [7.0], start + 5 * 3600, TRACKER) == (7.0, 1)
    assert level_offset([start], [7.0], start + 5 * 3600 - 1, TRACKER) == (0.0, 0)


def test_a_game_never_feeds_its_own_or_a_later_decision() -> None:
    later = T0 + 3600
    feed = ResidualFeed([T0, later], [4.0, 100.0])
    assert feed.offset_at(T0, TRACKER) == (0.0, 0)  # the same-kickoff game is not yet buffered
    assert feed.offset_at(later + 5 * 3600 - 1, TRACKER) == (4.0, 1)  # the later game is still in its buffer


def _feed_with_recent(m_recent: int) -> tuple[ResidualFeed, int, float, float]:
    """300 old games (residual 10) then ``m_recent`` games inside the 90 day window (residual 1)."""
    t = T0
    old = [t - 200 * DAY + i * 3600 for i in range(300)]
    recent = [t - 60 * DAY + i * 3600 for i in range(m_recent)]
    starts = old + recent
    resid = [10.0] * len(old) + [1.0] * len(recent)
    return ResidualFeed(starts, resid), t, 10.0, 1.0


def test_day_window_falls_back_to_the_last_150_games_below_the_minimum() -> None:
    feed, t, old_r, new_r = _feed_with_recent(49)
    offset, n = feed.offset_at(t, TRACKER)
    assert n == 150
    assert offset == pytest.approx((49 * new_r + 101 * old_r) / 150, abs=1e-12)
    feed50, t, _, _ = _feed_with_recent(50)
    offset50, n50 = feed50.offset_at(t, TRACKER)
    assert n50 == 50 and offset50 == pytest.approx(new_r, abs=1e-12)  # exactly 50 qualifies: no fallback


def test_day_window_length_is_exactly_the_artifact_days() -> None:
    t = T0
    boundary = t - 90 * DAY  # start >= T - 90 days counts
    starts = [boundary - 1] + [boundary + i for i in range(60)]
    resid = [1000.0] + [2.0] * 60
    feed = ResidualFeed(starts, resid)
    offset, n = feed.offset_at(t, TRACKER)
    assert n == 60 and offset == pytest.approx(2.0, abs=1e-12)  # boundary game in, one second older out


def test_games_window_takes_the_last_n_eligible_games() -> None:
    starts = [T0 - (50 - i) * DAY for i in range(50)]
    resid = list(range(50))
    cfg = dict(TRACKER, window_kind="games", window_size=10)
    offset, n = ResidualFeed(starts, resid).offset_at(T0, cfg)
    assert n == 10 and offset == pytest.approx(np.mean(resid[-10:]), abs=1e-12)
    assert ResidualFeed([], []).offset_at(T0, cfg) == (0.0, 0)


def test_unsorted_feed_and_naive_decision_time_are_refused() -> None:
    with pytest.raises(ValueError, match="sorted by kickoff"):
        ResidualFeed([5, 3], [1.0, 1.0])
    model = cfb_structural_v61_model()
    with pytest.raises(ValueError, match="timezone-aware"):
        model.set_decision(ResidualFeed([1], [1.0]), datetime(2026, 11, 14, 17, 0))  # noqa: DTZ001
    offset, n = model.set_decision(ResidualFeed([T0 - DAY], [3.0]), datetime.fromtimestamp(T0, UTC))
    assert (offset, n) == (3.0, 1) and model.offset == 3.0


# ----------------------------------------------------------------------------- seam and floor
def _features(i: int) -> SimpleNamespace:
    return SimpleNamespace(
        projected_margin_home=-14.0 + 0.7 * i,
        projected_total=38.0 + 0.9 * i,
        is_fbs_vs_fcs=i % 7 == 0,
        sample_games=i % 5,
    )


def test_offset_zero_v61_equals_a_fresh_v6_on_every_row_in_order() -> None:
    v6, v61 = cfb_structural_v6_model(), cfb_structural_v61_model()
    assert v61.offset == 0.0
    for i in range(40):
        feat = _features(i)
        line = 40.0 + i % 11
        a = v6.forecast_game(feat, spread_home_line=-3.5, total_line=line)
        b = v61.forecast_game(feat, spread_home_line=-3.5, total_line=line)
        assert (a.prob_over, a.prob_under, a.prob_push_total) == (
            b.prob_over,
            b.prob_under,
            b.prob_push_total,
        )
        assert (a.prob_home_cover, a.prob_home_win) == (b.prob_home_cover, b.prob_home_win)
        assert (a.projected_total, a.projected_margin_home) == (b.projected_total, b.projected_margin_home)


def test_a_negative_offset_lowers_the_total_and_only_the_total() -> None:
    v6, v61 = cfb_structural_v6_model(), cfb_structural_v61_model()
    v61.set_offset(-2.0)
    feat = _features(10)
    a = v6.forecast_game(feat, spread_home_line=-3.5, total_line=52.5)
    b = v61.forecast_game(feat, spread_home_line=-3.5, total_line=52.5)
    assert b.prob_over < a.prob_over
    assert b.projected_total == pytest.approx(a.projected_total - 2.0, abs=0.011)
    assert b.projected_margin_home == pytest.approx(a.projected_margin_home, abs=1e-9)
    assert v61.calibrate_means(3.0, 1.0)[1] == MIN_CALIBRATED_TOTAL  # the v6 floor binds identically


# ----------------------------------------------------------------------------- inertness
_PROBE = """
import importlib, json, sys
for name in sys.argv[1].split(","):
    importlib.import_module(name)
print(json.dumps({"model_prediction.models.cfb_structural_v61": "model_prediction.models.cfb_structural_v61" in sys.modules}))
"""


def _probe(modules: str) -> bool:
    env = {k: v for k, v in os.environ.items() if not k.startswith("MODEL_PREDICTION_")}
    env["MODEL_PREDICTION_SKIP_DOTENV"] = "1"
    env["PYTHONPATH"] = str(Path(model_prediction.__file__).resolve().parent.parent)
    out = subprocess.run(
        [sys.executable, "-c", _PROBE, modules],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    return bool(json.loads(out.stdout.strip().splitlines()[-1])["model_prediction.models.cfb_structural_v61"])


def test_serving_modules_never_load_v61_in_a_clean_subprocess() -> None:
    serving = (
        "model_prediction.learned_forward,model_prediction.models.college_football,model_prediction.cli.daily"
    )
    assert _probe(serving) is False
    assert _probe("model_prediction.models.cfb_structural_v61") is True  # negative control


def test_nothing_routes_to_v61() -> None:
    src = PROJECT_ROOT / "src" / "model_prediction"
    own = src / "models" / "cfb_structural_v61.py"
    offenders = [
        str(p.relative_to(PROJECT_ROOT))
        for p in src.rglob("*.py")
        if p != own
        and any(n in p.read_text(encoding="utf-8") for n in ("cfb_structural_v61", "cfb-structural-v6.1"))
    ]
    assert offenders == []
    for rel in ("src/model_prediction/learned_forward.py", "config/production.yaml", "config/model.yaml"):
        text = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
        assert "cfb-structural-v6.1" not in text and "cfb_structural_v61" not in text, rel
