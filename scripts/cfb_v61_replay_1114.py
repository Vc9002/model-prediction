"""RETROSPECTIVE REPLAY of v5, v6, v6.1 and the closing line on every settled 2026-11-14 NCAAF game.

quick-261115-183, decision D-183-REPLAY. 2026-11-14 is inside the data the lopsided / FBS-vs-FCS tilt was
found on, so nothing here is a test of the tilt (its real test is the pre-registered forward evaluation in
``cfb_lopsided_prereg.py``). Nothing is tuned here: the v6.1 spec is frozen (``cfb_v61_spec.json``) and this
script asserts its hash, the rule hash and the frozen v6 params hash before it reads a game.

What it does
  1. Lists every 2026-11-14 game (event start in [2026-11-14T10:00Z, 2026-11-15T10:00Z), ESPN scoreboards
     groups 80 and 81, FBS and FCS) and counts completed, forfeit and unfinished games honestly.
  2. Fetches the ESPN core odds of each completed game into the NEW file
     ``data/research/ncaaf_v6/espn_lines_20261114.jsonl`` with the espn-lines-rule-1 functions imported from
     ``cfb_v6_espn_lines.py`` (never edits ``espn_lines.jsonl``). Read-only GET, generic User-Agent.
  3. Projects every game with frozen v6 at its own kickoff (history = the frozen snapshot topped up from the
     scoreboards, the extractor's 5 h result buffer) and v6.1 = v6 total + the walk-forward tracker offset
     (window from the spec, history = frozen v6 research rows before 11-14 plus fresh v6 projections of the 11-14
     games themselves, each counted only when start + 5 h <= kickoff).
  4. Cohort A = every completed 11-14 game. Cohort B = events with a settled flat-tier cfb-structural-v5 total
     row in the runtime ledger (latest row per event), priced with the ledger's ESPN-consensus price on the
     ledger side and 1.0476 minus it on the other side (a standard 4.76% vig). v6.1 probabilities come from a
     script-local subclass of CFBStructuralV6Model overriding only ``calibrate_means``; a fresh offset-0 instance
     must equal a fresh v6 instance exactly on every row (the seam check).

Run: OPENBLAS_NUM_THREADS=1 .venv/Scripts/python scripts/cfb_v61_replay_1114.py
Percentages and points only; no odds format other than probabilities.
"""

# ruff: noqa: ISC004
from __future__ import annotations

import json
import math
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))
import cfb_total_level_tracker as T
import cfb_totals_lib as L
import cfb_v6_espn_lines as E

from model_prediction.domain import parse_utc
from model_prediction.features.cfb_features_v6 import V6Game, V6NoCall, load_snapshot, season_of
from model_prediction.models.cfb_structural_v6 import (
    MIN_CALIBRATED_TOTAL,
    CFBStructuralV6Model,
    cfb_structural_v6_model,
    load_cfb_structural_v6_artifact,
)

REPO = L.REPO
OUT = REPO / "outputs" / "ncaaf"
OUT_JSON = OUT / "cfb_v61_replay_1114.json"
OUT_MD = OUT / "cfb_v61_replay_1114.md"
DATA = REPO / "data" / "research" / "ncaaf_v6"
SNAPSHOT = DATA / "history_snapshot.jsonl"
LINES_1114 = DATA / "espn_lines_20261114.jsonl"
LEDGER_DB = r"file:E:\model-prediction-runtime\ledgers\ledgers.db?mode=ro"
BACKFILL_SHA = (
    "cc704059e4684edb9e580dff5033433424ee34e6a5fe96995c7c272ae82d8bf4"  # as v6_slate.py / v6_pnl.py
)
WIN_START = "2026-11-14T10:00Z"
WIN_END = "2026-11-15T10:00Z"
SCOREBOARD_DATES = ("20261114", "20261115")
SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"
    "?dates={d}&groups={g}&limit=500"
)
OTHER_SIDE_SUM = 1.0476  # other side priced so both sides sum to a standard 4.76% vig
LOPSIDED_POINTS = 28.0  # D-183-PREREG tilt definition
EDGE_LO, EDGE_HI = 0.02, 0.08  # v5 Main gate analogue
LABEL = "RETROSPECTIVE REPLAY"
BOOT_B = 1000
BOOT_SEED = 20261115


# --------------------------------------------------------------------------- ESPN scoreboards
def _get_json(client: httpx.Client, url: str) -> dict[str, Any]:
    last: Exception | None = None
    for attempt in range(5):
        try:
            r = client.get(url)
            if r.status_code == 200:
                return r.json()
            last = RuntimeError(f"HTTP {r.status_code}")
        except (httpx.HTTPError, ValueError) as exc:
            last = exc
        time.sleep(2.0 * (attempt + 1))
    raise SystemExit(f"scoreboard fetch failed for {url}: {last}")


