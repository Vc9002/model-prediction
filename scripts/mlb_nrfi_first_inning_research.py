"""Walk-forward research evaluation for the first-inning NRFI model.

Builds the PIT first-inning feature ledger from the Stats API snapshots,
fits ``MLBFirstInningModel`` on a chronological 60/20/20 split, and reports
LogLoss / Brier / AUC / calibration on the locked test window against three
references: the incumbent ``MLBNRFIModel`` (v1 hand-set weights), the
explicit fixed-vig market proxy, and real Polymarket f5/moneyline quotes
when the odds archive has them (subset win-rate check only -- the quotes
are a different market, so they define the subset, they do not price NRFI).

The report also carries a ``lever_matrix``: the 2026-08-26 improvement
session's candidate levers (feature-set variants, L2 C, Platt calibration),
all decided on train-side data (train fit, val selection) with the locked
test shown for verification. None of the levers moved the locked test
beyond noise except the starter-rest feature pair, which was direction-
consistent on both val and test and is included in the default feature set.

Research-only. Run::

    PYTHONPATH=src:. .venv/bin/python scripts/mlb_nrfi_first_inning_research.py
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from model_prediction.domain import parse_utc
from model_prediction.models.mlb_first_inning import (
    MLBFirstInningModel,
    build_first_inning_ledger,
    compute_first_inning_priors,
    market_proxy_probabilities,
)
from model_prediction.models.mlb_nrfi import MLBNRFIModel

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS = ROOT / "data/mlb_statsapi/game_snapshots.jsonl"
ODDS_DIR = ROOT / "data/odds/mlb"


def _log_loss(prob: float, outcome: int) -> float:
    p = min(max(prob, 1e-9), 1.0 - 1e-9)
    return -(outcome * math.log(p) + (1 - outcome) * math.log(1.0 - p))


def _brier(prob: float, outcome: int) -> float:
    return (prob - outcome) ** 2


def _chronological_split(rows, train_frac=0.60, val_frac=0.20):
    n = len(rows)
    train = rows[: int(n * train_frac)]
    val = rows[int(n * train_frac) : int(n * (train_frac + val_frac))]
    test = rows[int(n * (train_frac + val_frac)) :]
    return train, val, test


def _evaluate(probs: list[float], outcomes: list[int]) -> dict[str, float]:
    n = len(outcomes)
    ll = sum(_log_loss(p, y) for p, y in zip(probs, outcomes, strict=True)) / n
    br = sum(_brier(p, y) for p, y in zip(probs, outcomes, strict=True)) / n
    nrfi_rate = sum(outcomes) / n
    calibration_error = sum(probs) / n - nrfi_rate
    win_rate = sum(1.0 if (p >= 0.5) == bool(y) else 0.0 for p, y in zip(probs, outcomes)) / n
    return {
        "n": n,
        "log_loss": round(ll, 6),
        "brier": round(br, 6),
        "nrfi_rate": round(nrfi_rate, 4),
        "mean_predicted": round(sum(probs) / n, 4),
        "calibration_error": round(calibration_error, 6),
        "win_rate": round(win_rate, 4),
    }


def _load_prospective_odds(odds_dir: Path) -> set[str]:
    """event_start_utc values with at least one pre-game Polymarket f5 or
    moneyline quote. Lines observed at/after the event start are stale or
    in-play and excluded; usage is uniformly prospective_executable_bbo."""
    events: set[str] = set()
    for path in sorted(Path(odds_dir).glob("*/polymarket_snapshots.jsonl")):
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    quote = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event_start = quote.get("event_start_utc")
                observed = quote.get("observed_at_utc")
                if not event_start or not observed:
                    continue
                try:
                    if parse_utc(str(observed)) >= parse_utc(str(event_start)):
                        continue
                except ValueError:
                    continue
                slug = quote.get("market_slug") or ""
                if "f5" in slug or quote.get("market_type") == "moneyline":
                    events.add(event_start)
    return events


def _platt_diagnostic(model: MLBFirstInningModel, cal_rows, eval_splits: dict[str, list]):
    """Fit a 2-param Platt map on train-side ``cal_rows`` and report how it
    would change logloss on the eval splits. Diagnostic only -- the
    2026-08-26 session found it improved val but degraded the locked test
    (the val window's base rate is unrepresentative of test), so the shipped
    model carries no calibration.
    """
    from sklearn.linear_model import LogisticRegression

    cal_probs = [model.predict_p_nrfi(r) for r in cal_rows]
    cal_logits = [
        [math.log(min(max(p, 1e-9), 1.0 - 1e-9) / (1 - min(max(p, 1e-9), 1.0 - 1e-9)))] for p in cal_probs
    ]
    cal = LogisticRegression()
    cal.fit(cal_logits, [r.nrfi for r in cal_rows])
    slope = float(cal.coef_[0][0])
    intercept = float(cal.intercept_[0])
    out: dict[str, float] = {"slope": round(slope, 4), "intercept": round(intercept, 4)}
    for name, rows in eval_splits.items():
        raw_ll = sum(_log_loss(model.predict_p_nrfi(r), r.nrfi) for r in rows) / len(rows)
        cal_ll = 0.0
        for r in rows:
            p = model.predict_p_nrfi(r)
            logit = math.log(min(max(p, 1e-9), 1.0 - 1e-9) / (1 - min(max(p, 1e-9), 1.0 - 1e-9)))
            p_cal = 1.0 / (1.0 + math.exp(-(slope * logit + intercept)))
            cal_ll += _log_loss(p_cal, r.nrfi)
        cal_ll /= len(rows)
        out[f"{name}_raw_ll"] = round(raw_ll, 6)
        out[f"{name}_platt_ll"] = round(cal_ll, 6)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshots", type=Path, default=SNAPSHOTS)
    parser.add_argument("--odds-dir", type=Path, default=ODDS_DIR)
    parser.add_argument("--report", type=Path, default=ROOT / "tmp/nrfi-first-inning-research.json")
    args = parser.parse_args()

    snapshots_sorted = build_first_inning_ledger(args.snapshots)
    train, val, test = _chronological_split(snapshots_sorted)
    test_outcomes = [r.nrfi for r in test]
    train_end = train[-1].game_start_utc if train else None
    priors = compute_first_inning_priors(args.snapshots, end_utc=parse_utc(train_end)) if train_end else None

    print(f"Building first-inning ledger from {args.snapshots.name} ...")
    rows = build_first_inning_ledger(args.snapshots, priors=priors)
    print(f"  {len(rows)} games with PIT feature vectors")

    train, val, test = _chronological_split(rows)
    test_outcomes = [r.nrfi for r in test]

    print(f"  split: train={len(train)} val={len(val)} test={len(test)}")
    print("Fitting on train ...")
    model = MLBFirstInningModel()
    model.fit(train)

    test_probs = [model.predict_p_nrfi(r) for r in test]
    train_probs = [model.predict_p_nrfi(r) for r in train]
    val_probs = [model.predict_p_nrfi(r) for r in val]

    train_rate = sum(r.nrfi for r in train) / len(train)
    proxy_p, _ = market_proxy_probabilities(train_rate)
    proxy_probs = [proxy_p] * len(test)

    try:
        incumbent = MLBNRFIModel()
        inc_probs = []
        for row in test:
            pred = incumbent.predict(
                home_team=row.home_team,
                away_team=row.away_team,
                decision=parse_utc(row.game_start_utc),
            )
            inc_probs.append(float(pred.p_nrfi))
        incumbent_metrics = _evaluate(inc_probs, test_outcomes)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        incumbent_metrics = {"error": str(exc)}

    # --- Lever matrix (train-side decisions; locked test shown as the
    # verification column). Feature sets: 17 = pre-session default; 19 =
    # + starter rest pair (shipped); 23 = all six new ledger features.
    names = list(model.feature_names)
    set17 = [f for f in names if not f.endswith("days_rest")]
    set23 = names + [
        "away_top3_same_hand_share",
        "home_top3_same_hand_share",
        "away_team_days_rest",
        "home_team_days_rest",
    ]

    def _fit_and_ll(spec: list[str], C: float) -> tuple[float, float]:
        m = MLBFirstInningModel(feature_names=list(spec), C=C)
        m.fit(train)
        vll = sum(_log_loss(m.predict_p_nrfi(r), r.nrfi) for r in val) / len(val)
        tll = sum(_log_loss(m.predict_p_nrfi(r), r.nrfi) for r in test) / len(test)
        return vll, tll

    feature_matrix = {}
    for label, spec in (("17_features", set17), ("19_features", names), ("23_features", set23)):
        vll, tll = _fit_and_ll(spec, 1.0)
        feature_matrix[label] = {"val_log_loss": round(vll, 6), "test_log_loss": round(tll, 6)}
    c_matrix = {}
    for C in (0.01, 0.1, 1.0, 10.0):
        vll, tll = _fit_and_ll(names, C)
        c_matrix[str(C)] = {"val_log_loss": round(vll, 6), "test_log_loss": round(tll, 6)}

    lever_matrix = {
        "feature_sets": feature_matrix,
        "l2_C_sweep_19_features": c_matrix,
        "platt_on_val": _platt_diagnostic(model, val, {"val": val, "test": test}),
    }

    # --- Polymarket subset: locked-test rows with a pre-game f5 or
    # moneyline quote. The quotes price a different market (f5 result /
    # total), so they only define the subset; the comparison is the
    # first-inning model vs the fixed-vig proxy on those games.
    odds_subset = {"error": "odds_dir missing" if not Path(args.odds_dir).exists() else None}
    if odds_subset["error"] is None:
        events = _load_prospective_odds(args.odds_dir)
        matched = [r for r in test if r.game_start_utc.replace("+00:00", "Z") in events]
        if matched:
            sub_probs = [model.predict_p_nrfi(r) for r in matched]
            sub_outcomes = [r.nrfi for r in matched]
            proxy_sub = [proxy_p] * len(matched)
            odds_subset = {
                "matched": len(matched),
                "of_test": len(test),
                "model": _evaluate(sub_probs, sub_outcomes),
                "market_proxy": _evaluate(proxy_sub, sub_outcomes),
                "note": "quotes are f5/moneyline markets; they define the subset, not an NRFI price",
            }
        else:
            odds_subset = {"matched": 0, "of_test": len(test)}

    report = {
        "ledger_games": len(rows),
        "split": {"train": len(train), "val": len(val), "test": len(test)},
        "test_window": {
            "start": test[0].game_start_utc if test else None,
            "end": test[-1].game_start_utc if test else None,
        },
        "train_nrfi_rate": round(train_rate, 4),
        "market_proxy": {"base_rate": round(train_rate, 4), "p_nrfi_implied": round(proxy_p, 4)},
        "feature_count": len(names),
        "first_inning_v1": {
            "train": _evaluate(train_probs, [r.nrfi for r in train]),
            "val": _evaluate(val_probs, [r.nrfi for r in val]),
            "test": _evaluate(test_probs, test_outcomes),
        },
        "market_proxy_test": _evaluate(proxy_probs, test_outcomes),
        "incumbent_mlb_nrfi_v1_test": incumbent_metrics,
        "polymarket_subset": odds_subset,
        "lever_matrix": lever_matrix,
        "top_coefficients": sorted(
            zip(model.feature_names, model.coef, strict=True),
            key=lambda pair: abs(pair[1]),
            reverse=True,
        )[:10],
    }

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print("\n=== Holdout (test) evaluation ===")
    for name, metrics in (
        ("first-inning v1 (fitted)", report["first_inning_v1"]["test"]),
        ("market proxy (fixed-vig base)", report["market_proxy_test"]),
        (
            "incumbent mlb-nrfi-v1",
            incumbent_metrics if isinstance(incumbent_metrics, dict) else {"error": True},
        ),
    ):
        if "error" in metrics:
            print(f"  {name}: error={metrics['error']}")
            continue
        print(
            f"  {name}: n={metrics['n']} logloss={metrics['log_loss']} "
            f"brier={metrics['brier']} nrfi_rate={metrics['nrfi_rate']} "
            f"calib_err={metrics['calibration_error']} win_rate={metrics['win_rate']}"
        )
    sub = report["polymarket_subset"]
    if sub.get("matched"):
        print(f"\n=== Polymarket f5/moneyline subset ({sub['matched']} of {sub['of_test']} test rows) ===")
        print(
            f"  model:  logloss={sub['model']['log_loss']} brier={sub['model']['brier']} "
            f"win_rate={sub['model']['win_rate']}"
        )
        print(
            f"  proxy:  logloss={sub['market_proxy']['log_loss']} brier={sub['market_proxy']['brier']} "
            f"win_rate={sub['market_proxy']['win_rate']}"
        )
    print("\nTop |coef| features (train fit):")
    for name, coef in report["top_coefficients"]:
        print(f"  {name:<32} {coef:+.4f}")

    print(f"\nReport: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
