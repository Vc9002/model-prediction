"""Shared library for the NCAAF totals information-source tests (quick-261115-183, workstreams W1-W4).

One join, one bootstrap, one primary statistic, one verdict function, one tracker. The four workstream
scripts import this module and never edit it. It is committed together with the pinned rule file
``outputs/ncaaf/cfb_totals_sources_rule.json`` BEFORE any validation or holdout number exists.

What it pins (all values come from the rule file; ``require_rule`` cross-checks the constants below)
    * ``RULE_SHA256``        canonical-JSON hash of the rule file (line-ending independent); every output
                             records it and every script refuses to run if the rule file differs.
    * frozen inputs          four untracked inputs were copied once to ``data/research/ncaaf_v6/
                             totals_inputs_20261115/`` and their sha256 is pinned in the rule; this module
                             reads only the copies and refuses on a mismatch.
    * windows                select <= 2022-11-11 < validation <= 2024-11-14 < holdout <= 2026-11-13; rows
                             after that are labelled ``post_holdout`` and are never scored.
    * the 5 h result buffer  a past game counts at decision time T only if start + 5 h <= T (inclusive),
                             the same comparison as scripts/cfb_v6_lib.py ``advance`` (bisect_right on
                             cutoff - 5 h).
    * the primary statistic  dMSE = MSE(line-only OLS) - MSE(line + source OLS) on validation rows with the
                             select-window coefficients fixed; PASS = validation 95% CI lower bound > 0 AND
                             a positive one-time holdout point estimate.
    * bootstrap              date-clustered, B = 1000, a fresh ``default_rng(20261115)`` per call.

Typical use in a workstream script (see ``usage_notes`` at the bottom of this docstring)::

    import cfb_totals_lib as L
    hdr = L.startup(__file__, out_path, final=args.final)       # rule + inputs + v6 checks, header dict
    df = L.load_base()                                          # every non-forfeit v6-projected row
    # ... add the source's columns (names pinned in the rule) to df ...
    out = L.run_primary(df, "W4", "total", final=args.final)    # {"result": ..., "verdict": ...}

usage_notes
    W2  df = L.load_base(); for each record in L.iter_espn_lines(): oc = L.espn_open_close(rec); build the
        columns move_total, mv_close_total (totals) and move_spread, mv_close_margin (spreads), merge on
        event_id, then L.run_primary(df, "W2", "total") / ("W2", "spread").
    W3  merge wind_mph, precip_in, cold_f (+ wind_x_margin, close_margin from L.load_base) per the rule's
        formulas; L.run_primary(df, "W3", "total"); only if it passes, merge the *_fc forecast columns and
        L.run_primary(df, "W3", "total", stage="forecast").
    W4  merge backup_start_sum, qb_changed_sum (totals) / backup_start_diff, qb_changed_diff (spreads);
        run_primary filters to game_class == 'FBS-FBS' itself.
    W1  feed = L.load_base(); sel = L.lined(feed, "total", windows=("select",));
        L.tracker_select(feed, sel) picks the window on the select window only;
        offsets, n_used = L.tracker_offsets_for(feed, rows, ("days", 60)) gives the offset at each row's
        kickoff; L.model_stats(v6 + offsets, close, actual) and L.cboot give the report numbers.

Read-only on every input. No network. numpy and pandas only.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
RULE_PATH = REPO / "outputs" / "ncaaf" / "cfb_totals_sources_rule.json"
INPUT_DIR = REPO / "data" / "research" / "ncaaf_v6" / "totals_inputs_20261115"
FROZEN_FIT = REPO / "outputs" / "ncaaf" / "cfb_v6_frozen_fit.json"
V5_ARTIFACT = REPO / "config" / "models" / "cfb-structural-v5.json"

RULE_SHA256 = "1481273f1fbd860a5e35ecc25a864818e74818f564aa0f6d5710ea42f524145f"  # pinned after the rule file is final; require_rule() enforces it
V6_PARAMS_SHA256 = "6e38b14ca486acfd836c1259e5c4fd4fb8e9b5894a06f367322b1eb0c7b13e70"
V6_GRID_KEY = "0.1|0.2"

BOOT_B = 1000
BOOT_SEED = 20261115
RESULT_BUFFER_HOURS = 5.0
BUFFER_S = int(RESULT_BUFFER_HOURS * 3600)

SELECT_END = "2022-11-11"
VALID_START, VALID_END = "2022-11-12", "2024-11-14"
HOLD_START, HOLD_END = "2024-11-15", "2026-11-13"
WINDOWS = ("select", "validation", "holdout")

MIN_SELECT_N = 400
MIN_VALIDATION_N = 300
MIN_FLAGGED = 30

PROVISIONAL = "PROVISIONAL_PENDING_FINAL"
TAXONOMY = ("NOT_WORTH_BUILDING", "INSUFFICIENT_DATA", "UPPER_BOUND_PASS", "PIT_PASS", "DESCRIPTIVE_ONLY")
TIERS = ("leaky", "pit", "descriptive")

TRACKER_GRID: tuple[tuple[str, int], ...] = (
    ("days", 14),
    ("days", 30),
    ("days", 60),
    ("days", 90),
    ("games", 150),
    ("games", 300),
    ("games", 600),
)
TRACKER_MIN_DAY_GAMES = 50
TRACKER_FALLBACK_GAMES = 150


# --------------------------------------------------------------------------- small utilities
def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def canonical_sha256(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def script_sha256(path: str | Path) -> str:
    """sha256 of a script with CRLF normalised to LF (core.autocrlf checkouts differ from the blob)."""
    data = Path(path).read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def _jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


# --------------------------------------------------------------------------- rule and frozen inputs
_RULE_CACHE: dict[str, Any] = {}


def rule() -> dict[str, Any]:
    if "rule" not in _RULE_CACHE:
        _RULE_CACHE["rule"] = json.loads(RULE_PATH.read_text(encoding="utf-8"))
    return _RULE_CACHE["rule"]


def rule_sha256() -> str:
    """Canonical-JSON hash of the whole rule file (sorted keys, compact separators)."""
    return canonical_sha256(json.loads(RULE_PATH.read_text(encoding="utf-8")))


def require_rule() -> str:
    """Refuse to run unless the rule file hashes to RULE_SHA256 and the module constants match it."""
    got = rule_sha256()
    if got != RULE_SHA256:
        raise SystemExit(f"rule file hash {got} != pinned RULE_SHA256 {RULE_SHA256}: refusing to run")
    r = rule()
    w = r["windows"]
    problems = []
    if w["select"]["end"] != SELECT_END:
        problems.append("select end")
    if (w["validation"]["start"], w["validation"]["end"]) != (VALID_START, VALID_END):
        problems.append("validation window")
    if (w["holdout"]["start"], w["holdout"]["end"]) != (HOLD_START, HOLD_END):
        problems.append("holdout window")
    b = r["bootstrap"]
    if (b["B"], b["seed"]) != (BOOT_B, BOOT_SEED):
        problems.append("bootstrap")
    ins = r["verdicts"]["INSUFFICIENT_DATA"]
    if (ins["min_select_n"], ins["min_validation_n"], ins["min_flagged_per_window"]) != (
        MIN_SELECT_N,
        MIN_VALIDATION_N,
        MIN_FLAGGED,
    ):
        problems.append("insufficient-data thresholds")
    grid = tuple((kind, int(n)) for kind, n in r["tracker"]["windows"]["grid_in_order"])
    if grid != TRACKER_GRID:
        problems.append("tracker grid")
    if (
        r["frozen_models"]["v6_params_sha256"] != V6_PARAMS_SHA256
        or r["frozen_models"]["v6_selected_grid_key"] != V6_GRID_KEY
    ):
        problems.append("frozen v6 pin")
    if problems:
        raise SystemExit("rule file and library constants disagree: " + ", ".join(problems))
    return got


_INPUT_OK: dict[str, bool] = {}


def verify_frozen_inputs() -> dict[str, str]:
    """sha256 of each frozen copy against the rule's pin; raises SystemExit on any mismatch or absence."""
    pins = rule()["frozen_inputs"]["files"]
    seen: dict[str, str] = {}
    for name, meta in pins.items():
        path = INPUT_DIR / name
        if not path.exists():
            raise SystemExit(f"frozen input missing: {path}")
        got = file_sha256(path)
        if got != meta["sha256"]:
            raise SystemExit(f"frozen input {name} sha256 {got} != pinned {meta['sha256']}")
        seen[name] = got
    _INPUT_OK["ok"] = True
    return seen


