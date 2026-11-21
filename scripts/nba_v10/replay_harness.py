"""NBA research replay harness CLI (exploratory; train + validation only).

  python scripts/nba_v10/replay_harness.py golden          # replay v9, write/diff golden rows
  python scripts/nba_v10/replay_harness.py replay NAME...  # replay named engines, cache rows
  python scripts/nba_v10/replay_harness.py score NAME...   # scorecards from cached rows
  python scripts/nba_v10/replay_harness.py ablate NAME...  # compact train/val table
  python scripts/nba_v10/replay_harness.py fit NAME...     # B6/B7 fits on train rows
  python scripts/nba_v10/replay_harness.py g1              # gate G1 + nba-anchor-v10 artifact
  python scripts/nba_v10/replay_harness.py v91-artifacts   # write the v9.1 research artifacts

Inputs are read from the main data root (--data-root, default E:/MP/data). Cached replay
rows go to <data-root>/research/nba_v10/harness/ (untracked); the golden v9 rows and the
reports are written under outputs/nba/v10_harness/ in this repo.

Do not run heavy replays between 00:20 and 01:20 ET (scheduled runs crash on low memory).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from model_prediction.domain import EASTERN
from model_prediction.features.base import FeatureStore
from model_prediction.models.nba_v91 import POSTSEASON_TYPES
from model_prediction.nba_replay_harness import (
    DEFAULT_LINES_PATH,
    EngineSpec,
    G1Result,
    assign_splits,
    diff_golden,
    file_sha256,
    gate_g1,
    golden_rows,
    load_closing_lines,
    read_rows,
    replay,
    replay_dates,
    score_engine,
    scorecard_markdown,
    split_boundaries,
    write_rows,
)
from model_prediction.research_incumbent_evaluation import POLICY

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "outputs" / "nba" / "v10_harness"
GOLDEN = OUT_DIR / "golden_v9_rows.jsonl"
GOLDEN_META = OUT_DIR / "golden_v9_meta.json"


def _blackout_guard() -> None:
    now = datetime.now(EASTERN)
    minutes = now.hour * 60 + now.minute
    if 20 <= minutes <= 80:
        sys.exit(f"refusing heavy replay at {now:%H:%M} ET (00:20-01:20 blackout)")


def _engines() -> dict[str, EngineSpec]:
    from model_prediction.nba_v10_engines import engine_registry

    return engine_registry(REPO)


def _cache_dir(data_root: Path) -> Path:
    return data_root / "research" / "nba_v10" / "harness"


def _inputs(data_root: Path) -> tuple[FeatureStore, dict[str, Any], dict[str, str]]:
    store = FeatureStore(data_root)
    lines_path = data_root / DEFAULT_LINES_PATH.relative_to("data")
    lines = load_closing_lines(lines_path)
    meta = {
        "games_jsonl": str(store.processed_path("nba")),
        "games_sha256": file_sha256(store.processed_path("nba")),
        "lines_jsonl": str(lines_path),
        "lines_sha256": file_sha256(lines_path),
    }
    return store, lines, meta


# Pinned 2026-11-20 from split_boundaries over every replayable date (same rule as the review
# diagnostics: 2,049 train / 762 validation games). games.jsonl grows daily, which would slide a
# recomputed boundary; the study keeps these fixed and only reports drift.
PINNED_VAL_START = "2025-11-03"
PINNED_HOLDOUT_START = "2026-02-23"


def _boundaries(store: FeatureStore) -> tuple[str, str]:
    all_dates = list(replay_dates(store, end_date="9999-12-31"))
    dates_with_history = [d for d in all_dates if len(store.games_before("nba", d)) >= 200]
    computed = split_boundaries(dates_with_history)
    if computed != (PINNED_VAL_START, PINNED_HOLDOUT_START):
        print(f"note: recomputed split {computed} differs from pinned; using pinned", flush=True)
    return PINNED_VAL_START, PINNED_HOLDOUT_START


def cmd_replay(names: list[str], data_root: Path) -> dict[str, list[dict[str, Any]]]:
    _blackout_guard()
    registry = _engines()
    store, lines, meta = _inputs(data_root)
    t0 = time.time()
    rows = replay(store, [registry[n] for n in names], lines, progress=lambda d: print(d, flush=True))
    print(f"replayed {names} in {time.time() - t0:.0f}s", flush=True)
    for name, engine_rows in rows.items():
        path = _cache_dir(data_root) / f"{name}.jsonl"
        write_rows(path, engine_rows)
        (path.with_suffix(".meta.json")).write_text(json.dumps(meta, indent=2))
        print(f"{name}: {len(engine_rows)} rows -> {path}")
    return rows


def cmd_golden(data_root: Path) -> None:
    rows = cmd_replay(["v9"], data_root)["v9"]
    _, _, meta = _inputs(data_root)
    golden = golden_rows(rows)
    if GOLDEN.exists():
        result = diff_golden(read_rows(GOLDEN), golden)
        print("golden diff:", result)
        return
    write_rows(GOLDEN, golden)
    GOLDEN_META.write_text(json.dumps({**meta, "rows": len(golden)}, indent=2) + "\n")
    print(f"wrote golden {len(golden)} rows")


def cmd_diff(names: list[str], data_root: Path) -> None:
    golden = read_rows(GOLDEN)
    for name in names:
        print(name, diff_golden(golden, golden_rows(read_rows(_cache_dir(data_root) / f"{name}.jsonl"))))


def cmd_score(names: list[str], data_root: Path, draws: int) -> None:
    store = FeatureStore(data_root)
    val_start, holdout_start = _boundaries(store)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name in names:
        rows = read_rows(_cache_dir(data_root) / f"{name}.jsonl")
        card = score_engine(rows, val_start, holdout_start, draws=draws)
        md = scorecard_markdown(name, card)
        (OUT_DIR / f"scorecard_{name}.md").write_text(md, encoding="utf-8")
        print(md)


def _split_rows(name: str, data_root: Path) -> dict[str, list[dict[str, Any]]]:
    rows = read_rows(_cache_dir(data_root) / f"{name}.jsonl")
    assign_splits(rows, PINNED_VAL_START, PINNED_HOLDOUT_START)
    return {
        "train": [r for r in rows if r["split"] == "train"],
        "val": [r for r in rows if r["split"] == "val"],
    }


def _metrics(rows: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for market, sign in (("spread", -1), ("total", 1)):
        act = np.array([r["home_score"] + sign * r["away_score"] for r in rows], float)
        key = "model_margin" if market == "spread" else "model_total"
        line_key = "line_margin" if market == "spread" else "line_total"
        prob_key = "p_away_cover" if market == "spread" else "p_over"
        model = np.array([r[key] for r in rows], float)
        line = np.array([r[line_key] for r in rows], float)
        p = np.array([r[prob_key] for r in rows], float)
        hit = (act < line) if market == "spread" else (act > line)
        keep = act != line
        out[f"{market}_rmse"] = float(np.sqrt(np.mean((act - model) ** 2)))
        out[f"{market}_bias"] = float(np.mean(act - model))
        out[f"{market}_brier"] = float(np.mean((p[keep] - hit[keep]) ** 2))
        out[f"{market}_corr"] = float(np.corrcoef(model - line, act - line)[0, 1])
    return out


def cmd_ablate(names: list[str], data_root: Path) -> None:
    """Compact train/validation table: RMSE, bias (actual - model), Brier, corr per market."""
    header = (
        "| engine | split | n | spread RMSE | spread bias | spread Brier | spread corr | total RMSE | "
        "total bias | total Brier | total corr |"
    )
    lines = [header, "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name in names:
        for split, rows in _split_rows(name, data_root).items():
            m = _metrics(rows)
            lines.append(
                f"| {name} | {split} | {len(rows)} | {m['spread_rmse']:.3f} | {m['spread_bias']:+.2f} | "
                f"{m['spread_brier']:.4f} | {m['spread_corr']:+.3f} | {m['total_rmse']:.3f} | "
                f"{m['total_bias']:+.2f} | {m['total_brier']:.4f} | {m['total_corr']:+.3f} |"
            )
    print("\n".join(lines))


def fit_fatigue(train: list[dict[str, Any]]) -> dict[str, float]:
    """B6: symmetric per-team fatigue points, fitted on TRAIN regular-season rows of a
    ``fatigue_mode=fitted`` replay with all penalties at 0.

    Total residual ~ (def - off) * tired_count; margin residual ~ (off + def) * (away_tired -
    home_tired). Intercepts absorb level bias and are not applied.
    """
    rows = [r for r in train if r["season_type"] not in POSTSEASON_TYPES]
    b = [r["basis"] for r in rows]
    hb = np.array([x["home_b2b"] for x in b], float)
    ab = np.array([x["away_b2b"] for x in b], float)
    h34 = np.array([x["home_3in4"] and not x["home_b2b"] for x in b], float)
    a34 = np.array([x["away_3in4"] and not x["away_b2b"] for x in b], float)
    r_total = np.array([r["home_score"] + r["away_score"] - r["model_total"] for r in rows], float)
    r_margin = np.array([r["home_score"] - r["away_score"] - r["model_margin"] for r in rows], float)
    ones = np.ones(len(rows))
    c_total = np.linalg.lstsq(np.column_stack([ones, hb + ab, h34 + a34]), r_total, rcond=None)[0]
    c_margin = np.linalg.lstsq(np.column_stack([ones, ab - hb, a34 - h34]), r_margin, rcond=None)[0]
    fit = {
        "b2b_offense_penalty": float((c_margin[1] - c_total[1]) / 2.0),
        "b2b_defense_penalty": float((c_margin[1] + c_total[1]) / 2.0),
        "three_in_four_offense_penalty": float((c_margin[2] - c_total[2]) / 2.0),
        "three_in_four_defense_penalty": float((c_margin[2] + c_total[2]) / 2.0),
    }
    print(
        f"B6 fit on {len(rows)} train games: total coef b2b {c_total[1]:+.3f} 3in4 {c_total[2]:+.3f}; "
        f"margin coef b2b {c_margin[1]:+.3f} 3in4 {c_margin[2]:+.3f}; b2b games home {int(hb.sum())} "
        f"away {int(ab.sum())}"
    )
    return {k: round(v, 3) for k, v in fit.items()}


def fit_playoff_offset(train: list[dict[str, Any]]) -> float:
    """B7: mean(actual - model) total on TRAIN postseason rows, with a season-out check
    (fit on the earlier postseason, score the later one; both inside train)."""
    post = [r for r in train if r["season_type"] in POSTSEASON_TYPES]
    resid = {r["event_id"]: r["home_score"] + r["away_score"] - r["model_total"] for r in post}
    years = sorted({r["date"][:4] for r in post})
    offset = float(np.mean(list(resid.values())))
    print(f"B7 fit on {len(post)} train postseason games: offset {offset:+.2f}")
    if len(years) >= 2:
        early = [resid[r["event_id"]] for r in post if r["date"][:4] == years[0]]
        late = np.array([resid[r["event_id"]] for r in post if r["date"][:4] == years[-1]])
        off_early = float(np.mean(early))
        print(
            f"  season-out: fit {years[0]} ({len(early)} games) {off_early:+.2f}; {years[-1]} ({len(late)}) "
            f"RMSE raw {np.sqrt(np.mean(late**2)):.2f} -> with {years[0]} offset "
            f"{np.sqrt(np.mean((late - off_early) ** 2)):.2f}"
        )
    return round(offset, 2)


def cmd_fit(names: list[str], data_root: Path) -> None:
    for name in names:
        train = _split_rows(name, data_root)["train"]
        print(name, json.dumps(fit_fatigue(train)) if name.endswith("zero") else "", flush=True)
        fit_playoff_offset(train)


# (engine label, cached rows per market). v9.1 serves spreads from the spread artifact and
# totals from the total artifact, so each market is gated on the rows of its own artifact.
G1_MODELS = {
    "v9": {"spread": "v9", "total": "v9"},
    "v9.1": {"spread": "v91_spread_final", "total": "v91_total_final"},
}
ANCHOR_ARTIFACT = REPO / "config" / "models" / "research" / "nba-anchor-v10.json"


def cmd_g1(data_root: Path, draws: int) -> None:
    """Gate G1 for v9 and v9.1 as ``model``; writes G1.md and the nba-anchor-v10 artifact
    (lambda and sigma fitted on train with v9.1 as the model)."""
    from model_prediction.production_registry import compute_artifact_hash

    results: dict[tuple[str, str], G1Result] = {}
    for label, per_market in G1_MODELS.items():
        for market, rows_name in per_market.items():
            split = _split_rows(rows_name, data_root)
            results[(label, market)] = gate_g1(label, split["train"], split["val"], market, draws=draws)
    lines = [
        "# Gate G1 against 2024-26 closing lines (benchmark only, exploratory)",
        "",
        f"Split: train < {PINNED_VAL_START} <= validation < {PINNED_HOLDOUT_START} (holdout not replayed).",
        "Closing lines carry no capture time: this is a benchmark, never a decision input. Validation",
        "also selected the v9.1 fixes, so the v9.1 rows are doubly exploratory. CIs: 95% percentile,",
        f"{draws} date-cluster bootstrap draws. Lambda is the through-origin OLS weight of",
        "(actual - close) on (model - close); the anchored mean is close + max(0, lambda) * (model - close).",
        "",
        (
            "| model | market | n train | n val | lambda train [95% CI] | lambda val [95% CI] | "
            "corr val [95% CI] | sigma (train) | val RMSE close | val RMSE anchored | G1 |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results.values():
        lines.append(
            f"| {r.engine} | {r.market} | {r.n_train} | {r.n_val} | {r.lambda_train.fmt()} | "
            f"{r.lambda_val.fmt()} | {r.corr_val.fmt()} | {r.sigma_train:.2f} | {r.rmse_val_line:.3f} | "
            f"{r.rmse_val_anchored:.3f} | {'PASS' if r.passed else 'FAIL'} |"
        )
    lines += [
        "",
        "G1 pass rule: lambda (train) CI > 0, lambda (validation refit) CI > 0, corr (validation) CI > 0,",
        "n val >= 700. A failing component ships with lambda = 0 (anchored price = market) and is",
        "not re-tuned on the same data.",
    ]
    out = OUT_DIR / "G1.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))

    artifact: dict[str, Any] = {
        "model_version": "nba-anchor-v10",
        "status": "research",
        "serving": "not registered, not whitelisted; wiring spec in outputs/nba/v10_harness/ANCHOR_WIRING.md",
        "engine": "model_prediction.models.nba_anchor_v10",
        "underlying_model": {"spread": "nba-spread-margin-v9-1", "total": "nba-total-margin-v9-1"},
        "fee_coefficient": float(POLICY["fee_rate"]),
        "fee_source": "research_incumbent_evaluation.POLICY (Polymarket US taker fee c * p * (1 - p))",
        "fit": {
            "rows": "harness replay of the v9.1 artifacts vs 2024-26 closing lines",
            "train": f"< {PINNED_VAL_START}",
            "validation": f"{PINNED_VAL_START} .. < {PINNED_HOLDOUT_START}",
            "note": "lambda fit on train and clipped at 0; G1 failed, so lambda ships at 0. Serving "
            "lambda must be refit on decision-time anchors (captured consensus / Polymarket mid).",
        },
        "markets": {},
    }
    for market in ("spread", "total"):
        r = results[("v9.1", market)]
        artifact["markets"][market] = {
            "lambda": round(max(0.0, r.lambda_train.estimate), 4) if r.passed else 0.0,
            "lambda_train_raw": round(r.lambda_train.estimate, 4),
            "lambda_train_ci": [round(r.lambda_train.low, 4), round(r.lambda_train.high, 4)],
            "corr_val": round(r.corr_val.estimate, 4),
            "corr_val_ci": [round(r.corr_val.low, 4), round(r.corr_val.high, 4)],
            "g1": "PASS" if r.passed else "FAIL",
            "sigma": round(r.sigma_train, 3),
            "main_min_edge": None,
        }
    artifact["artifact_hash"] = compute_artifact_hash(artifact)
    ANCHOR_ARTIFACT.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {ANCHOR_ARTIFACT}")


V91_ARTIFACTS = {
    "spread": (
        "v91_spread_final",
        "nba-spread-margin-v9.json",
        "nba-spread-margin-v9-1.json",
        "expected_total",
    ),
    "total": ("v91_total_final", "nba-total-margin-v9.json", "nba-total-margin-v9-1.json", "expected_margin"),
}


def cmd_v91_artifacts() -> None:
    """Write the v9.1 research artifacts from the final harness configs.

    Engine parameters come from the v9 spread artifact (the v9 total artifact omits them and
    relies on identical engine defaults); descriptive fields (method, positive_class,
    prediction_formula) come from the market's own v9 artifact. The engine built from each file
    must equal the engine the harness replayed.
    """
    from model_prediction.models.nba_v91 import NBAStructuralV91Engine
    from model_prediction.production_registry import compute_artifact_hash

    models = REPO / "config" / "models"
    spread_v9 = json.loads((models / "nba-spread-margin-v9.json").read_text(encoding="utf-8"))
    configs = json.loads((OUT_DIR / "v91_configs.json").read_text(encoding="utf-8"))
    for market, (config_name, v9_name, out_name, drop_prefix) in V91_ARTIFACTS.items():
        own_v9 = json.loads((models / v9_name).read_text(encoding="utf-8"))
        art = {**spread_v9, **own_v9, **configs[config_name]}
        for key in [k for k in art if k.startswith(drop_prefix)] + [
            "artifact_hash",
            "qualification",
            "note",
        ]:
            art.pop(key, None)
        art.update(
            {
                "model_version": f"nba-{market}-margin-v9-1",
                "status": "research",
                "serving": f"not registered, not whitelisted; research challenger to nba-{market}-margin-v9",
                "engine": "model_prediction.models.nba_v91.NBAStructuralV91Engine",
                "evidence": {
                    "harness": "scripts/nba_v10/replay_harness.py (outputs/nba/v10_harness/V91_ABLATION.md)",
                    "split": "train < 2025-11-03 <= validation < 2026-02-23; holdout not replayed; exploratory",
                    "fits": "rolling_baseline_games tuned on validation {75,150,300}; fatigue and playoff "
                    "offset fit on train only",
                },
            }
        )
        art["artifact_hash"] = compute_artifact_hash(art)
        replayed = NBAStructuralV91Engine.from_artifact({**spread_v9, **configs[config_name]})
        built = NBAStructuralV91Engine.from_artifact(art)
        if vars(replayed) != vars(built):
            sys.exit(f"{out_name}: engine differs from the replayed config")
        (models / "research" / out_name).write_text(json.dumps(art, indent=2, sort_keys=True) + "\n")
        print(f"wrote {out_name} {art['artifact_hash'][:12]}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "command", choices=["golden", "replay", "diff", "score", "ablate", "fit", "g1", "v91-artifacts"]
    )
    parser.add_argument("names", nargs="*")
    parser.add_argument("--data-root", type=Path, default=Path("E:/MP/data"))
    parser.add_argument("--draws", type=int, default=2000)
    args = parser.parse_args()
    if args.command == "golden":
        cmd_golden(args.data_root)
    elif args.command == "replay":
        cmd_replay(args.names, args.data_root)
    elif args.command == "diff":
        cmd_diff(args.names, args.data_root)
    elif args.command == "ablate":
        cmd_ablate(args.names, args.data_root)
    elif args.command == "fit":
        cmd_fit(args.names, args.data_root)
    elif args.command == "g1":
        cmd_g1(args.data_root, args.draws)
    elif args.command == "v91-artifacts":
        cmd_v91_artifacts()
    else:
        cmd_score(args.names, args.data_root, args.draws)


if __name__ == "__main__":
    main()