def fetch_slate() -> tuple[dict[str, dict[str, Any]], dict[str, set[int]]]:
    """event_id -> scoreboard event, and event_id -> set of feeds (80 / 81) that listed it."""
    events: dict[str, dict[str, Any]] = {}
    feeds: dict[str, set[int]] = {}
    with httpx.Client(headers={"User-Agent": "Mozilla/5.0"}, timeout=30.0) as client:
        for d in SCOREBOARD_DATES:
            for g in (80, 81):
                payload = _get_json(client, SCOREBOARD_URL.format(d=d, g=g))
                for e in payload.get("events", []):
                    eid = str(e["id"])
                    events.setdefault(eid, e)
                    feeds.setdefault(eid, set()).add(g)
                time.sleep(0.3)
    return events, feeds


def in_window(e: dict[str, Any]) -> bool:
    return WIN_START <= str(e["date"]) < WIN_END


def parse_event(e: dict[str, Any]) -> dict[str, Any]:
    c = e["competitions"][0]
    h = next(x for x in c["competitors"] if x["homeAway"] == "home")
    a = next(x for x in c["competitors"] if x["homeAway"] == "away")
    st = e["status"]["type"]
    completed = bool(st.get("completed"))
    return {
        "event_id": str(e["id"]),
        "start": str(e["date"]),
        "completed": completed,
        "forfeit": str(st.get("name")) == "STATUS_FORFEIT",
        "home": h["team"]["displayName"],
        "away": a["team"]["displayName"],
        "home_id": str(h["team"]["id"]),
        "away_id": str(a["team"]["id"]),
        "neutral": bool(c.get("neutralSite")),
        "home_score": int(h["score"]) if completed and h.get("score") not in (None, "") else None,
        "away_score": int(a["score"]) if completed and a.get("score") not in (None, "") else None,
    }


