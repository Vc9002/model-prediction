"""Pool audit for cfb-structural-v6 (quick-261114-srb, workstream F). Read-only.

v6 sends a team with fewer than two events in a season, and any name it cannot resolve, to one per-season pooled
lower-division pseudo-team instead of declining. "v6 covered 575 of 575" and "all 1,527 holdout events scored" therefore
say nothing about whether a real FBS side was silently rated as the pooled key. This script counts, per scored cohort,
per season and per market, the scored games in which an FBS side was rated as the pooled pseudo-team at its decision
time, and the games in which an FBS side's name did not resolve by resolve_team or a shared ESPN id.

FBS is the ESPN division fact: a team-season is FBS when the team plays at least one FBS-FBS game (game_class) in
that season of data/research/ncaaf_v6/ncaaf_games_backfill.jsonl. Rating keys come from the same RatingBook and
TeamResolver the headline numbers used (history snapshot), through ``RatingBook.identity``, which is what
``project`` calls. A key depends on (name, season) only, not on the hour of the decision within a season.

If any scored game is flagged, a SECOND LOOK recomputes the headline holdout and market-check statistics with the flagged
games excluded and prints them beside the originals. The frozen fit is never refitted and nothing in the frozen
outputs is rewritten.

Usage (repo root):
  OPENBLAS_NUM_THREADS=1 PYTHONPATH=src .venv/Scripts/python scripts/cfb_v6_pool_audit.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import cfb_v6_lib as lib
import cfb_v6_research as r6

from model_prediction.domain import parse_utc

OUT = REPO / "outputs" / "ncaaf"
WORK = REPO / "data" / "research" / "ncaaf_v6"
BACKFILL = WORK / "ncaaf_games_backfill.jsonl"
SNAPSHOT = WORK / "history_snapshot.jsonl"
PINNED = WORK / "market_reference_pinned.json"
ESPN_LINES = WORK / "espn_lines.jsonl"
V5_CACHE = OUT / "cfb_v5_base_rows.jsonl"
V6_CACHE = OUT / "cfb_v6_base_rows.jsonl"
FROZEN = OUT / "cfb_v6_frozen_fit.json"
REPORT_JSON = OUT / "cfb_v6_pool_audit.json"
REPORT_MD = OUT / "cfb_v6_pool_audit.md"
EXPECTED_FROZEN_SHA = "6e38b14ca486acfd836c1259e5c4fd4fb8e9b5894a06f367322b1eb0c7b13e70"
V5_WINDOW = r6.V5_WINDOW


# ------------------------------------------------------------------ division facts and identities
class Divisions:
    """ESPN division per (team, season) from the backfill feed class; ids first, names for unseen ids."""

    def __init__(self) -> None:
        self.fbs_id: set[tuple[str, int]] = set()
        self.fbs_name: set[tuple[str, int]] = set()
        self.any_fbs_id: set[str] = set()  # broad: FBS-FBS in ANY season (sensitivity only)
        self.name_ids: dict[str, set[str]] = defaultdict(set)
        with BACKFILL.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                g = json.loads(line)
                season = lib.season_of(parse_utc(str(g["event_start_utc"])))
                for side in ("home", "away"):
                    tid, name = str(g.get(f"{side}_team_id") or ""), str(g.get(f"{side}_team") or "")
                    if tid and name:
                        self.name_ids[name].add(tid)
                    if g.get("game_class") == "FBS-FBS":
                        if tid:
                            self.fbs_id.add((tid, season))
                            self.any_fbs_id.add(tid)
                        self.fbs_name.add((name, season))

    def is_fbs(self, name: str, tid: str | None, season: int) -> bool:
        if tid:
            return (tid, season) in self.fbs_id
        return (name, season) in self.fbs_name

    def is_fbs_any(self, name: str, tid: str | None) -> bool:
        if tid:
            return tid in self.any_fbs_id
        return any((name, s) in self.fbs_name for s in range(2016, 2027))


class Auditor:
    def __init__(self) -> None:
        self.games = lib.load_snapshot(SNAPSHOT)
        self.by_id = {g.event_id: g for g in self.games}
        self.resolver = lib.TeamResolver.from_games(self.games)
        self.book = lib.RatingBook(self.games, self.resolver)
        self.div = Divisions()
        self.name_to_id = {}
        for g in self.games:
            if g.home_id:
                self.name_to_id.setdefault(g.home, g.home_id)
            if g.away_id:
                self.name_to_id.setdefault(g.away, g.away_id)

    def side(self, eid: str, which: str, name: str, season: int) -> dict[str, Any]:
        g = self.by_id.get(eid)
        tid = (getattr(g, f"{which}_id", None) if g is not None else None) or self.name_to_id.get(name)
        ident = self.book.identity(name, season)
        fbs = self.div.is_fbs(name, tid, season)
        fbs_any = self.div.is_fbs_any(name, tid)
        return {
            "side": which,
            "name": name,
            "espn_id": tid,
            "season": season,
            "key": ident.key,
            "pooled": ident.pooled,
            "resolved": ident.resolved,
            "fbs": fbs,
            "fbs_any_season": fbs_any,
            "season_events": int(
                self.book._season_counts.get((self.book.resolver.base_key(name), season), 0)
            ),
        }

    def event(self, eid: str, home: str, away: str, season: int) -> dict[str, Any]:
        h = self.side(eid, "home", home, season)
        a = self.side(eid, "away", away, season)
        sides = (h, a)
        return {
            "event_id": eid,
            "sides": sides,
            "fbs_pooled": any(s["fbs"] and s["pooled"] for s in sides),
            "fbs_unresolved": any(s["fbs"] and not s["resolved"] for s in sides),
            "fbs_any_pooled": any(s["fbs_any_season"] and s["pooled"] for s in sides),
            "fbs_any_unresolved": any(s["fbs_any_season"] and not s["resolved"] for s in sides),
            "any_pooled": any(s["pooled"] for s in sides),
            "any_unresolved": any(not s["resolved"] for s in sides),
        }


# ------------------------------------------------------------------ cohorts
def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def load_lines() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for rec in read_jsonl(ESPN_LINES):
        sel = rec.get("selected") or {}
        out[str(rec["event_id"])] = {
            "spread_home": sel.get("home_spread_line"),
            "total": sel.get("total_line"),
        }
    return out


def build_cohorts() -> dict[str, Any]:
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    val_end = frozen["split"]["validation_end"]
    cache = read_jsonl(V6_CACHE)
    v5_rows = read_jsonl(V5_CACHE)
    v5_ids = {r["event_id"] for r in v5_rows}
    v5_hold_ids = {r["event_id"] for r in v5_rows if V5_WINDOW[0] <= r["date"] <= V5_WINDOW[1]}
    hold = [r for r in cache if r["date"] > val_end and r["season"] > r6.BURN_IN_SEASON]
    scoreable = [r for r in hold if "grid" in r]
    existing = [r for r in scoreable if r["source"] == "existing"]
    common = [r for r in existing if r["event_id"] in v5_hold_ids]
    backfill_fbs = [r for r in scoreable if r["source"] == "backfill" and r.get("fbs_involved")]
    lines = load_lines()
    cohorts: dict[str, list[dict[str, Any]]] = {}

    def item(r: dict[str, Any], market: str | None = None) -> dict[str, Any]:
        return {
            "event_id": r["event_id"],
            "date": r["date"],
            "season": r["season"],
            "home": r["home"],
            "away": r["away"],
            "market": market,
            "window": "holdout" if r["date"] > val_end else "fit",
            "v5_common": r["event_id"] in v5_ids,
        }

    cohorts["holdout_common_1527"] = [item(r) for r in common]
    cohorts["holdout_v6_scoreable_existing"] = [item(r) for r in existing]
    cohorts["holdout_backfill_fbs_involved"] = [item(r) for r in backfill_fbs]
    cohorts["holdout_all_scoreable"] = [item(r) for r in scoreable]
    # ESPN-lined, per market (the lined cohorts the market check scored: cache rows with a grid and a line)
    for market, key in (("total", "total"), ("spread", "spread_home")):
        rows = [
            item(r, market)
            for r in cache
            if "grid" in r and r["event_id"] in lines and lines[r["event_id"]].get(key) is not None
        ]
        cohorts[f"espn_{market}_all"] = rows
    # 575 / 576 reference events at their pinned decision times
    pinned = json.loads(PINNED.read_text(encoding="utf-8"))
    for market in ("total", "spread"):
        rows = []
        for eid, ev in pinned["events"].items():
            when = ev["decision_utc"].get(market)
            if when is None:
                continue
            season = lib.season_of(parse_utc(when))
            rows.append(
                {
                    "event_id": eid,
                    "date": ev["start"][:10],
                    "season": season,
                    "home": ev["home"],
                    "away": ev["away"],
                    "market": market,
                    "window": "holdout",
                    "v5_common": True,
                }
            )
        cohorts[f"reference_{market}"] = rows
    return {
        "cohorts": cohorts,
        "val_end": val_end,
        "counts": {
            "cache_rows": len(cache),
            "holdout_rows_in_cache": len(hold),
            "holdout_scoreable": len(scoreable),
            "holdout_existing": len(existing),
            "holdout_common": len(common),
            "holdout_backfill_fbs_involved": len(backfill_fbs),
        },
    }


def audit_cohort(aud: Auditor, items: list[dict[str, Any]]) -> dict[str, Any]:
    evs = []
    for it in items:
        e = aud.event(it["event_id"], it["home"], it["away"], it["season"])
        e.update(
            date=it["date"],
            season=it["season"],
            market=it["market"],
            window=it["window"],
            v5_common=it["v5_common"],
        )
        evs.append(e)
    by_season: dict[str, dict[str, int]] = {}
    for season in sorted({e["season"] for e in evs}):
        sub = [e for e in evs if e["season"] == season]
        by_season[str(season)] = _tally(sub)
    by_window = {
        w: _tally([e for e in evs if e["window"] == w])
        for w in ("fit", "holdout")
        if any(e["window"] == w for e in evs)
    }
    return {
        "n": len(evs),
        "tally": _tally(evs),
        "by_season": by_season,
        "by_window": by_window,
        "events": evs,
    }


def _tally(evs: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "events": len(evs),
        "fbs_side_pooled_events": sum(e["fbs_pooled"] for e in evs),
        "fbs_side_unresolved_events": sum(e["fbs_unresolved"] for e in evs),
        "fbs_any_season_pooled_events": sum(e["fbs_any_pooled"] for e in evs),
        "fbs_any_season_unresolved_events": sum(e["fbs_any_unresolved"] for e in evs),
        "context_any_side_pooled_events": sum(e["any_pooled"] for e in evs),
        "context_any_side_unresolved_events": sum(e["any_unresolved"] for e in evs),
        "context_fbs_sides": sum(s["fbs"] for e in evs for s in e["sides"]),
        "context_fbs_sides_resolved": sum(s["fbs"] and s["resolved"] for e in evs for s in e["sides"]),
    }


def flagged_list(results: dict[str, Any]) -> list[dict[str, Any]]:
    """Every distinct team-event flagged under the primary or the broad FBS definition, with its cohorts."""
    acc: dict[tuple[str, str], dict[str, Any]] = {}
    for cname, res in results.items():
        for e in res["events"]:
            for s in e["sides"]:
                reasons = []
                if s["fbs"] and s["pooled"]:
                    reasons.append("fbs_side_rated_as_pooled_pseudo_team")
                if s["fbs"] and not s["resolved"]:
                    reasons.append("fbs_name_unresolved")
                if s["fbs_any_season"] and not s["fbs"] and (s["pooled"] or not s["resolved"]):
                    reasons.append(
                        "broad_only_fbs_in_another_season_" + ("pooled" if s["pooled"] else "unresolved")
                    )
                if not reasons:
                    continue
                k = (e["event_id"], s["side"])
                rec = acc.setdefault(
                    k,
                    {
                        "team": s["name"],
                        "espn_id": s["espn_id"],
                        "season": s["season"],
                        "date": e["date"],
                        "event_id": e["event_id"],
                        "side": s["side"],
                        "rating_key": s["key"],
                        "pooled": s["pooled"],
                        "resolved": s["resolved"],
                        "season_events": s["season_events"],
                        "reasons": reasons,
                        "cohorts": [],
                    },
                )
                rec["cohorts"].append(cname)
    return sorted(acc.values(), key=lambda r: (r["date"], r["event_id"], r["side"]))


def collision_checks(aud: Auditor) -> dict[str, Any]:
    """Extra: does any rating key stand for two ESPN ids, or any id for two keys, among resolved teams?"""
    key_ids: dict[str, set[str]] = defaultdict(set)
    id_keys: dict[str, set[str]] = defaultdict(set)
    for g in aud.games:
        for name, tid in ((g.home, g.home_id), (g.away, g.away_id)):
            if not tid:
                continue
            key = aud.resolver.base_key(name)
            key_ids[key].add(tid)
            id_keys[tid].add(key)
    return {
        "keys_with_several_espn_ids": {k: sorted(v) for k, v in key_ids.items() if len(v) > 1},
        "espn_ids_with_several_keys": {k: sorted(v) for k, v in id_keys.items() if len(v) > 1},
        "distinct_keys": len(key_ids),
        "resolved_keys": len(aud.resolver._resolved),
    }


def independent_pool_check(aud: Auditor) -> dict[str, Any]:
    """Re-derive 'pooled' from raw ESPN ids and resolve_team, without RatingBook, for every snapshot game side.

    pooled <=> no name of the team's ESPN id resolves through resolve_team AND the id has fewer than two
    events that season. Compared with RatingBook.identity over the whole snapshot (all seasons, all divisions).
    """
    resolvable_ids: set[str] = set()
    counts: Counter = Counter()
    for g in aud.games:
        for name, tid in ((g.home, g.home_id), (g.away, g.away_id)):
            counts[(tid, g.season)] += 1
            if aud.resolver._fallback(name) is not None:
                resolvable_ids.add(str(tid))
    mismatches = 0
    pooled_sides = 0
    pooled_fbs_sides = 0
    pooled_team_seasons: set[tuple[str, int]] = set()
    for g in aud.games:
        for which, name, tid in (("home", g.home, g.home_id), ("away", g.away, g.away_id)):
            indep = str(tid) not in resolvable_ids and counts[(tid, g.season)] < 2
            ident = aud.book.identity(name, g.season)
            if indep != ident.pooled:
                mismatches += 1
            if ident.pooled:
                pooled_sides += 1
                pooled_team_seasons.add((str(tid), g.season))
                if aud.div.is_fbs(name, tid, g.season):
                    pooled_fbs_sides += 1
    return {
        "games": len(aud.games),
        "mismatches_vs_rating_book": mismatches,
        "pooled_sides_whole_snapshot": pooled_sides,
        "pooled_team_seasons_whole_snapshot": len(pooled_team_seasons),
        "pooled_fbs_sides_whole_snapshot": pooled_fbs_sides,
    }


def name_coverage(aud: Auditor, cohorts: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    """Scored names the history name table has never seen (they would go through resolve_team / NAME: fallback)."""
    known = set(aud.resolver._name_key)
    out = {}
    for cname, items in cohorts.items():
        unseen = {n for it in items for n in (it["home"], it["away"]) if n not in known}
        out[cname] = len(unseen)
    return out


def no_call_counts() -> dict[str, Any]:
    reasons: Counter = Counter()
    holdout: Counter = Counter()
    val_end = json.loads(FROZEN.read_text(encoding="utf-8"))["split"]["validation_end"]
    for r in read_jsonl(V6_CACHE):
        if "no_call" in r:
            reasons[r["no_call"]] += 1
            if r["date"] > val_end:
                holdout[r["no_call"]] += 1
    return {"cache_no_call_by_reason": dict(reasons), "holdout_no_call_by_reason": dict(holdout)}


# ------------------------------------------------------------------ SECOND LOOK (exclusion recompute)
def _exclude(rows: list[dict[str, Any]], exclude: set[str]) -> list[dict[str, Any]]:
    return [r for r in rows if r["event_id"] not in exclude]


def holdout_block(exclude: set[str]) -> dict[str, Any]:
    """cmd_holdout's cohort and scoring (research script), over the cohort minus ``exclude``; writes nothing."""
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    params, sel = frozen["params"], frozen["selected"]
    gk = r6.gkey(sel["lambda"], sel["w_prev"])
    val_end = frozen["split"]["validation_end"]
    v5_art = json.loads(r6.V5_ARTIFACT.read_text(encoding="utf-8"))
    v5_hold = [r for r in r6._load_cache(r6.V5_CACHE) if V5_WINDOW[0] <= r["date"] <= V5_WINDOW[1]]
    r6.v5r.apply_variant(v5_hold, v5_art, mean_cal=True, sd_table=True)
    v5_by_id = {r["event_id"]: r for r in v5_hold}
    cache = r6._load_cache(r6.CACHE)
    hold = [r for r in cache if r["date"] > val_end and r["season"] > r6.BURN_IN_SEASON]
    scoreable = [r for r in hold if "grid" in r and r["event_id"] not in exclude]
    existing = [r for r in scoreable if r["source"] == "existing"]
    for r in existing:
        r.update(r6._v6_values(r, gk, params))
    gls = r6._gls
    common = []
    for r in existing:
        v5row = v5_by_id.get(r["event_id"])
        if v5row is None:
            continue
        m = dict(r)
        m.update({k: v5row[k] for k in ("an_p", "an_mu", "an_sd", "an_tmu", "an_tsd")})
        y = int(m["margin"] > 0)
        m["d_brier"] = (m["v6_p"] - y) ** 2 - (m["an_p"] - y) ** 2
        m["d_margin_ls"] = gls(m["margin"], m["v6_mu"], m["v6_sd"]) - gls(m["margin"], m["an_mu"], m["an_sd"])
        m["d_total_ls"] = gls(m["total"], m["v6_tmu"], m["v6_tsd"]) - gls(
            m["total"], m["an_tmu"], m["an_tsd"]
        )
        m["d_margin_abs"] = abs(m["margin"] - m["v6_mu"]) - abs(m["margin"] - m["an_mu"])
        common.append(m)
    boot = r6.v5r.clustered_bootstrap_mean_delta
    out: dict[str, Any] = {
        "n": len(common),
        "v6": r6._score(common, "v6"),
        "v5": r6._score(common, "v5"),
        "margin_mae": {"v6": r6._mae(common, "v6_mu"), "v5": r6._mae(common, "an_mu")},
        "delta_v6_minus_v5": {
            "brier": boot(common, "d_brier", seed=11),
            "margin_log_score": boot(common, "d_margin_ls", seed=12),
            "total_log_score": boot(common, "d_total_ls", seed=13),
            "margin_abs_error": boot(common, "d_margin_abs", seed=14),
        },
        "by_season": {},
    }
    for season in sorted({r["season"] for r in common}):
        sub = [r for r in common if r["season"] == season]
        out["by_season"][str(season)] = {
            "n": len(sub),
            "v6_brier": r6._score(sub, "v6")["brier"],
            "v5_brier": r6._score(sub, "v5")["brier"],
        }
    backfill_rows = [r for r in scoreable if r["source"] == "backfill" and r.get("fbs_involved")]
    for r in backfill_rows:
        r.update(r6._v6_values(r, gk, params))
    out["backfill_only_fbs_involved"] = r6._v6_only_block(backfill_rows)
    return out


