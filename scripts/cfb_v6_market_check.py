"""Market-relative harness for the v5 / v6 NCAAF challengers (quick-261114-srb, item 7 and acceptance).

  reproduce-v5   Regenerate the v5 market evidence from the ledger DB and the v5 artifact (point-in-time,
                 exactly as the 2026-11-14 scratch replay did) and gate it against the recorded figures.
                 Exits non-zero if the gate fails. Nothing here touches v6.
  v6             After the v6 fit is frozen: price the identical reference event sets with v6, compare with
                 v5 on the common covered set, write outputs/ncaaf/cfb_v6_market_check.{json,md}.
  v6 --check-only  Re-read the written JSON and confirm hash and counts without recomputing.
  v6-espn        Frozen v6 vs v5-calibrated on ESPN archived closing lines (E's espn_lines.jsonl), by season and by
                 fit / holdout window. Nothing is fitted on the lines.
  lines-audit    Thin wrapper over scripts/cfb_v6_lines_audit.py (item 7 line inventory).

Read-only: the ledger DB is opened mode=ro, no order endpoint is touched, no history file is written.
The 575 lined games are never used for selection or fitting; they are a post-freeze check only.

Usage (from repo root, OPENBLAS_NUM_THREADS=1):
  PYTHONPATH=src .venv/Scripts/python scripts/cfb_v6_market_check.py reproduce-v5
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import importlib.util
import json
import math
import random
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from model_prediction.domain import parse_utc
from model_prediction.models.cfb_structural_v5 import (
    CFBStructuralV5Model,
    load_cfb_structural_v5_artifact,
)
from model_prediction.models.college_football import UpcomingCFBGame, _load_cfb_history

AS_OF_UTC = "2026-11-15T02:10:00Z"
OUT = REPO / "outputs" / "ncaaf"
REFERENCE_PATH = OUT / "cfb_v5_market_reference_games.json"
PINNED_PATH = REPO / "data" / "research" / "ncaaf_v6" / "market_reference_pinned.json"
REPORT_JSON = OUT / "cfb_v6_market_check.json"
REPORT_MD = OUT / "cfb_v6_market_check.md"
LIVE_HISTORY = (
    REPO / "data" / "historical" / "ncaaf_games_all.jsonl",
    REPO / "data" / "processed" / "ncaaf" / "games.jsonl",
)

# Recorded 2026-11-14 figures (docs memory note) and tolerances from the plan.
GATE = {
    "total": {"corr": (-0.033, 0.005), "side": (218, 257), "model_sd": (4.13, 0.05)},
    "spread": {
        "corr": (0.009, 0.005),
        "side": (264, 233),
        "mae_model": (14.64, 0.05),
        "mae_line": (11.65, 0.05),
        "sd_model": (11.78, 0.05),
        "sd_line": (16.35, 0.05),
        "sd_actual": (22.37, 0.05),
    },
}
TOTAL_BUCKETS = ((0, 45, "under 45"), (45, 52, "45-52"), (52, 60, "52-60"), (60, 999, "60 and over"))
SPREAD_BUCKETS = ((0, 7, "|line| under 7"), (7, 14, "7-14"), (14, 21, "14-21"), (21, 999, "21 and over"))


# ------------------------------------------------------------------ shared helpers
def _replay_module() -> Any:
    spec = importlib.util.spec_from_file_location("replay_cfb", REPO / "scripts" / "replay_cfb_v4_all_settled.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def live_history_fingerprint() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for p in LIVE_HISTORY:
        rel = str(p.relative_to(REPO)).replace("\\", "/")
        out[rel] = {
            "rows": sum(1 for line in p.open("rb") if line.strip()),
            "sha256": file_sha256(p),
        }
    return out


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def pstdev(xs: list[float]) -> float:
    return statistics.pstdev(xs) if len(xs) >= 2 else float("nan")


def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return float("nan")
    mx, my = mean(xs), mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return float("nan")
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / math.sqrt(sxx * syy)


def ols_slope(xs: list[float], ys: list[float]) -> float:
    mx, my = mean(xs), mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx if sxx else float("nan")


def clustered_corr_ci(
    xs: list[float], ys: list[float], dates: list[str], *, seed: int = 17, n_boot: int = 2000
) -> dict[str, Any]:
    """Date-clustered bootstrap 95% CI of the Pearson correlation."""
    by_date: dict[str, list[int]] = defaultdict(list)
    for i, d in enumerate(dates):
        by_date[d].append(i)
    keys = sorted(by_date)
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(n_boot):
        idx = [i for d in (rng.choice(keys) for _ in keys) for i in by_date[d]]
        c = pearson([xs[i] for i in idx], [ys[i] for i in idx])
        if not math.isnan(c):
            samples.append(c)
    samples.sort()
    point = pearson(xs, ys)
    if not samples:
        return {"point": point, "ci_95_low": None, "ci_95_high": None, "dates": len(keys)}
    lo = samples[int(0.025 * (len(samples) - 1))]
    hi = samples[int(0.975 * (len(samples) - 1))]
    return {"point": point, "ci_95_low": lo, "ci_95_high": hi, "dates": len(keys)}


# ------------------------------------------------------------------ pinned reference sets
def build_pinned(replay: Any) -> dict[str, Any]:
    """One pinned ledger row per event per market, and the event-level facts the v5 replay used.

    Pin rule: among every settled NCAAF ledger row with a line (any result, flat and main tiers, ordered
    by event start then creation time) the LAST row per (event, market) supplies that market's line and
    decision time; this is the same last-row-wins rule the scratch replay's lines_by_event used. An event
    belongs to the set when it has at least one win/loss row with a valid executable ask and resolvable
    team names. Decision time = min(kickoff, the pinned row's observed_at_utc or created_at_utc).
    """
    rows = replay.load_settled_rows()
    facts = replay.load_schedule_facts()
    by_event_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_event_rows[str(r["event_id"])].append(r)
    events: dict[str, dict[str, Any]] = {}
    for eid, rs in by_event_rows.items():
        scored_rows = [
            r
            for r in rs
            if r["result"] in ("win", "loss")
            and r["market_probability"] not in (None, "")
            and 0 < float(r["market_probability"]) < 1
        ]
        if not scored_rows:
            continue
        pinned: dict[str, dict[str, Any]] = {}
        spread_home = total_line = None
        for r in rs:
            if r["line"] is None:
                continue
            sel = str(r["selection"]).lower()
            if r["market_type"] == "spread":
                spread_home = float(r["line"]) * (1 if sel == "home" else -1)
                pinned["spread"] = r
            elif r["market_type"] == "total":
                total_line = float(r["line"])
                pinned["total"] = r
        first = scored_rows[0]
        decision0 = json.loads(first["decision_payload_json"] or "{}")
        feats0 = json.loads(first["feature_payload_json"] or "{}")
        snap0 = ((feats0.get("model_input_snapshot") or {}).get("features")) or {}
        home = str(decision0.get("home_team") or snap0.get("home_team") or "")
        away = str(decision0.get("away_team") or snap0.get("away_team") or "")
        if not home or not away:
            continue
        start = parse_utc(str(first["event_start_utc"]))
        fact = facts.get(eid, {})
        neutral = bool(snap0.get("is_neutral_site", fact.get("is_neutral_site", False)))
        entry: dict[str, Any] = {
            "event_id": eid,
            "start": str(first["event_start_utc"]),
            "home": home,
            "away": away,
            "neutral": neutral,
            "home_score": decision0.get("home_score"),
            "away_score": decision0.get("away_score"),
            "spread_home_line": spread_home,
            "total_line": total_line,
            "week_fact": fact.get("week"),
            "decision_utc": {},
        }
        for market, r in pinned.items():
            decision = json.loads(r["decision_payload_json"] or "{}")
            observed = parse_utc(str(decision.get("observed_at_utc") or r["created_at_utc"]))
            entry["decision_utc"][market] = min(start, observed).isoformat()
        if not entry["decision_utc"]:
            continue
        events[eid] = entry
    return {"events": events, "ledger_rows": len(rows)}


def derive_weeks(replay: Any, history: list[Any]) -> dict[int, datetime]:
    season_first: dict[int, datetime] = {}
    for h in history:
        s = replay.season_of(h.start)
        if s not in season_first or h.start < season_first[s]:
            season_first[s] = h.start
    return season_first


def v5_raw_projection(
    model: CFBStructuralV5Model,
    replay: Any,
    history: list[Any],
    starts: list[datetime],
    season_first: dict[int, datetime],
    ev: dict[str, Any],
    market: str,
    cache: dict[Any, Any],
) -> dict[str, Any]:
    observed = parse_utc(ev["decision_utc"][market])
    start = parse_utc(ev["start"])
    week = ev.get("week_fact")
    if week is None:
        week = replay.derive_week(start, season_first)
    key = (ev["event_id"], observed.isoformat(), ev["spread_home_line"], ev["total_line"], ev["neutral"])
    if key in cache:
        return cache[key]
    past = history[: bisect.bisect_left(starts, observed)]
    up = UpcomingCFBGame(
        event_id=ev["event_id"],
        event_start_utc=ev["start"],
        away_team=ev["away"],
        home_team=ev["home"],
        spread_home_line=ev["spread_home_line"],
        total_line=ev["total_line"],
        is_neutral_site=ev["neutral"],
        season_year=replay.season_of(start),
        week=int(week),
    )
    preds = model.predict_matchup(past, up, project_missing_lines=True)
    ml = next(p for p in preds if p.market_type == "moneyline")
    fb = ml.feature_basis
    out = {
        "pm": float(fb["projected_margin_home"]),
        "pt": float(fb["projected_total"]),
        "sg": int(fb["sample_games"]),
        "fcs": bool(fb["is_fbs_vs_fcs"]),
        "past_n": len(past),
    }
    cache[key] = out
    return out


# ------------------------------------------------------------------ per-market statistics
def market_stats(rows: list[dict[str, Any]], market: str, model_key: str) -> dict[str, Any]:
    """rows carry actual, line and the model value on the same scale (total points, or home margin)."""
    model = [r[model_key] for r in rows]
    line = [r["line"] for r in rows]
    actual = [r["actual"] for r in rows]
    dates = [r["date"] for r in rows]
    xs = [m - ln for m, ln in zip(model, line, strict=True)]
    ys = [a - ln for a, ln in zip(actual, line, strict=True)]
    wins = losses = pushes = 0
    for x, y in zip(xs, ys, strict=True):
        if abs(x) < 1.0:
            continue
        if y == 0:
            pushes += 1
        elif (x > 0) == (y > 0):
            wins += 1
        else:
            losses += 1
    out: dict[str, Any] = {
        "n": len(rows),
        "corr": pearson(xs, ys),
        "corr_ci": clustered_corr_ci(xs, ys, dates),
        "ols_slope": ols_slope(xs, ys),
        "side_wins": wins,
        "side_losses": losses,
        "side_pushes": pushes,
        "mae_model": mean([abs(a - m) for a, m in zip(actual, model, strict=True)]),
        "mae_line": mean([abs(a - ln) for a, ln in zip(actual, line, strict=True)]),
        "sd_model": pstdev(model),
        "sd_line": pstdev(line),
        "sd_actual": pstdev(actual),
    }
    buckets = TOTAL_BUCKETS if market == "total" else SPREAD_BUCKETS
    bucket_rows = []
    for lo, hi, label in buckets:
        if market == "total":
            sub = [r for r in rows if lo <= r["line"] < hi]
        else:
            sub = [r for r in rows if lo <= abs(r["line"]) < hi]
        entry: dict[str, Any] = {"bucket": label, "n": len(sub)}
        if sub:
            if market == "total":
                took = [r for r in sub if abs(r[model_key] - r["line"]) >= 1.0 and r["actual"] != r["line"]]
                over_model = [r for r in took if r[model_key] > r["line"]]
                entry["actual_over_pct"] = 100.0 * sum(1 for r in sub if r["actual"] > r["line"]) / len(sub)
                entry["model_over_takes"] = len(over_model)
                entry["model_under_takes"] = len(took) - len(over_model)
                entry["model_side_win_pct"] = (
                    100.0 * sum(1 for r in took if (r[model_key] > r["line"]) == (r["actual"] > r["line"])) / len(took)
                    if took
                    else None
                )
            else:
                took = [r for r in sub if abs(r[model_key] - r["line"]) >= 1.0 and r["actual"] != r["line"]]
                entry["model_side_win_pct"] = (
                    100.0 * sum(1 for r in took if (r[model_key] > r["line"]) == (r["actual"] > r["line"])) / len(took)
                    if took
                    else None
                )
                entry["side_takes"] = len(took)
        bucket_rows.append(entry)
    out["buckets"] = bucket_rows
    if market == "spread":
        big = [r for r in rows if abs(r["line"]) >= 21]
        fav = []
        for r in big:
            sgn = 1.0 if r["line"] > 0 else -1.0  # line is the home margin; positive = home favoured
            fav.append((sgn * r[model_key], sgn * r["line"], sgn * r["actual"], r[model_key], r["line"]))
        dog_takes = 0
        for r in big:
            gap = r[model_key] - r["line"]
            if abs(gap) >= 1.0 and ((r["line"] > 0 and gap < 0) or (r["line"] < 0 and gap > 0)):
                dog_takes += 1
        out["line_21_plus"] = {
            "n": len(big),
            "mean_model_favourite_margin": mean([f[0] for f in fav]) if fav else None,
            "mean_line_favourite_margin": mean([f[1] for f in fav]) if fav else None,
            "mean_actual_favourite_margin": mean([f[2] for f in fav]) if fav else None,
            "dog_takes": dog_takes,
        }
    return out


def build_market_rows(
    pinned: dict[str, Any], model_values: dict[tuple[str, str], dict[str, float]], market: str
) -> list[dict[str, Any]]:
    """model_values[(event, market)] = {'value': model total or home margin}; only events present are used."""
    rows = []
    for eid, ev in pinned["events"].items():
        if market not in ev["decision_utc"]:
            continue
        mv = model_values.get((eid, market))
        if mv is None:
            continue
        hs, as_ = ev["home_score"], ev["away_score"]
        if hs is None or as_ is None:
            continue
        hs, as_ = float(hs), float(as_)
        if market == "total":
            line = ev["total_line"]
            actual = hs + as_
        else:
            line = -ev["spread_home_line"]  # home margin implied by the line
            actual = hs - as_
        rows.append(
            {
                "event_id": eid,
                "date": ev["start"][:10],
                "line": float(line),
                "actual": actual,
                "model": mv["value"],
                "sg": mv.get("sg"),
                "fcs": mv.get("fcs"),
            }
        )
    return rows


# ------------------------------------------------------------------ reproduce-v5
def v5_calibrated_values(art: dict[str, Any], proj: dict[str, Any], market: str) -> float:
    if market == "total":
        cal = art["total_calibration"]
        return max(24.0, cal["intercept"] + cal["slope"] * proj["pt"])
    cal = art["margin_calibration"]
    return cal["intercept"] + cal["slope"] * proj["pm"]


def compute_v5_block_saved() -> dict[str, Any]:
    """The v5 side of the check from the pinned reference saved by `reproduce-v5` (no live ledger or history).

    The live ledger and history files keep moving (settlement, 05:30Z daily appends); the pinned file holds the
    events, lines, scores, decision times and v5 raw projections exactly as the gate-passing reproduction saw them.
    """
    saved = json.loads(PINNED_PATH.read_text(encoding="utf-8"))
    events = saved["events"]
    projections = {tuple(k.split("|")): v for k, v in saved["projections"].items()}
    ref = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    ref_keys = {(r["start"], r["home"], r["away"]) for r in ref if r.get("tl") is not None or r.get("hl") is not None}
    pinned_keys = {(e["start"], e["home"], e["away"]) for e in events.values()}
    mismatches = []
    for eid, ev in events.items():
        r = ev.get("ref")
        if r is None:
            mismatches.append({"event_id": eid, "missing_ref": True})
            continue
        any_proj = next(projections[(eid, m)] for m in ev["decision_utc"])
        if abs(any_proj["pm"] - r["pm"]) > 0.1 + 1e-9 or abs(any_proj["pt"] - r["pt"]) > 0.1 + 1e-9:
            mismatches.append({"event_id": eid, "mine": [any_proj["pm"], any_proj["pt"]], "ref": [r["pm"], r["pt"]]})
    return {
        "art": load_cfb_structural_v5_artifact(),
        "pinned": {"events": events, "ledger_rows": None},
        "projections": projections,
        "mismatches": mismatches,
        "unmatched_pinned": [],
        "ref_not_in_pinned": sorted(ref_keys - pinned_keys),
        "event_set_equal": ref_keys == pinned_keys,
        "history_rows": None,
        "source": "saved pinned reference (market_reference_pinned.json)",
    }


def compute_v5_block() -> dict[str, Any]:
    """Pin the reference sets and compute v5 (raw + artifact-calibrated) values for them."""
    replay = _replay_module()
    art = load_cfb_structural_v5_artifact()
    model = CFBStructuralV5Model.from_artifact(art)
    history = _load_cfb_history(REPO / "data")
    starts = [h.start for h in history]
    season_first = derive_weeks(replay, history)
    pinned = build_pinned(replay)
    ref = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    ref_by_key = {(r["start"], r["home"], r["away"]): r for r in ref}
    cache: dict[Any, Any] = {}
    projections: dict[tuple[str, str], dict[str, Any]] = {}
    mismatches: list[dict[str, Any]] = []
    unmatched_pinned = []
    matched_events: set[str] = set()
    for eid, ev in pinned["events"].items():
        r = ref_by_key.get((ev["start"], ev["home"], ev["away"]))
        if r is None:
            unmatched_pinned.append(eid)
            continue
        matched_events.add(eid)
        ev["ref"] = r
        for market in ev["decision_utc"]:
            proj = v5_raw_projection(model, replay, history, starts, season_first, ev, market, cache)
            projections[(eid, market)] = proj
        any_proj = next(projections[(eid, m)] for m in ev["decision_utc"])
        if abs(any_proj["pm"] - r["pm"]) > 0.1 + 1e-9 or abs(any_proj["pt"] - r["pt"]) > 0.1 + 1e-9:
            mismatches.append(
                {"event_id": eid, "mine": [any_proj["pm"], any_proj["pt"]], "ref": [r["pm"], r["pt"]]}
            )
        if ev["total_line"] != r.get("tl") or ev["spread_home_line"] != r.get("hl"):
            mismatches.append(
                {
                    "event_id": eid,
                    "lines_mine": [ev["total_line"], ev["spread_home_line"]],
                    "lines_ref": [r.get("tl"), r.get("hl")],
                }
            )
        if float(ev["home_score"]) != float(r["h"]) or float(ev["away_score"]) != float(r["a"]):
            mismatches.append({"event_id": eid, "scores": [ev["home_score"], ev["away_score"], r["h"], r["a"]]})
    # The one reference event with no line at all (moneyline-only) has no market to check; the lined
    # reference events (576: 575 with a total, 576 with a spread) must equal the pinned set exactly.
    ref_keys = {k for k, r in ref_by_key.items() if r.get("tl") is not None or r.get("hl") is not None}
    pinned_keys = {(ev["start"], ev["home"], ev["away"]) for ev in pinned["events"].values()}
    return {
        "art": art,
        "pinned": pinned,
        "projections": projections,
        "mismatches": mismatches,
        "unmatched_pinned": unmatched_pinned,
        "ref_not_in_pinned": sorted(ref_keys - pinned_keys),
        "event_set_equal": ref_keys == pinned_keys,
        "history_rows": len(history),
    }


def v5_market_numbers(block: dict[str, Any]) -> dict[str, dict[str, Any]]:
    art = block["art"]
    out: dict[str, dict[str, Any]] = {}
    for market in ("total", "spread"):
        values = {
            k: {"value": v5_calibrated_values(art, p, market), "sg": p["sg"], "fcs": p["fcs"]}
            for k, p in block["projections"].items()
            if k[1] == market
        }
        rows = build_market_rows(block["pinned"], values, market)
        out[market] = {"rows": rows, "stats": market_stats(rows, market, "model")}
    return out


def gate_check(stats: dict[str, dict[str, Any]]) -> tuple[bool, list[str]]:
    lines: list[str] = []
    ok = True

    def chk(label: str, got: float, want: float, tol: float) -> None:
        nonlocal ok
        good = abs(got - want) <= tol + 1e-12
        ok &= good
        lines.append(f"  {'ok  ' if good else 'FAIL'} {label}: got {got:.4f}, recorded {want} (+-{tol}), diff {got - want:+.4f}")

    t, s = stats["total"], stats["spread"]
    chk("total n", t["n"], 575, 0)
    chk("spread n", s["n"], 576, 0)
    chk("total corr", t["corr"], *GATE["total"]["corr"])
    chk("total side wins", t["side_wins"], GATE["total"]["side"][0], 0)
    chk("total side losses", t["side_losses"], GATE["total"]["side"][1], 0)
    chk("total model SD", t["sd_model"], *GATE["total"]["model_sd"])
    g = GATE["spread"]
    chk("spread corr", s["corr"], *g["corr"])
    chk("spread side wins", s["side_wins"], g["side"][0], 0)
    chk("spread side losses", s["side_losses"], g["side"][1], 0)
    for k in ("mae_model", "mae_line", "sd_model", "sd_line", "sd_actual"):
        chk(f"spread {k}", s[k], *g[k])
    return ok, lines


def cmd_reproduce_v5(args: argparse.Namespace) -> int:
    block = compute_v5_block()
    print(f"as_of {AS_OF_UTC}; history rows {block['history_rows']}; ledger rows {block['pinned']['ledger_rows']}")
    print(
        f"events pinned {len(block['pinned']['events'])}; reference {len(json.loads(REFERENCE_PATH.read_text()))}; "
        f"event set equal: {block['event_set_equal']}; unmatched pinned {len(block['unmatched_pinned'])}; "
        f"reference-not-pinned {len(block['ref_not_in_pinned'])}"
    )
    print(f"projection / line / score mismatches vs reference: {len(block['mismatches'])}")
    for m in block["mismatches"][:10]:
        print("   ", m)
    nums = v5_market_numbers(block)
    stats = {m: nums[m]["stats"] for m in nums}
    ok, lines = gate_check(stats)
    print("gate:")
    print("\n".join(lines))
    ok = ok and block["event_set_equal"] and not block["mismatches"]
    PINNED_PATH.parent.mkdir(parents=True, exist_ok=True)
    PINNED_PATH.write_text(
        json.dumps(
            {
                "as_of_utc": AS_OF_UTC,
                "events": block["pinned"]["events"],
                "projections": {f"{k[0]}|{k[1]}": v for k, v in block["projections"].items()},
            },
            default=str,
        ),
        encoding="utf-8",
    )
    print("GATE", "PASS" if ok else "FAIL")
    return 0 if ok else 2


# ------------------------------------------------------------------ v6 market check (after freeze)
MD_NOTE = (
    "The 575-game check is design-informed (the v5 loss review that motivated v6 used these same games) and its "
    "window sits inside the locked holdout, so the two results are not independent. At about 575 games the "
    "correlation CI is roughly +/-0.08, so a small positive point estimate is no evidence of edge; the spread of "
    "projections, over% by line bucket and dog counts below are diagnostics, not evidence. No promotion decision."
)


def verdict(stat: dict[str, Any]) -> dict[str, Any]:
    ci = stat["corr_ci"]
    pos = stat["corr"] > 0
    excl = ci["ci_95_low"] is not None and (ci["ci_95_low"] > 0 or ci["ci_95_high"] < 0)
    return {
        "positive_out_of_sample_correlation": bool(pos),
        "ci_excludes_zero": bool(excl),
        "text": (
            f"positive correlation: {'yes' if pos else 'no'} (point {stat['corr']:+.3f}); "
            f"95% CI [{ci['ci_95_low']:+.3f}, {ci['ci_95_high']:+.3f}] "
            f"{'excludes' if excl else 'includes'} zero"
        ),
    }


def compute_v6_block(frozen: dict[str, Any], pinned: dict[str, Any]) -> dict[str, Any]:
    import cfb_v6_lib as lib

    snap = REPO / "data" / "research" / "ncaaf_v6" / "history_snapshot.jsonl"
    games = lib.load_snapshot(snap)
    resolver = lib.TeamResolver.from_games(games)
    book = lib.RatingBook(games, resolver, buffer_hours=frozen["result_buffer_hours"])
    sel = frozen["selected"]
    params = frozen["params"]
    mc, tc = params["margin_calibration"], params["total_calibration"]
    queries = []
    for eid, ev in pinned["events"].items():
        for market, when in ev["decision_utc"].items():
            queries.append((parse_utc(when), eid, market))
    queries.sort(key=lambda q: (q[0], q[1], q[2]))
    out: dict[tuple[str, str], dict[str, Any]] = {}
    uncovered: dict[tuple[str, str], str] = {}
    last_key = None
    sol = None
    for when, eid, market in queries:
        ev = pinned["events"][eid]
        ptr = book.advance(when)
        key = (ptr, lib.season_of(when))
        if key != last_key:
            try:
                sol = book.solve(when, lam=sel["lambda"], w_prev=sel["w_prev"])
            except lib.V6NoCall:
                sol = None
            last_key = key
        if sol is None:
            uncovered[(eid, market)] = lib.NO_CALL_NO_HISTORY
            continue
        try:
            p = lib.project(sol, book, ev["home"], ev["away"], ev["neutral"])
        except lib.V6NoCall as exc:
            uncovered[(eid, market)] = exc.reason
            continue
        if market == "total":
            value = tc["intercept"] + tc["slope"] * p.total
        else:
            value = mc["intercept"] + mc["slope"] * p.margin
        out[(eid, market)] = {
            "value": value,
            "raw": p.total if market == "total" else p.margin,
            "sg": p.sample_games,
            "fcs": p.is_fbs_vs_fcs,
        }
    reasons: dict[str, dict[str, int]] = {"total": {}, "spread": {}}
    for (_, market), reason in uncovered.items():
        reasons[market][reason] = reasons[market].get(reason, 0) + 1
    return {"values": out, "uncovered": uncovered, "reason_counts": reasons, "snapshot_games": len(games)}


def subset(rows: list[dict[str, Any]], ids: set[str]) -> list[dict[str, Any]]:
    return [r for r in rows if r["event_id"] in ids]


def run_v6_check(args: argparse.Namespace) -> int:
    import cfb_v6_research as r6

    if args.check_only:
        return check_only(r6)
    frozen_hash = r6.verify_frozen(quiet=True)
    frozen = json.loads(r6.FROZEN.read_text(encoding="utf-8"))
    v5 = compute_v5_block_saved()
    nums = v5_market_numbers(v5)
    gate_ok, gate_lines = gate_check({m: nums[m]["stats"] for m in nums})
    if not gate_ok or not v5["event_set_equal"] or v5["mismatches"]:
        print("\n".join(gate_lines))
        raise SystemExit("v5 reproduction gate failed; refusing to run v6")
    v6 = compute_v6_block(frozen, v5["pinned"])
    report: dict[str, Any] = {
        "as_of_utc": AS_OF_UTC,
        "frozen_params_sha256": frozen_hash,
        "frozen_label": frozen["label"],
        "reference": {
            "total_events": nums["total"]["stats"]["n"],
            "spread_events": nums["spread"]["stats"]["n"],
            "reference_file": "outputs/ncaaf/cfb_v5_market_reference_games.json",
            "reference_sha256": file_sha256(REFERENCE_PATH),
        },
        "v5_reproduction_gate": {"pass": True, "lines": gate_lines, "source": v5["source"]},
        "pinned_reference_sha256": file_sha256(PINNED_PATH),
        "history_snapshot_sha256": frozen["history_snapshot"]["sha256"],
        "first_look": True,
        "selected": frozen["selected"],
        "markets": {},
        "note": MD_NOTE,
    }
    for market in ("total", "spread"):
        full_rows = nums[market]["rows"]
        v6_rows = build_market_rows(v5["pinned"], {k: v for k, v in v6["values"].items() if k[1] == market}, market)
        covered_ids = {r["event_id"] for r in v6_rows}
        v5_common = subset(full_rows, covered_ids)
        block = {
            "v5_full_set": market_stats(full_rows, market, "model"),
            "v5_common_set": market_stats(v5_common, market, "model") if v5_common else None,
            "v6_common_set": market_stats(v6_rows, market, "model") if v6_rows else None,
            "v6_covered_n": len(v6_rows),
            "v5_full_n": len(full_rows),
            "v6_uncovered_reasons": v6["reason_counts"][market],
        }
        if block["v6_common_set"]:
            block["verdict_v6"] = verdict(block["v6_common_set"])
        if block["v5_common_set"]:
            block["verdict_v5_common"] = verdict(block["v5_common_set"])
        block["verdict_v5_full"] = verdict(block["v5_full_set"])
        report["markets"][market] = block
    report["post_reference_events"] = {
        "note": "not scored here: the reference sets are the pinned 575 / 576 events only",
        "count": 0,
    }
    REPORT_JSON.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    REPORT_MD.write_text(render_md(report), encoding="utf-8")
    print("wrote", REPORT_JSON, REPORT_MD)
    for m, b in report["markets"].items():
        print(m, "v6 covered", b["v6_covered_n"], "of", b["v5_full_n"], b.get("verdict_v6", {}).get("text"))
    return 0


def check_only(r6: Any) -> int:
    rep = json.loads(REPORT_JSON.read_text(encoding="utf-8"))
    want = r6.verify_frozen(quiet=True)
    problems = []
    if rep["frozen_params_sha256"] != want:
        problems.append("market report frozen hash differs from verify-frozen")
    if rep["reference"]["total_events"] != 575 or rep["reference"]["spread_events"] != 576:
        problems.append("reference event counts are not 575/576")
    for m in ("total", "spread"):
        b = rep["markets"][m]
        if not b.get("v6_covered_n") or b.get("v6_common_set") is None:
            problems.append(f"{m}: v6 covered n / common-set stats not recorded")
    if problems:
        print("check-only FAILED:", "; ".join(problems), file=sys.stderr)
        return 1
    print("check-only ok", want, {m: rep["markets"][m]["v6_covered_n"] for m in rep["markets"]})
    return 0


def _fmt(x: Any, nd: int = 3) -> str:
    return "n/a" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def _market_table(rep: dict[str, Any], market: str) -> list[str]:
    b = rep["markets"][market]
    cols = [
        ("v5 full set", b["v5_full_set"]),
        ("v5 common set", b["v5_common_set"]),
        ("v6 common set", b["v6_common_set"]),
    ]
    lines = [
        f"### {market.capitalize()}s",
        "",
        (
            f"v6 covered {b['v6_covered_n']} of {b['v5_full_n']} events; uncovered reasons: "
            f"{json.dumps(b['v6_uncovered_reasons'])}."
        ),
        "",
        "| statistic | " + " | ".join(c[0] for c in cols) + " |",
        "|---|" + "---|" * len(cols),
    ]

    def row(label: str, fn: Any) -> None:
        lines.append(f"| {label} | " + " | ".join("n/a" if c[1] is None else fn(c[1]) for c in cols) + " |")

    row("n", lambda s: str(s["n"]))
    row("corr(model - line, actual - line)", lambda s: _fmt(s["corr"]))
    row(
        "corr 95% CI (date-clustered)",
        lambda s: f"[{_fmt(s['corr_ci']['ci_95_low'])}, {_fmt(s['corr_ci']['ci_95_high'])}]",
    )
    row("OLS slope", lambda s: _fmt(s["ols_slope"]))
    row("model side record (|gap| >= 1, no push)", lambda s: f"{s['side_wins']}-{s['side_losses']}")
    row("MAE model / MAE line", lambda s: f"{_fmt(s['mae_model'], 2)} / {_fmt(s['mae_line'], 2)}")
    row(
        "SD model / line / actual",
        lambda s: f"{_fmt(s['sd_model'], 2)} / {_fmt(s['sd_line'], 2)} / {_fmt(s['sd_actual'], 2)}",
    )
    lines.append("")
    for name, s in cols:
        if s is None:
            continue
        lines += [f"Buckets, {name}:", ""]
        if market == "total":
            lines += ["| line bucket | n | actual over % | model over / under takes | model side win % |", "|---|---|---|---|---|"]
            for bk in s["buckets"]:
                lines.append(
                    f"| {bk['bucket']} | {bk['n']} | {_fmt(bk.get('actual_over_pct'), 1)} | "
                    f"{bk.get('model_over_takes', 'n/a')} / {bk.get('model_under_takes', 'n/a')} | "
                    f"{_fmt(bk.get('model_side_win_pct'), 1)} |"
                )
        else:
            lines += ["| line bucket | n | side takes | model side win % |", "|---|---|---|---|"]
            for bk in s["buckets"]:
                lines.append(
                    f"| {bk['bucket']} | {bk['n']} | {bk.get('side_takes', 'n/a')} | "
                    f"{_fmt(bk.get('model_side_win_pct'), 1)} |"
                )
            big = s["line_21_plus"]
            lines += [
                "",
                (
                    f"Lines of 21+: n {big['n']}, mean favourite margin model "
                    f"{_fmt(big['mean_model_favourite_margin'], 2)} vs line "
                    f"{_fmt(big['mean_line_favourite_margin'], 2)} "
                    f"vs actual {_fmt(big['mean_actual_favourite_margin'], 2)}; dog takes {big['dog_takes']}."
                ),
            ]
        lines.append("")
    return lines


def render_md(rep: dict[str, Any]) -> str:
    lines = [
        f"# cfb-structural-v6 market-relative check, as of {rep['as_of_utc']}",
        "",
        (
            f"Frozen fit sha256: `{rep['frozen_params_sha256']}` ({rep['frozen_label']}). "
            f"Selected: {json.dumps(rep['selected'])}."
        ),
        "",
        (
            f"v5 reproduction gate: PASS (reference file sha256 `{rep['reference']['reference_sha256'][:16]}`). "
            f"Reference sets: {rep['reference']['total_events']} totals, {rep['reference']['spread_events']} spreads."
        ),
        "",
        "> " + rep["note"],
        "",
        "## Verdicts",
        "",
    ]
    for m, b in rep["markets"].items():
        lines.append(f"- **{m}, v6 (common covered set, n {b['v6_covered_n']})**: {b['verdict_v6']['text']}")
        if "verdict_v5_common" in b:
            lines.append(f"- {m}, v5 on the same events: {b['verdict_v5_common']['text']}")
        lines.append(f"- {m}, v5 full set (n {b['v5_full_n']}): {b['verdict_v5_full']['text']}")
    lines += ["", "## Tables", ""]
    for m in ("total", "spread"):
        lines += _market_table(rep, m)
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ v6-espn: closing-line check on ESPN archived lines
ESPN_LINES = REPO / "data" / "research" / "ncaaf_v6" / "espn_lines.jsonl"
ESPN_AUDIT = OUT / "cfb_v6_espn_lines_audit.json"
ESPN_JSON = OUT / "cfb_v6_espn_market_check.json"
ESPN_MD = OUT / "cfb_v6_espn_market_check.md"
V6_CACHE = OUT / "cfb_v6_base_rows.jsonl"
V5_CACHE = OUT / "cfb_v5_base_rows.jsonl"
ESPN_NOTE = (
    "ESPN's odds feed gives archived closing/final pregame lines with no per-quote observed_at: a closing-line "
    "benchmark, not decision-time quotes. The line is E's pinned rule (median across non-live providers, fixed "
    "before any outcome was joined; sha256 in the header); nothing was fitted on any line. Fit-window games "
    "(on or before the frozen validation end) were used to fit v6's calibration and v5's, so the holdout-window "
    "seasons are the clean test. The 575/576 check is a design-informed subset of the 2026 holdout-window season "
    "and is not independent of it. Spread of projections, over% by line bucket and dog takes are diagnostics, not "
    "evidence. No promotion decision."
)
SENSITIVITY_EXCLUDE = ("Opening", "consensus")


def _load_espn_lines() -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    import importlib.util as ilu

    spec = ilu.spec_from_file_location("espn_lines_mod", REPO / "scripts" / "cfb_v6_espn_lines.py")
    mod = ilu.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    out: dict[str, dict[str, Any]] = {}
    rule_shas: set[str] = set()
    for line in ESPN_LINES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        sel = rec.get("selected") or {}
        rule_shas.add(str(rec.get("rule_sha256")))
        eid = str(rec["event_id"])
        entry: dict[str, Any] = {
            "spread_home": sel.get("home_spread_line"),
            "total": sel.get("total_line"),
            "n_providers": sel.get("n_nonlive_providers"),
            "spread_range": sel.get("spread_range"),
            "total_range": sel.get("total_range"),
        }
        spreads, totals = [], []
        for p in rec.get("providers") or []:
            if p.get("is_live") or p.get("provider_name") in SENSITIVITY_EXCLUDE:
                continue
            sp, tt = mod.provider_values(p)
            if sp is not None:
                spreads.append(sp)
            if tt is not None:
                totals.append(tt)
        entry["spread_home_ex_opening"] = statistics.median(spreads) if spreads else None
        entry["total_ex_opening"] = statistics.median(totals) if totals else None
        out[eid] = entry
    return out, {"rule_sha256_values": sorted(rule_shas), "records": len(out)}


def _group_stats(rows: list[dict[str, Any]], market: str, key: str) -> dict[str, Any] | None:
    return market_stats(rows, market, key) if len(rows) >= 30 else ({"n": len(rows)} if rows else None)


def run_v6_espn(args: argparse.Namespace) -> int:
    import cfb_v6_research as r6

    frozen_hash = r6.verify_frozen(quiet=True)
    frozen = json.loads(r6.FROZEN.read_text(encoding="utf-8"))
    sel = frozen["selected"]
    gk = r6.gkey(sel["lambda"], sel["w_prev"])
    params = frozen["params"]
    mc, tc = params["margin_calibration"], params["total_calibration"]
    val_end = frozen["split"]["validation_end"]
    v5_art = load_cfb_structural_v5_artifact()
    v5mc, v5tc = v5_art["margin_calibration"], v5_art["total_calibration"]
    lines, line_meta = _load_espn_lines()
    audit_sha = file_sha256(ESPN_AUDIT) if ESPN_AUDIT.exists() else None
    v5_by_id = {}
    for ln in V5_CACHE.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            r = json.loads(ln)
            v5_by_id[r["event_id"]] = r
    rows_by_market: dict[str, list[dict[str, Any]]] = {"total": [], "spread": []}
    sens_by_market: dict[str, list[dict[str, Any]]] = {"total": [], "spread": []}
    skipped = {"no_v6_row": 0, "no_call": 0, "no_line": 0}
    for ln in V6_CACHE.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        r = json.loads(ln)
        eid = r["event_id"]
        if eid not in lines:
            continue
        if "grid" not in r:
            skipped["no_call"] += 1
            continue
        ent = lines[eid]
        pm, pt = r["grid"][gk][:2]
        v6_margin = mc["intercept"] + mc["slope"] * pm
        v6_total = tc["intercept"] + tc["slope"] * pt
        v5r_ = v5_by_id.get(eid)
        v5_margin = v5mc["intercept"] + v5mc["slope"] * v5r_["proj_margin"] if v5r_ else None
        v5_total = max(24.0, v5tc["intercept"] + v5tc["slope"] * v5r_["proj_total"]) if v5r_ else None
        base = {
            "event_id": eid,
            "date": r["date"],
            "season": r["season"],
            "window": "holdout" if r["date"] > val_end else "fit",
            "source": r["source"],
            "game_class": r.get("game_class"),
        }
        for market, line_key, sens_key in (
            ("total", "total", "total_ex_opening"),
            ("spread", "spread_home", "spread_home_ex_opening"),
        ):
            raw_line = ent.get(line_key)
            if raw_line is None:
                skipped["no_line"] += 1
                continue
            line_val = float(raw_line) if market == "total" else -float(raw_line)
            actual = r["total"] if market == "total" else r["margin"]
            row = {
                **base,
                "line": line_val,
                "actual": actual,
                "v6": v6_total if market == "total" else v6_margin,
                "v5": v5_total if market == "total" else v5_margin,
            }
            rows_by_market[market].append(row)
            sraw = ent.get(sens_key)
            if sraw is not None:
                sens_by_market[market].append(
                    {**row, "line": float(sraw) if market == "total" else -float(sraw)}
                )
    report: dict[str, Any] = {
        "as_of_utc": AS_OF_UTC,
        "first_look": True,
        "frozen_params_sha256": frozen_hash,
        "espn_lines_sha256": file_sha256(ESPN_LINES),
        "espn_lines_audit_sha256": audit_sha,
        "espn_rule_sha256_values": line_meta["rule_sha256_values"],
        "lined_events_in_file": line_meta["records"],
        "validation_end": val_end,
        "skipped": skipped,
        "note": ESPN_NOTE,
        "markets": {},
    }
    for market in ("total", "spread"):
        rows = rows_by_market[market]
        common = [r for r in rows if r["v5"] is not None]
        block: dict[str, Any] = {"n_lined_v6": len(rows), "n_common_v5_v6": len(common), "groups": {}}

        def add_group(name: str, sub: list[dict[str, Any]], market: str = market, block: dict[str, Any] = block) -> None:
            com = [r for r in sub if r["v5"] is not None]
            entry: dict[str, Any] = {
                "n_lined_v6": len(sub),
                "v6_all_covered": _group_stats(
                    [{**r, "model": r["v6"]} for r in sub], market, "model"
                ),
                "v6_common": _group_stats([{**r, "model": r["v6"]} for r in com], market, "model"),
                "v5_common": _group_stats([{**r, "model": r["v5"]} for r in com], market, "model"),
            }
            for tag in ("v6_all_covered", "v6_common", "v5_common"):
                st = entry[tag]
                if st and "corr" in st:
                    entry[f"verdict_{tag}"] = verdict(st)
            block["groups"][name] = entry

        for window in ("fit", "holdout"):
            add_group(f"window:{window}", [r for r in rows if r["window"] == window])
        for season in sorted({r["season"] for r in rows}):
            add_group(f"season:{season}", [r for r in rows if r["season"] == season])
        add_group("all", rows)
        sens = sens_by_market[market]
        sens_h = [r for r in sens if r["window"] == "holdout"]
        block["sensitivity_ex_opening_consensus_holdout"] = {
            "note": "same rows, line = median excluding providers named Opening and consensus (diagnostic only)",
            "n": len(sens_h),
            "v6_all_covered": _group_stats([{**r, "model": r["v6"]} for r in sens_h], market, "model"),
        }
        # orientation sanity on the pinned line (diagnostic, uses outcomes only to confirm sign convention)
        if market == "spread":
            lm = [r["line"] for r in rows]
            am = [r["actual"] for r in rows]
            fav = [r for r in rows if r["line"] != 0 and r["actual"] != 0]
            block["orientation_check"] = {
                "corr_implied_home_margin_vs_actual": pearson(lm, am),
                "favourite_wins_straight_up_pct": 100.0
                * sum(1 for r in fav if (r["line"] > 0) == (r["actual"] > 0))
                / max(1, len(fav)),
                "n": len(fav),
            }
        else:
            tl = [r["line"] for r in rows]
            ta = [r["actual"] for r in rows]
            block["orientation_check"] = {"corr_line_vs_actual_total": pearson(tl, ta), "n": len(rows)}
        report["markets"][market] = block
    ESPN_JSON.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    ESPN_MD.write_text(render_espn_md(report), encoding="utf-8")
    print("wrote", ESPN_JSON, ESPN_MD)
    for m, b in report["markets"].items():
        for gname in ("window:fit", "window:holdout"):
            g = b["groups"].get(gname)
            if g and g.get("v6_common") and "corr" in g["v6_common"]:
                print(m, gname, "n", g["n_lined_v6"], "v6", g["verdict_v6_common"]["text"], "| v5", g["verdict_v5_common"]["text"])
    return 0


def render_espn_md(rep: dict[str, Any]) -> str:
    lines = [
        f"# cfb-structural-v6 vs v5 on ESPN archived closing lines (FIRST LOOK), as of {rep['as_of_utc']}",
        "",
        (
            f"Frozen fit sha256 `{rep['frozen_params_sha256']}`. ESPN lines file sha256 `{rep['espn_lines_sha256']}`; "
            f"audit sha256 `{rep['espn_lines_audit_sha256']}`; rule sha256 {rep['espn_rule_sha256_values']}. "
            f"Lined events in file: {rep['lined_events_in_file']}. Skipped: {json.dumps(rep['skipped'])}."
        ),
        "",
        "> " + rep["note"],
        "",
    ]
    for market in ("total", "spread"):
        b = rep["markets"][market]
        lines += [
            f"## {market.capitalize()}s: lined v6-covered events {b['n_lined_v6']}, common with v5 {b['n_common_v5_v6']}",
            "",
            f"Orientation check: {json.dumps(b['orientation_check'])}",
            "",
            "| group | n lined | model | n | corr | 95% CI | slope | side record | MAE model / line | SD model / line / actual | verdict |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for gname, g in b["groups"].items():
            for tag, label in (("v6_all_covered", "v6 all"), ("v6_common", "v6 common"), ("v5_common", "v5 common")):
                s = g.get(tag)
                if not s:
                    continue
                if "corr" not in s:
                    lines.append(f"| {gname} | {g['n_lined_v6']} | {label} | {s['n']} | too few | | | | | | |")
                    continue
                ci = s["corr_ci"]
                lines.append(
                    f"| {gname} | {g['n_lined_v6']} | {label} | {s['n']} | {_fmt(s['corr'])} | "
                    f"[{_fmt(ci['ci_95_low'])}, {_fmt(ci['ci_95_high'])}] | {_fmt(s['ols_slope'])} | "
                    f"{s['side_wins']}-{s['side_losses']} | {_fmt(s['mae_model'], 2)} / {_fmt(s['mae_line'], 2)} | "
                    f"{_fmt(s['sd_model'], 2)} / {_fmt(s['sd_line'], 2)} / {_fmt(s['sd_actual'], 2)} | "
                    f"{g['verdict_' + tag]['text']} |"
                )
        lines.append("")
        for gname in ("window:holdout", "window:fit"):
            g = b["groups"].get(gname)
            if not g:
                continue
            for tag, label in (("v6_all_covered", "v6 all covered"), ("v5_common", "v5 common")):
                s = g.get(tag)
                if not s or "buckets" not in s:
                    continue
                lines += [f"Buckets, {gname}, {label}:", ""]
                if market == "total":
                    lines += [
                        "| line bucket | n | actual over % | model over / under takes | model side win % |",
                        "|---|---|---|---|---|",
                    ]
                    for bk in s["buckets"]:
                        lines.append(
                            f"| {bk['bucket']} | {bk['n']} | {_fmt(bk.get('actual_over_pct'), 1)} | "
                            f"{bk.get('model_over_takes', 'n/a')} / {bk.get('model_under_takes', 'n/a')} | "
                            f"{_fmt(bk.get('model_side_win_pct'), 1)} |"
                        )
                else:
                    lines += ["| line bucket | n | side takes | model side win % |", "|---|---|---|---|"]
                    for bk in s["buckets"]:
                        lines.append(
                            f"| {bk['bucket']} | {bk['n']} | {bk.get('side_takes', 'n/a')} | "
                            f"{_fmt(bk.get('model_side_win_pct'), 1)} |"
                        )
                    big = s["line_21_plus"]
                    lines += [
                        "",
                        (
                            f"Lines of 21+: n {big['n']}, mean favourite margin model "
                            f"{_fmt(big['mean_model_favourite_margin'], 2)} vs line "
                            f"{_fmt(big['mean_line_favourite_margin'], 2)} vs actual "
                            f"{_fmt(big['mean_actual_favourite_margin'], 2)}; dog takes {big['dog_takes']}."
                        ),
                    ]
                lines.append("")
        sens = b["sensitivity_ex_opening_consensus_holdout"]
        ss = sens["v6_all_covered"]
        if ss and "corr" in ss:
            lines.append(
                f"Sensitivity (holdout window, line excluding Opening and consensus providers), v6: n {ss['n']}, "
                f"corr {_fmt(ss['corr'])} [{_fmt(ss['corr_ci']['ci_95_low'])}, {_fmt(ss['corr_ci']['ci_95_high'])}]."
            )
            lines.append("")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ lines-audit (thin wrapper)
def cmd_lines_audit(args: argparse.Namespace) -> int:
    script = REPO / "scripts" / "cfb_v6_lines_audit.py"
    if not script.exists():
        print("scripts/cfb_v6_lines_audit.py is missing; the line inventory is implemented there", file=sys.stderr)
        return 3
    return subprocess.call([sys.executable, str(script), *args.rest])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("reproduce-v5")
    p6 = sub.add_parser("v6")
    p6.add_argument("--check-only", action="store_true")
    sub.add_parser("v6-espn")
    pl = sub.add_parser("lines-audit")
    pl.add_argument("rest", nargs=argparse.REMAINDER)
    args = ap.parse_args()
    if args.cmd == "reproduce-v5":
        return cmd_reproduce_v5(args)
    if args.cmd == "lines-audit":
        return cmd_lines_audit(args)
    if args.cmd == "v6-espn":
        return run_v6_espn(args)
    return run_v6_check(args)


if __name__ == "__main__":
    raise SystemExit(main())