# --------------------------------------------------------------------------- 11-14 closing lines
def fetch_lines(game_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Fetch core odds into the NEW 11-14 file (resumable; a failed fetch is retried next run)."""
    existing = E.read_lines(LINES_1114)
    E.assert_records_match_rule(existing)
    todo = [
        eid
        for eid in game_ids
        if eid not in existing or existing[eid].get("no_line_reason") == "fetch_failed"
    ]
    if todo:
        fetcher = E.Fetcher(E.DEFAULT_INTERVAL_S)
        E._ensure_trailing_newline(LINES_1114)
        LINES_1114.parent.mkdir(parents=True, exist_ok=True)
        with LINES_1114.open("a", encoding="utf-8") as out:
            for eid in todo:
                rec = fetcher.fetch_event({"event_id": eid, "season_year": 2026})
                out.write(json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n")
                out.flush()
    return E.read_lines(LINES_1114)


# --------------------------------------------------------------------------- the v6.1 seam
class V61Replay(CFBStructuralV6Model):
    """Script-local v6.1: overrides only ``calibrate_means`` (calibrated total + the tracker offset)."""

    offset: float = 0.0

    def calibrate_means(self, projected_margin_home: float, projected_total: float) -> tuple[float, float]:
        margin, _ = super().calibrate_means(projected_margin_home, projected_total)
        total = (
            self.total_calibration["intercept"]
            + self.total_calibration["slope"] * projected_total
            + self.offset
        )
        return margin, max(MIN_CALIBRATED_TOTAL, total)


def fresh_v61() -> V61Replay:
    return V61Replay.from_artifact(load_cfb_structural_v6_artifact())


# --------------------------------------------------------------------------- statistics
def pct(x: float | None, nd: int = 1) -> str:
    return "n/a" if x is None or math.isnan(x) else f"{100.0 * x:.{nd}f}%"


def num(x: float | None, nd: int = 2) -> str:
    return "n/a" if x is None or math.isnan(x) else f"{x:.{nd}f}"


def model_block(model: np.ndarray, line: np.ndarray | None, actual: np.ndarray) -> dict[str, Any]:
    err = model - actual
    out: dict[str, Any] = {
        "n": len(actual),
        "mae": float(np.abs(err).mean()) if len(actual) else None,
        "bias": float(err.mean()) if len(actual) else None,
        "over_lean": None,
        "over_lean_n_decided": 0,
    }
    if line is not None and len(actual):
        gt, lt = int((model > line).sum()), int((model < line).sum())
        out["over_lean_n_decided"] = gt + lt
        out["over_lean"] = gt / (gt + lt) if gt + lt else None
    return out


def side_record(model: np.ndarray, line: np.ndarray, actual: np.ndarray) -> dict[str, int]:
    """Model side vs the closing total (over when model > line), unpriced win-loss-push."""
    w = lo = p = 0
    for m, ln, a in zip(model, line, actual, strict=True):
        if m == ln:
            continue
        if a == ln:
            p += 1
        elif (a > ln) == (m > ln):
            w += 1
        else:
            lo += 1
    return {"w": w, "l": lo, "p": p}


def paired_game_boot(a: np.ndarray, b: np.ndarray, actual: np.ndarray) -> dict[str, float] | None:
    """MAE(a) - MAE(b), game-level percentile bootstrap (all rows share one date: no date clustering)."""
    n = len(actual)
    if n < 10:
        return None
    d = np.abs(a - actual) - np.abs(b - actual)
    rng = np.random.default_rng(BOOT_SEED)
    idx = rng.integers(0, n, size=(BOOT_B, n))
    boots = d[idx].mean(axis=1)
    return {
        "point": float(d.mean()),
        "lo": float(np.quantile(boots, 0.025)),
        "hi": float(np.quantile(boots, 0.975)),
    }


def comparison(rows: list[dict[str, Any]], with_v5: bool) -> dict[str, Any]:
    """Models on the common lined rows (v6, v6.1, line and optionally v5), plus v6/v6.1 on every projected row."""
    proj = [r for r in rows if r["v6"] is not None]
    out: dict[str, Any] = {"n_rows": len(rows), "n_projected": len(proj), "n_no_call": len(rows) - len(proj)}
    act = np.array([r["actual"] for r in proj], float)
    out["projected_all"] = {
        "v6": model_block(np.array([r["v6_served"] for r in proj], float), None, act),
        "v6_1": model_block(np.array([r["v61_served"] for r in proj], float), None, act),
    }
    lined = [r for r in proj if r["line"] is not None]
    common = [r for r in lined if (r["v5"] is not None) or not with_v5]
    out["lined"] = {"n": len(lined)}
    for name, sub in (("lined_common", common),):
        a = np.array([r["actual"] for r in sub], float)
        ln = np.array([r["line"] for r in sub], float)
        blocks = {
            "v6": model_block(np.array([r["v6_served"] for r in sub], float), ln, a),
            "v6_1": model_block(np.array([r["v61_served"] for r in sub], float), ln, a),
            "closing_line": model_block(ln, None, a),
        }
        if with_v5:
            blocks["v5"] = model_block(np.array([r["v5"] for r in sub], float), ln, a)
        recs = {
            "v6": side_record(np.array([r["v6_served"] for r in sub], float), ln, a),
            "v6_1": side_record(np.array([r["v61_served"] for r in sub], float), ln, a),
        }
        if with_v5:
            recs["v5"] = side_record(np.array([r["v5"] for r in sub], float), ln, a)
        v6v = np.array([r["v6_served"] for r in sub], float)
        v61v = np.array([r["v61_served"] for r in sub], float)
        out[name] = {
            "n": len(sub),
            "models": blocks,
            "side_vs_close_unpriced": recs,
            "paired_mae_diff_game_level": {
                "v6_1_minus_v6": paired_game_boot(v61v, v6v, a),
                "v6_1_minus_line": paired_game_boot(v61v, ln, a),
                "v6_minus_line": paired_game_boot(v6v, ln, a),
            },
        }
    return out


def pnl_block(picks: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(picks)
    if not n:
        return {"n": 0}
    w = sum(1 for x in picks if x["won"] and not x["push"])
    lo = sum(1 for x in picks if (not x["won"]) and not x["push"])
    p = sum(1 for x in picks if x["push"])
    pnl = sum(x["pnl"] for x in picks)
    return {"n": n, "w": w, "l": lo, "p": p, "pnl_units": pnl, "roi_pct": 100.0 * pnl / n}


# --------------------------------------------------------------------------- main
def main() -> int:
    hdr = L.startup(__file__, None)
    spec = T._load_spec()
    if spec["components"]:
        raise SystemExit("spec carries components: this replay implements v6.1 = v6 + tracker only")
    window = (spec["tracker"]["window"]["kind"], int(spec["tracker"]["window"]["size"]))
    run_started = L.utc_now_iso()

    # ---- 1. the slate
    events, feeds = fetch_slate()
    slate = {eid: parse_event(e) for eid, e in events.items() if in_window(e)}
    counts = {
        "events_in_window": len(slate),
        "completed": sum(1 for g in slate.values() if g["completed"]),
        "completed_forfeit": sum(1 for g in slate.values() if g["completed"] and g["forfeit"]),
        "not_completed": sum(1 for g in slate.values() if not g["completed"]),
    }
    unfinished = sorted(
        (g["start"], g["event_id"], f"{g['away']} @ {g['home']}")
        for g in slate.values()
        if not g["completed"]
    )
    cohort_a_games = sorted(
        (g for g in slate.values() if g["completed"] and not g["forfeit"] and g["home_score"] is not None),
        key=lambda g: (g["start"], g["event_id"]),
    )
    counts["cohort_a_games"] = len(cohort_a_games)

    # ---- 2. closing lines into the new file
    lines = fetch_lines([g["event_id"] for g in cohort_a_games])
    L_have = {
        g["event_id"]
        for g in cohort_a_games
        if (lines.get(g["event_id"]) or {}).get("no_line_reason") is None
    }
    counts["with_espn_total_line"] = sum(
        1
        for g in cohort_a_games
        if ((lines.get(g["event_id"]) or {}).get("selected") or {}).get("total_line") is not None
    )
    counts["with_espn_spread_line"] = sum(
        1
        for g in cohort_a_games
        if ((lines.get(g["event_id"]) or {}).get("selected") or {}).get("home_spread_line") is not None
    )
    counts["fetch_failed"] = sum(
        1 for g in cohort_a_games if (lines.get(g["event_id"]) or {}).get("no_line_reason") == "fetch_failed"
    )
    del L_have

    # ---- 3. history (frozen snapshot topped up from the scoreboards, as v6_slate.py)
    hist = load_snapshot(SNAPSHOT)
    have = {g.event_id for g in hist}
    fbs_ids = {
        i for g in hist if g.season == 2026 and g.game_class == "FBS-FBS" for i in (g.home_id, g.away_id) if i
    }
    for eid, fs in feeds.items():
        if fs == {80} and eid in slate:
            fbs_ids.update((slate[eid]["home_id"], slate[eid]["away_id"]))
    topped = 0
    for eid, e in events.items():
        g = parse_event(e)
        if not g["completed"] or g["forfeit"] or eid in have or g["home_score"] is None:
            continue
        s = parse_utc(g["start"])
        cls = ["FCS-FCS", "FBS-FCS", "FBS-FBS"][(g["home_id"] in fbs_ids) + (g["away_id"] in fbs_ids)]
        hist.append(
            V6Game(
                eid,
                s,
                season_of(s),
                g["home"],
                g["away"],
                g["home_score"],
                g["away_score"],
                g["neutral"],
                None,
                "topup",
                g["home_id"],
                g["away_id"],
                cls,
                False,
            )
        )
        have.add(eid)
        topped += 1
    hist.sort(key=lambda g: (g.start, g.event_id))
    counts["history_games"] = len(hist)
    counts["history_topped_up_from_scoreboards"] = topped

    # ---- 4. v6 projections at each game's own kickoff (kickoff order; the extractor only advances)
    model = cfb_structural_v6_model()
    ext = model.extractor_for(hist, backfill_sha256=BACKFILL_SHA)
    tc = model.total_calibration
    feats: dict[str, Any] = {}
    rows: dict[str, dict[str, Any]] = {}
    for g in cohort_a_games:
        sel = (lines.get(g["event_id"]) or {}).get("selected") or {}
        total_line, spread = sel.get("total_line"), sel.get("home_spread_line")
        fbs_h, fbs_a = g["home_id"] in fbs_ids, g["away_id"] in fbs_ids
        vs_fcs = fbs_h != fbs_a
        lopsided = vs_fcs or (spread is not None and abs(float(spread)) >= LOPSIDED_POINTS)
        row = {
            "event_id": g["event_id"],
            "start": g["start"],
            "name": f"{g['away']} @ {g['home']}",
            "actual": float(g["home_score"] + g["away_score"]),
            "fbs_vs_fcs": vs_fcs,
            "lopsided": lopsided,
            "fbs_involved": fbs_h or fbs_a,
            "line": None if total_line is None else float(total_line),
            "spread_home": None if spread is None else float(spread),
            "v6": None,
            "v6_served": None,
            "v61": None,
            "v61_served": None,
            "v5": None,
            "no_call": None,
        }
        try:
            feat = ext.extract_features(
                event_id=g["event_id"],
                game_start_utc=g["start"],
                home_team=g["home"],
                away_team=g["away"],
                is_neutral_site=g["neutral"],
            )
        except V6NoCall as exc:
            row["no_call"] = f"{type(exc).__name__}: {exc}"[:120]
        else:
            feats[g["event_id"]] = feat
            row["raw_total"] = float(feat.projected_total)
            row["v6"] = tc["intercept"] + tc["slope"] * float(
                feat.projected_total
            )  # research convention, no floor
            row["v6_served"] = float(
                model.calibrate_means(feat.projected_margin_home, feat.projected_total)[1]
            )
            row["sample_games"] = int(feat.sample_games)
        rows[g["event_id"]] = row

    # ---- 5. tracker offset: frozen research feed before 11-14 + fresh 11-14 projections
    base = L.load_base()
    base_prev = base[base["date"] < "2026-11-14"]
    f_t = list(base_prev["t_s"].astype(np.int64))
    f_r = list((base_prev["actual"] - base_prev["v6"]).astype(float))
    f_id = list(base_prev["event_id"])
    fresh = [r for r in rows.values() if r["v6"] is not None]
    parity: list[float] = []
    base_idx = base.set_index("event_id")
    for r in fresh:
        if r["event_id"] in base_idx.index:
            parity.append(abs(float(base_idx.loc[r["event_id"], "v6"]) - r["v6"]))
    for r in fresh:
        f_t.append(int(parse_utc(r["start"]).timestamp()))
        f_r.append(r["actual"] - r["v6"])
        f_id.append(r["event_id"])
    order = sorted(range(len(f_t)), key=lambda i: (f_t[i], f_id[i]))
    f_t_s = np.array([f_t[i] for i in order], np.int64)
    f_r_s = np.array([f_r[i] for i in order], float)
    dec_t = np.array([int(parse_utc(r["start"]).timestamp()) for r in fresh], np.int64)
    off, n_used = L.tracker_offsets(f_t_s, f_r_s, dec_t, window)
    for r, o, n in zip(fresh, off, n_used, strict=True):
        r["offset"] = float(o)
        r["offset_n"] = int(n)
        r["v61"] = r["v6"] + float(o)
        r["v61_served"] = max(MIN_CALIBRATED_TOTAL, tc["intercept"] + tc["slope"] * r["raw_total"] + float(o))
    counts["fresh_vs_frozen_research_rows_overlap"] = len(parity)
    counts["fresh_vs_frozen_research_max_abs_diff"] = max(parity) if parity else None
    offs = np.array([r["offset"] for r in fresh], float)

    # ---- 6. v5 projections (latest pre-kickoff ledger row per event) and cohort B
    v5art = json.loads(L.V5_ARTIFACT.read_text(encoding="utf-8"))
    v5tc = v5art["total_calibration"]
    db = sqlite3.connect(LEDGER_DB, uri=True)
    db.row_factory = sqlite3.Row
    v5_rows = db.execute(
        """select event_id, created_at_utc, event_start_utc, feature_payload_json from ledger_records
           where sport='ncaaf' and model_id='cfb-structural-v5'
           and event_start_utc>=? and event_start_utc<? order by created_at_utc""",
        (WIN_START[:13], WIN_END[:13]),
    ).fetchall()
    latest_pre: dict[str, float] = {}
    for rr in v5_rows:
        if parse_utc(rr["created_at_utc"]) >= parse_utc(rr["event_start_utc"]):
            continue
        try:
            pt = float(json.loads(rr["feature_payload_json"])["features"]["projected_total"])
        except (KeyError, TypeError, ValueError):
            continue
        latest_pre[str(rr["event_id"])] = pt  # ascending created_at: last pre-kickoff row wins
    for eid, pt in latest_pre.items():
        if eid in rows:
            rows[eid]["v5"] = max(24.0, v5tc["intercept"] + v5tc["slope"] * pt)
    counts["cohort_a_with_v5_projection"] = sum(1 for r in rows.values() if r["v5"] is not None)

    cb = db.execute(
        """select event_id, selection, line, market_probability mp, model_probability p5, result, created_at_utc
           from ledger_records where sport='ncaaf' and model_id='cfb-structural-v5' and market_type='total'
           and status='settled' and result in ('win','loss','push') and ledger_tier='flat'
           and event_start_utc>=? and event_start_utc<? order by created_at_utc""",
        (WIN_START[:13], WIN_END[:13]),
    ).fetchall()
    best: dict[str, sqlite3.Row] = {}
    for rr in cb:
        best[str(rr["event_id"])] = rr
    b_events = sorted(best, key=lambda e: (rows[e]["start"], e) if e in rows else ("9", e))
    left_out_b = [e for e in b_events if e not in rows or rows[e]["v6"] is None]
    b_use = [e for e in b_events if e in rows and rows[e]["v6"] is not None]
    counts["cohort_b_settled_v5_flat_total_events"] = len(b_events)
    counts["cohort_b_priced"] = len(b_use)
    counts["cohort_b_left_out"] = [
        (e, "not a settled cohort-A game" if e not in rows else rows[e]["no_call"]) for e in left_out_b
    ]

    # fresh instances: v6, v6.1 (real offsets), v6.1 with offset 0 (seam), each over the same rows in order
    m_v6, m_v61, m_seam = cfb_structural_v6_model(), fresh_v61(), fresh_v61()
    picks: dict[str, list[dict[str, Any]]] = {"v5": [], "v6": [], "v6_1": []}
    seam_rows = 0
    seam_bad: list[str] = []

    def probs(m: CFBStructuralV6Model, feat: Any, line: float) -> tuple[float, float, float]:
        margin, _ = m.calibrate_means(feat.projected_margin_home, feat.projected_total)
        spread_line = round(-margin * 2.0) / 2.0  # as predict_matchup does when no spread is supplied
        fc = m.forecast_game(feat, spread_line, line)
        return fc.prob_over, fc.prob_under, fc.prob_push_total

    for eid in b_use:
        rr, row, feat = best[eid], rows[eid], feats[eid]
        ln = float(rr["line"])
        sel = rr["selection"]
        ask = {
            sel: float(rr["mp"]),
            ("under" if sel == "over" else "over"): max(0.02, OTHER_SIDE_SUM - float(rr["mp"])),
        }
        m_v61.offset = row["offset"]
        m_seam.offset = 0.0
        p6, p61, ps = probs(m_v6, feat, ln), probs(m_v61, feat, ln), probs(m_seam, feat, ln)
        seam_rows += 1
        if ps != p6:
            seam_bad.append(eid)
        t = row["actual"]
        push = t == ln
        for name, p in (("v6", p6), ("v6_1", p61)):
            pr = {"over": p[0], "under": p[1]}
            pick = max(("over", "under"), key=lambda s: pr[s] - ask[s])
            edge = pr[pick] - ask[pick]
            won = (t > ln) if pick == "over" else (t < ln)
            pnl = 0.0 if push else ((1 - ask[pick]) / ask[pick] if won else -1.0)
            picks[name].append(
                {
                    "eid": eid,
                    "pick": pick,
                    "edge": edge,
                    "won": won,
                    "push": push,
                    "pnl": pnl,
                    "lopsided": row["lopsided"],
                    "same_as_ledger": pick == sel,
                }
            )
        r5 = rr["result"]
        a5 = float(rr["mp"])
        picks["v5"].append(
            {
                "eid": eid,
                "pick": sel,
                "edge": float(rr["p5"]) - a5,
                "won": r5 == "win",
                "push": r5 == "push",
                "pnl": 0.0 if r5 == "push" else ((1 - a5) / a5 if r5 == "win" else -1.0),
                "lopsided": row["lopsided"],
                "same_as_ledger": True,
            }
        )

    # seam over every cohort-A projected game as well (no ledger line: the v6 own calibrated total is the line)
    m_v6s, m_seam2 = cfb_structural_v6_model(), fresh_v61()
    seam_a = 0
    for g in cohort_a_games:
        feat = feats.get(g["event_id"])
        if feat is None:
            continue
        m_seam2.offset = 0.0
        ln = float(m_v6s.calibrate_means(feat.projected_margin_home, feat.projected_total)[1])
        if probs(m_v6s, feat, ln) != probs(m_seam2, feat, ln):
            seam_bad.append("A:" + g["event_id"])
        seam_a += 1
    seam = {
        "rule": "fresh v6.1 instance with offset 0 vs fresh v6 instance, same rows in the same order, exact equality",
        "cohort_b_rows_checked": seam_rows,
        "cohort_a_rows_checked": seam_a,
        "mismatches": seam_bad,
        "exact_equal": not seam_bad,
    }
    if seam_bad:
        raise SystemExit(f"seam check FAILED on {seam_bad[:5]}")

    # ---- 7. tables
    all_rows = list(rows.values())
    cohorts_a = {
        "all": all_rows,
        "lopsided_or_fbs_vs_fcs": [r for r in all_rows if r["lopsided"]],
        "other": [r for r in all_rows if not r["lopsided"]],
        "fbs_involved": [r for r in all_rows if r["fbs_involved"]],
    }
    b_rows = [rows[e] for e in b_use]
    cohorts_b = {
        "all": b_rows,
        "lopsided_or_fbs_vs_fcs": [r for r in b_rows if r["lopsided"]],
        "other": [r for r in b_rows if not r["lopsided"]],
    }
    cmp_a = {k: comparison(v, with_v5=False) for k, v in cohorts_a.items()}
    cmp_a_v5 = {
        k: comparison([r for r in v if r["v5"] is not None], with_v5=True) for k, v in cohorts_a.items()
    }
    cmp_b = {k: comparison(v, with_v5=True) for k, v in cohorts_b.items()}

    def pnl_views(pk: dict[str, list[dict[str, Any]]], subset: str) -> dict[str, Any]:
        def sel(lst: list[dict[str, Any]]) -> list[dict[str, Any]]:
            if subset == "lopsided_or_fbs_vs_fcs":
                return [x for x in lst if x["lopsided"]]
            if subset == "other":
                return [x for x in lst if not x["lopsided"]]
            return lst

        res: dict[str, Any] = {}
        for name, lst in pk.items():
            lst = sel(lst)
            res[name] = {
                "flat_1u_all": pnl_block(lst),
                "edge_2_8pct": pnl_block([x for x in lst if EDGE_LO <= x["edge"] <= EDGE_HI]),
            }
        return res

    pnl = {s: pnl_views(picks, s) for s in ("all", "lopsided_or_fbs_vs_fcs", "other")}
    v6_vs_v61_flips = sum(
        1 for a, b in zip(picks["v6"], picks["v6_1"], strict=True) if a["pick"] != b["pick"]
    )

    out: dict[str, Any] = dict(hdr)
    out.update(
        {
            "label": LABEL,
            "holdout_reads": 0,
            "holdout_note": "the replay reads no research holdout; 2026-11-14 is inside the tilt's discovery data",
            "quick_task": "261115-183",
            "decision": "D-183-REPLAY",
            "run_started_at_utc": run_started,
            "spec_sha256": spec["spec_sha256"],
            "composition": spec["composition"],
            "tracker_window": spec["tracker"]["window"],
            "window": {"start_utc_inclusive": WIN_START, "end_utc_exclusive": WIN_END},
            "counts": counts,
            "unfinished_games": unfinished,
            "forfeits_left_out": [
                (g["event_id"], f"{g['away']} @ {g['home']}")
                for g in slate.values()
                if g["completed"] and g["forfeit"]
            ],
            "no_calls": [(r["event_id"], r["name"], r["no_call"]) for r in all_rows if r["no_call"]],
            "lines_file": str(LINES_1114.relative_to(REPO)).replace("\\", "/"),
            "lines_file_sha256": L.file_sha256(LINES_1114),
            "tracker_offsets_cohort_a": {
                "min": float(offs.min()) if len(offs) else None,
                "max": float(offs.max()) if len(offs) else None,
                "mean": float(offs.mean()) if len(offs) else None,
                "n_used_min": int(n_used.min()) if len(n_used) else None,
                "n_used_max": int(n_used.max()) if len(n_used) else None,
            },
            "seam_check": seam,
            "cohort_a_comparison": cmp_a,
            "cohort_a_comparison_with_v5_rows": cmp_a_v5,
            "cohort_b_comparison": cmp_b,
            "cohort_b_pnl": pnl,
            "cohort_b_v6_vs_v6_1_pick_flips": v6_vs_v61_flips,
            "price_rule": "ledger market_probability on the ledger side, 1.0476 minus it on the other side (4.76% vig); "
            "pick = larger model probability minus ask; flat 1U; v5 = the ledger row's own result at 1U",
            "edge_view": f"model probability minus ask in [{EDGE_LO:.0%}, {EDGE_HI:.0%}] (v5 Main gate analogue)",
            "rows": all_rows,
        }
    )
    OUT_JSON.write_text(json.dumps(out, indent=1, default=float) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(out), encoding="utf-8")
    print(f"wrote {OUT_JSON} and {OUT_MD}")
    print(json.dumps(counts, default=str)[:900])
    return 0


# --------------------------------------------------------------------------- markdown
def _row(name: str, b: dict[str, Any]) -> str:
    return f"| {name} | {b['n']} | {num(b['mae'])} | {num(b['bias'])} | {pct(b['over_lean'])} |"


def _cmp_md(title: str, c: dict[str, Any]) -> list[str]:
    lines = [f"#### {title}", ""]
    lines.append(
        f"rows {c['n_rows']}, v6 projected {c['n_projected']}, no-call {c['n_no_call']}, "
        f"projected and lined {c['lined']['n']}, on the common rows {c['lined_common']['n']}"
    )
    lines.append("")
    pa = c["projected_all"]
    lines += [
        "| model, every projected game (points) | n | MAE | bias | over lean |",
        "|---|---|---|---|---|",
    ]
    lines.append(_row("v6", pa["v6"]))
    lines.append(_row("v6.1", pa["v6_1"]))
    lines.append("")
    lc = c["lined_common"]
    if lc["n"]:
        lines += [
            "| model, common lined rows (points) | n | MAE | bias | over lean |",
            "|---|---|---|---|---|",
        ]
        for k in ("v5", "v6", "v6_1", "closing_line"):
            if k in lc["models"]:
                lines.append(
                    _row({"v6_1": "v6.1", "closing_line": "closing line"}.get(k, k), lc["models"][k])
                )
        lines.append("")
        rec = lc["side_vs_close_unpriced"]
        lines.append(
            "side vs the closing total, unpriced W-L-P: "
            + "; ".join(f"{k.replace('_', '.')} {v['w']}-{v['l']}-{v['p']}" for k, v in rec.items())
        )
        d = lc["paired_mae_diff_game_level"]
        txt = []
        for k, lab in (
            ("v6_1_minus_v6", "v6.1 - v6"),
            ("v6_1_minus_line", "v6.1 - line"),
            ("v6_minus_line", "v6 - line"),
        ):
            if d[k]:
                txt.append(f"{lab} {d[k]['point']:+.2f} [{d[k]['lo']:+.2f}, {d[k]['hi']:+.2f}]")
        if txt:
            lines.append("")
            lines.append(
                "paired MAE difference, game-level bootstrap (one date, no clustering; negative = first is better): "
                + "; ".join(txt)
            )
        lines.append("")
    return lines


def _pnl_line(name: str, b: dict[str, Any]) -> str:
    if not b.get("n"):
        return f"| {name} | 0 | n/a | n/a | n/a |"
    return (
        f"| {name} | {b['n']} | {b['w']}-{b['l']}-{b['p']} | {b['pnl_units']:+.2f} U | {b['roi_pct']:+.1f}% |"
    )


def render_md(o: dict[str, Any]) -> str:
    c = o["counts"]
    s = o["seam_check"]
    md = [
        f"# {LABEL}: NCAAF totals on every settled 2026-11-14 game (quick-261115-183)",
        "",
        f"**{LABEL}.** 2026-11-14 is inside the data the lopsided / FBS-vs-FCS tilt was found on, so none of this "
        "tests the tilt. The tilt's real test is the pre-registered forward evaluation. v6.1 here is the frozen "
        f"spec (spec_sha256 `{o['spec_sha256']}`; {o['composition']}; tracker window {o['tracker_window']['kind']} "
        f"{o['tracker_window']['size']}). It is not routed and no decision follows from this table.",
        "",
        f"- rule_sha256 `{o['rule_sha256']}`; script sha256 `{o['script_sha256']}`; generated {o['generated_at_utc']}",
        f"- window: event start in [{o['window']['start_utc_inclusive']}, {o['window']['end_utc_exclusive']}), "
        "ESPN scoreboards groups 80 and 81, FBS and FCS",
        "",
        "## What was settled at run time",
        "",
        f"- games listed in the window: {c['events_in_window']}; completed: {c['completed']}; "
        f"unfinished at run time: {c['not_completed']}; completed forfeits left out: {c['completed_forfeit']}",
        f"- cohort A (every completed non-forfeit game): {c['cohort_a_games']}; with an ESPN closing total "
        f"{c['with_espn_total_line']}; with an ESPN closing spread {c['with_espn_spread_line']}; "
        f"line fetches that failed {c['fetch_failed']}",
        f"- v6 no-calls (unrated team, counted, not scored): {len(o['no_calls'])}",
        f"- cohort A rows that also have a v5 ledger projection: {c['cohort_a_with_v5_projection']}",
        f"- cohort B (settled flat-tier cfb-structural-v5 total rows, one per event): "
        f"{c['cohort_b_settled_v5_flat_total_events']}; priced {c['cohort_b_priced']}; left out "
        f"{len(c['cohort_b_left_out'])}",
    ]
    if o["unfinished_games"]:
        md.append(
            "- unfinished games (not scored): " + "; ".join(f"{a[1]} {a[2]}" for a in o["unfinished_games"])
        )
    md += [
        f"- closing lines: ESPN core odds fetched into the new file `{o['lines_file']}` "
        f"(sha256 `{o['lines_file_sha256']}`), espn-lines-rule-1, `espn_lines.jsonl` untouched",
        f"- history: {c['history_games']} games (frozen snapshot plus {c['history_topped_up_from_scoreboards']} "
        "topped up from the scoreboards), 5 h result buffer, decision time = kickoff",
        f"- fresh v6 projections vs the frozen research rows for the same games: {c['fresh_vs_frozen_research_rows_overlap']} "
        f"overlapping, max abs difference {c['fresh_vs_frozen_research_max_abs_diff']}",
        f"- tracker offsets applied to cohort A: mean {num(o['tracker_offsets_cohort_a']['mean'])} points, range "
        f"[{num(o['tracker_offsets_cohort_a']['min'])}, {num(o['tracker_offsets_cohort_a']['max'])}], games averaged "
        f"{o['tracker_offsets_cohort_a']['n_used_min']} to {o['tracker_offsets_cohort_a']['n_used_max']}",
        "",
        "## Seam check",
        "",
        f"A fresh v6.1 instance with offset 0 and a fresh v6 instance give exactly equal over/under/push "
        f"probabilities on every row in the same order: cohort B rows {s['cohort_b_rows_checked']}, cohort A rows "
        f"{s['cohort_a_rows_checked']}, mismatches {len(s['mismatches'])}. Exact equal: {s['exact_equal']}.",
        "",
        "Conventions: bias = mean(model - actual) in points; over lean = share of games where the model total is above "
        "the closing total, ties excluded; MAE in points. Percentages and units only.",
        "",
        "## Cohort A: every completed 2026-11-14 game",
        "",
    ]
    for k, lab in (
        ("all", "all games"),
        ("lopsided_or_fbs_vs_fcs", "lopsided (closing home spread 28 points or more) or FBS-vs-FCS"),
        ("other", "all other games"),
        ("fbs_involved", "FBS-involved games"),
    ):
        md += _cmp_md(f"A / {lab}", o["cohort_a_comparison"][k])
    md += ["### Cohort A, only games that also carry a v5 ledger projection (v5 on the same rows)", ""]
    for k, lab in (("all", "all"), ("lopsided_or_fbs_vs_fcs", "lopsided or FBS-vs-FCS")):
        md += _cmp_md(f"A with v5 / {lab}", o["cohort_a_comparison_with_v5_rows"][k])
    md += ["## Cohort B: settled flat-tier v5 total rows (priced)", ""]
    for k, lab in (
        ("all", "all"),
        ("lopsided_or_fbs_vs_fcs", "lopsided or FBS-vs-FCS"),
        ("other", "all other"),
    ):
        md += _cmp_md(f"B / {lab}", o["cohort_b_comparison"][k])
        md += [
            f"##### B / {lab}: flat 1U PnL on the total market ({o['price_rule'].split(';')[0]})",
            "",
            "| model | picks | W-L-P | PnL | ROI |",
            "|---|---|---|---|---|",
        ]
        for name, lab2 in (("v5", "v5 (ledger row's own result)"), ("v6", "v6"), ("v6_1", "v6.1")):
            md.append(_pnl_line(lab2 + ", all rows", o["cohort_b_pnl"][k][name]["flat_1u_all"]))
        for name, lab2 in (("v5", "v5"), ("v6", "v6"), ("v6_1", "v6.1")):
            md.append(_pnl_line(lab2 + ", 2-8% edge view", o["cohort_b_pnl"][k][name]["edge_2_8pct"]))
        md.append("")
    md += [
        f"v6 and v6.1 chose different sides on {o['cohort_b_v6_vs_v6_1_pick_flips']} of {c['cohort_b_priced']} cohort-B rows.",
        "",
        "Price rule: " + o["price_rule"] + ". Edge view: " + o["edge_view"] + ".",
        "",
        "The closing line makes no pick, so it has no PnL; it is the benchmark in the MAE tables.",
        "",
        f"## {LABEL}: reading",
        "",
        "One Saturday is a small sample drawn from the data the tilt was found on: the tables describe what happened, "
        "they do not validate v6, v6.1 or the tilt.",
        "",
    ]
    return "\n".join(md)


if __name__ == "__main__":
    raise SystemExit(main())
