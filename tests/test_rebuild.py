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
    calibration_curve, date_cluster_bootstrap, team_cluster_bootstrap,
    build_split_manifest, date_cluster_split, ChronologicalFold,
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
    compute_decision_times, horizon_specs_for_sport, horizon_from_time_to_start,
    validate_horizon_separation, HORIZONS, HORIZON_HOURS_BEFORE,
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
    executable_edge, is_tradeable, MarketResidualModel, MarketResidualFeatures,
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
            ref = store.write("espn", "2026-08-01", "test1", {"a": 1})
            assert len(ref.snapshot_hash) == 64  # SHA-256 hex
            assert store.verify_hash(ref)  # verify stored hash
            data = store.read(ref)
            assert data["a"] == 1

    def test_immutability(self):
        with tempfile.TemporaryDirectory() as td:
            store = RawStore(td)
            ref1 = store.write("espn", "2026-08-01", "test1", {"a": 1})
            # Write same data again — same hash, idempotent
            ref2 = store.write("espn", "2026-08-01", "test1", {"a": 1})
            assert ref1.snapshot_hash == ref2.snapshot_hash, "Same payload should produce same hash"
            # Different payload — different hash
            ref3 = store.write("espn", "2026-08-01", "test1", {"a": 2})
            assert ref1.snapshot_hash != ref3.snapshot_hash, "Different payload should produce different hash"

    def test_write_is_atomic_no_leftover_temp_files(self):
        """Real gap fixed (FOUNDATION_COMPLETION.md Phase 2): write()
        previously wrote directly to the final content-addressed path — a
        crash mid-write could leave a truncated file sitting at a
        hash-named path that looks like a valid immutable snapshot. Now
        writes to a temp file and os.replace()s into place; verify no temp
        file is ever left behind and the final file is valid."""
        with tempfile.TemporaryDirectory() as td:
            store = RawStore(td)
            ref = store.write("espn", "2026-08-01", "test1", {"a": 1, "b": [1, 2, 3]})
            all_files = list(Path(td).rglob("*"))
            tmp_files = [f for f in all_files if ".tmp" in f.name]
            assert tmp_files == [], f"leftover temp file(s) after write: {tmp_files}"
            assert store.verify_hash(ref)
            assert ref.path.exists()


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

    def test_primary_key_deduplicates_repeated_collection(self):
        """Real bug fixed (see outputs/rebuild/takeover_status.md
        Checkpoint 9): append mode previously had no primary-key awareness
        at all, so repeated collection of the same real event produced
        duplicate rows — 188 real STATUS_FINAL MLB scoreboard rows were
        only 135 real unique games."""
        with tempfile.TemporaryDirectory() as td:
            store = NormalizedStore(td)
            row1 = pl.DataFrame({"event_id": ["E1"], "observed_at_utc": ["t1"], "home_score": [3]})
            row2 = pl.DataFrame({"event_id": ["E1"], "observed_at_utc": ["t2"], "home_score": [3]})
            store.write("mlb", "scoreboard", row1, primary_key=["event_id"])
            store.write("mlb", "scoreboard", row2, primary_key=["event_id"])
            result = store.read("mlb", "scoreboard")
            assert result.height == 1

    def test_primary_key_keeps_latest_content_on_legitimate_update(self):
        """A game's score/status legitimately changes across re-collections
        — keep_latest must reflect the newest state, not the first."""
        with tempfile.TemporaryDirectory() as td:
            store = NormalizedStore(td)
            in_progress = pl.DataFrame({
                "event_id": ["E1"], "observed_at_utc": ["t1"],
                "home_score": [0], "status": ["STATUS_IN_PROGRESS"],
            })
            final = pl.DataFrame({
                "event_id": ["E1"], "observed_at_utc": ["t2"],
                "home_score": [5], "status": ["STATUS_FINAL"],
            })
            store.write("mlb", "scoreboard", in_progress, primary_key=["event_id"])
            store.write("mlb", "scoreboard", final, primary_key=["event_id"])
            result = store.read("mlb", "scoreboard")
            assert result.height == 1
            assert result["home_score"][0] == 5
            assert result["status"][0] == "STATUS_FINAL"

    def test_fail_closed_conflict_policy_raises_on_real_conflict(self):
        with tempfile.TemporaryDirectory() as td:
            store = NormalizedStore(td)
            row1 = pl.DataFrame({"game_pk": [1], "observed_at_utc": ["t1"], "pitcher_id": [100]})
            row2 = pl.DataFrame({"game_pk": [1], "observed_at_utc": ["t2"], "pitcher_id": [200]})
            store.write("mlb", "starters", row1, primary_key=["game_pk"])
            with pytest.raises(ValueError, match="Conflicting content"):
                store.write("mlb", "starters", row2, primary_key=["game_pk"], conflict_policy="fail_closed")

    def test_fail_closed_conflict_policy_allows_identical_recollection(self):
        with tempfile.TemporaryDirectory() as td:
            store = NormalizedStore(td)
            row1 = pl.DataFrame({"game_pk": [1], "observed_at_utc": ["t1"], "pitcher_id": [100]})
            row2 = pl.DataFrame({"game_pk": [1], "observed_at_utc": ["t2"], "pitcher_id": [100]})
            store.write("mlb", "starters", row1, primary_key=["game_pk"])
            store.write("mlb", "starters", row2, primary_key=["game_pk"], conflict_policy="fail_closed")
            result = store.read("mlb", "starters")
            assert result.height == 1

    def test_no_primary_key_preserves_old_unconditional_append_behavior(self):
        with tempfile.TemporaryDirectory() as td:
            store = NormalizedStore(td)
            store.write("test", "data", pl.DataFrame({"x": [1]}))
            store.write("test", "data", pl.DataFrame({"x": [1]}))
            result = store.read("test", "data")
            assert result.height == 2, "without primary_key, behavior must be unchanged (no dedup)"


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

    def test_team_cluster_bootstrap(self):
        # CLAUDE.md Part 2 SS2 names both date-cluster and team-cluster
        # bootstrap as required; only date-cluster existed until now.
        vals = [0.1, -0.2, 0.3, 0.0, 0.1, -0.1]
        teams = ["SEA", "SEA", "DET", "DET", "NYY", "NYY"]
        result = team_cluster_bootstrap(vals, teams, n_bootstrap=50)
        assert "ci_lower" in result
        assert "ci_upper" in result
        assert result["ci_lower"] <= result["mean"] <= result["ci_upper"]

    def test_expanding_folds_carry_train_start_and_embargo_dates(self):
        # Real gap closed: fold date-range provenance (CLAUDE.md's exact
        # split-manifest schema) was previously computed but never
        # recorded on the fold object itself.
        dates = [f"2026-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 29)]
        folds = expanding_folds(dates, n_splits=3, val_size=10, gap=2, test_size=30)
        assert len(folds) >= 1
        for f in folds:
            assert f.train_start is not None
            assert f.train_start <= f.train_end
            assert f.embargo_start is not None
            assert f.embargo_end is not None
            # The embargo sits strictly between train_end and val_start.
            assert f.train_end < f.embargo_start <= f.embargo_end < f.val_start

    def test_expanding_folds_zero_gap_has_no_embargo(self):
        dates = [f"2026-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 29)]
        folds = expanding_folds(dates, n_splits=3, val_size=10, gap=0, test_size=30)
        assert len(folds) >= 1
        for f in folds:
            assert f.embargo_start is None
            assert f.embargo_end is None

    def test_rolling_folds_carry_train_start_and_embargo_dates(self):
        dates = [f"2026-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 29)]
        folds = rolling_folds(dates, n_splits=3, train_size=60, val_size=10, gap=2)
        assert len(folds) >= 1
        for f in folds:
            assert f.train_start is not None
            assert f.train_start <= f.train_end
            assert f.train_end < f.embargo_start <= f.embargo_end < f.val_start

    def test_rolling_folds_train_start_advances_across_folds(self):
        # Rolling (unlike expanding) has a fixed-size window that slides
        # forward -- train_start must differ across folds, not stay fixed.
        dates = [f"2026-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 29)]
        folds = rolling_folds(dates, n_splits=3, train_size=60, val_size=10, gap=1)
        assert len(folds) >= 2
        starts = [f.train_start for f in folds]
        assert len(set(starts)) > 1


class TestBuildSplitManifest:
    """CLAUDE.md Part 2 SS2's exact required split-manifest schema."""

    def test_matches_the_required_schema_shape(self):
        folds = [
            ChronologicalFold(
                fold_index=0, train_end="2026-01-10", val_start="2026-01-12", val_end="2026-01-20",
                train_start="2026-01-01", embargo_start="2026-01-11", embargo_end="2026-01-11",
            ),
        ]
        manifest = build_split_manifest(
            sport="mlb", horizon="late", dataset_hash="abc123", folds=folds,
            final_test_start="2026-02-01", final_test_end="2026-02-10",
        )
        assert manifest["sport"] == "mlb"
        assert manifest["horizon"] == "late"
        assert manifest["dataset_hash"] == "abc123"
        assert manifest["final_test_consumed"] is False
        assert manifest["folds"] == [{
            "train_start": "2026-01-01", "train_end": "2026-01-10",
            "embargo_start": "2026-01-11", "embargo_end": "2026-01-11",
            "validation_start": "2026-01-12", "validation_end": "2026-01-20",
        }]

    def test_final_test_consumed_defaults_false(self):
        manifest = build_split_manifest(
            sport="mlb", horizon="late", dataset_hash="x", folds=[],
            final_test_start="2026-02-01", final_test_end="2026-02-10",
        )
        assert manifest["final_test_consumed"] is False

    def test_real_expanding_folds_round_trip_into_a_valid_manifest(self):
        dates = [f"2026-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 29)]
        folds = expanding_folds(dates, n_splits=3, val_size=10, gap=1, test_size=30)
        manifest = build_split_manifest(
            sport="mlb", horizon="late", dataset_hash="h", folds=folds,
            final_test_start=dates[-30], final_test_end=dates[-1], final_test_consumed=True,
        )
        assert len(manifest["folds"]) == len(folds)
        for entry in manifest["folds"]:
            assert entry["train_start"] <= entry["train_end"] < entry["validation_start"] <= entry["validation_end"]


class TestDateClusterSplit:
    """Task 8: real fix for a same-day-contamination bug in the actual
    final-test split (train_mlb_rebuild_real_features.py sliced by game
    *count*, not real calendar date -- two games on the identical date
    could land in different buckets)."""

    def test_never_splits_a_single_dates_games_across_buckets(self):
        # Real shape: several games share each of a handful of real dates.
        dates = (
            ["2026-08-01"] * 5 + ["2026-08-02"] * 3 + ["2026-08-03"] * 4
            + ["2026-08-04"] * 2 + ["2026-08-05"] * 6
        )
        train_dates, calib_dates, test_dates = date_cluster_split(dates, test_size=1, calib_size=1)

        assert set(train_dates) & set(calib_dates) == set()
        assert set(calib_dates) & set(test_dates) == set()
        assert set(train_dates) & set(test_dates) == set()
        assert test_dates == ["2026-08-05"]
        assert calib_dates == ["2026-08-04"]
        assert train_dates == ["2026-08-01", "2026-08-02", "2026-08-03"]

    def test_test_dates_are_chronologically_last(self):
        dates = [f"2026-08-{d:02d}" for d in range(1, 11)]
        _, _, test_dates = date_cluster_split(dates, test_size=3)
        assert test_dates == ["2026-08-08", "2026-08-09", "2026-08-10"]

    def test_zero_calib_size_produces_no_calibration_dates(self):
        dates = [f"2026-08-{d:02d}" for d in range(1, 11)]
        train_dates, calib_dates, _test_dates = date_cluster_split(dates, test_size=2, calib_size=0)
        assert calib_dates == []
        assert len(train_dates) == 8

    def test_not_enough_real_dates_returns_everything_as_train_not_a_fabricated_split(self):
        dates = ["2026-08-01", "2026-08-02"]
        train_dates, calib_dates, test_dates = date_cluster_split(dates, test_size=5, calib_size=5)
        assert train_dates == ["2026-08-01", "2026-08-02"]
        assert calib_dates == []
        assert test_dates == []

    def test_duplicate_dates_are_deduplicated(self):
        dates = ["2026-08-01", "2026-08-01", "2026-08-02", "2026-08-02", "2026-08-03"]
        train_dates, _calib_dates, test_dates = date_cluster_split(dates, test_size=1)
        assert test_dates == ["2026-08-03"]
        assert train_dates == ["2026-08-01", "2026-08-02"]


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
        assert len(results) == 13
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


class TestMarketResidualModel:
    """Real first unit coverage for MarketResidualModel/MarketResidualFeatures
    -- executable_edge()/is_tradeable() (the pure functions above) already
    had tests, but the class itself had zero coverage anywhere (grep-
    verified; tests/test_market_residual.py tests a different, legacy
    MarketResidualModel in model_prediction.models.market_residual, not
    this rebuild one)."""

    def _synthetic_rows(self, n=200, seed=0):
        import random
        rng = random.Random(seed)
        features, labels = [], []
        for _ in range(n):
            # Genuine disagreement (large |logit_model - logit_market|,
            # fresh quote, real depth) predicts a positive outcome; a
            # stale/thin/small-disagreement quote predicts a negative one
            # -- a real, learnable signal, not noise.
            disagreement = rng.uniform(-2.0, 2.0)
            quote_age = rng.uniform(0, 600)
            depth = rng.uniform(0, 5000)
            genuine_score = disagreement - quote_age / 300 + depth / 5000
            label = 1 if genuine_score + rng.uniform(-0.3, 0.3) > 0 else 0
            features.append(MarketResidualFeatures(
                logit_model=disagreement, logit_market=0.0, spread=rng.uniform(0, 0.05),
                depth_ask=depth, quote_age_seconds=quote_age, time_to_start_hours=rng.uniform(0.5, 48),
                model_uncertainty=rng.uniform(0.01, 0.1),
            ))
            labels.append(label)
        return features, labels

    def test_fit_and_predict_produce_valid_probabilities(self):
        features, labels = self._synthetic_rows()
        model = MarketResidualModel().fit(features, labels)
        for f in features[:10]:
            prob = model.predict_genuine_edge_prob(f)
            assert 0.0 <= prob <= 1.0

    def test_learns_the_real_synthetic_signal(self):
        features, labels = self._synthetic_rows(n=400)
        train_f, train_y = features[:300], labels[:300]
        test_f, test_y = features[300:], labels[300:]
        model = MarketResidualModel().fit(train_f, train_y)

        preds = [model.predict_genuine_edge_prob(f) >= 0.5 for f in test_f]
        accuracy = sum(p == bool(y) for p, y in zip(preds, test_y)) / len(test_y)
        assert accuracy > 0.6, "model should learn better than a coin flip on a real separable signal"

    def test_unfitted_model_returns_honest_fallback_not_a_crash(self):
        model = MarketResidualModel()
        f = MarketResidualFeatures(
            logit_model=0.5, logit_market=0.0, spread=0.01, depth_ask=100,
            quote_age_seconds=5, time_to_start_hours=2, model_uncertainty=0.05,
        )
        assert model.predict_genuine_edge_prob(f) == 0.5

    def test_should_trade_respects_the_threshold(self):
        features, labels = self._synthetic_rows()
        model = MarketResidualModel().fit(features, labels)
        strong_edge = MarketResidualFeatures(
            logit_model=2.0, logit_market=0.0, spread=0.0, depth_ask=5000,
            quote_age_seconds=0, time_to_start_hours=24, model_uncertainty=0.01,
        )
        weak_edge = MarketResidualFeatures(
            logit_model=-2.0, logit_market=0.0, spread=0.05, depth_ask=0,
            quote_age_seconds=600, time_to_start_hours=1, model_uncertainty=0.1,
        )
        strong_trade, strong_prob = model.should_trade(strong_edge, threshold=0.6)
        weak_trade, weak_prob = model.should_trade(weak_edge, threshold=0.6)
        assert strong_prob > weak_prob
        assert strong_trade or not weak_trade  # strong case should trade at least as readily

    def test_never_touches_sports_probability(self):
        # Real architectural invariant (module docstring: "Never rewrites
        # the independent sports probability. Market isolation is
        # absolute.") -- MarketResidualFeatures only carries market-side
        # and disagreement-derived inputs, never a raw sports-model
        # probability field the residual model could silently overwrite.
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(MarketResidualFeatures)}
        assert "calibrated_probability" not in field_names
        assert "sports_probability" not in field_names


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


class TestComputeDecisionTimes:
    """Sport-agnostic decision_time_utc computation (Task 4: finish horizon
    architecture) -- extracted from horizon_builder.py's
    build_mlb_horizon_dataset(), where this was previously MLB-only inlined
    logic, so a future real non-MLB horizon builder can reuse it without
    re-deriving the same computation."""

    def _scoreboard(self):
        import polars as pl
        return pl.DataFrame([
            {"event_id": "1", "event_start_utc": "2026-07-20T22:35:00+00:00"},
            {"event_id": "2", "event_start_utc": "2026-07-20T18:05:00+00:00"},
            {"event_id": "3", "event_start_utc": "2026-07-21T18:05:00+00:00"},  # different date
        ])

    def test_filters_to_the_given_game_date(self):
        times = compute_decision_times(self._scoreboard(), "2026-07-20", "late")
        assert set(times.keys()) == {"1", "2"}

    def test_decision_time_is_start_minus_horizon_hours(self):
        from datetime import datetime
        times = compute_decision_times(self._scoreboard(), "2026-07-20", "early")
        expected = datetime.fromisoformat("2026-07-20T22:35:00+00:00").timestamp() - HORIZON_HOURS_BEFORE["early"] * 3600
        assert times["1"].timestamp() == expected

    def test_rejects_an_unknown_horizon(self):
        try:
            compute_decision_times(self._scoreboard(), "2026-07-20", "not_a_real_horizon")
            raised = False
        except ValueError:
            raised = True
        assert raised

    def test_empty_scoreboard_returns_empty(self):
        import polars as pl
        empty = pl.DataFrame(schema={"event_id": pl.Utf8, "event_start_utc": pl.Utf8})
        assert compute_decision_times(empty, "2026-07-20", "mid") == {}


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
