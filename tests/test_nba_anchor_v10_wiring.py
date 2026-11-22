"""nba-anchor-v10 wired into the NBA slates as a Flat-only challenger (quick-261121-nw1).

Fixtures reuse the live-shaped LAC @ SA quotes of tests/test_nba_serving_plumbing.py; the ESPN
consensus record mirrors the real 2026-11-21 DraftKings capture shape (home_line negative when
home is favoured).
"""

from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from model_prediction.cli import forecast as forecast_cli
from model_prediction.cli import forecast_nba_anchor_v10 as v10
from model_prediction.data_sources.nba_pretip_capture import espn_odds_path
from model_prediction.ledger import DuplicatePickError
from model_prediction.models.nba_anchor_v10 import AnchorQuote, CapturedAnchorSource
from tests.test_nba_serving_plumbing import (
    CLIPPERS,
    NOW,
    NUGGETS,
    SPURS,
    TIP_ESPN,
    _event,
    _patch_slate,
    _spread_row,
    _total_row,
)

SIGMA_SPREAD = 13.792
CONFIG = {
    "models": {"NBA": {"min_edge": 0.05}},
    "project": {"maximum_unreviewed_market_disagreement": 0.10},
}


class FakeLedger:
    def __init__(self, name: str, log: list) -> None:
        self.name, self.log, self.keys = name, log, set()

    def exposure(self, *args, **kwargs):
        return object()

    def rows(self, **kwargs):
        return []

    def append_evaluated(self, request, eligibility, now=None):
        key = (request.event_id, request.market_type, request.line, request.model_version)
        if key in self.keys:
            raise DuplicatePickError("dup")
        self.keys.add(key)
        self.log.append((self.name, request.model_version, request))
        return {"pick_id": "x"}


def _registry():
    return SimpleNamespace(resolve=lambda *args: SimpleNamespace(canonical_team_id="t"), version="1")


def _patch_logging(monkeypatch, log: list) -> None:
    eligibility = SimpleNamespace(decision="CALL", qualified=False)
    monkeypatch.setattr(forecast_cli, "unit_policy", lambda config: object())
    monkeypatch.setattr(v10, "unit_policy", lambda config: object())
    monkeypatch.setattr(forecast_cli, "evaluate_eligibility", lambda *args, **kwargs: eligibility)
    monkeypatch.setattr(v10, "evaluate_eligibility", lambda *args, **kwargs: eligibility)
    monkeypatch.setattr(forecast_cli, "_is_registered_serving_model", lambda *args: True)
    monkeypatch.setattr(forecast_cli, "_downgrade_unserved", lambda e, *args: e)
    monkeypatch.setattr(forecast_cli.PickRequest, "validate", lambda self, now: None)
    # v9's Flat/Main writes go through _append_secondary_ledger: record their order.
    monkeypatch.setattr(
        forecast_cli,
        "_append_secondary_ledger",
        lambda ledger, request, eligibility, now, source: (
            ledger.append_evaluated(request, eligibility, now) and None
        ),
    )


def _write_consensus(tmp_path, *, home_line: float, away_fair: float, observed, event_id: str = "401"):
    path = espn_odds_path(tmp_path, "2026-11-19")
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "event_id": event_id,
        "observed_at_utc": observed.isoformat(),
        "providers": [{"provider_name": "DraftKings", "is_live": False}],
        "consensus": {
            "spread": {"home_line": home_line, "home": 1 - away_fair, "away": away_fair, "n_books": 1},
            "total": {"line": 197.5, "over": 0.5, "under": 0.5, "n_books": 1},
        },
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")


def _run_sport(monkeypatch, tmp_path, market: str, rows: list[dict]) -> tuple[list, dict]:
    events = [_event("401", CLIPPERS, SPURS), _event("499", NUGGETS, "Los Angeles Lakers")]
    _patch_slate(monkeypatch, market=market, rows=rows, events=events)
    log: list = []
    _patch_logging(monkeypatch, log)
    result = getattr(forecast_cli, f"_forecast_nba_{market}_sport")(
        data_root=tmp_path,
        args_date="2026-11-19",
        config=CONFIG,
        registry=_registry(),
        bans=None,
        flat_ledger=FakeLedger("flat", log),
        main_ledger=FakeLedger("main", log),
    )
    return log, result


