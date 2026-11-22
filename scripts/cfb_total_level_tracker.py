"""W1: walk-forward level tracker for frozen v6 totals, the v6.1 freeze and the v6.1 comparison.

quick-261115-183, decisions D-183-TRACKER, D-183-SHRINK, D-183-HOLDOUT. Everything pinned lives in
``outputs/ncaaf/cfb_totals_sources_rule.json`` (hash checked by ``cfb_totals_lib.startup``); this script
adds no parameter of its own beyond report formatting.

Subcommands
    tracker [--final]   Pick the tracker window on the select window only (``L.tracker_select``), then report
                        per window (select, validation, and holdout with --final) frozen v6 against v6 +
                        tracker against the closing line: bias, MAE, RMSE, over lean, corr(model - line,
                        actual - line) with date-clustered CIs, paired MAE/RMSE differences against the line
                        and against v6, and the diagnostic tracker on (actual - close total).
    freeze              Read the finished W1-W4 outputs, keep only PIT_PASS components (D-183-SHRINK), write
                        ``cfb_v61_spec.json`` with ``spec_sha256``. Nothing is tuned after this point.
    compare [--final]   v5 (common cohort), v6, v6.1 and the closing line on select/validation/holdout, paired
                        MAE differences with CIs, the plain closing-line verdict, and the parity fixture.

Holdout rows are touched only by ``--final``; ``holdout_reads`` follows D-183-HOLDOUT. Percentages and
points only; no odds format other than probabilities.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cfb_totals_lib as L

OUT = L.REPO / "outputs" / "ncaaf"
TRACKER_JSON = OUT / "cfb_total_level_tracker.json"
TRACKER_MD = OUT / "cfb_total_level_tracker.md"
SPEC_JSON = OUT / "cfb_v61_spec.json"
COMPARE_JSON = OUT / "cfb_v61_comparison.json"
COMPARE_MD = OUT / "cfb_v61_comparison.md"
FIXTURE_JSON = OUT / "cfb_v61_parity_fixture.json"

VERDICT_FILES = {
    "W2": "cfb_line_movement.json",
    "W3": "cfb_weather_bound.json",
    "W4": "cfb_qb_bound.json",
}
SCRIPTS = (
    "cfb_totals_lib.py",
    "cfb_total_level_tracker.py",
    "cfb_line_movement.py",
    "cfb_weather_bound.py",
    "cfb_qb_bound.py",
)
FIXTURE_ROWS = 200
SHRINK = 0.5
MODEL_ID = "cfb-structural-v6.1"


# --------------------------------------------------------------------------- generic helpers
def _clean(obj: Any) -> Any:
    """JSON-safe copy: NaN/inf -> None, numpy scalars -> python."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return [_clean(v) for v in obj.tolist()]
    if isinstance(obj, (np.floating, float)):
        return None if not np.isfinite(obj) else float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def _write_json(path: Path, obj: dict[str, Any]) -> None:
    path.write_text(json.dumps(_clean(obj), indent=2) + "\n", encoding="utf-8")


