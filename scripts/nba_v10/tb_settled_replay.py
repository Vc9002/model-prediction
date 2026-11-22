"""Track T-B replayed over every settled NBA game (read-only; ledger games, not a historical backtest).

  python scripts/nba_v10/tb_settled_replay.py [--runtime E:/model-prediction-runtime]
                                              [--out outputs/nba/TB_SETTLED_REPLAY.md]

The games are every NBA game with a settled ledger row. Prices are the archived Polymarket
executable quotes (``odds/nba/<date>/polymarket_snapshots.jsonl``); results are the ledger scores.

Pinned before any outcome was read (plan section 9 and quick 261122-tbr):

- Rule: a pick fires when the devigged DraftKings probability for a side minus that side's
  Polymarket ask is at least ``TB_THRESHOLD`` (3.0 points, gross of fee). Not tuned here.
- Quote: per Polymarket market, the last snapshot observed in [tip - QUOTE_MAX_AGE, tip - 5 min]
  with both sides' asks present and the market open. The decision time is that quote's time.
- DraftKings: the live ESPN capture (``espn_odds.jsonl``) observed at or before the decision and
  at most 90 minutes old is point-in-time clean. Without one, the backfilled DraftKings close is
  used. The close carries no capture time and is observed at tip, after the decision, so those
  picks are an optimistic upper bound and are reported separately.
- Contracts: moneyline (two-way devig) and spread/total contracts at exactly DraftKings' line form
  the primary cut. A same-line spread/total is fair at 50/50 unless the capture stored both
  sides' juice. Translated lines (the nearest Polymarket line, priced through a normal with the
  anchor artifact's sigma) are reported separately: one point is about 3 probability points,
  which is the whole threshold.
- One contract per game and market; of its two sides, the larger gap.
- PnL: 1 unit staked at the ask; win (1 - ask) / ask, loss -1, push 0 (gross, the ledger
  convention). Net adds the Polymarket US taker fee 0.06 * p * (1 - p) to the price.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import NormalDist
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME = Path("E:/model-prediction-runtime")
ESPN_BACKFILL = ROOT / "data/research/nba_v10/espn_odds/espn_lines.jsonl"
ANCHOR_ARTIFACT = ROOT / "config/models/research/nba-anchor-v10.json"

TB_THRESHOLD = 0.030
QUOTE_MAX_AGE = timedelta(minutes=120)
QUOTE_MIN_LEAD = timedelta(minutes=5)
DK_MAX_AGE = timedelta(minutes=90)
FEE_COEFFICIENT = 0.06
BOOTSTRAP_DRAWS = 10_000
SEED = 20261122
ET = ZoneInfo("America/New_York")
SLUG_RE = re.compile(r"[a-z]{3}-(nba-[a-z]+-[a-z]+-\d{4}-\d{2}-\d{2})")
MARKET_SLUG_RE = re.compile(r"([a-z]{3}-nba-[a-z]+-[a-z]+-\d{4}-\d{2}-\d{2}[a-z0-9-]*)")


def parse_utc(value: str) -> datetime:
    # Ledger moneyline rows store "-0500" offsets while spread/total rows use "Z"; compare instants.
    return pd.Timestamp(str(value)).tz_convert("UTC").to_pydatetime()


def finite(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# --------------------------------------------------------------------------------------------- games


def load_games(ledger: Path) -> dict[str, dict]:
    """Every settled NBA game: teams, scores, tip and Polymarket event slug, from the ledger."""
    conn = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    rows = conn.execute(
        """select event_id, event_start_utc, decision_payload_json from ledger_records
           where sport='nba' and status='settled'"""
    ).fetchall()
    conn.close()
    games: dict[str, dict] = {}
    for event_id, start, payload_text in rows:
        payload = json.loads(payload_text or "{}")
        home, away = payload.get("home_score"), payload.get("away_score")
        game = games.setdefault(
            str(event_id),
            {
                "event_id": str(event_id),
                "home": payload.get("home_team"),
                "away": payload.get("away_team"),
                "tip": parse_utc(payload.get("event_start_utc") or start),
                "home_score": None,
                "away_score": None,
                "slug": None,
            },
        )
        if finite(home) is not None and finite(away) is not None:
            game["home_score"], game["away_score"] = float(home), float(away)
        match = SLUG_RE.search(str(payload.get("rationale") or ""))
        if match and not game["slug"]:
            game["slug"] = match.group(1)
    return {k: g for k, g in games.items() if g["home_score"] is not None}


# ---------------------------------------------------------------------------------------- polymarket


def load_snapshots(runtime: Path) -> dict[str, list[dict]]:
    """Polymarket snapshots by event slug (all archived dates)."""
    out: dict[str, list[dict]] = defaultdict(list)
    for path in sorted((runtime / "odds/nba").glob("*/polymarket_snapshots.jsonl")):
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


def ask(side: dict | None) -> float | None:
    value = finite((side or {}).get("ask"))
    return value if value is not None and 0.0 < value < 1.0 else None


def latest_quotes(snaps: list[dict], tip: datetime) -> list[dict]:
    """The last usable snapshot per market inside the pinned pre-tip window."""
    lo, hi = tip - QUOTE_MAX_AGE, tip - QUOTE_MIN_LEAD
    best: dict[str, dict] = {}
    for rec in snaps:
        if not lo <= rec["_obs"] <= hi:
            continue
        if rec.get("market_state") not in (None, "MARKET_STATE_OPEN"):
            continue
        if ask(rec.get("long")) is None or ask(rec.get("short")) is None:
            continue
        key = str(rec.get("market_slug") or rec.get("market_id"))
        if key not in best or rec["_obs"] > best[key]["_obs"]:
            best[key] = rec
    return list(best.values())


# ------------------------------------------------------------------------------------------ DraftKings


def load_dk_backfill() -> dict[str, dict]:
    out = {}
    with ESPN_BACKFILL.open(encoding="utf-8") as handle:
        for raw in handle:
            rec = json.loads(raw)
            if rec.get("season_year") != 2027:
                continue
            dk = next((p for p in rec.get("providers") or [] if p.get("provider_name") == "DraftKings"), None)
            sel = rec.get("selected") or {}
            if dk is None:
                continue
            h, a = finite(dk.get("ml_implied_home_close")), finite(dk.get("ml_implied_away_close"))
            ml = None if h is None or a is None else {"home": h / (h + a), "away": a / (h + a)}
            out[str(rec["event_id"])] = {
                "source": "dk_close_backfill",
                "pit": False,
                "observed": None,
                "home_line": finite(sel.get("close_home_spread")),
                "spread_home_fair": None,
                "total": finite(sel.get("close_total")),
                "over_fair": None,
                "ml": ml,
            }
    return out


def load_dk_live(runtime: Path) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for path in sorted((runtime / "odds/nba").glob("*/espn_odds.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for raw in handle:
                try:
                    rec = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                dk = next(
                    (
                        p
                        for p in rec.get("providers") or []
                        if p.get("provider_name") == "DraftKings" and not p.get("is_live")
                    ),
                    None,
                )
                if dk is None:
                    continue
                sp, tot, ml = dk.get("spread") or {}, dk.get("total") or {}, dk.get("moneyline") or {}
                mh, ma = finite(ml.get("home_fair")), finite(ml.get("away_fair"))
                out[str(rec["event_id"])].append(
                    {
                        "source": "dk_live_capture",
                        "pit": True,
                        "observed": parse_utc(rec["observed_at_utc"]),
                        "home_line": finite(sp.get("home_line")),
                        "spread_home_fair": finite(sp.get("home_fair")),
                        "total": finite(tot.get("line")),
                        "over_fair": finite(tot.get("over_fair")),
                        "ml": None if mh is None or ma is None else {"home": mh, "away": ma},
                    }
                )
    return out


def dk_for(event_id: str, decision: datetime, live: dict[str, list[dict]], backfill: dict[str, dict]):
    usable = [
        rec
        for rec in live.get(event_id, [])
        if rec["observed"] <= decision and decision - rec["observed"] <= DK_MAX_AGE
    ]
    if usable:
        return max(usable, key=lambda rec: rec["observed"])
    return backfill.get(event_id)


# --------------------------------------------------------------------------------------------- rules


def norm_cdf(x: float) -> float:
    return NormalDist().cdf(x)


def contract_candidates(game: dict, quotes: list[dict], dk: dict, sigmas: dict[str, float]) -> list[dict]:
    """One candidate per game/market with both sides' fair probability, ask and grading spec."""
    home, away = game["home"], game["away"]
    out = []
    # Moneyline: direct two-way devig, no line translation.
    ml_quotes = [q for q in quotes if q["market_type"] == "moneyline" and q.get("team") in (home, away)]
    if dk.get("ml") and ml_quotes:
        q = max(ml_quotes, key=lambda r: r["_obs"])
        long_team = q["team"]
        short_team = away if long_team == home else home
        out.append(
            {
                "market": "moneyline",
                "translated": False,
                "quote": q,
                "sides": [
                    {
                        "label": long_team,
                        "team": long_team,
                        "line": None,
                        "ask": ask(q["long"]),
                        "fair": dk["ml"]["home" if long_team == home else "away"],
                    },
                    {
                        "label": short_team,
                        "team": short_team,
                        "line": None,
                        "ask": ask(q["short"]),
                        "fair": dk["ml"]["home" if short_team == home else "away"],
                    },
                ],
            }
        )
    # Spread: contract "team T at L"; home-margin line h is DraftKings' home line (negative = fav).
    h = dk.get("home_line")
    sp_quotes = [
        q
        for q in quotes
        if q["market_type"] == "spread"
        and q.get("team") in (home, away)
        and finite(q.get("line")) is not None
    ]
    if h is not None and sp_quotes:

        def home_equiv(q: dict) -> float:  # the contract's line expressed for the home team
            line = float(q["line"])
            return line if q["team"] == home else -line

        q = min(sp_quotes, key=lambda r: (abs(home_equiv(r) - h), abs(ask(r["long"]) - 0.5)))
        line_home = home_equiv(q)
        same = abs(line_home - h) < 1e-9
        sigma = sigmas["spread"]
        if same:
            p_home = dk.get("spread_home_fair") or 0.5
        else:
            # Mean home margin implied by DraftKings: P(home margin > -h) = fair home cover prob.
            p_dk = dk.get("spread_home_fair") or 0.5
            mean = -h + sigma * NormalDist().inv_cdf(p_dk)
            p_home = 1.0 - norm_cdf((-line_home - mean) / sigma)
        long_team = q["team"]
        long_line = float(q["line"])
        short_team = away if long_team == home else home
        p_long = p_home if long_team == home else 1.0 - p_home
        out.append(
            {
                "market": "spread",
                "translated": not same,
                "quote": q,
                "sides": [
                    {
                        "label": f"{long_team} {long_line:+g}",
                        "team": long_team,
                        "line": long_line,
                        "ask": ask(q["long"]),
                        "fair": p_long,
                    },
                    {
                        "label": f"{short_team} {-long_line:+g}",
                        "team": short_team,
                        "line": -long_line,
                        "ask": ask(q["short"]),
                        "fair": 1.0 - p_long,
                    },
                ],
            }
        )
    t = dk.get("total")
    to_quotes = [q for q in quotes if q["market_type"] == "total" and finite(q.get("line")) is not None]
    if t is not None and to_quotes:
        q = min(to_quotes, key=lambda r: (abs(float(r["line"]) - t), abs(ask(r["long"]) - 0.5)))
        line = float(q["line"])
        same = abs(line - t) < 1e-9
        sigma = sigmas["total"]
        p_over_dk = dk.get("over_fair") or 0.5
        if same:
            p_over = p_over_dk
        else:
            mean = t + sigma * NormalDist().inv_cdf(p_over_dk)
            p_over = 1.0 - norm_cdf((line - mean) / sigma)
        out.append(
            {
                "market": "total",
                "translated": not same,
                "quote": q,
                "sides": [
                    {
                        "label": f"over {line:g}",
                        "team": "over",
                        "line": line,
                        "ask": ask(q["long"]),
                        "fair": p_over,
                    },
                    {
                        "label": f"under {line:g}",
                        "team": "under",
                        "line": line,
                        "ask": ask(q["short"]),
                        "fair": 1.0 - p_over,
                    },
                ],
            }
        )
    return out


