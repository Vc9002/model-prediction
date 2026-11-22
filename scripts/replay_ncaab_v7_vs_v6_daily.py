"""Point-in-time replay of ncaab-spread-v6 vs ncaab-spread-v7 over every settled NCAAB game, day by day.

Research only: reads the ledger read-only, writes nothing outside outputs/ncaab/.

Universe: every settled NCAAB spread / moneyline row in ``ledger_records`` (all tiers, all model ids;
superseded / removed / archived rows excluded), deduplicated to ONE decision per (event, market): the
latest settled row created before tip-off (``--dedup first`` takes the earliest instead, as a
sensitivity). That row fixes the decision time, the line, the teams and the neutral flag.

Prices: both sides of the contract are read from the Polymarket quote archive
(``<runtime>/odds/ncaab/<date>/polymarket_snapshots.jsonl``): the latest two-sided BBO of the same
market (slug, else |line|) observed at or before the decision time and before tip-off. Where no
archived BBO exists the logged side keeps its logged ask and the other side is the complement plus the
median observed overround (flagged ``complement``). Both models always see the same prices.

Models: the production factories ``ncaab_spread_v6_model()`` and ``ncaab_spread_v7_model()`` (hash-verified
v6 artifact; v7 = same artifact + frozen multi-season margin engine), called through ``predict_games``
exactly as ``college_basketball_forward`` does, on ``FeatureStore.games_before(cutoff)`` where the cutoff
is the row's recorded ``history_cutoff_date`` (else the ET date of the decision). The v7 engine's own
fallback (it swallows exceptions and serves v6) is instrumented so a failed batch is counted, not
silently scored as v7. ``v7m`` (moneyline champion since 2026-11-21: P_t8(margin >= 1), Elo weight 0)
is derived from the v7 margin distribution as an extra moneyline column.

Calling rules (``college_basketball_forward``), applied identically to both models:
- flat: spread takes the side with p >= 0.5 and the best edge (no hurdle; divergence gate 12 points);
  moneyline takes the favoured side (p >= 0.5) when its edge > 0.
- main: flat AND edge >= 3.5% AND |spread line| <= 16 AND moneyline ask >= 20% AND the model's
  current-season sample >= 8.
- both: non-D1 games declined (``decline_non_d1``). Stakes: ``compute_dynamic_units(edge, min_edge=0.035)``
  (1.0-2.0U), capped at 1U when p >= 80%; and flat 1U.
Raw probabilities are used (all NCAAB calibrators serve identity since 2026-11-21).

Grading: final scores from the ESPN history (FeatureStore) matched by teams and ET date, else the scores
stored on the settled ledger row; the ledger's own result for the logged side is cross-checked.

Usage:
    PYTHONPATH=src python scripts/replay_ncaab_v7_vs_v6_daily.py [--dedup latest|first] [--no-write]
Writes outputs/ncaab/ncaab_v7_vs_v6_daily.{json,md}.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, "src")

from model_prediction.domain import EASTERN, parse_utc
from model_prediction.features.base import FeatureStore, GameRecord
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.ncaab_spread_v6 import ncaab_spread_v6_model, ncaab_spread_v7_model

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT_DIR = ROOT / "outputs" / "ncaab"
RUNTIME = Path(r"E:\model-prediction-runtime")
LEDGER_DB = RUNTIME / "ledgers" / "ledgers.db"
QUOTES = RUNTIME / "odds" / "ncaab"
TRACKER = RUNTIME / "manual_trackers" / "best_pick_audit.csv"
WIDGET = Path(r"C:\Users\Vince\Documents\Rainmeter\Skins\BestPickAudit\@Resources")

MIN_EDGE = 0.035  # config/model.yaml models.NCAAB.min_edge
MIN_TEAM_GAMES = 8  # models.NCAAB.min_team_games (Main current-season sample floor)
MIN_ML_ASK = 0.20
MAX_SPREAD_LINE = 16.0
MAX_MARGIN_DIVERGENCE = 12.0
HIGH_P_FLOOR, HIGH_P_CAP = 0.80, 1.0
V6_FIX_AT = parse_utc("2026-11-12T19:05:00Z")  # v6 artifact reproduction gate passed (artifact evidence)
MODELS = ("v6", "v7")
TIERS = ("flat", "qualified", "main")
STAKES = ("dyn", "u1")


# ----------------------------------------------------------------------------------------- ledger


def _snapshot(dp: dict[str, Any]) -> dict[str, Any]:
    snap = dp.get("model_input_snapshot_json") or {}
    if isinstance(snap, str):
        try:
            snap = json.loads(snap)
        except json.JSONDecodeError:
            snap = {}
    return snap if isinstance(snap, dict) else {}


def load_ledger() -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{LEDGER_DB.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        select pick_id, ledger_tier, event_id, event_start_utc, market_type, selection, line, model_id,
               model_artifact_hash, model_probability, market_probability, edge, units, status, result,
               pnl_units, created_at_utc, decision_payload_json
        from ledger_records
        where lower(sport)='ncaab' and status='settled' and market_type in ('spread','moneyline')
        """
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        dp = json.loads(d.pop("decision_payload_json") or "{}")
        feats = _snapshot(dp).get("features") or {}
        engine = feats.get("margin_engine")
        if d["model_id"] == "ncaab-spread-v7":
            engine = engine or "v7"
        if d["model_id"] == "ncaab-spread-v6" and engine is None:
            engine = "v6" if parse_utc(d["created_at_utc"]) >= V6_FIX_AT else "v6-prefix"
        d.update(
            home=str(dp.get("home_team") or ""),
            away=str(dp.get("away_team") or ""),
            home_score=dp.get("home_score"),
            away_score=dp.get("away_score"),
            neutral=feats.get("neutral_site"),
            cutoff=feats.get("history_cutoff_date"),
            history_games=feats.get("history_games"),
            raw_p=feats.get("model_probability_raw", dp.get("model_probability_raw", d["model_probability"])),
            slug=feats.get("research_quote_join.market_slug"),
            provenance=dp.get("market_quote_provenance") or "unknown",
            engine=engine or "other",
        )
        out.append(d)
    return out


