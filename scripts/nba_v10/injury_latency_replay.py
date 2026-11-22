"""Injury-report latency (plan track T-C2) on every settled NBA game.

  python scripts/nba_v10/injury_latency_replay.py [--runtime E:/model-prediction-runtime]

Hypothesis: when the official NBA injury report rules a rotation player out close to tip, the
Polymarket ladder sometimes has not moved by the time of its next quote. Betting against the
weakened team at that quote then captures the move the market has not made. Every input is
point-in-time: the report row carries its own observed_at_utc (the report slot), player minutes
come from 2026-27 box scores of earlier games only, and the Polymarket books are archived quotes.

Pinned before any outcome was read (quick 261122-inj):

- Rotation player: >= 3 earlier 2026-27 games and >= 25.0 average minutes in them.
- News event: the first report R in [tip - 6 h, tip - 5 min] listing the player Out (or Doubtful)
  when the latest earlier report for that game listed him otherwise (Questionable, Probable,
  Available) or did not list him.
- Books: P0 = the last Polymarket book observed before R; Q = the first book observed at or after R
  and at or before tip - 5 min. Both need a fitted ladder mean (round 2's fit_mean, sigma 13.792).
- Signal: the home-margin ladder mean moved by less than MOVE_MAX (1.0 point) toward the opponent
  of the weakened team between P0 and Q. Games where both teams have news events are skipped.
- Bet: the opponent of the weakened team at Q's main spread rung (long mid nearest 50c), 1u at the
  ask, gross and net of the 0.06 p (1 - p) fee; weekly by ET Monday. One bet per game.
- Coverage: official reports are parsed through 2026-11-19, so later games cannot qualify.
"""

from __future__ import annotations

import argparse
import gzip
import json
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import pandas as pd
from pm_momentum_settled_replay import fit_mean, ladder_points, mid
from settled_common import attach, load_games, load_snapshots
from tb_settled_replay import (
    DEFAULT_RUNTIME,
    ET,
    QUOTE_MIN_LEAD,
    ROOT,
    ask,
    cluster_ci,
    fee,
    finite,
    grade,
    parse_utc,
    pnl,
)

SIGMA = 13.792
MIN_GAMES, MIN_MINUTES = 3, 25.0
WINDOW = timedelta(hours=6)
MOVE_MAX = 1.0
BOX = ROOT / "data/availability/nba/espn_boxscores"
REPORT_ROWS = ROOT / "data/research/nba_v10/injury_reports/rows"
OUT_STATUSES = {"Out", "Doubtful"}


