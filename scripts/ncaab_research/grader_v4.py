"""Spread grader for addendum v4 of NCAAB research protocol 261120 (quick 261121-hmc, lane P).

Imports ``grader.py`` and ``grader_v3.py`` unchanged (their protocol, addendum-v2, addendum-v3 and data-file pins
are verified on every call) and adds what neither can do for the v4 grid: the v4 multiplicity (K spread 25,
cover 16, moneyline 16, total 26), the two registered player-feature grid names, the re-hash of the pinned
player-feature inputs, the v4 reproduction gate and the non-verdict "adjustment non-zero" subset report.
``protocol_261120_addendum_v4.json`` is pinned by its canonical sha256 below; any edit makes this module refuse.

grader.score reads K and the grid from the v1 protocol dict it loads itself, so this module does not call it.
It composes the same steps (load_predictions, validate_echo, baseline_predictions, model_block, paired_vs_v7,
leak_flags, verdict) with a v1 protocol copy whose K and grid are the v4 ones. grader.py stays byte-identical.

The confirmation window stays sealed here: no confirmation read is implemented.

Research only: reads the untracked archives under data/research/, writes scorecards under
outputs/ncaab/research_261120/<grid>/ and appends to outputs/ncaab/research_261120/read_log.jsonl.
Points and probabilities only, never American odds.

    .venv/Scripts/python scripts/ncaab_research/grader_v4.py repro      # spread reproduction gate (logged)
    .venv/Scripts/python scripts/ncaab_research/grader_v4.py score --pred P --meta M [--shuffle-seed 5]
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import grader  # byte-identical v1 grader; verifies the v1/v2 pins on load_protocol
import grader_v3  # byte-identical v3 grader; verifies the v3 pin

nr = grader.nr
REPO = grader.REPO
ADDENDUM_V4_PATH = HERE / "protocol_261120_addendum_v4.json"
ADDENDUM_V4_SHA256 = "ccb107fc6f0b5b525337f8e48e7c9717b27afe824b78f7ea6c354f354618490e"
OUT_V4 = grader.OUT_DIR  # scorecards go to outputs/ncaab/research_261120/<grid>/
READ_LOG = grader.READ_LOG  # shared with v1/v3; tests monkeypatch this attribute
BASELINE_CARD = grader.OUT_DIR / "baseline_scorecard.json"
FEATURE_TABLE_REL = "data/research/ncaab_external/player_features/pit_player_features.jsonl"

# v4 K by statistic family; model_block reads the v1 key name total_market_relative for the totals family
K_V4 = {
    "spread_market_relative": 25,
    "total_market_relative": 26,
    "cover_calibration": 16,
    "moneyline": 16,
}
K_V1_SPREAD = 21  # the reproduction gate reproduces the baseline card at v1's K
GRID = {
    "P1_player_availability": "mean",
    "P2_returning_production_early": "mean",
}
SEALED_SEASON = 2022  # a confirmation season: never fit on, never scored in development
DEV_FIT_SEASONS = frozenset(grader.DEV_SEASONS)
REPRO_GRID = "BASELINE_SPREAD_REPRO_V4"
REPRO_KEYS = ("margin_all", "market_spread", "rule1_spread", "cover")
NONZERO_TOL = 1e-9  # adjustment smaller than this counts as zero in the non-verdict subset report


# --------------------------------------------------------------------------------------------- pins
def load_addendum_v4(path: Path = ADDENDUM_V4_PATH) -> dict[str, Any]:
    """Verify v1 + v2 (grader.load_protocol), v3 (grader_v3) and the v4 pin; return the v4 object."""
    grader.load_protocol()
    grader_v3.load_addendum_v3()
    add = json.loads(Path(path).read_text(encoding="utf-8"))
    got = grader.canonical_sha256(add)
    if got != ADDENDUM_V4_SHA256:
        raise grader.ProtocolMismatchError(f"{path} hashes to {got}, pinned {ADDENDUM_V4_SHA256}")
    k = add["multiplicity"]["K"]
    want = {
        "spread_market_relative": K_V4["spread_market_relative"],
        "total": K_V4["total_market_relative"],
        "cover_calibration": K_V4["cover_calibration"],
        "moneyline": K_V4["moneyline"],
    }
    if k != want:
        raise grader.ProtocolMismatchError(f"v4 K {k} drifted from the module constants {want}")
    if set(add["candidate_grid"]) != set(GRID):
        raise grader.ProtocolMismatchError("v4 grid drifted from the module constants")
    return add


def pinned_input_shas(add: dict[str, Any]) -> dict[str, str]:
    """The addendum's file -> sha256 pins (its prose 'rule' key is not a file)."""
    return {k: v for k, v in add["pinned_inputs_sha256"].items() if k != "rule"}


