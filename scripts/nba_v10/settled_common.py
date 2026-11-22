"""Shared, coverage-complete loaders for the settled-game replays (quick 261122-emp).

Rounds 1-7 matched Polymarket events and teams by exact name or exact tip minute and dropped every
game whose last quote was older than 120 minutes, so they scored 141-151 of 219 settled games and
almost nothing in the opening week (October snapshots name teams "Hawks", not "Atlanta Hawks").
This module:

- resolves a snapshot team to the game's home/away by exact name, then prefix, then nickname
  suffix ("Hawks" -> "Atlanta Hawks"), and rewrites ``rec["team"]`` to the ledger's full name;
- maps a ledger game to its Polymarket event by the rationale slug, else by ET date (+/- 1 day)
  and both teams;
- takes the last pre-tip book at any age (tip - 5 min) and reports the age;
- rebuilds every settled NBA ledger row's ask and result from the archive (the harness).
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from tb_settled_replay import (
    ET,
    MARKET_SLUG_RE,
    QUOTE_MIN_LEAD,
    ask,
    finite,
    grade,
    load_games,
    load_snapshots,
    parse_utc,
)

__all__ = ["attach", "decision_book", "harness_all", "load_games", "load_snapshots", "resolve_side"]


def _norm(name: object) -> str:
    return re.sub(r"[^a-z0-9 ]", "", str(name).lower()).strip()


def resolve_side(team: object, game: dict) -> str | None:
    """'home' / 'away' when ``team`` names exactly one side of ``game``."""
    if not team:
        return None
    t = _norm(team)
    h, a = _norm(game["home"]), _norm(game["away"])
    tests = (
        lambda full: full == t,
        lambda full: full.startswith(t + " ") or t.startswith(full + " "),
        lambda full: full.endswith(" " + t),
    )
    for test in tests:
        hits = [side for side, full in (("home", h), ("away", a)) if test(full)]
        if len(hits) == 1:
            return hits[0]
        if hits:
            return None
    return None


def attach(games: dict[str, dict], snaps: dict[str, list[dict]]) -> dict[str, int]:
    """Set ``game['slug']`` and normalise snapshot team names in place; return mapping counts."""
    by_date: dict[str, list[str]] = defaultdict(list)
    for slug, recs in snaps.items():
        start = recs[0].get("event_start_utc")
        if not start:
            continue
        d = parse_utc(start).astimezone(ET).date()
        for off in (-1, 0, 1):
            by_date[(d + timedelta(days=off)).isoformat()].append(slug)
    teams_by_slug = {slug: {r.get("team") for r in recs if r.get("team")} for slug, recs in snaps.items()}
    counts: dict[str, int] = defaultdict(int)
    for game in games.values():
        if game["slug"] in snaps:
            counts["rationale_slug"] += 1
        else:
            game["slug"] = None
            day = game["tip"].astimezone(ET).date().isoformat()
            found = []
            for slug in by_date.get(day, []):
                sides = {resolve_side(t, game) for t in teams_by_slug[slug]}
                if {"home", "away"} <= sides:
                    found.append(slug)
            if not found:
                # Some events carry only one team's spread rungs and no moneyline; a team plays at
                # most once per ET date, so one resolved team on the same date identifies the game.
                for slug in by_date.get(day, []):
                    start = parse_utc(snaps[slug][0]["event_start_utc"]).astimezone(ET).date().isoformat()
                    sides = {resolve_side(t, game) for t in teams_by_slug[slug]} - {None}
                    if start == day and sides and None not in sides:
                        found.append(slug)
            if len(found) == 1:
                game["slug"] = found[0]
                counts["date_team_match"] += 1
            else:
                counts["unmapped" if not found else "ambiguous"] += 1
                continue
        for rec in snaps[game["slug"]]:
            side = resolve_side(rec.get("team"), game)
            if side is not None:
                rec["team"] = game[side]
    return dict(counts)


def decision_book(recs: list[dict], tip: datetime) -> list[dict]:
    """Last open, two-sided snapshot per market observed at or before tip - 5 min (any age)."""
    best: dict[str, dict] = {}
    for rec in recs:
        if rec["_obs"] > tip - QUOTE_MIN_LEAD:
            continue
        if rec.get("market_state") not in (None, "MARKET_STATE_OPEN"):
            continue
        if ask(rec.get("long")) is None or ask(rec.get("short")) is None:
            continue
        key = str(rec.get("market_slug") or rec.get("market_id"))
        if key not in best or rec["_obs"] > best[key]["_obs"]:
            best[key] = rec
    return list(best.values())


def harness_all(ledger: Path, snaps: dict[str, list[dict]], games: dict[str, dict]) -> dict:
    """Every settled NBA row with a quote time and a market slug: ask rebuilt, result regraded."""
    index = {}
    for recs in snaps.values():
        for rec in recs:
            index[(str(rec.get("market_slug")), rec["observed_at_utc"])] = rec
    conn = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    rows = conn.execute(
        """select event_id, market_type, selection, line, market_probability, result, decision_payload_json
           from ledger_records where sport='nba' and status='settled'"""
    ).fetchall()
    conn.close()
    out = {"rows": len(rows), "with_slug_and_time": 0, "snapshot_found": 0, "ask_match": 0, "graded": 0,
           "result_match": 0, "ask_mismatch_examples": [], "result_mismatch_examples": []}  # fmt: skip
    for event_id, market, selection, line, prob, stored, text in rows:
        p = json.loads(text or "{}")
        game = games.get(str(event_id))
        if game is None:
            continue
        team = selection if market == "total" else game["home" if selection == "home" else "away"]
        if stored in ("win", "loss", "push"):
            out["graded"] += 1
            regraded = grade(game, market, {"team": team, "line": finite(line)})
            if regraded == stored:
                out["result_match"] += 1
            elif len(out["result_mismatch_examples"]) < 5:
                out["result_mismatch_examples"].append([event_id, market, selection, line, stored, regraded])
        m = MARKET_SLUG_RE.search(str(p.get("rationale") or ""))
        obs = p.get("market_quote_observed_at_utc")
        if not m or not obs:
            continue
        out["with_slug_and_time"] += 1
        rec = index.get((m.group(1), str(obs)))
        if rec is None:
            continue
        out["snapshot_found"] += 1
        if market == "total":
            side = rec["long"] if selection == "over" else rec["short"]
        else:
            side = rec["long"] if rec.get("team") == team else rec["short"]
        rebuilt = ask(side)
        if rebuilt is not None and prob is not None and abs(rebuilt - float(prob)) <= 0.002:
            out["ask_match"] += 1
        elif len(out["ask_mismatch_examples"]) < 5:
            out["ask_mismatch_examples"].append([event_id, market, selection, prob, rebuilt, rec.get("team")])
    return out
