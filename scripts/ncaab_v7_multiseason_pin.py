"""PINNED windows, grids, selection and acceptance rules for the NCAAB v7 multi-season replay (quick 261115-m5r).

This file is committed BEFORE any outcome is joined to a multi-season component row. ``PIN_SHA256`` (the
canonical-JSON hash of ``PIN``) is printed by every stage of ``scripts/ncaab_v7_multiseason.py`` and recorded in
the frozen config; the final stage refuses to run when the frozen config carries a different hash.

Honest framing written into the pin: 2025-26 (season year 2026) shaped v7's design in quick 261115-1m7 and 2026-27
(season year 2027) was v7's holdout there. Neither is a clean holdout. 2024-25 (season year 2025) was read for
nine models per market in quick 261115-1m8 (market residual rule 1) but never for v7 or v6 as-of; it is the one
decision window, and the multiplicity below counts those earlier reads.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

PIN: dict[str, Any] = {
    "id": "ncaab-v7-multiseason-pin-1",
    "windows_by_espn_season_year": {
        "burn_in": [2022],  # 2021-22: rating history only, never scored
        "fit": [2023, 2024],  # 2022-23, 2023-24: hyperparameters, coefficients, scales
        "decision": [2025],  # 2024-25: the only window where a decision-bearing number is read
        "contaminated_2025_26": [
            2026
        ],  # shaped v7 design in 1m7; reported line-free only, labelled contaminated
        "contaminated_2026_27_partial": [2027],  # v7's 1m7 holdout (Nov 2026); line-free only, contaminated
    },
    "line_joined_metrics_policy": (
        "Every metric that joins the ESPN closing line to an outcome (cover calibration, |line| buckets, "
        "(model - line) residual tests, p >= 0.80 cover hit rate) is read on the fit window (LOSO) and the "
        "decision window 2024-25 ONLY. 2025-26 and 2026-27 carry no line x outcome x model number, so the "
        "1m8 locked windows stay unspent. Line-free metrics (RMSE and bias by games played, win-probability "
        "calibration, totals RMSE and bias) are reported on every window, the last two labelled contaminated."
    ),
    "conference_map": {
        "source": "scoreboard home/away_conference_id per season (as-of-season), id -> name table in "
        "models/ncaab_v7_multiseason.py; unknown id raises; ids 43 (D1 independents) scored at the non-D1 prior",
        "legacy_pac12_override": "id 21 through season year 2024 uses tier 1660 (ACC level), not the table's 1550",
        "membership_note": "a team's conference in season s is read from any of its season-s games: membership is "
        "public before the season, a result is never used",
        "comparators": "v6 as-of map (same information as v7) and v6 as served (today's map, "
        "v6_projections.jsonl); their 2024-25 difference is the quantified map risk",
    },
    "ridge_grid": {
        "carry_geometric_per_season_age": [0.25, 0.5, 0.75],
        "max_age_seasons": 3,
        "lambda_toward_tier_prior": [3.0, 8.0, 20.0],
        "tier_prior_scale_kappa": [1.0, 1.4],
        "n_variants": 18,
        "note": "carryover weight and offseason regression are one lever in a Massey ridge (weight of last "
        "season's games vs the pull toward the tier prior); both are varied. Venue HCA, rest and "
        "efficiency x pace are NOT re-tested (failed in 1m7).",
    },
    "ridge_variant_selection": "lowest mean over the two fit seasons of D1-vs-D1 margin RMSE of the RAW ridge "
    "(no fitted parameter, so one season is a clean test of the other); ties broken by the order carry, lambda, kappa",
    "combiner_forms": {
        "common": "D1-vs-D1 margin = f(ridge, tail) with tail = sign(v6) * max(|v6 as-of margin| - 12, 0)",
        "F1": "a * ridge + c * tail",
        "F2": "a_b * ridge + c * tail, a_b by sample bucket",
        "F3": "F2 plus an intercept d_b by sample bucket",
        "sample_buckets_for_fit": ["0", "1-2", "3-7", "8+"],
        "selection": "leave-one-season-out RMSE over the fit seasons; a more complex form must beat the simpler "
        "by more than 0.01 RMSE, else the simpler stays",
    },
    "non_d1_rule": "margin = alpha * v6_asof + beta fitted on as-of non-D1 games in the fit window; "
    "Student-t df 8 scale by MLE; the production no-pick rule (fc7ff8b8) is a separate, committed policy",
    "scale": "Student-t df 8, scale by MLE on fit residuals per sample bucket (D1) and one scale (non-D1)",
    "total_model": {
        "v7_total": "per-sample-bucket linear recalibration total = alpha_b + beta_b * v6_asof_total, fitted on "
        "fit seasons; used ONLY if its leave-one-season-out total RMSE beats v6 as-of by more than 0.05, "
        "otherwise v7_total IS v6 as-of (stated as such). Efficiency x pace is not used.",
        "scale": "Student-t df 8, scale MLE per sample bucket",
    },
    "comparators": {
        "v6_served": "v6 as served: today's conference map, existing PIT projections (mean, sd, scale = sd)",
        "v6_asof": "v6 model code unchanged, team priors / conference baselines replaced by the as-of map",
        "elo": "v6 as-of tiered-Elo margin half, raw; Student-t scale by MLE on fit D1 residuals",
        "ridge": "selected ridge variant raw (no fitted parameter); scale by MLE on fit D1 residuals",
    },
    "scorecard_a_forecast_quality_decision_window_2024_25": {
        "population": "D1-vs-D1 as-of games (all games, lined or not) unless a metric needs a line",
        "buckets_games_played": ["0", "1-2", "3-7", "8-15", "16+"],
        "buckets_abs_spread_line": ["<5", "5-10", "10-15", "15-20", ">=20"],
        "metrics": [
            "margin RMSE and bias (actual - predicted) overall, by games played, by |line| bucket",
            "total RMSE and bias overall and by games played",
            "home-cover Brier and reliability of the favoured side (lined games, pushes excluded)",
            "hit rate of the favoured cover side at p >= 0.80 over ALL lined games (not just picked ones)",
            "home win-probability Brier and hit rate at p >= 0.80 over all games",
        ],
        "bootstrap": {"resamples": 4000, "cluster": "game date", "seed": 17, "interval": "90% percentile"},
        "pass_if_all": [
            "(i) v7 minus v6 as-of D1 margin RMSE: bootstrap upper bound < 0",
            "(ii) v7 home-win Brier minus v6 as-of: point estimate <= 0",
            "(iii) v7 RMSE below v6 as-of in games-played bucket 0-2 (point estimate)",
        ],
        "also_reported_not_gating": [
            "p >= 0.80 spread-cover hit rate within 0.05 of mean p with n >= 50",
            "|line| >= 20 bias: |v7| < |v6 as-of|",
        ],
    },
    "scorecard_b_market_relative": {
        "rule_id": "ncaab-v7-market-rule-2",
        "inherits": "ncaab-residual-rule-1 (sha256 58c373445cdaef65f96fab3a29f461de249d0bcab1824c0b876d53ab297f96dc): "
        "ridge alpha 1000 on the standardised single feature x = model - line, 0.5 shrink, target actual - "
        "line, fit-window standardisation and coefficients, bootstrap 4000 resamples by game date seed 17, "
        "Bonferroni alpha = 0.025 / K per tail",
        "fit_window_predictions": "leave-one-season-out v7 predictions (each fit season predicted by a config "
        "fitted on the other), because v7's coefficients were fitted on those outcomes",
        "validation_window": "2024-25 only",
        "family_spread": ["v7", "ridge", "v6_asof"],
        "family_total": ["v6_asof_total", "v7_total"],
        "multiplicity": "K = 9 models already read on 2024-25 per market (1m8) + new models: spread 9 + 3 = 12, "
        "total 9 + 2 = 11 (v7_total counted even if it equals v6 as-of)",
        "accept_if_all": [
            (
                "(a) K-adjusted bootstrap lower bound of the validation OLS slope of (actual - line) on the shrunk "
                "prediction is above zero"
            ),
            (
                "(b) K-adjusted bootstrap lower bound of the out-of-sample MSE delta (line-only baseline MSE - model "
                "MSE) is above zero"
            ),
        ],
        "also_reported": "plain 95% slope CI of (actual - line) on (model - line); side record |pred| >= 1",
        "if_not_accepted": "NO PICK / no edge over the line; the contaminated windows are not scored line-joined",
        "no_locked_window_score": "2025-26 and 2026-27 are never line-joined here, even if accepted",
    },
    "mismatch_cohort": {
        "definition": "both teams D1 as-of, non-neutral, home tier prior >= 1550 and away tier prior <= 1400 "
        "(power or upper mid-major host vs a low-major visitor)",
        "metrics": "margin bias and RMSE, p >= 0.80 cover hit rate (2024-25 lined), win-prob calibration; "
        "line-free margin bias and RMSE on the contaminated windows; the settled-ledger replay",
        "verdict_rule": "the fix is claimed only if v7 bias and RMSE in the cohort are both smaller in absolute "
        "terms than v6 as-of on 2024-25 AND the p >= 0.80 cover hit rate is not below v6 as-of's",
    },
    "non_d1_ledger_check": "settled Flat rows of ncaab-spread-v6 and ncaab-total-v6 (read-only runtime ledger); a "
    "row would be declined when either team name is absent from v6 team_priors (the committed fc7ff8b8 predicate)",
    "promotion": "NOT done here. A promotion question is asked only if scorecard A passes AND scorecard B accepts "
    "or the operator asks for a forecast-quality-only upgrade; the exact command is printed, never run.",
}

PIN_SHA256 = hashlib.sha256(json.dumps(PIN, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

if __name__ == "__main__":
    print("pin id", PIN["id"])
    print("PIN_SHA256", PIN_SHA256)
