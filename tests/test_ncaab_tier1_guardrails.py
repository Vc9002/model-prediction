"""NCAAB Tier-1 guardrails (quick task 261115-1m3).

Every guardrail here was chosen after looking at the 11-08..11-15 flat v6 ledger, so these
tests pin mechanics only; whether the thresholds help is a forward question.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from model_prediction.college_basketball_forward import (
    HIGH_PROBABILITY_UNIT_CAP,
    MAX_SAME_DIRECTION_TOTALS_PER_SLATE,
    _apply_same_direction_total_cap,
    _model_line_divergence,
    forecast_ncaab_sport,
)
from model_prediction.features.base import GameRecord
from model_prediction.main_ledgers import MultiSportPickLedger

NOW = datetime.now(UTC)
START = (NOW + timedelta(days=1)).isoformat()


def _side(selection, price, line=None, is_long=True):
    return {
        "selection": selection,
        "team": selection.title(),
        "description": selection.title(),
        "price_probability": price,
        "line": line,
        "is_long": is_long,
    }


def _market(kind, slug, line, sides):
    return {"market_id": slug, "market_slug": slug, "market_type": kind, "line": line, "sides": sides}


def _event(event_id, markets):
    return {"event_id": event_id, "event_start_utc": START, "title": "Away vs. Home", "markets": markets}


def _quote(event_id, slug, kind, line, long_bid, long_ask, short_bid, short_ask, age_seconds=60):
    return {
        "provider": "polymarket_us",
        "reconstructed": False,
        "timestamp_valid": True,
        "market_state": "MARKET_STATE_OPEN",
        "event_id": event_id,
        "event_start_utc": START,
        "market_id": slug,
        "market_slug": slug,
        "market_type": kind,
        "line": line,
        "observed_at_utc": (NOW - timedelta(seconds=age_seconds)).isoformat(),
        "transact_time_utc": (NOW - timedelta(seconds=age_seconds + 1)).isoformat(),
        "long": {"bid": long_bid, "ask": long_ask},
        "short": {"bid": short_bid, "ask": short_ask},
    }


def run(
    tmp_path,
    monkeypatch,
    events: list[dict[str, Any]],
    *,
    spread_predict=None,
    total_predict=None,
    quotes: list[dict[str, Any]] | None = None,
    policy: dict[str, Any] | None = None,
    sample: int = 9,
    main: bool = False,
):
    """Run forecast_ncaab_sport against a fixture slate.

    ``spread_predict(game)`` -> (market_type, probabilities, inference_inputs) for moneyline and
    spread games; ``total_predict(game)`` -> (probabilities, inference_inputs) for total games.
    """
    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_MIRROR", "0")
    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_AUTHORITY", "xlsx")
    games = [
        GameRecord(str(i), (NOW - timedelta(days=i + 1)).isoformat(), "NCAAB", "Away", "Home", 70, 75)
        for i in range(20)
    ]
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.ESPNClient.scoreboard",
        lambda *a: {"events": [{"id": "fixture"}]},
    )
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.FeatureStore.games_before", lambda *a: games
    )
    if quotes:
        archive = tmp_path / "odds" / "ncaab" / NOW.date().isoformat() / "polymarket_snapshots.jsonl"
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_text("".join(json.dumps(q) + "\n" for q in quotes), encoding="utf-8")
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.PolymarketUSClient.slate", lambda *a: events
    )

    class SpreadModel:
        version = "ncaab-spread-v6"

        def predict_games(self, history, upcoming):
            out = []
            for g in upcoming:
                if g.total_line is not None or spread_predict is None:
                    continue
                kind, probabilities, inputs = spread_predict(g)
                out.append(
                    SimpleNamespace(
                        event_id=g.event_id,
                        market_type=kind,
                        probabilities=probabilities,
                        uncertainty=0.1,
                        rationale="fixture",
                        inference_inputs=inputs,
                        feature_basis={"current_season_sample": sample},
                    )
                )
            return out

    class TotalModel:
        version = "ncaab-total-v6"

        def predict_games(self, history, upcoming):
            out = []
            for g in upcoming:
                if g.total_line is None or total_predict is None:
                    continue
                probabilities, inputs = total_predict(g)
                out.append(
                    SimpleNamespace(
                        event_id=g.event_id,
                        market_type="total",
                        probabilities=probabilities,
                        uncertainty=0.1,
                        rationale="fixture",
                        inference_inputs=inputs,
                        feature_basis={"current_season_sample": sample},
                    )
                )
            return out

    monkeypatch.setattr("model_prediction.college_basketball_forward.ncaab_spread_model", SpreadModel)
    monkeypatch.setattr("model_prediction.college_basketball_forward.ncaab_total_model", TotalModel)
    monkeypatch.setattr("model_prediction.college_basketball_forward.ncaab_model", SpreadModel)
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.ProductionModelRegistry.load",
        lambda *a, **k: SimpleNamespace(
            champion=lambda sport, market: SimpleNamespace(
                available=True, model_id="ncaab-total-v6" if market == "total" else "ncaab-spread-v6"
            )
        ),
    )
    flat = MultiSportPickLedger(tmp_path, flat=True)
    main_ledger = MultiSportPickLedger(tmp_path / "main") if main else None
    report = forecast_ncaab_sport(
        data_root=tmp_path,
        args_date=NOW.date().isoformat(),
        config={"models": {"NCAAB": {"min_edge": 0.035, "min_team_games": 8, **(policy or {})}}},
        flat_ledger=flat,
        main_ledger=main_ledger,
    )
    assert not report["errors"], report["errors"]
    report["_flat_rows"] = flat.rows()
    report["_main_rows"] = main_ledger.rows() if main_ledger else []
    return report


def _spread_event(event_id="s1", away_line=5.5, away_price=0.52, home_price=0.48):
    return _event(
        event_id,
        [
            _market(
                "spread",
                f"{event_id}-sp",
                away_line,
                [
                    _side("away", away_price, away_line, is_long=True),
                    _side("home", home_price, -away_line, is_long=False),
                ],
            )
        ],
    )


def _spread_quote(event_id="s1", away_line=5.5):
    return _quote(event_id, f"{event_id}-sp", "spread", away_line, 0.49, 0.51, 0.47, 0.49)


# ---------------------------------------------------------------- stake caps / shadow


def test_high_probability_picks_are_capped_to_one_unit_on_the_persisted_row(tmp_path, monkeypatch):
    def spread(game):
        return "spread", {"away": 0.88, "home": 0.12}, {"mean": 5.0, "sd": 10.5, "line": 5.5}

    report = run(
        tmp_path,
        monkeypatch,
        [_spread_event()],
        spread_predict=spread,
        quotes=[_spread_quote()],
        main=True,
    )
    flat, main = report["_flat_rows"][0], report["_main_rows"][0]
    assert flat["selection"] == "away"
    assert float(flat["model_probability"]) >= 0.80
    assert float(flat["units"]) == HIGH_PROBABILITY_UNIT_CAP
    assert float(main["units"]) == HIGH_PROBABILITY_UNIT_CAP


def test_below_the_probability_floor_keeps_edge_scaled_stake(tmp_path, monkeypatch):
    def spread(game):
        return "spread", {"away": 0.70, "home": 0.30}, {"mean": 5.0, "sd": 10.5, "line": 5.5}

    report = run(tmp_path, monkeypatch, [_spread_event()], spread_predict=spread, quotes=[_spread_quote()])
    assert float(report["_flat_rows"][0]["units"]) > 1.0


def test_unquoted_placeholder_price_row_is_visible_but_stakes_nothing(tmp_path, monkeypatch):
    def spread(game):
        return "spread", {"away": 0.62, "home": 0.38}, {"mean": 5.0, "sd": 10.5, "line": 5.5}

    placeholder = run(
        tmp_path / "ph",
        monkeypatch,
        [_spread_event(away_price=0.50, home_price=0.50)],
        spread_predict=spread,
    )
    row = placeholder["_flat_rows"][0]
    assert row["sportsbook"] == "polymarket_us_indicative"
    assert float(row["units"]) == 0.0
    assert "UNQUOTED_SHADOW" in row["rationale"]

    real_indicative = run(
        tmp_path / "px",
        monkeypatch,
        [_spread_event(away_price=0.52, home_price=0.48)],
        spread_predict=spread,
    )
    assert float(real_indicative["_flat_rows"][0]["units"]) > 0.0


# ---------------------------------------------------------------- moneyline edge


def _moneyline_event(price_home):
    return _event(
        "m1",
        [
            _market(
                "moneyline",
                "m1-ml",
                None,
                [_side("away", 1 - price_home, is_long=False), _side("home", price_home, is_long=True)],
            )
        ],
    )


def _moneyline(game):
    return "moneyline", {"home": 0.60, "away": 0.40}, {"mean": 3.0, "sd": 10.0}


def test_moneyline_with_nonpositive_edge_is_declined_and_positive_edge_is_kept(tmp_path, monkeypatch):
    declined = run(tmp_path / "neg", monkeypatch, [_moneyline_event(0.70)], spread_predict=_moneyline)
    assert declined["_flat_rows"] == []
    assert declined["declined_slots"] == [["NCAAB", "m1", "moneyline"]]
    kept = run(tmp_path / "pos", monkeypatch, [_moneyline_event(0.55)], spread_predict=_moneyline)
    assert len(kept["_flat_rows"]) == 1
    assert kept["declined_slots"] == []


# ---------------------------------------------------------------- divergence gate on Flat


def test_flat_applies_the_divergence_gate(tmp_path, monkeypatch):
    def spread(game):
        # Away +5.5 but the model expects the home side to win by 30.
        return "spread", {"away": 0.65, "home": 0.35}, {"mean": 30.0, "sd": 10.5, "line": 5.5}

    report = run(tmp_path, monkeypatch, [_spread_event()], spread_predict=spread)
    assert report["_flat_rows"] == []
    assert any(s["reason"] == "flat_divergence_gate" for s in report["skipped"])
    assert report["declined_slots"] == [["NCAAB", "s1", "spread"]]


def test_divergence_is_measured_in_the_models_away_frame_for_home_picks():
    prediction = SimpleNamespace(inference_inputs={"mean": 14.0})
    # Home laying 14.5: the away line is +14.5, so a +14 expected home margin is 0.5 away from it.
    assert _model_line_divergence("spread", prediction, "home", -14.5) == pytest.approx(0.5)
    assert _model_line_divergence("spread", prediction, "away", 14.5) == pytest.approx(0.5)
    # The model expects the AWAY side to win by 11; home -2 means away line +2: 13 apart.
    prediction = SimpleNamespace(inference_inputs={"mean": -11.0})
    assert _model_line_divergence("spread", prediction, "home", -2.0) == pytest.approx(13.0)


def test_home_pick_on_a_heavy_favourite_is_not_wrongly_gated(tmp_path, monkeypatch):
    def spread(game):
        return "spread", {"away": 0.40, "home": 0.60}, {"mean": 14.0, "sd": 10.5, "line": 14.5}

    report = run(
        tmp_path,
        monkeypatch,
        [_spread_event(away_line=14.5, away_price=0.45, home_price=0.55)],
        spread_predict=spread,
    )
    assert [r["selection"] for r in report["_flat_rows"]] == ["home"]


# ---------------------------------------------------------------- same-direction totals


def _total_event(event_id, line=145.5, over=0.52, under=0.48):
    return _event(
        event_id,
        [
            _market(
                "total",
                f"{event_id}-t{line}",
                line,
                [_side("over", over, line, is_long=True), _side("under", under, line, is_long=False)],
            )
        ],
    )


def test_same_slate_same_direction_total_cap_keeps_highest_edge(tmp_path, monkeypatch):
    # Three overs with different model probability (edge) and one under.
    probabilities = {"t1": 0.58, "t2": 0.66, "t3": 0.62, "t4": 0.40}

    def total(game):
        key = game.event_id.split("_")[0]
        p = probabilities[key]
        return {"over": p, "under": 1 - p}, {"mean": 145.5 + (6 if p > 0.5 else -6), "sd": 13.5}

    report = run(
        tmp_path,
        monkeypatch,
        [_total_event(k) for k in probabilities],
        total_predict=total,
        policy={"max_same_direction_totals": 2},
    )
    kept = sorted(r["event_id"] for r in report["_flat_rows"])
    assert kept == ["t2", "t3", "t4"]  # best two overs plus the lone under
    assert report["declined_slots"] == [["NCAAB", "t1", "total"]]
    assert any(s["reason"] == "same_slate_same_direction_total_cap" for s in report["skipped"])


def test_total_cap_has_a_named_default_and_can_be_disabled():
    assert MAX_SAME_DIRECTION_TOTALS_PER_SLATE > 0
    decisions = [
        {
            "kind": "total",
            "selection": "over",
            "has_executable_quote": False,
            "edge": e,
            "decline_reason": None,
        }
        for e in (0.1, 0.2, 0.3)
    ]
    _apply_same_direction_total_cap(decisions, 0)
    assert all(d["decline_reason"] is None for d in decisions)
    _apply_same_direction_total_cap(decisions, 1)
    assert [d["decline_reason"] is None for d in decisions] == [False, False, True]


# ---------------------------------------------------------------- balanced totals line


def _two_line_event():
    # 138.5 is an untraded alternate sitting at the 50% placeholder; 140.5 is the real market.
    return _event(
        "e1",
        [
            _market(
                "total",
                "e1-t138",
                138.5,
                [_side("over", 0.50, 138.5, True), _side("under", 0.50, 138.5, False)],
            ),
            _market(
                "total",
                "e1-t140",
                140.5,
                [_side("over", 0.55, 140.5, True), _side("under", 0.45, 140.5, False)],
            ),
        ],
    )


def _two_line_total(game):
    return {"over": 0.62, "under": 0.38}, {"mean": 146.0, "sd": 13.5}


def test_balanced_totals_line_comes_from_fresh_bbo_mids_not_slate_prices(tmp_path, monkeypatch):
    quotes = [
        # 138.5 trades far from even (mid 0.80); 140.5 is balanced (mid 0.50).
        _quote("e1", "e1-t138", "total", 138.5, 0.78, 0.82, 0.18, 0.22),
        _quote("e1", "e1-t140", "total", 140.5, 0.49, 0.51, 0.47, 0.49),
    ]
    report = run(tmp_path, monkeypatch, [_two_line_event()], total_predict=_two_line_total, quotes=quotes)
    row = report["_flat_rows"][0]
    assert float(row["line"]) == 140.5
    assert row["sportsbook"] == "polymarket_us_executable_ask"
    # Priced at the chosen line's own quote (over ask 51%), not the other line's.
    assert float(row["market_implied_probability"]) == pytest.approx(0.51, abs=0.001)
    snapshot = json.loads(row["model_input_snapshot_json"])
    assert snapshot["features"]["main_line_source"] == "bbo_mid"
    assert snapshot["features"]["research_quote_join.market_slug"] == "e1-t140"


def test_totals_line_falls_back_to_the_slate_rule_without_fresh_quotes(tmp_path, monkeypatch):
    report = run(tmp_path, monkeypatch, [_two_line_event()], total_predict=_two_line_total)
    row = report["_flat_rows"][0]
    assert float(row["line"]) == 138.5  # the placeholder wins the slate rule; no quote can overrule it
    snapshot = json.loads(row["model_input_snapshot_json"])
    assert snapshot["features"]["main_line_source"] == "slate_price"


def test_stale_quotes_do_not_decide_the_line(tmp_path, monkeypatch):
    quotes = [
        _quote("e1", "e1-t138", "total", 138.5, 0.78, 0.82, 0.18, 0.22, age_seconds=7200),
        _quote("e1", "e1-t140", "total", 140.5, 0.49, 0.51, 0.47, 0.49, age_seconds=7200),
    ]
    report = run(tmp_path, monkeypatch, [_two_line_event()], total_predict=_two_line_total, quotes=quotes)
    assert float(report["_flat_rows"][0]["line"]) == 138.5


def test_paper_trial_cohort_excludes_zero_stake_shadow_rows():
    from model_prediction.portfolio.ncaab_paper_trial import eligible_trial_pick

    base = {"model_id": "ncaab-spread-v6", "sport": "ncaab", "market_type": "spread", "edge": "0.060000"}
    assert eligible_trial_pick({**base, "units": "1.40"}, min_edge=0.05, max_edge=0.08)
    assert eligible_trial_pick(base, min_edge=0.05, max_edge=0.08)  # legacy rows carry no units
    assert not eligible_trial_pick({**base, "units": "0.00"}, min_edge=0.05, max_edge=0.08)