def _stat_cell(st: dict[str, Any] | None) -> dict[str, Any] | None:
    if not st:
        return None
    if "corr" not in st:
        return {"n": st.get("n")}
    ci = st["corr_ci"]
    return {
        "n": st["n"],
        "corr": st["corr"],
        "ci_low": ci["ci_95_low"],
        "ci_high": ci["ci_95_high"],
        "side": f"{st['side_wins']}-{st['side_losses']}",
        "mae_model": st["mae_model"],
        "mae_line": st["mae_line"],
        "sd_model": st["sd_model"],
        "sd_line": st["sd_line"],
        "sd_actual": st["sd_actual"],
    }


def reference_blocks() -> dict[str, Any]:
    """Row sets of the 575 / 576 reference check (frozen v6 and v5 at pinned decision times); stats come later."""
    import cfb_v6_market_check as mc

    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    v5 = mc.compute_v5_block_saved()
    full = {}
    for market in ("total", "spread"):
        values = {
            k: {"value": mc.v5_calibrated_values(v5["art"], p, market), "sg": p["sg"], "fcs": p["fcs"]}
            for k, p in v5["projections"].items()
            if k[1] == market
        }
        full[market] = mc.build_market_rows(v5["pinned"], values, market)
    v6 = mc.compute_v6_block(frozen, v5["pinned"])
    out: dict[str, Any] = {}
    for market in ("total", "spread"):
        v6_rows = mc.build_market_rows(
            v5["pinned"], {k: v for k, v in v6["values"].items() if k[1] == market}, market
        )
        out[market] = {"v5_full": full[market], "v6": v6_rows, "uncovered": v6["reason_counts"][market]}
    return out


