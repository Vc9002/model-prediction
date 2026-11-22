"""Addendum-v5 confirmation tool for P1_player_availability (quick 261121-hmc, lane P). No read happens here:
every outcome is synthetic and the read log is never touched."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[2]
for _p in (
    REPO / "scripts" / "ncaab_research",
    REPO / "scripts" / "ncaab_research" / "candidates",
    REPO / "scripts" / "ncaab_research" / "confirmation",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import confirm_p1_261122 as cp
import grader


def test_addendum_v5_pin_and_seasons():
    add = cp.load_addendum_v5()
    assert grader.canonical_sha256(add) == cp.ADDENDUM_V5_SHA256
    assert tuple(add["f_confirmation_seasons"]["season_years"]) == (2018, 2019, 2020, 2022)


def test_tampered_addendum_v5_refused(tmp_path):
    add = json.loads(cp.ADDENDUM_V5_PATH.read_text(encoding="utf-8"))
    add["c_season_usability"] += " (edited)"
    p = tmp_path / "v5.json"
    p.write_text(json.dumps(add), encoding="utf-8")
    with pytest.raises(grader.ProtocolMismatchError):
        cp.load_addendum_v5(p)


def test_input_hash_mismatch_refused_and_lf_normalised(tmp_path):
    (tmp_path / "s.py").write_bytes(b"x = 1\r\ny = 2\r\n")
    (tmp_path / "d.jsonl").write_bytes(b'{"a": 1}\n')
    lf = {"s.py": cp.sha_bytes(b"x = 1\ny = 2\n")}  # CRLF checkout matches its LF pin
    raw = {"d.jsonl": cp.sha_bytes(b'{"a": 1}\n')}
    assert set(cp.verify_inputs(lf, raw, root=tmp_path)) == {"s.py", "d.jsonl"}
    (tmp_path / "d.jsonl").write_bytes(b'{"a": 2}\n')  # a rebuilt table
    with pytest.raises(grader.ProtocolMismatchError, match="d.jsonl"):
        cp.verify_inputs(lf, raw, root=tmp_path)
    with pytest.raises(grader.ProtocolMismatchError, match="missing"):
        cp.verify_inputs({"gone.py": "0" * 64}, {}, root=tmp_path)


def test_reread_refused():
    other = json.dumps({"grid_name": cp.GRID, "window": "development", "predictions_sha256": "a"})
    assert not cp.confirmation_already_read([other, ""])
    logged = json.dumps({"grid_name": cp.GRID, "window": cp.WINDOW, "predictions_sha256": "b"})
    assert cp.confirmation_already_read([other, logged])
    assert not cp.confirmation_already_read([logged], grid="P2_returning_production_early")


def test_null_inputs_give_v7_exactly_and_coef_applied():
    mu = np.array([1.1, -2.3, 4.7, 0.3])
    x, valid = cp.p1_design([0.9, np.nan, 1.0, 0.8], [0.7, 0.9, 1.0, 0.9], [0.1, 0.0, np.nan, 0.0], [0.0] * 4)
    out = cp.apply_p1(mu, x, valid, [5.0, -2.0])
    assert valid.tolist() == [True, False, False, True]
    np.testing.assert_array_equal(out[~valid], mu[~valid])
    np.testing.assert_allclose(out[valid], mu[valid] + np.array([5 * 0.2 - 2 * 0.1, 5 * -0.1]))


def test_verdict_rule():
    assert cp.verdict({"lower_one_sided_95": 0.001}, 4) == "CONFIRMED"
    assert cp.verdict({"lower_one_sided_95": -0.001}, 4) == "NOT_CONFIRMED"
    assert cp.verdict({"lower_one_sided_95": 0.5}, 2) == "CONFIRMATION_UNAVAILABLE"
