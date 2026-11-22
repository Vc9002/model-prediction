"""Polymarket international as a point-in-time reference for Polymarket US, over every settled NBA game.

  python scripts/nba_v10/intl_lead_settled_replay.py [--runtime E:/model-prediction-runtime] [--offline]

Round 1 (quick 261122-tbr) found Polymarket US quotes lag the DraftKings close, but no timestamped
DraftKings history exists for past games. Polymarket's international venue (gamma-api / clob
``prices-history``) lists the same NBA games with far more volume (an NBA moneyline trades millions)
and publishes a minute-level price series, so its price at any past instant is point-in-time
recoverable. If the US venue lags it, buying a US contract that is cheap against the international
price earns money. Fetched data is cached under ``data/research/nba_v10/polymarket_intl/``.

Pinned before any outcome was read (quick 261122-int):

- US decision quote: as in tb_settled_replay, the last snapshot per US market in
  [tip - 120 min, tip - 5 min]; the decision time T is that rung's own observation time.
- International reference: the last ``prices-history`` point (fidelity 1 min) at or before T and
  at most 30 min older than T, for the identical proposition (same team and line, or over/under at
  the same total, or the same moneyline team).
- Signal: international price minus the US ask >= GAP (3.0 points, gross of fee), the T-B
  threshold of plan section 9, not tuned here.
- One pick per game and market type (moneyline / spread / total): the rung-side with the largest gap.
- PnL: 1 unit at the US ask; gross, and net of the 0.06 p (1 - p) fee; weekly by ET Monday.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import httpx
import pandas as pd
from tb_settled_replay import (
    DEFAULT_RUNTIME,
    ET,
    QUOTE_MAX_AGE,
    QUOTE_MIN_LEAD,
    ROOT,
    ask,
    cluster_ci,
    fee,
    finite,
    grade,
    latest_quotes,
    load_games,
    load_snapshots,
    pnl,
)

GAP = 0.030
# Amendment after the first run (data validity, not an outcome filter): every first-run spread
# signal sat on a deep-tail rung (e.g. -29.5) whose international "price" was an untraded 10-18c
# placeholder against a 2-5c US ask; all 31 lost. A reference price only exists where both venues
# quote a contested rung, so both prices must sit inside this band.
PRICE_BAND = (0.15, 0.85)
INTL_MAX_AGE = timedelta(minutes=30)
HISTORY_LOOKBACK = timedelta(hours=3)
CACHE = ROOT / "data/research/nba_v10/polymarket_intl"
GAMMA = "https://gamma-api.polymarket.com/events"
CLOB_HISTORY = "https://clob.polymarket.com/prices-history"
# US slugs use a few short codes the international venue spells out.
INTL_ABBR = {"ny": "nyk", "gs": "gsw", "sa": "sas", "no": "nop", "pho": "phx"}


class Intl:
    def __init__(self, offline: bool) -> None:
        self.offline = offline
        self.client = httpx.Client(timeout=30, headers={"User-Agent": "research-replay"})
        (CACHE / "events").mkdir(parents=True, exist_ok=True)
        (CACHE / "history").mkdir(parents=True, exist_ok=True)

    def _get(self, url: str, params: dict) -> object:
        for attempt in range(4):
            try:
                resp = self.client.get(url, params=params)
                if resp.status_code == 429:
                    time.sleep(2 + 2 * attempt)
                    continue
                resp.raise_for_status()
                time.sleep(0.05)
                return resp.json()
            except httpx.HTTPError:
                time.sleep(1 + attempt)
        return None

    def event(self, us_slug: str) -> dict | None:
        parts = us_slug.split("-")
        away, home = INTL_ABBR.get(parts[1], parts[1]), INTL_ABBR.get(parts[2], parts[2])
        slug = "-".join(["nba", away, home, *parts[3:]])
        path = CACHE / "events" / f"{slug}.json"
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
        elif self.offline:
            return None
        else:
            data = self._get(GAMMA, {"slug": slug})
            if data is None:
                return None
            path.write_text(json.dumps(data), encoding="utf-8")
        return data[0] if data else None

    def history(self, token: str, start: int, end: int) -> list[dict]:
        path = CACHE / "history" / f"{token[:40]}_{start}_{end}.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        if self.offline:
            return []
        data = self._get(CLOB_HISTORY, {"market": token, "startTs": start, "endTs": end, "fidelity": 1})
        hist = (data or {}).get("history", []) if isinstance(data, dict) else []
        path.write_text(json.dumps(hist), encoding="utf-8")
        return hist


def nickname_match(full: str, outcome: str) -> bool:
    return full.lower().endswith(outcome.lower()) or outcome.lower() in full.lower().split()[-1:]


def intl_propositions(event: dict, game: dict) -> dict[tuple, str]:
    """Proposition key -> CLOB token. Keys: ('moneyline', team), ('spread', team, line),
    ('total', 'over'|'under', line); team is the US full name."""
    out: dict[tuple, str] = {}
    teams = (game["home"], game["away"])
    for market in event.get("markets", []):
        kind = market.get("sportsMarketType")
        try:
            outcomes = json.loads(market.get("outcomes") or "[]")
            tokens = json.loads(market.get("clobTokenIds") or "[]")
        except json.JSONDecodeError:
            continue
        if len(outcomes) != 2 or len(tokens) != 2:
            continue
        line = finite(market.get("line"))
        full = []
        for outcome in outcomes:
            match = [t for t in teams if nickname_match(t, outcome)]
            full.append(match[0] if len(match) == 1 else None)
        if kind == "moneyline" and None not in full:
            for name, token in zip(full, tokens, strict=True):
                out[("moneyline", name)] = token
        elif kind == "spreads" and None not in full and line is not None:
            # "Spread: X (l)": outcome 0 is X at l, outcome 1 the other team at -l.
            out[("spread", full[0], line)] = tokens[0]
            out[("spread", full[1], -line)] = tokens[1]
        elif kind == "totals" and line is not None and [o.lower() for o in outcomes] == ["over", "under"]:
            out[("total", "over", line)] = tokens[0]
            out[("total", "under", line)] = tokens[1]
    return out


def us_sides(rec: dict, game: dict) -> list[tuple[tuple, dict]]:
    """(proposition key, side spec) for both sides of a US snapshot rung."""
    home, away = game["home"], game["away"]
    mt = rec["market_type"]
    line = finite(rec.get("line"))
    if mt == "moneyline" and rec.get("team") in (home, away):
        other = away if rec["team"] == home else home
        return [
            (("moneyline", rec["team"]), {"team": rec["team"], "line": None, "ask": ask(rec["long"])}),
            (("moneyline", other), {"team": other, "line": None, "ask": ask(rec["short"])}),
        ]
    if mt == "spread" and rec.get("team") in (home, away) and line is not None:
        other = away if rec["team"] == home else home
        return [
            (("spread", rec["team"], line), {"team": rec["team"], "line": line, "ask": ask(rec["long"])}),
            (("spread", other, -line), {"team": other, "line": -line, "ask": ask(rec["short"])}),
        ]
    if mt == "total" and line is not None:
        return [
            (("total", "over", line), {"team": "over", "line": line, "ask": ask(rec["long"])}),
            (("total", "under", line), {"team": "under", "line": line, "ask": ask(rec["short"])}),
        ]
    return []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runtime", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--offline", action="store_true", help="use only the cache")
    parser.add_argument("--out", default=str(ROOT / "outputs/nba/INTL_LEAD_SETTLED_REPLAY.md"))
    parser.add_argument("--csv", default=str(ROOT / "outputs/nba/intl_lead_settled_picks.csv"))
    args = parser.parse_args()
    runtime = Path(args.runtime)
    games = load_games(runtime / "ledgers/ledgers.db")
    snaps = load_snapshots(runtime)
    by_tip: dict[str, list[str]] = defaultdict(list)
    for slug, recs in snaps.items():
        by_tip[recs[0]["event_start_utc"][:16]].append(slug)
    for game in games.values():
        if game["slug"] in snaps:
            continue
        for slug in by_tip.get(game["tip"].strftime("%Y-%m-%dT%H:%M"), []):
            if {game["home"], game["away"]} & {r.get("team") for r in snaps[slug]}:
                game["slug"] = slug

    intl = Intl(args.offline)
    coverage: dict[str, int] = defaultdict(int)
    rows = []
    for game in sorted(games.values(), key=lambda g: g["tip"]):
        coverage["games"] += 1
        recs = snaps.get(game["slug"] or "")
        if not recs:
            coverage["no_us_event"] += 1
            continue
        quotes = latest_quotes(recs, game["tip"])
        if not quotes:
            coverage["no_us_quote_in_window"] += 1
            continue
        event = intl.event(game["slug"])
        if event is None:
            coverage["no_intl_event"] += 1
            continue
        props = intl_propositions(event, game)
        start = int((game["tip"] - QUOTE_MAX_AGE - HISTORY_LOOKBACK).timestamp())
        end = int((game["tip"] - QUOTE_MIN_LEAD).timestamp())
        needed = {props[k] for rec in quotes for k, _ in us_sides(rec, game) if k in props}
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(intl.history, needed, [start] * len(needed), [end] * len(needed)))
        best: dict[str, dict] = {}
        compared = 0
        for rec in quotes:
            decision = rec["_obs"]
            for key, side in us_sides(rec, game):
                token = props.get(key)
                if token is None or side["ask"] is None:
                    continue
                hist = [h for h in intl.history(token, start, end) if h.get("t") is not None]
                usable = [h for h in hist if h["t"] <= decision.timestamp()]
                if not usable:
                    continue
                last = max(usable, key=lambda h: h["t"])
                if decision.timestamp() - last["t"] > INTL_MAX_AGE.total_seconds():
                    continue
                ref = finite(last.get("p"))
                if ref is None or not 0.0 < ref < 1.0:
                    continue
                lo_band, hi_band = PRICE_BAND
                if not (lo_band <= ref <= hi_band and lo_band <= side["ask"] <= hi_band):
                    continue
                compared += 1
                gap = ref - side["ask"]
                mt = rec["market_type"]
                if mt not in best or gap > best[mt]["gap"]:
                    best[mt] = {"gap": gap, "ref": ref, "side": side, "rec": rec, "key": key}
        if compared == 0:
            coverage["no_intl_match"] += 1
            continue
        coverage["evaluated"] += 1
        tip_et = game["tip"].astimezone(ET).date()
        for mt, b in best.items():
            side, price = b["side"], b["side"]["ask"]
            result = grade(game, mt, side)
            rows.append(
                {
                    "event_id": game["event_id"],
                    "week": (tip_et - timedelta(days=tip_et.weekday())).isoformat(),
                    "matchup": f"{game['away']} @ {game['home']}",
                    "market": mt,
                    "proposition": " ".join(str(x) for x in b["key"]),
                    "intl_price": b["ref"],
                    "us_ask": price,
                    "gap": round(b["gap"], 4),
                    "signal": b["gap"] >= GAP,
                    "quote_minutes_to_tip": round((game["tip"] - b["rec"]["_obs"]).total_seconds() / 60, 1),
                    "result": result,
                    "stake": 1.0,
                    "pnl_gross": pnl(result, price),
                    "pnl_net": pnl(result, price + fee(price)),
                }
            )
    df = pd.DataFrame(rows)
    Path(args.csv).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.csv, index=False)

    def line(name: str, part: pd.DataFrame) -> str:
        if part.empty:
            return f"| {name} | 0 | - | - | - | - |"
        w, lo_ = int((part.result == "win").sum()), int((part.result == "loss").sum())
        ci = cluster_ci(part, "pnl_gross", "stake")
        n = len(part)
        return (
            f"| {name} | {n} | {w}-{lo_} | {part.pnl_gross.sum():+.2f} ({part.pnl_gross.sum() / n:+.1%}) | "
            f"{part.pnl_net.sum():+.2f} ({part.pnl_net.sum() / n:+.1%}) | {ci[0]:+.1%} .. {ci[1]:+.1%} |"
        )

    head = [
        "| slice | picks | W-L | PnL gross (ROI) | PnL net (ROI) | 95% CI ROI (games) |",
        "|---|---|---|---|---|---|",
    ]
    out = ["# Polymarket international vs US, every settled NBA game", ""]
    out += ["```", json.dumps(dict(coverage), indent=1), "```", ""]
    if not df.empty:
        out.append(
            f"Gap (intl price - US ask) over the best rung per game/market: median {df.gap.median():+.3f}, "
            f"p90 {df.gap.quantile(0.9):+.3f}."
        )
        sig = df[df.signal]
        out += ["", f"## Signal picks (gap >= {GAP:.3f})", "", *head, line("ALL signal picks", sig)]
        for mt in ("moneyline", "spread", "total"):
            out.append(line(mt, sig[sig.market == mt]))
        out += [
            "",
            "### By week (ET, Monday start)",
            "",
            "| week | picks | W-L | PnL gross | PnL net |",
            "|---|---|---|---|---|",
        ]
        for week, wk in sig.groupby("week"):
            w, lo_ = int((wk.result == "win").sum()), int((wk.result == "loss").sum())
            out.append(
                f"| {week} | {len(wk)} | {w}-{lo_} | {wk.pnl_gross.sum():+.2f} | {wk.pnl_net.sum():+.2f} |"
            )
        out += ["", "## Context only: best rung per game/market regardless of gap", "", *head]
        out.append(line("all", df))
    Path(args.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