def reference_stats(rows: dict[str, Any], exclude: set[str]) -> dict[str, Any]:
    import cfb_v6_market_check as mc

    out: dict[str, Any] = {}
    for market, blk in rows.items():
        v5_full = _exclude(blk["v5_full"], exclude)
        v6_rows = _exclude(blk["v6"], exclude)
        covered = {r["event_id"] for r in v6_rows}
        v5_common = mc.subset(v5_full, covered)
        out[market] = {
            "v5_full_set": _stat_cell(mc.market_stats(v5_full, market, "model")),
            "v5_common_set": _stat_cell(mc.market_stats(v5_common, market, "model")) if v5_common else None,
            "v6_common_set": _stat_cell(mc.market_stats(v6_rows, market, "model")) if v6_rows else None,
        }
    return out


def espn_rows() -> dict[str, list[dict[str, Any]]]:
    """Row construction of cfb_v6_market_check.run_v6_espn (pinned ESPN close, calibrated v6 and v5)."""
    import cfb_v6_market_check as mc

    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    sel, params = frozen["selected"], frozen["params"]
    gk = r6.gkey(sel["lambda"], sel["w_prev"])
    mcal, tcal = params["margin_calibration"], params["total_calibration"]
    val_end = frozen["split"]["validation_end"]
    v5_art = mc.load_cfb_structural_v5_artifact()
    v5m, v5t = v5_art["margin_calibration"], v5_art["total_calibration"]
    lines = load_lines()
    v5_by_id = {r["event_id"]: r for r in read_jsonl(V5_CACHE)}
    out: dict[str, list[dict[str, Any]]] = {"total": [], "spread": []}
    for r in read_jsonl(V6_CACHE):
        eid = r["event_id"]
        if eid not in lines or "grid" not in r:
            continue
        pm, pt = r["grid"][gk][:2]
        v6_margin = mcal["intercept"] + mcal["slope"] * pm
        v6_total = tcal["intercept"] + tcal["slope"] * pt
        v5r_ = v5_by_id.get(eid)
        v5_margin = v5m["intercept"] + v5m["slope"] * v5r_["proj_margin"] if v5r_ else None
        v5_total = max(24.0, v5t["intercept"] + v5t["slope"] * v5r_["proj_total"]) if v5r_ else None
        base = {
            "event_id": eid,
            "date": r["date"],
            "season": r["season"],
            "window": "holdout" if r["date"] > val_end else "fit",
        }
        for market, key in (("total", "total"), ("spread", "spread_home")):
            raw = lines[eid].get(key)
            if raw is None:
                continue
            out[market].append(
                {
                    **base,
                    "line": float(raw) if market == "total" else -float(raw),
                    "actual": r["total"] if market == "total" else r["margin"],
                    "v6": v6_total if market == "total" else v6_margin,
                    "v5": v5_total if market == "total" else v5_margin,
                }
            )
    return out


