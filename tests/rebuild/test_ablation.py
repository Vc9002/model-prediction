"""Real unit coverage for FeatureAblationRunner (ablation.py) -- a complete,
CLAUDE.md-compliant implementation with zero test coverage and zero real
callers anywhere in this codebase (grep-verified) before
scripts/train_mlb_feature_ablation.py wired it to real MLB data.

Also guards against FEATURE_GROUPS_MLB drifting back out of sync with the
real rebuild feature schema -- every name in it used to be a legacy name
(elo_probability, starter_era_gap, lineup_xwoba, ...) that doesn't exist
anywhere in mlb_features.build_game_feature_row()'s real output, which
would have made every real ablation call silently vacuous (zero
available_features -> a 0.5 coin-flip baseline for every group).
"""

from __future__ import annotations

import numpy as np
import polars as pl

from model_prediction.rebuild.ablation import FEATURE_GROUPS_MLB, FeatureAblationRunner, FeatureGroup

# The real columns scripts/train_mlb_rebuild_real_features.py and
# scripts/train_mlb_xgboost_ensemble.py both train on -- kept in sync with
# those scripts' INTENSITY_FEATURES + DIFFERENTIAL_FEATURES by convention;
# a genuine schema change to mlb_features.build_game_feature_row() should
# update this list too.
REAL_MLB_FEATURE_COLUMNS = {
    "home_sp_avg_velocity",
    "away_sp_avg_velocity",
    "home_sp_csw_pct",
    "away_sp_csw_pct",
    "home_bp_bullpen_pitches",
    "away_bp_bullpen_pitches",
    "park_factor",
    "temp_f_first_pitch",
    "home_sp_k_pct",
    "away_sp_k_pct",
    "home_sp_bb_pct",
    "away_sp_bb_pct",
    "home_sp_days_rest",
    "away_sp_days_rest",
    "home_bp_bullpen_avg_velocity",
    "away_bp_bullpen_avg_velocity",
    "home_sp_clean_first_inning_clean_rate",
    "away_sp_clean_first_inning_clean_rate",
    "home_sp_clean_scoreless_inning_rate",
    "away_sp_clean_scoreless_inning_rate",
    "home_sp_clean_clean_appearance_rate",
    "away_sp_clean_clean_appearance_rate",
}


class TestFeatureGroupsMlbMatchesRealSchema:
    def test_every_declared_feature_is_a_real_rebuild_column(self):
        declared = {f for g in FEATURE_GROUPS_MLB for f in g.features}
        unknown = declared - REAL_MLB_FEATURE_COLUMNS
        assert not unknown, (
            f"FEATURE_GROUPS_MLB references features that don't exist in the real "
            f"rebuild feature schema: {unknown}"
        )

    def test_no_group_is_empty(self):
        for group in FEATURE_GROUPS_MLB:
            assert group.features, f"group {group.name!r} has no features"


def _train_fn(X, y):
    from sklearn.linear_model import LogisticRegression

    model = LogisticRegression()
    model.fit(X, y)
    return model


def _predict_fn(model, X):
    return model.predict_proba(X)[:, 1].tolist()