@pytest.mark.parametrize(
    ("market", "rows"),
    [("spread", [_spread_row("Los Angeles Clippers")]), ("total", [_total_row()])],
)
def test_v10_logs_one_flat_row_per_game_after_v9_and_never_main(monkeypatch, tmp_path, market, rows):
    log, result = _run_sport(monkeypatch, tmp_path, market, rows)
    versions = [(ledger, version) for ledger, version, _ in log]
    v9 = f"nba-{market}-margin-v9"
    # v9 rows first and untouched; then exactly one v10 row, on Flat only.
    assert versions[-1] == ("flat", "nba-anchor-v10")
    assert all(version == v9 for _, version in versions[:-1]) and ("flat", v9) in versions
    assert ("main", "nba-anchor-v10") not in versions
    request = log[-1][2]
    snapshot = json.loads(request.model_input_snapshot_json)
    # No consensus file: the Polymarket mid of the priced contract anchors, so P = mid exactly.
    assert snapshot["anchor_source"] == "polymarket_mid"
    assert snapshot["lambda"] == 0.0 and snapshot["main_eligible"] is False
    assert snapshot["tb"] == {"evaluated": False, "flag": False, "threshold": 0.03}
    assert snapshot["net_edge"] < 0  # a mid never clears its own ask plus fee
    assert result["anchor_v10"]["logged"] == 1 and result["anchor_v10"]["anchor_sources"] == {
        "polymarket_mid": 1
    }
    assert "anchor_v10_inputs" not in result
    if market == "total":
        assert snapshot["model_source"] == "nba-total-margin-v9-1" and snapshot["v9_mean"] is not None


def test_v10_failure_is_contained_and_v9_rows_are_unchanged(monkeypatch, tmp_path):
    rows = [_spread_row("Los Angeles Clippers")]
    baseline, _ = _run_sport(monkeypatch, tmp_path, "spread", rows)

    def boom(**kwargs):
        raise RuntimeError("v10 exploded")

    monkeypatch.setattr(forecast_cli, "log_nba_anchor_v10", boom)
    log, result = _run_sport(monkeypatch, tmp_path, "spread", rows)
    assert result["anchor_v10"]["status"] == "error"
    v9_before = [
        (l, r.selection, r.line, r.model_probability) for l, v, r in baseline if v != "nba-anchor-v10"
    ]
    v9_after = [(l, r.selection, r.line, r.model_probability) for l, v, r in log]
    assert v9_after == v9_before and v9_before


def _inputs(*, asks: dict, mids: dict, observed) -> dict:
    return {
        "anchor_v10_inputs": [
            {
                "event_id": "401",
                "event_start_utc": TIP_ESPN,
                "away_team": CLIPPERS,
                "home_team": SPURS,
                "market_type": "spread",
                "contract_line": 11.5,
                "asks": asks,
                "mids": mids,
                "quote_observed_at_utc": observed.isoformat(),
                "market_slug": "asc-test",
                "market_lineage": None,
                "model_mean": 9.0,
                "model_source": "nba-spread-margin-v9",
            }
        ]
    }


def _log_direct(monkeypatch, tmp_path, forecast: dict, clock) -> tuple[dict, list]:
    log: list = []
    _patch_logging(monkeypatch, log)
    monkeypatch.setattr(forecast_cli, "utc_now", clock)
    summary = v10.log_nba_anchor_v10(
        forecast=forecast,
        market="spread",
        data_root=tmp_path,
        args_date="2026-11-19",
        config=CONFIG,
        registry=_registry(),
        bans=None,
        flat_ledger=FakeLedger("flat", log),
    )
    return summary, log