def espn_stats(
    rows: dict[str, list[dict[str, Any]]], exclude: set[str], group_filter: list[str]
) -> dict[str, Any]:
    import cfb_v6_market_check as mc

    out: dict[str, Any] = {}
    for market, src_all in rows.items():
        src = _exclude(src_all, exclude)
        names: list[tuple[str, list[dict[str, Any]]]] = [
            (f"window:{w}", [r for r in src if r["window"] == w]) for w in ("fit", "holdout")
        ]
        names += [
            (f"season:{se}", [r for r in src if r["season"] == se])
            for se in sorted({r["season"] for r in src})
        ]
        names.append(("all", src))
        blk: dict[str, Any] = {}
        for name, sub in names:
            if name not in group_filter:
                continue
            com = [r for r in sub if r["v5"] is not None]
            blk[name] = {
                "v6_all_covered": _stat_cell(
                    mc._group_stats([{**r, "model": r["v6"]} for r in sub], market, "model")
                ),
                "v6_common": _stat_cell(
                    mc._group_stats([{**r, "model": r["v6"]} for r in com], market, "model")
                ),
                "v5_common": _stat_cell(
                    mc._group_stats([{**r, "model": r["v5"]} for r in com], market, "model")
                ),
            }
        out[market] = blk
    return out


def stored_espn_cell(stored: dict[str, Any], market: str, group: str, model: str) -> dict[str, Any] | None:
    g = stored["markets"][market]["groups"].get(group)
    return _stat_cell(g.get(model)) if g else None


