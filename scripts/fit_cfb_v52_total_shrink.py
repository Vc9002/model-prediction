"""Fit cfb-structural-v5.2's total-probability logit shrink and write its artifact (quick-261123-2w2).

v5.2 = cfb-structural-v5 with only the total market's served probability moved to
``sigmoid(s * logit(p))`` (intercept 0, 0 <= s <= 1). This script:

1. rebuilds v5's served total probability for every completed FBS-involved game that has an ESPN pinned
   closing total (espn-lines-rule-1), from the v5 point-in-time replay rows (v5's raw v4 projection, history
   strictly before kickoff) through the served model's own ``calibrate_means`` / ``compute_total_sd`` and
   the served engine's total branch (an analytic normal CDF: no simulation, no RNG);
2. gates on reproducing ``outputs/ncaaf/cfb_v7_v5_repro.json`` (v5's calibrated total and the joined line
   on the v7 holdout) before any number is used;
3. splits 60/20/20 on complete kickoff dates with ``validation.chronological_split`` (fraction mode),
   fits s on TRAIN ONLY (slope-only logistic of over/under at the closing line on logit of v5's no-push
   over share, pushes dropped, clipped to [0, 1]), checks it on validation and reports the locked holdout
   once;
4. counts holdout games that would still clear the NCAAF Main total rule (forecast.py ``_forecast_cfb_sport``)
   at a stated ask, raw v5 vs shrunk;
5. writes outputs/ncaaf/cfb_v52_total_shrink.{json,md} and config/models/cfb-structural-v5.2.json (v5's
   artifact + the slope + provenance, hash recomputed). cfb-structural-v5.json is never modified.

Disclosures (also in the report): every ESPN lined window 2017-2025 and this holdout were read before
(docs/ROADMAP.md NCAAF section; v5, v6, v6.1, v6.2, v7 and v7.1 all scored on it), so market-relative numbers
are design-informed. v5's total_calibration and total_sd were fitted on 2016-08-27..2022-11-11, inside this
script's train window, so train-window v5 probabilities are in-sample for those two layers.

Inputs are read-only: E:\\MP\\data\\research (untracked research data in the main checkout).
Run: PYTHONPATH=src python scripts/fit_cfb_v52_total_shrink.py
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import yaml
from scipy.optimize import minimize, minimize_scalar

REPO = Path(__file__).resolve().parents[1]
MAIN = Path(r"E:\MP")  # read-only research inputs (untracked data in the main checkout)
V5_ROWS = MAIN / "data" / "research" / "ncaaf_v7_work" / "v5_replay_rows.jsonl"
V5_ROWS_SHA_PREFIX = "68caa21f0fc0f4e3"  # the cache scripts/cfb_v7_fit.py reproduces v5 from
ESPN_LINES = MAIN / "data" / "research" / "ncaaf_v6" / "espn_lines.jsonl"
V5_REPRO = REPO / "outputs" / "ncaaf" / "cfb_v7_v5_repro.json"
V5_BASE_ROWS = REPO / "outputs" / "ncaaf" / "cfb_v5_base_rows.jsonl"
OUT_JSON = REPO / "outputs" / "ncaaf" / "cfb_v52_total_shrink.json"
OUT_MD = REPO / "outputs" / "ncaaf" / "cfb_v52_total_shrink.md"
ARTIFACT_OUT = REPO / "config" / "models" / "cfb-structural-v5.2.json"
MODEL_YAML = REPO / "config" / "model.yaml"

V5_TRAIN_END = "2022-11-11"  # v5's own artifact split, reported as a sensitivity only
V5_VALIDATION_END = "2024-11-14"
SLOPE_DECIMALS = 4
N_BOOT = 1000
BOOT_SEED = 11
# No historical asks exist for these games. Two stated prices for the Main-rule count: the no-vig fair
# price at the closing line (50%) and the usual vigged closing price (52.4%, the break-even
# scripts/cfb_v7_fit.py already uses).
ASSUMED_ASKS = (0.50, 0.524)

sys.path.insert(0, str(REPO / "src"))
from model_prediction.cfb_replay import shrink_total_probabilities
from model_prediction.models.cfb_distribution import CFBJointDistributionEngine
from model_prediction.models.cfb_structural_v4 import main_gates_from_artifact
from model_prediction.models.cfb_structural_v5 import (
    CFB_V5_ARTIFACT_PATH,
    CFB_V52_MODEL_VERSION,
    CFBStructuralV5Model,
    load_cfb_structural_v5_artifact,
)
from model_prediction.production_registry import compute_artifact_hash
from model_prediction.validation import chronological_split


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_sha256(path: Path) -> str:
    """sha256 of a git-tracked text file with CRLF folded to LF: the same on a Windows (autocrlf) checkout
    and on an LF checkout, so the artifact's provenance hash verifies wherever the repo is cloned."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def load_lines() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for ln in ESPN_LINES.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            rec = json.loads(ln)
            out[str(rec["event_id"])] = rec.get("selected") or {}
    return out


