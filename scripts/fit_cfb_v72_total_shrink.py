"""Fit cfb-structural-v7.2's total-probability logit shrink and write its artifact (quick-261123-6q5 T1).

v7.2 = cfb-structural-v7.1 with only the total market's served probability moved to
``sigmoid(s * logit(p))`` (intercept 0, 0 <= s <= 1), the v5.2 construction (scripts/fit_cfb_v52_total_shrink.py,
whose slope-only fit, date bootstrap and window metrics this script reuses). This script:

1. Gate 1: the re-walked v7.1 features of the selected combo (``cfbd_portal_m0.5_hl45``, durable copy in
   E:\\MP\\data\\research\\ncaaf_v71_work) must match the sha256 pins in ``cfb_v71_spec.json`` (train/validation
   file) and ``cfb_v71_rule.json`` (holdout file);
2. Gate 2: ``CFBStructuralV7Model(v7.1).means`` on those rows must reproduce v7.1's published stage-2 numbers
   (validation n 1858: margin MAE 12.6314, total MAE 12.8311; holdout n 1948: margin MAE 12.5057, total MAE
   12.8432), using the walk's own window labels (v7.1's train/validation/holdout boundaries);
3. prices v7.1's raw total pmf (the served model class, analytic, no RNG) at the ESPN pinned closing total
   (espn-lines-rule-1, the v5.2 join) for every called game with a line;
4. splits 60/20/20 on complete kickoff dates with ``validation.chronological_split`` (fraction mode, as v5.2),
   fits s on TRAIN ONLY (slope-only logistic of over at the closing total on logit of v7.1's no-push over share,
   pushes dropped, clipped to [0, 1]), checks it on validation, scores the holdout once; a sensitivity refit on
   v7.1's own artifact split (train_end / validation_end read from the artifact) is reported, not used;
5. counts holdout games that would still clear the NCAAF Main total rule at a 50% and a 48% ask, raw vs shrunk;
6. writes outputs/ncaaf/cfb_v72_total_shrink.{json,md}; then, only if s did not clip at 1.0 and the fit did not
   fail (pre-committed stop rule, plan section 4 / OD-7), config/models/cfb-structural-v7.2.json (v7.1's artifact
   + slope + provenance + v5.2's ``gates`` verbatim (OD-1) + a CANDIDATE qualification, hash recomputed).
   cfb-structural-v7.1.json is never modified.

Disclosures (also in the report): this is the THIRD read of the holdout window (v7 2026-11-21, v7.1 2026-11-22,
this fit) and every ESPN lined window 2017-2025 was read before, so market-relative numbers are design-informed.
v7.1's total calibration and total SD buckets were fitted on its train window (<= 2022-11-11), inside this
script's train window, so train-window v7.1 probabilities are in-sample for those layers. CFBD inputs are
current-revised values.

Inputs are read-only except the walk directory, which ``scripts/cfb_v71_fit.py walk`` writes.
Run: OPENBLAS_NUM_THREADS=1 PYTHONPATH=src python scripts/fit_cfb_v72_total_shrink.py
Percentages and points only; no American odds.
"""

from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import yaml

REPO = Path(__file__).resolve().parents[1]
MAIN = Path(r"E:\MP")  # read-only research inputs (untracked data in the main checkout)
WALK_DIR = MAIN / "data" / "research" / "ncaaf_v71_work"  # durable copy of the re-walk (survives worktrees)
COMBO = "cfbd_portal_m0.5_hl45"
WALK_TV = WALK_DIR / f"walk_{COMBO}.jsonl"
WALK_HOLDOUT = WALK_DIR / f"walk_{COMBO}.holdout.jsonl"
ESPN_LINES = MAIN / "data" / "research" / "ncaaf_v6" / "espn_lines.jsonl"
V71_SPEC = REPO / "outputs" / "ncaaf" / "cfb_v71_spec.json"
V71_RULE = REPO / "outputs" / "ncaaf" / "cfb_v71_rule.json"
V71_REPORT = REPO / "outputs" / "ncaaf" / "cfb_v71_report.json"
V52_ARTIFACT = REPO / "config" / "models" / "cfb-structural-v5.2.json"
V52_SCRIPT = REPO / "scripts" / "fit_cfb_v52_total_shrink.py"
MODEL_YAML = REPO / "config" / "model.yaml"
OUT_JSON = REPO / "outputs" / "ncaaf" / "cfb_v72_total_shrink.json"
OUT_MD = REPO / "outputs" / "ncaaf" / "cfb_v72_total_shrink.md"
ARTIFACT_OUT = REPO / "config" / "models" / "cfb-structural-v7.2.json"

