"""Exit policies on every settled NBA ledger pick: sell back on Polymarket before tip instead of holding.

  python scripts/nba_v10/exit_policy_replay.py [--runtime E:/model-prediction-runtime]

The models' picks have no outcome edge, but v9 picks were measured (plan section 6) to gain about
+0.6c against later Polymarket quotes. A trade that is closed before tip earns that price move
without the outcome variance, which is what a "profitable every week" goal needs. This replays
exit rules on the actual ledger picks, using only snapshots observed after the entry and before
tip, so every exit decision is point-in-time.

Pinned (quick 261122-exi):

- Picks: every settled NBA Flat ledger row whose entry ask is rebuilt by the harness (snapshot at the
  stored quote time, same market slug), deduplicated per game/market/side (earliest entry kept).
- P1 always-exit: sell at the bid of the same contract side in the last snapshot observed after the
  entry and at or before tip - 5 min; hold to settlement if there is none.
- P2 take-profit: sell at the first later snapshot (before tip - 5 min) whose bid is at least the
  entry ask + 2c; otherwise hold to settlement.
- PnL per 1u staked: shares = 1 / ask; sell value = shares * bid; gross ignores fees; net charges
  the 0.06 p (1 - p) taker fee on both the buy and the sell. Hold = ledger convention.
- Weekly by ET Monday; game-cluster CI.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import pandas as pd
from settled_common import ANY_MARKET_SLUG_RE, attach, load_games, load_snapshots
from tb_settled_replay import (
    DEFAULT_RUNTIME,
    ET,
    QUOTE_MIN_LEAD,
    ROOT,
    cluster_ci,
    fee,
    finite,
    grade,
    parse_utc,
)

TAKE_PROFIT = 0.02


def side_quote(rec: dict, market: str, team: str) -> dict:
    if market == "total":
        return rec["long"] if team == "over" else rec["short"]
    return rec["long"] if rec.get("team") == team else rec["short"]


def hold_pnl(result: str, price: float, net: bool) -> float:
    cost = price + (fee(price) if net else 0.0)
    if result == "push":
        return 0.0
    return (1.0 - cost) / cost if result == "win" else -1.0


def sell_pnl(entry: float, bid: float, net: bool) -> float:
    if not net:
        return bid / entry - 1.0
    shares = 1.0 / (entry + fee(entry))
    return shares * (bid - fee(bid)) - 1.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runtime", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--out", default=str(ROOT / "outputs/nba/EXIT_POLICY_REPLAY.md"))
    args = parser.parse_args()
    runtime = Path(args.runtime)
    games = load_games(runtime / "ledgers/ledgers.db")
    snaps = load_snapshots(runtime)
    attach(games, snaps)
    by_market: dict[str, list[dict]] = defaultdict(list)
    for recs in snaps.values():
        for r in recs:
            by_market[str(r.get("market_slug"))].append(r)
    for recs in by_market.values():
        recs.sort(key=lambda r: r["_obs"])

    conn = sqlite3.connect(f"file:{runtime / 'ledgers/ledgers.db'}?mode=ro", uri=True)
    rows = conn.execute(
        """select event_id, market_type, selection, line, market_probability, model_id, created_at_utc,
                  decision_payload_json
           from ledger_records where sport='nba' and status='settled' and ledger_tier='flat'
           order by created_at_utc"""
    ).fetchall()
    conn.close()
    seen, picks, cov = set(), [], defaultdict(int)
    for event_id, market, selection, line, prob, model_id, _created, text in rows:
        cov["flat_rows"] += 1
        g = games.get(str(event_id))
        p = json.loads(text or "{}")
        m = ANY_MARKET_SLUG_RE.search(str(p.get("rationale") or ""))
        obs = p.get("market_quote_observed_at_utc")
        if g is None or not m or not obs:
            cov["no_slug_or_time"] += 1
            continue
        team = selection if market == "total" else g["home" if selection == "home" else "away"]
        key = (str(event_id), market, team)
        if key in seen:
            cov["duplicate"] += 1
            continue
        series = by_market.get(m.group(1), [])
        entry_t = parse_utc(obs)
        entry = next((r for r in series if r["observed_at_utc"] == str(obs)), None)
        if entry is None or finite(prob) is None:
            cov["entry_not_rebuilt"] += 1
            continue
        seen.add(key)
        price = side_quote(entry, market, team)["ask"]
        if price is None or abs(price - float(prob)) > 0.002:
            cov["entry_ask_mismatch"] += 1
            continue
        cov["picks"] += 1
        result = grade(g, market, {"team": team, "line": finite(line)})
        later = [
            r for r in series
            if entry_t < r["_obs"] <= g["tip"] - QUOTE_MIN_LEAD
            and r.get("market_state") in (None, "MARKET_STATE_OPEN")
            and side_quote(r, market, team).get("bid") is not None
        ]  # fmt: skip
        last_bid = side_quote(later[-1], market, team)["bid"] if later else None
        tp = next((side_quote(r, market, team)["bid"] for r in later
                   if side_quote(r, market, team)["bid"] >= price + TAKE_PROFIT), None)  # fmt: skip
        tip_et = g["tip"].astimezone(ET).date()
        row = {"event_id": str(event_id), "week": (tip_et - timedelta(days=tip_et.weekday())).isoformat(),
               "model": model_id, "market": market, "ask": price, "last_bid": last_bid, "result": result,
               "stake": 1.0}  # fmt: skip
        for net in (False, True):
            tag = "net" if net else "gross"
            row[f"hold_{tag}"] = hold_pnl(result, price, net)
            row[f"p1_{tag}"] = sell_pnl(price, last_bid, net) if last_bid is not None else row[f"hold_{tag}"]
            row[f"p2_{tag}"] = sell_pnl(price, tp, net) if tp is not None else row[f"hold_{tag}"]
        row["p1_exited"] = last_bid is not None
        row["p2_exited"] = tp is not None
        picks.append(row)
    df = pd.DataFrame(picks)
    out = ["# Exit policies on settled NBA Flat picks", "", "```", json.dumps(dict(cov), indent=1), "```", ""]
    out += ["| policy | n | exited | gross | net | ROI net | net 95% CI |", "|---|---|---|---|---|---|---|"]
    for pol, exited in (("hold", None), ("p1", "p1_exited"), ("p2", "p2_exited")):
        ci = cluster_ci(df, f"{pol}_net", "stake")
        n_exit = int(df[exited].sum()) if exited else 0
        out.append(f"| {pol} | {len(df)} | {n_exit} | {df[f'{pol}_gross'].sum():+.2f} | {df[f'{pol}_net'].sum():+.2f} | "
                   f"{df[f'{pol}_net'].sum() / len(df):+.2%} | {ci[0]:+.1%} .. {ci[1]:+.1%} |")  # fmt: skip
    out += [
        "",
        "## By week (net)",
        "",
        "| week | n | hold | P1 always-exit | P2 take-profit |",
        "|---|---|---|---|---|",
    ]
    for week, wk in df.groupby("week"):
        out.append(
            f"| {week} | {len(wk)} | {wk.hold_net.sum():+.2f} | {wk.p1_net.sum():+.2f} | {wk.p2_net.sum():+.2f} |"
        )
    exited = df[df.p1_exited]
    if not exited.empty:
        move = (exited.last_bid - exited.ask) * 100
        out += [
            "",
            f"P1 exits: mean (last bid - entry ask) {move.mean():+.2f}c, median {move.median():+.2f}c, n {len(exited)}.",
        ]
    Path(args.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