def v5_total(
    model: CFBStructuralV5Model, raw_margin: float, raw_total: float, line: float
) -> dict[str, float]:
    """v5's served total-market output for one game: forecast_game's arithmetic, total branch only."""
    margin, total = model.calibrate_means(raw_margin, raw_total)
    mu_home = max(3.0, (total + margin) / 2.0)
    mu_away = max(3.0, (total - margin) / 2.0)
    engine = CFBJointDistributionEngine(
        distribution_type=model.distribution_type,
        margin_sd=model.margin_sd_table["established"],  # does not enter the total branch
        total_sd=model.compute_total_sd(projected_total=mu_home + mu_away, total_line=line),
        n_simulations=1,  # the total is an analytic normal CDF; the simulated scores are unused
    )
    joint = engine.compute_market_probabilities(mu_home=mu_home, mu_away=mu_away, total_line=line)
    return {
        "cal_total": total,
        "served_total": mu_home + mu_away,
        "p_over": joint.p_over,
        "p_under": joint.p_under,
        "p_push": joint.p_push_total,
    }


def build_rows(model: CFBStructuralV5Model) -> list[SimpleNamespace]:
    if not sha256(V5_ROWS).startswith(V5_ROWS_SHA_PREFIX):
        raise SystemExit("v5 replay row cache sha256 mismatch")
    lines = load_lines()
    replay = [json.loads(x) for x in V5_ROWS.read_text(encoding="utf-8").splitlines() if x.strip()]
    replay.sort(key=lambda r: (r["start_utc"], r["event_id"]))
    rows = []
    for r in replay:
        line = (lines.get(r["event_id"]) or {}).get("total_line")
        if line is None:
            continue
        line = float(line)
        priced = v5_total(model, float(r["raw_margin"]), float(r["raw_total"]), line)
        actual = float(r["total"])
        rows.append(
            SimpleNamespace(
                date=r["date"],
                event_id=r["event_id"],
                line=line,
                actual=actual,
                push=actual == line,
                y=int(actual > line),
                raw_total=float(r["raw_total"]),
                is_fcs=bool(r["is_fcs"]),
                week=int(r["week"]),
                **priced,
            )
        )
    return rows


# ------------------------------------------------------------------------------------------- reproduction
def reproduction_gate(rows: list[SimpleNamespace], model: CFBStructuralV5Model) -> dict[str, Any]:
    """v5's calibrated total and the joined ESPN line must match the v7 harness's v5 arm on its holdout."""
    per_event = json.loads(V5_REPRO.read_text(encoding="utf-8"))["per_event"]
    by_id = {r.event_id: r for r in rows}
    n_tot = n_line = 0
    worst_tot = worst_line = 0.0
    for eid, rec in per_event.items():
        row = by_id.get(eid)
        if row is None or rec.get("line_total") is None:
            continue
        n_tot += 1
        worst_tot = max(worst_tot, abs(row.cal_total - float(rec["tot"])))
        n_line += 1
        worst_line = max(worst_line, abs(row.line - float(rec["line_total"])))
    # The brief's diagnosis: per_event joined to cfb_v5_base_rows actual totals, pushes dropped.
    actual = {}
    for ln in V5_BASE_ROWS.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            b = json.loads(ln)
            actual[str(b["event_id"])] = float(b["total"])
    xs, ys = [], []
    for eid, rec in per_event.items():
        if rec.get("line_total") is None or eid not in actual or actual[eid] == float(rec["line_total"]):
            continue
        line = float(rec["line_total"])
        tot = float(rec["tot"])
        engine = CFBJointDistributionEngine(
            distribution_type=model.distribution_type,
            margin_sd=model.margin_sd_table["established"],
            total_sd=model.compute_total_sd(projected_total=tot, total_line=line),
            n_simulations=1,
        )
        j = engine.compute_market_probabilities(mu_home=tot / 2.0, mu_away=tot / 2.0, total_line=line)
        xs.append(_logit(j.p_over / (j.p_over + j.p_under)))
        ys.append(int(actual[eid] > line))
    brief = calibration_slope(np.array(xs), np.array(ys))
    passed = n_tot >= 1400 and worst_tot < 1e-6 and worst_line == 0.0
    return {
        "pass": passed,
        "matched_events": n_tot,
        "max_abs_calibrated_total_diff": worst_tot,
        "max_abs_line_diff": worst_line,
        "brief_join": {"n": len(xs), "calibration_slope": brief["slope"], "intercept": brief["intercept"]},
    }