def check_origin_espn_lines() -> bool:
    """True when the untracked origin espn_lines.jsonl still has the pinned sha (Step 4 pre-commit check)."""
    meta = rule()["frozen_inputs"]["files"]["espn_lines.jsonl"]
    return file_sha256(REPO / meta["origin"]) == meta["sha256"]


def v6_frozen_fit() -> dict[str, Any]:
    return json.loads(FROZEN_FIT.read_text(encoding="utf-8"))


def assert_v6_frozen() -> str:
    """params_sha256 stored in the frozen fit equals the pin AND equals the recomputed hash.

    Not ``cfb_v6_research.py verify-frozen`` (it can fail on an LF checkout until the sibling lands).
    """
    frozen = v6_frozen_fit()
    blob = {k: frozen[k] for k in sorted(frozen) if k not in ("params_sha256", "generated_at_utc")}
    recomputed = hashlib.sha256(canonical_json(blob).encode("utf-8")).hexdigest()
    stored = frozen["params_sha256"]
    if not (stored == recomputed == V6_PARAMS_SHA256):
        raise SystemExit(f"frozen v6 params hash mismatch: stored {stored} recomputed {recomputed}")
    return stored


def startup(
    script_file: str | Path, out_path: str | Path | None = None, *, final: bool = False
) -> dict[str, Any]:
    """Run every start-of-script check and return the header every output JSON must carry.

    Refuses on a rule/constant mismatch, a frozen-input mismatch or a frozen-v6 mismatch, and (when
    ``out_path`` is given) refuses a non-final run that would overwrite a final output.
    """
    require_rule()
    verify_frozen_inputs()
    assert_v6_frozen()
    sha = script_sha256(script_file)
    header: dict[str, Any] = {
        "rule_sha256": RULE_SHA256,
        "rule_version": rule()["rule_version"],
        "script": Path(script_file).name,
        "script_sha256": sha,
        "generated_at_utc": utc_now_iso(),
        "final": bool(final),
        "frozen_inputs_sha256": {k: v["sha256"] for k, v in rule()["frozen_inputs"]["files"].items()},
        "v6_frozen_params_sha256": V6_PARAMS_SHA256,
    }
    if out_path is not None:
        reads, note = holdout_reads(out_path, sha, final)
        header["holdout_reads"] = reads
        header["holdout_note"] = note
    return header


def holdout_reads(out_path: str | Path, script_sha: str, final: bool) -> tuple[int, str | None]:
    """D-183-HOLDOUT bookkeeping. Non-final runs keep the prior count (and may not overwrite a final output);
    a --final run is read 1, or the previous count + 1 when an earlier output already read the holdout."""
    path = Path(out_path)
    prior = 0
    prev: dict[str, Any] = {}
    if path.exists():
        try:
            prev = json.loads(path.read_text(encoding="utf-8"))
            prior = int(prev.get("holdout_reads") or 0)
        except (ValueError, OSError):
            prior = 0
    if not final:
        if prior >= 1:
            raise SystemExit(
                f"{path} already holds a final output (holdout_reads={prior}); a non-final run must not overwrite it"
            )
        return 0, None
    if prior == 0:
        return 1, None
    note = (
        f"RE-RUN: the holdout was read {prior} time(s) before; this is read {prior + 1}. "
        f"Previous script sha256 {prev.get('script_sha256')}, this one {script_sha}."
    )
    return prior + 1, note


