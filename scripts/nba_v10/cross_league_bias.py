"""Main-line side biases developed on other basketball leagues, frozen, then scored once on NBA.

  python scripts/nba_v10/cross_league_bias.py [--runtime E:/model-prediction-runtime]

Advisor guidance after round 8: stop searching rules on the five settled NBA weeks. Develop on the
venue's other basketball leagues, freeze, score NBA once. This round tests the simplest structural
retail biases a venue can carry (home, favourite, over), each as one bet per game at the main rung.

Pinned (quick 261122-xlb):

- Development leagues: WNBA (2026 season) and NCAAB (2026-27 to date), settled ledger games, the
  coverage-complete loaders of ``settled_common``. A league counts only if its orientation sanity
  checks pass (moneyline favourites at 60-80c win 55-85%; main spread rungs cover 40-60%).
- Book: last pre-tip book at any age (tip - 5 min). Main rung: per market type the rung whose
  long mid is nearest 50c; moneyline is its own market.
- Rules (10): spread home / away / favourite (team laying points) / underdog; total over / under;
  moneyline home / away / favourite (ask > 50c) / underdog.
- Freeze: every rule whose pooled development net ROI has a 95% game-cluster CI above 0. Only
  frozen rules are scored on NBA. If none freezes, NBA is not scored as a candidate.
- PnL: 1u at the ask, gross and net of the 0.06 p (1 - p) fee; weekly by ET Monday.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import pandas as pd
from pm_momentum_settled_replay import mid
from settled_common import attach, decision_book, load_sport
from tb_settled_replay import DEFAULT_RUNTIME, ET, ROOT, ask, cluster_ci, fee, finite, grade, pnl

DEV = ("wnba", "ncaab")
RULES = [
    "spread_home", "spread_away", "spread_fav", "spread_dog", "total_over", "total_under",
    "ml_home", "ml_away", "ml_fav", "ml_dog",
]  # fmt: skip


def main_rungs(book: list[dict], game: dict) -> dict[str, dict]:
    """Per market type the rung nearest 50c, with both sides spelled out."""
    out: dict[str, dict] = {}
    for rec in book:
        mt, line = rec["market_type"], finite(rec.get("line"))
        m = mid(rec.get("long"))
        if m is None:
            continue
        if mt == "spread" and rec.get("team") not in (game["home"], game["away"]):
            continue
        if mt == "moneyline" and rec.get("team") not in (game["home"], game["away"]):
            continue
        if mt in ("spread", "total") and line is None:
            continue
        if mt not in out or abs(m - 0.5) < abs(out[mt]["mid"] - 0.5):
            out[mt] = {"rec": rec, "mid": m}
    return out


def rule_bets(book: list[dict], game: dict) -> dict[str, dict]:
    bets: dict[str, dict] = {}
    home, away = game["home"], game["away"]
    for mt, r in main_rungs(book, game).items():
        rec = r["rec"]
        la, sa = ask(rec["long"]), ask(rec["short"])
        line = finite(rec.get("line"))
        if mt == "total":
            bets["total_over"] = {"team": "over", "line": line, "ask": la, "market": mt}
            bets["total_under"] = {"team": "under", "line": line, "ask": sa, "market": mt}
            continue
        long_team = rec["team"]
        short_team = away if long_team == home else home
        sides = {
            long_team: {"team": long_team, "line": line, "ask": la, "market": mt},
            short_team: {
                "team": short_team,
                "line": None if line is None else -line,
                "ask": sa,
                "market": mt,
            },
        }
        prefix = "spread" if mt == "spread" else "ml"
        bets[f"{prefix}_home"] = sides[home]
        bets[f"{prefix}_away"] = sides[away]
        if mt == "spread":
            fav = long_team if line < 0 else short_team
        else:
            fav = long_team if (la or 0) > 0.5 else short_team
        bets[f"{prefix}_fav"] = sides[fav]
        bets[f"{prefix}_dog"] = sides[short_team if fav == long_team else long_team]
    return bets


def league_rows(runtime: Path, sport: str) -> tuple[pd.DataFrame, dict]:
    games, snaps = load_sport(runtime, sport)
    mapping = attach(games, snaps)
    rows, sanity = [], defaultdict(list)
    for g in games.values():
        if not g["slug"]:
            continue
        book = decision_book(snaps[g["slug"]], g["tip"])
        if not book:
            continue
        tip_et = g["tip"].astimezone(ET).date()
        week = (tip_et - timedelta(days=tip_et.weekday())).isoformat()
        for rule, bet in rule_bets(book, g).items():
            if bet["ask"] is None:
                continue
            result = grade(g, bet["market"], bet)
            if rule == "ml_fav" and 0.60 <= bet["ask"] <= 0.80:
                sanity["ml_fav_60_80"].append(result == "win")
            if rule == "spread_home" and result != "push":
                sanity["spread_home_cover"].append(result == "win")
            rows.append(
                {
                    "sport": sport,
                    "event_id": g["event_id"],
                    "week": week,
                    "rule": rule,
                    "ask": bet["ask"],
                    "result": result,
                    "stake": 1.0,
                    "pnl_gross": pnl(result, bet["ask"]),
                    "pnl_net": pnl(result, bet["ask"] + fee(bet["ask"])),
                }
            )
    checks = {k: (round(sum(v) / len(v), 3) if v else None, len(v)) for k, v in sanity.items()}
    ok = (
        checks.get("ml_fav_60_80", (None, 0))[0] is not None
        and 0.55 <= checks["ml_fav_60_80"][0] <= 0.85
        and checks.get("spread_home_cover", (None, 0))[0] is not None
        and 0.40 <= checks["spread_home_cover"][0] <= 0.60
    )
    return pd.DataFrame(rows), {"mapping": mapping, "sanity": checks, "sanity_pass": ok}


def summary(part: pd.DataFrame) -> dict:
    if part.empty:
        return {"n": 0}
    ci = cluster_ci(part, "pnl_net", "stake")
    return {
        "n": len(part),
        "wl": f"{int((part.result == 'win').sum())}-{int((part.result == 'loss').sum())}",
        "gross": round(part.pnl_gross.sum(), 2),
        "net": round(part.pnl_net.sum(), 2),
        "roi_net": round(part.pnl_net.sum() / len(part), 4),
        "ci": [round(ci[0], 4), round(ci[1], 4)],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runtime", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--out", default=str(ROOT / "outputs/nba/CROSS_LEAGUE_BIAS.md"))
    args = parser.parse_args()
    runtime = Path(args.runtime)
    dev_frames, meta = [], {}
    for sport in DEV:
        df, info = league_rows(runtime, sport)
        meta[sport] = info
        if info["sanity_pass"]:
            dev_frames.append(df)
    dev = pd.concat(dev_frames, ignore_index=True) if dev_frames else pd.DataFrame()
    out = [
        "# Main-line side biases: develop on WNBA/NCAAB, score NBA once",
        "",
        "```",
        json.dumps(meta, indent=1),
        "```",
    ]
    out += ["", "## Development (leagues passing sanity)", "", "| rule | n | W-L | gross | net | ROI net | 95% CI |",
            "|---|---|---|---|---|---|---|"]  # fmt: skip
    frozen = []
    for rule in RULES:
        s = summary(dev[dev.rule == rule]) if not dev.empty else {"n": 0}
        if s["n"]:
            out.append(f"| {rule} | {s['n']} | {s['wl']} | {s['gross']:+.2f} | {s['net']:+.2f} | "
                       f"{s['roi_net']:+.1%} | {s['ci'][0]:+.1%} .. {s['ci'][1]:+.1%} |")  # fmt: skip
            if s["ci"][0] > 0:
                frozen.append(rule)
    out += ["", f"Frozen rules: {frozen or 'none'}", ""]
    if frozen:
        nba, info = league_rows(runtime, "nba")
        out += ["## NBA (scored once, frozen rules only)", "", "```", json.dumps(info, indent=1), "```", ""]
        for rule in frozen:
            part = nba[nba.rule == rule]
            s = summary(part)
            out.append(f"- {rule}: {json.dumps(s)}")
            for week, wk in part.groupby("week"):
                out.append(f"  - {week}: n {len(wk)}, net {wk.pnl_net.sum():+.2f}")
    Path(args.out).write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
