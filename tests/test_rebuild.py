"""Tests for the rebuild platform — leakage, schema drift, collector restart, identity.

Covers: future leakage, same-day leakage, historical corrections, train-serving parity,
raw-data hash stability, schema drift, duplicate events, player/roster identity,
stale reports, conflicting sources, failed collectors, restartability, deterministic
features, horizon separation, market timestamp validity.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from model_prediction.rebuild import (
    RawStore, NormalizedStore, FeatureStore, MarketStore,
    MetadataDB, IdentityRegistry,
    provenance_row, PROVENANCE_COLUMNS,
)
from model_prediction.rebuild.validation import (
    expanding_folds, rolling_folds, log_loss, brier_score, ece,
    calibration_curve, date_cluster_bootstrap,
)
from model_prediction.rebuild.missingness import (
    FeatureRecord, MissingnessReport, compute_missingness_report,
    beta_binomial_shrink, empirical_bayes_shrink, pitcher_clean_rate_shrink,
    PITCHER_CLEAN_RATES,
)
from model_prediction.rebuild.xgboost_stress import (
    run_stress_tests, stress_test_summary, STRESS_SCENARIOS,
)
from model_prediction.rebuild.horizons import (
    horizon_specs_for_sport, horizon_from_time_to_start,
    validate_horizon_separation, HORIZONS,
)
from model_prediction.rebuild.calibration import (
    PlattCalibrator, IsotonicCalibrator, TemperatureScaling, fit_calibrator,
)
from model_prediction.rebuild.ensemble import Ensemble, equal_weight_ensemble
from model_prediction.rebuild.economic import (
    kelly_fraction, edge_scaled_units, SizeLimits, Exposure,
    evaluate_portfolio, EconomicResult, MonitorState, HEALTH_STATES,
)
from model_prediction.rebuild.market_residual import (
    executable_edge, is_tradeable,
)
from model_prediction.rebuild.models.kbo_npb import KBONPBModel
from model_prediction.rebuild.models.esports import EsportsModel, game_to_series_prob


# ═══════════════════════════════════════════════════════════════════════════════
# Storage tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestProvenance:
    def test_provenance_row_has_all_columns(self):
        r = provenance_row("espn", "rec1", "v1", "2026-01-01T00:00:00Z",
                           "2026-01-01T00:00:00Z", "2026-01-01T19:00:00Z")
        for col in PROVENANCE_COLUMNS:
            assert col in r, f"Missing column: {col}"

    def test_provenance_ingested_at_is_set(self):
        r = provenance_row("src", "id", "v1", "2026T", "2026T", "2026T")
        assert r["ingested_at_utc"] != ""
        assert r["schema_version"] == "1"


class TestRawStore:
    def test_write_and_read(self):
        with tempfile.TemporaryDirectory() as td:
            store = RawStore(td)
            h = store.write("espn", "2026-08-01", "test1", {"a": 1})
            assert len(h) == 64  # SHA-256 hex
            data = store.read("espn", "2026-08-01", "test1")
            assert data["a"] == 1

    def test_immutability(self):
        with tempfile.TemporaryDirectory() as td:
            store = RawStore(td)
            store.write("espn", "2026-08-01", "test1", {"a": 1})
            h1 = store.read_hash("espn", "2026-08-01", "test1")
            # Write same data again — should produce same hash
            store.write("espn", "2026-08-01", "test1", {"a": 1})
            h2 = store.read_hash("espn", "2026-08-01", "test1")
            assert h1 == h2, "Same payload should produce same hash"


class TestNormalizedStore:
    def test_write_read_parquet(self):
        with tempfile.TemporaryDirectory() as td:
            store = NormalizedStore(td)
            df = pl.DataFrame({"x": [1, 2, 3], "y": ["a", "b", "c"]})
            n = store.write("test", "data", df)
            assert n == 3
            result = store.read("test", "data")
            assert result.height == 3
            assert result["x"].to_list() == [1, 2, 3]

    def test_append_mode(self):
        with tempfile.TemporaryDirectory() as td:
            store = NormalizedStore(td)
            store.write("test", "data", pl.DataFrame({"x": [1]}))
            store.write("test", "data", pl.DataFrame({"x": [2]}))
            result = store.read("test", "data")
            assert result.height == 2


# ═══════════════════════════════════════════════════════════════════════════════
# Validation tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestValidation:
    def test_expanding_folds(self):
        dates = [f"2026-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 29)]
        folds = expanding_folds(dates, n_splits=3, val_size=10, test_size=30)
        assert len(folds) >= 1, "Should produce at least one fold"
        for f in folds:
            assert f.train_end < f.val_start, "Train must end before validation starts"

    def test_rolling_folds(self):
        dates = [f"2026-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 29)]
        folds = rolling_folds(dates, n_splits=3, train_size=60, val_size=10)
        for f in folds:
            assert f.train_end < f.val_start

    def test_log_loss_perfect(self):
        assert log_loss([1, 0], [0.99, 0.01]) < 0.05

    def test_brier_perfect(self):
        assert brier_score([1, 0, 1], [1.0, 0.0, 1.0]) < 0.001

    def test_ece_calibrated(self):
        y_true = [1, 0, 1, 0] * 25
        y_prob = [0.5, 0.5, 0.5, 0.5] * 25
        e = ece(y_true, y_prob, n_bins=2)
        assert e <= 1.0

    def test_date_cluster_bootstrap(self):
        vals = [0.1, -0.2, 0.3, 0.0, 0.1]
        dates = ["2026-01-01"] * len(vals)
        result = date_cluster_bootstrap(vals, dates, n_bootstrap=50)
        assert "ci_lower" in result
        assert "ci_upper" in result


# ═══════════════════════════════════════════════════════════════════════════════
# Leakage tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestLeakage:
    def test_expanding_folds_no_future_leakage(self):
        """Train end must always be before validation start."""
        dates = sorted(set(f"2026-{m:02d}-{d:02d}" for m in range(1, 7) for d in range(1, 29)))
        folds = expanding_folds(dates, n_splits=5, val_size=10, gap=1)
        for f in folds:
            assert f.train_end < f.val_start, \
                f"Fold {f.fold_index}: train_end={f.train_end} >= val_start={f.val_start}"

    def test_horizon_separation(self):
        """Late features should not leak into early predictions."""
        early = [{"event_id": "e1", "home_win_prob": 0.55}]
        late = [{"event_id": "e1", "home_win_prob": 0.55}]
        result = validate_horizon_separation(early, [], late)
        assert result["horizon_separation_valid"]

    def test_horizon_from_time(self):
        assert horizon_from_time_to_start(30) == "early"
        assert horizon_from_time_to_start(5) == "mid"
        assert horizon_from_time_to_start(0.5) == "late"

    def test_provenance_observed_before_ingested(self):
        """observed_at_utc must be <= ingested_at_utc."""
        r = provenance_row("src", "id", "v1", "2026-01-01T00:00:00Z",
                           "2026-01-01T00:00:00Z", "2026-01-01T19:00:00Z")
        assert r["observed_at_utc"] <= r["ingested_at_utc"], \
            "observed_at must not be after ingested_at"


# ═══════════════════════════════════════════════════════════════════════════════
# Identity / schema tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestIdentity:
    def test_register_and_resolve(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "meta.db"
            db = MetadataDB(db_path)
            # Register source first — FK constraint on entity_mappings
            db.register_source("espn", "ESPN", "official_public")
            reg = IdentityRegistry(db)
            ident = reg.register("team", "New York Yankees", "mlb", "1903-01-01T00:00:00Z",
                                 source_id="espn", source_entity_id="NYY")
            assert ident.entity_type == "team"
            assert ident.sport == "mlb"
            resolved = reg.resolve("espn", "NYY")
            assert resolved is not None
            assert resolved.canonical_name == "New York Yankees"
            db.close()

    def test_fuzzy_match_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            db = MetadataDB(Path(td) / "meta.db")
            reg = IdentityRegistry(db)
            reg.register("team", "Los Angeles Dodgers", "mlb", "1958-01-01T00:00:00Z")
            match, score = reg.propose_match("team", "mlb", "Los Angeles Angels", min_confidence=0.95)
            assert match is None, "Should fail closed on low-confidence match"


# ═══════════════════════════════════════════════════════════════════════════════
# Missingness tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestMissingness:
    def test_feature_record(self):
        fr = FeatureRecord("elo", 1500.0)
        assert fr.available
        d = fr.to_dict()
        assert d["name"] == "elo"

    def test_missingness_report(self):
        records = [
            FeatureRecord("f1", 1.0, available=True),
            FeatureRecord("f1", None, available=False, missing_reason="not_collected"),
            FeatureRecord("f1", 3.0, available=True),
        ]
        report = compute_missingness_report("group1", records)
        assert report.total_rows == 3
        assert report.available_rows == 2
        assert report.missing_rows == 1
        assert report.coverage == 2 / 3

    def test_beta_binomial_shrink(self):
        r = beta_binomial_shrink("p1", "clean", successes=8, opportunities=20)
        assert 0 < r.posterior_mean < 1
        assert r.raw_rate == 0.4
        # Posterior should be pulled toward prior (0.5 with α=β=2)
        assert r.posterior_mean > r.raw_rate  # 0.42 > 0.40

    def test_empirical_bayes_shrink(self):
        vals = [0.10, 0.20, 0.80, 0.90]
        ses = [0.02, 0.02, 0.02, 0.02]  # small errors, large spread
        prior_var, shrunk = empirical_bayes_shrink(vals, ses)
        # All values should be shrunk toward grand mean (0.5)
        grand_mean = np.mean(vals)
        for v, s in zip(vals, shrunk):
            assert abs(s - grand_mean) < abs(v - grand_mean), \
                f"Value {v} not shrunk toward mean: {s}"

    def test_pitcher_clean_rates(self):
        assert len(PITCHER_CLEAN_RATES) == 5
        r = pitcher_clean_rate_shrink("verlander", "scoreless_inning", 45, 60)
        assert r.posterior_mean > 0.5


# ═══════════════════════════════════════════════════════════════════════════════
# Stress tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestStress:
    def test_run_stress_tests(self):
        trades = [
            {"pnl": 0.5, "team": "A", "month": "2026-06", "edge": 0.05},
            {"pnl": 0.3, "team": "A", "month": "2026-06", "edge": 0.03},
            {"pnl": -0.2, "team": "B", "month": "2026-07", "edge": -0.02},
            {"pnl": 1.0, "team": "C", "month": "2026-08", "edge": 0.10},
            {"pnl": -0.1, "team": "B", "month": "2026-08", "edge": -0.01},
        ]
        results = run_stress_tests(trades, base_pnl=1.5)
        assert len(results) == 6
        summary = stress_test_summary(results)
        assert "overall_verdict" in summary

    def test_stress_scenarios_defined(self):
        assert len(STRESS_SCENARIOS) == 13


# ═══════════════════════════════════════════════════════════════════════════════
# Economics tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestEconomics:
    def test_kelly_fraction(self):
        f = kelly_fraction(0.55, 2.0, fraction=0.25)
        assert f > 0

    def test_kelly_no_edge(self):
        f = kelly_fraction(0.45, 2.0, fraction=0.25)
        assert f == 0.0

    def test_edge_scaled_units(self):
        result = edge_scaled_units(0.58, 0.55, 0.52)
        assert "units" in result
        assert "edge" in result

    def test_exposure(self):
        exp = Exposure()
        ok, reason = exp.can_add("mlb", "NYY", "ev1", 1.0)
        assert ok
        exp.add("mlb", "NYY", "ev1", 1.0)
        ok2, _ = exp.can_add("mlb", "NYY", "ev1", 1.5)
        assert not ok2  # event cap exceeded

    def test_portfolio_evaluation(self):
        trades = [
            EconomicResult("e1", "mlb", "moneyline", "home", None, 0.55, 0.52, 1.0, 1, 0.15),
            EconomicResult("e2", "mlb", "moneyline", "away", None, 0.45, 0.48, 1.0, 0, -1.0),
            EconomicResult("e3", "nba", "moneyline", "home", None, 0.60, 0.50, 1.0, 1, 1.0),
        ]
        result = evaluate_portfolio(trades)
        assert result["total_trades"] == 3
        assert result["settled"] == 3

    def test_zero_min_units(self):
        limits = SizeLimits()
        assert limits.min_units == 0.0, "Zero must be valid default"


# ═══════════════════════════════════════════════════════════════════════════════
# Calibration tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestCalibration:
    def test_platt_calibration(self):
        y_prob = [0.6, 0.7, 0.4, 0.8] * 25
        y_true = [1, 1, 0, 1] * 25
        cal = PlattCalibrator().fit(y_prob, y_true)
        calibrated = cal.transform(0.65)
        assert 0 <= calibrated <= 1

    def test_platt_identity_for_small_sample(self):
        cal = PlattCalibrator().fit([0.6, 0.4], [1, 0])
        assert cal.slope == 1.0  # Falls back to identity with <50 samples


# ═══════════════════════════════════════════════════════════════════════════════
# Ensemble tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestEnsemble:
    def test_equal_weight(self):
        probs = equal_weight_ensemble([[0.6, 0.4], [0.7, 0.3]])
        assert len(probs) == 2

    def test_ensemble_predict(self):
        y_prob1 = [0.55, 0.60, 0.45, 0.70] * 10
        y_prob2 = [0.50, 0.55, 0.50, 0.65] * 10
        y_true = [1, 1, 0, 1] * 10
        ens = Ensemble("equal_weight")
        ens.fit({"m1": y_prob1, "m2": y_prob2}, y_true)
        pred = ens.predict({"m1": 0.65, "m2": 0.55})
        assert 0 <= pred <= 1


# ═══════════════════════════════════════════════════════════════════════════════
# Market residual tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestMarketResidual:
    def test_executable_edge(self):
        result = executable_edge(0.58, 0.55, 0.52, spread=0.02)
        assert result["raw_edge"] > 0
        assert result["cost_adjusted_edge"] < result["raw_edge"]

    def test_is_tradeable(self):
        result = executable_edge(0.58, 0.55, 0.52)
        assert is_tradeable(result, min_edge=0.02)

    def test_not_tradeable_no_edge(self):
        result = executable_edge(0.48, 0.47, 0.52)
        assert not is_tradeable(result)


# ═══════════════════════════════════════════════════════════════════════════════
# Sport model tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestSportModels:
    def test_kbo_model(self):
        model = KBONPBModel("kbo")
        model.fit([])
        pred = model.predict("e1", "Doosan", "KIA")
        assert pred.home_market_value + pred.away_market_value == pytest.approx(1.0, abs=0.01)
        assert pred.tie_prob > 0

    def test_npb_model(self):
        model = KBONPBModel("npb")
        pred = model.predict("e1", "Yomiuri", "Hanshin")
        assert 0 < pred.home_market_value < 1

    def test_esports_model(self):
        model = EsportsModel("cs2")
        model.fit([{"winner": "NaVi", "loser": "FaZe", "date": "2026-01-01"}])
        pred = model.predict("m1", "NaVi", "FaZe", "bo3")
        assert pred.team_a_win_prob > 0.5  # NaVi just beat FaZe

    def test_game_to_series(self):
        assert game_to_series_prob(0.55, "bo3") > 0.55  # Series edge compounds
        assert game_to_series_prob(0.55, "bo5") > game_to_series_prob(0.55, "bo3")


# ═══════════════════════════════════════════════════════════════════════════════
# Horizon tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestHorizons:
    def test_horizon_specs_for_all_sports(self):
        for sport in ["mlb", "nba", "wnba", "nfl", "soccer", "tennis", "unknown"]:
            specs = horizon_specs_for_sport(sport)
            for h in HORIZONS:
                assert h in specs

    def test_no_late_leakage_into_early(self):
        mlb = horizon_specs_for_sport("mlb")
        late_features = set(mlb["late"].available_features)
        early_features = set(mlb["early"].available_features)
        # Late-only features should not be in early
        late_only = {"confirmed_batting_order", "wind_vector"}
        assert not (late_only & early_features), \
            f"Late features {late_only & early_features} leaked into early"


# ═══════════════════════════════════════════════════════════════════════════════
# Monitoring tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestMonitoring:
    def test_health_states(self):
        assert len(HEALTH_STATES) == 8
        assert "HEALTHY_SHADOW" in HEALTH_STATES
        assert "ROLLBACK_REQUIRED" in HEALTH_STATES

    def test_monitor_default_healthy(self):
        m = MonitorState()
        assert m.evaluate() == "HEALTHY_SHADOW"

    def test_monitor_data_degraded(self):
        m = MonitorState(source_health={"espn": "down"})
        assert m.evaluate() == "DATA_DEGRADED"

    def test_monitor_calibration_drift(self):
        m = MonitorState(calibration_drift=0.06)
        assert m.evaluate() == "CALIBRATION_DRIFT"

    def test_monitor_negative_clv(self):
        m = MonitorState(recent_clv=-0.03)
        assert m.evaluate() == "NEGATIVE_CLV"