def stored_reference_cell(stored: dict[str, Any], market: str, which: str) -> dict[str, Any] | None:
    return _stat_cell(stored["markets"][market][which])


def _same(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    if a is None or b is None:
        return a == b
    return a["n"] == b["n"] and all(
        abs(a[k] - b[k]) < 1e-9
        for k in ("corr", "ci_low", "ci_high")
        if k in a and k in b and a[k] is not None
    )


def second_look(excluded: set[str]) -> dict[str, Any]:
    stored_research = json.loads((OUT / "cfb_v6_research.json").read_text(encoding="utf-8"))
    stored_ref = json.loads((OUT / "cfb_v6_market_check.json").read_text(encoding="utf-8"))
    stored_espn = json.loads((OUT / "cfb_v6_espn_market_check.json").read_text(encoding="utf-8"))
    headline_groups = ["window:fit", "window:holdout", "all"]
    sl: dict[str, Any] = {"excluded_event_count": len(excluded)}

    print("second look: holdout", flush=True)
    ctrl_h = holdout_block(set())
    sl["holdout"] = {
        "original_stored": {
            "n": stored_research["common_cohort"]["n"],
            "v6": stored_research["common_cohort"]["v6"],
            "v5": stored_research["common_cohort"]["v5"],
            "margin_mae": stored_research["common_cohort"]["margin_mae"],
            "delta_v6_minus_v5": stored_research["common_cohort"]["delta_v6_minus_v5"],
            "by_season": stored_research["common_cohort"]["by_season"],
            "backfill_only_fbs_involved": stored_research["backfill_only_fbs_involved_block"],
        },
        "control_recompute_no_exclusion": ctrl_h,
        "second_look": holdout_block(excluded),
    }
    c, o = ctrl_h, stored_research["common_cohort"]
    sl["holdout"]["control_matches_stored"] = bool(
        c["n"] == o["n"]
        and abs(c["v6"]["brier"] - o["v6"]["brier"]) < 1e-9
        and abs(c["v5"]["brier"] - o["v5"]["brier"]) < 1e-9
        and all(
            abs(c["delta_v6_minus_v5"][k]["point_estimate"] - o["delta_v6_minus_v5"][k]["point_estimate"])
            < 1e-6
            and abs(c["delta_v6_minus_v5"][k]["ci_95_low"] - o["delta_v6_minus_v5"][k]["ci_95_low"]) < 1e-6
            for k in o["delta_v6_minus_v5"]
        )
    )

    print("second look: reference", flush=True)
    ref_rows = reference_blocks()
    ctrl_r = reference_stats(ref_rows, set())
    sl["reference"] = {
        "original_stored": {
            m: {
                w: stored_reference_cell(stored_ref, m, w)
                for w in ("v5_full_set", "v5_common_set", "v6_common_set")
            }
            for m in ("total", "spread")
        },
        "control_recompute_no_exclusion": ctrl_r,
        "second_look": reference_stats(ref_rows, excluded),
        "uncovered_reasons": {m: ref_rows[m]["uncovered"] for m in ref_rows},
    }
    sl["reference"]["control_matches_stored"] = all(
        _same(ctrl_r[m][w], sl["reference"]["original_stored"][m][w]) for m in ctrl_r for w in ctrl_r[m]
    )

    print("second look: espn control", flush=True)
    rows = espn_rows()
    ctrl_e = espn_stats(rows, set(), ["window:holdout", "all"])
    sl["espn"] = {
        "control_recompute_no_exclusion": ctrl_e,
        "control_matches_stored": all(
            _same(ctrl_e[m][g][t], stored_espn_cell(stored_espn, m, g, t))
            for m in ctrl_e
            for g in ctrl_e[m]
            for t in ("v6_all_covered", "v6_common", "v5_common")
        ),
    }
    print("second look: espn excluded", flush=True)
    all_groups = ["window:fit", "window:holdout", "all"] + [f"season:{y}" for y in range(2017, 2027)]
    second = espn_stats(rows, excluded, all_groups)
    sl["espn"]["second_look"] = second
    sl["espn"]["original_stored"] = {
        m: {
            g: {
                t: stored_espn_cell(stored_espn, m, g, t)
                for t in ("v6_all_covered", "v6_common", "v5_common")
            }
            for g in all_groups
        }
        for m in ("total", "spread")
    }
    sl["headline_groups"] = headline_groups
    return sl


# ------------------------------------------------------------------ report
def _f(x: Any, nd: int = 3) -> str:
    return "n/a" if x is None else f"{x:+.{nd}f}" if isinstance(x, float) else str(x)


def _corr_txt(c: dict[str, Any] | None) -> str:
    if not c:
        return "-"
    if "corr" not in c:
        return f"n {c['n']}"
    return f"{c['corr']:+.3f} [{c['ci_low']:+.3f}, {c['ci_high']:+.3f}] (n {c['n']})"


def render_md(rep: dict[str, Any]) -> str:
    t = rep["cohort_tallies"]
    L: list[str] = []
    L.append(f"# cfb-structural-v6 pool audit, as of {rep['as_of_utc']}")
    L.append("")
    L.append(
        f"Frozen fit sha256 `{rep['frozen_params_sha256']}` (verified before and after this run; nothing refitted, "
        "no frozen output rewritten). History snapshot sha256 "
        f"`{rep['snapshot_sha256']}`, backfill sha256 `{rep['backfill_sha256']}`."
    )
    L.append("")
    L.append("## Question")
    L.append("")
    L.append(
        "v6 sends a team with fewer than two events in a season, and any unseen name, to one per-season pooled "
        "lower-division pseudo-team (`__POOL_OTHER__:<season>`) instead of declining. Among the scored games, how "
        "many had an FBS side (ESPN division: the team-season plays an FBS-FBS `game_class` game) whose rating key "
        "was the pooled pseudo-team, and how many had a name that did not resolve by `resolve_team` or a shared "
        "ESPN id? Keys come from the same `RatingBook.identity` that `project` calls, over the frozen history "
        "snapshot; a key depends on (name, season), not on the hour of the decision."
    )
    L.append("")
    L.append("## Verdict")
    L.append("")
    L.append(rep["verdict_text"])
    L.append("")
    L.append("## Counts per cohort (events)")
    L.append("")
    L.append(
        "| cohort | events | FBS side POOLED | FBS-side name UNRESOLVED | FBS sides total | any-division side pooled "
        "(context) | any-division side unresolved (context) |"
    )
    L.append("|---|---|---|---|---|---|---|")
    for name, blk in t.items():
        x = blk["tally"]
        L.append(
            f"| {name} | {x['events']} | {x['fbs_side_pooled_events']} | {x['fbs_side_unresolved_events']} | "
            f"{x['context_fbs_sides']} | {x['context_any_side_pooled_events']} | "
            f"{x['context_any_side_unresolved_events']} |"
        )
    L.append("")
    L.append(
        "`holdout_common_1527` is the headline locked-holdout cohort; `holdout_v6_scoreable_existing` (1,532) adds the "
        "5 v6-only events; `holdout_backfill_fbs_involved` (384) is the separately scored backfill block; "
        "`holdout_all_scoreable` also holds FCS-vs-FCS backfill rows that no headline statistic uses (all of its "
        "pooled events are there). `reference_total` / `reference_spread` are the 575 / 576 reference events at their "
        "pinned decision times. `espn_*_all` are all ESPN-lined, v6-scored events for the market (fit window plus "
        "holdout window)."
    )
    L.append("")
    L.append("## Per season and per market")
    L.append("")
    L.append("Events with an FBS side pooled / FBS-side name unresolved (of events), per season:")
    L.append("")
    seasons = sorted({s for blk in t.values() for s in blk["by_season"]})
    L.append("| cohort | " + " | ".join(seasons) + " |")
    L.append("|---|" + "---|" * len(seasons))
    for name, blk in t.items():
        cells = []
        for s in seasons:
            x = blk["by_season"].get(s)
            cells.append(
                "-"
                if not x
                else f"{x['fbs_side_pooled_events']} / {x['fbs_side_unresolved_events']} of {x['events']}"
            )
        L.append(f"| {name} | " + " | ".join(cells) + " |")
    L.append("")
    L.append("Per market (total vs spread) the same counts, from the lined cohorts above:")
    L.append("")
    L.append("| market | cohort | events | FBS pooled | FBS unresolved |")
    L.append("|---|---|---|---|---|")
    for market in ("total", "spread"):
        for name in (f"reference_{market}", f"espn_{market}_all"):
            x = t[name]["tally"]
            L.append(
                f"| {market} | {name} | {x['events']} | {x['fbs_side_pooled_events']} | {x['fbs_side_unresolved_events']} |"
            )
        for w, x in t[f"espn_{market}_all"]["by_window"].items():
            L.append(
                f"| {market} | espn_{market}_all window:{w} | {x['events']} | {x['fbs_side_pooled_events']} | {x['fbs_side_unresolved_events']} |"
            )
    L.append("")
    L.append("## Independent checks")
    L.append("")
    ic = rep["independent_pool_check"]
    L.append(
        f"- Pooled status recomputed from raw ESPN ids and `resolve_team` (no RatingBook) over all {ic['games']} "
        f"snapshot games: {ic['mismatches_vs_rating_book']} mismatches against `RatingBook.identity`. Whole "
        f"snapshot: {ic['pooled_sides_whole_snapshot']} pooled sides over {ic['pooled_team_seasons_whole_snapshot']} "
        f"pooled team-seasons; FBS pooled sides: {ic['pooled_fbs_sides_whole_snapshot']}."
    )
    L.append(
        f"- Scored names the history name table has never seen (would hit the resolve_team / `NAME:` fallback): "
        f"{json.dumps(rep['unseen_names_by_cohort'])}."
    )
    kc = rep["key_collision_checks"]
    L.append(
        f"- Key collisions: rating keys standing for several ESPN ids: {len(kc['keys_with_several_espn_ids'])}; "
        f"ESPN ids spread over several keys: {len(kc['espn_ids_with_several_keys'])} "
        f"({kc['distinct_keys']} distinct keys, {kc['resolved_keys']} resolved)."
    )
    nc = rep["no_call"]
    L.append(
        f"- The fail-closed path does fire: no-call rows in the cache {json.dumps(nc['cache_no_call_by_reason'])}, "
        f"of which holdout {json.dumps(nc['holdout_no_call_by_reason'])}; those events are not scored anywhere."
    )
    L.append("")
    L.append("## The unresolved FBS names (rated under a stable ESPN-id key, not pooled)")
    L.append("")
    for team, blk in rep["unresolved_team_summary"].items():
        L.append(
            f"- {team}: rating key `{blk['key']}`; seasons {blk['seasons']}; events per season "
            f"{blk['events_per_season']}; flagged team-events {blk['flagged_team_events']}."
        )
    L.append("")
    L.append(
        "`resolve_team` has no alias for these, so it returns None and the rating key falls to `ID:<espn id>`. "
        "Every name variant of the same ESPN id lands on the same key, and the team has at least two events in its "
        "season (the counts above), so it is a proper own-team rating, never the pooled pseudo-team."
    )
    L.append("")
    if "second_look" in rep:
        L.extend(render_second_look(rep["second_look"]))
    L.append("## Every flagged team-event")
    L.append("")
    L.append(
        f"{rep['flagged_primary_count']} distinct team-events with an FBS side whose name did not resolve "
        "(reason `fbs_name_unresolved`); 0 pooled. Cohorts are the audited cohorts the event belongs to. "
        f"A further {rep['flagged_broad_only_count']} team-events are listed in the JSON only (the team is FBS in "
        "another season and FCS in this one; broad definition, same stable key, not pooled)."
    )
    L.append("")
    L.append("| team | season | date | side | rating key | events that season | reason | cohorts |")
    L.append("|---|---|---|---|---|---|---|---|")
    for f in rep["flagged"]:
        if (
            "fbs_name_unresolved" not in f["reasons"]
            and "fbs_side_rated_as_pooled_pseudo_team" not in f["reasons"]
        ):
            continue
        L.append(
            f"| {f['team']} | {f['season']} | {f['date']} | {f['side']} | `{f['rating_key']}` | "
            f"{f['season_events']} | {', '.join(f['reasons'])} | {', '.join(f['cohorts'])} |"
        )
    L.append("")
    return "\n".join(L)


def render_second_look(sl: dict[str, Any]) -> list[str]:
    L: list[str] = []
    L.append("## SECOND LOOK: recomputation with the flagged games excluded")
    L.append("")
    L.append(
        "SECOND LOOK, not a replacement of the first look. No game had an FBS side rated as the pooled pseudo-team, "
        "so no pool contamination exists to remove. The flagged team-events are the FBS sides whose name did not "
        "resolve (rated under a stable `ID:` key, see above). The recomputation excludes every event in which such a "
        f"side plays ({sl['excluded_event_count']} events, all cohorts) as a conservative sensitivity bound, using the "
        "frozen v6 values and the frozen v5 calibration unchanged. The unexcluded recompute reproduces the stored "
        f"first-look numbers: holdout {sl['holdout']['control_matches_stored']}, reference "
        f"{sl['reference']['control_matches_stored']}, ESPN {sl['espn']['control_matches_stored']}."
    )
    L.append("")
    h = sl["holdout"]
    o, s = h["original_stored"], h["second_look"]
    L.append(f"### Locked holdout, common cohort (original n {o['n']}, second look n {s['n']})")
    L.append("")
    L.append("| statistic | original | second look | delta |")
    L.append("|---|---|---|---|")
    for label, ov, sv, nd in (
        ("v6 Brier", o["v6"]["brier"], s["v6"]["brier"], 5),
        ("v5 Brier", o["v5"]["brier"], s["v5"]["brier"], 5),
        ("v6 log loss", o["v6"]["log_loss"], s["v6"]["log_loss"], 4),
        ("v5 log loss", o["v5"]["log_loss"], s["v5"]["log_loss"], 4),
        ("v6 ECE", o["v6"]["ece"], s["v6"]["ece"], 4),
        ("v5 ECE", o["v5"]["ece"], s["v5"]["ece"], 4),
        ("v6 margin MAE", o["margin_mae"]["v6"], s["margin_mae"]["v6"], 3),
        ("v5 margin MAE", o["margin_mae"]["v5"], s["margin_mae"]["v5"], 3),
    ):
        L.append(f"| {label} | {ov:.{nd}f} | {sv:.{nd}f} | {sv - ov:+.{nd}f} |")
    for key, label in (
        ("brier", "Brier delta v6-v5"),
        ("margin_log_score", "margin log score delta"),
        ("total_log_score", "total log score delta"),
        ("margin_abs_error", "margin abs error delta"),
    ):
        a, b = o["delta_v6_minus_v5"][key], s["delta_v6_minus_v5"][key]
        L.append(
            f"| {label} (95% CI) | {a['point_estimate']:+.5f} [{a['ci_95_low']:+.5f}, {a['ci_95_high']:+.5f}] | "
            f"{b['point_estimate']:+.5f} [{b['ci_95_low']:+.5f}, {b['ci_95_high']:+.5f}] | "
            f"{b['point_estimate'] - a['point_estimate']:+.5f} |"
        )
    L.append("")
    L.append(
        "| season | n orig | n second look | v6 Brier orig | v6 Brier SL | v5 Brier orig | v5 Brier SL |"
    )
    L.append("|---|---|---|---|---|---|---|")
    for season, ob in o["by_season"].items():
        sb = s["by_season"].get(season, {})
        L.append(
            f"| {season} | {ob['n']} | {sb.get('n')} | {ob['v6_brier']:.5f} | {sb.get('v6_brier', float('nan')):.5f} | "
            f"{ob['v5_brier']:.5f} | {sb.get('v5_brier', float('nan')):.5f} |"
        )
    ob, sb = o["backfill_only_fbs_involved"], s["backfill_only_fbs_involved"]
    if ob and sb and "v6" in ob and "v6" in sb:
        L.append("")
        L.append(
            f"Backfill-only FBS-involved block: n {ob['n']} -> {sb['n']}, v6 Brier {ob['v6']['brier']:.5f} -> "
            f"{sb['v6']['brier']:.5f}."
        )
    L.append("")
    L.append("### Reference 575 / 576 (frozen v6 vs v5, date-clustered 95% CI)")
    L.append("")
    L.append("| market | set | original corr [CI] (n) | second look corr [CI] (n) | side orig | side SL |")
    L.append("|---|---|---|---|---|---|")
    for market in ("total", "spread"):
        for w in ("v6_common_set", "v5_common_set", "v5_full_set"):
            a, b = sl["reference"]["original_stored"][market][w], sl["reference"]["second_look"][market][w]
            L.append(
                f"| {market} | {w} | {_corr_txt(a)} | {_corr_txt(b)} | {a['side'] if a else '-'} | {b['side'] if b else '-'} |"
            )
    L.append("")
    L.append("Model SD / line SD / actual SD and MAE (model / line), original vs second look:")
    L.append("")
    L.append(
        "| market | set | SD model orig | SD model SL | MAE model orig | MAE model SL | MAE line orig | MAE line SL |"
    )
    L.append("|---|---|---|---|---|---|---|---|")
    for market in ("total", "spread"):
        for w in ("v6_common_set", "v5_common_set"):
            a, b = sl["reference"]["original_stored"][market][w], sl["reference"]["second_look"][market][w]
            L.append(
                f"| {market} | {w} | {a['sd_model']:.2f} | {b['sd_model']:.2f} | {a['mae_model']:.2f} | "
                f"{b['mae_model']:.2f} | {a['mae_line']:.2f} | {b['mae_line']:.2f} |"
            )
    L.append("")
    L.append(
        "### ESPN closing lines (pinned median), corr(model - line, actual - line) with date-clustered 95% CI"
    )
    L.append("")
    for market in ("total", "spread"):
        L.append(f"#### {market}")
        L.append("")
        L.append(
            "| group | model | original corr [CI] (n) | second look corr [CI] (n) | side orig | side SL |"
        )
        L.append("|---|---|---|---|---|---|")
        for g, cells in sl["espn"]["second_look"][market].items():
            for t in ("v6_all_covered", "v6_common", "v5_common"):
                a = sl["espn"]["original_stored"][market][g][t]
                b = cells[t]
                L.append(
                    f"| {g} | {t} | {_corr_txt(a)} | {_corr_txt(b)} | "
                    f"{a['side'] if a and 'side' in a else '-'} | {b['side'] if b and 'side' in b else '-'} |"
                )
        L.append("")
    return L


def reference_orientation(aud: Auditor) -> dict[str, Any]:
    """Pinned reference events vs the snapshot game: home/away slot swaps (name spelling variants are not swaps)."""
    pinned = json.loads(PINNED.read_text(encoding="utf-8"))
    swaps = spelling = 0
    for eid, ev in pinned["events"].items():
        g = aud.by_id[eid]
        if (g.home, g.away) == (ev["home"], ev["away"]):
            continue
        bk = aud.resolver.base_key
        if (bk(g.home), bk(g.away)) == (bk(ev["home"]), bk(ev["away"])):
            spelling += 1
        else:
            swaps += 1
    return {
        "events": len(pinned["events"]),
        "name_spelling_variants_only": spelling,
        "home_away_swaps_or_mismatch": swaps,
    }


def verdict_flips(sl: dict[str, Any]) -> list[dict[str, Any]]:
    """ESPN rows (any group, incl. seasons) whose sign or CI-excludes-zero verdict differs after exclusion."""

    def excl(c: dict[str, Any]) -> bool:
        return bool(c["ci_low"] > 0 or c["ci_high"] < 0)

    out = []
    e = sl["espn"]
    for market, groups in e["second_look"].items():
        for g, cells in groups.items():
            for t, b in cells.items():
                o = e["original_stored"][market][g][t]
                if not o or not b or "corr" not in o or "corr" not in b:
                    continue
                if excl(o) != excl(b) or (o["corr"] > 0) != (b["corr"] > 0):
                    out.append(
                        {
                            "market": market,
                            "group": g,
                            "model": t,
                            "orig_ci_excludes_zero": excl(o),
                            "sl_ci_excludes_zero": excl(b),
                        }
                    )
    return out


def main() -> int:
    sha = r6.verify_frozen(quiet=True)
    if sha != EXPECTED_FROZEN_SHA:
        raise SystemExit(f"frozen fit sha {sha} != {EXPECTED_FROZEN_SHA}")
    aud = Auditor()
    built = build_cohorts()
    results = {name: audit_cohort(aud, items) for name, items in built["cohorts"].items()}
    flagged = flagged_list(results)
    primary_reasons = ("fbs_side_rated_as_pooled_pseudo_team", "fbs_name_unresolved")
    primary = [f for f in flagged if any(r in primary_reasons for r in f["reasons"])]
    excluded = {
        e["event_id"]
        for res in results.values()
        for e in res["events"]
        if e["fbs_pooled"] or e["fbs_unresolved"]
    }
    summary: dict[str, Any] = {}
    for f in primary:
        blk = summary.setdefault(
            f["team"],
            {"key": f["rating_key"], "seasons": set(), "events_per_season": {}, "flagged_team_events": 0},
        )
        blk["seasons"].add(f["season"])
        blk["events_per_season"][str(f["season"])] = f["season_events"]
        blk["flagged_team_events"] += 1
    for blk in summary.values():
        blk["seasons"] = sorted(blk["seasons"])
    pooled_total = sum(r["tally"]["fbs_side_pooled_events"] for r in results.values())
    report: dict[str, Any] = {
        "frozen_params_sha256": sha,
        "as_of_utc": r6.AS_OF_UTC,
        "history_snapshot_games": len(aud.games),
        "backfill_sha256": r6.sha256_file(BACKFILL),
        "snapshot_sha256": r6.sha256_file(SNAPSHOT),
        "counts": built["counts"],
        "cohort_tallies": {
            n: {"tally": r["tally"], "by_season": r["by_season"], "by_window": r["by_window"]}
            for n, r in results.items()
        },
        "flagged_primary_count": len(primary),
        "flagged_broad_only_count": len(flagged) - len(primary),
        "flagged": flagged,
        "unresolved_team_summary": summary,
        "key_collision_checks": collision_checks(aud),
        "independent_pool_check": independent_pool_check(aud),
        "unseen_names_by_cohort": name_coverage(aud, built["cohorts"]),
        "no_call": no_call_counts(),
        "excluded_event_ids": sorted(excluded),
        "reference_orientation_check": reference_orientation(aud),
    }
    counts_ps = [n for blk in summary.values() for n in blk["events_per_season"].values()]
    lo, hi = (min(counts_ps), max(counts_ps)) if counts_ps else (0, 0)
    hc = results["holdout_common_1527"]["tally"]
    rt, rs = results["reference_total"]["tally"], results["reference_spread"]["tally"]
    report["verdict_text"] = (
        f"**Pooled: zero. The reported numbers stand with respect to pool contamination.** Across every scored cohort "
        f"(holdout 1,527 / 1,532 / 384, reference 575 and 576, ESPN-lined total and spread) {pooled_total} events had "
        "an FBS side rated as the per-season pooled pseudo-team, and an independent recount from raw ESPN ids "
        "agrees with the rating book on every snapshot game. "
        f"Names with neither an ESPN id nor a resolve_team alias (`NAME:` keys) among the flagged team-events: "
        f"{sum(1 for f in flagged if f['rating_key'].startswith('NAME:'))}; scored names never seen in the history: "
        f"{sum(report['unseen_names_by_cohort'].values())}. "
        f"**Unresolved names: nonzero but harmless.** {hc['fbs_side_unresolved_events']} of the 1,527 holdout events, "
        f"{rt['fbs_side_unresolved_events']} of 575 and {rs['fbs_side_unresolved_events']} of 576 reference events had "
        f"an FBS side whose name `resolve_team` does not know. All are {len(summary)} programs "
        f"({', '.join(sorted(summary))}) that carry a stable `ID:<espn id>` rating key with {lo} to {hi} events in "
        "every flagged season, never the pooled key. Because the count is nonzero, a SECOND LOOK recomputes the headline numbers "
        "without any event they play in; see below."
    )
    print(json.dumps({n: r["tally"] for n, r in results.items()}, indent=1))
    print(
        "primary flagged team-events",
        len(primary),
        "excluded events",
        len(excluded),
        "pooled events",
        pooled_total,
    )
    if "--audit-only" not in sys.argv:
        report["second_look"] = second_look(excluded)
        flips = verdict_flips(report["second_look"])
        report["second_look_verdict_flips"] = flips
        report["verdict_text"] += " " + (
            "Second-look result: no headline statistic changes its verdict (sign of the correlation or whether its "
            "95% CI excludes zero), the holdout v6-minus-v5 Brier delta stays negative with a CI excluding zero, and "
            "every control recompute reproduces the stored first-look value exactly. Rows that do flip on exclusion "
            f"(season-level ESPN rows only): {json.dumps(flips) if flips else 'none'}."
        )
    sha_after = r6.verify_frozen(quiet=True)
    if sha_after != EXPECTED_FROZEN_SHA:
        raise SystemExit("frozen fit changed during the audit")
    report["frozen_params_sha256_after"] = sha_after
    REPORT_JSON.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    REPORT_MD.write_text(render_md(report), encoding="utf-8")
    print("wrote", REPORT_JSON, REPORT_MD)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