# --------------------------------------------------------------------------- the 5 h result buffer
def buffer_eligible_end(start_s_sorted: np.ndarray, t_s: np.ndarray | int) -> np.ndarray | int:
    """Index one past the last game with start + 5 h <= T (inclusive), for kickoff-sorted epoch seconds.

    Equals ``bisect_right(starts, T - 5 h)``, the v6 ``advance`` comparison.
    """
    return np.searchsorted(start_s_sorted, np.asarray(t_s) - BUFFER_S, side="right")


def is_buffer_eligible(start_s: int, t_s: int) -> bool:
    return start_s + BUFFER_S <= t_s


def window_of(dates: pd.Series) -> np.ndarray:
    """Window label from the ISO ``date`` string (lexicographic compare is correct for ISO dates)."""
    d = dates.astype(str).to_numpy()
    return np.select(
        [d <= SELECT_END, (d >= VALID_START) & (d <= VALID_END), (d >= HOLD_START) & (d <= HOLD_END)],
        ["select", "validation", "holdout"],
        default="post_holdout",
    )


# --------------------------------------------------------------------------- data loading
def iter_espn_lines() -> Iterator[dict[str, Any]]:
    """Stream the frozen copy of espn_lines.jsonl (full provider records) after the sha check."""
    verify_frozen_inputs()
    yield from _jsonl(INPUT_DIR / "espn_lines.jsonl")


def load_base() -> pd.DataFrame:
    """Every non-forfeit v6-projected game (lined or not, FBS and FCS), kickoff-sorted, one row each.

    Columns: event_id, date, season, start_utc, t_s (epoch seconds), home, away, home_id, away_id,
    game_class, neutral, week, actual (total), margin (home), raw_total, raw_margin, mu, hterm, v6 (calibrated
    total, no floor), v6m, line (selected close total or NaN), spread_home (selected close home line or NaN),
    close_margin (= -spread_home, expected home margin), n_prov, v5 and v5m (NaN when no v5 row), window.
    Only the selected grid key of each row is kept (the grid is ~70 MB across all rows).
    """
    require_rule()
    verify_frozen_inputs()
    assert_v6_frozen()
    fit = v6_frozen_fit()
    gk = f"{fit['selected']['lambda']:g}|{fit['selected']['w_prev']:g}"
    if gk != V6_GRID_KEY:
        raise SystemExit(f"selected grid key {gk} != pinned {V6_GRID_KEY}")
    tc = fit["params"]["total_calibration"]
    mc = fit["params"]["margin_calibration"]
    burn_in = int(fit["burn_in_season"])
    v5 = json.loads(V5_ARTIFACT.read_text(encoding="utf-8"))
    v5tc, v5mc = v5["total_calibration"], v5["margin_calibration"]

    lines: dict[str, tuple[float | None, float | None, int | None]] = {}
    for rec in _jsonl(INPUT_DIR / "espn_lines.jsonl"):
        sel = rec.get("selected") or {}
        lines[str(rec["event_id"])] = (
            sel.get("total_line"),
            sel.get("home_spread_line"),
            sel.get("n_nonlive_providers"),
        )
    v5rows = {str(r["event_id"]): r for r in _jsonl(INPUT_DIR / "cfb_v5_base_rows.jsonl")}
    snap = {str(g["event_id"]): g for g in _jsonl(INPUT_DIR / "history_snapshot.jsonl")}

    rows: list[dict[str, Any]] = []
    for r in _jsonl(INPUT_DIR / "cfb_v6_base_rows.jsonl"):
        eid = str(r["event_id"])
        grid = r.get("grid")
        if not grid or int(r["season"]) <= burn_in:
            continue
        g = snap.get(eid)
        if g is None or g.get("is_forfeit"):
            continue
        raw_margin, raw_total, mu, hterm = grid[gk][:4]
        total_line, spread_line, n_prov = lines.get(eid, (None, None, None))
        x = v5rows.get(eid)
        rows.append(
            {
                "event_id": eid,
                "date": r["date"],
                "season": int(r["season"]),
                "start_utc": r["start_utc"],
                "home": r["home"],
                "away": r["away"],
                "home_id": g.get("home_id"),
                "away_id": g.get("away_id"),
                "game_class": r.get("game_class"),
                "neutral": bool(r["neutral"]),
                "week": g.get("week"),
                "actual": float(r["total"]),
                "margin": float(r["margin"]),
                "raw_total": raw_total,
                "raw_margin": raw_margin,
                "mu": mu,
                "hterm": hterm,
                "v6": tc["intercept"] + tc["slope"] * raw_total,
                "v6m": mc["intercept"] + mc["slope"] * raw_margin,
                "line": np.nan if total_line is None else float(total_line),
                "spread_home": np.nan if spread_line is None else float(spread_line),
                "n_prov": np.nan if n_prov is None else float(n_prov),
                "sample_games": r.get("sample_games"),
                "v5": np.nan if x is None else max(24.0, v5tc["intercept"] + v5tc["slope"] * x["proj_total"]),
                "v5m": np.nan if x is None else v5mc["intercept"] + v5mc["slope"] * x["proj_margin"],
            }
        )
    df = pd.DataFrame(rows)
    df["close_margin"] = -df["spread_home"]
    df["t_s"] = (
        pd.to_datetime(df["start_utc"].str.replace("Z", "", regex=False))
        .to_numpy()
        .astype("datetime64[s]")
        .astype("int64")
    )
    df["window"] = window_of(df["date"])
    return df.sort_values(["t_s", "event_id"], kind="stable").reset_index(drop=True)


def lined(
    df: pd.DataFrame, market: str, *, windows: tuple[str, ...] = ("select", "validation")
) -> pd.DataFrame:
    """Rows of the market's lined cohort in the given windows. ``total`` needs a selected total_line,
    ``spread`` a selected home_spread_line. The default excludes holdout; ask for it explicitly."""
    col = {"total": "line", "spread": "spread_home"}[market]
    return df[df[col].notna() & df["window"].isin(windows)]


