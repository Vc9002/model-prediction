"""Settlement CLV finds the closing quote for Clippers road spreads (NBA review B3, settle side).

The snapshot row mirrors the live 2026-11-19 LAC @ SA capture: Polymarket anchors the rung on
"Los Angeles Clippers" while the ledger row carries ESPN's "LA Clippers".
"""

from __future__ import annotations

import json

from model_prediction.cli import settle as settle_module

SLUG = "asc-nba-lac-sa-2026-11-19-pos-2pt5"
START = "2026-11-20T01:00:00Z"


def _write_snapshot(root, sport: str) -> None:
    folder = root / "odds" / sport / "2026-11-19"
    folder.mkdir(parents=True)
    row = {
        "market_slug": SLUG,
        "market_type": "spread",
        "line": 2.5,
        "team": "Los Angeles Clippers",
        "observed_at_utc": "2026-11-20T00:55:00Z",
        "long": {"description": "+2.50", "ask": 0.47},
        "short": {"description": "-2.50", "ask": 0.55},
    }
    (folder / "polymarket_snapshots.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")


def _close(root, sport: str, selection: str):
    return settle_module._closing_probability_for_moneyline_pick(
        root, sport, SLUG, START, "San Antonio Spurs", "LA Clippers", selection
    )


def test_clippers_road_spread_gets_its_closing_quote(monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(tmp_path / "runtime"))
    _write_snapshot(tmp_path, "nba")
    assert _close(tmp_path, "nba", "away")[0] == 0.47
    assert _close(tmp_path, "nba", "home")[0] == 0.55


def test_alias_is_nba_scoped_and_the_shared_matcher_stays_strict(monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(tmp_path / "runtime"))
    _write_snapshot(tmp_path, "wnba")
    assert _close(tmp_path, "wnba", "away") == (None, None)
