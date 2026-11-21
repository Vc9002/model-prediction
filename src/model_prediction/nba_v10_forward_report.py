"""Read-only scoring of the NBA v10 forward logs (``outputs/nba/v10_forward/PREREG_FORWARD.md``).

Test A (line movement) and test B (T-C2) exactly as pre-registered; nothing here is tuned on
forward data and nothing writes outside the report directory. The capture side lives in
``nba_v10_forward``.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np

from .domain import EASTERN, parse_utc
from .nba_v10_forward import CONSENSUS_MAX_AGE, TC2_MIN_DELTA, _read_jsonl, anchor_mean, mids_path

A_MIN_GAMES = 400
B_MIN_EVENTS = 100
B3_MIN_GAP_EVENTS = 30
GAP_PP = 3.0
BOOTSTRAP_DRAWS = 10_000
BOOTSTRAP_SEED = 20261121
PRIMARY_VARIANT = "avail"
MARKETS = ("spread", "total")
MODEL_KEY = {"spread": "margin", "total": "total"}
V9_MODELS = {"spread": "nba-spread-margin-v9", "total": "nba-total-margin-v9"}
T10_LEAD = timedelta(minutes=10)
T10_MAX_LEAD = timedelta(minutes=40)
FEE_COEFFICIENT = 0.06


# --------------------------------------------------------------------------- statistics
@dataclass(frozen=True)
class Fit:
    n: int
    corr: float | None
    slope: float | None
    slope_low: float | None
    slope_high: float | None
    spearman: float | None


def _pearson(x: np.ndarray, y: np.ndarray) -> float | None:
    if len(x) < 3 or np.std(x) == 0 or np.std(y) == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def _slope(x: np.ndarray, y: np.ndarray) -> float | None:
    vx = float(np.var(x))
    return None if vx == 0 else float(np.mean((x - x.mean()) * (y - y.mean())) / vx)


def fit_xy(
    x: Sequence[float], y: Sequence[float], *, draws: int = BOOTSTRAP_DRAWS, seed: int = BOOTSTRAP_SEED
) -> Fit:
    """Pearson corr, OLS slope of y on x and its 95% percentile CI (one observation per game)."""
    xa, ya = np.asarray(x, float), np.asarray(y, float)
    n = len(xa)
    corr, slope = _pearson(xa, ya), _slope(xa, ya)
    low = high = spearman = None
    if n >= 3:
        rx = np.argsort(np.argsort(xa)).astype(float)
        ry = np.argsort(np.argsort(ya)).astype(float)
        spearman = _pearson(rx, ry)
        rng = np.random.default_rng(seed)
        idx = rng.integers(0, n, size=(draws, n))
        bx, by = xa[idx], ya[idx]
        mx, my = bx.mean(axis=1, keepdims=True), by.mean(axis=1, keepdims=True)
        vx = ((bx - mx) ** 2).mean(axis=1)
        cov = ((bx - mx) * (by - my)).mean(axis=1)
        ok = vx > 0
        slopes = cov[ok] / vx[ok]
        if len(slopes):
            low, high = float(np.percentile(slopes, 2.5)), float(np.percentile(slopes, 97.5))
    return Fit(n, corr, slope, low, high, spearman)


def cluster_mean(
    values: Sequence[float],
    clusters: Sequence[str],
    *,
    draws: int = BOOTSTRAP_DRAWS,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Observation-weighted mean with a 95% CI from resampling whole games."""
    v = np.asarray(values, float)
    if len(v) == 0:
        return {"n": 0, "n_games": 0, "mean": None, "low": None, "high": None}
    keys = sorted(set(clusters))
    code = {k: i for i, k in enumerate(keys)}
    c = np.array([code[k] for k in clusters])
    g = len(keys)
    sums = np.bincount(c, weights=v, minlength=g)
    counts = np.bincount(c, minlength=g).astype(float)
    out = {"n": len(v), "n_games": g, "mean": float(v.mean()), "low": None, "high": None}
    if g >= 2:
        rng = np.random.default_rng(seed)
        picks = rng.integers(0, g, size=(draws, g))
        s, n = sums[picks].sum(axis=1), counts[picks].sum(axis=1)
        means = s[n > 0] / n[n > 0]
        out["low"], out["high"] = float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))
    return out


# --------------------------------------------------------------------------- test A
def load_captures(files: Iterable[Path]) -> dict[tuple[str, str], list[Any]]:
    from .nba_v10_g2 import load_consensus_captures

    captures, _ = load_consensus_captures(files)
    return captures