def test_consensus_anchor_sign_and_tb_flag(monkeypatch, tmp_path):
    """ESPN home_line -11.5 (home favoured) is the home-margin anchor +11.5, not -11.5."""
    _write_consensus(tmp_path, home_line=-11.5, away_fair=0.489166, observed=NOW - timedelta(minutes=20))
    forecast = _inputs(
        asks={"away": 0.55, "home": 0.47},
        mids={"away": 0.54, "home": 0.46},
        observed=NOW - timedelta(minutes=5),
    )
    summary, log = _log_direct(monkeypatch, tmp_path, forecast, lambda: NOW)
    snapshot = json.loads(log[0][2].model_input_snapshot_json)
    assert snapshot["anchor_source"] == "espn_consensus_devig"
    assert snapshot["anchor_quoted_line"] == 11.5
    # Contract on the consensus line: the anchored price is the devigged consensus itself.
    assert snapshot["side"] == "home" and snapshot["probability"] == pytest.approx(0.510834, abs=1e-6)
    # T-B: 51.08% consensus vs a 47c home ask is a 4.08-point gap, above the pinned 3.0.
    assert snapshot["tb"]["flag"] is True and snapshot["tb"]["side"] == "home"
    assert snapshot["tb"]["gap"] == pytest.approx(0.040834, abs=1e-6)
    assert summary["tb_flags"] == 1 and summary["anchor_sources"] == {"espn_consensus_devig": 1}
    assert snapshot["tb"]["row_side_is_tb_side"] is True and summary["tb_flags_off_row_side"] == 0
    assert log[0][2].line == -11.5  # home at -C, v9's convention


def test_stale_or_future_consensus_falls_back_to_the_mid(monkeypatch, tmp_path):
    _write_consensus(tmp_path, home_line=-11.5, away_fair=0.30, observed=NOW - timedelta(minutes=91))
    _write_consensus(tmp_path, home_line=-11.5, away_fair=0.30, observed=NOW + timedelta(minutes=1))
    forecast = _inputs(
        asks={"away": 0.55, "home": 0.47},
        mids={"away": 0.54, "home": 0.46},
        observed=NOW - timedelta(minutes=5),
    )
    _, log = _log_direct(monkeypatch, tmp_path, forecast, lambda: NOW)
    snapshot = json.loads(log[0][2].model_input_snapshot_json)
    assert snapshot["anchor_source"] == "polymarket_mid"
    assert snapshot["probability"] == pytest.approx(0.54, abs=1e-9)  # mean of 0.54 and 1 - 0.46
    assert snapshot["tb"]["evaluated"] is False


def test_decision_time_is_read_after_the_anchors_load(monkeypatch, tmp_path):
    """A capture written while anchors load must not be judged by a clock read before it."""
    calls: list[str] = []
    real = v10._consensus_quotes

    def consensus(*args):
        calls.append("anchors")
        return real(*args)

    def clock():
        calls.append("clock")
        return NOW

    monkeypatch.setattr(v10, "_consensus_quotes", consensus)
    forecast = _inputs(asks={"away": 0.55, "home": 0.47}, mids={"away": 0.54}, observed=NOW)
    _log_direct(monkeypatch, tmp_path, forecast, clock)
    assert calls.index("anchors") < calls.index("clock")


def test_no_anchor_and_no_ask_are_skipped_and_counted(monkeypatch, tmp_path):
    no_anchor = _inputs(asks={"away": 0.55, "home": 0.47}, mids={}, observed=NOW)
    summary, log = _log_direct(monkeypatch, tmp_path, no_anchor, lambda: NOW)
    assert log == [] and summary["skip_counts"] == {"NO_CALL_ANCHOR_MISSING": 1}
    no_ask = _inputs(asks={"away": 0.55, "home": None}, mids={"away": 0.40}, observed=NOW)
    summary, log = _log_direct(monkeypatch, tmp_path, no_ask, lambda: NOW)
    assert log == [] and summary["skip_counts"] == {"NO_EXECUTABLE_ASK": 1}


def test_duplicate_rerun_is_counted_not_raised(monkeypatch, tmp_path):
    forecast = _inputs(asks={"away": 0.55, "home": 0.47}, mids={"away": 0.54}, observed=NOW)
    log: list = []
    _patch_logging(monkeypatch, log)
    monkeypatch.setattr(forecast_cli, "utc_now", lambda: NOW)
    ledger = FakeLedger("flat", log)
    kwargs = {
        "forecast": forecast,
        "market": "spread",
        "data_root": tmp_path,
        "args_date": "2026-11-19",
        "config": CONFIG,
        "registry": _registry(),
        "bans": None,
        "flat_ledger": ledger,
    }
    assert v10.log_nba_anchor_v10(**kwargs)["logged"] == 1
    second = v10.log_nba_anchor_v10(**kwargs)
    assert second["logged"] == 0 and second["skip_counts"] == {"duplicate": 1}


