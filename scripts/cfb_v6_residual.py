"""Market-anchored residual test for frozen cfb-structural-v6 on ESPN closing lines (quick-261114-srb, item 7).

Question: does (v6 calibrated projection - line) explain any of (actual - line)? If it does not, a model
anchored on the market (line + b * (v6 - line)) has no skill to add and declines every game.

Scripts-only. Reads the frozen v6 fit, the cached v6 projection rows, ESPN's pinned closing lines and outcomes
(through cfb_v6_market_check's loader); writes only outputs/ncaaf/cfb_v6_residual.{json,md}. No fitting on the
holdout window, no grids, no extra features: one pinned rule, below.

Usage (repo root): OPENBLAS_NUM_THREADS=1 PYTHONPATH=src .venv/Scripts/python scripts/cfb_v6_residual.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import cfb_v6_market_check as mkt  # reused loader/hash helpers; that file is not edited
import cfb_v6_research as r6

OUT = REPO / "outputs" / "ncaaf"
OUT_JSON = OUT / "cfb_v6_residual.json"
OUT_MD = OUT / "cfb_v6_residual.md"
EXPECTED_FROZEN_SHA = "6e38b14ca486acfd836c1259e5c4fd4fb8e9b5894a06f367322b1eb0c7b13e70"

# ----------------------------------------------------------------------------------------------------------
# THE PINNED RULE. Written and hashed before any validation- or holdout-window number exists; the hash is
# printed first, and the script reads the holdout window only inside score_holdout(), which is called only for
# a market the rule accepted on the validation window.
# ----------------------------------------------------------------------------------------------------------
RULE: dict[str, Any] = {
    "id": "cfb-v6-residual-rule-1",
    "target": "actual - line (spread: home margin minus implied home margin; total: actual total minus line)",
    "feature": "frozen v6 calibrated projection minus line",
    "frozen_params_sha256": EXPECTED_FROZEN_SHA,
    "line": "ESPN pinned median of non-live providers (rule espn-lines-rule-1); robustness line: book-only median "
    "(providers Opening, accuscore, teamrankings, numberfire, consensus dropped)",
    "cohort": "lined events covered by v6 (cfb_v6_base_rows.jsonl rows with a grid), 2016 burn-in excluded",
    "windows": {
        "fit": "start date <= 2022-11-11",
        "validation": "2022-11-12 through 2024-11-14",
        "holdout": "start date >= 2024-11-15",
    },
    "fit_date_end": "2022-11-11",
    "validation_date_end": "2024-11-14",
    "model": "OLS of (actual - line) on (v6 - line) with intercept, fit on the FIT window only",
    "baseline": "line only = intercept-only fit on the FIT window (line + mean fit-window residual)",
    "accept_if_all_on_VALIDATION_window": [
        (
            "(a) date-clustered bootstrap 95% CI of the validation-window OLS slope of (actual - line) on (v6 - line) "
            "has its lower bound above zero"
        ),
        (
            "(b) out-of-sample MSE delta = MSE(baseline) - MSE(residual model), both with FIT-window coefficients "
            "applied to validation games, has a date-clustered bootstrap 95% CI with its lower bound above zero"
        ),
    ],
    "if_not_accepted": "NO PICK: residual shows no skill; the holdout window is not scored for that market/line",
    "if_accepted": "score the holdout window once: slope + CI, OOS MSE delta + CI (fit coefficients), side record "
    "following v6 against the line (|v6 - line| >= 1, pushes excluded), date-clustered bootstrap CI of hit rate "
    "vs 50%",
    "bootstrap": {"resamples": 2000, "cluster": "game date", "interval": "percentile 2.5 / 97.5", "seed": 17},
    "side_min_gap_points": 1.0,
    "no_tuning": "no extra features, no grids, no per-bucket cuts",
}
RULE_SHA256 = hashlib.sha256(json.dumps(RULE, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
N_BOOT = RULE["bootstrap"]["resamples"]
SEED = RULE["bootstrap"]["seed"]
FIT_END = RULE["fit_date_end"]
VAL_END = RULE["validation_date_end"]
MARKETS = ("total", "spread")
LINE_VARIANTS = ("pinned", "book_only")


# ------------------------------------------------------------------ data
def load_rows() -> tuple[dict[str, dict[str, list[dict[str, Any]]]], dict[str, Any]]:
    """Lined, v6-covered rows per line variant and market, same construction as cfb_v6_market_check v6-espn."""
    frozen = json.loads(r6.FROZEN.read_text(encoding="utf-8"))
    sel = frozen["selected"]
    gk = r6.gkey(sel["lambda"], sel["w_prev"])
    mc, tc = frozen["params"]["margin_calibration"], frozen["params"]["total_calibration"]
    lines, line_meta = mkt._load_espn_lines()
    rows: dict[str, dict[str, list[dict[str, Any]]]] = {v: {m: [] for m in MARKETS} for v in LINE_VARIANTS}
    skipped = {"no_call": 0, "no_line": {v: 0 for v in LINE_VARIANTS}, "burn_in_rows_in_cache": 0}
    for ln in mkt.V6_CACHE.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        eid = r["event_id"]
        if eid not in lines:
            continue
        if "grid" not in r:
            skipped["no_call"] += 1
            continue
        if r["season"] <= r6.BURN_IN_SEASON:
            skipped["burn_in_rows_in_cache"] += 1
            continue
        pm, pt = r["grid"][gk][:2]
        v6 = {"margin": mc["intercept"] + mc["slope"] * pm, "total": tc["intercept"] + tc["slope"] * pt}
        ent = lines[eid]
        for market, keys in (
            ("total", {"pinned": "total", "book_only": "total_ex_opening"}),
            ("spread", {"pinned": "spread_home", "book_only": "spread_home_ex_opening"}),
        ):
            for variant in LINE_VARIANTS:
                raw = ent.get(keys[variant])
                if raw is None:
                    skipped["no_line"][variant] += 1
                    continue
                line = float(raw) if market == "total" else -float(raw)  # spread: implied home margin
                actual = r["total"] if market == "total" else r["margin"]
                model = v6["total"] if market == "total" else v6["margin"]
                rows[variant][market].append(
                    {"event_id": eid, "date": r["date"], "x": model - line, "y": actual - line}
                )
    meta = {
        "frozen_params_sha256": r6.verify_frozen(quiet=True),
        "espn_lines_sha256": mkt.file_sha256(mkt.ESPN_LINES),
        "espn_rule_sha256_values": line_meta["rule_sha256_values"],
        "skipped": skipped,
    }
    return rows, meta


def split_windows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {
        "fit": [r for r in rows if r["date"] <= FIT_END],
        "validation": [r for r in rows if FIT_END < r["date"] <= VAL_END],
        "holdout": [r for r in rows if r["date"] > VAL_END],
    }


# ------------------------------------------------------------------ statistics
def _arrays(rows: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    x = np.array([r["x"] for r in rows], dtype=float)
    y = np.array([r["y"] for r in rows], dtype=float)
    uniq, inv = np.unique(np.array([r["date"] for r in rows]), return_inverse=True)
    return x, y, inv, len(uniq)


def ols(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    xm, ym = x.mean(), y.mean()
    sxx = float(((x - xm) ** 2).sum())
    b = float(((x - xm) * (y - ym)).sum() / sxx) if sxx > 0 else float("nan")
    return float(ym - b * xm), b


def r_squared(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 3 or x.std() == 0 or y.std() == 0:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1] ** 2)


def _boot_counts(n_dates: int, rng: np.random.Generator) -> np.ndarray:
    """B x D matrix of cluster multiplicities for date-clustered resampling."""
    draws = rng.integers(0, n_dates, size=(N_BOOT, n_dates))
    return np.stack([np.bincount(d, minlength=n_dates) for d in draws]).astype(float)


def _ci(samples: np.ndarray) -> list[float]:
    s = np.sort(samples[~np.isnan(samples)])
    return [float(s[int(0.025 * (len(s) - 1))]), float(s[int(0.975 * (len(s) - 1))])]


def slope_ci(x: np.ndarray, y: np.ndarray, inv: np.ndarray, d: int) -> dict[str, Any]:
    """Date-clustered bootstrap CI of the OLS slope (refit on each resample)."""
    cnt = _boot_counts(d, np.random.default_rng(SEED))
    agg = [np.bincount(inv, weights=w, minlength=d) for w in (np.ones_like(x), x, y, x * x, x * y)]
    n, sx, sy, sxx, sxy = (cnt @ a for a in agg)
    with np.errstate(invalid="ignore", divide="ignore"):
        slopes = (sxy - sx * sy / n) / (sxx - sx * sx / n)
    return {"point": ols(x, y)[1], "ci_95": _ci(slopes), "dates": d}


def mse_delta_ci(
    x: np.ndarray, y: np.ndarray, inv: np.ndarray, d: int, coef: dict[str, float], base_const: float
) -> dict[str, Any]:
    """MSE(baseline) - MSE(residual model), fixed (fit-window) coefficients; positive = residual model better."""
    e_base = (y - base_const) ** 2
    e_res = (y - coef["a"] - coef["b"] * x) ** 2
    cnt = _boot_counts(d, np.random.default_rng(SEED))
    n_d = np.bincount(inv, minlength=d).astype(float)
    diff_d = np.bincount(inv, weights=e_base - e_res, minlength=d)
    delta = (cnt @ diff_d) / (cnt @ n_d)
    return {
        "mse_baseline": float(e_base.mean()),
        "mse_residual_model": float(e_res.mean()),
        "delta": float((e_base - e_res).mean()),
        "delta_ci_95": _ci(delta),
        "oos_r2_vs_baseline": float(1 - e_res.sum() / e_base.sum()),
    }


def side_record(x: np.ndarray, y: np.ndarray, inv: np.ndarray, d: int) -> dict[str, Any]:
    gap = RULE["side_min_gap_points"]
    take = (np.abs(x) >= gap) & (y != 0)
    win = take & ((x > 0) == (y > 0))
    pushes = int(((np.abs(x) >= gap) & (y == 0)).sum())
    w_d = np.bincount(inv, weights=win.astype(float), minlength=d)
    t_d = np.bincount(inv, weights=take.astype(float), minlength=d)
    cnt = _boot_counts(d, np.random.default_rng(SEED))
    with np.errstate(invalid="ignore", divide="ignore"):
        rate = (cnt @ w_d) / (cnt @ t_d)
    ci = _ci(rate)
    wins, takes = int(win.sum()), int(take.sum())
    return {
        "wins": wins,
        "losses": takes - wins,
        "pushes_excluded": pushes,
        "hit_rate_pct": 100.0 * wins / takes if takes else None,
        "hit_rate_ci_95_pct": [100.0 * ci[0], 100.0 * ci[1]],
        "ci_excludes_50": bool(ci[0] > 0.5 or ci[1] < 0.5),
    }


def window_block(
    rows: list[dict[str, Any]], coef: dict[str, float], base_const: float, *, with_side: bool = False
) -> dict[str, Any]:
    x, y, inv, d = _arrays(rows)
    out: dict[str, Any] = {
        "n": len(rows),
        "dates": d,
        "date_range": [min(r["date"] for r in rows), max(r["date"] for r in rows)],
        "slope_refit": slope_ci(x, y, inv, d),
        "r_squared_refit": r_squared(x, y),
        "oos_with_fit_coefficients": mse_delta_ci(x, y, inv, d, coef, base_const),
    }
    if with_side:
        out["side_record_following_v6"] = side_record(x, y, inv, d)
    return out


# ------------------------------------------------------------------ the pinned decision procedure
def score_holdout(
    holdout_rows: list[dict[str, Any]], coef: dict[str, float], base_const: float
) -> dict[str, Any]:
    """Called only for an accepted market/line: the single holdout-window scoring."""
    return window_block(holdout_rows, coef, base_const, with_side=True)


def run_one(rows: list[dict[str, Any]]) -> dict[str, Any]:
    win = split_windows(rows)
    xf, yf, _, _ = _arrays(win["fit"])
    a, b = ols(xf, yf)
    coef = {"a": a, "b": b}
    base_const = float(yf.mean())
    res: dict[str, Any] = {
        "coefficients_fit_window": {"intercept": a, "slope": b, "baseline_constant": base_const},
        "window_n": {k: len(v) for k, v in win.items()},
        "fit_window": window_block(win["fit"], coef, base_const),
        "validation_window": window_block(win["validation"], coef, base_const),
    }
    va = res["validation_window"]
    crit_a = va["slope_refit"]["ci_95"][0] > 0
    crit_b = va["oos_with_fit_coefficients"]["delta_ci_95"][0] > 0
    res["criteria_validation"] = {
        "a_slope_ci_lower_above_zero": bool(crit_a),
        "b_mse_delta_ci_lower_above_zero": bool(crit_b),
    }
    res["accepted"] = bool(crit_a and crit_b)
    if res["accepted"]:
        res["verdict"] = "ACCEPTED on validation; holdout scored once (second look, see note)"
        res["holdout_window"] = score_holdout(win["holdout"], coef, base_const)
    else:
        res["verdict"] = "NO PICK: residual shows no skill"
        res["holdout_window"] = "NOT SCORED (market not accepted on validation; holdout kept unspent)"
    return res


# ------------------------------------------------------------------ rendering
def _f(v: float, nd: int = 3) -> str:
    return f"{v:.{nd}f}"


def _ci_s(ci: list[float], nd: int = 3) -> str:
    return f"[{ci[0]:+.{nd}f}, {ci[1]:+.{nd}f}]"


def _window_row(label: str, w: dict[str, Any]) -> str:
    o = w["oos_with_fit_coefficients"]
    return (
        f"| {label} | {w['n']} | {w['date_range'][0]} to {w['date_range'][1]} | {_f(w['slope_refit']['point'])} "
        f"{_ci_s(w['slope_refit']['ci_95'])} | {_f(w['r_squared_refit'], 5)} | "
        f"{_f(o['mse_baseline'], 2)} / {_f(o['mse_residual_model'], 2)} | {o['delta']:+.3f} "
        f"{_ci_s(o['delta_ci_95'])} | {o['oos_r2_vs_baseline']:+.5f} |"
    )


def serving_text(report: dict[str, Any]) -> str:
    acc = [(v, m) for v in LINE_VARIANTS for m in MARKETS if report["results"][v][m]["accepted"]]
    if not acc:
        return (
            "NO PICK on both markets and both line definitions. For serving: the fitted slope is "
            "indistinguishable from zero, so a market-anchored challenger, line + b*(v6 - line), collapses to the "
            "line itself and has no side to take: it would decline every game. v6 adds no information beyond the "
            "ESPN closing line in this data; this does not make v6 a worse rating model, only a non-edge "
            "against a closing benchmark."
        )
    return (
        "Accepted on validation for: "
        + ", ".join(f"{m} ({v} line)" for v, m in acc)
        + ". Holdout numbers for those are a second look at data whose v6-vs-line correlations were already seen "
        "in the v6-espn check, not an independent test; no promotion decision follows from this script."
    )


def render_md(rep: dict[str, Any]) -> str:
    lines = [
        "# cfb-structural-v6 market-anchored residual test (item 7), ESPN closing lines",
        "",
        (
            f"Pinned rule `{RULE['id']}` sha256 `{rep['rule_sha256']}` (canonical JSON of the rule block in "
            "scripts/cfb_v6_residual.py, computed before any validation or holdout number). "
            f"Frozen v6 fit sha256 `{rep['frozen_params_sha256']}`. ESPN lines sha256 `{rep['espn_lines_sha256']}`, "
            f"rule {rep['espn_rule_sha256_values']}. Script sha256 `{rep['script_sha256']}`."
        ),
        "",
        (
            "> The line is a CLOSING benchmark with no observed_at, so this tests whether v6 explains anything the "
            "closing market missed, not whether v6 could have beaten a decision-time quote. The holdout window's "
            "v6-vs-line correlations were already seen in the earlier v6-espn check, so any holdout result below is a "
            "SECOND LOOK at the same data, not an independent test. Nothing was tuned: one pinned rule, no grids, no "
            "extra features. No promotion decision."
        ),
        "",
        "## Verdict",
        "",
    ]
    for v in LINE_VARIANTS:
        for m in MARKETS:
            r = rep["results"][v][m]
            lines.append(f"- **{m}, {v} line**: {r['verdict']}")
    lines += ["", rep["serving"], "", "## Rule", ""]
    lines += ["```json", json.dumps(RULE, indent=2), "```", ""]
    for v in LINE_VARIANTS:
        title = (
            "ESPN pinned median line"
            if v == "pinned"
            else "Robustness: book-only median (Opening, accuscore, teamrankings, numberfire, consensus dropped)"
        )
        lines += [f"## {title}", ""]
        for m in MARKETS:
            r = rep["results"][v][m]
            c = r["coefficients_fit_window"]
            lines += [
                f"### {m.capitalize()}s",
                "",
                (
                    f"Window n: {json.dumps(r['window_n'])}. Fit-window OLS (actual - line = a + b*(v6 - line)): "
                    f"a {c['intercept']:+.3f}, b {c['slope']:+.3f}; baseline constant {c['baseline_constant']:+.3f}."
                ),
                "",
                (
                    "Slope and R-squared are refit within the window with a date-clustered bootstrap CI (2,000 "
                    "resamples). MSE columns apply the FIT-window coefficients (baseline / residual model); the "
                    "fit-window row is in-sample. Delta = baseline MSE minus residual MSE (positive favours the "
                    "residual model)."
                ),
                "",
                "| window | n | dates | slope [95% CI] | R-squared | MSE baseline / model | MSE delta [95% CI] | OOS R-squared vs baseline |",
                "|---|---|---|---|---|---|---|---|",
                _window_row("fit (in-sample)", r["fit_window"]),
                _window_row("validation (selection)", r["validation_window"]),
            ]
            if isinstance(r["holdout_window"], dict):
                lines.append(_window_row("holdout (SECOND LOOK)", r["holdout_window"]))
            lines.append("")
            cr = r["criteria_validation"]
            lines.append(
                f"Validation criteria: (a) slope CI lower bound above zero: {'pass' if cr['a_slope_ci_lower_above_zero'] else 'fail'}; "
                f"(b) OOS MSE delta CI lower bound above zero: {'pass' if cr['b_mse_delta_ci_lower_above_zero'] else 'fail'}. "
                f"**{r['verdict']}.**"
            )
            if isinstance(r["holdout_window"], dict):
                s = r["holdout_window"]["side_record_following_v6"]
                lines.append(
                    f"Holdout side record following v6 against the line (|gap| >= 1, pushes excluded): "
                    f"{s['wins']}-{s['losses']} ({s['pushes_excluded']} pushes), hit rate {s['hit_rate_pct']:.1f}% "
                    f"CI [{s['hit_rate_ci_95_pct'][0]:.1f}%, {s['hit_rate_ci_95_pct'][1]:.1f}%], "
                    f"CI {'excludes' if s['ci_excludes_50'] else 'includes'} 50%. SECOND LOOK."
                )
            else:
                lines.append(f"Holdout window: {r['holdout_window']}.")
            lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    print("pinned rule", RULE["id"], "sha256", RULE_SHA256, flush=True)
    rows, meta = load_rows()
    if meta["frozen_params_sha256"] != EXPECTED_FROZEN_SHA:
        raise SystemExit(f"frozen sha mismatch: {meta['frozen_params_sha256']}")
    expected_n = {"total": 8279, "spread": 8376}  # v6 all-covered counts in the v6-espn check
    for m, n in expected_n.items():
        got = len(rows["pinned"][m])
        if got != n:
            raise SystemExit(f"cohort mismatch for {m}: {got} != {n} (v6-espn check)")
    report: dict[str, Any] = {
        "rule": RULE,
        "rule_sha256": RULE_SHA256,
        "script_sha256": mkt.file_sha256(Path(__file__)),
        "as_of_utc": mkt.AS_OF_UTC,
        "cohort_counts_all_windows": {v: {m: len(rows[v][m]) for m in MARKETS} for v in LINE_VARIANTS},
        "results": {v: {m: run_one(rows[v][m]) for m in MARKETS} for v in LINE_VARIANTS},
        **meta,
    }
    report["serving"] = serving_text(report)
    OUT_JSON.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    OUT_MD.write_text(render_md(report), encoding="utf-8")
    for v in LINE_VARIANTS:
        for m in MARKETS:
            r = report["results"][v][m]
            va = r["validation_window"]
            print(
                v,
                m,
                r["verdict"],
                "| val slope",
                round(va["slope_refit"]["point"], 4),
                [round(z, 4) for z in va["slope_refit"]["ci_95"]],
                "| val mse delta",
                round(va["oos_with_fit_coefficients"]["delta"], 4),
                [round(z, 4) for z in va["oos_with_fit_coefficients"]["delta_ci_95"]],
            )
    print("wrote", OUT_JSON, OUT_MD)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