def cohort_counts(df: pd.DataFrame, market: str) -> dict[str, int]:
    """Rows per window of the lined cohort (counts only; no outcome statistic)."""
    col = {"total": "line", "spread": "spread_home"}[market]
    sub = df[df[col].notna()]
    return {w: int((sub["window"] == w).sum()) for w in (*WINDOWS, "post_holdout")}


def coverage_by_season(df: pd.DataFrame, col: str) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for season, grp in df.groupby("season"):
        out[str(season)] = {"n": len(grp), "with_value": int(grp[col].notna().sum())}
    return out


def espn_open_close(rec: dict[str, Any]) -> dict[str, Any]:
    """Open and close total/home line for one espn_lines record under the rule's W2 definition.

    Open = median over eligible non-live providers of total_open (else initial_over_under) and home_ps_open
    (else initial_spread, as stored, home-signed, never conditioned on the close); provider 23 ('Opening',
    2016-2017) contributes its own current line to the open and is excluded from the close. Close = median of
    the espn-lines-rule-1 per-provider values. Caesars-family providers are dropped from both in 2017-2018.
    """
    import statistics

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import cfb_v6_espn_lines as espn

    season = int(rec.get("season_year") or 0)
    open_t: list[float] = []
    open_s: list[float] = []
    close_t: list[float] = []
    close_s: list[float] = []
    init_sign_conflicts = 0
    for p in rec.get("providers") or []:
        if p.get("is_live"):
            continue
        name = str(p.get("provider_name") or "").casefold()
        if season in (2017, 2018) and name.startswith("caesar"):
            continue
        spread, total = espn.provider_values(p)
        if str(p.get("provider_id")) == "23":
            if total is not None:
                open_t.append(total)
            if spread is not None:
                open_s.append(spread)
            continue
        if spread is not None:
            close_s.append(spread)
        if total is not None:
            close_t.append(total)
        o_total = espn.total_ok(espn.parse_num(p.get("total_open")))
        if o_total is None:
            o_total = espn.total_ok(espn.parse_num(p.get("initial_over_under")))
        o_spread = espn.spread_ok(espn.parse_num(p.get("home_ps_open")))
        if o_spread is None:
            o_spread = espn.spread_ok(espn.parse_num(p.get("initial_spread")))
            fav = p.get("fav_side")
            if (
                o_spread is not None
                and o_spread != 0
                and fav in ("home", "away")
                and (o_spread < 0) != (fav == "home")
            ):
                init_sign_conflicts += 1
        if o_total is not None:
            open_t.append(o_total)
        if o_spread is not None:
            open_s.append(o_spread)

    def med(x: list[float]) -> float | None:
        return statistics.median(x) if x else None

    return {
        "event_id": str(rec["event_id"]),
        "season": season,
        "open_total": med(open_t),
        "close_total": med(close_t),
        "open_spread": med(open_s),
        "close_spread": med(close_s),
        "n_open_total": len(open_t),
        "n_close_total": len(close_t),
        "n_open_spread": len(open_s),
        "n_close_spread": len(close_s),
        "initial_spread_sign_conflicts": init_sign_conflicts,
    }


# --------------------------------------------------------------------------- statistics helpers
def corr(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, float) - np.mean(a)
    b = np.asarray(b, float) - np.mean(b)
    den = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / den) if den > 0 else float("nan")


def ols_fit(cols: list[np.ndarray], y: np.ndarray) -> np.ndarray:
    """OLS coefficients [intercept, b1, ...] of y on the given columns (minimum-norm lstsq)."""
    x = np.column_stack([np.ones(len(y)), *[np.asarray(c, float) for c in cols]])
    return np.linalg.lstsq(x, np.asarray(y, float), rcond=None)[0]


def ols_predict(coef: np.ndarray, cols: list[np.ndarray]) -> np.ndarray:
    n = len(cols[0]) if cols else 0
    x = np.column_stack([np.ones(n), *[np.asarray(c, float) for c in cols]]) if cols else np.ones((n, 1))
    return x @ coef


def _groups(clusters: pd.Series | np.ndarray) -> list[np.ndarray]:
    codes, uni = pd.factorize(pd.Series(np.asarray(clusters)))
    order = np.argsort(codes, kind="stable")
    bounds = np.searchsorted(codes[order], np.arange(len(uni) + 1))
    return [order[bounds[i] : bounds[i + 1]] for i in range(len(uni))]


def cboot_samples(
    d: pd.DataFrame,
    fn: Callable[[pd.DataFrame, np.ndarray], Any],
    *,
    B: int = BOOT_B,
    seed: int = BOOT_SEED,
    cluster: str = "date",
) -> tuple[np.ndarray, np.ndarray]:
    """(point, samples[B, k]) of ``fn(d, row_index_array)`` under date-clustered resampling.

    A fresh ``default_rng(seed)`` per call: the CI never depends on what was bootstrapped before.
    """
    d = d.reset_index(drop=True)
    point = np.atleast_1d(np.asarray(fn(d, np.arange(len(d))), float))
    groups = _groups(d[cluster])
    n = len(groups)
    rng = np.random.default_rng(seed)
    res = np.full((B, len(point)), np.nan)
    for b in range(B):
        pick = rng.integers(0, n, n)
        idx = np.concatenate([groups[i] for i in pick])
        try:
            res[b] = np.atleast_1d(np.asarray(fn(d, idx), float))
        except (ValueError, ZeroDivisionError, FloatingPointError, np.linalg.LinAlgError):
            pass
    return point, res


