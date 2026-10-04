"""Train an isolated research generation for all current production markets.

Run from the repository root with PYTHONPATH=src:. No production configuration,
historical source, ledger, or incumbent artifact is modified. Existing output
directories are refused to preserve prior experiments.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from model_prediction.domain import parse_utc
from model_prediction.research_generation import (
    FEATURE_NAMES,
    ResearchRow,
    build_rows,
    file_hash,
    load_games,
    partition,
    source_path,
    train_candidate,
)


def nrfi_rows(root: Path, as_of: str) -> tuple[list[ResearchRow], tuple[str, ...], dict[str, Any]]:
    from model_prediction.models.mlb_first_inning import (
        FEATURE_NAMES as NRFI_FEATURES,
    )
    from model_prediction.models.mlb_first_inning import (
        build_first_inning_ledger,
        compute_first_inning_priors,
    )

    path = root / "data/mlb_statsapi/game_snapshots.jsonl"
    initial = [r for r in build_first_inning_ledger(path) if r.game_start_utc[:10] < as_of]

    def convert(source: Any) -> list[ResearchRow]:
        return sorted(
            [
                ResearchRow(
                    str(r.game_pk),
                    r.game_start_utc[:10],
                    tuple(float(r.features[n]) for n in NRFI_FEATURES),
                    int(r.nrfi),
                    0.0,
                    float(r.runs_1st_total),
                    "UNVERIFIED_LEGACY_FEATURE_BUILDER",
                )
                for r in source
                if r.game_start_utc[:10] < as_of
            ],
            key=lambda r: (r.date, r.event_id),
        )

    rows = convert(initial)
    masks = partition(rows)
    train_end = rows[masks["train"][-1]].date
    # League priors use only the training partition. Advance to midnight after
    # train_end so all games from the complete training day are included.
    from datetime import timedelta

    prior_cutoff = parse_utc(train_end + "T00:00:00+00:00") + timedelta(days=1)
    priors = compute_first_inning_priors(path, end_utc=prior_cutoff)
    rebuilt = convert(build_first_inning_ledger(path, priors=priors))
    if [(r.event_id, r.date) for r in rows] != [(r.event_id, r.date) for r in rebuilt]:
        raise ValueError("nrfi_cohort_changed_after_prior_fit")
    return (
        rebuilt,
        tuple(NRFI_FEATURES),
        {
            "source": str(path),
            "source_sha256": file_hash(path),
            "evidence_origin": "historical_backtest",
            "priors": priors,
            "prior_training_end": train_end,
            "historical_observation_times_verified": False,
            "limitation": "Legacy snapshot feature builder; full input lineage and historical lineup observability require a separate audit.",
        },
    )


def run(root: Path, output: Path, sports: set[str] | None = None) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    production = root / "config/production.yaml"
    config = yaml.safe_load(production.read_text())["prediction_service"]
    champions = config["champions"]
    before = {str(p.relative_to(root)): file_hash(p) for p in (root / "config/models").rglob("*.json")}
    before["config/production.yaml"] = file_hash(production)
    as_of = datetime.now(UTC).date().isoformat()
    manifest: dict[str, Any] = {
        "started_at_utc": datetime.now(UTC).isoformat(),
        "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "code_files_sha256": {
            str(p.relative_to(root)): file_hash(p)
            for p in (Path(__file__).resolve(), root / "src/model_prediction/research_generation.py")
        },
        "operator_directive": "2026-09-08/09: improve prediction accuracy and profitability for all current models; old frozen protocols obsolete.",
        "protocol": "historical_development_v1_60_10_10_20_complete_dates",
        "evidence_origin": "historical_backtest",
        "as_of_utc_date_exclusive": as_of,
        "incumbent_superiority_claimed": False,
        "profitability_claimed": False,
        "prior_test_exposure": "Unknown; historical evaluation is not new prospective confirmation.",
        "production_files_before": before,
        "models": [],
        "data_audits": {},
    }
    manifest_path = output / "manifest.json"

    def checkpoint() -> None:
        manifest_path.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n")

    checkpoint()
    for sport, markets in champions.items():
        if sports and sport not in sports:
            continue
        print(f"Loading {sport} ...", flush=True)
        try:
            games, audit = load_games(source_path(root, sport), sport, as_of)
            rows = build_rows(games, sport)
            audit["feature_rows"] = len(rows)
            audit["cold_start_excluded"] = len(games) - len(rows)
            audit["conflict_policy"] = "All versions of conflicting events excluded."
            manifest["data_audits"][sport] = audit
            feature_dir = output / "features"
            feature_dir.mkdir(exist_ok=True)
            with (feature_dir / f"{sport.lower()}.jsonl").open("w") as handle:
                for row in rows:
                    handle.write(json.dumps(asdict(row), allow_nan=False) + "\n")
        except (ValueError, OSError, KeyError, TypeError) as exc:
            manifest["data_audits"][sport] = {"status": "FAILED", "reason": str(exc)}
            for market, incumbent in markets.items():
                manifest["models"].append(
                    {
                        "sport": sport,
                        "market": market,
                        "incumbent": incumbent,
                        "status": "FAILED_DATA",
                        "reason": str(exc),
                    }
                )
            checkpoint()
            continue
        for market, incumbent in markets.items():
            print(f"Training {sport}/{market}: {len(rows)} historical rows ...", flush=True)
            try:
                market_rows, names, metadata = (
                    nrfi_rows(root, as_of) if market == "nrfi" else (rows, FEATURE_NAMES, audit)
                )
                report = train_candidate(
                    market_rows,
                    sport,
                    market,
                    incumbent,
                    output / "models" / f"{sport.lower()}-{market}",
                    names,
                    metadata,
                )
                print(f"  {report['selected_variant']} | {report['metrics']}", flush=True)
            except (ValueError, OSError, KeyError, TypeError, RuntimeError) as exc:
                report = {
                    "sport": sport,
                    "market": market,
                    "incumbent": incumbent,
                    "status": "FAILED_TRAINING",
                    "reason": str(exc),
                }
                print(f"  FAILED: {exc}", flush=True)
            manifest["models"].append(report)
            checkpoint()
    after = {name: file_hash(root / name) for name in before}
    manifest["production_artifacts_unchanged"] = before == after
    manifest["completed_at_utc"] = datetime.now(UTC).isoformat()
    manifest["trained_markets"] = sum(r["status"] == "TRAINED_RESEARCH_ONLY" for r in manifest["models"])
    manifest["expected_markets"] = sum(
        len(ms) for sport, ms in champions.items() if not sports or sport in sports
    )
    checkpoint()
    if before != after:
        raise RuntimeError(
            "Production files changed during run; inspect concurrent writers before relying on snapshot."
        )
    lines = [
        "# New research generation",
        "",
        "Historical development candidates; none promoted.",
        "",
        "Exact incumbent reproduction and executable-price economics are UNVERIFIED for every candidate.",
        "The reference below is a constant training prior/median, not the production champion.",
        "Previously inspected historical dates are not fresh prospective confirmation.",
        "",
        "| Sport | Market | Candidate | Test rows | Diagnostic | Constant reference |",
        "|---|---|---|---:|---|---|",
    ]
    for report in manifest["models"]:
        metrics = report.get("metrics", {})
        classification = "log_loss" in metrics
        metric = "log_loss" if classification else "mae"
        reference = "constant_prior_log_loss" if classification else "constant_median_mae"
        lines.append(
            f"| {report['sport']} | {report['market']} | {report.get('selected_variant', report['status'])} | "
            f"{report.get('splits', {}).get('test', {}).get('n', 0)} | {metric}: {metrics.get(metric, 'unavailable')} | "
            f"{metrics.get(reference, 'unavailable')} |"
        )
    lines += [
        "",
        "Next: reproduce each exact incumbent on common decision contexts, audit source observation lineage,",
        "and bind decision-time contract/quote/fee evidence before calculating economic qualification.",
        "Do not select a different candidate using this historical test report.",
        "Changes motivated by these results require a new development generation and future confirmation.",
        "",
    ]
    (output / "REPORT.md").write_text("\n".join(lines))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sport", action="append", help="Optional sport subset; default is all 21 markets.")
    args = parser.parse_args()
    result = run(
        args.root.resolve(), args.output.resolve(), {s.upper() for s in args.sport} if args.sport else None
    )
    print(f"Trained {result['trained_markets']}/{result['expected_markets']} markets. No promotions.")
    return 0 if result["trained_markets"] == result["expected_markets"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