MODEL_VERSION = "cfb-structural-v7.2"
BASE_MODEL = "cfb-structural-v7.1"
BASE_ARTIFACT_HASH = "e15d12d1af566f04ecc20c8d4df91dc1605fb968cc9f5f68cc8c414e9cf3c022"  # plan section 4
SLOPE_DECIMALS = 4
# No historical asks exist for these games. Two stated prices for the Main-rule count (plan section 4): the no-vig
# fair price at the closing line (50%) and 48%, below which v5.2's shrunk probabilities would clear on price alone.
ASSUMED_ASKS = (0.50, 0.48)
GATE2_TOLERANCE = 5e-5  # published MAEs are rounded to 4 decimals

sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))
import fit_cfb_v52_total_shrink as v52  # the v5.2 fit's slope-only fit, date bootstrap and window metrics

from model_prediction.cfb_replay import shrink_total_probabilities
from model_prediction.features import cfb_features_v7 as core
from model_prediction.models.cfb_structural_v4 import main_gates_from_artifact
from model_prediction.models.cfb_structural_v7 import (
    CFB_V71_ARTIFACT_PATH,
    CFB_V71_MODEL_VERSION,
    CFBStructuralV7Model,
    load_cfb_structural_v7_artifact,
)
from model_prediction.production_registry import compute_artifact_hash
from model_prediction.validation import chronological_split

sha256 = v52.sha256
text_sha256 = v52.text_sha256


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


# ------------------------------------------------------------------------------------------------- gate 1
def gate1() -> dict[str, Any]:
    spec_pin = json.loads(V71_SPEC.read_text(encoding="utf-8"))["walk_files_sha256"][
        f"v71/walk_{COMBO}.jsonl"
    ]
    rule_pin = json.loads(V71_RULE.read_text(encoding="utf-8"))["holdout_walk_files_sha256"][
        WALK_HOLDOUT.name
    ]
    got_tv, got_h = sha256(WALK_TV), sha256(WALK_HOLDOUT)
    return {
        "pass": got_tv == spec_pin and got_h == rule_pin,
        "train_validation": {"file": str(WALK_TV), "sha256": got_tv, "pin_cfb_v71_spec": spec_pin},
        "holdout": {"file": str(WALK_HOLDOUT), "sha256": got_h, "pin_cfb_v71_rule": rule_pin},
    }


# ------------------------------------------------------------------------------------------------- pricing
def features_of(r: dict[str, Any]) -> core.MatchupFeatures:
    return core.MatchupFeatures(
        home=core.SideFeatures(team_id=str(r["home_id"]), values=dict(r["home_e"])),
        away=core.SideFeatures(team_id=str(r["away_id"]), values=dict(r["away_e"])),
        home_games=int(r["home_games"]),
        away_games=int(r["away_games"]),
        is_fbs_vs_fcs=bool(r["is_fcs"]),
        neutral=bool(r["neutral"]),
    )


def load_lines() -> dict[str, float]:
    out: dict[str, float] = {}
    for rec in read_jsonl(ESPN_LINES):
        line = (rec.get("selected") or {}).get("total_line")
        if line is not None:
            out[str(rec["event_id"])] = float(line)
    return out


def price_rows(model: CFBStructuralV7Model, walk: list[dict[str, Any]]) -> list[SimpleNamespace]:
    """Every called walk row, priced by the served model; total fields only where an ESPN closing total exists."""
    lines = load_lines()
    out = []
    for r in walk:
        if "no_call" in r:
            continue
        line = lines.get(str(r["event_id"]))
        pr = model.price(features_of(r), total_line=line)
        row = SimpleNamespace(
            date=r["date"],
            event_id=str(r["event_id"]),
            window=r["window"],
            margin=float(r["margin"]),
            actual=float(r["total"]),
            mu_margin=pr.mu_margin,
            mu_total=pr.mu_total,
            served_total=pr.mu_total,  # v52.window_metrics reads served_total
            p_home=pr.probabilities["home_win"],
            is_fcs=bool(r["is_fcs"]),
            line=line,
        )
        if line is not None:
            row.p_over = pr.probabilities["over"]
            row.p_under = pr.probabilities["under"]
            row.p_push = pr.probabilities["push_total"]
            row.push = row.actual == line
            row.y = int(row.actual > line)
        out.append(row)
    out.sort(key=lambda x: (x.date, x.event_id))
    return out


