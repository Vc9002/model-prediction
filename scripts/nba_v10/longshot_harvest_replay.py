"""Favourite-longshot harvest on Polymarket ladders: validated on other sports, scored on settled NBA games.

  python scripts/nba_v10/longshot_harvest_replay.py [--runtime E:/model-prediction-runtime]

Prediction markets are documented to overprice longshots. A Polymarket game lists a ladder of
spread and total rungs plus the moneyline, so every game carries contracts whose favourite side
trades at 85-97c. If the longshot side is overpriced, buying the favourite side of those rungs
wins slightly more often than its price says, many small wins against rare full losses. That rule
needs no NBA model and no fitting, so it is validated first on the other sports' settled games
and only then scored on NBA.

Pinned before any outcome was read (quick 261122-lsh):

- Decision book: per Polymarket market, the last snapshot in [tip - 120 min, tip - 5 min] with
  both asks, market open (as in tb_settled_replay).
- Candidate: a rung side whose ask lies in [FAV_LO, FAV_HI] = [0.85, 0.97].
- One pick per game and market type (moneyline / spread / total): the candidate whose ask is
  nearest FAV_TARGET (0.91).
- PnL: 1u at the ask, gross and net of the 0.06 p (1 - p) fee; weekly by ET Monday.
- Validation sports: MLB, NFL, NCAAF, NCAAB, WNBA settled ledger games (scores from the ledger).
  PASS needs the pooled net ROI 95% game-cluster CI above 0. NBA is reported either way.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import pandas as pd
from tb_settled_replay import (
    DEFAULT_RUNTIME,
    ET,
    ROOT,
    ask,
    cluster_ci,
    fee,
    finite,
    grade,
    latest_quotes,
    parse_utc,
    pnl,
)

FAV_LO, FAV_HI, FAV_TARGET = 0.85, 0.97, 0.91
VALIDATION_SPORTS = ("mlb", "nfl", "ncaaf", "ncaab", "wnba")
SLUG_RE = re.compile(r"[a-z]{3}-((?:nba|wnba|mlb|nfl|cfb|cbb)-[a-z0-9]+-[a-z0-9]+-\d{4}-\d{2}-\d{2})")


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", str(name).lower()).strip()


def team_side(snap_team: str | None, game: dict) -> str | None:
    """'home' / 'away' when the snapshot team names exactly one side (exact, else prefix)."""
    if not snap_team:
        return None
    t = norm(snap_team)
    h, a = norm(game["home"]), norm(game["away"])
    for test in (lambda x: x == t, lambda x: x.startswith(t + " ") or t.startswith(x + " ")):
        hits = [s for s, n in (("home", h), ("away", a)) if test(n)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return None
    return None


def load_games(ledger: Path, sport: str) -> dict[str, dict]:
    conn = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    rows = conn.execute(
        """select event_id, event_start_utc, decision_payload_json from ledger_records
           where sport=? and status='settled'""",
        (sport,),
    ).fetchall()
    conn.close()
    games: dict[str, dict] = {}
    for event_id, start, text in rows:
        p = json.loads(text or "{}")
        hs, as_ = finite(p.get("home_score")), finite(p.get("away_score"))
        if hs is None or as_ is None or not p.get("home_team"):
            continue
        g = games.setdefault(
            str(event_id),
            {
                "event_id": str(event_id),
                "home": p["home_team"],
                "away": p["away_team"],
                "tip": parse_utc(p.get("event_start_utc") or start),
                "home_score": hs,
                "away_score": as_,
                "slug": None,
            },
        )
        m = SLUG_RE.search(str(p.get("rationale") or ""))
        if m and not g["slug"]:
            g["slug"] = m.group(1)
    return games


def load_snapshots(runtime: Path, sport: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for path in sorted((runtime / "odds" / sport).glob("*/polymarket_snapshots.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for raw in handle:
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if rec.get("market_type") not in {"moneyline", "spread", "total"}:
                    continue
                rec["_obs"] = parse_utc(rec["observed_at_utc"])
                out[str(rec.get("event_slug"))].append(rec)
    return out


def candidates(book: list[dict], game: dict) -> dict[str, dict]:
    """Best favourite-side candidate per market type."""
    best: dict[str, dict] = {}
    for rec in book:
        mt = rec["market_type"]
        line = finite(rec.get("line"))
        sides = []
        if mt == "total" and line is not None:
            sides = [("over", line, ask(rec["long"])), ("under", line, ask(rec["short"]))]
        elif mt in ("spread", "moneyline"):
            side = team_side(rec.get("team"), game)
            if side is None or (mt == "spread" and line is None):
                continue
            long_team = game[side]
            short_team = game["away" if side == "home" else "home"]
            ln = None if mt == "moneyline" else line
            sides = [
                (long_team, ln, ask(rec["long"])),
                (short_team, None if ln is None else -ln, ask(rec["short"])),
            ]
        for team, ln, price in sides:
            if price is None or not FAV_LO <= price <= FAV_HI:
                continue
            cand = {"team": team, "line": ln, "ask": price, "rec": rec}
            if mt not in best or abs(price - FAV_TARGET) < abs(best[mt]["ask"] - FAV_TARGET):
                best[mt] = cand
    return best


def run_sport(runtime: Path, sport: str) -> tuple[pd.DataFrame, dict]:
    games = load_games(runtime / "ledgers/ledgers.db", sport)
    snaps = load_snapshots(runtime, sport)
    by_tip: dict[str, list[str]] = defaultdict(list)
    for slug, recs in snaps.items():
        by_tip[str(recs[0].get("event_start_utc"))[:16]].append(slug)
    cov: dict[str, int] = defaultdict(int)
    rows = []
    for g in games.values():
        cov["games"] += 1
        if g["slug"] not in snaps:
            for slug in by_tip.get(g["tip"].strftime("%Y-%m-%dT%H:%M"), []):
                if any(team_side(r.get("team"), g) for r in snaps[slug][:50]):
                    g["slug"] = slug
                    break
        recs = snaps.get(g["slug"] or "")
        if not recs:
            cov["no_polymarket_event"] += 1
            continue
        book = latest_quotes(recs, g["tip"])
        if not book:
            cov["no_quote_in_window"] += 1
            continue
        cov["evaluated"] += 1
        tip_et = g["tip"].astimezone(ET).date()
        for mt, c in candidates(book, g).items():
            result = grade(g, mt, c)
            rows.append(
                {
                    "sport": sport,
                    "event_id": g["event_id"],
                    "week": (tip_et - timedelta(days=tip_et.weekday())).isoformat(),
                    "market": mt,
                    "side": f"{c['team']} {'' if c['line'] is None else c['line']}",
                    "ask": c["ask"],
                    "result": result,
                    "stake": 1.0,
                    "pnl_gross": pnl(result, c["ask"]),
                    "pnl_net": pnl(result, c["ask"] + fee(c["ask"])),
                }
            )
    return pd.DataFrame(rows), dict(cov)


def line(name: str, part: pd.DataFrame) -> str:
    if part.empty:
        return f"| {name} | 0 | - | - | - | - | - |"
    w, lo_ = int((part.result == "win").sum()), int((part.result == "loss").sum())
    n = len(part)
    g_ci = cluster_ci(part, "pnl_gross", "stake")
    n_ci = cluster_ci(part, "pnl_net", "stake")
    return (
        f"| {name} | {n} | {w}-{lo_} | {part.pnl_gross.sum():+.2f} ({part.pnl_gross.sum() / n:+.1%}, "
        f"CI {g_ci[0]:+.1%}..{g_ci[1]:+.1%}) | {part.pnl_net.sum():+.2f} ({part.pnl_net.sum() / n:+.1%}) | "
        f"{n_ci[0]:+.1%} .. {n_ci[1]:+.1%} | {part.ask.mean():.3f} |"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runtime", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--out", default=str(ROOT / "outputs/nba/LONGSHOT_HARVEST_REPLAY.md"))
    parser.add_argument("--csv", default=str(ROOT / "outputs/nba/longshot_harvest_picks.csv"))
    args = parser.parse_args()
    runtime = Path(args.runtime)
    frames, coverage = [], {}
    for sport in (*VALIDATION_SPORTS, "nba"):
        df, cov = run_sport(runtime, sport)
        frames.append(df)
        coverage[sport] = cov
    all_df = pd.concat(frames, ignore_index=True)
    all_df.to_csv(args.csv, index=False)
    head = [
        "| slice | picks | W-L | PnL gross (ROI, CI) | PnL net (ROI) | net 95% CI (games) | mean ask |",
        "|---|---|---|---|---|---|---|",
    ]
    val = all_df[all_df.sport.isin(VALIDATION_SPORTS)]
    nba = all_df[all_df.sport == "nba"]
    val_ci = cluster_ci(val, "pnl_net", "stake") if not val.empty else (float("nan"),) * 2
    out = [
        "# Favourite-longshot harvest on Polymarket ladders",
        "",
        "```",
        json.dumps(coverage, indent=1),
        "```",
    ]
    out += ["", "## Validation (other sports)", "", *head, line("pooled validation", val)]
    for sport in VALIDATION_SPORTS:
        out.append(line(sport, val[val.sport == sport]))
    for mt in ("moneyline", "spread", "total"):
        out.append(line(f"validation {mt}", val[val.market == mt]))
    out += [
        "",
        f"Validation verdict: {'PASS' if val_ci[0] > 0 else 'FAIL'} (net CI lower {val_ci[0]:+.1%}).",
        "",
    ]
    out += ["## NBA settled games", "", *head, line("NBA all", nba)]
    for mt in ("moneyline", "spread", "total"):
        out.append(line(f"NBA {mt}", nba[nba.market == mt]))
    out += [
        "",
        "### NBA by week",
        "",
        "| week | picks | W-L | PnL gross | PnL net |",
        "|---|---|---|---|---|",
    ]
    for week, wk in nba.groupby("week"):
        w, lo_ = int((wk.result == "win").sum()), int((wk.result == "loss").sum())
        out.append(
            f"| {week} | {len(wk)} | {w}-{lo_} | {wk.pnl_gross.sum():+.2f} | {wk.pnl_net.sum():+.2f} |"
        )
    Path(args.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