def verify_pinned_inputs_v4(pins: dict[str, str], root: Path = REPO) -> dict[str, str]:
    """Re-hash the feature table and its builders; refuse on any mismatch (a rebuilt table is a new addendum)."""
    checked = {}
    for rel, want in pins.items():
        p = Path(root) / rel
        if not p.exists():
            raise grader.ProtocolMismatchError(f"pinned input {rel} is missing")
        got = nr.sha256_file(p)
        if got != want:
            raise grader.ProtocolMismatchError(
                f"{rel} hashes to {got}, pinned {want}; a rebuild needs a new addendum"
            )
        checked[rel] = got
    return checked


def proto_v4() -> dict[str, Any]:
    """A copy of the verified v1 protocol with the v4 K and grid; the file on disk is never touched."""
    p = copy.deepcopy(grader.load_protocol())
    p["multiplicity"]["K"] = dict(K_V4)
    p["candidate_grid"] = {name: {"family": fam} for name, fam in GRID.items()}
    return p


# --------------------------------------------------------------------------------------------- read log
def _read_log_entries(path: Path | None = None) -> list[dict[str, Any]]:
    p = path or READ_LOG
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def append_read_log(entry: dict[str, Any]) -> int:
    """Append a v4 read (BEFORE any statistic exists) and return the number of distinct prediction files read
    under this grid name in this window, this one included."""
    for key in ("grid_name", "predictions_sha256", "window", "candidate"):
        if key not in entry:
            raise ValueError(f"read-log entry lacks {key!r}")
    entry = {
        **entry,
        "at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "protocol_sha256": grader.PROTOCOL_SHA256,
        "addendum_v4_sha256": ADDENDUM_V4_SHA256,
    }
    prior = _read_log_entries()
    READ_LOG.parent.mkdir(parents=True, exist_ok=True)
    with READ_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    shas = {
        p["predictions_sha256"]
        for p in prior
        if p.get("grid_name") == entry["grid_name"] and p.get("window") == entry["window"]
    }
    shas.add(entry["predictions_sha256"])
    return len(shas)


def reread_guard(
    v: dict[str, Any], registered: bool, read_count: int | None, grid_name: str
) -> dict[str, Any]:
    """Only the first predictions file read under a grid name (per window) carries a verdict."""
    if registered and read_count is not None and read_count > 1:
        return {
            "status": "REREAD_NO_VERDICT",
            "reason": f"read {read_count} of grid name {grid_name}: only the first file carries a verdict",
            "would_have_been": v,
        }
    return v


def frame_sha256(df: pd.DataFrame) -> str:
    return hashlib.sha256(pd.util.hash_pandas_object(df, index=False).values.tobytes()).hexdigest()


# --------------------------------------------------------------------------------------------- submissions
def validate_meta_v4(meta: dict[str, Any], seasons: Iterable[int], pins: dict[str, str]) -> dict[str, Any]:
    for key in (
        "candidate",
        "grid_name",
        "protocol_sha256",
        "addendum_v4_sha256",
        "code_sha256",
        "fit_seasons_by_predicted_season",
        "data_sources",
    ):
        if key not in meta:
            raise grader.SubmissionError(f"sidecar meta lacks {key!r}")
    if meta["protocol_sha256"] != grader.PROTOCOL_SHA256:
        raise grader.SubmissionError("sidecar protocol_sha256 does not match the pinned v1 protocol")
    if meta["addendum_v4_sha256"] != ADDENDUM_V4_SHA256:
        raise grader.SubmissionError("sidecar addendum_v4_sha256 does not match the pinned addendum v4")
    if meta["data_sources"].get(FEATURE_TABLE_REL) != pins[FEATURE_TABLE_REL]:
        raise grader.SubmissionError(
            "sidecar data_sources lacks the pinned feature table or its sha256 differs"
        )
    fits = {int(k): [int(x) for x in v] for k, v in meta["fit_seasons_by_predicted_season"].items()}
    for s, f in fits.items():
        if SEALED_SEASON in f or s == SEALED_SEASON:
            raise grader.SubmissionError(
                f"leak: sealed confirmation season {SEALED_SEASON} in fit map {s}: {f}"
            )
        if any(x >= grader.LOCK_SEASON_MIN for x in f):
            raise grader.LockedWindowError(f"fit seasons for {s} include a locked season")
        if not set(f) <= DEV_FIT_SEASONS:
            raise grader.SubmissionError(f"fit seasons for {s} leave the development window: {f}")
    for s in seasons:
        if s not in fits:
            raise grader.SubmissionError(f"sidecar has no fit seasons for predicted season {s}")
        if s in fits[s]:
            raise grader.SubmissionError(f"leak: season {s} is among its own fit seasons {fits[s]}")
    registered = meta["grid_name"] in GRID
    return {"registered": registered, "family": GRID.get(meta["grid_name"]), "fit_seasons": fits}