def grade(game: dict, market: str, side: dict) -> str:
    hs, as_ = game["home_score"], game["away_score"]
    if market == "total":
        total = hs + as_
        if total == side["line"]:
            return "push"
        return "win" if (total > side["line"]) == (side["team"] == "over") else "loss"
    margin = hs - as_ if side["team"] == game["home"] else as_ - hs
    if market == "moneyline":
        return "win" if margin > 0 else "loss"
    if margin + side["line"] == 0:
        return "push"
    return "win" if margin + side["line"] > 0 else "loss"


def pnl(result: str, price: float) -> float:
    if result == "push":
        return 0.0
    return (1.0 - price) / price if result == "win" else -1.0


def fee(price: float) -> float:
    return FEE_COEFFICIENT * price * (1.0 - price)


# ------------------------------------------------------------------------------------------- harness


def harness_check(ledger: Path, snaps_by_event: dict[str, list[dict]], games: dict[str, dict]) -> dict:
    """Rebuild stored Flat rows' asks from the snapshot archive and regrade them from the scores."""
    by_market_obs: dict[tuple[str, str], dict] = {}
    for recs in snaps_by_event.values():
        for rec in recs:
            by_market_obs[(str(rec.get("market_slug")), rec["observed_at_utc"])] = rec
    conn = sqlite3.connect(f"file:{ledger}?mode=ro", uri=True)
    rows = conn.execute(
        """select event_id, market_type, selection, line, market_probability, result, decision_payload_json
           from ledger_records where sport='nba' and status='settled' and ledger_tier='flat'
           and model_id in ('nba-spread-margin-v9','nba-total-margin-v9','nba-elo-trend-lr-v5')"""
    ).fetchall()
    conn.close()
    stats = {
        "rows": len(rows),
        "snapshot_found": 0,
        "ask_match": 0,
        "result_match": 0,
        "graded": 0,
        "mismatch_examples": [],
    }
    for event_id, market, selection, line, stored_prob, stored_result, payload_text in rows:
        payload = json.loads(payload_text or "{}")
        game = games.get(str(event_id))
        m = MARKET_SLUG_RE.search(str(payload.get("rationale") or ""))
        rec = by_market_obs.get((m.group(1), str(payload.get("market_quote_observed_at_utc")))) if m else None
        if game is None:
            continue
        if rec is not None:
            stats["snapshot_found"] += 1
            if market == "total":
                side_rec = rec["long"] if selection == "over" else rec["short"]
            else:
                team = game["home"] if selection == "home" else game["away"]
                side_rec = rec["long"] if rec.get("team") == team else rec["short"]
            rebuilt = ask(side_rec)
            if rebuilt is not None and abs(rebuilt - float(stored_prob)) <= 0.002:
                stats["ask_match"] += 1
            elif len(stats["mismatch_examples"]) < 5:
                stats["mismatch_examples"].append(
                    {
                        "event": event_id,
                        "market": market,
                        "selection": selection,
                        "stored": stored_prob,
                        "rebuilt": rebuilt,
                        "team": rec.get("team"),
                        "line": rec.get("line"),
                    }
                )
        if stored_result in ("win", "loss", "push"):
            team = selection if market == "total" else (game["home"] if selection == "home" else game["away"])
            regraded = grade(game, market, {"team": team, "line": finite(line)})
            stats["graded"] += 1
            stats["result_match"] += regraded == stored_result
    return stats


