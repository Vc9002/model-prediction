"""Shadow report: Polymarket executable asks against the no-vig sportsbook consensus, same line.

Read-only research tooling (2026-11-15). The NFL structural engines carry no
information beyond the closing line, so the only edge left to look for is a
price one: a Polymarket ask cheaper than the fair price implied by the sharp
two-sided book for the identical contract. This prints today's candidates. It
writes nothing, places nothing, and is not a qualified signal: the consensus is
one book (DraftKings via ESPN), and there is one week of capture history, so the
gap has never been validated against results.

Usage: python scripts/nfl_price_edge_report.py [YYYY-MM-DD] [--min-edge 0.02]
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from model_prediction.domain import parse_utc, utc_now
from model_prediction.features.nfl_efficiency import normalize_nfl_team_code
from model_prediction.runtime_paths import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]
MAX_QUOTE_GAP_MINUTES = 90.0  # a consensus/ask pair further apart than this is not the same market moment


def _implied(american: float) -> float:
    return 100.0 / (american + 100.0) if american > 0 else -american / (-american + 100.0)


def _no_vig(first: object, second: object) -> tuple[float, float] | None:
    try:
        a, b = _implied(float(first)), _implied(float(second))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return (a / (a + b), b / (a + b)) if a + b > 0 else None


def _latest(path: Path, key: str) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    if not path.exists():
        return latest
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        ident = str(row.get(key) or "")
        # Ingest-ordered file: newest is a max-scan on the record's own timestamp.
        if ident and (
            ident not in latest or str(row["observed_at_utc"]) > str(latest[ident]["observed_at_utc"])
        ):
            latest[ident] = row
    return latest


def build(date: str) -> list[dict]:
    odds_dir = RuntimePaths.resolve(repo_root=ROOT).runtime_root / "odds" / "nfl" / date
    consensus = _latest(odds_dir / "espn_odds.jsonl", "event_id")
    books = _latest(odds_dir / "polymarket_snapshots.jsonl", "market_slug")
    now = utc_now()
    out: list[dict] = []
    for game in consensus.values():
        start = parse_utc(str(game["event_start_utc"]))
        if start <= now:
            continue
        home, away = normalize_nfl_team_code(game["home_team"]), normalize_nfl_team_code(game["away_team"])
        cons_at = parse_utc(str(game["observed_at_utc"]))
        fair: list[tuple[str, str, float | None, float]] = []  # market, side, line, fair probability
        if (ml := _no_vig(game.get("ml_home_odds"), game.get("ml_away_odds"))) is not None:
            fair += [("moneyline", "home", None, ml[0]), ("moneyline", "away", None, ml[1])]
        home_line = game.get("spread_home_line")
        if home_line is not None and (
            sp := _no_vig(game.get("spread_home_odds"), game.get("spread_away_odds"))
        ):
            fair += [
                ("spread", "home", float(home_line), sp[0]),
                ("spread", "away", -float(home_line), sp[1]),
            ]
        total = game.get("total_line")
        if total is not None and (tt := _no_vig(game.get("total_over_odds"), game.get("total_under_odds"))):
            fair += [("total", "over", float(total), tt[0]), ("total", "under", float(total), tt[1])]
        for market, side, line, probability in fair:
            ask = quote_at = None
            for book in books.values():
                if book.get("market_type") != market or book.get("market_state") != "MARKET_STATE_OPEN":
                    continue
                if abs((parse_utc(str(book["event_start_utc"])) - start).total_seconds()) > 3 * 3600:
                    continue
                teams = set(str(book.get("event_slug") or "").split("-")[1:3])
                if {home.lower(), away.lower()} != teams:
                    continue
                if market == "total":
                    if book.get("line") is None or float(book["line"]) != line:
                        continue
                    leg = "long" if side == "over" else "short"
                else:
                    long_team = normalize_nfl_team_code(str(book.get("team") or ""))
                    want_team = home if side == "home" else away
                    if market == "spread":
                        book_line = float(book["line"])
                        # long = `team` at `line`; short = the other team at -line
                        if long_team == want_team and book_line == line:
                            leg = "long"
                        elif long_team != want_team and -book_line == line:
                            leg = "short"
                        else:
                            continue
                    else:
                        leg = "long" if long_team == want_team else "short"
                candidate = (book.get(leg) or {}).get("ask")
                if candidate is None:
                    continue
                ask, quote_at = float(candidate), parse_utc(str(book["observed_at_utc"]))
                break
            if ask is None or quote_at is None:
                continue
            gap = abs((quote_at - cons_at).total_seconds()) / 60.0
            out.append(
                {
                    "kickoff_et": start.astimezone(ZoneInfo("America/New_York")).strftime("%a %H:%M"),
                    "game": f"{away} @ {home}",
                    "market": market,
                    "side": side,
                    "line": line,
                    "fair": probability,
                    "ask": ask,
                    "edge": probability - ask,
                    "quote_gap_min": gap,
                    "comparable": gap <= MAX_QUOTE_GAP_MINUTES,
                }
            )
    return sorted(out, key=lambda row: -row["edge"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "date", nargs="?", default=datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    )
    parser.add_argument("--min-edge", type=float, default=0.02)
    args = parser.parse_args()
    rows = build(args.date)
    print(f"NFL price-edge shadow report {args.date}: {len(rows)} matched contracts (not a qualified signal)")
    shown = [row for row in rows if row["edge"] >= args.min_edge]
    for row in shown:
        line = (
            ""
            if row["line"] is None
            else f" {row['line']:+g}"
            if row["market"] == "spread"
            else f" {row['line']:g}"
        )
        flag = "" if row["comparable"] else f"  [quotes {row['quote_gap_min']:.0f} min apart: not comparable]"
        print(
            f"  {row['kickoff_et']}  {row['game']:10s} {row['market']:9s} {row['side']:5s}{line:7s}"
            f" fair {row['fair']:.1%}  ask {row['ask'] * 100:.1f}c  edge {row['edge']:+.1%}{flag}"
        )
    if not shown:
        print(f"  no contract with ask at least {args.min_edge:.0%} under the no-vig consensus")
    edges = [row["edge"] for row in rows if row["comparable"]]
    if edges:
        print(
            f"  comparable contracts {len(edges)}: mean edge {sum(edges) / len(edges):+.2%}, best {max(edges):+.2%}"
        )


if __name__ == "__main__":
    main()