# ------------------------------------------------------------------------------------------------- fitting
def _logit(p: float) -> float:
    p = min(max(p, 1e-12), 1.0 - 1e-12)
    return math.log(p / (1.0 - p))


def share(r: SimpleNamespace) -> float:
    return r.p_over / (r.p_over + r.p_under)


def fit_slope_only(x: np.ndarray, y: np.ndarray) -> float:
    def nll(s: float) -> float:
        z = s * x
        return float(np.sum(np.logaddexp(0.0, z) - y * z))

    return float(minimize_scalar(nll, bounds=(-5.0, 5.0), method="bounded", options={"xatol": 1e-9}).x)


def calibration_slope(x: np.ndarray, y: np.ndarray) -> dict[str, float]:
    """Standard calibration intercept and slope: logit P(y) = a + b * logit(p)."""

    def nll(theta: np.ndarray) -> float:
        z = theta[0] + theta[1] * x
        return float(np.sum(np.logaddexp(0.0, z) - y * z))

    res = minimize(nll, np.array([0.0, 1.0]), method="Nelder-Mead", options={"xatol": 1e-9, "fatol": 1e-12})
    return {"intercept": float(res.x[0]), "slope": float(res.x[1])}


def decided(rows: list[SimpleNamespace]) -> list[SimpleNamespace]:
    return [r for r in rows if not r.push]


def xy(rows: list[SimpleNamespace]) -> tuple[np.ndarray, np.ndarray]:
    d = decided(rows)
    return np.array([_logit(share(r)) for r in d]), np.array([r.y for r in d], dtype=float)


def bootstrap_slope(rows: list[SimpleNamespace]) -> list[float]:
    by_date: dict[str, list[SimpleNamespace]] = {}
    for r in decided(rows):
        by_date.setdefault(r.date, []).append(r)
    dates = sorted(by_date)
    rng = np.random.default_rng(BOOT_SEED)
    fits = []
    for _ in range(N_BOOT):
        sample = [r for i in rng.integers(0, len(dates), len(dates)) for r in by_date[dates[i]]]
        fits.append(fit_slope_only(*xy(sample)))
    return [float(np.percentile(fits, 2.5)), float(np.percentile(fits, 97.5))]


def window_metrics(rows: list[SimpleNamespace], slope: float) -> dict[str, Any]:
    d = decided(rows)
    y = np.array([r.y for r in d], dtype=float)
    raw = np.array([share(r) for r in d])
    shrunk = np.array(
        [
            o / (o + u)
            for o, u in (shrink_total_probabilities(r.p_over, r.p_under, r.p_push, slope) for r in d)
        ]
    )

    x_raw = np.array([_logit(v) for v in raw])
    raw_cal = calibration_slope(x_raw, y)
    raw_slope_only = fit_slope_only(x_raw, y)
    # logit(shrunk share) = s * logit(raw share), so the shrunk window's slopes are the raw ones divided by s
    # (intercept unchanged). Computed analytically: a numeric fit on the compressed logits runs into
    # fit_slope_only's [-5, 5] search bound (validation's true shrunk MLE is about 22.5).
    shrunk_cal = (
        {"slope": raw_cal["slope"] / slope, "intercept": raw_cal["intercept"]}
        if slope > 0
        else {"slope": float("nan"), "intercept": float("nan")}
    )
    shrunk_slope_only = raw_slope_only / slope if slope > 0 else float("nan")

    def scores(p: np.ndarray, cal: dict[str, float], slope_only: float) -> dict[str, Any]:
        pc = np.clip(p, 1e-12, 1 - 1e-12)
        return {
            "brier": round(float(np.mean((p - y) ** 2)), 6),
            "log_loss": round(float(-np.mean(y * np.log(pc) + (1 - y) * np.log(1 - pc))), 6),
            "calibration_slope": round(cal["slope"], 4),
            "calibration_intercept": round(cal["intercept"], 4),
            "slope_only": round(slope_only, 4),
            "mean_p_over": round(float(np.mean(p)), 4),
            "max_abs_minus_half": round(float(np.max(np.abs(p - 0.5))), 6),
        }

    shrunk_scores = scores(shrunk, shrunk_cal, shrunk_slope_only)
    shrunk_scores["slopes_basis"] = "analytic: raw slope / s, raw intercept"
    return {
        "start": rows[0].date,
        "end": rows[-1].date,
        "games_lined": len(rows),
        "pushes_dropped": len(rows) - len(d),
        "games_scored": len(d),
        "over_rate": round(float(np.mean(y)), 4),
        "mean_served_total_minus_line": round(float(np.mean([r.served_total - r.line for r in d])), 3),
        "raw": scores(raw, raw_cal, raw_slope_only),
        "shrunk": shrunk_scores,
    }