def consensus_mean_at(captures: Sequence[Any], t: datetime, sigma: float) -> float | None:
    """PREREG 2: the latest capture with observed <= t and at most 15 minutes old (max-scan)."""
    pool = [c for c in captures if t - CONSENSUS_MAX_AGE <= c.observed_at_utc <= t]
    if not pool:
        return None
    best = max(pool, key=lambda c: c.observed_at_utc)
    return anchor_mean(best.line, best.p_low, sigma)


def t10_mean(
    captures: Sequence[Any], t: datetime, tip: datetime, as_of: datetime, sigma: float
) -> float | None:
    from .nba_v10_g2 import select_t10_benchmark

    best, _reason = select_t10_benchmark(captures, decision_time=t, tip=tip, as_of=as_of)
    return None if best is None else anchor_mean(best.line, best.p_low, sigma)


def line_movement_observations(
    decisions: Iterable[Mapping[str, Any]],
    captures: Mapping[tuple[str, str], Sequence[Any]],
    sigmas: Mapping[str, float],
    as_of: datetime,
) -> dict[str, list[dict[str, Any]]]:
    """One (x, y) observation per game per market: the EARLIEST decision with model, consensus at t
    and a T10 benchmark (PREREG 2). ``decisions`` carry event_id, tip, t and per-market model means.
    Returned per market in tip order."""
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for d in sorted(decisions, key=lambda d: (d["t"], d["event_id"])):
        for market in MARKETS:
            key = (d["event_id"], market)
            if key in best:
                continue
            model = d["model"].get(market)
            if model is None:
                continue
            caps = captures.get(key, ())
            at_t = consensus_mean_at(caps, d["t"], sigmas[market])
            if at_t is None:
                continue
            at_t10 = t10_mean(caps, d["t"], d["tip"], as_of, sigmas[market])
            if at_t10 is None:
                continue
            best[key] = {
                "event_id": d["event_id"],
                "tip": d["tip"],
                "t": d["t"],
                "x": model - at_t,
                "y": at_t10 - at_t,
                "model": model,
                "consensus_t": at_t,
                "consensus_t10": at_t10,
            }
    out: dict[str, list[dict[str, Any]]] = {m: [] for m in MARKETS}
    for (_, market), obs in best.items():
        out[market].append(obs)
    for market in MARKETS:
        out[market].sort(key=lambda o: (o["tip"], o["event_id"]))
    return out


def player_decisions(
    records: Iterable[Mapping[str, Any]], variant: str = PRIMARY_VARIANT
) -> list[dict[str, Any]]:
    out = []
    for r in records:
        if r.get("reason"):
            continue
        v = (r.get("variants") or {}).get(variant) or {}
        if v.get("reason"):
            continue
        try:
            t, tip = parse_utc(str(r["observed_at_utc"])), parse_utc(str(r["event_start_utc"]))
        except (KeyError, ValueError):
            continue
        out.append(
            {
                "event_id": str(r["event_id"]),
                "t": t,
                "tip": tip,
                "model": {"spread": v.get("margin"), "total": v.get("total")},
            }
        )
    return out


def v9_decisions(ledgers_db: Path, since_utc: str) -> list[dict[str, Any]]:
    """v9 Flat rows' stored served means (home margin for spreads), read-only."""
    if not Path(ledgers_db).is_file():
        return []
    conn = sqlite3.connect(f"{Path(ledgers_db).resolve().as_uri()}?mode=ro", uri=True, timeout=10.0)
    try:
        rows = conn.execute(
            "SELECT model_id, event_id, event_start_utc, decision_payload_json FROM ledger_records "
            "WHERE lower(sport) = 'nba' AND ledger_tier = 'flat' AND model_id IN (?, ?) AND created_at_utc >= ?",
            (V9_MODELS["spread"], V9_MODELS["total"], parse_utc(since_utc).strftime("%Y-%m-%dT%H:%M:%S")),
        ).fetchall()
    finally:
        conn.close()
    out = []
    for model_id, event_id, start, payload in rows:
        try:
            snap = json.loads(json.loads(payload)["model_input_snapshot_json"])
            mean = float(snap["inference_inputs"]["mean"])
            t = parse_utc(str(snap["observed_at_utc"]))
            tip = parse_utc(str(start))
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(mean):
            continue
        market = "spread" if model_id == V9_MODELS["spread"] else "total"
        out.append({"event_id": str(event_id), "t": t, "tip": tip, "model": {market: mean}})
    return out