def dedup(rows: list[dict[str, Any]], mode: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if parse_utc(r["created_at_utc"]) < parse_utc(r["event_start_utc"]):
            groups[(str(r["event_id"]), r["market_type"])].append(r)
    chosen, conflicts = [], []
    for key, rs in groups.items():
        rs.sort(key=lambda r: (r["created_at_utc"], r["ledger_tier"] == "flat"))
        pick = rs[-1] if mode == "latest" else rs[0]
        chosen.append(pick)
        variants = {(r["selection"], r["line"]) for r in rs}
        if len(variants) > 1:
            conflicts.append(
                {
                    "event_id": key[0],
                    "market": key[1],
                    "game": f"{pick['away']} @ {pick['home']}",
                    "rows": [
                        {
                            "tier": r["ledger_tier"],
                            "model": r["model_id"],
                            "created": r["created_at_utc"],
                            "selection": r["selection"],
                            "line": r["line"],
                            "result": r["result"],
                        }
                        for r in rs
                    ],
                }
            )
    chosen.sort(key=lambda r: (r["event_start_utc"], r["event_id"], r["market_type"]))
    return chosen, conflicts


# ----------------------------------------------------------------------------------------- quotes


def load_quotes(event_ids: set[str]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in sorted(QUOTES.glob("*/polymarket_snapshots.jsonl")):
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                m = re.search(r'"event_id":"([^"]+)"', line)
                if not m or m.group(1) not in event_ids or '"market_type":"total"' in line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("market_type") not in ("spread", "moneyline") or row.get("reconstructed"):
                    continue
                try:
                    row["_obs"] = parse_utc(str(row["observed_at_utc"]))
                except (KeyError, ValueError):
                    continue
                index[m.group(1)].append(row)
    return index


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower().replace("&", " ").replace("-", " ")).strip()


def _team_side(team: str, home: str, away: str) -> str | None:
    t = _norm(team)
    if not t:
        return None
    hits = [
        side
        for side, name in (("home", home), ("away", away))
        if (n := _norm(name)) == t or n.startswith(t + " ")
    ]
    return hits[0] if len(hits) == 1 else None


def _ask(book: dict[str, Any] | None) -> float | None:
    try:
        a = float((book or {})["ask"])
    except (KeyError, TypeError, ValueError):
        return None
    return a if 0.0 < a < 1.0 else None


def price_sides(r: dict[str, Any], quotes: list[dict[str, Any]]) -> dict[str, Any]:
    """{'home': ask, 'away': ask, 'source': ...} for the decision, from the latest pre-decision BBO."""
    decided = parse_utc(r["created_at_utc"])
    start = parse_utc(r["event_start_utc"])
    market = r["market_type"]
    line = float(r["line"]) if r["line"] is not None else None
    cands = [q for q in quotes if q["market_type"] == market and q["_obs"] <= decided and q["_obs"] < start]
    if market == "spread" and line is not None:
        if r["slug"]:
            by_slug = [q for q in cands if q.get("market_slug") == r["slug"]]
            cands = by_slug or cands
        cands = [
            q for q in cands if q.get("line") is not None and abs(abs(float(q["line"])) - abs(line)) < 1e-6
        ]
    if cands:
        q = max(cands, key=lambda q: q["_obs"])
        long_ask, short_ask = _ask(q.get("long")), _ask(q.get("short"))
        long_side: str | None = None
        if market == "spread" and line is not None and abs(line) > 1e-9:
            try:
                long_line = float(str((q.get("long") or {}).get("description")).replace("+", ""))
            except ValueError:
                long_line = None
            if long_line is not None:
                if abs(long_line - line) < 1e-6:
                    long_side = r["selection"]
                elif abs(long_line + line) < 1e-6:
                    long_side = "home" if r["selection"] == "away" else "away"
        if long_side is None:
            long_side = _team_side(str(q.get("team") or ""), r["home"], r["away"])
        if long_side is not None and long_ask is not None and short_ask is not None:
            other = "home" if long_side == "away" else "away"
            return {
                long_side: long_ask,
                other: short_ask,
                "source": "archived_bbo",
                "quote_observed_at": q["observed_at_utc"],
                "quote_age_min": round((decided - q["_obs"]).total_seconds() / 60.0, 1),
            }
    return {"source": "complement"}


# ----------------------------------------------------------------------------------------- scores


def _scoreboard_events(paths: list[Path]) -> list[tuple[str, str, str, int | None, int | None, bool, bool]]:
    """(home, away, ET day, home score, away score, final, neutral) from ESPN scoreboard JSON files."""
    out = []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for event in payload.get("events", []):
            comp = (event.get("competitions") or [{}])[0]
            side = {c.get("homeAway"): c for c in comp.get("competitors", [])}
            if "home" not in side or "away" not in side or not event.get("date"):
                continue
            day = parse_utc(str(event["date"])).astimezone(EASTERN).date().isoformat()
            final = bool(((event.get("status") or {}).get("type") or {}).get("completed"))

            def score(c: dict[str, Any]) -> int | None:
                try:
                    return int(c.get("score"))
                except (TypeError, ValueError):
                    return None

            out.append(
                (
                    str((side["home"].get("team") or {}).get("displayName")),
                    str((side["away"].get("team") or {}).get("displayName")),
                    day,
                    score(side["home"]),
                    score(side["away"]),
                    final,
                    bool(comp.get("neutralSite", False)),
                )
            )
    return out


def neutral_site_map() -> dict[tuple[str, str, str], bool]:
    """(home, away, ET day) -> neutralSite from the archived ESPN scoreboards, both orientations."""
    out: dict[tuple[str, str, str], bool] = {}
    for home, away, day, *_rest, neutral in _scoreboard_events(
        sorted((DATA / "raw" / "ncaab").glob("*/scores_ncaab.json"))
    ):
        out[(home, away, day)] = neutral
        out.setdefault((away, home, day), neutral)
    return out


class Scores:
    def __init__(self, store: FeatureStore, extra_scoreboards: list[Path] | None = None) -> None:
        self.by_key: dict[tuple[str, str, str], GameRecord] = {}
        for g in store.load_games("ncaab"):
            day = g.start.astimezone(EASTERN).date().isoformat()
            self.by_key[(g.home_team, g.away_team, day)] = g
        # finals not yet in the ingested history (e.g. today's slate), from a fresh public ESPN scoreboard
        self.extra: dict[tuple[str, str, str], tuple[int, int]] = {}
        for home, away, day, hs, as_, final, _n in _scoreboard_events(extra_scoreboards or []):
            if final and hs is not None and as_ is not None:
                self.extra[(home, away, day)] = (hs, as_)

    def final(self, r: dict[str, Any]) -> tuple[int | None, int | None, str]:
        day = parse_utc(r["event_start_utc"]).astimezone(EASTERN).date().isoformat()
        g = self.by_key.get((r["home"], r["away"], day))
        if g is not None:
            return int(g.home_score), int(g.away_score), "espn_history"
        g = self.by_key.get((r["away"], r["home"], day))
        if g is not None:  # neutral site listed the other way round by ESPN
            return int(g.away_score), int(g.home_score), "espn_history_swapped"
        if (r["home"], r["away"], day) in self.extra:
            hs, as_ = self.extra[(r["home"], r["away"], day)]
            return hs, as_, "espn_scoreboard_fetch"
        if (r["away"], r["home"], day) in self.extra:
            as_, hs = self.extra[(r["away"], r["home"], day)]
            return hs, as_, "espn_scoreboard_fetch_swapped"
        try:
            return int(r["home_score"]), int(r["away_score"]), "ledger_payload"
        except (TypeError, ValueError):
            return None, None, "missing"


def outcome(market: str, side: str, away_line: float | None, hs: int, as_: int) -> float:
    """1 win, 0 loss, 0.5 push, for ``side`` of the contract."""
    margin = hs - as_
    if market == "moneyline":
        won = margin > 0 if side == "home" else margin < 0
        return 1.0 if won else 0.0
    assert away_line is not None
    if margin == away_line:
        return 0.5
    away_covers = margin < away_line
    return 1.0 if (away_covers if side == "away" else not away_covers) else 0.0


# ----------------------------------------------------------------------------------------- pricing


class FallbackProbe:
    """Wraps the v7 serving object so an exception inside v7 is counted instead of silently served as v6."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.failures: list[str] = []

    def margins(self, history: Any, upcoming: Any) -> Any:
        try:
            return self.inner.margins(history, upcoming)
        except Exception as exc:
            self.failures.append(repr(exc)[:200])
            raise


def price_all(rows: list[dict[str, Any]], store: FeatureStore) -> dict[str, Any]:
    from model_prediction.models.ncaab_v7_serving import default_serving

    v6, v7 = ncaab_spread_v6_model(), ncaab_spread_v7_model()
    probe = FallbackProbe(default_serving())
    v7.v7_serving = probe
    d1 = set(v6.team_priors)
    batches: dict[tuple[str, str], list[tuple[dict[str, Any], UpcomingGame]]] = defaultdict(list)
    hist_cache: dict[str, list[GameRecord]] = {}
    for r in rows:
        created = parse_utc(r["created_at_utc"])
        cutoff = str(r["cutoff"] or created.astimezone(EASTERN).date().isoformat())
        r["cutoff_used"] = cutoff
        line = float(r["line"]) if r["line"] is not None else None
        away_line = None
        if r["market_type"] == "spread" and line is not None:
            away_line = line if r["selection"] == "away" else -line
        r["away_line"] = away_line
        r["d1"] = r["home"] in d1 and r["away"] in d1
        game = UpcomingGame(
            str(r["pick_id"]),
            str(r["event_start_utc"]),
            r["away"],
            r["home"],
            spread_away_line=away_line,
            neutral_site=bool(r["neutral"]),
            league="NCAAB",
        )
        day = parse_utc(r["event_start_utc"]).astimezone(EASTERN).date().isoformat()
        batches[(cutoff, day)].append((r, game))

    preds: dict[tuple[str, str, str], Any] = {}
    batch_log = []
    for (cutoff, day), items in sorted(batches.items()):
        if cutoff not in hist_cache:
            hist_cache[cutoff] = store.games_before("ncaab", cutoff)
        hist = hist_cache[cutoff]
        games = [g for _r, g in items]
        before = len(probe.failures)
        for label, model in (("v6", v6), ("v7", v7)):
            for p in model.predict_games(hist, games):
                preds[(label, p.event_id, p.market_type)] = p
        engines = Counter(
            preds[("v7", g.event_id, "moneyline")].feature_basis.get("margin_engine") for g in games
        )
        batch_log.append(
            {
                "cutoff": cutoff,
                "game_day": day,
                "rows": len(items),
                "history_games": len(hist),
                "v7_engine_counts": dict(engines),
                "v7_failures": probe.failures[before:],
            }
        )
        print(f"  batch cutoff {cutoff} day {day}: {len(items)} rows, v7 engines {dict(engines)}", flush=True)
    for r in rows:
        for label in MODELS:
            pm = preds[(label, str(r["pick_id"]), "moneyline")]
            ps = preds.get((label, str(r["pick_id"]), "spread"))
            fb = pm.feature_basis
            r[f"{label}_engine"] = fb.get("margin_engine")
            r[f"{label}_sample"] = fb.get("current_season_sample")
            r[f"{label}_mean"] = float(pm.inference_inputs["mean"])
            r[f"{label}_scale"] = float(pm.inference_inputs["scale"])
            if r["market_type"] == "moneyline":
                r[f"{label}_p"] = {
                    "home": float(pm.probabilities["home"]),
                    "away": float(pm.probabilities["away"]),
                }
            else:
                assert ps is not None
                r[f"{label}_p"] = {
                    "home": float(ps.probabilities["home"]),
                    "away": float(ps.probabilities["away"]),
                }
                r[f"{label}_spread_mean"] = float(ps.inference_inputs["mean"])
        if r["market_type"] == "moneyline":
            # v7m: P_t8(margin >= 1) under v7's served distribution (Elo weight 0)
            p_home = 1.0 - v7._margin_cdf(0.5, r["v7_mean"], r["v7_scale"])
            p_home = max(0.0001, min(0.9999, p_home))
            r["v7m_p"] = {"home": p_home, "away": 1.0 - p_home}
    return {"batches": batch_log, "v7_failures_total": len(probe.failures), "units_model": v6}


# ----------------------------------------------------------------------------------------- calling rule


def decide(r: dict[str, Any], label: str, units_model: Any) -> dict[str, Any] | None:
    """The live NCAAB calling rule for one model on one contract; None = no call."""
    if not r["d1"]:
        return None
    probs = r[f"{label}_p"]
    asks = r["asks"]
    market = r["market_type"]
    if market == "moneyline":
        side = max(("home", "away"), key=lambda s: probs[s])
        if probs[side] < 0.5 or asks.get(side) is None:
            return None
        edge = probs[side] - asks[side]
        if edge <= 0:
            return None
    else:
        sides = [s for s in ("home", "away") if probs[s] >= 0.5 and asks.get(s) is not None]
        if not sides:
            return None
        side = max(sides, key=lambda s: probs[s] - asks[s])
        edge = probs[side] - asks[side]
        mean = r.get(f"{label}_spread_mean", r[f"{label}_mean"])
        if r["away_line"] is not None and abs(mean - r["away_line"]) > MAX_MARGIN_DIVERGENCE:
            return None
    p, ask = probs[side], asks[side]
    units = float(units_model.compute_dynamic_units(edge, min_edge=MIN_EDGE)) or 1.0
    if p >= HIGH_P_FLOOR:
        units = min(units, HIGH_P_CAP)
    # Live placeholder rule: no executable quote and the slate shows exactly 50% -> zero stake. It can only be
    # applied where the replay prices off the logged indicative ask (complement rows, logged side).
    placeholder = (
        r["price_source"] == "complement"
        and side == r["selection"]
        and abs(ask - 0.5) < 1e-9
        and r["provenance"] != "archived_two_sided_bbo"
    )
    if placeholder:
        units = 0.0
    side_line = None
    if market == "spread":
        side_line = r["away_line"] if side == "away" else -r["away_line"]
    sample = r.get(f"{label}_sample")
    # qualified = the research/gated-ledger admission (min edge, ask floor, line cap); main adds the
    # current-season sample floor, which no team reaches before late November.
    qualified = (
        edge >= MIN_EDGE
        and not (market == "moneyline" and ask < MIN_ML_ASK)
        and not (market == "spread" and side_line is not None and abs(side_line) > MAX_SPREAD_LINE)
    )
    main = qualified and isinstance(sample, int) and sample >= MIN_TEAM_GAMES
    res = r["outcome"][side]

    def pnl(stake: float) -> float:
        return stake * (1.0 / ask - 1.0) if res == 1.0 else (-stake if res == 0.0 else 0.0)

    return {
        "side": side,
        "line": side_line,
        "p": round(p, 6),
        "ask": ask,
        "edge": round(edge, 6),
        "units": units,
        "qualified": qualified,
        "main": main,
        "placeholder_zero_stake": placeholder,
        "result": {1.0: "win", 0.0: "loss", 0.5: "push"}[res],
        "pnl_dyn": round(pnl(units), 4),
        "pnl_u1": round(pnl(1.0), 4),
        "opp_priced_by_complement": r["price_source"] == "complement" and side != r["selection"],
    }


# ----------------------------------------------------------------------------------------- stats


def agg(bets: list[dict[str, Any]]) -> dict[str, Any]:
    w = sum(b["result"] == "win" for b in bets)
    lo = sum(b["result"] == "loss" for b in bets)
    pu = sum(b["result"] == "push" for b in bets)
    out: dict[str, Any] = {"bets": len(bets), "w": w, "l": lo, "push": pu}
    for st in STAKES:
        staked = sum(b["units"] if st == "dyn" else 1.0 for b in bets)
        pnl = sum(b[f"pnl_{st}"] for b in bets)
        out[f"pnl_{st}"] = round(pnl, 2)
        out[f"staked_{st}"] = round(staked, 2)
        out[f"roi_{st}"] = round(pnl / staked, 4) if staked else None
    out["mean_edge"] = round(statistics.mean(b["edge"] for b in bets), 4) if bets else None
    out["mean_ask"] = round(statistics.mean(b["ask"] for b in bets), 4) if bets else None
    return out


def scoring(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    """Brier / log loss of P(home covers | home wins) vs the de-vigged two-sided BBO."""
    sub = [r for r in rows if r["price_source"] == "archived_bbo" and r["outcome"]["home"] in (0.0, 1.0)]
    if not sub:
        return {"n": 0}

    def ll(p: float, y: float) -> float:
        p = min(max(p, 1e-6), 1 - 1e-6)
        return -(y * math.log(p) + (1 - y) * math.log(1 - p))

    def devig(r: dict[str, Any]) -> float:
        h, a = r["asks"]["home"], r["asks"]["away"]
        return h / (h + a)

    out: dict[str, Any] = {"n": len(sub)}
    for lab, f in (
        ("market", devig),
        (key, lambda r: r[f"{key}_p"]["home"] / (r[f"{key}_p"]["home"] + r[f"{key}_p"]["away"])),
    ):
        ps = [f(r) for r in sub]
        ys = [r["outcome"]["home"] for r in sub]
        out[f"{lab}_brier"] = round(sum((p - y) ** 2 for p, y in zip(ps, ys, strict=True)) / len(sub), 5)
        out[f"{lab}_logloss"] = round(sum(ll(p, y) for p, y in zip(ps, ys, strict=True)) / len(sub), 5)
    return out


def bootstrap(rows: list[dict[str, Any]], tier: str, stake: str, reps: int = 10000) -> dict[str, Any]:
    """Paired day-clustered bootstrap of ROI(v7) - ROI(v6) and PnL(v7) - PnL(v6)."""
    by_day: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: {"v6p": [], "v6s": [], "v7p": [], "v7s": []}
    )
    for r in rows:
        for m in MODELS:
            b = r["calls"][m]
            if b is None or (tier != "flat" and not b[tier]):
                continue
            by_day[r["day"]][f"{m}p"].append(b[f"pnl_{stake}"])
            by_day[r["day"]][f"{m}s"].append(b["units"] if stake == "dyn" else 1.0)
    days = sorted(by_day)
    if not days:
        return {}
    tot = {k: [sum(by_day[d][k]) for d in days] for k in ("v6p", "v6s", "v7p", "v7s")}
    rng = random.Random(20261121)
    droi, dpnl = [], []
    for _ in range(reps):
        idx = [rng.randrange(len(days)) for _ in days]
        s = {k: sum(tot[k][i] for i in idx) for k in tot}
        if s["v6s"] and s["v7s"]:
            droi.append(s["v7p"] / s["v7s"] - s["v6p"] / s["v6s"])
        dpnl.append(s["v7p"] - s["v6p"])
    droi.sort()
    dpnl.sort()

    def q(xs: list[float], a: float) -> float:
        return round(xs[min(len(xs) - 1, int(a * len(xs)))], 4)

    point_roi = (sum(tot["v7p"]) / sum(tot["v7s"]) if sum(tot["v7s"]) else 0) - (
        sum(tot["v6p"]) / sum(tot["v6s"]) if sum(tot["v6s"]) else 0
    )
    return {
        "days": len(days),
        "roi_diff": round(point_roi, 4),
        "roi_diff_ci95": [q(droi, 0.025), q(droi, 0.975)],
        "p_roi_diff_le_0": round(sum(x <= 0 for x in droi) / len(droi), 4),
        "pnl_diff": round(sum(tot["v7p"]) - sum(tot["v6p"]), 2),
        "pnl_diff_ci95": [q(dpnl, 0.025), q(dpnl, 0.975)],
    }


# ----------------------------------------------------------------------------------------- tracker


def tracker_reconcile(ledger_all: list[dict[str, Any]], scores: Scores) -> dict[str, Any]:
    if not TRACKER.exists():
        return {"available": False}
    with TRACKER.open(newline="", encoding="utf-8-sig") as fh:
        rows = [r for r in csv.DictReader(fh) if r["model_id"].lower().startswith("ncaab")]
    grades = (
        json.loads((WIDGET / "grades.json").read_text(encoding="utf-8"))
        if (WIDGET / "grades.json").exists()
        else {}
    )
    sup = (
        json.loads((WIDGET / "superseded.json").read_text(encoding="utf-8"))
        if (WIDGET / "superseded.json").exists()
        else {}
    )
    # every ledger row (all statuses) to read the engine behind a tracker pick
    conn = sqlite3.connect(f"file:{LEDGER_DB.as_posix()}?mode=ro", uri=True)
    allrows = conn.execute(
        "select model_id, model_artifact_hash, market_type, selection, line, created_at_utc, event_start_utc, status, "
        "decision_payload_json from ledger_records where lower(sport)='ncaab' and market_type in ('spread','moneyline')"
    ).fetchall()
    conn.close()
    by_game: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for mid, h, mt, sel, line, created, start, status, dpj in allrows:
        dp = json.loads(dpj or "{}")
        eng = (_snapshot(dp).get("features") or {}).get("margin_engine")
        by_game[(_norm(str(dp.get("away_team") or "")), _norm(str(dp.get("home_team") or "")))].append(
            {
                "model_id": mid,
                "hash": bool(h),
                "market": mt,
                "selection": sel,
                "line": line,
                "created": created,
                "start": start,
                "status": status,
                "engine": eng or ("v7" if mid == "ncaab-spread-v7" else "v6"),
                "home": dp.get("home_team"),
                "away": dp.get("away_team"),
                "home_score": dp.get("home_score"),
                "away_score": dp.get("away_score"),
            }
        )
    out_rows = []
    for t in rows:
        pick = t["pick"].strip()
        kind = (
            "total"
            if re.match(r"^(over|under)\s", pick, re.IGNORECASE)
            else "ML"
            if pick.lower().endswith("moneyline")
            else "spread"
        )
        if kind == "total":
            continue
        ver = re.search(r"-(v\d+m?)$", t["model_id"])
        label = f"NCAAB {kind} {ver.group(1) if ver else ''}"
        gk = f"{t['slate_date']}|{t['game'].strip().lower()}|{pick.lower()}"
        g = grades.get(gk) or {}
        away_s, home_s = [x.strip() for x in t["game"].split("@")]
        cands = [
            c
            for c in by_game.get((_norm(away_s), _norm(home_s)), [])
            if c["market"] == ("moneyline" if kind == "ML" else "spread")
        ]
        logged = parse_utc(t["logged_at_et"].replace(" ", "T") + ":00-05:00") if t["logged_at_et"] else None
        side = None
        m = re.match(r"^(.+?)\s+([+-][\d.]+)$", pick)
        tok = (m.group(1) if m else re.sub(r"\s+moneyline$", "", pick, flags=re.IGNORECASE)).strip()
        if _norm(tok) == _norm(home_s):
            side = "home"
        elif _norm(tok) == _norm(away_s):
            side = "away"
        line = float(m.group(2)) if m else None
        match = [
            c
            for c in cands
            if c["selection"] == side
            and (line is None or (c["line"] is not None and abs(float(c["line"]) - line) < 1e-6))
            and (logged is None or parse_utc(c["created"]) <= logged)
        ]
        match.sort(key=lambda c: c["created"])
        lr = match[-1] if match else None
        # independent grade from the final score
        indep = None
        if lr is not None:
            fake = {
                "home": lr["home"],
                "away": lr["away"],
                "event_start_utc": lr["start"],
                "home_score": lr["home_score"],
                "away_score": lr["away_score"],
            }
            hs, as_, _src = scores.final(fake)
            if hs is not None and side is not None:
                away_line = None if kind == "ML" else (line if side == "away" else -line)
                o = outcome("moneyline" if kind == "ML" else "spread", side, away_line, hs, as_)
                indep = {1.0: "win", 0.0: "loss", 0.5: "push"}[o]
        out_rows.append(
            {
                "slate_date": t["slate_date"],
                "logged_at_et": t["logged_at_et"],
                "game": t["game"],
                "pick": pick,
                "label": label.strip(),
                "model_id": t["model_id"],
                "edge": t["edge"],
                "stake": t["stake_units"],
                "notes": t["notes"],
                "superseded": gk in sup,
                "widget_result": g.get("res"),
                "widget_pnl": g.get("pnl"),
                "independent_result": indep,
                "ledger_engine": lr["engine"] if lr else None,
                "ledger_model_id": lr["model_id"] if lr else None,
                "ledger_hash": lr["hash"] if lr else None,
            }
        )
    groups: dict[str, Any] = {}
    for lab in sorted({r["label"] for r in out_rows}):
        sub = [r for r in out_rows if r["label"] == lab and not r["superseded"]]
        graded = [r for r in sub if r["widget_result"] in ("win", "loss", "push")]
        groups[lab] = {
            "rows_counted": len(sub),
            "graded": len(graded),
            "w": sum(r["widget_result"] == "win" for r in graded),
            "l": sum(r["widget_result"] == "loss" for r in graded),
            "pnl": round(sum(float(r["widget_pnl"] or 0) for r in graded), 2),
            "slate_dates": sorted({r["slate_date"] for r in graded}),
            "engines": dict(Counter(r["ledger_engine"] for r in graded)),
            "grade_mismatches": [
                {k: r[k] for k in ("slate_date", "game", "pick", "widget_result", "independent_result")}
                for r in graded
                if r["independent_result"] is not None and r["independent_result"] != r["widget_result"]
            ],
            "ungraded_by_independent_check": sum(r["independent_result"] is None for r in graded),
        }
    return {"available": True, "rows": out_rows, "groups": groups}


# ----------------------------------------------------------------------------------------- main


def run(mode: str, extra_scoreboards: list[Path]) -> dict[str, Any]:
    store = FeatureStore(DATA)
    ledger = load_ledger()
    rows, conflicts = dedup(ledger, mode)
    print(
        f"{len(ledger)} settled spread/ML rows -> {len(rows)} game-markets ({len(conflicts)} with conflicting side/line)"
    )
    quotes = load_quotes({str(r["event_id"]) for r in rows})
    scores = Scores(store, extra_scoreboards)
    neutral_map = neutral_site_map()

    skipped: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    overrounds = []
    for r in rows:
        if not r["home"] or not r["away"]:
            skipped.append({"pick_id": r["pick_id"], "reason": "teams_missing"})
            continue
        hs, as_, src = scores.final(r)
        if hs is None:
            skipped.append({"pick_id": r["pick_id"], "reason": "final_score_missing"})
            continue
        r["final"] = {"home": hs, "away": as_, "source": src}
        if r["neutral"] is not None:
            r["neutral_source"] = "logged_features"
        else:
            day = parse_utc(r["event_start_utc"]).astimezone(EASTERN).date().isoformat()
            if (r["home"], r["away"], day) in neutral_map:
                r["neutral"] = neutral_map[(r["home"], r["away"], day)]
                r["neutral_source"] = "espn_scoreboard"
            else:
                r["neutral"] = False
                r["neutral_source"] = "missing_assumed_home_court"
        try:
            phs, pas = int(r["home_score"]), int(r["away_score"])
            r["payload_scores_match"] = (phs, pas) == (hs, as_)
        except (TypeError, ValueError):
            r["payload_scores_match"] = None
        pr = price_sides(r, quotes.get(str(r["event_id"]), []))
        r["price_source"] = pr["source"]
        r["quote_age_min"] = pr.get("quote_age_min")
        if pr["source"] == "archived_bbo":
            r["asks"] = {"home": pr["home"], "away": pr["away"]}
            overrounds.append(pr["home"] + pr["away"] - 1.0)
            r["logged_ask_vs_bbo"] = round(float(r["market_probability"]) - pr[r["selection"]], 4)
            other_side = "home" if r["selection"] == "away" else "away"
            # a logged ask that sits on the OTHER side's archived ask flags a home/away mapping error
            r["logged_ask_closer_to_other_side"] = abs(
                float(r["market_probability"]) - pr[other_side]
            ) + 0.02 < abs(r["logged_ask_vs_bbo"])
        kept.append(r)
    over = statistics.median(overrounds) if overrounds else 0.02
    for r in kept:
        if r["price_source"] == "complement":
            ask = float(r["market_probability"])
            other = "home" if r["selection"] == "away" else "away"
            opp = min(0.99, max(0.01, 1.0 - ask + over))
            r["asks"] = {r["selection"]: ask, other: opp}
        r["day"] = parse_utc(r["event_start_utc"]).astimezone(EASTERN).date().isoformat()
        line_away = None
        if r["market_type"] == "spread":
            line = float(r["line"])
            line_away = line if r["selection"] == "away" else -line
        r["outcome"] = {
            s: outcome(r["market_type"], s, line_away, r["final"]["home"], r["final"]["away"])
            for s in ("home", "away")
        }
        lres = r["result"]
        r["ledger_grade_ok"] = (
            (r["outcome"][r["selection"]] == 1.0) == (lres == "win")
            if r["outcome"][r["selection"]] != 0.5
            else None
        )

    print(f"pricing {len(kept)} rows ({len(skipped)} skipped)")
    meta = price_all(kept, store)
    units_model = meta.pop("units_model")
    for r in kept:
        r["calls"] = {m: decide(r, m, units_model) for m in MODELS}
        if r["market_type"] == "moneyline":
            r["calls"]["v7m"] = decide({**r, "v7_p": r["v7m_p"]}, "v7", units_model)

    # ---- reproduction gate: replayed probability on the logged side vs the logged raw probability
    repro: dict[str, Any] = {}
    for eng, label in (("v6", "v6"), ("v7", "v7")):
        sub = [r for r in kept if r["engine"] == eng]
        diffs = [abs(r[f"{label}_p"][r["selection"]] - float(r["raw_p"])) for r in sub]
        repro[eng] = {
            "n": len(sub),
            "within_1e-4": sum(d <= 1e-4 for d in diffs),
            "within_1e-2": sum(d <= 1e-2 for d in diffs),
            "max_abs_diff": round(max(diffs), 4) if diffs else None,
            "misses": [
                {
                    "pick_id": r["pick_id"],
                    "game": f"{r['away']} @ {r['home']}",
                    "market": r["market_type"],
                    "logged": float(r["raw_p"]),
                    "replay": round(r[f"{label}_p"][r["selection"]], 6),
                    "logged_history_games": r["history_games"],
                    "replay_engine": r[f"{label}_engine"],
                }
                for r, d in zip(sub, diffs, strict=True)
                if d > 1e-4
            ][:40],
        }

    # ---- daily table
    def calls(r: dict[str, Any], m: str, tier: str) -> dict[str, Any] | None:
        b = r["calls"].get(m)
        if b is None or (tier != "flat" and not b[tier]):
            return None
        return b

    def table(all_rows: list[dict[str, Any]], tier: str, stake: str, market: str) -> list[dict[str, Any]]:
        sub_rows = [r for r in all_rows if market == "all" or r["market_type"] == market]
        days = sorted({r["day"] for r in sub_rows})
        cum = {m: 0.0 for m in MODELS}
        out = []
        for d in days:
            drows = [r for r in sub_rows if r["day"] == d]
            row: dict[str, Any] = {"date": d, "priced": len(drows), "d1": sum(r["d1"] for r in drows)}
            for m in MODELS:
                bs = [b for r in drows if (b := calls(r, m, tier))]
                a = agg(bs)
                cum[m] += a[f"pnl_{stake}"]
                row[m] = {
                    "bets": a["bets"],
                    "w": a["w"],
                    "l": a["l"],
                    "push": a["push"],
                    "pnl": a[f"pnl_{stake}"],
                    "cum": round(cum[m], 2),
                }
            both = [r for r in drows if calls(r, "v6", tier) and calls(r, "v7", tier)]
            row["both_bet"] = len(both)
            row["same_side"] = sum(calls(r, "v6", tier)["side"] == calls(r, "v7", tier)["side"] for r in both)
            out.append(row)
        return out

    report: dict[str, Any] = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "dedup_mode": mode,
        "artifact_hashes": {
            "ncaab-spread-v6": units_model.model_artifact_hash,
            "ncaab-spread-v7": ncaab_spread_v7_model().model_artifact_hash,
        },
        "n_ledger_rows": len(ledger),
        "n_game_markets": len(rows),
        "n_priced": len(kept),
        "skipped": skipped,
        "conflicts": conflicts,
        "price_sources": dict(Counter(r["price_source"] for r in kept)),
        "median_overround": round(over, 4),
        "logged_ask_vs_bbo": dict(
            Counter(
                "match" if abs(r["logged_ask_vs_bbo"]) < 0.005 else "differs"
                for r in kept
                if r.get("logged_ask_vs_bbo") is not None
            )
        ),
        "logged_ask_closer_to_other_side": [
            {
                "pick_id": r["pick_id"],
                "game": f"{r['away']} @ {r['home']}",
                "market": r["market_type"],
                "selection": r["selection"],
                "logged_ask": r["market_probability"],
                "asks": r["asks"],
            }
            for r in kept
            if r.get("logged_ask_closer_to_other_side")
        ],
        "neutral_sources": dict(Counter(r["neutral_source"] for r in kept)),
        "placeholder_zero_stake_bets": {
            m: sum(1 for r in kept if (b := r["calls"].get(m)) and b["placeholder_zero_stake"])
            for m in MODELS
        },
        "score_sources": dict(Counter(r["final"]["source"] for r in kept)),
        "payload_vs_history_score_mismatch": [
            {
                "pick_id": r["pick_id"],
                "game": f"{r['away']} @ {r['home']}",
                "day": r["day"],
                "history": [r["final"]["home"], r["final"]["away"]],
                "payload": [r["home_score"], r["away_score"]],
            }
            for r in kept
            if r["payload_scores_match"] is False
        ],
        "ledger_grade_mismatch": [
            {
                "pick_id": r["pick_id"],
                "game": f"{r['away']} @ {r['home']}",
                "market": r["market_type"],
                "selection": r["selection"],
                "line": r["line"],
                "ledger_result": r["result"],
                "final": [r["final"]["home"], r["final"]["away"]],
            }
            for r in kept
            if r["ledger_grade_ok"] is False
        ],
        "pushes_in_universe": sum(r["outcome"]["home"] == 0.5 for r in kept),
        "logged_engine_counts": dict(Counter(f"{r['model_id']}|{r['engine']}" for r in kept)),
        "non_d1_rows": sum(not r["d1"] for r in kept),
        "v7_engine_on_d1_rows": dict(Counter(r["v7_engine"] for r in kept if r["d1"])),
        "batches": meta["batches"],
        "v7_failures_total": meta["v7_failures_total"],
        "reproduction": repro,
        "daily": {},
        "totals": {},
        "scoring": {},
        "bootstrap": {},
    }
    for market in ("spread", "moneyline", "all"):
        for tier in TIERS:
            for stake in STAKES:
                report["daily"][f"{market}_{tier}_{stake}"] = table(kept, tier, stake, market)
    for market in ("spread", "moneyline", "all"):
        sub = [r for r in kept if market == "all" or r["market_type"] == market]
        report["totals"][market] = {}
        for tier in TIERS:
            for m in MODELS + (("v7m",) if market == "moneyline" else ()):
                bs = [b for r in sub if (b := calls(r, m, tier))]
                report["totals"][market][f"{tier}_{m}"] = agg(bs)
                report["totals"][market][f"{tier}_{m}_excl_complement_priced"] = agg(
                    [b for b in bs if not b["opp_priced_by_complement"]]
                )
                report["totals"][market][f"{tier}_{m}_bbo_priced_only"] = agg(
                    [b for r in sub if r["price_source"] == "archived_bbo" and (b := calls(r, m, tier))]
                )
            both = [r for r in sub if calls(r, "v6", tier) and calls(r, "v7", tier)]
            report["totals"][market][f"{tier}_agreement"] = {
                "both_bet": len(both),
                "same_side": sum(calls(r, "v6", tier)["side"] == calls(r, "v7", tier)["side"] for r in both),
                "only_v6": sum(1 for r in sub if calls(r, "v6", tier) and not calls(r, "v7", tier)),
                "only_v7": sum(1 for r in sub if calls(r, "v7", tier) and not calls(r, "v6", tier)),
            }
            for stake in STAKES:
                report["bootstrap"][f"{market}_{tier}_{stake}"] = bootstrap(sub, tier, stake)
        if market != "all":
            d1 = [r for r in sub if r["d1"]]
            report["scoring"][market] = {
                m: scoring(d1, m) for m in MODELS + (("v7m",) if market == "moneyline" else ())
            }
            report["scoring"][market]["d1_since_2026-11-16"] = {
                m: scoring([r for r in d1 if r["day"] >= "2026-11-16"], m) for m in MODELS
            }
    # Windows: from 2026-11-12 the v6 artifact is out of sample (its round-2 holdout ran to 11-11 and was
    # mostly this season's first fortnight, which flatters v6 before that date); from 2026-11-16 the
    # logged rows are the v7 engine's own picks.
    report["windows"] = {}
    for lo, why in (("2026-11-12", "v6 artifact out of sample"), ("2026-11-16", "v7 engine serving")):
        late = [r for r in kept if r["day"] >= lo]
        blk: dict[str, Any] = {"why": why}
        for mk in ("spread", "moneyline"):
            sub = [r for r in late if r["market_type"] == mk]
            for tier in ("flat", "qualified"):
                for m in MODELS:
                    blk[f"{mk}_{tier}_{m}"] = agg([b for r in sub if (b := calls(r, m, tier))])
                for stake in STAKES:
                    blk[f"{mk}_{tier}_{stake}_bootstrap"] = bootstrap(sub, tier, stake)
        report["windows"][f"since_{lo}"] = blk
    # as logged: the ledger's own settled record of the deduplicated decisions, by model id and engine
    as_logged: dict[str, Any] = {}
    for key in sorted(
        {
            f"{r['model_id']}|{r['engine']}|hash={bool(r['model_artifact_hash'])}|{r['market_type']}"
            for r in kept
        }
    ):
        sub = [
            r
            for r in kept
            if f"{r['model_id']}|{r['engine']}|hash={bool(r['model_artifact_hash'])}|{r['market_type']}"
            == key
        ]
        eng = sub[0]["engine"]
        label = "v7" if eng == "v7" else "v6"
        rep = [b for r in sub if (b := calls(r, label, "flat"))]
        as_logged[key] = {
            "n": len(sub),
            "logged_w": sum(r["result"] == "win" for r in sub),
            "logged_l": sum(r["result"] == "loss" for r in sub),
            "logged_pnl_units": round(sum(float(r["pnl_units"] or 0) for r in sub), 2),
            "logged_pnl_1u_at_logged_ask": round(
                sum((1 / float(r["market_probability"]) - 1) if r["result"] == "win" else -1.0 for r in sub),
                2,
            ),
            "replay_engine": label
            if eng in ("v6", "v7")
            else f"{label} (logged by {eng}; not the same model)",
            "replay_flat": agg(rep),
            "replay_same_side_as_logged": sum(
                1 for r in sub if (b := calls(r, label, "flat")) and b["side"] == r["selection"]
            ),
            # gap decomposition for the same-side picks: stake size, price source, and 0U placeholder rows
            "logged_zero_unit_rows": sum(1 for r in sub if float(r["units"] or 0) == 0.0),
            "logged_staked_units": round(sum(float(r["units"] or 0) for r in sub), 2),
            "same_side_replay_staked_units": round(
                sum(
                    b["units"] for r in sub if (b := calls(r, label, "flat")) and b["side"] == r["selection"]
                ),
                2,
            ),
            "same_side_replay_1u_at_replay_price": round(
                sum(
                    b["pnl_u1"] for r in sub if (b := calls(r, label, "flat")) and b["side"] == r["selection"]
                ),
                2,
            ),
            "same_side_logged_1u_at_logged_ask": round(
                sum(
                    ((1 / float(r["market_probability"]) - 1) if r["result"] == "win" else -1.0)
                    for r in sub
                    if (b := calls(r, label, "flat")) and b["side"] == r["selection"]
                ),
                2,
            ),
        }
    report["as_logged"] = as_logged
    report["tracker"] = tracker_reconcile(ledger, scores)
    report["rows"] = [
        {
            k: r.get(k)
            for k in (
                "pick_id",
                "event_id",
                "day",
                "event_start_utc",
                "created_at_utc",
                "market_type",
                "home",
                "away",
                "neutral",
                "d1",
                "selection",
                "line",
                "away_line",
                "model_id",
                "engine",
                "raw_p",
                "result",
                "pnl_units",
                "market_probability",
                "price_source",
                "quote_age_min",
                "asks",
                "final",
                "outcome",
                "v6_p",
                "v7_p",
                "v7m_p",
                "v6_engine",
                "v7_engine",
                "v6_sample",
                "v7_sample",
                "calls",
                "cutoff_used",
            )
        }
        for r in kept
    ]
    return report


# ----------------------------------------------------------------------------------------- markdown


def pct(x: float | None, nd: int = 1) -> str:
    return "n/a" if x is None else f"{100 * x:+.{nd}f}%"


def fmt_agg(a: dict[str, Any], stake: str = "dyn") -> str:
    if not a or not a.get("bets"):
        return "0 bets"
    return (
        f"{a['bets']} bets {a['w']}-{a['l']}{'-' + str(a['push']) if a['push'] else ''}, "
        f"{a[f'pnl_{stake}']:+.2f}U (ROI {pct(a[f'roi_{stake}'])}), mean edge {100 * a['mean_edge']:.1f}%, "
        f"mean ask {100 * a['mean_ask']:.1f}c"
    )


def write_markdown(rep: dict[str, Any], path: Path) -> None:
    md: list[str] = []
    a = md.append
    a("# NCAAB spread v7 vs v6: point-in-time replay of every settled game, day by day\n")
    a(
        f"Generated {rep['generated_at_utc']} by `scripts/replay_ncaab_v7_vs_v6_daily.py` (dedup `{rep['dedup_mode']}`). "
        "Research only: the ledger was opened read-only; nothing in config/, src/ or the ledgers changed.\n"
    )
    a(
        f"- Models: `ncaab-spread-v6` (artifact `{rep['artifact_hashes']['ncaab-spread-v6'][:12]}`) and `ncaab-spread-v7` "
        f"(identity `{rep['artifact_hashes']['ncaab-spread-v7'][:12]}`: same v6 artifact + frozen multi-season margin engine), "
        "both through the production factories and `predict_games`."
    )
    a(
        f"- Universe: {rep['n_ledger_rows']} settled NCAAB spread/moneyline ledger rows (all tiers, all model ids) -> "
        f"{rep['n_game_markets']} game x market decisions (latest settled row before tip); {rep['n_priced']} priced, "
        f"{len(rep['skipped'])} skipped ({dict(Counter(s['reason'] for s in rep['skipped']))})."
    )
    a(
        f"- Prices: {rep['price_sources']}. `archived_bbo` = both sides' asks from the latest Polymarket two-sided quote "
        f"observed at or before the decision; `complement` = logged ask for the logged side, other side = 1 - ask + "
        f"{100 * rep['median_overround']:.1f} pts (median overround). Logged ask vs archived ask on the logged side: {rep['logged_ask_vs_bbo']}."
    )
    a(
        f"- Grading: final scores {rep['score_sources']}; pushes in universe {rep['pushes_in_universe']}; "
        f"ledger result disagrees with the final score on {len(rep['ledger_grade_mismatch'])} rows; payload score differs "
        f"from ESPN history on {len(rep['payload_vs_history_score_mismatch'])} rows."
    )
    a(
        f"- Neutral flag source: {rep['neutral_sources']}. Placeholder rule (0U when the replay prices a side off the "
        f"logged 50% indicative placeholder): {rep['placeholder_zero_stake_bets']} bets zero-staked in the edge-scaled "
        "columns; they still count at 1U in the flat-1U columns."
    )
    a(
        f"- Side mapping check: logged ask sits on the other side's archived ask on {len(rep['logged_ask_closer_to_other_side'])} "
        "BBO-priced rows (would indicate a home/away mapping error in the replay or the ledger)."
    )
    a(
        f"- Non-D1 games (declined by the live rule for both models; v7 has no engine there): {rep['non_d1_rows']}. "
        f"v7 engine on D1 rows: {rep['v7_engine_on_d1_rows']}; v7 exceptions caught: {rep['v7_failures_total']}."
    )
    a(
        "- Calling rule = `college_basketball_forward`: **flat** = favoured side (p >= 50%), spread best edge with no hurdle "
        "and a 12-point divergence gate, moneyline edge > 0; **qualified** (research/gated admission) = flat + edge >= 3.5%, "
        "|line| <= 16, moneyline ask >= 20%; **main** = qualified + model current-season sample >= 8 (no team reaches 8 before "
        "late November, so main is empty this season so far). Stakes `compute_dynamic_units` (1.0-2.0U, 1U cap at p >= 80%) unless marked 1U. "
        "Raw probabilities (NCAAB calibrators serve identity).\n"
    )

    a("## Reproduction gate (replayed probability on the logged side vs the logged raw probability)\n")
    for eng, g in rep["reproduction"].items():
        a(
            f"- logged engine {eng}: n {g['n']}, within 0.01% {g['within_1e-4']}, within 1 pt {g['within_1e-2']}, max |diff| {g['max_abs_diff']}"
        )
    a("")

    daily_order = (
        [("spread", t, st) for t in ("flat", "qualified") for st in STAKES]
        + [("moneyline", t, "dyn") for t in ("flat", "qualified")]
        + [("all", "flat", "dyn")]
    )
    for market, tier, stake in daily_order:
        if True:
            a(f"## Daily, {market}, {tier} rule, {'edge-scaled 1.0-2.0U' if stake == 'dyn' else 'flat 1U'}\n")
            a(
                "| date | game-markets priced (D1) | v6 bets | v6 W-L | v6 U | v6 cum | v7 bets | v7 W-L | v7 U | v7 cum | both bet | same side |"
            )
            a("|---|---|---|---|---|---|---|---|---|---|---|---|")
            for d in rep["daily"][f"{market}_{tier}_{stake}"]:
                v6, v7 = d["v6"], d["v7"]
                a(
                    f"| {d['date']} | {d['priced']} ({d['d1']}) | {v6['bets']} | {v6['w']}-{v6['l']}{'-' + str(v6['push']) if v6['push'] else ''} | "
                    f"{v6['pnl']:+.2f} | {v6['cum']:+.2f} | {v7['bets']} | {v7['w']}-{v7['l']}{'-' + str(v7['push']) if v7['push'] else ''} | "
                    f"{v7['pnl']:+.2f} | {v7['cum']:+.2f} | {d['both_bet']} | {d['same_side']} |"
                )
            a("")

    a("## Totals by market\n")
    for market, blk in rep["totals"].items():
        a(f"### {market}\n")
        for tier in TIERS:
            for m in MODELS + (("v7m",) if market == "moneyline" else ()):
                agg_ = blk[f"{tier}_{m}"]
                a(
                    f"- {tier} {m}: {fmt_agg(agg_)}; 1U: {agg_.get('pnl_u1', 0):+.2f}U (ROI {pct(agg_.get('roi_u1'))})"
                )
                a(f"  - BBO-priced rows only: {fmt_agg(blk[f'{tier}_{m}_bbo_priced_only'])}")
            ag = blk[f"{tier}_agreement"]
            a(
                f"- {tier} agreement: both bet {ag['both_bet']}, same side {ag['same_side']}, only v6 {ag['only_v6']}, only v7 {ag['only_v7']}"
            )
            for stake in STAKES:
                b = rep["bootstrap"].get(f"{market}_{tier}_{stake}") or {}
                if b:
                    a(
                        f"- {tier} {stake} v7 - v6: ROI {pct(b['roi_diff'])} (95% CI {pct(b['roi_diff_ci95'][0])} to "
                        f"{pct(b['roi_diff_ci95'][1])}, P(diff <= 0) {b['p_roi_diff_le_0']:.2f}); PnL {b['pnl_diff']:+.2f}U "
                        f"(CI {b['pnl_diff_ci95'][0]:+.2f} to {b['pnl_diff_ci95'][1]:+.2f}); {b['days']} day clusters"
                    )
        a("")
    for wname, blk in rep["windows"].items():
        a(f"### Window {wname} ({blk['why']})\n")
        for mk in ("spread", "moneyline"):
            for tier in ("flat", "qualified"):
                for m in MODELS:
                    v = blk[f"{mk}_{tier}_{m}"]
                    a(f"- {mk} {tier} {m}: {fmt_agg(v)}; 1U {v.get('pnl_u1', 0):+.2f}U")
                for stake in STAKES:
                    b = blk[f"{mk}_{tier}_{stake}_bootstrap"]
                    if b:
                        a(
                            f"  - v7 - v6 {stake}: ROI {pct(b['roi_diff'])} (95% CI {pct(b['roi_diff_ci95'][0])} to "
                            f"{pct(b['roi_diff_ci95'][1])}); PnL {b['pnl_diff']:+.2f}U; {b['days']} days"
                        )
        a("")

    a(
        "## Probability quality (D1 rows with a two-sided archived quote; frame = P(home covers) / P(home wins))\n"
    )
    for market, blk in rep["scoring"].items():
        for m, s in blk.items():
            if m.startswith("d1_since"):
                for mm, ss in s.items():
                    a(f"- {market} {mm} since 11-16: {ss}")
            else:
                a(f"- {market} {m}: {s}")
    a("")

    a("## As logged vs replayed (deduplicated decisions, by logged model id | engine | hash)\n")
    a(
        "| key | n | logged W-L | logged U | logged 1U | replay engine | replay flat | same side | logged 0U rows | "
        "logged staked U | replay staked U (same side) | same side 1U logged ask | same side 1U replay price |"
    )
    a("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for k, v in rep["as_logged"].items():
        a(
            f"| {k} | {v['n']} | {v['logged_w']}-{v['logged_l']} | {v['logged_pnl_units']:+.2f} | {v['logged_pnl_1u_at_logged_ask']:+.2f} | "
            f"{v['replay_engine']} | {fmt_agg(v['replay_flat'])} | {v['replay_same_side_as_logged']} | "
            f"{v['logged_zero_unit_rows']} | {v['logged_staked_units']} | {v['same_side_replay_staked_units']} | "
            f"{v['same_side_logged_1u_at_logged_ask']:+.2f} | {v['same_side_replay_1u_at_replay_price']:+.2f} |"
        )
    a("")

    tr = rep["tracker"]
    if tr.get("available"):
        a("## Best-pick tracker (best_pick_audit.csv + widget grades.json / superseded.json)\n")
        a(
            "| label | counted rows | graded | W-L | widget U | slate dates | ledger engine behind the rows | grade mismatches |"
        )
        a("|---|---|---|---|---|---|---|---|")
        for lab, g in tr["groups"].items():
            a(
                f"| {lab} | {g['rows_counted']} | {g['graded']} | {g['w']}-{g['l']} | {g['pnl']:+.2f} | {', '.join(g['slate_dates'])} | "
                f"{g['engines']} | {len(g['grade_mismatches'])} |"
            )
        a("")

    a("## Caveats\n")
    a(
        "- Most asks are indicative: before 2026-11-13 about two thirds of rows have no archived BBO for the logged line "
        "(complement pricing), and the archived BBOs themselves are not 'executable' by the forward's own test: NCAAB "
        "snapshots carry `transact_time_utc: null`, so `college_contract_evidence._valid_quote` rejects nearly all of them "
        "as `invalid_time` (candidate packets 2026-11-2x: ~1,600-2,500 invalid_time vs 3-14 matched per run). That is why "
        "the ledger's NCAAB rows are `indicative_slate`."
    )
    a(
        "- The v6 artifact's round-2 holdout ran to 2026-11-11 and was mostly this season's opening fortnight, so v6 is "
        "partly in sample before 2026-11-12; the `since_2026-11-12` window is the fair one for v6."
    )
    a(
        "- Dedup sensitivity: `--dedup first` changes only 5 of 1,227 game-markets (settled rows are already one decision "
        "per run; the superseded re-runs are excluded), and the totals are identical to the latest-row dedup."
    )
    a(
        "- 20 day clusters overall, 6-10 in the late windows: every CI is wide and the late-window CIs are not "
        "reliable even where they exclude zero; a 15-bet or one-day record says nothing either way."
    )
    a(
        "- Settlement bug seen in the grade cross-check (pick 53c03c58cbaa4a05, Samford @ South Carolina Upstate, "
        "2026-11-18, 0U row): `cli/settle.py::_find_espn_result` also name-matched UNC Greensboro Spartans @ Gardner-Webb "
        "Runnin' Bulldogs through the inverted branch (mascots 'Spartans'/'Bulldogs'), and the start-time tie-break "
        "picked it because the Polymarket start (23:30Z) sat closer to that game (01:00Z) than to the real one (01:30Z). "
        "The row was settled 78-72 as a win; the real final was 64-74, a loss.\n"
    )

    if rep["conflicts"]:
        a("## Game-markets with conflicting logged side/line (latest kept)\n")
        for c in rep["conflicts"]:
            a(
                f"- {c['game']} {c['market']}: "
                + "; ".join(
                    f"{r['tier']} {r['model']} {r['created'][:16]} {r['selection']} {r['line']} {r['result']}"
                    for r in c["rows"]
                )
            )
        a("")
    if rep["ledger_grade_mismatch"]:
        a("## Ledger result vs final score mismatches\n")
        for m in rep["ledger_grade_mismatch"]:
            a(f"- {m}")
        a("")
    if rep["payload_vs_history_score_mismatch"]:
        a("## Payload score vs ESPN history mismatches\n")
        for m in rep["payload_vs_history_score_mismatch"]:
            a(f"- {m}")
        a("")
    path.write_text("\n".join(md) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dedup", choices=("latest", "first"), default="latest")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument(
        "--extra-scoreboards",
        nargs="*",
        default=[],
        type=Path,
        help="ESPN scoreboard JSON files for finals not yet in the ingested history (graded before payload scores)",
    )
    args = ap.parse_args()
    rep = run(args.dedup, list(args.extra_scoreboards))
    for market in ("spread", "moneyline", "all"):
        for tier in TIERS:
            for m in MODELS:
                print(f"{market:9s} {tier:4s} {m}: {fmt_agg(rep['totals'][market][f'{tier}_{m}'])}")
    if not args.no_write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        suffix = "" if args.dedup == "latest" else "_first"
        (OUT_DIR / f"ncaab_v7_vs_v6_daily{suffix}.json").write_text(
            json.dumps(rep, indent=1, default=str), encoding="utf-8"
        )
        write_markdown(rep, OUT_DIR / f"ncaab_v7_vs_v6_daily{suffix}.md")
        print(f"wrote {OUT_DIR / f'ncaab_v7_vs_v6_daily{suffix}.md'}")


if __name__ == "__main__":
    main()
