"""Targeted checks for the NCAAB ESPN line archive and residual-test research scripts (quick-261115-1m8)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import ncaab_espn_lines as lines
import ncaab_residual as resid


def _item(name: str, details: str, home_fav: bool, *, close: str | None = None, open_: str | None = None):
    home: dict = {"favorite": home_fav, "current": {"pointSpread": {"alternateDisplayValue": "-3"}}}
    if close is not None:
        home["close"] = {"pointSpread": {"alternateDisplayValue": close}}
    if open_ is not None:
        home["open"] = {"pointSpread": {"alternateDisplayValue": open_}}
    return {
        "provider": {"id": "1", "name": name},
        "details": details,
        "spread": 999.0,  # the raw field's sign/value must never be trusted
        "overUnder": 140.5,
        "homeTeamOdds": home,
        "awayTeamOdds": {"favorite": not home_fav},
        "close": {"total": {"alternateDisplayValue": "141.5"}},
        "open": {"total": {"alternateDisplayValue": "139.5"}},
    }


def test_orientation_uses_flags_and_never_the_raw_spread_field() -> None:
    home_fav = lines.trim_item(_item("DraftKings", "ABC -3", True))
    away_fav = lines.trim_item(_item("DraftKings", "XYZ -3", False))
    assert home_fav["home_spread_line"] == -3.0
    assert away_fav["home_spread_line"] == 3.0
    assert home_fav["raw_spread"] is None  # 999 is outside the plausibility bound and dropped


def test_live_items_are_excluded_and_open_close_medians_are_separate() -> None:
    providers = [
        lines.trim_item(_item("Book A", "ABC -3", True, close="-4", open_="-2")),
        lines.trim_item(_item("Book B", "ABC -3", True, close="-5", open_="-3")),
        lines.trim_item(_item("Book C - Live Odds", "ABC -20", True, close="-20", open_="-20")),
    ]
    sel = lines.select_lines(providers)
    assert sel["n_nonlive_providers"] == 2
    assert sel["home_spread_line"] == -4.5  # median of the two non-live closes
    assert sel["open_home_spread_line"] == -2.5
    assert sel["total_line"] == 141.5 and sel["open_total_line"] == 139.5


def test_price_like_values_never_enter_a_line_field() -> None:
    item = _item("ESPN BET", "ABC -110", True, close="-110")
    rec = lines.trim_item(item)
    assert rec["home_ps_close"] is None
    assert "ps_close_dropped" in rec["flags"]
    assert rec["details"] is None


def test_rule_hash_is_stable_and_stamped() -> None:
    event = {"event_id": "1", "season_year": 2025}
    rec = lines.build_record(event, 404, [], failed=False)
    assert rec["rule_sha256"] == lines._rule_hash()
    assert rec["no_line_reason"] == "http_404"
    try:
        lines.assert_records_match_rule({"1": {**rec, "rule_sha256": "other"}})
    except SystemExit:
        pass
    else:
        raise AssertionError("a drifted rule hash must refuse to run")


def test_side_record_excludes_pushes_and_follows_prediction_sign() -> None:
    pred = np.array([2.0, -2.0, 2.0, 0.4, 2.0])
    y = np.array([3.0, -1.0, 0.0, 5.0, -4.0])
    inv, d = resid._clusters(["a", "a", "b", "b", "c"])
    cnt = resid._boot_counts(d, np.random.default_rng(1))
    out = resid.side_record(pred, y, inv, d, cnt)
    assert (out["wins"], out["losses"], out["pushes_excluded"]) == (2, 1, 1)


def test_ridge_recovers_signal_and_noise_features_are_rejected() -> None:
    rng = np.random.default_rng(3)
    n = 6000
    x = rng.normal(size=n)
    y_signal = 0.4 * x + rng.normal(size=n) * 10
    dates = [f"d{i % 200}" for i in range(n)]
    fit = resid.ridge_fit(x[:3000].reshape(-1, 1), y_signal[:3000])
    pred = resid.ridge_predict(fit, x[3000:].reshape(-1, 1))
    good = resid.window_stats(pred, y_signal[3000:], dates[3000:], fit["ymean"], 7)
    assert good["slope"] > 0
    y_noise = rng.normal(size=n) * 10
    fit2 = resid.ridge_fit(x[:3000].reshape(-1, 1), y_noise[:3000])
    bad = resid.window_stats(
        resid.ridge_predict(fit2, x[3000:].reshape(-1, 1)), y_noise[3000:], dates[3000:], fit2["ymean"], 7
    )
    assert bad["mse_delta_ci_adj"][0] <= 0  # pure noise never clears the adjusted lower bound


def test_rule_has_pinned_hash_and_windows_are_disjoint() -> None:
    years = [y for ys in resid.WINDOW_SEASONS.values() for y in ys]
    assert len(years) == len(set(years))
    assert len(resid.RULE_SHA256) == 64
    assert resid.power_statement()["bets_needed_80pct_power_vs_52.4_break_even"]["53%"] > 40000


def _synthetic_rows(market: str, rng: np.random.Generator, signal: float) -> list[dict]:
    rows = []
    for year in (2023, 2024, 2025, 2026, 2027):
        for i in range(2400):
            x = float(rng.normal(0, 3))
            rows.append(
                {
                    "event_id": f"{year}-{i}",
                    "date": f"{year}-{1 + (i // 120) % 12:02d}-{1 + (i % 28):02d}",
                    "season_year": year,
                    "tempo": float(rng.normal()),
                    "rest": float(rng.integers(-3, 4)),
                    "neutral": float(rng.random() < 0.1),
                    "n_min": float(rng.integers(0, 16)),
                    "nond1": float(rng.random() < 0.05),
                    "line": float(rng.normal(0, 8)),
                    "x": x,
                    "y": signal * x + float(rng.normal(0, 11)),
                    "move": float(rng.normal()) if year >= 2026 else None,
                }
            )
    return rows


def test_run_market_no_pick_on_noise_survives_zero_bets_and_renders() -> None:
    rng = np.random.default_rng(11)
    res = resid.run_market("spread", _synthetic_rows("spread", rng, 0.0))
    assert res["verdict"].startswith("NO PICK")
    assert res["locked_windows"] == "NOT SCORED"
    assert not res["movement_in_family"] and "movement_diagnostic" in res
    # the shrunk, ridge-regularised prediction of a no-skill model never reaches the 1-point threshold
    assert res["models"]["m0_v6"]["validation"]["side_record"]["bets"] == 0
    rep = {
        "rule_sha256": resid.RULE_SHA256,
        "script_sha256": "x",
        "selftest_label_shuffled": True,
        "inputs": {
            "lines_sha256": "l",
            "espn_rule_sha256_values": [],
            "scoreboard_sha256": "s",
            "skipped": {},
            "line_records": 0,
            "line_records_with_line": 0,
        },
        "power": resid.power_statement(),
        "pipeline_gate": {},
        "local_history_overlap": {},
        "orientation_favourite_wins": {m: {} for m in resid.MARKETS},
        "residual_by_season": {m: {} for m in resid.MARKETS},
        "markets": {m: res for m in resid.MARKETS},
    }
    text = resid.render_md(rep)
    assert "NO PICK" in text and "no bets" in text


def test_run_market_accepts_real_signal_but_never_a_movement_model() -> None:
    rng = np.random.default_rng(12)
    res = resid.run_market("total", _synthetic_rows("total", rng, 0.45))
    assert res["selected_model"] in resid.BASE_FAMILY
    assert "m_move" not in res["accepted_models"] and "m_joint_move" not in res["accepted_models"]
    assert isinstance(res["locked_windows"]["holdout"], dict)
    assert res["models"]["m0_v6"]["accepted_on_validation"]