# ------------------------------------------------------------------------------------------------ main rule
def main_rule() -> dict[str, Any]:
    cfg = yaml.safe_load(MODEL_YAML.read_text(encoding="utf-8"))["models"]["NCAAF"]
    gates = main_gates_from_artifact(load_cfb_structural_v5_artifact())
    return {
        "min_edge": float(cfg["min_edge"]),
        "max_edge": float(gates.max_edge),
        "block_fcs": bool(gates.block_fcs),
        "main_total_gates": dict(cfg.get("main_total_gates") or {}),
    }


def side_clears(rule: dict[str, Any], r: SimpleNamespace, side: str, prob: float, ask: float) -> bool:
    """forecast.py's Main conditions for one NCAAF total side that can be evaluated without live data."""
    prob = round(prob, 6)
    edge = round(prob - ask, 6)
    if not rule["min_edge"] <= edge <= rule["max_edge"]:
        return False
    if rule["block_fcs"] and r.is_fcs:
        return False
    tg = rule["main_total_gates"]
    if tg.get("max_model_probability") is not None and prob > float(tg["max_model_probability"]):
        return False
    if tg.get("under_min_line") is not None and side == "under" and r.line >= float(tg["under_min_line"]):
        return False
    # feature_basis.projected_total is the extractor's raw (pre-calibration) projection.
    return not (
        tg.get("min_projection_gap") is not None
        and abs(r.raw_total - r.line) < float(tg["min_projection_gap"])
    )


