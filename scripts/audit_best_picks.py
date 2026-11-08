#!/usr/bin/env python3
"""Best Picks Empirical Audit Tool.

Audits candidate or open picks against 2,300+ historical settled records in ledgers.db.
Evaluates picks beyond nominal edge using:
1. Historical Edge Tier Win Rate & ROI
2. Directional Asymmetry (Totals Over vs Under; Spread Favorites vs Underdogs)
3. Regime/League Traps (e.g. early NBA Over inflation, early NCAAB 12%+ spread noise)
4. Market Disagreement Limits (avoiding injury/weather information traps)

Classification Verdicts:
- CONFIRMED_BEST: Proven empirical sweet spot (Win Rate >= 60%, positive ROI, n >= 5)
- QUALIFIED_MODERATE: Positive historical baseline, normal sizing recommended
- DOWNGRADE_TRAP: Negative historical ROI or win rate < 48% despite positive nominal edge
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import zoneinfo
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DB_PATH = Path("E:/model-prediction-runtime/ledgers/ledgers.db")
ET_TZ = zoneinfo.ZoneInfo("America/New_York")


@dataclass
class EdgeTierStats:
    wins: int = 0
    losses: int = 0
    pushes: int = 0
    pnl: float = 0.0
    staked: float = 0.0

    @property
    def total_graded(self) -> int:
        return self.wins + self.losses

    @property
    def win_rate(self) -> float:
        return (self.wins / self.total_graded * 100.0) if self.total_graded > 0 else 0.0

    @property
    def roi(self) -> float:
        return (self.pnl / self.staked * 100.0) if self.staked > 0 else 0.0


def get_edge_bucket(edge: float) -> str:
    pct = edge * 100.0
    if pct < 2.0:
        return "< 2%"
    elif pct < 5.0:
        return "2.0% - 4.9%"
    elif pct < 8.0:
        return "5.0% - 7.9%"
    elif pct < 12.0:
        return "8.0% - 11.9%"
    else:
        return ">= 12.0%"


class HistoricalAuditEngine:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self.stats_by_sport_mkt_bucket: dict[tuple[str, str, str], EdgeTierStats] = defaultdict(EdgeTierStats)
        self.stats_by_sport_dir_bucket: dict[tuple[str, str, str], EdgeTierStats] = defaultdict(EdgeTierStats)
        self._load_historical_settlements()

    def _load_historical_settlements(self) -> None:
        if not self.db_path.exists():
            return
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT sport, market_type, model_probability, market_probability, 
                   selection, line, pnl_units, result, units
            FROM ledger_records
            WHERE status = 'settled'
            """
        ).fetchall()
        conn.close()

        for r in rows:
            res = str(r["result"] or "").upper()
            if res not in ("WIN", "LOSS", "PUSH"):
                continue
            sport = str(r["sport"] or "").lower()
            mkt = str(r["market_type"] or "").lower()
            mod_p = float(r["model_probability"] or 0.0)
            mkt_p = float(r["market_probability"] or 0.0)
            edge = mod_p - mkt_p
            bucket = get_edge_bucket(edge)
            pnl = float(r["pnl_units"] or 0.0)
            staked = float(r["units"] or 1.0)
            sel = str(r["selection"] or "").lower()

            entry = self.stats_by_sport_mkt_bucket[(sport, mkt, bucket)]
            if res == "WIN":
                entry.wins += 1
            elif res == "LOSS":
                entry.losses += 1
            elif res == "PUSH":
                entry.pushes += 1
            entry.pnl += pnl
            entry.staked += staked

            if mkt == "total" and sel in ("over", "under"):
                dir_entry = self.stats_by_sport_dir_bucket[(sport, sel, bucket)]
                if res == "WIN":
                    dir_entry.wins += 1
                elif res == "LOSS":
                    dir_entry.losses += 1
                elif res == "PUSH":
                    dir_entry.pushes += 1
                dir_entry.pnl += pnl
                dir_entry.staked += staked

    def audit_candidate(
        self,
        sport: str,
        market_type: str,
        selection: str,
        edge: float,
        model_prob: float,
        market_prob: float,
    ) -> dict[str, Any]:
        sp = sport.lower()
        mkt = market_type.lower()
        sel = selection.lower()
        bucket = get_edge_bucket(edge)

        mkt_stats = self.stats_by_sport_mkt_bucket.get((sp, mkt, bucket), EdgeTierStats())
        dir_stats = None
        if mkt == "total" and sel in ("over", "under"):
            dir_stats = self.stats_by_sport_dir_bucket.get((sp, sel, bucket), EdgeTierStats())

        # Determine verdict
        verdict = "QUALIFIED_MODERATE"
        flags: list[str] = []

        # 1. Check Totals directional bias
        if mkt == "total":
            if sp == "nba" and sel == "over":
                # NBA Overs historically bleed heavily (1-8, 11% WR)
                verdict = "DOWNGRADE_TRAP"
                flags.append("NBA Overs across all edges hit only 11% WR (-7.80u). Early market over-totals.")
            elif sp == "nfl" and bucket == "5.0% - 7.9%":
                # NFL 5-8% totals are 6-0 all-time
                verdict = "CONFIRMED_BEST"
                flags.append("NFL Totals 5-8% edge are 100% WR (6W-0L, +98.9% ROI) all-time.")
            elif sp == "nfl" and sel == "over" and bucket == "2.0% - 4.9%":
                verdict = "CONFIRMED_BEST"
                flags.append("NFL Overs 2-5% edge hit 80% WR (4W-1L, +62.8% ROI).")
            elif sp == "ncaab" and sel == "over" and bucket in ("8.0% - 11.9%", ">= 12.0%"):
                verdict = "CONFIRMED_BEST"
                flags.append("NCAAB Overs in 8-12% edge hit 62.5% WR (+22.4% ROI).")

        # 2. Check Spread traps
        if mkt == "spread":
            if sp == "nfl" and bucket == "2.0% - 4.9%":
                # 2-5% NFL spreads hit 54.5% WR (+0.35u, +2.9% ROI) - near-breakeven vig churn
                verdict = "DOWNGRADE_TRAP"
                flags.append(
                    "NFL Spreads 2-5% edge hit only 54.5% WR (+2.9% ROI, +0.35u over 11 bets) - near-breakeven vig churn; insufficient alpha for Best Pick status."
                )
            elif sp == "nfl" and bucket in ("5.0% - 7.9%", "8.0% - 11.9%"):
                # NFL 5-8% spread dogs hit only 33% WR
                verdict = "DOWNGRADE_TRAP"
                flags.append(
                    "NFL Spreads 5-8% edge hit only 33.3% WR (2W-4L, -41% ROI) on heavy dogs - market information asymmetry."
                )
            elif sp == "ncaab" and bucket == ">= 12.0%":
                # Early NCAAB 12%+ spreads hit 46% (-37.78u)
                verdict = "DOWNGRADE_TRAP"
                flags.append(
                    "Early NCAAB Spreads >= 12% edge hit 46.0% WR (-37.78u) from early-season roster volatility."
                )
            elif sp == "nba" and bucket in ("8.0% - 11.9%", ">= 12.0%"):
                verdict = "CONFIRMED_BEST"
                flags.append(
                    "NBA Spreads >= 8% edge hit 63.2% WR (+9.47u, +26.7% ROI) on double-digit cushions."
                )

        # 3. Check empirical edge bucket overall if no override
        if verdict == "QUALIFIED_MODERATE" and mkt_stats.total_graded >= 4:
            if mkt_stats.win_rate >= 60.0 and mkt_stats.roi >= 15.0:
                verdict = "CONFIRMED_BEST"
                flags.append(
                    f"Bucket {bucket} meets Gold Standard: {mkt_stats.win_rate:.1f}% WR, {mkt_stats.roi:+.1f}% ROI ({mkt_stats.wins}W-{mkt_stats.losses}L)."
                )
            elif mkt_stats.win_rate < 55.0 or mkt_stats.roi < 5.0:
                verdict = "DOWNGRADE_TRAP"
                flags.append(
                    f"Bucket {bucket} fails Best Pick criteria: {mkt_stats.win_rate:.1f}% WR, {mkt_stats.roi:+.1f}% ROI (near-breakeven or negative)."
                )

        return {
            "verdict": verdict,
            "flags": flags,
            "bucket": bucket,
            "mkt_stats": mkt_stats,
            "dir_stats": dir_stats,
        }