def verdict_a(obs: Sequence[Mapping[str, Any]], as_of: datetime) -> dict[str, Any]:
    """First 400 games in tip order, evaluated once the 400th has tipped (PREREG 2).

    Below 400 only the count is reported: the statistic is computed once, never watched."""
    tipped = [o for o in obs if o["tip"] <= as_of]
    if len(tipped) < A_MIN_GAMES:
        return {"verdict": "PENDING", "n": len(tipped), "needed": A_MIN_GAMES, "fit": None}
    sample = tipped[:A_MIN_GAMES]
    fit = fit_xy([o["x"] for o in sample], [o["y"] for o in sample])
    passed = (
        fit.corr is not None
        and fit.corr > 0
        and fit.slope is not None
        and fit.slope > 0
        and fit.slope_low is not None
        and fit.slope_low > 0
    )
    return {
        "verdict": "PASS" if passed else "FAIL",
        "n": len(sample),
        "needed": A_MIN_GAMES,
        "fit": fit,
        "after_evaluation": len(tipped) - A_MIN_GAMES,
    }


# --------------------------------------------------------------------------- test B
def _poll_quote(
    polls: Sequence[Mapping[str, Any]],
    event_id: str,
    slug: str,
    *,
    after: datetime | None,
    upto: datetime,
    not_before: datetime | None = None,
) -> tuple[datetime, dict[str, Any]] | None:
    best = None
    for rec in polls:
        if str(rec.get("espn_event_id")) != event_id:
            continue
        obs = parse_utc(str(rec["observed_at_utc"]))
        if (
            obs > upto
            or (after is not None and obs <= after)
            or (not_before is not None and obs < not_before)
        ):
            continue
        q = next((m for m in rec.get("markets") or [] if m.get("slug") == slug), None)
        if q is not None and (best is None or obs > best[0]):
            best = (obs, q)
    return best