def main_rule_counts(rows: list[SimpleNamespace], slope: float, rule: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for ask in ASSUMED_ASKS:
        block: dict[str, Any] = {}
        for label, s in (("raw_v5", None), ("v5.2_shrunk", slope)):
            over_n = under_n = games = 0
            for r in rows:
                o, u = (
                    (r.p_over, r.p_under)
                    if s is None
                    else shrink_total_probabilities(r.p_over, r.p_under, r.p_push, s)
                )
                oc, uc = side_clears(rule, r, "over", o, ask), side_clears(rule, r, "under", u, ask)
                over_n += oc
                under_n += uc
                games += oc or uc
            block[label] = {"games": games, "over_sides": over_n, "under_sides": under_n}
        out[f"ask_{round(ask * 100, 1)}pct"] = block
    return out


# ------------------------------------------------------------------------------------------------ outputs
def write_markdown(report: dict[str, Any]) -> None:
    fit = report["fit"]
    lines = [
        "# cfb-structural-v5.2: NCAAF total-probability shrink (quick-261123-2w2)",
        "",
        (
            "v5.2 = cfb-structural-v5 with only the total market's served P(over)/P(under) moved to "
            "`sigmoid(s * logit(p))` (intercept 0) on the no-push over share. Moneyline and spread are v5's, bit for bit."
        ),
        "",
        (
            f"**Fitted s = {fit['slope']}** (train-only slope-only logistic, unclipped {fit['slope_unclipped']:.6f}, "
            f"date-bootstrap 95% CI [{fit['bootstrap_95ci'][0]:.4f}, {fit['bootstrap_95ci'][1]:.4f}], {N_BOOT} resamples). "
            f"A slope this close to 0 serves every total at about 50%."
        ),
        "",
        "## Split",
        "",
        (
            f"`validation.chronological_split` fraction mode (60/20/20 on complete kickoff dates) over "
            f"{report['rows']['lined_games']} lined FBS-involved games:"
        ),
        "",
        "| Window | Dates | Lined | Pushes dropped | Scored |",
        "|---|---|---|---|---|",
    ]
    for name in ("train", "validation", "locked_holdout"):
        w = report["windows"][name]
        lines.append(
            f"| {name} | {w['start']} to {w['end']} | {w['games_lined']} | {w['pushes_dropped']} | {w['games_scored']} |"
        )
    lines += [
        "",
        "## Raw v5 vs shrunk, per window (no-push over share vs over/under at the ESPN pinned closing total)",
        "",
        (
            "| Window | Over rate | Model lean vs line (pts) | Brier raw | Brier shrunk | Log loss raw | Log loss shrunk "
            "| Cal. slope raw | Slope-only raw | Mean P(over) raw | Mean P(over) shrunk | Max abs(P(over) - 50%) shrunk |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name in ("train", "validation", "locked_holdout"):
        w = report["windows"][name]
        r, s = w["raw"], w["shrunk"]
        lines.append(
            f"| {name} | {w['over_rate']:.1%} | {w['mean_served_total_minus_line']:+.2f} | {r['brier']:.5f} | "
            f"{s['brier']:.5f} | {r['log_loss']:.5f} | {s['log_loss']:.5f} | {r['calibration_slope']:+.3f} | "
            f"{r['slope_only']:+.4f} | {r['mean_p_over']:.1%} | {s['mean_p_over']:.1%} | {s['max_abs_minus_half']:.2%} |"
        )
    sens = report["sensitivity_v5_artifact_split"]
    lines += [
        "",
        (
            "The shrunk probability's calibration slope is the raw slope divided by s (a rescaling of logit), so it "
            "is not a separate check (it is in the JSON); Brier and log loss are. Validation's own slope-only fit is "
            f"{report['windows']['validation']['raw']['slope_only']:+.4f} (date-bootstrap 95% CI "
            f"[{fit['validation_slope_only_bootstrap_95ci'][0]:+.4f}, {fit['validation_slope_only_bootstrap_95ci'][1]:+.4f}]) "
            "against the train-fitted s; s was not adjusted after seeing it."
        ),
        "",
        "## Sensitivity: v5's own artifact split",
        "",
        (
            f"Train <= {V5_TRAIN_END}, validation <= {V5_VALIDATION_END} (the split v5's layers were fitted on): "
            f"train slope-only s = {sens['slope_unclipped']:.4f} (n {sens['train_scored']}); validation slope-only "
            f"{sens['validation_slope_only']:+.4f}; holdout slope-only {sens['holdout_slope_only']:+.4f}. Not used for the artifact."
        ),
        "",
        "## Locked holdout: games that would still clear the NCAAF Main total rule",
        "",
        (
            "Conditions applied (cli/forecast.py `_forecast_cfb_sport`): "
            f"{report['main_rule']['min_edge']:.0%} <= edge <= {report['main_rule']['max_edge']:.0%} (config min_edge, "
            "artifact max_edge), FCS blocked, model probability <= "
            f"{report['main_rule']['main_total_gates'].get('max_model_probability')}, no under at a line >= "
            f"{report['main_rule']['main_total_gates'].get('under_min_line')}, |raw projection - line| >= "
            f"{report['main_rule']['main_total_gates'].get('min_projection_gap')} points. Not evaluable historically: "
            "`weather_inputs_verified`, the eligibility engine's exposure caps and market-disagreement check. "
            "No historical asks exist, so the ask is assumed at the closing line: 50% (no-vig) and 52.4% (vigged)."
        ),
        "",
        "| Assumed ask | Raw v5 games (over / under sides) | v5.2 games (over / under sides) |",
        "|---|---|---|",
    ]
    for key, block in report["holdout_main_rule"].items():
        a, b = block["raw_v5"], block["v5.2_shrunk"]
        lines.append(
            f"| {key.removeprefix('ask_').replace('pct', '%')} | {a['games']} ({a['over_sides']} / {a['under_sides']}) "
            f"| {b['games']} ({b['over_sides']} / {b['under_sides']}) |"
        )
    hold_dev = report["windows"]["locked_holdout"]["shrunk"]["max_abs_minus_half"]
    lines += [
        "",
        (
            f"The zero holds only for asks at or above 50%: no shrunk holdout probability is more than {hold_dev:.2%} "
            f"from 50%, so a side whose ask is below about {0.5 + hold_dev - report['main_rule']['min_edge']:.1%} "
            "would still clear on price alone. Raw v5 clears more games at 52.4% than at 50% because the 8% "
            "max_edge cap rejects many raw v5 edges at 50%."
        ),
    ]
    rep = report["reproduction_gate"]
    lines += [
        "",
        "## Reproduction gate",
        "",
        (
            f"PASS={rep['pass']}: v5's calibrated total matches `outputs/ncaaf/cfb_v7_v5_repro.json` on "
            f"{rep['matched_events']} holdout events (max abs diff {rep['max_abs_calibrated_total_diff']:.2e}), the joined "
            f"ESPN line matches exactly. The brief's join (per_event + cfb_v5_base_rows actual totals, pushes dropped) "
            f"gives n {rep['brief_join']['n']} (the brief's count), calibration slope "
            f"{rep['brief_join']['calibration_slope']:+.4f} against the brief's +0.008 (close, not identical; the "
            "brief's exact probability construction is not recorded)."
        ),
        "",
        "## Disclosures",
        "",
        (
            "- Every ESPN lined window 2017-2025 and this locked holdout were read before (docs/ROADMAP.md NCAAF section: "
            "v5, v6, v6.1, v6.2, v7, v7.1), and this task's exploration read the holdout slope under both splits before "
            "the fit was run. Market-relative numbers here are design-informed, not a clean test."
        ),
        (
            f"- v5's total_calibration and total_sd were fitted on 2016-08-27..{V5_TRAIN_END}, inside this train window, so "
            "train-window v5 probabilities are in-sample for those layers (train lean vs the line is near 0, "
            "validation and holdout lean +1.3 to +1.8 points). The train-fitted s is still about 0."
        ),
        "- Closing lines are ESPN's pinned median (espn-lines-rule-1), not decision-time Polymarket asks.",
        "",
        "## Provenance",
        "",
        "| Input | sha256 |",
        "|---|---|",
    ]
    for name, digest in report["inputs"].items():
        lines.append(f"| {name} | `{digest}` |")
    lines.append("")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def write_artifact(report: dict[str, Any], fit_sha: str) -> dict[str, Any]:
    base = load_cfb_structural_v5_artifact()
    payload = copy.deepcopy(base)
    payload.pop("artifact_hash", None)
    payload["model_version"] = CFB_V52_MODEL_VERSION
    payload["base_model"] = "cfb-structural-v5"
    payload["total_probability_logit_slope"] = report["fit"]["slope"]
    payload["qualification"] = {
        "status": "CANDIDATE",
        "qualified": False,
        "locked_holdout": True,
        "framework": base["qualification"]["framework"],
        "note": (
            "Operator-approved 2026-11-23 (quick-261123-2w2): v5 with the total probability shrunk toward 50% "
            "because v5's totals carry no information against the close. Promotion via model_promotion, all three "
            "NCAAF markets (cfb_model requires one artifact). Moneyline and spread are v5's; their evidence is v5's."
        ),
    }
    payload["total_shrink_fit"] = {
        "script": "scripts/fit_cfb_v52_total_shrink.py",
        "fit_output": "outputs/ncaaf/cfb_v52_total_shrink.json",
        "fit_output_sha256": fit_sha,
        "base_artifact": "config/models/cfb-structural-v5.json",
        "base_artifact_hash": base["artifact_hash"],
        "formula": "p_over' = sigmoid(s * logit(p_over / (p_over + p_under))) * (1 - p_push); p_under' = 1 - p_push - p_over'",
        "split": {
            "method": report["split"]["method"],
            "train": report["split"]["train"],
            "validation": report["split"]["validation"],
            "locked_holdout": report["split"]["locked_holdout"],
        },
        "fit_output_sha256_basis": "sha256 of the fit JSON's bytes with CRLF folded to LF",
        "fitted_on": (
            "train only (slope-only logistic, pushes dropped, clipped to [0, 1]); checked on validation; holdout "
            "scored once by this script, but it and every ESPN lined window were read before (design-informed)"
        ),
        "slope_unclipped": report["fit"]["slope_unclipped"],
        "bootstrap_95ci": report["fit"]["bootstrap_95ci"],
        "design_informed": True,
    }
    payload["artifact_hash"] = compute_artifact_hash(payload)
    ARTIFACT_OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
    return payload


Cohorts = tuple[list[SimpleNamespace], list[SimpleNamespace], list[SimpleNamespace], dict[str, Any]]


def split_rows(rows: list[SimpleNamespace], **boundaries: str) -> Cohorts:
    """validation.chronological_split on these rows; it reads only ``.date`` (complete-date cohorts)."""
    return chronological_split(rows, **boundaries)  # type: ignore[arg-type,return-value]


def main() -> int:
    model = CFBStructuralV5Model.from_artifact(load_cfb_structural_v5_artifact())
    rows = build_rows(model)
    gate = reproduction_gate(rows, model)
    if not gate["pass"]:
        print(json.dumps(gate, indent=2))
        raise SystemExit("v5 reproduction gate FAILED; nothing written")

    train, validation, holdout, split = split_rows(rows)
    s_raw = fit_slope_only(*xy(train))
    slope = round(min(1.0, max(0.0, s_raw)), SLOPE_DECIMALS)
    ci = bootstrap_slope(train)
    validation_ci = bootstrap_slope(validation)  # report only: s is never re-fitted on validation

    v5_train, v5_val, v5_hold, _ = split_rows(
        rows, train_end_date=V5_TRAIN_END, validation_end_date=V5_VALIDATION_END
    )
    sensitivity = {
        "slope_unclipped": fit_slope_only(*xy(v5_train)),
        "train_scored": len(decided(v5_train)),
        "validation_slope_only": fit_slope_only(*xy(v5_val)),
        "holdout_slope_only": fit_slope_only(*xy(v5_hold)),
    }
    rule = main_rule()
    report = {
        "model_version": CFB_V52_MODEL_VERSION,
        "base_model": "cfb-structural-v5",
        "inputs": {
            "v5_replay_rows (E:/MP/data/research/ncaaf_v7_work/v5_replay_rows.jsonl)": sha256(V5_ROWS),
            "espn_lines (E:/MP/data/research/ncaaf_v6/espn_lines.jsonl)": sha256(ESPN_LINES),
            # Tracked repo files: CRLF-folded so the digests match on any checkout.
            "config/models/cfb-structural-v5.json": text_sha256(CFB_V5_ARTIFACT_PATH),
            "outputs/ncaaf/cfb_v7_v5_repro.json": text_sha256(V5_REPRO),
            "outputs/ncaaf/cfb_v5_base_rows.jsonl": text_sha256(V5_BASE_ROWS),
            "scripts/fit_cfb_v52_total_shrink.py": text_sha256(Path(__file__)),
        },
        "rows": {"lined_games": len(rows), "pushes": sum(r.push for r in rows)},
        "reproduction_gate": gate,
        "split": split,
        "fit": {
            "method": "slope-only logistic (intercept 0) of over at the closing total on logit(v5 no-push over share)",
            "slope_unclipped": s_raw,
            "slope": slope,
            "bootstrap_95ci": ci,
            "bootstrap": f"{N_BOOT} resamples of train kickoff dates, seed {BOOT_SEED}",
            "validation_slope_only_bootstrap_95ci": validation_ci,
            "adjusted_after_validation": False,
        },
        "windows": {
            "train": window_metrics(train, slope),
            "validation": window_metrics(validation, slope),
            "locked_holdout": window_metrics(holdout, slope),
        },
        "sensitivity_v5_artifact_split": sensitivity,
        "main_rule": rule,
        "holdout_main_rule": main_rule_counts(holdout, slope, rule),
        "assumed_asks": list(ASSUMED_ASKS),
        "disclosures": [
            "ESPN lined windows 2017-2025 and this holdout were read before (docs/ROADMAP.md NCAAF); design-informed.",
            "v5 total_calibration and total_sd were fitted on 2016-08-27..2022-11-11: train-window v5 probabilities are in-sample for those layers.",
            "Main-rule count assumes asks at the closing line; weather verification and eligibility caps not evaluated.",
        ],
    }
    OUT_JSON.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    write_markdown(report)
    payload = write_artifact(report, text_sha256(OUT_JSON))
    print(
        json.dumps(
            {
                "slope": slope,
                "slope_unclipped": s_raw,
                "ci": ci,
                "split": split,
                "artifact_hash": payload["artifact_hash"],
                "fit_sha": text_sha256(OUT_JSON),
                "holdout_main_rule": report["holdout_main_rule"],
                "gate": gate,
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
