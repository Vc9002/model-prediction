"""Fit the v9.2 probability shrink for the NBA v9.1 total engine (quick-261123-h40).

v9.2 serves p' = sigmoid(s * logit(p)) with intercept 0 and 0 <= s <= 1, where p is the v9.1 total
engine's P(over) (research artifact config/models/research/nba-total-margin-v9-1.json: B5 + B6 + B7).
s is fitted here, mirroring scripts/fit_nfl_v71_shrink.py and scripts/fit_cfb_v52_total_shrink.py:

1. Rows: the NBA research replay harness (model_prediction.nba_replay_harness.replay), one ET date at a
   time with the production history cutoff, the v9.1 total engine at slope 1.0, priced at the archived
   closing total. The replay runs past the harness's holdout cap so the holdout can be reported once.
   Nothing is written under the data root (read-only); rows are kept in memory.
2. Reproduction gate: every pre-holdout row must match the harness's cached ``v91_total_final`` rows
   (same events, same P(over) and mean), or the script stops before fitting.
3. Split: the harness's pinned boundaries (scripts/nba_v10/replay_harness.py): train < 2025-11-03 <=
   validation < 2026-02-23 <= holdout. Pre-holdout rows are labelled by ``assign_splits``.
4. Fit: s maximises the train-window log likelihood of a slope-only logistic of over/under on
   logit(P(over)) (pushes dropped), clipped to [0, 1], rounded to 3 decimals. Every reported number
   uses the rounded value. Pre-committed stop rule: if s clips at 1.0, no artifact is written.
5. Report raw vs shrunk per window (RMSE, bias, Brier, log loss, calibration slope), and how many games
   clear the NBA Main total edge rule before and after.

Disclosures (also in the report): validation was read by the v9.1 ablation (it selected B5's window),
and B6/B7 were fitted on train, so train-window probabilities are partly in-sample and s is biased
toward too little shrink. The 2026-02-23+ holdout is the NBA registry holdout, touched twice before
(NBA review 2026-11-20); this is its first read for v9.1, and it is read once, after s is frozen.

Usage (read-only on the data root; run single-threaded on this host):
  OPENBLAS_NUM_THREADS=1 python scripts/nba_v10/fit_nba_v92_total_shrink.py --data-root E:/MP/data \
      [--write-artifact]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import replay_harness as H

from model_prediction.calibration import calibration_metrics
from model_prediction.config import load_config
from model_prediction.features.base import FeatureStore
from model_prediction.models.nba_v91 import NBA_TOTAL_V92_VERSION, _logit_shrink
from model_prediction.nba_replay_harness import (
    DEFAULT_LINES_PATH,
    assign_splits,
    file_sha256,
    load_closing_lines,
    read_rows,
    replay,
)
from model_prediction.nba_v10_engines import v91_spec
from model_prediction.production_registry import compute_artifact_hash

RESEARCH_ARTIFACT = REPO / "config" / "models" / "research" / "nba-total-margin-v9-1.json"
V9_ARTIFACT = REPO / "config" / "models" / "nba-total-margin-v9.json"
OUT_DIR = REPO / "outputs" / "nba" / "v10_harness"
OUT_JSON = OUT_DIR / "v92_total_shrink.json"
OUT_MD = OUT_DIR / "v92_total_shrink.md"
ARTIFACT_PATH = REPO / "config" / "models" / f"{NBA_TOTAL_V92_VERSION}.json"
CACHED_RAW = "v91_total_final"  # harness cache name of the same engine
CACHED_V9 = "v9"
SLOPE_DECIMALS = 3
REPLAY_END = "9999-12-31"  # past the harness cap: the holdout is replayed here, once
REPRO_TOL = 1e-9
# Asks at which the Main edge band is counted. The archive has no Polymarket prices, so the ask is
# assumed: 52.4% is the standard lay price (two-sided 4.8% overround), 50% a fair-coin ask.
ASSUMED_ASKS = (0.5238, 0.50)
OVER_FLOOR = 0.09  # forecast.py _log_basketball_total_picks thin-sample Over floor
OVER_FLOOR_GAMES = 15


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def fit_slope(probs: list[float], outcomes: list[int]) -> dict[str, float]:
    """Slope-only logistic MLE of y on logit(p) (intercept fixed at 0), by Newton's method.

    The same estimator as scripts/fit_nfl_v71_shrink.py::fit_slope.
    """
    xs = [_logit(p) for p in probs]
    s = 1.0
    for _ in range(100):
        grad = hess = 0.0
        for x, y in zip(xs, outcomes, strict=True):
            q = 1.0 / (1.0 + math.exp(-s * x))
            grad += x * (y - q)
            hess += x * x * q * (1.0 - q)
        step = grad / hess
        s += step
        if abs(step) < 1e-12:
            break
    info = sum(x * x * (q := 1.0 / (1.0 + math.exp(-s * x))) * (1.0 - q) for x in xs)
    return {"mle": s, "se": 1.0 / math.sqrt(info), "clipped": min(1.0, max(0.0, s))}


def _over(row: dict[str, Any]) -> int | None:
    total = row["home_score"] + row["away_score"]
    if total == row["line_total"]:
        return None
    return int(total > row["line_total"])


def outcome_metrics(rows: list[dict[str, Any]], key: str = "p_over", slope: float = 1.0) -> dict[str, Any]:
    act = np.array([r["home_score"] + r["away_score"] for r in rows], float)
    mean = np.array([r["model_total"] for r in rows], float)
    decided = [r for r in rows if _over(r) is not None]
    probs = [_logit_shrink(float(r[key]), slope) for r in decided]
    ys = [_over(r) for r in decided]
    cal = calibration_metrics(probs, ys)
    return {
        "n": len(rows),
        "n_decided": len(decided),
        "rmse": float(np.sqrt(np.mean((act - mean) ** 2))),
        "bias_actual_minus_model": float(np.mean(act - mean)),
        "brier": cal.get("brier_score"),
        "log_loss": cal.get("log_loss"),
        "calibration_slope": cal.get("calibration_slope"),
        "calibration_intercept": cal.get("calibration_intercept"),
        "ece": cal.get("expected_calibration_error"),
        "mean_abs_distance_from_50": float(np.mean([abs(p - 0.5) for p in probs])),
        "over_share_called": float(np.mean([p >= 0.5 for p in probs])),
    }


def main_rule() -> dict[str, Any]:
    """The NBA Main total edge band, read from config the way cli/forecast.py reads it.

    _forecast_nba_total_sport -> _log_basketball_total_picks(nba_rules=True) admits a row to Main when
    eligibility is CALL and qualified and min_edge <= model_probability - executable_ask <=
    max_disagreement, where min_edge is models.NBA.min_edge, raised to 0.09 for an Over when the
    smaller current-season game count is below 15, and max_disagreement is
    project.maximum_unreviewed_market_disagreement. The slate takes the Over at P(over) >= 50%.
    """
    config = load_config()
    return {
        "min_edge": float(config.get("models", {}).get("NBA", {}).get("min_edge", 0.05)),
        "max_edge": float(config.get("project", {}).get("maximum_unreviewed_market_disagreement", 0.10)),
        "over_floor": OVER_FLOOR,
        "over_floor_games": OVER_FLOOR_GAMES,
        "assumed_asks": list(ASSUMED_ASKS),
        "source": "src/model_prediction/cli/forecast.py _log_basketball_total_picks (nba_rules=True); "
        "config/model.yaml",
        "not_modelled": "eligibility CALL/qualified (registration, input freshness); edge only",
    }


def clears_main(p_over: float, season_games_min: int, ask: float, rule: dict[str, Any]) -> bool:
    is_over = p_over >= 0.5
    edge = (p_over if is_over else 1.0 - p_over) - ask
    floor = rule["min_edge"]
    if is_over and season_games_min < rule["over_floor_games"]:
        floor = max(floor, rule["over_floor"])
    return floor <= edge <= rule["max_edge"]


def season_games_min(store: FeatureStore) -> dict[str, int]:
    """Smaller team's prior games in the same season (non-preseason), per event.

    Approximates forecast.py's ``current_season_games_played_min`` (TrendEngine current-season count
    with the opener override) from the stored season year; the harness rows only carry the engine's
    lifetime count (B1), under which the Over floor would never fire.
    """
    games = sorted(
        (g for g in store.load_games("nba") if g.season_type != "preseason"), key=lambda g: g.start
    )
    seen: dict[tuple[int | None, str], int] = defaultdict(int)
    out: dict[str, int] = {}
    for g in games:
        out[g.event_id] = min(seen[(g.season_year, g.home_team)], seen[(g.season_year, g.away_team)])
        seen[(g.season_year, g.home_team)] += 1
        seen[(g.season_year, g.away_team)] += 1
    return out


def reproduce(rows: list[dict[str, Any]], cached: list[dict[str, Any]]) -> dict[str, Any]:
    new = {r["event_id"]: r for r in rows if r["date"] < H.PINNED_HOLDOUT_START}
    old = {r["event_id"]: r for r in cached}
    common = new.keys() & old.keys()
    dp = max((abs(new[e]["p_over"] - old[e]["p_over"]) for e in common), default=0.0)
    dm = max((abs(new[e]["model_total"] - old[e]["model_total"]) for e in common), default=0.0)
    return {
        "replayed_pre_holdout": len(new),
        "cached": len(old),
        "only_replayed": sorted(new.keys() - old.keys())[:20],
        "only_cached": sorted(old.keys() - new.keys())[:20],
        "max_abs_p_over_diff": dp,
        "max_abs_model_total_diff": dm,
        "ok": new.keys() == old.keys() and dp <= REPRO_TOL and dm <= REPRO_TOL,
    }


def fit(
    rows: list[dict[str, Any]], v9_rows: list[dict[str, Any]], games_min: dict[str, int]
) -> dict[str, Any]:
    pre = [r for r in rows if r["date"] < H.PINNED_HOLDOUT_START]
    assign_splits(pre, H.PINNED_VAL_START, H.PINNED_HOLDOUT_START)
    windows = {
        "train": [r for r in pre if r["split"] == "train"],
        "validation": [r for r in pre if r["split"] == "val"],
        "locked_holdout": [r for r in rows if r["date"] >= H.PINNED_HOLDOUT_START],
    }
    train = [r for r in windows["train"] if _over(r) is not None]
    slope = fit_slope([float(r["p_over"]) for r in train], [int(_over(r)) for r in train])
    stored = round(slope["clipped"], SLOPE_DECIMALS)
    assign_splits(v9_rows, H.PINNED_VAL_START, H.PINNED_HOLDOUT_START)
    v9_windows = {
        "train": [r for r in v9_rows if r["split"] == "train"],
        "validation": [r for r in v9_rows if r["split"] == "val"],
    }
    rule = main_rule()
    report_windows: dict[str, Any] = {}
    for name, sub in windows.items():
        block: dict[str, Any] = {
            "start": min(r["date"] for r in sub),
            "end": max(r["date"] for r in sub),
            "season_types": dict(sorted(_count(r["season_type"] for r in sub).items())),
            "v91_raw": outcome_metrics(sub),
            "v92_shrunk": outcome_metrics(sub, slope=stored),
        }
        if name in v9_windows:
            block["v9_champion"] = outcome_metrics(v9_windows[name])
        main: dict[str, Any] = {}
        for ask in ASSUMED_ASKS:
            key = f"ask_{ask:.4f}"
            main[key] = {
                "v91_raw": sum(
                    clears_main(float(r["p_over"]), games_min[r["event_id"]], ask, rule) for r in sub
                ),
                "v92_shrunk": sum(
                    clears_main(
                        _logit_shrink(float(r["p_over"]), stored), games_min[r["event_id"]], ask, rule
                    )
                    for r in sub
                ),
            }
            if name in v9_windows:
                main[key]["v9_champion"] = sum(
                    clears_main(float(r["p_over"]), games_min[r["event_id"]], ask, rule)
                    for r in v9_windows[name]
                )
        block["main_rule_clears"] = main
        report_windows[name] = block
    return {
        "split": {
            "validation_start": H.PINNED_VAL_START,
            "holdout_start": H.PINNED_HOLDOUT_START,
            "source": "scripts/nba_v10/replay_harness.py PINNED_VAL_START / PINNED_HOLDOUT_START "
            "(nba_replay_harness.assign_splits)",
        },
        "main_rule": rule,
        "slope_fit_train": {
            "mle": slope["mle"],
            "standard_error": slope["se"],
            "clipped": slope["clipped"],
            "stored_probability_logit_slope": stored,
            "n_train_decided": len(train),
            "stop_rule_clipped_at_1": stored == 1.0,
        },
        "served_probability_examples": {
            str(p): round(_logit_shrink(p, stored), 6) for p in (0.35, 0.45, 0.55, 0.65, 0.75)
        },
        "windows": report_windows,
    }


def _count(values: Any) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for v in values:
        out[str(v)] += 1
    return dict(out)


def _f(value: Any, digits: int = 4) -> str:
    return "n/a" if value is None else f"{float(value):.{digits}f}"


def render_markdown(report: dict[str, Any]) -> str:
    fit_block = report["slope_fit_train"]
    rule = report["main_rule"]
    rep = report["reproduction"]
    lines = [
        "# NBA v9.2 total probability shrink fit (quick-261123-h40)",
        "",
        f"Generated {report['generated_at_utc']} by `scripts/nba_v10/fit_nba_v92_total_shrink.py`.",
        "",
        (
            "Engine: the v9.1 total research artifact (B5 + B6 + B7) at slope 1.0, replayed point-in-time "
            "by the NBA harness against the archived closing totals. Shrink: `p' = sigmoid(s * logit(p))`, "
            "intercept 0, s fitted on train only (slope-only logistic on logit(P(over)), pushes dropped), "
            "clipped to [0, 1], rounded to 3 decimals; every number below uses the rounded value."
        ),
        "",
        (
            f"Reproduction of the cached `{CACHED_RAW}` harness rows: {rep['replayed_pre_holdout']} replayed vs "
            f"{rep['cached']} cached pre-holdout rows, max |P(over) diff| {rep['max_abs_p_over_diff']:.2e}, "
            f"max |mean diff| {rep['max_abs_model_total_diff']:.2e}: {'OK' if rep['ok'] else 'FAILED'}."
        ),
        "",
        "## Split (harness pinned boundaries)",
        "",
        "| Window | Start | End | Games | Season types |",
        "|---|---|---|---|---|",
    ]
    for name, w in report["windows"].items():
        lines.append(
            f"| {name} | {w['start']} | {w['end']} | {w['v91_raw']['n']} | "
            + ", ".join(f"{k} {v}" for k, v in w["season_types"].items())
            + " |"
        )
    stored = fit_block["stored_probability_logit_slope"]
    lines += [
        "",
        (
            f"**Fit:** train MLE s = {fit_block['mle']:.4f} (SE {fit_block['standard_error']:.4f}, "
            f"n = {fit_block['n_train_decided']} decided games); stored `probability_logit_slope` = **{stored}**."
        ),
        "",
        "Served P(over) after the shrink: "
        + ", ".join(f"{float(k):.0%} -> {v:.2%}" for k, v in report["served_probability_examples"].items())
        + ".",
        "",
    ]
    if fit_block["stop_rule_clipped_at_1"]:
        lines += ["**STOP:** s clipped at 1.0 (pre-committed stop rule). No artifact is written.", ""]
    lines += [
        "## Outcomes per window",
        "",
        (
            "RMSE and bias are on the total mean (the shrink does not move it). Bias = mean(actual - model). "
            "Brier, log loss and calibration slope are on over/under at the closing total, pushes dropped "
            "(coin flip Brier 0.2500)."
        ),
        "",
        "| Window | Model | n | RMSE | Bias | Brier | Log loss | Cal. slope | Mean abs dist from 50% |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, w in report["windows"].items():
        for label, key in (
            ("v9 (champion)", "v9_champion"),
            ("v9.1 raw", "v91_raw"),
            ("v9.2 shrunk", "v92_shrunk"),
        ):
            if key not in w:
                continue
            m = w[key]
            lines.append(
                f"| {name} | {label} | {m['n_decided']} | {m['rmse']:.3f} | {m['bias_actual_minus_model']:+.2f} | "
                f"{_f(m['brier'])} | {_f(m['log_loss'])} | {_f(m['calibration_slope'], 3)} | "
                f"{m['mean_abs_distance_from_50']:.2%} |"
            )
    lines += [
        "",
        "## Games clearing the NBA Main total edge band",
        "",
        (
            f"Rule: {rule['min_edge']:.0%} <= side probability - ask <= {rule['max_edge']:.0%}; an Over needs "
            f"{rule['over_floor']:.0%} when the smaller current-season game count is below {rule['over_floor_games']} "
            "(counted from the stored season year). The archive has no Polymarket prices, so the ask is assumed. "
            "Edge only: eligibility (CALL, qualified, registration) is not modelled."
        ),
        "",
        "| Window | Assumed ask | v9 (champion) | v9.1 raw | v9.2 shrunk |",
        "|---|---|---|---|---|",
    ]
    for name, w in report["windows"].items():
        for key, counts in w["main_rule_clears"].items():
            ask = float(key.split("_")[1])
            lines.append(
                f"| {name} | {ask:.1%} | {counts.get('v9_champion', 'not replayed')} | {counts['v91_raw']} | "
                f"{counts['v92_shrunk']} |"
            )
    lines += [
        "",
        "## Disclosures",
        "",
        (
            "- Validation was read by the 2026-11-20 v9.1 ablation and selected B5's window (W = 300); B6 and "
            "B7 were fitted on train. Train-window probabilities are therefore partly in-sample, which biases "
            "s toward too little shrink. Validation is a check, not a clean test."
        ),
        (
            "- The 2026-02-23+ holdout is the NBA registry holdout, touched twice before the harness existed "
            "(NBA review 2026-11-20). The harness never replayed it; this script reads it once, after s is "
            "frozen. It is design-informed (the same review motivated v9.1), not a clean confirmation. It "
            "holds the 2026 playoffs, where B7's train-fitted offset applies."
        ),
        (
            "- Closing totals carry no capture time: a benchmark only. Gate G1 (market) failed for v9.1; the "
            "shrink calibrates the probability, it does not create market edge."
        ),
        "",
        "## Inputs",
        "",
        "| Input | sha256 |",
        "|---|---|",
    ]
    for key, value in report["inputs"].items():
        lines.append(f"| {key} | `{value}` |")
    return "\n".join(lines) + "\n"


def text_sha256(path: Path) -> str:
    """sha256 with CRLF folded to LF: the committed blob content whatever core.autocrlf leaves on disk."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def build_artifact(report: dict[str, Any], fit_sha256: str) -> dict[str, Any]:
    parent = json.loads(RESEARCH_ARTIFACT.read_text(encoding="utf-8"))
    if compute_artifact_hash(parent) != parent["artifact_hash"]:
        raise SystemExit(f"{RESEARCH_ARTIFACT} does not self-verify")
    v9 = json.loads(V9_ARTIFACT.read_text(encoding="utf-8"))
    if compute_artifact_hash(v9) != v9["artifact_hash"]:
        raise SystemExit(f"{V9_ARTIFACT} does not self-verify")
    fit_block = report["slope_fit_train"]
    slope = fit_block["stored_probability_logit_slope"]
    windows = report["windows"]
    artifact = {k: v for k, v in parent.items() if k not in {"artifact_hash", "serving", "evidence"}}
    artifact.update(
        {
            "model_version": NBA_TOTAL_V92_VERSION,
            "total_version": NBA_TOTAL_V92_VERSION,
            "status": "candidate",
            "method": "nba_total_structural_v92_engine",
            "engine": "model_prediction.models.nba_v91.NBATotalModelV92",
            "probability_logit_slope": slope,
            "parent_model_version": parent["model_version"],
            "parent_artifact_hash": parent["artifact_hash"],
            "prediction_formula": (
                "P(over) = sigmoid(probability_logit_slope * logit(1 - normal_cdf(total_line, expected_total, "
                "effective_total_sd)))"
            ),
            "availability_adjustment": False,
            "note": (
                f"{parent['model_version']} total engine (B5 rolling baseline, B6 fitted fatigue, B7 playoff "
                f"offset) unchanged; the final P(over) is served as sigmoid({slope} * logit(p)), intercept 0, "
                "so no side is favoured. Player availability (B9) is not applied: v9.1 never fitted it. "
                f"Train-window fit MLE {fit_block['mle']:.3f} (SE {fit_block['standard_error']:.3f}); see "
                "outputs/nba/v10_harness/v92_total_shrink.md."
            ),
        }
    )
    artifact["probability_shrink_provenance"] = {
        "fit_script": "scripts/nba_v10/fit_nba_v92_total_shrink.py",
        "fit_output": "outputs/nba/v10_harness/v92_total_shrink.json",
        "fit_output_sha256": fit_sha256,
        "fit_output_sha256_of": "LF-normalised file bytes (the committed git blob content)",
        "fitted_on": "harness replay P(over) of the v9.1 total engine at the closing total, pushes dropped",
        "split": "nba_replay_harness pinned boundaries (scripts/nba_v10/replay_harness.py)",
        "train": [windows["train"]["start"], windows["train"]["end"]],
        "validation": [windows["validation"]["start"], windows["validation"]["end"]],
        "locked_holdout": [windows["locked_holdout"]["start"], windows["locked_holdout"]["end"]],
        "train_mle_unclipped": round(fit_block["mle"], 6),
        "train_standard_error": round(fit_block["standard_error"], 6),
        "n_train_decided": fit_block["n_train_decided"],
        "validation_read_before": True,
        "holdout_design_informed": True,
    }
    hold = windows["locked_holdout"]
    val = windows["validation"]
    artifact["qualification"] = {
        "model_version": NBA_TOTAL_V92_VERSION,
        "status": "candidate",
        "qualified": True,
        "inherited_from": v9["model_version"],
        "inherited_note": (
            "qualified is inherited from the nba-total-margin-v9 champion lineage, NOT earned by this engine: "
            "the engine differs from v9 (v9.1 fixes B5/B6/B7) and v9's own qualification figures are not "
            "reproducible (NBA review 2026-11-20, B8). The harness numbers below are v9.2's own evidence; gate "
            "G1 (market) failed for v9.1, so this model has no demonstrated edge over the closing line."
        ),
        "harness_validation": {
            "n": val["v92_shrunk"]["n_decided"],
            "start": val["start"],
            "rmse": val["v92_shrunk"]["rmse"],
            "bias_actual_minus_model": val["v92_shrunk"]["bias_actual_minus_model"],
            "brier_raw": val["v91_raw"]["brier"],
            "brier_shrunk": val["v92_shrunk"]["brier"],
            "calibration_slope_shrunk": val["v92_shrunk"]["calibration_slope"],
            "note": "validation was read by the v9.1 ablation (B5 window selection)",
        },
        "probability_shrink_locked_holdout": {
            "n": hold["v92_shrunk"]["n_decided"],
            "start": hold["start"],
            "rmse": hold["v92_shrunk"]["rmse"],
            "bias_actual_minus_model": hold["v92_shrunk"]["bias_actual_minus_model"],
            "brier_raw": hold["v91_raw"]["brier"],
            "brier_shrunk": hold["v92_shrunk"]["brier"],
            "log_loss_raw": hold["v91_raw"]["log_loss"],
            "log_loss_shrunk": hold["v92_shrunk"]["log_loss"],
            "note": "design-informed: the NBA registry holdout was touched twice before; read once here",
        },
    }
    artifact["artifact_hash"] = compute_artifact_hash(artifact)
    return artifact


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, default=Path("E:/MP/data"), help="read-only data root")
    ap.add_argument("--write-artifact", action="store_true", help=f"also write {ARTIFACT_PATH.name}")
    ap.add_argument("--force", action="store_true", help="overwrite an existing v9.2 artifact")
    args = ap.parse_args()
    H._blackout_guard()

    research = json.loads(RESEARCH_ARTIFACT.read_text(encoding="utf-8"))
    if compute_artifact_hash(research) != research["artifact_hash"]:
        raise SystemExit(f"{RESEARCH_ARTIFACT} does not self-verify")
    if research.get("probability_logit_slope", 1.0) != 1.0:
        raise SystemExit("the research artifact must price raw (no probability_logit_slope)")

    store = FeatureStore(args.data_root)
    lines_path = args.data_root / DEFAULT_LINES_PATH.relative_to("data")
    lines = load_closing_lines(lines_path)
    cache = H._cache_dir(args.data_root)
    t0 = time.time()
    rows = replay(
        store,
        [v91_spec("v92_raw", research)],
        lines,
        end_date=REPLAY_END,
        progress=lambda d: print(d, flush=True),
    )["v92_raw"]
    print(f"replayed {len(rows)} rows in {time.time() - t0:.0f}s", flush=True)
    reproduction = reproduce(rows, read_rows(cache / f"{CACHED_RAW}.jsonl"))
    print("reproduction:", json.dumps(reproduction), flush=True)
    if not reproduction["ok"]:
        raise SystemExit("pre-holdout rows do not reproduce the cached harness rows; not fitting")

    games_min = season_games_min(store)
    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "data_root": str(args.data_root),
        "reproduction": reproduction,
        "inputs": {
            "games_jsonl": file_sha256(store.processed_path("nba")),
            "closing_lines_jsonl": file_sha256(lines_path),
            "research_artifact_hash": research["artifact_hash"],
            "cached_v91_total_final_rows": file_sha256(cache / f"{CACHED_RAW}.jsonl"),
            "cached_v9_rows": file_sha256(cache / f"{CACHED_V9}.jsonl"),
        },
        **fit(rows, read_rows(cache / f"{CACHED_V9}.jsonl"), games_min),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    OUT_MD.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    fit_sha256 = text_sha256(OUT_JSON)
    print(render_markdown(report))
    print("wrote", OUT_JSON, "sha256", fit_sha256)
    if report["slope_fit_train"]["stop_rule_clipped_at_1"]:
        raise SystemExit("STOP: s clipped at 1.0; no artifact written")
    if args.write_artifact:
        if ARTIFACT_PATH.exists() and not args.force:
            raise SystemExit(f"{ARTIFACT_PATH} exists; use a new version id or pass --force")
        artifact = build_artifact(report, fit_sha256)
        ARTIFACT_PATH.write_text(
            json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
        print("wrote", ARTIFACT_PATH, artifact["artifact_hash"])


if __name__ == "__main__":
    main()
