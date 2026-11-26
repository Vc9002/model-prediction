"""Settlement CLV for a spread resolves the row's side with the shared alias table.

The close path matched the snapshot's team to the row's teams with learned_forward._team_matches,
which has no alias table, so a Polymarket "UCA" close stayed blank for Central Arkansas. It also took
the home team whenever both teams matched, so a market on "Louisiana Monroe" beside a home team
"Louisiana" was read as the home side: a close on the wrong team. Accounting only (CLV), but it is
the number the buyer cohorts are judged on.
"""

from __future__ import annotations

import json

import pytest

from model_prediction.cli import settle as settle_module

SLUG = "asc-cbb-cark-jax-2026-11-21-neg-3pt5"
START = "2026-11-21T21:00:00Z"


def _write_snapshot(root, team: str, line: float = -3.5) -> None:
    folder = root / "odds" / "ncaab" / "2026-11-21"
    folder.mkdir(parents=True)
    row = {
        "market_slug": SLUG,
        "market_type": "spread",
        "line": line,
        "team": team,
        "observed_at_utc": "2026-11-21T20:55:00Z",
        "long": {"description": f"{line:+.2f}", "ask": 0.50},
        "short": {"description": f"{-line:+.2f}", "ask": 0.51},
    }
    (folder / "polymarket_snapshots.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")


def _close(root, home: str, away: str, selection: str, line: float):
    return settle_module._closing_probability_for_moneyline_pick(
        root, "ncaab", SLUG, START, home, away, selection, line=line
    )


@pytest.mark.parametrize(("selection", "line", "ask"), [("away", -3.5, 0.50), ("home", 3.5, 0.51)])
def test_an_abbreviated_polymarket_team_still_gets_its_close(
    monkeypatch, tmp_path, selection, line, ask
) -> None:
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(tmp_path / "runtime"))
    _write_snapshot(tmp_path, "UCA")

    got = _close(tmp_path, "Jacksonville Dolphins", "Central Arkansas Bears", selection, line)

    assert got[0] == ask


def test_a_market_team_matching_both_row_teams_gets_no_close(monkeypatch, tmp_path) -> None:
    """The name "Louisiana" is a whole-word prefix of "Louisiana Monroe", so the market on Louisiana Monroe
    matched the home team first and the away pick was closed on the short side."""
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(tmp_path / "runtime"))
    _write_snapshot(tmp_path, "Louisiana Monroe")

    assert _close(tmp_path, "Louisiana", "Louisiana Monroe", "away", -3.5) == (None, None)