# ------------------------------------------------------------------------------------------------- gate 2
def gate2(priced: list[SimpleNamespace]) -> dict[str, Any]:
    spec = json.loads(V71_SPEC.read_text(encoding="utf-8"))
    sel = spec["selected"]
    cand = next(
        c for c in spec["candidates"] if c["combo"] == sel["combo"] and c["feature_set"] == sel["feature_set"]
    )
    published_holdout = json.loads(V71_REPORT.read_text(encoding="utf-8"))["outcomes"]["all"]["v71"]
    expected = {
        "validation": {
            "n": cand["validation_n"],
            "margin_mae": cand["validation_margin_mae"],
            "total_mae": cand["validation_total_mae"],
            "ml_brier": spec["validation_scores"]["ml_brier"],
        },
        "holdout": {
            "n": published_holdout["n"],
            "margin_mae": published_holdout["margin_mae"],
            "total_mae": published_holdout["total_mae"],
            "ml_brier": published_holdout["ml_brier"],
        },
    }
    out: dict[str, Any] = {"pass": True}
    for window, exp in expected.items():
        rows = [r for r in priced if r.window == window]
        decided = [r for r in rows if r.margin != 0]
        got = {
            "n": len(rows),
            "margin_mae": float(np.mean([abs(r.margin - r.mu_margin) for r in rows])),
            "total_mae": float(np.mean([abs(r.actual - r.mu_total) for r in rows])),
            "ml_brier": float(np.mean([(r.p_home - float(r.margin > 0)) ** 2 for r in decided])),
        }
        ok = (
            got["n"] == exp["n"]
            and abs(got["margin_mae"] - exp["margin_mae"]) < GATE2_TOLERANCE
            and abs(got["total_mae"] - exp["total_mae"]) < GATE2_TOLERANCE
            and abs(got["ml_brier"] - exp["ml_brier"]) < 5e-6  # published Brier: 5 decimals
        )
        out[window] = {"pass": ok, "got": got, "published": exp}
        out["pass"] = out["pass"] and ok
    return out


# ------------------------------------------------------------------------------------------------ main rule
def main_rule(gates_artifact: dict[str, Any]) -> dict[str, Any]:
    cfg = yaml.safe_load(MODEL_YAML.read_text(encoding="utf-8"))["models"]["NCAAF"]
    gates = main_gates_from_artifact(gates_artifact)
    return {
        "min_edge": float(cfg["min_edge"]),
        "max_edge": float(gates.max_edge),
        "block_fcs": bool(gates.block_fcs),
        "main_total_gates": dict(cfg.get("main_total_gates") or {}),
    }


def side_clears(rule: dict[str, Any], r: SimpleNamespace, side: str, prob: float, ask: float) -> bool:
    """forecast.py's Main conditions for one NCAAF total side that can be evaluated without live data. For a v7
    contract ``feature_basis.projected_total`` is the calibrated mean (cfb_structural_v7_challenger), so the
    projection gap is measured from ``mu_total``."""
    prob = round(prob, 6)
    edge = round(prob - ask, 6)
    if prob < 0.5 or not rule["min_edge"] <= edge <= rule["max_edge"]:
        return False
    if rule["block_fcs"] and r.is_fcs:
        return False
    tg = rule["main_total_gates"]
    if tg.get("max_model_probability") is not None and prob > float(tg["max_model_probability"]):
        return False
    if tg.get("under_min_line") is not None and side == "under" and r.line >= float(tg["under_min_line"]):
        return False
    return not (
        tg.get("min_projection_gap") is not None
        and abs(r.mu_total - r.line) < float(tg["min_projection_gap"])
    )