def tc2_observations(
    events: Iterable[Mapping[str, Any]],
    polls: Sequence[Mapping[str, Any]],
    sigmas: Mapping[str, float],
    as_of: datetime,
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {m: [] for m in MARKETS}
    for ev in events:
        if ev.get("kind") != "event":
            continue
        tau0 = parse_utc(str(ev["published_at_utc"]))
        fetched = parse_utc(str(ev["fetched_at_utc"]))
        tip = parse_utc(str(ev["event_start_utc"]))
        for market in MARKETS:
            d = (ev.get("delta") or {}).get(MODEL_KEY[market])
            rung = (ev.get("fixed_rungs_before") or {}).get(market)
            row: dict[str, Any] = {
                "event_key": ev["event_key"],
                "game": ev["espn_event_id"],
                "published": tau0,
                "latency_min": (fetched - tau0).total_seconds() / 60,
                "d": d,
                "excluded": None,
            }
            if ev.get("reason"):
                row["excluded"] = str(ev["reason"])
            elif d is None or abs(d) < TC2_MIN_DELTA:
                row["excluded"] = "no_direction"
            elif rung is None:
                row["excluded"] = "no_before_quote"
            if row["excluded"]:
                out[market].append(row)
                continue
            sigma, c, p_b = sigmas[market], float(rung["contract_line"]), float(rung["p_high"])
            sign = 1.0 if d > 0 else -1.0
            m_b = anchor_mean(c, 1.0 - p_b, sigma)
            q30 = _poll_quote(
                polls, ev["espn_event_id"], rung["slug"], after=tau0, upto=tau0 + timedelta(minutes=30)
            )
            if q30 is None:
                row["excluded"] = "no_plus30_quote"
                out[market].append(row)
                continue
            m30 = anchor_mean(c, 1.0 - float(q30[1]["p_high"]), sigma)
            nd = NormalDist()
            dp_model = nd.cdf((m_b + d - c) / sigma) - nd.cdf((m_b - c) / sigma)
            gaps = []
            for rec in polls:
                if str(rec.get("espn_event_id")) != ev["espn_event_id"]:
                    continue
                obs = parse_utc(str(rec["observed_at_utc"]))
                if not (fetched <= obs <= tau0 + timedelta(minutes=30)):
                    continue
                q = next((m for m in rec.get("markets") or [] if m.get("slug") == rung["slug"]), None)
                if q is not None:
                    gaps.append(
                        (obs, sign * (dp_model - (float(q["p_high"]) - p_b)) * 100.0, float(q["p_high"]))
                    )
            gaps.sort()
            first_gap = next((g for g in gaps if g[1] >= GAP_PP), None)
            t10 = _poll_quote(
                polls,
                ev["espn_event_id"],
                rung["slug"],
                after=None,
                upto=tip - T10_LEAD,
                not_before=tip - T10_MAX_LEAD,
            )
            row.update(
                s30=sign * (m30 - m_b),
                moved_with=None if m30 == m_b else (m30 - m_b) * sign > 0,
                max_gap_pp=max((g[1] for g in gaps), default=None),
                has_gap=first_gap is not None,
                b3=None
                if first_gap is None or t10 is None or tip > as_of
                else sign * (float(t10[1]["p_high"]) - first_gap[2]),
                p_at_gap=None if first_gap is None else first_gap[2],
            )
            out[market].append(row)
    for market in MARKETS:
        out[market].sort(key=lambda r: (r["published"], r["event_key"]))
    return out


def verdict_b(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    usable = [r for r in rows if not r["excluded"]]
    excluded: dict[str, int] = {}
    for r in rows:
        if r["excluded"]:
            excluded[r["excluded"]] = excluded.get(r["excluded"], 0) + 1
    sample = usable[:B_MIN_EVENTS]
    games = [r["game"] for r in sample]
    b1 = cluster_mean([r["s30"] for r in sample], games)
    b2 = cluster_mean([1.0 if r["has_gap"] else 0.0 for r in sample], games)
    gap_rows = [r for r in sample if r["has_gap"] and r["b3"] is not None]
    b3 = cluster_mean([r["b3"] for r in gap_rows], [r["game"] for r in gap_rows])
    moved = [r["moved_with"] for r in sample if r["moved_with"] is not None]
    out: dict[str, Any] = {
        "n_usable": len(usable),
        "needed": B_MIN_EVENTS,
        "excluded": excluded,
        "b1": b1,
        "b1_share_with_model": None if not moved else sum(moved) / len(moved),
        "b2_gap_share": b2,
        "b3": b3,
        "latency_minutes_median": float(np.median([r["latency_min"] for r in rows])) if rows else None,
    }
    if len(usable) < B_MIN_EVENTS:
        # Counts and exclusions only: the statistics are computed once, at the pre-registered n.
        for key in ("b1", "b2_gap_share", "b3"):
            out[key] = {
                "n": out[key]["n"],
                "n_games": out[key]["n_games"],
                "mean": None,
                "low": None,
                "high": None,
            }
        out["b1_share_with_model"] = None
        out["verdict_b1"] = out["verdict_b3"] = "PENDING"
        return out
    out["verdict_b1"] = "PASS" if b1["low"] is not None and b1["mean"] > 0 and b1["low"] > 0 else "FAIL"
    if out["verdict_b1"] != "PASS":
        out["verdict_b3"] = "NOT_EVALUATED (B1 did not pass)"
    elif len(gap_rows) < B3_MIN_GAP_EVENTS:
        out["verdict_b3"] = "INSUFFICIENT"
    else:
        out["verdict_b3"] = "PASS" if b3["low"] is not None and b3["mean"] > 0 and b3["low"] > 0 else "FAIL"
    return out


# --------------------------------------------------------------------------- driver
def build_report(
    out_root: Path, runtime_root: Path, repo_root: Path, ledgers_db: Path, as_of: datetime
) -> dict[str, Any]:
    from .nba_v10_forward import anchor_sigmas

    sigmas = anchor_sigmas(repo_root)
    records = _read_jsonl(out_root / "player_log.jsonl")
    first = min((parse_utc(str(r["observed_at_utc"])) for r in records), default=None)
    dates: set[str] = set()
    for r in records:
        dates.add(parse_utc(str(r["event_start_utc"])).astimezone(EASTERN).date().isoformat())
    v9 = v9_decisions(ledgers_db, first.isoformat()) if first is not None else []
    for d in v9:
        dates.add(d["tip"].astimezone(EASTERN).date().isoformat())
    from .data_sources.nba_pretip_capture import espn_odds_path

    captures = load_captures(espn_odds_path(runtime_root, day) for day in sorted(dates))
    a = {}
    for label, decisions in (
        ("player_avail", player_decisions(records, "avail")),
        ("player_exavail", player_decisions(records, "exavail")),
        ("v9", v9),
    ):
        obs = line_movement_observations(decisions, captures, sigmas, as_of)
        a[label] = {m: verdict_a(obs[m], as_of) for m in MARKETS}
    sign_check = {}
    v9_obs = line_movement_observations(v9, captures, sigmas, as_of)
    for m in MARKETS:
        rows = v9_obs[m]
        sign_check[m] = (
            _pearson(np.array([r["model"] for r in rows]), np.array([r["consensus_t"] for r in rows]))
            if len(rows) >= 3
            else None
        )
    events = _read_jsonl(out_root / "tc2_events.jsonl")
    ev_dates = {str(e.get("game_date")) for e in events if e.get("kind") == "event"}
    polls: list[dict[str, Any]] = []
    for day in sorted(ev_dates):
        polls.extend(_read_jsonl(mids_path(out_root, day)))
    tc2 = tc2_observations(events, polls, sigmas, as_of)
    b = {m: verdict_b(tc2[m]) for m in MARKETS}
    reasons: dict[str, int] = {}
    for r in records:
        key = str(r.get("reason") or ((r.get("variants") or {}).get("avail") or {}).get("reason") or "scored")
        reasons[key] = reasons.get(key, 0) + 1
    return {
        "as_of": as_of.isoformat(),
        "player_log_records": len(records),
        "player_log_reasons": reasons,
        "tc2_events_logged": sum(1 for e in events if e.get("kind") == "event"),
        "a": a,
        "v9_sign_check": sign_check,
        "b": b,
    }


def _f(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:+.{digits}f}"


def render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        f"# NBA v10 forward research report ({report['as_of'][:16]}Z)",
        "",
        (
            "Spec: `outputs/nba/v10_forward/PREREG_FORWARD.md` (pre-registered; nothing tuned on forward data). "
            "Gate G1 for `nba-player-v10` FAILED historically and that stands. Prices are probabilities "
            "(percent or cents); lines are points."
        ),
        "",
        f"Player-log records: {report['player_log_records']}; by reason: "
        + ", ".join(f"{k} {v}" for k, v in sorted(report["player_log_reasons"].items())),
        "",
        "## Test A: does model minus consensus predict the consensus move to T-10?",
        "",
        "| model | market | verdict | n / needed | corr | slope [95% CI] | Spearman |",
        "|---|---|---|---|---|---|---|",
    ]
    for label, block in report["a"].items():
        for market, v in block.items():
            fit = v["fit"]
            if fit is None:
                lines.append(
                    f"| {label} | {market} | {v['verdict']} | {v['n']} / {v['needed']} | n/a | n/a | n/a |"
                )
                continue
            lines.append(
                f"| {label} | {market} | {v['verdict']} | {v['n']} / {v['needed']} | {_f(fit.corr)} | "
                f"{_f(fit.slope)} [{_f(fit.slope_low)}, {_f(fit.slope_high)}] | {_f(fit.spearman)} |"
            )
    lines += [
        "",
        (
            "PASS needs corr > 0, slope > 0 and the slope CI above 0 on the first 400 games per market. "
            "`player_exavail` and `v9` are comparators, not gates. Below 400 only counts are shown."
        ),
        "",
        "v9 sign check, corr(v9 served mean, consensus mean at t): "
        + ", ".join(f"{m} {_f(c)}" for m, c in report["v9_sign_check"].items()),
        "",
        "## Test B: T-C2 injury latency",
        "",
        f"Events logged: {report['tc2_events_logged']} (game x report with a qualifying status change).",
        "",
        "| market | usable / needed | B1 mean signed move, points [95% CI] | moved with model | B2 share with >= 3.0 pp gap | B3 signed mid change, cents [95% CI] | verdict B1 | verdict B3 | excluded |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for market, v in report["b"].items():
        b1, b2, b3 = v["b1"], v["b2_gap_share"], v["b3"]
        share = "n/a" if v["b1_share_with_model"] is None else f"{v['b1_share_with_model'] * 100:.0f}%"
        gap = "n/a" if b2["mean"] is None else f"{b2['mean'] * 100:.0f}%"
        b3s = (
            "n/a"
            if b3["mean"] is None
            else f"{b3['mean'] * 100:+.2f}c [{_f(None if b3['low'] is None else b3['low'] * 100, 2)}, {_f(None if b3['high'] is None else b3['high'] * 100, 2)}] (n={b3['n']})"
        )
        lines.append(
            f"| {market} | {v['n_usable']} / {v['needed']} | {_f(b1['mean'], 2)} [{_f(b1['low'], 2)}, {_f(b1['high'], 2)}] | "
            f"{share} | {gap} | {b3s} | {v['verdict_b1']} | {v['verdict_b3']} | "
            + (", ".join(f"{k} {n}" for k, n in sorted(v["excluded"].items())) or "none")
            + " |"
        )
    lines += [
        "",
        "Taker fee for reference: 0.06 p (1 - p), about 1.5c at 50c. Median report latency (fetched minus "
        "published), minutes: "
        + ", ".join(f"{m} {_f(v['latency_minutes_median'], 1)}" for m, v in report["b"].items()),
        "",
    ]
    return "\n".join(lines)