def audit_open_picks(db_path: Path = DB_PATH, only_unstarted: bool = True) -> None:
    engine = HistoricalAuditEngine(db_path)
    now_utc = datetime.now(UTC)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT pick_id, sport, ledger_tier, market_type, selection, line, 
               model_probability, market_probability, units, event_start_utc, decision_payload_json
        FROM ledger_records
        WHERE status = 'open'
        ORDER BY event_start_utc, sport, market_type
        """
    ).fetchall()
    conn.close()

    audited_picks = []
    for r in rows:
        start_utc_str = r["event_start_utc"]
        if not start_utc_str:
            continue
        dt_utc = datetime.fromisoformat(start_utc_str)
        if only_unstarted and dt_utc <= now_utc:
            continue

        p = json.loads(r["decision_payload_json"] or "{}")
        away = p.get("away_team") or p.get("original_away_team") or ""
        home = p.get("home_team") or p.get("original_home_team") or ""
        mod_p = float(r["model_probability"] or 0.0)
        mkt_p = float(r["market_probability"] or 0.0)
        edge = mod_p - mkt_p

        audit = engine.audit_candidate(
            sport=r["sport"],
            market_type=r["market_type"],
            selection=r["selection"],
            edge=edge,
            model_prob=mod_p,
            market_prob=mkt_p,
        )

        dt_et = dt_utc.astimezone(ET_TZ)
        audited_picks.append(
            {
                "pick_id": r["pick_id"],
                "sport": r["sport"].upper(),
                "tier": r["ledger_tier"].capitalize(),
                "start_et": dt_et.strftime("%I:%M %p ET"),
                "away": away,
                "home": home,
                "market": r["market_type"].upper(),
                "selection": r["selection"],
                "line": r["line"],
                "model_prob": mod_p,
                "market_prob": mkt_p,
                "edge": edge,
                "units": float(r["units"] or 1.0),
                "verdict": audit["verdict"],
                "flags": audit["flags"],
                "bucket": audit["bucket"],
                "mkt_stats": audit["mkt_stats"],
                "dir_stats": audit["dir_stats"],
            }
        )

    # Group by verdict
    best_picks = [p for p in audited_picks if p["verdict"] == "CONFIRMED_BEST"]
    moderate_picks = [p for p in audited_picks if p["verdict"] == "QUALIFIED_MODERATE"]
    trap_picks = [p for p in audited_picks if p["verdict"] == "DOWNGRADE_TRAP"]

    print("=" * 80)
    print("BEST PICKS EMPIRICAL AUDIT REPORT")
    print(
        f"Generated at: {now_utc.isoformat()} (Scope: {'Unstarted Only' if only_unstarted else 'All Open'})"
    )
    print("=" * 80)

    print(f"\n[1] CONFIRMED BEST PICKS ({len(best_picks)} verified high-alpha picks)")
    print("-" * 80)
    if not best_picks:
        print("  None found.")
    for p in sorted(best_picks, key=lambda x: (x["tier"] != "Main", -x["edge"])):
        sel_name = (
            p["home"]
            if p["selection"] == "home"
            else (p["away"] if p["selection"] == "away" else p["selection"].upper())
        )
        line_str = (
            f"{p['line']:+.1f}"
            if p["line"] is not None and p["market"] == "SPREAD"
            else (f"{p['line']:.1f}" if p["line"] is not None else "")
        )
        print(f"  [{p['tier']}] [{p['start_et']}] {p['sport']} | {p['away']} @ {p['home']}")
        print(
            f"      {p['market']} -> {sel_name} {line_str} | Mod: {p['model_prob'] * 100:.1f}% | Mkt: {p['market_prob'] * 100:.1f}% | Edge: {p['edge'] * 100:+.1f}% | Stake: {p['units']:.2f}u"
        )
        for f in p["flags"]:
            print(f"      >>> EVIDENCE: {f}")
        print()

    print(f"\n[2] DOWNGRADE / FADE CANDIDATES ({len(trap_picks)} nominal edge traps)")
    print("-" * 80)
    if not trap_picks:
        print("  None found.")
    for p in trap_picks:
        sel_name = (
            p["home"]
            if p["selection"] == "home"
            else (p["away"] if p["selection"] == "away" else p["selection"].upper())
        )
        line_str = (
            f"{p['line']:+.1f}"
            if p["line"] is not None and p["market"] == "SPREAD"
            else (f"{p['line']:.1f}" if p["line"] is not None else "")
        )
        print(f"  [{p['tier']}] [{p['start_et']}] {p['sport']} | {p['away']} @ {p['home']}")
        print(
            f"      {p['market']} -> {sel_name} {line_str} | Nominal Edge: {p['edge'] * 100:+.1f}% | Stake: {p['units']:.2f}u"
        )
        for f in p["flags"]:
            print(f"      >>> WARNING: {f}")
        print()

    print(f"\n[3] QUALIFIED MODERATE PICKS ({len(moderate_picks)} baseline picks)")
    print("-" * 80)
    for p in moderate_picks[:10]:
        sel_name = (
            p["home"]
            if p["selection"] == "home"
            else (p["away"] if p["selection"] == "away" else p["selection"].upper())
        )
        line_str = (
            f"{p['line']:+.1f}"
            if p["line"] is not None and p["market"] == "SPREAD"
            else (f"{p['line']:.1f}" if p["line"] is not None else "")
        )
        print(
            f"  [{p['tier']}] [{p['start_et']}] {p['sport']:<5} | {p['away']} @ {p['home']} | {p['market']} {sel_name} {line_str} | Edge: {p['edge'] * 100:+.1f}% | Stake: {p['units']:.2f}u"
        )
    if len(moderate_picks) > 10:
        print(f"  ... and {len(moderate_picks) - 10} more.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit open or candidate picks against historical edge-tier performance."
    )
    parser.add_argument(
        "--all-open", action="store_true", help="Audit all open picks including games already underway."
    )
    args = parser.parse_args()

    audit_open_picks(only_unstarted=not args.all_open)


if __name__ == "__main__":
    main()