def player_minutes() -> dict[str, list[tuple]]:
    """team -> [(tip_utc, player_name, minutes)] for 2026-27 box scores."""
    out: dict[str, list[tuple]] = defaultdict(list)
    for path in BOX.glob("4019*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))["payload"]
            tip = parse_utc(payload["header"]["competitions"][0]["date"])
        except (KeyError, IndexError, ValueError, json.JSONDecodeError):
            continue
        if tip.year < 2026 or (tip.year == 2026 and tip.month < 10):
            continue
        for team_block in payload.get("boxscore", {}).get("players", []):
            team = team_block["team"]["displayName"]
            for stat in team_block.get("statistics", []):
                labels = stat.get("labels") or []
                if "MIN" not in labels:
                    continue
                i = labels.index("MIN")
                for a in stat.get("athletes", []):
                    stats = a.get("stats") or []
                    minutes = finite(stats[i]) if len(stats) > i else None
                    if minutes is not None and minutes > 0:
                        out[team].append((tip, a["athlete"]["displayName"], minutes))
    return out


def rotation(team_rows: list[tuple], before) -> dict[str, float]:
    per: dict[str, list[float]] = defaultdict(list)
    for tip, name, minutes in team_rows:
        if tip < before:
            per[name].append(minutes)
    return {
        n: sum(v) / len(v) for n, v in per.items() if len(v) >= MIN_GAMES and sum(v) / len(v) >= MIN_MINUTES
    }


def report_rows() -> dict[tuple[str, str], list[dict]]:
    """(game_date ET, team) -> rows sorted by observed time."""
    out: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for month in ("2026-10", "2026-11"):
        path = REPORT_ROWS / f"{month}.jsonl.gz"
        if not path.is_file():
            continue
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for raw in handle:
                r = json.loads(raw)
                if not r.get("consistent", True) or not r.get("player_name"):
                    continue
                last, _, first = str(r["player_name"]).partition(", ")
                r["name"] = f"{first} {last}".strip()
                r["_obs"] = parse_utc(r["observed_at_utc"])
                out[(r["game_date"], r["team"])].append(r)
    for rows in out.values():
        rows.sort(key=lambda r: r["_obs"])
    return out


def books_by_time(recs: list[dict]) -> list[tuple]:
    """Snapshot batches grouped to the minute: [(time, [records])], ascending."""
    groups: dict = defaultdict(list)
    for r in recs:
        if r.get("market_state") in (None, "MARKET_STATE_OPEN"):
            groups[r["_obs"].replace(second=0, microsecond=0)].append(r)
    return sorted(groups.items())


def book_until(recs: list[dict], until) -> list[dict]:
    best: dict[str, dict] = {}
    for r in recs:
        if r["_obs"] > until or r.get("market_state") not in (None, "MARKET_STATE_OPEN"):
            continue
        key = str(r.get("market_slug"))
        if key not in best or r["_obs"] > best[key]["_obs"]:
            best[key] = r
    return list(best.values())


def news_events(game: dict, team: str, rows: list[dict], minutes: dict[str, float]) -> list[dict]:
    tip = game["tip"]
    events = []
    for name, mpg in minutes.items():
        prior_status = None
        for r in rows:
            if r["name"] != name:
                continue
            if r["_obs"] < tip - WINDOW:
                prior_status = r["status"]
                continue
            if r["_obs"] > tip - QUOTE_MIN_LEAD:
                break
            if r["status"] in OUT_STATUSES and prior_status not in OUT_STATUSES:
                events.append({"team": team, "player": name, "mpg": round(mpg, 1), "R": r["_obs"],
                               "from": prior_status, "to": r["status"]})  # fmt: skip
                break
            prior_status = r["status"]
    return events


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runtime", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--out", default=str(ROOT / "outputs/nba/INJURY_LATENCY_REPLAY.md"))
    parser.add_argument("--csv", default=str(ROOT / "outputs/nba/injury_latency_events.csv"))
    args = parser.parse_args()
    runtime = Path(args.runtime)
    games = load_games(runtime / "ledgers/ledgers.db")
    snaps = load_snapshots(runtime)
    attach(games, snaps)
    mins = player_minutes()
    reports = report_rows()
    cov: dict[str, int] = defaultdict(int)
    events_out, bets = [], []
    for g in sorted(games.values(), key=lambda x: x["tip"]):
        cov["games"] += 1
        day = g["tip"].astimezone(ET).date().isoformat()
        evs = []
        for team in (g["home"], g["away"]):
            rows = reports.get((day, team), [])
            if not rows:
                continue
            evs += news_events(g, team, rows, rotation(mins.get(team, []), g["tip"]))
        if not evs:
            continue
        cov["games_with_news"] += 1
        teams_hit = {e["team"] for e in evs}
        if len(teams_hit) > 1:
            cov["both_teams_news"] += 1
            continue
        if not g["slug"]:
            cov["no_polymarket_event"] += 1
            continue
        weakened = teams_hit.pop()
        R = min(e["R"] for e in evs)
        recs = snaps[g["slug"]]
        p0_book = book_until(recs, R - timedelta(seconds=1))
        later = [t for t, _ in books_by_time(recs) if R <= t <= g["tip"] - QUOTE_MIN_LEAD]
        if not p0_book or not later:
            cov["no_book_pair"] += 1
            continue
        q_book = book_until(recs, later[0] + timedelta(minutes=1))
        m0 = fit_mean(ladder_points(p0_book, g, "spread"), SIGMA)
        m1 = fit_mean(ladder_points(q_book, g, "spread"), SIGMA)
        if m0 is None or m1 is None:
            cov["no_ladder_mean"] += 1
            continue
        toward_opp = (m0 - m1) if weakened == g["home"] else (m1 - m0)
        cov["evaluated"] += 1
        row = {"event_id": g["event_id"], "matchup": f"{g['away']} @ {g['home']}", "weakened": weakened,
               "players": "; ".join(f"{e['player']} ({e['mpg']}) {e['from']}->{e['to']}" for e in evs),
               "report_utc": R.isoformat(), "p0_minutes_before_R": round((R - max(r['_obs'] for r in p0_book)).total_seconds() / 60, 1),
               "q_minutes_after_R": round((later[0] - R).total_seconds() / 60, 1),
               "mean_p0": round(m0, 2), "mean_q": round(m1, 2), "move_toward_opp": round(toward_opp, 2),
               "signal": toward_opp < MOVE_MAX}  # fmt: skip
        events_out.append(row)
        if not row["signal"]:
            continue
        opp = g["away"] if weakened == g["home"] else g["home"]
        rungs = [
            r for r in q_book
            if r["market_type"] == "spread" and r.get("team") in (g["home"], g["away"])
            and mid(r.get("long")) is not None and ask(r.get("long")) and ask(r.get("short"))
        ]  # fmt: skip
        if not rungs:
            continue
        rec = min(rungs, key=lambda r: abs(mid(r["long"]) - 0.5))
        line = float(rec["line"])
        side = {"team": opp, "line": line if rec["team"] == opp else -line}
        price = ask(rec["long"] if rec["team"] == opp else rec["short"])
        result = grade(g, "spread", side)
        tip_et = g["tip"].astimezone(ET).date()
        bets.append({**row, "week": (tip_et - timedelta(days=tip_et.weekday())).isoformat(),
                     "bet": f"{opp} {side['line']:+g}", "ask": price, "result": result, "stake": 1.0,
                     "pnl_gross": pnl(result, price), "pnl_net": pnl(result, price + fee(price))})  # fmt: skip
    ev_df, bet_df = pd.DataFrame(events_out), pd.DataFrame(bets)
    ev_df.to_csv(args.csv, index=False)
    out = [
        "# Injury-report latency on settled NBA games",
        "",
        "```",
        json.dumps(dict(cov), indent=1),
        "```",
        "",
    ]
    if not ev_df.empty:
        out.append(f"Evaluated news events: {len(ev_df)}; median move toward opponent {ev_df.move_toward_opp.median():+.2f} pts;"
                   f" signals {int(ev_df.signal.sum())}.")  # fmt: skip
    if not bet_df.empty:
        ci = cluster_ci(bet_df, "pnl_net", "stake")
        w, lo_ = int((bet_df.result == "win").sum()), int((bet_df.result == "loss").sum())
        head = (
            f"Bets: {len(bet_df)}, {w}-{lo_}, gross {bet_df.pnl_gross.sum():+.2f}, "
            f"net {bet_df.pnl_net.sum():+.2f} (net CI {ci[0]:+.1%}..{ci[1]:+.1%})"
        )
        out += ["", head, "", "| week | n | gross | net |", "|---|---|---|---|"]
        for week, wk in bet_df.groupby("week"):
            out.append(f"| {week} | {len(wk)} | {wk.pnl_gross.sum():+.2f} | {wk.pnl_net.sum():+.2f} |")
    Path(args.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
