"""Tests for Phase F Autonomous Research State Machine & Runner."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from scripts.phase_f_runner import (
    EvalGameRecord,
    _date_clustered_bootstrap_beta_within,
    _fit_ols,
    _sanitize_for_yaml,
    _within_date_permutation_test,
    build_mlb_slug_edt,
    canonical_mlb_abbr,
    evaluate_panel,
    extract_slug_from_market_slug,
    load_state_file,
    save_state_file,
)


def test_canonical_mlb_abbr() -> None:
    assert canonical_mlb_abbr("Oakland Athletics") == "ath"
    assert canonical_mlb_abbr("Athletics") == "ath"
    assert canonical_mlb_abbr("Los Angeles Dodgers") == "lad"
    assert canonical_mlb_abbr("Arizona Diamondbacks") == "az"
    assert canonical_mlb_abbr("D-backs") == "az"
    assert canonical_mlb_abbr("Chicago White Sox") == "cws"
    assert canonical_mlb_abbr("Chicago Cubs") == "chc"


def test_build_mlb_slug_edt() -> None:
    # 2026-07-21 01:40 UTC is 2026-07-20 21:40 EDT
    slug = build_mlb_slug_edt("Oakland Athletics", "Arizona Diamondbacks", "2026-07-21T01:40:00Z")
    assert slug == "mlb-ath-az-2026-07-20"


def test_extract_slug_from_market_slug() -> None:
    mslug = "tsc-mlb-lad-nyy-2026-07-17-f5-2pt5"
    assert extract_slug_from_market_slug(mslug) == "mlb-lad-nyy-2026-07-17"
    assert extract_slug_from_market_slug("") is None
    assert extract_slug_from_market_slug("random-market-slug") is None


def test_sanitize_for_yaml() -> None:
    data: dict[str, Any] = {
        "float_val": np.float64(0.45),
        "int_val": np.int64(123),
        "bool_true": np.bool_(True),
        "bool_false": False,
        "arr": np.array([1.0, 2.0, 3.0]),
        "nested": {"val": np.float32(1.5)},
    }
    sanitized = _sanitize_for_yaml(data)
    assert isinstance(sanitized["float_val"], float)
    assert isinstance(sanitized["int_val"], int)
    assert isinstance(sanitized["bool_true"], bool)
    assert sanitized["bool_true"] is True
    assert isinstance(sanitized["bool_false"], bool)
    assert sanitized["bool_false"] is False
    assert isinstance(sanitized["arr"], list)
    assert isinstance(sanitized["nested"]["val"], float)


def test_load_and_save_state_tmp(tmp_path: Path) -> None:
    state_file = tmp_path / "phase_f_state.yaml"
    initial_state: dict[str, Any] = {
        "current_stage": "F1R_REPLICATION",
        "sample": {"games": 500, "dates": 40},
        "unlocked": {"f2_distribution": False, "f3_complex_m4": False},
        "production_changes_allowed": False,
    }
    save_state_file(initial_state, state_file)
    assert state_file.exists()

    loaded = load_state_file(state_file)
    assert loaded["current_stage"] == "F1R_REPLICATION"
    assert loaded["sample"]["games"] == 500
    assert loaded["unlocked"]["f2_distribution"] is False
    assert loaded["production_changes_allowed"] is False


def test_fit_ols() -> None:
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    y = np.array([2.0, 4.0, 6.0, 8.0, 10.0])  # y = 2x
    alpha, beta, _se_beta, r_val, p_val = _fit_ols(x, y)
    assert np.isclose(beta, 2.0)
    assert np.isclose(alpha, 0.0)
    assert np.isclose(r_val, 1.0)
    assert p_val < 0.001


def test_f1r_protocol_hash_stability() -> None:
    from scripts.phase_f_runner import F1R_PROTOCOL_HASH

    assert len(F1R_PROTOCOL_HASH) == 16
    assert isinstance(F1R_PROTOCOL_HASH, str)


def test_date_clustered_bootstrap_beta_within() -> None:
    # Build synthetic date-clustered rows where realized residual has positive relationship with delta
    by_date: dict[str, list[EvalGameRecord]] = {}
    for d_idx in range(10):
        d_str = f"2026-07-{10 + d_idx:02d}"
        date_rows = []
        for g_idx in range(5):
            delta = float(g_idx - 2)
            # Within-date: R = 0.5 * delta + noise
            residual = 0.6 * delta + float((g_idx % 2) * 0.1)
            rec = EvalGameRecord(
                event_id=f"game_{d_idx}_{g_idx}",
                decision_utc=f"{d_str}T18:00:00Z",
                game_start_utc=f"{d_str}T19:00:00Z",
                market_line=8.5,
                market_prob=0.50,
                actual_outcome=8.5 + residual,
                structural_pred=8.5 + delta,
                discrepancy=delta,
                realized_residual=residual,
                is_integer_line=False,
                sharp_soft_gap=0.0,
                book_count=2,
                sharp_book_count=1,
                soft_book_count=1,
                quote_count=2,
                quote_age_seconds=120.0,
                date_cluster=d_str,
                season="2026",
            )
            date_rows.append(rec)
        by_date[d_str] = date_rows

    beta_point, ci_low, _ci_high, p_pos = _date_clustered_bootstrap_beta_within(
        by_date, resamples=200, seed=42
    )
    assert beta_point > 0.4
    assert ci_low > 0.0
    assert p_pos >= 0.95


def test_within_date_permutation_test() -> None:
    by_date: dict[str, list[EvalGameRecord]] = {}
    for d_idx in range(8):
        d_str = f"2026-07-{10 + d_idx:02d}"
        date_rows = []
        for g_idx in range(4):
            delta = float(g_idx - 1.5)
            residual = 0.8 * delta
            rec = EvalGameRecord(
                event_id=f"g_{d_idx}_{g_idx}",
                decision_utc=f"{d_str}T18:00:00Z",
                game_start_utc=f"{d_str}T19:00:00Z",
                market_line=8.0,
                market_prob=0.50,
                actual_outcome=8.0 + residual,
                structural_pred=8.0 + delta,
                discrepancy=delta,
                realized_residual=residual,
                is_integer_line=True,
                sharp_soft_gap=0.0,
                book_count=2,
                sharp_book_count=1,
                soft_book_count=1,
                quote_count=2,
                quote_age_seconds=120.0,
                date_cluster=d_str,
                season="2026",
            )
            date_rows.append(rec)
        by_date[d_str] = date_rows

    perm_p, _mean_null, rejects_null = _within_date_permutation_test(
        by_date, actual_beta=0.8, resamples=100, seed=42
    )
    assert perm_p < 0.05
    assert rejects_null is True


def test_evaluate_panel_synthetic() -> None:
    records: list[EvalGameRecord] = []
    for d_idx in range(12):
        d_str = f"2026-07-{10 + d_idx:02d}"
        for g_idx in range(4):
            delta = float(g_idx - 1.5)
            residual = 0.5 * delta + 0.1
            actual = 8.5 + residual
            records.append(
                EvalGameRecord(
                    event_id=f"ev_{d_idx}_{g_idx}",
                    decision_utc=f"{d_str}T18:00:00Z",
                    game_start_utc=f"{d_str}T19:00:00Z",
                    market_line=8.5,
                    market_prob=0.50,
                    actual_outcome=actual,
                    structural_pred=8.5 + delta,
                    discrepancy=delta,
                    realized_residual=residual,
                    is_integer_line=False,
                    sharp_soft_gap=0.0,
                    book_count=2,
                    sharp_book_count=1,
                    soft_book_count=1,
                    quote_count=2,
                    quote_age_seconds=120.0,
                    date_cluster=d_str,
                    season="2026",
                )
            )

    result = evaluate_panel(records, panel_name="SYNTHETIC_TEST", k_folds=4, seed=42)
    assert result["n_games"] == 48
    assert result["n_dates"] == 12
    assert result["beta_within"] > 0
    assert "m0_mae" in result
    assert "m0b_mae" in result
    assert "m4_1_mae" in result
    assert "mae_gain_vs_m0b" in result
    assert "brier_improvement" in result
    assert "nll_improvement" in result
    assert "ece_m4_1" in result
    assert "market_quality" in result
    assert result["market_quality"]["median_books_per_game"] == 2.0