def _scoring_frame(a: pd.DataFrame, models: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, dict[int, float]]:
    """grader.score's frame: the lined D1 cohort inner-joined with every model, archive-sample buckets."""
    cohort = a[a["d1"] & a["line"].notna()].copy()
    cols = ["mu_margin", "scale_margin", "dist", "p_home_ml", "mu_total", "scale_total", "dist_total"]
    cols.append("games_played_min")
    df = cohort[["event_id", "season", "game_date", "margin", "total", "line", "total_line", "sample"]].copy()
    coverage: dict[int, float] = {}
    for name, m in models.items():
        sub = m[["event_id", *cols]].rename(columns={c: f"{name}_{c}" for c in cols})
        before = df.groupby("season").size()
        df = df.merge(sub, on="event_id", how="inner")
        after = df.groupby("season").size()
        if name == "cand":
            coverage = {int(s): round(float(after.get(s, 0) / before[s]), 5) for s in before.index}
    df["games_played_min"] = df["sample"].astype(float)
    sigma = grader.fold_sigma(df)
    for name in ["closing_line", *[m for m in models if m != "closing_line"]]:
        df = grader._model_columns(df, name, sigma)
    return df, coverage


def nonzero_subset_report(df: pd.DataFrame) -> dict[str, Any]:
    """Non-verdict report (null_rule): the paired statistics on games whose adjustment is non-zero.

    Not exact equality: pd.read_json (grader.load_predictions) parses floats a few ulps off the written value,
    which marked ~10-15% of null-rule rows as adjusted in the plumbing run."""
    nz = (
        np.abs(df["cand_mu_margin"].to_numpy(float) - df["v7_served_mu_margin"].to_numpy(float)) > NONZERO_TOL
    )
    share = {
        int(s): {"n": len(g), "n_nonzero": int(g["_nz"].sum()), "share": round(float(g["_nz"].mean()), 5)}
        for s, g in df.assign(_nz=nz).groupby("season")
    }
    out: dict[str, Any] = {
        "verdict": "none (reported alongside, null_rule)",
        "n_nonzero": int(nz.sum()),
        "share_nonzero_all": round(float(nz.mean()), 5),
        "by_season": share,
    }
    if nz.sum() >= 50:
        sub = df[nz].reset_index(drop=True)
        out["paired_vs_v7"] = grader.paired_vs_v7(sub, K_V4)
        adj = sub["cand_mu_margin"] - sub["v7_served_mu_margin"]
        out["adjustment"] = {
            "mean": round(float(adj.mean()), 4),
            "sd": round(float(adj.std()), 4),
            "mean_abs": round(float(adj.abs().mean()), 4),
            "p99_abs": round(float(adj.abs().quantile(0.99)), 4),
        }
    return out