def _text_sha256(path: Path) -> str:
    """sha256 with CRLF normalised to LF (core.autocrlf checkouts differ from the blob)."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _ci(p: float, lo: float, hi: float) -> dict[str, float]:
    return {"point": float(p), "lo": float(lo), "hi": float(hi)}


def _fmt(c: dict[str, float] | None, nd: int = 2, pct: bool = False) -> str:
    if c is None or c.get("point") is None:
        return "n/a"
    k = 100.0 if pct else 1.0
    u = "%" if pct else ""
    return f"{c['point'] * k:.{nd}f}{u} [{c['lo'] * k:.{nd}f}, {c['hi'] * k:.{nd}f}]"


def stat_table(
    dates: np.ndarray,
    close: np.ndarray,
    actual: np.ndarray,
    models: dict[str, np.ndarray],
    pairs: list[tuple[str, str]],
) -> dict[str, Any]:
    """Per-model bias/MAE/RMSE/over lean/corr and paired |e| / RMSE differences, one shared set of
    date-clustered resamples (``L.cboot``, B = 1000, fixed seed). A model named ``line`` has no lean or corr.
    ``pairs`` entries (a, b) report metric(a) - metric(b); negative = a is better."""
    close, actual = np.asarray(close, float), np.asarray(actual, float)
    models = {k: np.asarray(v, float) for k, v in models.items()}
    layout: list[tuple[str, str]] = []
    for k in models:
        layout += [(k, "mae"), (k, "rmse"), (k, "bias")]
        if k != "line":
            layout += [(k, "over_lean"), (k, "corr_model_line")]
    for a, b in pairs:
        layout += [(f"{a}-{b}", "mae_diff"), (f"{a}-{b}", "rmse_diff")]

    def vec(_d: pd.DataFrame, idx: np.ndarray) -> list[float]:
        ac, cl = actual[idx], close[idx]
        errs: dict[str, np.ndarray] = {}
        out: list[float] = []
        for k, arr in models.items():
            m = arr[idx]
            e = m - ac
            errs[k] = e
            out += [float(np.abs(e).mean()), float(np.sqrt((e * e).mean())), float(e.mean())]
            if k != "line":
                gt, lt = int((m > cl).sum()), int((m < cl).sum())
                out += [gt / (gt + lt) if gt + lt else float("nan"), L.corr(m - cl, ac - cl)]
        for a, b in pairs:
            ea, eb = errs[a], errs[b]
            out += [
                float(np.abs(ea).mean() - np.abs(eb).mean()),
                float(np.sqrt((ea * ea).mean()) - np.sqrt((eb * eb).mean())),
            ]
        return out

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        point, lo, hi = L.cboot(pd.DataFrame({"date": np.asarray(dates)}), vec)
    flat = {lay: _ci(point[i], lo[i], hi[i]) for i, lay in enumerate(layout)}
    out: dict[str, Any] = {"n": len(actual), "n_dates": len(set(np.asarray(dates).tolist()))}
    for k in models:
        entry = {"n": len(actual)}
        for stat in ("mae", "rmse", "bias", "over_lean", "corr_model_line"):
            if (k, stat) in flat:
                entry[stat] = flat[(k, stat)]
        out[k] = entry
    out["paired"] = {
        f"{a}-{b}": {"mae_diff": flat[(f"{a}-{b}", "mae_diff")], "rmse_diff": flat[(f"{a}-{b}", "rmse_diff")]}
        for a, b in pairs
    }
    # cross-check the bootstrap point estimates against the library's plain statistics
    for k in models:
        if k == "line":
            continue
        ref = L.model_stats(models[k], close, actual)
        for stat in ("mae", "rmse", "bias", "over_lean", "corr_model_line"):
            got, want = out[k][stat]["point"], ref[stat]
            if not (np.isnan(want) and np.isnan(got)) and abs(got - want) > 1e-9:
                raise SystemExit(f"internal check failed: {k} {stat} {got} != {want}")
    return out


def _leak_check(feed: pd.DataFrame, rows: pd.DataFrame, allowed: tuple[str, ...]) -> None:
    """No feed game from outside ``allowed`` windows may be eligible for any decision row (D-183-HOLDOUT)."""
    f = feed.sort_values(["t_s", "event_id"], kind="stable")
    hi = np.asarray(L.buffer_eligible_end(f["t_s"].to_numpy(), rows["t_s"].to_numpy()), np.int64)
    outside = np.concatenate([[0], np.cumsum(~f["window"].isin(allowed).to_numpy())])
    bad = outside[hi] > 0
    if bad.any():
        raise SystemExit(f"leak check: {int(bad.sum())} decision rows use feed games outside {allowed}")


def _windows(final: bool) -> list[str]:
    return ["select", "validation", *(["holdout"] if final else [])]


def _allowed_feed_windows(w: str) -> tuple[str, ...]:
    return {
        "select": ("select",),
        "validation": ("select", "validation"),
        "holdout": ("select", "validation", "holdout"),
    }[w]


# --------------------------------------------------------------------------- tracker
def _selection_for_residual(
    feed: pd.DataFrame, sel: pd.DataFrame, resid_col: str, base_col: str
) -> dict[str, Any]:
    """Grid selection for a residual column: lowest RMSE of (base + offset) vs actual on select rows."""
    table = []
    actual = sel["actual"].to_numpy(float)
    base = sel[base_col].to_numpy(float)
    for window in L.TRACKER_GRID:
        off, n = L.tracker_offsets_for(feed, sel, window, resid_col=resid_col)
        table.append(
            {
                "window": list(window),
                "rmse": float(np.sqrt(((base + off - actual) ** 2).mean())),
                "mean_n_used": float(n.mean()),
            }
        )
    best = min(range(len(table)), key=lambda i: (table[i]["rmse"], i))
    return {"grid": table, "selected": table[best]["window"], "selected_index": best}


def _offset_summary(off: np.ndarray, n: np.ndarray) -> dict[str, float]:
    return {
        "mean": float(off.mean()),
        "sd": float(off.std()),
        "min": float(off.min()),
        "max": float(off.max()),
        "mean_n_used": float(n.mean()),
        "share_no_eligible_game": float((n == 0).mean()),
    }


def cmd_tracker(final: bool) -> None:
    hdr = L.startup(__file__, TRACKER_JSON, final=final)
    df = L.load_base()
    check = L.self_check_select()
    if not check["ok"] or not check["n_matches_expected"]:
        raise SystemExit(f"self-check failed (line/v6 MAE on the select window): {check}")

    sel = L.lined(df, "total", windows=("select",)).reset_index(drop=True)
    selection = L.tracker_select(df, sel)
    window = (str(selection["selected"][0]), int(selection["selected"][1]))

    lined_feed = df[df["line"].notna()].copy()
    lined_feed["res_line"] = lined_feed["actual"] - lined_feed["line"]
    lsel = lined_feed[lined_feed["window"] == "select"].reset_index(drop=True)
    diag_sel = _selection_for_residual(lined_feed, lsel, "res_line", "line")
    diag_window = (str(diag_sel["selected"][0]), int(diag_sel["selected"][1]))

    results: dict[str, Any] = {}
    diagnostic: dict[str, Any] = {}
    for w in _windows(final):
        rows = L.lined(df, "total", windows=(w,)).reset_index(drop=True)
        allowed = _allowed_feed_windows(w)
        _leak_check(df, rows, allowed)
        off, n_used = L.tracker_offsets_for(df, rows, window)
        close = rows["line"].to_numpy(float)
        actual = rows["actual"].to_numpy(float)
        v6 = rows["v6"].to_numpy(float)
        res = stat_table(
            rows["date"].to_numpy(),
            close,
            actual,
            {"v6": v6, "v6_tracker": v6 + off, "line": close},
            [("v6", "line"), ("v6_tracker", "line"), ("v6_tracker", "v6")],
        )
        res["offset_summary"] = _offset_summary(off, n_used)
        results[w] = res

        lrows = lined_feed[lined_feed["window"] == w].reset_index(drop=True)
        _leak_check(lined_feed, lrows, allowed)
        d: dict[str, Any] = {}
        for label, win in (("own_selected_window", diag_window), ("at_v6_tracker_window", window)):
            loff, ln = L.tracker_offsets_for(lined_feed, lrows, win, resid_col="res_line")
            lc = lrows["line"].to_numpy(float)
            la = lrows["actual"].to_numpy(float)
            t = stat_table(
                lrows["date"].to_numpy(),
                lc,
                la,
                {"line": lc, "line_tracker": lc + loff},
                [("line_tracker", "line")],
            )
            t["window"] = list(win)
            t["offset_summary"] = _offset_summary(loff, ln)
            d[label] = t
        diagnostic[w] = d

    checks = _expected_checks(results)
    out: dict[str, Any] = dict(hdr)
    out.update(
        {
            "command": "tracker",
            "model": "frozen v6 calibrated total (+ walk-forward level tracker)",
            "cohort": "lined games: forfeits dropped, v6 grid projection present, selected ESPN total line present",
            "cohort_counts": L.cohort_counts(df, "total"),
            "feed_rows": len(df),
            "self_check_select": check,
            "selection": {
                "criterion": "lowest RMSE of (v6 + offset) vs actual on select-window lined games; ties to the earlier grid entry",
                "selected_window": list(window),
                "grid": selection["grid"],
                "v6_rmse_no_tracker": selection["v6_rmse_no_tracker"],
                "n_select_rows": selection["n_select_rows"],
            },
            "results": results,
            "line_tracker_diagnostic": {
                "label": "diagnostic only; the same tracker on (actual - close total), feed = lined games",
                "selection": diag_sel,
                "by_window": diagnostic,
            },
            "expected_result_checks": checks,
            "verdicts": {
                "total_tracker": {
                    "verdict": "ENTERS_V61_BY_OPERATOR_INSTRUCTION",
                    "not_a_taxonomy_verdict": True,
                    "selected_window": list(window),
                    "expected_result_confirmed": checks["confirmed"],
                    "beats_closing_line_holdout": checks.get("beats_closing_line_holdout"),
                    "note": "the tracker enters v6.1 regardless of its validation delta (D-183-TRACKER)",
                }
            },
        }
    )
    _write_json(TRACKER_JSON, out)
    TRACKER_MD.write_text(_tracker_md(out), encoding="utf-8")
    print(
        f"wrote {TRACKER_JSON} and {TRACKER_MD}; selected window {window}; holdout_reads {out['holdout_reads']}"
    )


def _expected_checks(results: dict[str, Any]) -> dict[str, Any]:
    """Descriptive reading of the expected outcome (removes the over lean, adds no edge). These are report
    rules, not pinned thresholds, and never feed a verdict:
    removes the over lean = over lean within 5 points of 50% in every window;
    adds no edge = the tracker does not improve MAE over v6 with a CI entirely below 0 in any window and
    v6 + tracker does not beat the closing line (paired MAE CI entirely below 0) in any window."""
    per: dict[str, Any] = {}
    for w, r in results.items():
        lean6 = r["v6"]["over_lean"]["point"]
        lean61 = r["v6_tracker"]["over_lean"]["point"]
        vs_line = r["paired"]["v6_tracker-line"]["mae_diff"]
        vs_v6 = r["paired"]["v6_tracker-v6"]["mae_diff"]
        per[w] = {
            "over_lean_v6": lean6,
            "over_lean_v6_tracker": lean61,
            "lean_closer_to_50pct": bool(abs(lean61 - 0.5) < abs(lean6 - 0.5)),
            "lean_within_5pts_of_50pct": bool(abs(lean61 - 0.5) <= 0.05),
            "corr_v6": r["v6"]["corr_model_line"]["point"],
            "corr_v6_tracker": r["v6_tracker"]["corr_model_line"]["point"],
            "tracker_improves_mae_over_v6_ci_below_0": bool(vs_v6["hi"] < 0),
            "paired_mae_vs_line_ci_hi_below_0": bool(vs_line["hi"] < 0),
        }
    removes_lean = all(p["lean_within_5pts_of_50pct"] for p in per.values())
    adds_edge = any(
        p["tracker_improves_mae_over_v6_ci_below_0"] or p["paired_mae_vs_line_ci_hi_below_0"]
        for p in per.values()
    )
    out: dict[str, Any] = {
        "per_window": per,
        "rule": "removes the over lean = over lean within 5 points of 50% in every window; adds no edge = no window where the tracker improves paired MAE over v6 with a CI entirely below 0 or where v6 + tracker beats the closing line with a CI entirely below 0",
        "removes_over_lean": bool(removes_lean),
        "adds_edge": bool(adds_edge),
        "confirmed": bool(removes_lean and not adds_edge),
    }
    if "holdout" in per:
        out["beats_closing_line_holdout"] = per["holdout"]["paired_mae_vs_line_ci_hi_below_0"]
    return out


def _model_rows(res: dict[str, Any], names: list[tuple[str, str]]) -> list[str]:
    lines = [
        "| model | n | bias | MAE | RMSE | over lean | corr(model-line, actual-line) |",
        "|---|---|---|---|---|---|---|",
    ]
    for key, label in names:
        m = res[key]
        lean = _fmt(m.get("over_lean"), 1, pct=True)
        cor = _fmt(m.get("corr_model_line"), 3)
        lines.append(
            f"| {label} | {m['n']} | {_fmt(m['bias'])} | {_fmt(m['mae'])} | {_fmt(m['rmse'])} | {lean} | {cor} |"
        )
    return lines


def _paired_rows(res: dict[str, Any]) -> list[str]:
    lines = ["| pair (a - b) | MAE diff | RMSE diff |", "|---|---|---|"]
    for k, v in res["paired"].items():
        lines.append(f"| {k} | {_fmt(v['mae_diff'], 3)} | {_fmt(v['rmse_diff'], 3)} |")
    return lines


def _tracker_md(o: dict[str, Any]) -> str:
    sel = o["selection"]
    win = sel["selected_window"]
    md = [
        "# NCAAF totals W1: walk-forward level tracker on frozen v6 (quick-261115-183)",
        "",
        f"- rule_sha256 `{o['rule_sha256']}`; script sha256 `{o['script_sha256']}`; generated {o['generated_at_utc']}",
        f"- final run: {o['final']}; holdout_reads: {o['holdout_reads']}"
        + (f"; {o['holdout_note']}" if o.get("holdout_note") else ""),
        "- Values are points (bias = mean(model - actual)); brackets are date-clustered 95% CIs (B = 1000, seed 20261115).",
        f"- Select-window self check: line MAE {o['self_check_select']['mae_line']}, v6 MAE {o['self_check_select']['mae_v6']}, n {o['self_check_select']['n_select_total_lined']} (matches the diagnosis).",
        "",
        "## Selected window (select window only)",
        "",
        f"Chosen window: **{win[0]} {win[1]}**. v6 RMSE with no tracker on the select rows: {sel['v6_rmse_no_tracker']:.3f}.",
        "",
        "| window | select RMSE of v6 + offset | mean games used |",
        "|---|---|---|",
    ]
    for g in sel["grid"]:
        mark = " (chosen)" if g["window"] == win else ""
        md.append(f"| {g['window'][0]} {g['window'][1]}{mark} | {g['rmse']:.3f} | {g['mean_n_used']:.1f} |")
    for w, res in o["results"].items():
        md += ["", f"## {w} window (n = {res['n']} lined games, {res['n_dates']} dates)", ""]
        md += _model_rows(res, [("v6", "v6"), ("v6_tracker", "v6 + tracker"), ("line", "closing line")])
        md += ["", *_paired_rows(res), ""]
        s = res["offset_summary"]
        md.append(
            f"Tracker offset: mean {s['mean']:.2f}, sd {s['sd']:.2f}, range [{s['min']:.2f}, {s['max']:.2f}], "
            f"mean games used {s['mean_n_used']:.0f}, rows with no eligible game {s['share_no_eligible_game'] * 100:.1f}%."
        )
    md += ["", "## Line tracker diagnostic (diagnostic only)", ""]
    md.append(
        "Same tracker on (actual - close total) over lined games; window picked on the select window by lowest "
        f"RMSE of (line + offset). Own window: **{o['line_tracker_diagnostic']['selection']['selected'][0]} "
        f"{o['line_tracker_diagnostic']['selection']['selected'][1]}**."
    )
    for w, d in o["line_tracker_diagnostic"]["by_window"].items():
        for label, t in d.items():
            md += ["", f"### {w}, {label} ({t['window'][0]} {t['window'][1]}), n = {t['n']}", ""]
            md += _model_rows(t, [("line", "closing line"), ("line_tracker", "line + line tracker")])
            md += ["", *_paired_rows(t)]
    c = o["expected_result_checks"]
    md += ["", "## Reading against the expectation", "", f"Rule: {c['rule']}.", ""]
    md += [
        "| window | over lean v6 | over lean v6 + tracker | within 5 pts of 50% | corr v6 | corr v6 + tracker | tracker MAE vs v6 CI < 0 | v6 + tracker MAE vs line CI < 0 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for w, p in c["per_window"].items():
        md.append(
            f"| {w} | {p['over_lean_v6'] * 100:.1f}% | {p['over_lean_v6_tracker'] * 100:.1f}% | "
            f"{p['lean_within_5pts_of_50pct']} | {p['corr_v6']:.3f} | {p['corr_v6_tracker']:.3f} | "
            f"{p['tracker_improves_mae_over_v6_ci_below_0']} | {p['paired_mae_vs_line_ci_hi_below_0']} |"
        )
    md += [
        "",
        (
            f"Removes the over lean: **{c['removes_over_lean']}**. Adds edge: **{c['adds_edge']}**. "
            f"Expected result (removes the over lean and adds no edge) confirmed: **{c['confirmed']}**."
        ),
        "",
    ]
    if not o["final"]:
        md.append("Holdout rows were not read in this run (no --final).")
    return "\n".join(md) + "\n"


# --------------------------------------------------------------------------- freeze
def _load_final(name: str) -> dict[str, Any]:
    path = OUT / name
    if not path.exists():
        raise SystemExit(f"freeze needs {path} (workstream output missing)")
    d = json.loads(path.read_text(encoding="utf-8"))
    if d.get("rule_sha256") != L.RULE_SHA256:
        raise SystemExit(f"{name}: rule_sha256 {d.get('rule_sha256')} != pinned {L.RULE_SHA256}")
    if not d.get("final") or int(d.get("holdout_reads") or 0) < 1:
        raise SystemExit(
            f"{name}: not a --final output (final={d.get('final')}, holdout_reads={d.get('holdout_reads')})"
        )
    return d


def _walk_dicts(obj: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(obj, dict):
        found.append(obj)
        for v in obj.values():
            found += _walk_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            found += _walk_dicts(v)
    return found


def _market_of(key: str, v: dict[str, Any]) -> str | None:
    if v.get("market") in ("total", "spread"):
        return str(v["market"])
    k = key.lower()
    if "total" in k:
        return "total"
    if "spread" in k:
        return "spread"
    return None


def _pit_component(source: str, d: dict[str, Any], key: str, v: dict[str, Any]) -> dict[str, Any]:
    """Pull the select-window coefficients for a PIT_PASS verdict out of the workstream output.

    The workstream output must carry the ``dmse_primary`` result dict (keys ``source_coef``, ``features``,
    ``select_feature_means``, ``market``); a forecast-stage pass is matched on a non-null ``eval_features``.
    """
    market = _market_of(key, v)
    if market is None:
        raise SystemExit(f"{source}: PIT_PASS verdict {key!r} has no identifiable market")
    forecast = v.get("stage") == "forecast" or "forecast" in key.lower()
    cands = [
        x
        for x in _walk_dicts(d)
        if x.get("source_coef")
        and x.get("features")
        and x.get("market") == market
        and bool(x.get("eval_features")) == forecast
    ]
    if len(cands) != 1:
        raise SystemExit(
            f"{source}: cannot resolve the coefficients of PIT_PASS verdict {key!r} "
            f"({len(cands)} candidate result blocks); freeze refuses to guess"
        )
    r = cands[0]
    feats = list(r["features"])
    serve = list(r["eval_features"]) if r.get("eval_features") else feats
    coef = r["source_coef"]
    means = r["select_feature_means"]
    terms = [
        {
            "feature": f,
            "serve_column": serve[i],
            "select_ols_coefficient": float(coef[2 + i]),
            "shrunk_coefficient": SHRINK * float(coef[2 + i]),
            "select_mean": float(means[f]),
        }
        for i, f in enumerate(feats)
    ]
    return {
        "source": source,
        "market": market,
        "stage": "forecast" if forecast else "observed",
        "verdict_key": key,
        "applies_to": "total" if market == "total" else "margin",
        "terms": terms,
        "formula": "added after the tracker: sum over terms of shrunk_coefficient * (serve_column value - select_mean)",
    }


def cmd_freeze() -> None:
    hdr = L.startup(__file__, None, final=False)
    tracker = _load_final(TRACKER_JSON.name)
    window = tracker["selection"]["selected_window"]
    components: list[dict[str, Any]] = []
    verdict_table: dict[str, Any] = {}
    for source, name in VERDICT_FILES.items():
        d = _load_final(name)
        verdict_table[source] = {}
        for key, v in (d.get("verdicts") or {}).items():
            verdict = v.get("verdict") if isinstance(v, dict) else None
            verdict_table[source][key] = verdict
            if verdict == "PIT_PASS":
                components.append(_pit_component(source, d, key, v))

    inputs: dict[str, Any] = {
        "rule_file_canonical_sha256": L.rule_sha256(),
        "rule_file_lf_sha256": _text_sha256(L.RULE_PATH),
        "frozen_inputs_sha256": hdr["frozen_inputs_sha256"],
        "v6_frozen_fit_lf_sha256": _text_sha256(L.FROZEN_FIT),
        "v5_artifact_lf_sha256": _text_sha256(L.V5_ARTIFACT),
        "v6_artifact_lf_sha256": _text_sha256(L.REPO / "config" / "models" / "cfb-structural-v6.json"),
        "workstream_outputs_lf_sha256": {
            n: _text_sha256(OUT / n) for n in (TRACKER_JSON.name, *VERDICT_FILES.values())
        },
        "scripts_lf_sha256": {n: L.script_sha256(Path(__file__).resolve().parent / n) for n in SCRIPTS},
    }
    if len(components) == 0:
        composition = "v6.1 = v6 + tracker (no source beyond the tracker is PIT_PASS)"
    else:
        composition = "v6.1 = v6 + tracker + shrunk PIT_PASS components"
    spec: dict[str, Any] = {
        "model_id": MODEL_ID,
        "spec_version": "cfb-v61-spec-1",
        "quick_task": "261115-183",
        "generated_at_utc": L.utc_now_iso(),
        "rule_sha256": L.RULE_SHA256,
        "v6_frozen_params_sha256": L.V6_PARAMS_SHA256,
        "composition": composition,
        "total": "frozen v6 calibrated total (total_calibration.intercept + slope * grid[0.1|0.2][1], no floor) + tracker offset + sum of PIT_PASS total components",
        "margin": "frozen v6 calibrated margin unchanged unless a spread component is PIT_PASS",
        "tracker": {
            "window": {"kind": window[0], "size": int(window[1])},
            "grid_in_order": [list(g) for g in L.TRACKER_GRID],
            "day_window_min_games": L.TRACKER_MIN_DAY_GAMES,
            "day_window_fallback_games": L.TRACKER_FALLBACK_GAMES,
            "result_buffer_hours": L.RESULT_BUFFER_HOURS,
            "result_buffer_rule": "a past game counts at decision time T only if start_utc + 5 h <= T (inclusive)",
            "feed": "every non-forfeit game with a v6 grid projection at the frozen key (lined or not, FBS and FCS)",
            "residual": "actual total - frozen v6 calibrated total",
            "offset": "mean of the residual over the window; 0 when no eligible game",
            "decision_time": "kickoff",
            "selected_on": "select window only (L.tracker_select)",
            "entry": "enters v6.1 by operator instruction regardless of its validation delta",
        },
        "shrinkage": {"id": "D-183-SHRINK", "factor": SHRINK},
        "components": components,
        "source_verdicts": verdict_table,
        "inputs": inputs,
        "nothing_tuned_after_freeze": True,
    }
    spec["spec_sha256"] = L.canonical_sha256({k: v for k, v in spec.items() if k != "spec_sha256"})
    _write_json(SPEC_JSON, spec)
    print(f"wrote {SPEC_JSON}: {composition}; spec_sha256 {spec['spec_sha256']}")


# --------------------------------------------------------------------------- compare
def _load_spec() -> dict[str, Any]:
    if not SPEC_JSON.exists():
        raise SystemExit(f"{SPEC_JSON} missing: run freeze first")
    spec = json.loads(SPEC_JSON.read_text(encoding="utf-8"))
    want = L.canonical_sha256({k: v for k, v in spec.items() if k != "spec_sha256"})
    if want != spec.get("spec_sha256"):
        raise SystemExit(f"spec_sha256 mismatch: file {spec.get('spec_sha256')} recomputed {want}")
    if spec["rule_sha256"] != L.RULE_SHA256 or spec["v6_frozen_params_sha256"] != L.V6_PARAMS_SHA256:
        raise SystemExit("spec rule or v6 params hash differs from the pinned constants")
    inp = spec["inputs"]
    current = {n: _text_sha256(OUT / n) for n in inp["workstream_outputs_lf_sha256"]}
    current_scripts = {
        n: L.script_sha256(Path(__file__).resolve().parent / n) for n in inp["scripts_lf_sha256"]
    }
    if current != inp["workstream_outputs_lf_sha256"] or current_scripts != inp["scripts_lf_sha256"]:
        raise SystemExit(
            "a frozen input output or script changed since freeze: refusing (re-freeze is a new decision)"
        )
    return spec


def _v61_total(df: pd.DataFrame, spec: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if spec["components"]:
        raise SystemExit(
            "spec carries PIT_PASS components: compare does not recompute component features from the "
            "workstream caches in this version; extend cmd_compare before using this spec"
        )
    w = spec["tracker"]["window"]
    off, n = L.tracker_offsets_for(df, df, (w["kind"], int(w["size"])))
    return df["v6"].to_numpy(float) + off, off, n


def cmd_compare(final: bool) -> None:
    hdr = L.startup(__file__, COMPARE_JSON, final=final)
    spec = _load_spec()
    df = L.load_base()
    v61, off, n_used = _v61_total(df, spec)
    df = df.assign(v61=v61)

    results: dict[str, Any] = {}
    for w in _windows(final):
        rows_all = df[df["line"].notna() & (df["window"] == w)].reset_index(drop=True)
        _leak_check(df, rows_all, _allowed_feed_windows(w))
        entry: dict[str, Any] = {}
        for cohort, rows in (
            ("lined", rows_all),
            ("common_v5", rows_all[rows_all["v5"].notna()].reset_index(drop=True)),
        ):
            models = {
                "v6": rows["v6"].to_numpy(float),
                "v6_1": rows["v61"].to_numpy(float),
                "line": rows["line"].to_numpy(float),
            }
            pairs = [("v6_1", "v6"), ("v6_1", "line"), ("v6", "line")]
            if cohort == "common_v5":
                models = {"v5": rows["v5"].to_numpy(float), **models}
                pairs += [("v5", "line"), ("v6_1", "v5")]
            entry[cohort] = stat_table(
                rows["date"].to_numpy(),
                rows["line"].to_numpy(float),
                rows["actual"].to_numpy(float),
                models,
                pairs,
            )
        results[w] = entry

    verdict = _beats_line(results)
    out: dict[str, Any] = dict(hdr)
    out.update(
        {
            "command": "compare",
            "spec_sha256": spec["spec_sha256"],
            "composition": spec["composition"],
            "tracker_window": spec["tracker"]["window"],
            "cohort_counts": L.cohort_counts(df, "total"),
            "results": results,
            "verdicts": {"v6_1_vs_closing_line": verdict},
        }
    )
    fixture = _fixture(df, off, n_used, spec)
    out["parity_fixture"] = {
        "path": "outputs/ncaaf/cfb_v61_parity_fixture.json",
        "rows": len(fixture["rows"]),
        "sha256_lf": hashlib.sha256((json.dumps(fixture, indent=1) + "\n").encode("utf-8")).hexdigest(),
    }
    _write_json(COMPARE_JSON, out)
    FIXTURE_JSON.write_text(json.dumps(fixture, indent=1) + "\n", encoding="utf-8")
    COMPARE_MD.write_text(_compare_md(out), encoding="utf-8")
    print(f"wrote {COMPARE_JSON}, {COMPARE_MD}, {FIXTURE_JSON}; {verdict['statement']}")


def _beats_line(results: dict[str, Any]) -> dict[str, Any]:
    if "holdout" not in results:
        return {
            "verdict": L.PROVISIONAL,
            "statement": "holdout not read (no --final): no closing-line verdict yet.",
        }
    d = results["holdout"]["lined"]["paired"]["v6_1-line"]["mae_diff"]
    beats = bool(d["hi"] < 0)
    v61_bad = bool(d["lo"] > 0)
    if beats:
        state = "v6.1 BEATS the closing line"
    elif v61_bad:
        state = "v6.1 does NOT beat the closing line; the closing line is significantly better"
    else:
        state = "v6.1 does NOT beat the closing line (the holdout CI is not entirely below 0)"
    return {
        "verdict": "BEATS_CLOSING_LINE" if beats else "DOES_NOT_BEAT_CLOSING_LINE",
        "holdout_paired_mae_diff_v6_1_minus_line": d,
        "rule": "holdout paired MAE difference (v6.1 minus line) 95% CI entirely below 0",
        "statement": f"{state}: holdout paired MAE difference (v6.1 minus line) = {d['point']:.3f} points "
        f"[{d['lo']:.3f}, {d['hi']:.3f}], n = {results['holdout']['lined']['n']}.",
    }


def _fixture(df: pd.DataFrame, off: np.ndarray, n_used: np.ndarray, spec: dict[str, Any]) -> dict[str, Any]:
    """200 rows by sorted-event_id stride; history = the full tracker feed in kickoff order (ties by event_id)."""
    w = spec["tracker"]["window"]
    fit = L.v6_frozen_fit()
    tc = fit["params"]["total_calibration"]
    feed = df.sort_values(["t_s", "event_id"], kind="stable")
    resid = (feed["actual"] - feed["v6"]).to_numpy(float)
    ids = sorted(df["event_id"].astype(str))
    stride = max(1, len(ids) // FIXTURE_ROWS)
    pick = ids[::stride][:FIXTURE_ROWS]
    by_id = df.assign(_off=off, _n=n_used).set_index("event_id")
    if not by_id.index.is_unique:
        raise SystemExit("event_id is not unique in the feed")
    f_t = feed["t_s"].to_numpy(np.int64)
    rows = []
    max_diff = 0.0
    for eid in pick:
        r = by_id.loc[eid]
        t = int(r["t_s"])
        hi = int(L.buffer_eligible_end(f_t, t))
        n = int(r["_n"])
        lo = hi - n
        direct = float(resid[lo:hi].mean()) if n > 0 else 0.0
        o = float(r["_off"])
        max_diff = max(max_diff, abs(o - direct))
        rows.append(
            {
                "event_id": eid,
                "start_utc": r["start_utc"],
                "t_s": t,
                "window": r["window"],
                "raw_total": float(r["raw_total"]),
                "v6_total": float(r["v6"]),
                "offset_cumsum": o,
                "offset_mean_direct": direct,
                "n_used": n,
                "eligible_end_index": hi,
                "v6_1_total": float(r["v6"]) + o,
            }
        )
    return {
        "purpose": "parity fixture for the src v6.1 port: 200 rows by sorted event_id stride with tracker inputs and the v6.1 total to full float precision",
        "model_id": MODEL_ID,
        "spec_sha256": spec["spec_sha256"],
        "rule_sha256": L.RULE_SHA256,
        "tracker_window": w,
        "result_buffer_seconds": L.BUFFER_S,
        "day_window_min_games": L.TRACKER_MIN_DAY_GAMES,
        "day_window_fallback_games": L.TRACKER_FALLBACK_GAMES,
        "v6_total_calibration": {"intercept": float(tc["intercept"]), "slope": float(tc["slope"])},
        "total_formula": "v6_1_total = v6_total + offset_cumsum; v6_total = intercept + slope * raw_total (no floor)",
        "offset_formula": "offset_cumsum = (cs[hi] - cs[lo]) / (hi - lo) with cs = [0.0, cumsum(actual - v6_total)] over history in kickoff order, hi = bisect_right(history t_s, T - 5 h), lo per the window rule; offset_mean_direct is the plain mean of the same slice",
        "max_abs_diff_cumsum_vs_direct": max_diff,
        "row_selection": f"event_ids sorted as strings, every {stride}th, first {FIXTURE_ROWS}",
        "history": {
            "order": "kickoff t_s ascending, ties by event_id",
            "t_s": [int(x) for x in f_t],
            "actual": feed["actual"].to_numpy(float).tolist(),
            "v6_total": feed["v6"].to_numpy(float).tolist(),
        },
        "rows": rows,
    }


def _compare_md(o: dict[str, Any]) -> str:
    md = [
        "# NCAAF totals: v5, v6, v6.1 and the closing line (quick-261115-183)",
        "",
        f"- rule_sha256 `{o['rule_sha256']}`; spec_sha256 `{o['spec_sha256']}`; script sha256 `{o['script_sha256']}`; generated {o['generated_at_utc']}",
        f"- {o['composition']}; tracker window {o['tracker_window']['kind']} {o['tracker_window']['size']}",
        f"- final run: {o['final']}; holdout_reads: {o['holdout_reads']}"
        + (f"; {o['holdout_note']}" if o.get("holdout_note") else ""),
        "- Points; bias = mean(model - actual); over lean = share model > close, ties excluded; brackets are date-clustered 95% CIs.",
        "",
        f"## Verdict: {o['verdicts']['v6_1_vs_closing_line']['statement']}",
    ]
    names = {"v5": "v5", "v6": "v6", "v6_1": "v6.1", "line": "closing line"}
    for w, entry in o["results"].items():
        for cohort, res in entry.items():
            label = "common cohort with v5" if cohort == "common_v5" else "all lined games"
            md += ["", f"## {w}, {label} (n = {res['n']}, {res['n_dates']} dates)", ""]
            md += _model_rows(res, [(k, names[k]) for k in names if k in res])
            md += ["", *_paired_rows(res)]
    if not o["final"]:
        md += ["", "Holdout rows were not read in this run (no --final)."]
    return "\n".join(md) + "\n"


# --------------------------------------------------------------------------- entry point
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("tracker", "compare"):
        p = sub.add_parser(name)
        p.add_argument("--final", action="store_true", help="read the holdout (one-time, D-183-HOLDOUT)")
    sub.add_parser("freeze")
    args = ap.parse_args(argv)
    if args.cmd == "tracker":
        cmd_tracker(args.final)
    elif args.cmd == "compare":
        cmd_compare(args.final)
    else:
        cmd_freeze()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