def ci95(samples: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return np.nanpercentile(samples, 2.5, axis=0), np.nanpercentile(samples, 97.5, axis=0)


def cboot(
    d: pd.DataFrame,
    fn: Callable[[pd.DataFrame, np.ndarray], Any],
    *,
    B: int = BOOT_B,
    seed: int = BOOT_SEED,
    cluster: str = "date",
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(point, lo, hi) arrays, 95% percentile CI, date-clustered. ``fn(d, idx)`` returns a scalar or array."""
    point, samples = cboot_samples(d, fn, B=B, seed=seed, cluster=cluster)
    lo, hi = ci95(samples)
    return point, lo, hi


def boot_mean_samples(
    values: np.ndarray, clusters: np.ndarray | pd.Series, *, B: int = BOOT_B, seed: int = BOOT_SEED
) -> np.ndarray:
    """Date-clustered bootstrap distribution of mean(values): resample whole clusters, ratio of sums."""
    values = np.asarray(values, float)
    codes, uni = pd.factorize(pd.Series(np.asarray(clusters)))
    g = len(uni)
    sums = np.bincount(codes, weights=values, minlength=g)
    cnts = np.bincount(codes, minlength=g).astype(float)
    picks = np.random.default_rng(seed).integers(0, g, size=(B, g))
    return sums[picks].sum(axis=1) / cnts[picks].sum(axis=1)


def mean_ci(
    values: np.ndarray, clusters: np.ndarray | pd.Series, *, B: int = BOOT_B, seed: int = BOOT_SEED
) -> dict[str, float]:
    s = boot_mean_samples(values, clusters, B=B, seed=seed)
    lo, hi = ci95(s)
    return {
        "point": float(np.mean(values)),
        "lo": float(lo),
        "hi": float(hi),
        "lower_one_sided_5pct": float(np.percentile(s, 5)),
        "B": B,
        "seed": seed,
    }


def paired_diff_ci(
    err_a: np.ndarray,
    err_b: np.ndarray,
    clusters: np.ndarray | pd.Series,
    *,
    B: int = BOOT_B,
    seed: int = BOOT_SEED,
) -> dict[str, float]:
    """Mean(err_a - err_b) with a date-clustered 95% CI (e.g. |e_v6.1| - |e_line|; negative = a is better)."""
    return mean_ci(np.asarray(err_a, float) - np.asarray(err_b, float), clusters, B=B, seed=seed)


def model_stats(model: np.ndarray, close: np.ndarray, actual: np.ndarray) -> dict[str, float]:
    """MAE, RMSE, bias (= mean(model - actual)), over lean (share model > close, ties excluded) and
    corr(model - close, actual - close) for one model against one close and the realised values."""
    model, close, actual = (np.asarray(x, float) for x in (model, close, actual))
    err = model - actual
    gt, lt = model > close, model < close
    decided = gt.sum() + lt.sum()
    return {
        "n": len(actual),
        "mae": float(np.abs(err).mean()),
        "rmse": float(np.sqrt((err**2).mean())),
        "bias": float(err.mean()),
        "over_lean": float(gt.sum() / decided) if decided else float("nan"),
        "corr_model_line": corr(model - close, actual - close),
    }


# --------------------------------------------------------------------------- the primary statistic
def _market_cols(market: str) -> tuple[str, str]:
    if market == "total":
        return "line", "actual"
    if market == "spread":
        return "close_margin", "margin"
    raise ValueError(f"market must be 'total' or 'spread', got {market!r}")


def source_spec(source: str, market: str, stage: str = "observed") -> dict[str, Any]:
    """The rule's pinned feature columns, flags, close/y columns and tier for a source and market."""
    r = rule()["sources"][source]
    m = r["totals" if market == "total" else "spreads"]
    spec: dict[str, Any] = {
        "features": list(m["features"]),
        "flags": list(m["flags"]),
        "close_col": m["close_col"],
        "y_col": m["y_col"],
        "tier": r["tier"],
        "eval_features": None,
    }
    if stage == "forecast":
        fr = r["forecast_retest"]
        spec["tier"] = fr["tier"]
        spec["eval_features"] = list(fr["eval_feature_columns"]["totals" if market == "total" else "spreads"])
    elif stage != "observed":
        raise ValueError(f"stage must be 'observed' or 'forecast', got {stage!r}")
    return spec


def dmse_primary(
    df: pd.DataFrame,
    market: str,
    features: list[str],
    *,
    flags: list[str] | tuple[str, ...] = (),
    close_col: str | None = None,
    y_col: str | None = None,
    eval_features: list[str] | None = None,
    final: bool = False,
    B: int = BOOT_B,
    seed: int = BOOT_SEED,
) -> dict[str, Any]:
    """D-183-PRIMARY. dMSE = MSE(baseline) - MSE(with source) on validation rows (positive = helps).

    Baseline = OLS of y on [1, close]; with-source = OLS of y on [1, close, features]; both fit on the
    select rows of the source cohort (every needed column non-missing) and then held fixed. The CI resamples
    validation dates. ``eval_features`` (forecast retest) substitutes other columns of the same meaning on
    the evaluation rows. Holdout rows are dropped before any computation unless ``final`` is True, so a
    non-final run cannot touch them; with ``final`` the holdout point estimate is added (one read).
    """
    d_close, d_y = _market_cols(market)
    close_col = close_col or d_close
    y_col = y_col or d_y
    fit_cols = [close_col, y_col, *features]
    eval_cols = [close_col, y_col, *(eval_features or features)]
    windows = ("select", "validation", "holdout") if final else ("select", "validation")
    d = df[df["window"].isin(windows)]
    sel = d[(d["window"] == "select") & d[fit_cols].notna().all(axis=1)]
    val = d[(d["window"] == "validation") & d[eval_cols].notna().all(axis=1)]
    hold = d[(d["window"] == "holdout") & d[eval_cols].notna().all(axis=1)] if final else None

    flag_counts: dict[str, dict[str, int]] = {}
    for f in flags:
        ef = f if not eval_features else eval_features[features.index(f)]
        flag_counts[f] = {
            "select_flagged": int((sel[f] != 0).sum()),
            "validation_flagged": int((val[ef] != 0).sum()),
        }
    out: dict[str, Any] = {
        "market": market,
        "features": list(features),
        "eval_features": list(eval_features) if eval_features else None,
        "close_col": close_col,
        "y_col": y_col,
        "n_select": len(sel),
        "n_validation": len(val),
        "n_holdout": len(hold) if hold is not None else None,
        "flag_counts": flag_counts,
        "final": bool(final),
        "validation": None,
        "holdout": None,
        "baseline_coef": None,
        "source_coef": None,
        "select_feature_means": None,
        "exploratory": {},
    }
    if len(sel) < len(features) + 3 or len(val) < 2:
        return out

    ys = sel[y_col].to_numpy(float)
    base = ols_fit([sel[close_col].to_numpy(float)], ys)
    src = ols_fit([sel[close_col].to_numpy(float), *[sel[f].to_numpy(float) for f in features]], ys)
    out["baseline_coef"] = [float(x) for x in base]
    out["source_coef"] = [float(x) for x in src]
    out["select_feature_means"] = {f: float(sel[f].mean()) for f in features}

    def errs(part: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        y = part[y_col].to_numpy(float)
        c = part[close_col].to_numpy(float)
        pb = ols_predict(base, [c])
        ps = ols_predict(src, [c, *[part[f].to_numpy(float) for f in (eval_features or features)]])
        return y - pb, y - ps, y

    eb, es, _ = errs(val)
    gain = eb**2 - es**2
    samples = boot_mean_samples(gain, val["date"].to_numpy(), B=B, seed=seed)
    lo, hi = ci95(samples)
    out["validation"] = {
        "mse_baseline": float((eb**2).mean()),
        "mse_with_source": float((es**2).mean()),
        "dmse": float(gain.mean()),
        "ci_lo": float(lo),
        "ci_hi": float(hi),
        "B": B,
        "seed": seed,
    }
    out["exploratory"] = {
        "label": "exploratory, not a verdict input",
        "select_in_sample_dmse": float(
            (
                (ys - ols_predict(base, [sel[close_col].to_numpy(float)])) ** 2
                - (
                    ys
                    - ols_predict(
                        src, [sel[close_col].to_numpy(float), *[sel[f].to_numpy(float) for f in features]]
                    )
                )
                ** 2
            ).mean()
        ),
        "validation_dmae": float((np.abs(eb) - np.abs(es)).mean()),
    }
    if hold is not None and len(hold) >= 2:
        hb, hs, _ = errs(hold)
        out["holdout"] = {
            "mse_baseline": float((hb**2).mean()),
            "mse_with_source": float((hs**2).mean()),
            "dmse": float((hb**2 - hs**2).mean()),
            "point_estimate_only": True,
        }
    return out


def verdict(result: dict[str, Any], tier: str, *, final: bool) -> dict[str, Any]:
    """D-183-VERDICTS. ``tier`` is 'leaky', 'pit' or 'descriptive'.

    Without ``final`` the holdout is unread, so anything that depends on it returns PROVISIONAL (a value
    outside the taxonomy); INSUFFICIENT_DATA and DESCRIPTIVE_ONLY never depend on the holdout.
    """
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {TIERS}, got {tier!r}")
    if tier == "descriptive":
        return {"verdict": "DESCRIPTIVE_ONLY", "reason": "no select/validation data"}
    reasons = []
    if result["n_select"] < MIN_SELECT_N:
        reasons.append(f"select n {result['n_select']} < {MIN_SELECT_N}")
    if result["n_validation"] < MIN_VALIDATION_N:
        reasons.append(f"validation n {result['n_validation']} < {MIN_VALIDATION_N}")
    for name, c in (result.get("flag_counts") or {}).items():
        if c["select_flagged"] < MIN_FLAGGED:
            reasons.append(f"{name}: select flagged {c['select_flagged']} < {MIN_FLAGGED}")
        if c["validation_flagged"] < MIN_FLAGGED:
            reasons.append(f"{name}: validation flagged {c['validation_flagged']} < {MIN_FLAGGED}")
    if reasons or result.get("validation") is None:
        return {"verdict": "INSUFFICIENT_DATA", "reason": "; ".join(reasons) or "no validation statistic"}
    val_pass = result["validation"]["ci_lo"] > 0
    if not final:
        return {
            "verdict": PROVISIONAL,
            "validation_pass": bool(val_pass),
            "reason": "holdout not read (no --final)",
        }
    hold = result.get("holdout")
    if hold is None:
        return {"verdict": "INSUFFICIENT_DATA", "reason": "no holdout rows carry the feature"}
    hold_pass = hold["dmse"] > 0
    if val_pass and hold_pass:
        return {
            "verdict": "PIT_PASS" if tier == "pit" else "UPPER_BOUND_PASS",
            "validation_pass": True,
            "holdout_pass": True,
            "reason": "validation CI lower bound > 0 and holdout point estimate > 0",
        }
    return {
        "verdict": "NOT_WORTH_BUILDING",
        "validation_pass": bool(val_pass),
        "holdout_pass": bool(hold_pass),
        "reason": "fails the pass rule (validation CI lower bound > 0 AND holdout point estimate > 0)",
    }


def run_primary(
    df: pd.DataFrame, source: str, market: str, *, stage: str = "observed", final: bool = False
) -> dict[str, Any]:
    """Pinned features -> dmse_primary -> verdict for a source ('W2', 'W3', 'W4') and market.

    W4 is restricted to FBS-vs-FBS games and W3 to non-neutral games here, as the rule pins; dome and
    missing-coordinate rows must already be NaN in the feature columns.
    """
    spec = source_spec(source, market, stage)
    d = df
    if source == "W4":
        d = d[d["game_class"] == "FBS-FBS"]
    elif source == "W3":
        d = d[~d["neutral"].astype(bool)]
    res = dmse_primary(
        d,
        market,
        spec["features"],
        flags=spec["flags"],
        close_col=spec["close_col"],
        y_col=spec["y_col"],
        eval_features=spec["eval_features"],
        final=final,
    )
    return {
        "source": source,
        "market": market,
        "stage": stage,
        "tier": spec["tier"],
        "result": res,
        "verdict": verdict(res, spec["tier"], final=final),
    }


# --------------------------------------------------------------------------- the level tracker
def tracker_offsets(
    feed_t_s: np.ndarray,
    feed_resid: np.ndarray,
    decision_t_s: np.ndarray,
    window: tuple[str, int],
) -> tuple[np.ndarray, np.ndarray]:
    """D-183-TRACKER offset at each decision time, and the number of games it averaged.

    ``feed_t_s`` must be kickoff-sorted epoch seconds, ``feed_resid`` the residual (actual - v6 total, or
    actual - close for the diagnostic). A feed game counts for decision time T only if start + 5 h <= T
    (inclusive). ('days', N): eligible games with start >= T - N days, falling back to the last 150 eligible
    games when fewer than 50 qualify. ('games', N): the last N eligible games (all of them if fewer).
    No eligible game gives offset 0.0 with n_used 0.
    """
    feed_t_s = np.asarray(feed_t_s, np.int64)
    if len(feed_t_s) > 1 and np.any(np.diff(feed_t_s) < 0):
        raise ValueError("feed must be sorted by kickoff")
    cs = np.concatenate([[0.0], np.cumsum(np.asarray(feed_resid, float))])
    t = np.asarray(decision_t_s, np.int64)
    hi = np.asarray(buffer_eligible_end(feed_t_s, t), np.int64)
    kind, size = window
    if kind == "games":
        lo = np.maximum(0, hi - size)
    elif kind == "days":
        lo = np.searchsorted(feed_t_s, t - size * 86400, side="left").astype(np.int64)
        lo = np.minimum(lo, hi)
        short = (hi - lo) < TRACKER_MIN_DAY_GAMES
        lo = np.where(short, np.maximum(0, hi - TRACKER_FALLBACK_GAMES), lo)
    else:
        raise ValueError(f"unknown tracker window kind {kind!r}")
    n = hi - lo
    offset = np.where(n > 0, (cs[hi] - cs[lo]) / np.maximum(n, 1), 0.0)
    return offset, n


def tracker_offsets_for(
    feed: pd.DataFrame, rows: pd.DataFrame, window: tuple[str, int], *, resid_col: str | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """Offsets at each ``rows`` kickoff using ``feed`` (load_base output) as history.

    ``resid_col`` None means actual - v6. Any other column of ``feed`` can be the residual (e.g. a
    precomputed actual - line column on a lined-only feed).
    """
    f = feed.sort_values(["t_s", "event_id"], kind="stable")
    resid = (f["actual"] - f["v6"]).to_numpy(float) if resid_col is None else f[resid_col].to_numpy(float)
    return tracker_offsets(f["t_s"].to_numpy(), resid, rows["t_s"].to_numpy(), window)


def tracker_select(feed: pd.DataFrame, select_rows: pd.DataFrame) -> dict[str, Any]:
    """Pick the tracker window by lowest RMSE of (v6 + offset) vs actual on the select-window lined games.

    ``select_rows`` must be select-window total-lined rows (``lined(feed, 'total', windows=('select',))``);
    anything else raises. Ties go to the earlier grid entry. Uses no validation or holdout row.
    """
    if not (select_rows["window"] == "select").all() or select_rows["line"].isna().any():
        raise ValueError("tracker_select needs select-window rows with a total line only")
    table = []
    actual = select_rows["actual"].to_numpy(float)
    v6 = select_rows["v6"].to_numpy(float)
    for window in TRACKER_GRID:
        off, n = tracker_offsets_for(feed, select_rows, window)
        rmse = float(np.sqrt(((v6 + off - actual) ** 2).mean()))
        table.append({"window": list(window), "rmse": rmse, "mean_n_used": float(n.mean())})
    best = min(range(len(table)), key=lambda i: (table[i]["rmse"], i))
    return {
        "grid": table,
        "selected": table[best]["window"],
        "selected_index": best,
        "n_select_rows": len(select_rows),
        "v6_rmse_no_tracker": float(np.sqrt(((v6 - actual) ** 2).mean())),
    }


# --------------------------------------------------------------------------- real-data self check
def self_check_select() -> dict[str, Any]:
    """Reproduce the diagnosis' select-window numbers (line / v6 MAE, n) before any new number.

    Touches select rows only. Expected from out_q67.txt: n 4,590, MAE line 12.92, v6 13.41.
    """
    df = load_base()
    sel = lined(df, "total", windows=("select",))
    n = len(sel)
    mae_line = float(np.abs(sel["actual"] - sel["line"]).mean())
    mae_v6 = float(np.abs(sel["actual"] - sel["v6"]).mean())
    return {
        "n_select_total_lined": n,
        "mae_line": round(mae_line, 2),
        "mae_v6": round(mae_v6, 2),
        "expected": {"n": 4590, "mae_line": 12.92, "mae_v6": 13.41},
        "ok": (round(mae_line, 2), round(mae_v6, 2)) == (12.92, 13.41),
        "n_matches_expected": n == 4590,
    }


# --------------------------------------------------------------------------- synthetic self-test
def _synthetic(n: int = 3000, seed: int = 1, effect: float = 0.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2018-09-01", periods=n // 6 + 1, freq="D")
    date = np.repeat(dates.strftime("%Y-%m-%d").to_numpy(), 6)[:n]
    line = 54 + rng.normal(0, 8, n)
    x = rng.normal(0, 1, n)
    actual = line + effect * x + rng.normal(0, 16, n)
    df = pd.DataFrame({"date": date, "line": line, "actual": actual, "x": x, "neutral": False})
    df["season"] = df["date"].str[:4].astype(int)
    df["window"] = window_of(df["date"])
    return df


def selftest() -> None:
    """Synthetic-data checks of the helpers; never reads real data. Raises AssertionError on failure."""
    # 1. 5 h buffer boundary is inclusive and exact to the second
    starts = np.array([1_000_000], dtype=np.int64)
    resid = np.array([2.0])
    off, n = tracker_offsets(
        starts, resid, np.array([1_000_000 + BUFFER_S - 1, 1_000_000 + BUFFER_S]), ("games", 150)
    )
    assert list(n) == [0, 1] and list(off) == [0.0, 2.0], (off, n)
    assert is_buffer_eligible(10, 10 + BUFFER_S) and not is_buffer_eligible(10, 10 + BUFFER_S - 1)
    # 2. day window with < 50 games falls back to the last 150
    st = np.arange(200, dtype=np.int64) * 3600
    r = np.where(np.arange(200) < 100, 10.0, -10.0)
    t = np.array([199 * 3600 + 3600 * 6])
    _, n_d = tracker_offsets(st, r, t, ("days", 14))  # 14 d covers all 200 eligible -> no fallback
    assert n_d[0] == 200
    _, n_f = tracker_offsets(st, r, np.array([199 * 3600 + 6 * 3600]), ("days", 1))  # 1 d = 24 games < 50
    assert n_f[0] == TRACKER_FALLBACK_GAMES, n_f
    _, n_g = tracker_offsets(st, r, np.array([6 * 3600]), ("games", 600))  # fewer than N eligible: all
    assert n_g[0] == 2, n_g
    try:
        tracker_offsets(st[::-1], r, t, ("games", 5))
        raise AssertionError("unsorted feed accepted")
    except ValueError:
        pass
    # 3. bootstrap: deterministic, order independent, and a real effect passes while noise does not
    dnull, dreal = _synthetic(effect=0.0, seed=2), _synthetic(effect=2.5, seed=3)
    a = boot_mean_samples(dnull["x"].to_numpy(), dnull["date"].to_numpy())
    _ = boot_mean_samples(dreal["x"].to_numpy(), dreal["date"].to_numpy())
    b = boot_mean_samples(dnull["x"].to_numpy(), dnull["date"].to_numpy())
    assert np.array_equal(a, b)
    # 4. windows and holdout isolation
    far = _synthetic(n=600, seed=4)
    far["date"] = np.where(
        np.arange(600) % 3 == 0, "2023-10-01", np.where(np.arange(600) % 3 == 1, "2021-10-01", "2025-10-01")
    )
    far["window"] = window_of(far["date"])
    assert set(far["window"]) == {"select", "validation", "holdout"}
    assert window_of(
        pd.Series(["2022-11-11", "2022-11-12", "2024-11-14", "2024-11-15", "2026-11-13", "2026-11-14"])
    ).tolist() == ["select", "validation", "validation", "holdout", "holdout", "post_holdout"]
    dd = _synthetic(n=6000, seed=5, effect=3.0)
    dd["date"] = pd.date_range("2018-09-01", periods=len(dd), freq="12h").strftime("%Y-%m-%d").to_numpy()
    dd["window"] = window_of(dd["date"])
    nonfinal = dd.copy()
    nonfinal.loc[nonfinal["window"] == "holdout", ["actual", "line", "x"]] = np.nan  # holdout unreadable
    res = dmse_primary(nonfinal, "total", ["x"], final=False)
    assert res["holdout"] is None and res["n_holdout"] is None
    if res["n_validation"] >= 100:
        assert res["validation"]["ci_lo"] > 0, res["validation"]
    # 5. verdict matrix
    base = {
        "n_select": 500,
        "n_validation": 400,
        "flag_counts": {},
        "validation": {"ci_lo": 0.5},
        "holdout": {"dmse": 0.1},
    }
    assert verdict(base, "leaky", final=True)["verdict"] == "UPPER_BOUND_PASS"
    assert verdict(base, "pit", final=True)["verdict"] == "PIT_PASS"
    assert verdict(base, "pit", final=False)["verdict"] == PROVISIONAL
    assert verdict({**base, "holdout": {"dmse": -0.1}}, "pit", final=True)["verdict"] == "NOT_WORTH_BUILDING"
    assert (
        verdict({**base, "validation": {"ci_lo": -0.1}}, "pit", final=True)["verdict"] == "NOT_WORTH_BUILDING"
    )
    assert verdict({**base, "n_select": 399}, "pit", final=True)["verdict"] == "INSUFFICIENT_DATA"
    assert verdict({**base, "n_validation": 299}, "pit", final=False)["verdict"] == "INSUFFICIENT_DATA"
    low = {**base, "flag_counts": {"f": {"select_flagged": 29, "validation_flagged": 100}}}
    assert verdict(low, "leaky", final=True)["verdict"] == "INSUFFICIENT_DATA"
    assert verdict(base, "descriptive", final=False)["verdict"] == "DESCRIPTIVE_ONLY"
    assert all(verdict(base, t, final=True)["verdict"] in TAXONOMY for t in TIERS)
    # 6. hashing is key-order independent and CRLF independent
    assert canonical_sha256({"a": 1, "b": [1, 2]}) == canonical_sha256({"b": [1, 2], "a": 1})
    assert hashlib.sha256(canonical_json({"a": 1}).encode()).hexdigest() == canonical_sha256({"a": 1})
    # 7. model_stats basics
    m = model_stats(np.array([1.0, 2.0, 3.0]), np.array([1.0, 1.0, 4.0]), np.array([1.0, 3.0, 3.0]))
    assert m["over_lean"] == 0.5 and abs(m["mae"] - 1 / 3) < 1e-12 and abs(m["bias"] - (-1 / 3)) < 1e-12
    print("selftest ok")


def _main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "selftest"
    if cmd == "selftest":
        selftest()
        return 0
    if cmd == "rule-hash":
        print(rule_sha256())
        return 0
    if cmd == "check":
        require_rule()
        print("rule ok", RULE_SHA256)
        print("inputs", json.dumps(verify_frozen_inputs(), indent=1))
        print("v6 frozen", assert_v6_frozen())
        print("espn_lines origin unchanged:", check_origin_espn_lines())
        return 0
    if cmd == "self-check-select":
        require_rule()
        print(json.dumps(self_check_select(), indent=1))
        return 0
    print("usage: cfb_totals_lib.py selftest | rule-hash | check | self-check-select", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