def _synthetic_ablation_data(n: int = 90, seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    informative = rng.normal(0, 1, n)
    noise = rng.normal(0, 1, n)
    y = (informative + rng.normal(0, 0.2, n) > 0).astype(int)
    dates = [f"2026-01-{(i % 28) + 1:02d}" for i in range(n)]
    return pl.DataFrame(
        {
            "game_date": sorted(dates),
            "informative_feature": informative,
            "noise_feature": noise,
            "home_win": y,
        }
    )


class TestFeatureAblationRunner:
    def test_run_isolated_detects_the_informative_group(self):
        data = _synthetic_ablation_data()
        groups = [
            FeatureGroup("informative", ["informative_feature"]),
            FeatureGroup("noise", ["noise_feature"]),
        ]
        runner = FeatureAblationRunner(groups)
        results = runner.run_isolated(
            data,
            target_col="home_win",
            train_fn=_train_fn,
            predict_fn=_predict_fn,
        )

        by_group = {r.group: r for r in results}
        # Removing the real signal should hurt (positive delta_log_loss);
        # removing pure noise should not meaningfully help or hurt.
        assert by_group["informative"].delta_log_loss > by_group["noise"].delta_log_loss

    def test_coverage_impact_reflects_real_null_fraction(self):
        data = pl.DataFrame(
            {
                "game_date": ["2026-01-01"] * 10,
                "f1": [1.0, None, 1.0, None, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
                "home_win": [1, 0] * 5,
            }
        )
        runner = FeatureAblationRunner([FeatureGroup("g1", ["f1"])])
        coverage = runner._coverage(data, ["f1"])
        assert coverage == 0.8

    def test_coverage_is_zero_when_no_features_present(self):
        data = pl.DataFrame({"game_date": ["2026-01-01"], "home_win": [1]})
        runner = FeatureAblationRunner([FeatureGroup("g1", ["missing_feature"])])
        assert runner._coverage(data, ["missing_feature"]) == 0.0

    def test_run_cumulative_adds_groups_in_order_of_isolated_importance(self):
        data = _synthetic_ablation_data()
        groups = [
            FeatureGroup("informative", ["informative_feature"]),
            FeatureGroup("noise", ["noise_feature"]),
        ]
        runner = FeatureAblationRunner(groups)
        cumulative = runner.run_cumulative(
            data,
            target_col="home_win",
            train_fn=_train_fn,
            predict_fn=_predict_fn,
            top_n=2,
        )
        assert len(cumulative) == 2
        assert cumulative[0].group == "cumulative_informative"

    def test_report_structure(self):
        data = _synthetic_ablation_data()
        groups = [FeatureGroup("informative", ["informative_feature"])]
        runner = FeatureAblationRunner(groups)
        runner.run_isolated(data, target_col="home_win", train_fn=_train_fn, predict_fn=_predict_fn)
        report = runner.report()
        assert report["groups_tested"] == 1
        assert "rejected_groups" in report
        assert "results" in report

    def test_a_group_absent_from_all_features_is_skipped(self):
        # run_isolated()'s skip condition fires when a group's own features
        # aren't part of the all_features union being ablated at all --
        # ablating it would be a no-op (identical before/after), so it's
        # correctly excluded from results rather than reported as a
        # meaningless zero-delta PASS.
        data = _synthetic_ablation_data()
        groups = [
            FeatureGroup("informative", ["informative_feature"]),
            FeatureGroup("not_in_all_features", ["noise_feature"]),
        ]
        runner = FeatureAblationRunner(groups)
        results = runner.run_isolated(
            data,
            target_col="home_win",
            train_fn=_train_fn,
            predict_fn=_predict_fn,
            all_features=["informative_feature"],  # deliberately excludes noise_feature
        )
        assert {r.group for r in results} == {"informative"}


class TestPreRegisteredExperiment:
    def test_pre_registered_threshold_clears_candidate(self):
        from model_prediction.rebuild.ablation import AblationResult, PreRegisteredExperiment

        exp = PreRegisteredExperiment(
            experiment_id="exp_001",
            hypothesis="Starter rest improves OOF Brier score",
            feature_group="starter_rest",
            registered_at_utc="2026-08-22T18:00:00Z",
            registered_brier_threshold=0.002,
            registered_log_loss_threshold=0.003,
            registered_coverage_floor=0.90,
        )

        res = AblationResult(
            group="starter_rest",
            baseline_log_loss=0.680,
            ablated_log_loss=0.685,
            delta_log_loss=0.005,  # > 0.003 threshold
            baseline_brier=0.240,
            ablated_brier=0.243,
            delta_brier=0.003,  # > 0.002 threshold
            baseline_ece=0.02,
            ablated_ece=0.02,
            coverage_impact=0.02,  # 98% retained >= 90%
            fold_stability=0.001,
            verdict="PASS",
        )

        evaluation = exp.evaluate(res)
        assert evaluation["verdict"] == "PROMOTION_CANDIDATE"
        assert evaluation["brier_improvement"] == 0.003

    def test_pre_registered_threshold_rejects_underperforming_candidate(self):
        from model_prediction.rebuild.ablation import AblationResult, PreRegisteredExperiment

        exp = PreRegisteredExperiment(
            experiment_id="exp_002",
            hypothesis="Noise feature test",
            feature_group="noise",
            registered_at_utc="2026-08-22T18:00:00Z",
            registered_brier_threshold=0.002,
        )

        res = AblationResult(
            group="noise",
            baseline_log_loss=0.680,
            ablated_log_loss=0.678,
            delta_log_loss=-0.002,
            baseline_brier=0.240,
            ablated_brier=0.238,
            delta_brier=-0.002,
            baseline_ece=0.02,
            ablated_ece=0.02,
            coverage_impact=0.0,
            fold_stability=0.001,
            verdict="REJECT",
        )

        evaluation = exp.evaluate(res)
        assert evaluation["verdict"] == "REJECT"
