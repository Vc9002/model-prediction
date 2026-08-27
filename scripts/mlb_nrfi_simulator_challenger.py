"""MLB NRFI: PA-level simulator challenger (plan challenger 3).

Reuses the half-inning challenger's split/production flow (frozen priors,
60/20/20) and evaluates the seeded plate-appearance simulator from
``models.mlb_first_inning_sim`` on the same locked test rows: P(NRFI)
from 2,000 simulated innings per game, versus the incumbent classifier.
The simulator carries no information the ledger features don't already
encode — the research question is whether the generative structure
itself (explicit outs, base state, per-PA outcomes) produces better
calibrated probabilities than the flat logistic.

Research-only: no promotion, no ledger writes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mlb_nrfi_half_inning_challenger import (
    REPRODUCTION_TOLERANCE,
    SHIPPED_HOLDOUT_LOGLOSS,
    _chronological_split,
    _logloss,
    _paired_logloss_delta_ci,
)

from model_prediction.domain import parse_utc
from model_prediction.models.mlb_first_inning import (
    DEFAULT_SNAPSHOT_PATH,
    MLBFirstInningModel,
    build_first_inning_ledger,
    compute_first_inning_priors,
)
from model_prediction.models.mlb_first_inning_sim import (
    DEFAULT_SIMS_PER_GAME,
    first_inning_run_distribution,
    nrfi_probability,
    simulate_first_inning,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshots", default=str(DEFAULT_SNAPSHOT_PATH))
    parser.add_argument("--sims", type=int, default=DEFAULT_SIMS_PER_GAME)
    args = parser.parse_args()

    probe_rows = build_first_inning_ledger(args.snapshots)
    probe_train, _probe_val, _probe_test = _chronological_split(probe_rows)
    train_end = probe_train[-1].game_start_utc
    priors = compute_first_inning_priors(args.snapshots, end_utc=parse_utc(train_end))
    rows = build_first_inning_ledger(args.snapshots, priors=priors)

    train, val, test = _chronological_split(rows)
    outcomes = [r.nrfi for r in test]
    dates = [r.game_start_utc[:10] for r in test]

    inc = MLBFirstInningModel().fit(train)
    inc_preds = [inc.predict_p_nrfi(r) for r in test]
    inc_logloss = _logloss(inc_preds, outcomes)
    drift = abs(inc_logloss - SHIPPED_HOLDOUT_LOGLOSS)

    sim_preds = []
    sims_seed = 20260827
    for i, row in enumerate(test):
        sims = simulate_first_inning(row, n_sims=args.sims, seed=sims_seed + i)
        sim_preds.append(nrfi_probability(sims))
        if i == 0:
            sample_dist = first_inning_run_distribution(sims)

    report = {
        "n_snapshots": len(rows),
        "split": {"train": len(train), "validation": len(val), "test": len(test)},
        "sims_per_game": args.sims,
        "reproduction_gate": {
            "holdout_logloss": round(inc_logloss, 6),
            "shipped": SHIPPED_HOLDOUT_LOGLOSS,
            "drift": round(drift, 6),
            "gate": "PASS" if drift <= REPRODUCTION_TOLERANCE else "DRIFT — comparison void",
        },
        "incumbent_logloss": round(inc_logloss, 6),
        "simulator_logloss": round(_logloss(sim_preds, outcomes), 6),
        "simulator_minus_incumbent_logloss_95ci": _paired_logloss_delta_ci(
            inc_preds, sim_preds, outcomes, dates
        ),
        "simulator_mean_p_nrfi": round(sum(sim_preds) / len(sim_preds), 4),
        "actual_nrfi_rate_on_test": round(sum(outcomes) / len(outcomes), 4),
        "sample_run_distribution": {str(runs): round(prob, 4) for runs, prob in sorted(sample_dist.items())},
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