# ------------------------------------------------------------------------------------------- report


def cluster_ci(df: pd.DataFrame, col: str, stake_col: str) -> tuple[float, float]:
    if df.empty:
        return float("nan"), float("nan")
    g = df.groupby("event_id")[[col, stake_col]].sum().to_numpy()
    rng = np.random.default_rng(SEED)
    idx = rng.integers(0, len(g), size=(BOOTSTRAP_DRAWS, len(g)))
    sums = g[idx].sum(axis=1)
    rois = sums[:, 0] / sums[:, 1]
    lo, hi = np.percentile(rois, [2.5, 97.5])
    return float(lo), float(hi)


def summary_line(name: str, df: pd.DataFrame) -> str:
    if df.empty:
        return f"| {name} | 0 | 0 | - | - | - | - | - | - |"
    n = len(df)
    w, lo_ = int((df.result == "win").sum()), int((df.result == "loss").sum())
    g, net = df.pnl_gross.sum(), df.pnl_net.sum()
    ci = cluster_ci(df, "pnl_gross", "stake")
    return (
        f"| {name} | {n} | {df.event_id.nunique()} | {w}-{lo_} | {g:+.2f} | {g / n:+.1%} | {net:+.2f} | "
        f"{net / n:+.1%} | {ci[0]:+.1%} .. {ci[1]:+.1%} |"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runtime", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--out", default=str(ROOT / "outputs/nba/TB_SETTLED_REPLAY.md"))
    parser.add_argument("--csv", default=str(ROOT / "outputs/nba/tb_settled_replay_picks.csv"))
    args = parser.parse_args()
    runtime = Path(args.runtime)
    ledger = runtime / "ledgers/ledgers.db"
    sigmas = {m: float(v["sigma"]) for m, v in json.loads(ANCHOR_ARTIFACT.read_text())["markets"].items()}

    games = load_games(ledger)
    snaps = load_snapshots(runtime)
    # Ledger rows without a slug in the rationale: match the snapshot event on tip and team names.
    by_tip: dict[str, list[str]] = defaultdict(list)
    for slug, recs in snaps.items():
        teams = {r.get("team") for r in recs if r.get("team")}
        by_tip[recs[0]["event_start_utc"][:16]].append(slug)
        for r in recs:
            r["_teams"] = teams
    for game in games.values():
        if game["slug"] in snaps:
            continue
        key = game["tip"].strftime("%Y-%m-%dT%H:%M")
        for slug in by_tip.get(key, []):
            if {game["home"], game["away"]} & snaps[slug][0]["_teams"]:
                game["slug"] = slug
    harness = harness_check(ledger, snaps, games)

    dk_live, dk_back = load_dk_live(runtime), load_dk_backfill()
    coverage = defaultdict(int)
    picks, everything = [], []
    for game in sorted(games.values(), key=lambda g: g["tip"]):
        coverage["games"] += 1
        if not game["slug"] or game["slug"] not in snaps:
            coverage["no_polymarket_event"] += 1
            continue
        quotes = latest_quotes(snaps[game["slug"]], game["tip"])
        if not quotes:
            coverage["no_quote_in_window"] += 1
            continue
        decision = max(q["_obs"] for q in quotes)
        dk = dk_for(game["event_id"], decision, dk_live, dk_back)
        if dk is None:
            coverage["no_draftkings"] += 1
            continue
        coverage["dk_" + dk["source"]] += 1
        for cand in contract_candidates(game, quotes, dk, sigmas):
            sides = [s for s in cand["sides"] if s["ask"] is not None and s["fair"] is not None]
            if not sides:
                continue
            side = max(sides, key=lambda s: s["fair"] - s["ask"])
            gap = side["fair"] - side["ask"]
            result = grade(game, cand["market"], side)
            price = side["ask"]
            row = {
                "event_id": game["event_id"],
                "tip_utc": game["tip"].isoformat(),
                "week": (
                    game["tip"].astimezone(ET).date() - timedelta(days=game["tip"].astimezone(ET).weekday())
                ).isoformat(),
                "matchup": f"{game['away']} @ {game['home']}",
                "market": cand["market"],
                "translated": cand["translated"],
                "side": side["label"],
                "ask": price,
                "fair": round(side["fair"], 4),
                "gap": round(gap, 4),
                "dk_source": dk["source"],
                "pit": dk["pit"],
                "quote_minutes_to_tip": round((game["tip"] - cand["quote"]["_obs"]).total_seconds() / 60, 1),
                "result": result,
                "stake": 1.0,
                "pnl_gross": pnl(result, price),
                "pnl_net": pnl(result, price + fee(price)),
            }
            everything.append(row)
            if gap >= TB_THRESHOLD:
                picks.append(row)

    all_df = pd.DataFrame(everything)
    pick_df = pd.DataFrame(picks)
    Path(args.csv).parent.mkdir(parents=True, exist_ok=True)
    all_df.to_csv(args.csv, index=False)

    lines = ["# T-B replayed over every settled NBA game", ""]
    lines.append(
        f"Generated by `scripts/nba_v10/tb_settled_replay.py`; threshold {TB_THRESHOLD:.3f} (pinned)."
    )
    lines += [
        "",
        "## Harness (stored Flat rows rebuilt from the snapshot archive)",
        "",
        "```",
        json.dumps(harness, indent=1, default=str),
        "```",
        "",
        "## Coverage",
        "",
        "```",
        json.dumps(dict(coverage), indent=1),
        "```",
        "",
    ]
    if not all_df.empty:
        age = all_df.quote_minutes_to_tip
        lines.append(f"Quote age before tip (min): median {age.median():.0f}, p90 {age.quantile(0.9):.0f}.")
        lines.append("")
    header = "| slice | picks | games | W-L | PnL gross | ROI | PnL net | ROI net | 95% CI ROI (games) |"
    sep = "|---|---|---|---|---|---|---|---|---|"
    lines += ["## T-B picks (gap >= 3.0 points)", "", header, sep]
    if not pick_df.empty:
        prim = pick_df[~pick_df.translated]
        lines.append(summary_line("PRIMARY: moneyline + same-line", prim))
        lines.append(summary_line("primary, PIT-clean DK (live capture)", prim[prim.pit]))
        lines.append(summary_line("primary, DK close (look-ahead)", prim[~prim.pit]))
        for market in ("moneyline", "spread", "total"):
            lines.append(summary_line(f"primary {market}", prim[prim.market == market]))
        lines.append(summary_line("translated lines (separate)", pick_df[pick_df.translated]))
        lines += [
            "",
            "### Primary cut by week (ET, Monday start)",
            "",
            "| week | picks | W-L | PnL gross | PnL net |",
            "|---|---|---|---|---|",
        ]
        for week, wk in prim.groupby("week"):
            w, lo_ = int((wk.result == "win").sum()), int((wk.result == "loss").sum())
            lines.append(
                f"| {week} | {len(wk)} | {w}-{lo_} | {wk.pnl_gross.sum():+.2f} | {wk.pnl_net.sum():+.2f} |"
            )
    lines += ["", "## Context only (not a gate): every evaluated contract, larger-gap side", "", header, sep]
    if not all_df.empty:
        lines.append(summary_line("all contracts, same-line + ML", all_df[~all_df.translated]))
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