def test_captured_anchor_source_max_age():
    quotes = {
        ("1", "spread"): [
            AnchorQuote("1", "spread", 3.0, "c", (NOW - timedelta(minutes=95)).isoformat(), False),
            AnchorQuote("1", "spread", 4.0, "c", (NOW - timedelta(minutes=30)).isoformat(), False),
            AnchorQuote("1", "spread", 5.0, "c", (NOW + timedelta(minutes=1)).isoformat(), False),
        ]
    }
    assert CapturedAnchorSource(quotes, max_age=timedelta(minutes=90)).anchor("1", "spread", NOW).line == 4.0
    fresh_only = {("1", "spread"): quotes[("1", "spread")][:1]}
    assert CapturedAnchorSource(fresh_only, max_age=timedelta(minutes=90)).anchor("1", "spread", NOW) is None
    assert CapturedAnchorSource(fresh_only).anchor("1", "spread", NOW).line == 3.0


def test_anchor_mean_round_trips_the_fair_probability():
    from statistics import NormalDist

    mean = v10.anchor_mean(11.5, 0.489166, SIGMA_SPREAD)
    assert NormalDist().cdf((11.5 - mean) / SIGMA_SPREAD) == pytest.approx(0.489166, abs=1e-12)


def test_ledger_replay_accepts_the_v10_snapshot_and_rejects_tampering(monkeypatch, tmp_path):
    """The ledger replays every stored snapshot on append; an unknown schema is rejected outright."""
    from model_prediction.learned_replay import validate_decision_snapshot

    _write_consensus(tmp_path, home_line=-11.5, away_fair=0.489166, observed=NOW - timedelta(minutes=20))
    forecast = _inputs(asks={"away": 0.55, "home": 0.47}, mids={"away": 0.54}, observed=NOW)
    _, log = _log_direct(monkeypatch, tmp_path, forecast, lambda: NOW)
    request = log[0][2]
    payload = json.loads(request.model_input_snapshot_json)
    row = {
        "event_id": request.event_id,
        "model_version": request.model_version,
        "market_type": request.market_type.value,
        "selection": request.selection,
        "league": request.league.value,
        "event_start_utc": request.event_start_utc,
        "model_probability": f"{request.model_probability:.6f}",
    }
    assert validate_decision_snapshot(payload, row) == pytest.approx(request.model_probability, abs=2e-6)
    tampered = {**payload, "anchor_mean": payload["anchor_mean"] + 1.0}
    with pytest.raises(ValueError):
        validate_decision_snapshot(tampered, row)


def test_mid_needs_a_two_sided_quote_at_most_ten_points_wide():
    # Live 2026-11-21 NO @ SA 232.5 over: bid 3c, ask 50c, midpoint 26.5c -- not a price.
    assert v10._row_mid({"bid": 0.03, "ask": 0.50, "midpoint": 0.265}) is None
    assert v10._row_mid({"ask": 0.50}) is None
    assert v10._row_mid({"bid": 0.48, "ask": 0.50, "midpoint": 0.49}) == 0.49
    assert v10._row_mid({"bid": 0.45, "ask": 0.55}) == pytest.approx(0.50)


def test_an_old_mid_still_anchors_because_it_is_the_quote_v9_priced(monkeypatch, tmp_path):
    forecast = _inputs(
        asks={"away": 0.55, "home": 0.47}, mids={"away": 0.54}, observed=NOW - timedelta(hours=3)
    )
    summary, log = _log_direct(monkeypatch, tmp_path, forecast, lambda: NOW)
    snapshot = json.loads(log[0][2].model_input_snapshot_json)
    assert snapshot["anchor_source"] == "polymarket_mid" and snapshot["anchor_age_seconds"] == 10800.0
    assert summary["logged"] == 1


def test_settlement_recovers_the_slug_from_the_v10_rationale(monkeypatch, tmp_path):
    from model_prediction.cli.settle import _extract_market_slug

    forecast = _inputs(asks={"away": 0.55, "home": 0.47}, mids={"away": 0.54}, observed=NOW)
    forecast["anchor_v10_inputs"][0]["market_slug"] = "asc-nba-lac-sa-2026-11-19-pos-11pt5"
    _, log = _log_direct(monkeypatch, tmp_path, forecast, lambda: NOW)
    rationale = log[0][2].rationale
    assert _extract_market_slug(rationale, {}) == "asc-nba-lac-sa-2026-11-19-pos-11pt5"