def main_rule_counts(
    rows: list[SimpleNamespace], slope: float | None, rule: dict[str, Any]
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for ask in ASSUMED_ASKS:
        block: dict[str, Any] = {}
        for label, s in (("raw_v7.1", None), ("v7.2_shrunk", slope)):
            if label == "v7.2_shrunk" and s is None:
                continue
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
def fmt_pct(p: float) -> str:
    return f"{p:.1%}"


def write_markdown(report: dict[str, Any]) -> None:
    fit = report["fit"]
    g1, g2 = report["gate1"], report["gate2"]
    stop = report["stop"]
    lines = [
        "# cfb-structural-v7.2: NCAAF total-probability shrink on v7.1 (quick-261123-6q5 T1)",
        "",
        (
            "v7.2 = cfb-structural-v7.1 with only the total market's served P(over)/P(under) moved to "
            "`sigmoid(s * logit(p))` (intercept 0) on the no-push over share, the v5.2 construction. Moneyline and "
            "spread are v7.1's, bit for bit."
        ),
        "",
    ]
    if stop["stopped"]:
        lines += [f"**STOPPED (pre-committed stop rule): {stop['reason']}. No artifact was written.**", ""]
    if fit.get("slope") is not None:
        lines += [
            (
                f"**Fitted s = {fit['slope']}** (train-only slope-only logistic, unclipped "
                f"{fit['slope_unclipped']:.6f}, date-bootstrap 95% CI [{fit['bootstrap_95ci'][0]:.4f}, "
                f"{fit['bootstrap_95ci'][1]:.4f}], {v52.N_BOOT} resamples, seed {v52.BOOT_SEED})."
            ),
            "",
        ]
    elif fit.get("slope_unclipped") is not None:
        lines += [
            (
                f"Train-only slope-only fit (unclipped) {fit['slope_unclipped']:.6f}, date-bootstrap 95% CI "
                f"[{fit['bootstrap_95ci'][0]:.4f}, {fit['bootstrap_95ci'][1]:.4f}]. Window metrics below are raw "
                "only (the shrunk column equals raw at s = 1)."
            ),
            "",
        ]
    lines += [
        "## Gates",
        "",
        (
            f"- **Gate 1 (walk sha256 pin): {'PASS' if g1['pass'] else 'FAIL'}.** Re-walked `walk_{COMBO}.jsonl` "
            f"`{g1['train_validation']['sha256']}` vs the `cfb_v71_spec.json` pin "
            f"`{g1['train_validation']['pin_cfb_v71_spec']}`; holdout file `{g1['holdout']['sha256']}` vs the "
            f"`cfb_v71_rule.json` pin `{g1['holdout']['pin_cfb_v71_rule']}`."
        ),
    ]
    if g2:
        lines.append(
            f"- **Gate 2 (v7.1 stage-2 reproduction through `CFBStructuralV7Model.means`): "
            f"{'PASS' if g2['pass'] else 'FAIL'}.**"
        )
        lines += [
            "",
            "| Window | n | Margin MAE | Total MAE | ML Brier | Published (n / margin / total / Brier) |",
        ]
        lines.append("|---|---|---|---|---|---|")
        for w in ("validation", "holdout"):
            got, pub = g2[w]["got"], g2[w]["published"]
            lines.append(
                f"| {w} | {got['n']} | {got['margin_mae']:.4f} | {got['total_mae']:.4f} | {got['ml_brier']:.5f} | "
                f"{pub['n']} / {pub['margin_mae']:.4f} / {pub['total_mae']:.4f} / {pub['ml_brier']:.5f} |"
            )
    lines.append("")
    if "windows" in report:
        lines += [
            "## Split",
            "",
            (
                f"`validation.chronological_split` fraction mode (60/20/20 on complete kickoff dates) over "
                f"{report['rows']['lined_games']} called, lined games ({report['rows']['called_games']} called, "
                f"{report['rows']['unlined_games']} without an ESPN pinned closing total):"
            ),
            "",
            "| Window | Dates | Lined | Pushes dropped | Scored |",
            "|---|---|---|---|---|",
        ]
        for name in ("train", "validation", "locked_holdout"):
            w = report["windows"][name]
            lines.append(
                f"| {name} | {w['start']} to {w['end']} | {w['games_lined']} | {w['pushes_dropped']} | "
                f"{w['games_scored']} |"
            )
        lines += [
            "",
            "## Raw v7.1 vs shrunk, per window (no-push over share vs over/under at the ESPN pinned closing total)",
            "",
            (
                "| Window | Over rate | Model lean vs line (pts) | Brier raw | Brier shrunk | Log loss raw | Log loss "
                "shrunk | Cal. slope raw | Slope-only raw | Mean P(over) raw | Mean P(over) shrunk | Max abs(P(over) "
                "- 50%) shrunk |"
            ),
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for name in ("train", "validation", "locked_holdout"):
            w = report["windows"][name]
            r, s = w["raw"], w["shrunk"]
            lines.append(
                f"| {name} | {w['over_rate']:.1%} | {w['mean_served_total_minus_line']:+.2f} | {r['brier']:.5f} | "
                f"{s['brier']:.5f} | {r['log_loss']:.5f} | {s['log_loss']:.5f} | {r['calibration_slope']:+.3f} | "
                f"{r['slope_only']:+.4f} | {r['mean_p_over']:.1%} | {s['mean_p_over']:.1%} | "
                f"{s['max_abs_minus_half']:.2%} |"
            )
        sens = report["sensitivity_v71_artifact_split"]
        lines += [
            "",
            (
                "The shrunk probability's calibration slope is the raw slope divided by s (a rescaling of logit), so it "
                "is not a separate check (it is in the JSON); Brier and log loss are. Validation's own slope-only fit "
                f"is {report['windows']['validation']['raw']['slope_only']:+.4f} (date-bootstrap 95% CI "
                f"[{fit['validation_slope_only_bootstrap_95ci'][0]:+.4f}, "
                f"{fit['validation_slope_only_bootstrap_95ci'][1]:+.4f}]) against the train-fitted s; s was not "
                "adjusted after seeing it."
            ),
            "",
            "## Sensitivity: v7.1's own artifact split",
            "",
            (
                f"Train <= {sens['train_end']}, validation <= {sens['validation_end']} (read from the v7.1 artifact; "
                f"the split its stage 2, total calibration and total SDs were fitted on): train slope-only s = "
                f"{sens['slope_unclipped']:.4f} (n {sens['train_scored']}); validation slope-only "
                f"{sens['validation_slope_only']:+.4f}; holdout slope-only {sens['holdout_slope_only']:+.4f}. Not "
                "used for the artifact."
            ),
            "",
            "## Locked holdout: games that would still clear the NCAAF Main total rule",
            "",
        ]
        mr = report["main_rule"]
        tg = mr["main_total_gates"]
        lines += [
            (
                "Conditions applied (cli/forecast.py `_forecast_cfb_sport`, eligibility): model probability >= 50%, "
                f"{mr['min_edge']:.0%} <= edge <= {mr['max_edge']:.0%} (config min_edge, the copied v5.2 gates' "
                f"max_edge), FCS blocked, model probability <= {fmt_pct(float(tg['max_model_probability']))}, no under "
                f"at a line >= {tg['under_min_line']}, |projected total - line| >= {tg['min_projection_gap']} points "
                "(v7 contracts carry the calibrated mean as `projected_total`). **Not evaluable historically, and "
                "decisive in serving: `pregame_weather_unverified` rejects every v7-family total in Main (v7 contracts "
                "carry no verified weather; OD-5 keeps the gate), so no v7.2 total reaches Main whatever these counts "
                "say.** Also not evaluated: exposure caps and the market-disagreement check. No historical asks exist, "
                "so the ask is assumed: 50% (no-vig at the closing line) and 48%."
            ),
            "",
            "| Assumed ask | Raw v7.1 games (over / under sides) | v7.2 games (over / under sides) |",
            "|---|---|---|",
        ]
        for key, block in report["holdout_main_rule"].items():
            a = block["raw_v7.1"]
            b = block.get("v7.2_shrunk")
            b_txt = f"{b['games']} ({b['over_sides']} / {b['under_sides']})" if b else "n/a"
            lines.append(
                f"| {key.removeprefix('ask_').replace('pct', '%')} | {a['games']} ({a['over_sides']} / "
                f"{a['under_sides']}) | {b_txt} |"
            )
        hold_dev = report["windows"]["locked_holdout"]["shrunk"]["max_abs_minus_half"]
        lines += [
            "",
            (
                f"No shrunk holdout probability is more than {hold_dev:.2%} from 50%, so a side clears the "
                f"{mr['min_edge']:.0%} minimum edge on price alone only at an ask at or below about "
                f"{0.5 + hold_dev - mr['min_edge']:.1%}."
            ),
        ]
        if fit.get("slope") == 0.0:
            lines += [
                "",
                (
                    "**s = 0: every total on a half-point line is served at exactly 50% over and 50% under** (an "
                    "integer line keeps its push mass, so both sides sit below 50% and fail the 50% eligibility "
                    "floor). At a 48% ask the edge is exactly the 2% minimum, a boundary that floating point can tip "
                    "either way in serving, and both sides of one game can clear together (the 48% row counts over and "
                    "under sides separately; games where only the over clears are the under-line rule's). At a 50% "
                    "ask nothing clears. In serving the weather gate rejects all of these anyway (OD-5)."
                ),
            ]
        lines.append("")
    lines += [
        "## Gates block copied into the artifact (OD-1, from cfb-structural-v5.2, verbatim; none validated on v7)",
        "",
        (
            f"Edge band {report['copied_gates']['min_edge']:.0%} to {report['copied_gates']['max_edge']:.0%}, "
            f"moneyline ask floor {report['copied_gates']['min_moneyline_ask']:.1%}, max spread line "
            f"{report['copied_gates']['max_spread_line']} points, FCS blocked: {report['copied_gates']['block_fcs']}, "
            f"sizing `{report['copied_gates']['sizing']}`."
        ),
        "",
        "## Disclosures",
        "",
        (
            "- This is the THIRD read of the holdout window (v7 2026-11-21, v7.1 2026-11-22, this fit), and every ESPN "
            "lined window 2017-2025 was read before (docs/ROADMAP.md NCAAF: v5, v6, v6.1, v6.2, v7, v7.1; v5.2's shrink "
            "fit). Market-relative numbers here are design-informed, not a clean test."
        ),
        (
            "- v7.1's total calibration and total SD buckets were fitted on its train window (<= 2022-11-11), inside "
            "this script's 60/20/20 train window, so train-window v7.1 probabilities are in-sample for those layers."
        ),
        (
            "- v7.1 vs v7 was NOT_CONFIRMED; v7.1 has no market skill on totals (total corr with the close +0.009, "
            "cfb_v71_report.md); CFBD inputs are current-revised values."
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


def write_artifact(report: dict[str, Any], fit_sha: str, gates_source: dict[str, Any]) -> dict[str, Any]:
    base = load_cfb_structural_v7_artifact(model_version=CFB_V71_MODEL_VERSION)
    if base["artifact_hash"] != BASE_ARTIFACT_HASH:
        raise SystemExit(f"v7.1 artifact hash {base['artifact_hash']} is not the plan's {BASE_ARTIFACT_HASH}")
    payload = copy.deepcopy(base)
    payload.pop("artifact_hash", None)
    payload["model_version"] = MODEL_VERSION
    payload["base_model"] = BASE_MODEL
    payload["total_probability_logit_slope"] = report["fit"]["slope"]
    payload["gates"] = copy.deepcopy(gates_source["gates"])
    payload["gates_source"] = (
        "config/models/cfb-structural-v5.2.json gates, copied verbatim (quick-261123-6q5 OD-1); not validated on v7"
    )
    payload["note"] = (
        "NCAAF CHAMPION CANDIDATE (quick-261123-6q5): cfb-structural-v7.1 with only the total probability shrunk "
        "toward 50% (v5.2's construction) because v7.1's totals carry no information against the close. Moneyline "
        "and spread are v7.1's, bit for bit, and their evidence is v7.1's (holdout read twice before, NOT_CONFIRMED "
        "vs v7, CFBD current-revised). Promotion only via model_promotion, all three NCAAF markets."
    )
    payload["qualification"] = {
        "status": "CANDIDATE",
        "qualified": False,
        "locked_holdout": True,
        "framework": base["method"],
        "note": (
            "Operator-approved 2026-11-23 (quick-261123-6q5): promote NCAAF v7.1 for spread and moneyline; totals "
            "keep a shrink toward 50%. v7.1 stays the Flat challenger for the 2027 gate (OD-4)."
        ),
    }
    payload["total_shrink_fit"] = {
        "script": "scripts/fit_cfb_v72_total_shrink.py",
        "fit_output": "outputs/ncaaf/cfb_v72_total_shrink.json",
        "fit_output_sha256": fit_sha,
        "fit_output_sha256_basis": "sha256 of the fit JSON's bytes with CRLF folded to LF",
        "base_artifact": "config/models/cfb-structural-v7.1.json",
        "base_artifact_hash": base["artifact_hash"],
        "formula": (
            "p_over' = sigmoid(s * logit(p_over / (p_over + p_under))) * (1 - p_push); "
            "p_under' = 1 - p_push - p_over'"
        ),
        "split": {k: report["split"][k] for k in ("method", "train", "validation", "locked_holdout")},
        "fitted_on": (
            "train only (slope-only logistic, pushes dropped, clipped to [0, 1]); checked on validation; holdout "
            "scored once by this script, but it was read twice before and every ESPN lined window was read before "
            "(design-informed)"
        ),
        "slope_unclipped": report["fit"]["slope_unclipped"],
        "bootstrap_95ci": report["fit"]["bootstrap_95ci"],
        "design_informed": True,
        "walk_sha256": report["gate1"]["train_validation"]["sha256"],
        "walk_holdout_sha256": report["gate1"]["holdout"]["sha256"],
    }
    payload["artifact_hash"] = compute_artifact_hash(payload)
    ARTIFACT_OUT.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    return payload


def split_rows(rows: list[SimpleNamespace], **boundaries: str) -> v52.Cohorts:
    return chronological_split(rows, **boundaries)  # type: ignore[arg-type,return-value]


def stop_reason(s_raw: float | None) -> str | None:
    """The pre-committed stop rule (plan section 4, OD-7): a failed fit or a clip at 1.0 returns to the operator.
    A fit pinned to the search bound is a failed fit."""
    if s_raw is None or not math.isfinite(s_raw):
        return "the slope fit failed (no finite estimate)"
    if abs(s_raw) >= 5.0 - 1e-6:
        return f"the slope fit hit its search bound ({s_raw:.6f})"
    if s_raw >= 1.0:
        return f"s clips at 1.0 (unclipped {s_raw:.6f})"
    return None


def main() -> int:
    v71_before = sha256(CFB_V71_ARTIFACT_PATH)
    gates_source = json.loads(V52_ARTIFACT.read_text(encoding="utf-8"))
    copied = main_gates_from_artifact(gates_source)
    report: dict[str, Any] = {
        "model_version": MODEL_VERSION,
        "base_model": BASE_MODEL,
        "inputs": {
            f"walk_{COMBO}.jsonl (E:/MP/data/research/ncaaf_v71_work)": sha256(WALK_TV),
            f"walk_{COMBO}.holdout.jsonl (E:/MP/data/research/ncaaf_v71_work)": sha256(WALK_HOLDOUT),
            "espn_lines (E:/MP/data/research/ncaaf_v6/espn_lines.jsonl)": sha256(ESPN_LINES),
            # Tracked repo files: CRLF-folded so the digests match on any checkout.
            "config/models/cfb-structural-v7.1.json": text_sha256(CFB_V71_ARTIFACT_PATH),
            "config/models/cfb-structural-v5.2.json": text_sha256(V52_ARTIFACT),
            "outputs/ncaaf/cfb_v71_spec.json": text_sha256(V71_SPEC),
            "outputs/ncaaf/cfb_v71_rule.json": text_sha256(V71_RULE),
            "outputs/ncaaf/cfb_v71_report.json": text_sha256(V71_REPORT),
            "scripts/fit_cfb_v52_total_shrink.py": text_sha256(V52_SCRIPT),
            "scripts/fit_cfb_v72_total_shrink.py": text_sha256(Path(__file__)),
        },
        "copied_gates": {
            "min_edge": copied.min_edge,
            "max_edge": copied.max_edge,
            "min_moneyline_ask": copied.min_moneyline_ask,
            "max_spread_line": copied.max_spread_line,
            "block_fcs": copied.block_fcs,
            "sizing": copied.sizing,
        },
        "fit": {},
        "gate2": {},
    }

    def finish(stop: str | None) -> None:
        report["stop"] = {"stopped": stop is not None, "reason": stop}
        OUT_JSON.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
        write_markdown(report)

    report["gate1"] = gate1()
    if not report["gate1"]["pass"]:
        finish("Gate 1 failed: the re-walk does not match the v7.1 sha256 pins")
        print(json.dumps(report["gate1"], indent=1))
        return 2

    model = CFBStructuralV7Model(load_cfb_structural_v7_artifact(model_version=CFB_V71_MODEL_VERSION))
    walk = read_jsonl(WALK_TV) + read_jsonl(WALK_HOLDOUT)
    priced = price_rows(model, walk)
    report["gate2"] = gate2(priced)
    if not report["gate2"]["pass"]:
        finish("Gate 2 failed: v7.1's published stage-2 MAEs were not reproduced")
        print(json.dumps(report["gate2"], indent=1))
        return 2

    rows = [r for r in priced if r.line is not None]
    report["rows"] = {
        "called_games": len(priced),
        "lined_games": len(rows),
        "unlined_games": len(priced) - len(rows),
        "pushes": sum(r.push for r in rows),
    }
    train, validation, holdout, split = split_rows(rows)
    report["split"] = split
    try:
        s_raw: float | None = v52.fit_slope_only(*v52.xy(train))
    except (ValueError, ArithmeticError) as exc:  # the stop rule covers a failed fit; record why
        s_raw = None
        report["fit"]["error"] = repr(exc)
    stop = stop_reason(s_raw)
    slope = None if stop else round(min(1.0, max(0.0, float(s_raw))), SLOPE_DECIMALS)  # type: ignore[arg-type]
    report["fit"].update(
        {
            "method": (
                "slope-only logistic (intercept 0) of over at the closing total on logit(v7.1 no-push over share)"
            ),
            "slope_unclipped": s_raw,
            "slope": slope,
            "bootstrap_95ci": v52.bootstrap_slope(train),
            "bootstrap": f"{v52.N_BOOT} resamples of train kickoff dates, seed {v52.BOOT_SEED}",
            "validation_slope_only_bootstrap_95ci": v52.bootstrap_slope(validation),
            "adjusted_after_validation": False,
        }
    )
    metric_slope = slope if slope is not None else 1.0  # a stopped fit still reports raw-window metrics
    report["windows"] = {
        "train": v52.window_metrics(train, metric_slope),
        "validation": v52.window_metrics(validation, metric_slope),
        "locked_holdout": v52.window_metrics(holdout, metric_slope),
    }
    training = json.loads(CFB_V71_ARTIFACT_PATH.read_text(encoding="utf-8"))["training"]
    s_train, s_val, s_hold, _ = split_rows(
        rows, train_end_date=training["train_end"], validation_end_date=training["validation_end"]
    )
    report["sensitivity_v71_artifact_split"] = {
        "train_end": training["train_end"],
        "validation_end": training["validation_end"],
        "slope_unclipped": v52.fit_slope_only(*v52.xy(s_train)),
        "train_scored": len(v52.decided(s_train)),
        "validation_slope_only": v52.fit_slope_only(*v52.xy(s_val)),
        "holdout_slope_only": v52.fit_slope_only(*v52.xy(s_hold)),
    }
    rule = main_rule(gates_source)
    report["main_rule"] = rule
    report["holdout_main_rule"] = main_rule_counts(holdout, slope, rule)
    report["assumed_asks"] = list(ASSUMED_ASKS)
    report["disclosures"] = [
        "Third read of the holdout window (v7, v7.1, this fit); ESPN lined windows 2017-2025 read before; design-informed.",
        "v7.1 total calibration and total SDs were fitted on <= 2022-11-11: train-window probabilities are in-sample for those layers.",
        "Main-rule count assumes asks of 50% and 48%; the weather gate (rejects every v7 total in Main), exposure caps and disagreement are not evaluated.",
    ]
    finish(stop)

    summary: dict[str, Any] = {
        "gate1": report["gate1"]["pass"],
        "gate2": {w: report["gate2"][w]["got"] for w in ("validation", "holdout")},
        "slope": slope,
        "slope_unclipped": s_raw,
        "ci": report["fit"]["bootstrap_95ci"],
        "brier": {w: (m["raw"]["brier"], m["shrunk"]["brier"]) for w, m in report["windows"].items()},
        "holdout_main_rule": report["holdout_main_rule"],
        "split": {k: split[k] for k in ("train", "validation", "locked_holdout")},
        "fit_sha": text_sha256(OUT_JSON),
        "stop": report["stop"],
    }
    if stop is None:
        payload = write_artifact(report, text_sha256(OUT_JSON), gates_source)
        summary["artifact_hash"] = payload["artifact_hash"]
        summary["artifact_hash_verifies"] = payload["artifact_hash"] == compute_artifact_hash(
            json.loads(ARTIFACT_OUT.read_text(encoding="utf-8"))
        )
    if sha256(CFB_V71_ARTIFACT_PATH) != v71_before:
        raise SystemExit("cfb-structural-v7.1.json changed during the fit")
    summary["v71_artifact_unchanged"] = True
    print(json.dumps(summary, indent=1))
    return 2 if stop else 0


if __name__ == "__main__":
    raise SystemExit(main())