def score_v4(
    predictions: str | Path | pd.DataFrame,
    meta: dict[str, Any],
    *,
    window: str = "development",
    shuffle_seed: int | None = None,
    archive: pd.DataFrame | None = None,
    skip_repro: bool = False,
) -> dict[str, Any]:
    """Development read of a v4 spread candidate against served v7 (LOSO 2023-2026, espn_archive)."""
    add = load_addendum_v4()
    if window != "development":
        if window in ("confirmation", "confirmation_backcast", "forward_final"):
            raise grader.LockedWindowError(
                f"window {window!r} is sealed under addendum v4 (no confirmation read)"
            )
        raise ValueError(f"unknown window {window!r}")
    pins = pinned_input_shas(add)
    verify_pinned_inputs_v4(pins)
    proto = proto_v4()
    pred = grader.load_predictions(predictions)  # refuses locked rows before any score or line is read
    if (pred["season"] == SEALED_SEASON).any():
        raise grader.SubmissionError(
            f"season {SEALED_SEASON} rows are sealed (confirmation), never scored here"
        )
    a = archive if archive is not None else grader.load_archive(grader.DEV_SEASONS, shuffle_seed=shuffle_seed)
    if a.attrs.get("line_source", {}).get("name") != grader.DEV_LINE_SOURCE:
        raise grader.SubmissionError(
            f"development archive must come from load_archive({grader.DEV_LINE_SOURCE})"
        )
    grader.validate_echo(pred, a)
    seasons = sorted(set(pred["season"]) & set(grader.DEV_SEASONS)) or list(grader.DEV_SEASONS)
    info = validate_meta_v4(meta, seasons, pins)
    repro = None
    if shuffle_seed is None and not skip_repro:
        repro = reproduction_gate(a, log=False)  # must pass before any candidate statistic exists
    read_count = None
    if shuffle_seed is None:  # logged BEFORE any statistic exists
        read_count = append_read_log(
            {
                "grid_name": meta["grid_name"],
                "candidate": meta["candidate"],
                "window": "development",
                "line_source": a.attrs["line_source"]["name"],
                "predictions_sha256": frame_sha256(pred),
            }
        )
    models: dict[str, pd.DataFrame] = {**grader.baseline_predictions(a), "cand": pred}
    df, coverage = _scoring_frame(a, models)
    order = ["cand", "v7_served", "v6_asof", "closing_line"]
    blocks = {name: grader.model_block(df, name, proto) for name in order}
    paired = grader.paired_vs_v7(df, K_V4)
    flags = grader.leak_flags(blocks, "cand")
    cov_ok = bool(coverage) and min(coverage.values()) >= grader.COVERAGE_MIN
    card: dict[str, Any] = {
        "protocol_id": proto["id"],
        "protocol_sha256": grader.PROTOCOL_SHA256,
        "addendum_sha256": grader.ADDENDUM_SHA256,
        "addendum_v4_sha256": ADDENDUM_V4_SHA256,
        "pinned_inputs_v4": pins,
        "window": window,
        "seasons": list(grader.DEV_SEASONS),
        "shuffled_outcomes": shuffle_seed is not None,
        "line_source": a.attrs["line_source"],
        "read_count_for_grid_name": read_count,
        "k_v4": dict(K_V4),
        "reproduction_gate": repro,
        "cohort": {
            "definition": proto["cohort"]["games"],
            "n_scored": len(df),
            "n_by_season": {int(s): int(n) for s, n in df.groupby("season").size().items()},
            "n_d1_unlined_not_scored": int((a["d1"] & a["line"].isna()).sum()),
            "closing_line_sigma_by_fold": {int(s): round(v, 4) for s, v in grader.fold_sigma(df).items()},
        },
        "models": blocks,
        "candidate": {
            "label": meta["candidate"],
            "grid_name": meta["grid_name"],
            "code_sha256": meta["code_sha256"],
            "fit_seasons_by_predicted_season": info["fit_seasons"],
            "coverage_by_season": coverage,
            "leak_flags": flags,
        },
        "paired_vs_v7": paired,
        "nonzero_adjustment_subset": nonzero_subset_report(df),
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    v = grader.verdict(info["family"], info["registered"], paired, cov_ok, flags)
    card["verdict"] = reread_guard(v, info["registered"], read_count, meta["grid_name"])
    card["edge_dev"] = {"spread": blocks["cand"]["rule1_spread"]["edge_eligible"]}
    return card


# --------------------------------------------------------------------------------------------- reproduction
def _close(a: Any, b: Any, tol: float = 1e-9) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(_close(a[k], b[k], tol) for k in a)
    if isinstance(a, list | tuple) and isinstance(b, list | tuple):
        return len(a) == len(b) and all(_close(x, y, tol) for x, y in zip(a, b, strict=True))
    if isinstance(a, int | float) and isinstance(b, int | float) and not isinstance(a, bool):
        return math.isclose(float(a), float(b), rel_tol=0, abs_tol=tol)
    return a == b


def _jsonable(o: Any) -> Any:
    return json.loads(json.dumps(o, default=lambda v: v.item() if hasattr(v, "item") else str(v)))


def reproduction_gate(archive: pd.DataFrame | None = None, *, log: bool = True) -> dict[str, Any]:
    """grader.model_block (v1 K = 21) on the baselines must equal the baseline card's v7_served spread block:
    n, RMSE, bias, slope and CI, corr, rule-1 MSE delta and K-adjusted CI, cover log loss. A miss aborts."""
    load_addendum_v4()
    proto = grader.load_protocol()  # the unmodified v1 protocol: K spread 21
    a = archive if archive is not None else grader.load_archive(grader.DEV_SEASONS)
    if log:
        append_read_log(
            {
                "grid_name": REPRO_GRID,
                "candidate": "served v7 spread block (baseline card reproduction at v1 K 21, no new K)",
                "window": "development",
                "line_source": a.attrs["line_source"]["name"],
                "predictions_sha256": "baselines",
            }
        )
    df, _ = _scoring_frame(a, dict(grader.baseline_predictions(a)))
    got = _jsonable(grader.model_block(df, "v7_served", proto))
    want = json.loads(BASELINE_CARD.read_text(encoding="utf-8"))["models"]["v7_served"]
    checks = {key: _close(got[key], want[key]) for key in REPRO_KEYS}
    checks["k_is_v1_21"] = got["rule1_spread"]["k"] == K_V1_SPREAD
    out = {
        "passed": all(checks.values()),
        "checks": checks,
        "got": {
            "n": got["margin_all"]["n"],
            "rmse": got["margin_all"]["rmse"],
            "bias": got["margin_all"]["bias"],
            "slope": got["market_spread"]["slope"],
            "slope_ci95": got["market_spread"]["slope_ci95"],
            "corr": got["market_spread"]["corr"],
            "rule1_mse_delta": got["rule1_spread"]["mse_delta"],
            "rule1_mse_delta_ci_adj_k21": got["rule1_spread"]["mse_delta_ci_adj"],
            "cover_logloss": got["cover"]["logloss"],
            "cover_brier": got["cover"]["brier"],
        },
    }
    if not out["passed"]:
        raise RuntimeError(f"v4 spread reproduction gate failed: {out}")
    return out


# --------------------------------------------------------------------------------------------- rendering
def render_markdown(card: dict[str, Any]) -> str:
    """grader.render_markdown's tables plus the v4 header and the non-zero-adjustment subset."""
    base = grader.render_markdown({**card, "edge_dev": card["edge_dev"]})
    c, nz = card["candidate"], card["nonzero_adjustment_subset"]
    head = [
        f"# {c['grid_name']} (addendum v4, development, spread)",
        "",
        f"- candidate: {c['label']}",
        f"- addendum v4 sha256 `{card['addendum_v4_sha256']}`; K {card['k_v4']}",
        f"- read count for grid name: {card['read_count_for_grid_name']}; shuffled: {card['shuffled_outcomes']}",
        f"- reproduction gate (v1 K 21): {(card['reproduction_gate'] or {}).get('passed', 'not run (shuffled)')}",
        f"- coverage by season: {c['coverage_by_season']}; leak flags: {c['leak_flags'] or 'none'}",
        f"- **verdict: {card['verdict']['status']}** {json.dumps(card['verdict'].get('checks', ''))}",
        "",
    ]
    tail = ["", "## Adjustment non-zero subset (no verdict)", ""]
    tail.append(
        f"- n non-zero {nz['n_nonzero']} ({nz['share_nonzero_all']:.1%} of the scored cohort); by season "
        f"{json.dumps(nz['by_season'])}"
    )
    if "paired_vs_v7" in nz:
        tail.append(f"- adjustment: {json.dumps(nz['adjustment'])}")
        for key in ("spread_corr_delta_vs_v7", "margin_rmse_delta_vs_v7", "cover_logloss_delta_vs_v7"):
            v = nz["paired_vs_v7"][key]
            tail.append(
                f"- {key}: {v['delta']:+.5f} 95% {grader._ci_txt(v['ci95'], 5)} "
                f"K-adj {grader._ci_txt(v.get('ci_adj'), 5)} 2025+2026 {grader._f(v.get('late_folds_point'), 5)}"
            )
    return "\n".join(head) + base + "\n".join(tail) + "\n"


def write_json(obj: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_jsonable(obj), indent=1, sort_keys=True), encoding="utf-8")


# --------------------------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("repro")
    sc = sub.add_parser("score")
    sc.add_argument("--pred", required=True)
    sc.add_argument("--meta", required=True)
    sc.add_argument("--shuffle-seed", type=int, default=None)
    sc.add_argument("--out-dir", default=None)
    args = ap.parse_args(argv)
    if args.cmd == "repro":
        res = reproduction_gate()
        write_json(res, OUT_V4 / "reproduction_gate_spread_v4.json")
        print(json.dumps(res["got"], indent=1), "\npassed", res["passed"])
        return 0
    meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))
    card = score_v4(args.pred, meta, shuffle_seed=args.shuffle_seed)
    out = Path(args.out_dir) if args.out_dir else OUT_V4 / meta["grid_name"]
    stem = meta["grid_name"] + (f"_shuffled_{args.shuffle_seed}" if args.shuffle_seed is not None else "")
    write_json(card, out / f"{stem}.json")
    (out / f"{stem}.md").write_text(render_markdown(card), encoding="utf-8")
    print(render_markdown(card))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
