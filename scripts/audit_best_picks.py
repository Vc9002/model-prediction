#!/usr/bin/env python3
"""Best Picks Empirical Audit Tool.

Audits candidate or open picks against 2,300+ historical settled records in ledgers.db.
Evaluates picks beyond nominal edge using:
1. Historical Edge Tier Win Rate & ROI
2. Directional Asymmetry (Totals Over vs Under; Spread Favorites vs Underdogs)
3. Regime/League Traps (e.g. early NBA Over inflation, early NCAAB 12%+ spread noise)
4. Market Disagreement Limits (avoiding injury/weather information traps)

Classification Verdicts:
- CONFIRMED_BEST: evidence counts only verified-quote rows (see VERIFIED_QUOTE_PROVENANCE
  below) in the candidate's bucket, reaching >= BEST_MIN_WIN_RATE WR and >= BEST_MIN_ROI
  ROI, AND the candidate's own quote is itself verified. A mechanical bucket verdict of
  CONFIRMED_BEST computed from an unverified candidate quote is withheld and reported as
  QUALIFIED_MODERATE instead (the bucket_verdict key still carries the mechanical verdict).
- QUALIFIED_MODERATE: neither confirmed nor a trap -- positive or inconclusive evidence,
  normal sizing recommended.
- DOWNGRADE_TRAP: verified WR < TRAP_WIN_RATE or verified ROI < TRAP_ROI, or negative
  nominal edge.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import zoneinfo
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DB_PATH = Path("E:/model-prediction-runtime/ledgers/ledgers.db")
ET_TZ = zoneinfo.ZoneInfo("America/New_York")

# forecast.py stamps this provenance only after a timestamp-valid, non-reconstructed
# Polymarket US snapshot taken at decision time. Every other value (empty, missing,
# indicative_slate, archived_two_sided_bbo, decision_time_sportsbook_quote) means the
# recorded price was not verified as executable at decision time, so it is excluded from
# any verdict evidence -- this is a literal operator rule, not a quality heuristic to widen.
VERIFIED_QUOTE_PROVENANCE = "decision_time_executable_quote"

MIN_GRADED = 4
BEST_MIN_WIN_RATE = 60.0
BEST_MIN_ROI = 15.0
TRAP_WIN_RATE = 55.0
TRAP_ROI = 5.0


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
    # Negative-edge flat calls are mostly favourites, so pooling them with 0-2% edges made
    # that bucket look like a high-win-rate profit centre for picks the model itself dislikes.
    if pct < 0.0:
        return "< 0%"
    elif pct < 2.0:
        return "< 2%"
    elif pct < 5.0:
        return "2.0% - 4.9%"
    elif pct < 8.0:
        return "5.0% - 7.9%"
    elif pct < 12.0:
        return "8.0% - 11.9%"
    else:
        return ">= 12.0%"


@dataclass
class BucketStats:
    """All settled rows in a bucket, split by whether the row's own quote was verified.

    Only `.verified` counts toward any verdict; `.all_rows` is retained for context so a
    report can show "0 verified of N graded" instead of silently dropping the bucket.
    """

    all_rows: EdgeTierStats = field(default_factory=EdgeTierStats)
    verified: EdgeTierStats = field(default_factory=EdgeTierStats)


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    # ledgers.db is the canonical ledger; this report must never mutate it, and opening a
    # plain sqlite3.connect() on a path that doesn't yet exist silently creates an empty
    # file there instead of erroring -- mode=ro refuses both failure modes.
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


class HistoricalAuditEngine:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self.stats_by_sport_mkt_bucket: dict[tuple[str, str, str], BucketStats] = defaultdict(BucketStats)
        self.stats_by_sport_dir_bucket: dict[tuple[str, str, str], BucketStats] = defaultdict(BucketStats)
        # A pooled bucket mixes retired models with the serving one, so a bucket can look
        # profitable purely on a predecessor's record; these hold the candidate's own model.
        self.stats_by_model_mkt_bucket: dict[tuple[str, str, str, str], BucketStats] = defaultdict(
            BucketStats
        )
        self.stats_by_model_dir_bucket: dict[tuple[str, str, str, str], BucketStats] = defaultdict(
            BucketStats
        )
        self._load_historical_settlements()

    def _load_historical_settlements(self) -> None:
        if not self.db_path.exists():
            return
        conn = _connect_ro(self.db_path)
        rows = conn.execute(
            """
            SELECT sport, market_type, model_probability, market_probability,
                   selection, line, pnl_units, result, units, event_id, model_id,
                   CASE WHEN json_valid(decision_payload_json)
                        THEN json_extract(decision_payload_json, '$.market_quote_provenance')
                   END AS quote_provenance
            FROM ledger_records
            WHERE status = 'settled'
            ORDER BY CASE ledger_tier WHEN 'main' THEN 0 ELSE 1 END, created_at_utc
            """
        ).fetchall()
        conn.close()

        # The same call is logged once per ledger tier (Main and Flat); counting both rows
        # inflates n and double-weights those games. Main's row wins when both exist.
        seen: set[tuple[str, str, str, str, str]] = set()
        for r in rows:
            res = str(r["result"] or "").upper()
            if res not in ("WIN", "LOSS", "PUSH"):
                continue
            sport = str(r["sport"] or "").lower()
            mkt = str(r["market_type"] or "").lower()
            model_id = str(r["model_id"] or "").lower()
            event_id = str(r["event_id"] or "")
            if event_id:
                key = (sport, event_id, mkt, str(r["selection"] or "").lower(), model_id)
                if key in seen:
                    continue
                seen.add(key)
            mod_p = float(r["model_probability"] or 0.0)
            mkt_p = float(r["market_probability"] or 0.0)
            edge = mod_p - mkt_p
            bucket = get_edge_bucket(edge)
            pnl = float(r["pnl_units"] or 0.0)
            staked = float(r["units"] or 1.0)
            sel = str(r["selection"] or "").lower()
            is_verified = r["quote_provenance"] == VERIFIED_QUOTE_PROVENANCE

            entries: list[BucketStats] = [
                self.stats_by_sport_mkt_bucket[(sport, mkt, bucket)],
                self.stats_by_model_mkt_bucket[(sport, mkt, bucket, model_id)],
            ]
            if mkt == "total" and sel in ("over", "under"):
                entries.append(self.stats_by_sport_dir_bucket[(sport, sel, bucket)])
                entries.append(self.stats_by_model_dir_bucket[(sport, sel, bucket, model_id)])
            for bucket_stats in entries:
                for entry in (
                    (bucket_stats.all_rows,)
                    if not is_verified
                    else (bucket_stats.all_rows, bucket_stats.verified)
                ):
                    if res == "WIN":
                        entry.wins += 1
                    elif res == "LOSS":
                        entry.losses += 1
                    elif res == "PUSH":
                        entry.pushes += 1
                    entry.pnl += pnl
                    entry.staked += staked

    def audit_candidate(
        self,
        sport: str,
        market_type: str,
        selection: str,
        edge: float,
        model_prob: float,
        market_prob: float,
        model_id: str | None = None,
        quote_provenance: str | None = None,
    ) -> dict[str, Any]:
        sp = sport.lower()
        mkt = market_type.lower()
        sel = selection.lower()
        bucket = get_edge_bucket(edge)
        mid = (model_id or "").lower()
        prov = quote_provenance or ""
        quote_verified = prov == VERIFIED_QUOTE_PROVENANCE

        pooled = self.stats_by_sport_mkt_bucket.get((sp, mkt, bucket), BucketStats())
        model = self.stats_by_model_mkt_bucket.get((sp, mkt, bucket, mid), BucketStats())
        # Judge on the candidate's own model when it has enough VERIFIED graded history;
        # otherwise the pooled bucket is the only evidence there is, and a flag says so.
        use_model = bool(mid) and model.verified.total_graded >= MIN_GRADED
        evidence = model if use_model else pooled
        basis = f"{model_id} only" if use_model else "all models pooled"

        # Determine verdict
        verdict = "QUALIFIED_MODERATE"
        flags: list[str] = []

        # 1. Check Totals directional bias
        if mkt == "total":
            if sp == "nba" and sel == "over":
                # NBA Overs bleed heavily all-time (7W-24L, 22.6% WR, -19.67u, -53.0% ROI)
                verdict = "DOWNGRADE_TRAP"
                flags.append(
                    "NBA Overs bleed heavily (7W-24L, 22.6% WR, -19.67u, -53.0% ROI). Market totals inflated."
                )
            elif sp == "nba" and sel == "under" and bucket in ("5.0% - 7.9%", "8.0% - 11.9%", ">= 12.0%"):
                # NBA Unders with >= 5% edge are a confirmed profit center (35W-25L, 58.3% WR, +14.86u, +15.2% ROI)
                verdict = "CONFIRMED_BEST"
                flags.append(
                    "NBA Unders >= 5% edge hit 58.3% WR (35W-25L, +14.86u, +15.2% ROI); Unders >= 9% hit 56.6% WR (+11.34u)."
                )
            elif sp == "nba" and sel == "under" and bucket in ("< 2%", "2.0% - 4.9%"):
                # Sub-5% NBA Unders are near-breakeven vig churn (15W-18L, 45.5% WR, -6.82u)
                verdict = "DOWNGRADE_TRAP"
                flags.append(
                    "NBA Unders < 5% edge hit only 45.5% WR (15W-18L, -6.82u, -16.8% ROI) - sub-vig noise."
                )
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

        # 3. Check empirical edge bucket overall if no override. Only VERIFIED rows count
        # toward a verdict; an unverified-but-large all-rows record is shown as context only.
        if verdict == "QUALIFIED_MODERATE":
            v = evidence.verified
            a = evidence.all_rows
            if v.total_graded >= MIN_GRADED:
                if v.win_rate >= BEST_MIN_WIN_RATE and v.roi >= BEST_MIN_ROI:
                    verdict = "CONFIRMED_BEST"
                    flags.append(
                        f"Bucket {bucket} meets Gold Standard ({basis}): verified {v.win_rate:.1f}% WR, {v.roi:+.1f}% ROI ({v.wins}W-{v.losses}L)."
                    )
                elif v.win_rate < TRAP_WIN_RATE or v.roi < TRAP_ROI:
                    verdict = "DOWNGRADE_TRAP"
                    flags.append(
                        f"Bucket {bucket} fails Best Pick criteria ({basis}): verified {v.win_rate:.1f}% WR, {v.roi:+.1f}% ROI ({v.wins}W-{v.losses}L; near-breakeven or negative)."
                    )
            else:
                flags.append(
                    f"insufficient verified history in bucket {bucket} ({basis}): {v.total_graded} verified of {a.total_graded} graded; all-rows record {a.wins}W-{a.losses}L"
                )
        if edge < 0:
            # A pick the model prices below the market can never be a best pick, whatever
            # the bucket's win rate says.
            verdict = "DOWNGRADE_TRAP"
            flags = [
                (
                    f"Negative edge ({edge * 100:+.1f}%): model probability is below the market price; "
                    f"verified bucket record {evidence.verified.wins}W-{evidence.verified.losses}L, {evidence.verified.roi:+.1f}% ROI ({basis})."
                )
            ]
        elif mid and not use_model:
            flags.append(
                f"{model_id} has only {model.verified.total_graded} verified graded picks in bucket {bucket}; verdict rests on the pooled bucket."
            )
        elif use_model and pooled.verified.total_graded:
            flags.append(
                f"Context, all models pooled: verified {pooled.verified.win_rate:.1f}% WR, {pooled.verified.roi:+.1f}% ROI ({pooled.verified.wins}W-{pooled.verified.losses}L)."
            )

        bucket_verdict = verdict
        if bucket_verdict == "CONFIRMED_BEST" and not quote_verified:
            # The mechanical bucket evidence qualifies, but the candidate's own quote was
            # never verified as executable at decision time -- withhold the label rather than
            # present an unverified-quote pick as a proven best pick.
            verdict = "QUALIFIED_MODERATE"
            flags.append(
                f"mechanical bucket verdict CONFIRMED_BEST withheld: candidate quote provenance is '{prov or 'missing'}', not {VERIFIED_QUOTE_PROVENANCE}"
            )

        return {
            "verdict": verdict,
            "bucket_verdict": bucket_verdict,
            "flags": flags,
            "bucket": bucket,
            "basis": basis,
            "evidence": evidence,
            "pooled": pooled,
            "model": model,
            "quote_provenance": prov,
            "quote_verified": quote_verified,
        }


def _sel_name(p: dict[str, Any]) -> str:
    return (
        p["home"]
        if p["selection"] == "home"
        else (p["away"] if p["selection"] == "away" else p["selection"].upper())
    )


def _line_str(p: dict[str, Any]) -> str:
    if p["line"] is None:
        return ""
    return f"{p['line']:+.1f}" if p["market"] == "SPREAD" else f"{p['line']:.1f}"


def _evidence_line(p: dict[str, Any]) -> str:
    # Live-only line: basis, verified W-L, verified-of-graded, verified WR/ROI (percentages
    # only, never American odds -- CLAUDE.md directive), and the candidate's own quote
    # provenance, so every printed pick shows exactly what evidence backed its verdict.
    v = p["evidence"].verified
    a = p["evidence"].all_rows
    prov = p["quote_provenance"] or "missing"
    return (
        f"      >>> live evidence ({p['basis']}): verified {v.wins}W-{v.losses}L "
        f"({v.total_graded} verified of {a.total_graded} graded), {v.win_rate:.1f}% WR, "
        f"{v.roi:+.1f}% ROI | quote: {prov}"
    )


def collect_open_pick_audits(
    db_path: Path = DB_PATH, only_unstarted: bool = True, pick_id: str | None = None
) -> list[dict[str, Any]]:
    engine = HistoricalAuditEngine(db_path)
    now_utc = datetime.now(UTC)

    conn = _connect_ro(db_path)
    rows = conn.execute(
        """
        SELECT pick_id, sport, ledger_tier, market_type, selection, line,
               model_probability, market_probability, units, event_start_utc, decision_payload_json,
               model_id
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
        if pick_id and r["pick_id"] != pick_id:
            continue
        dt_utc = datetime.fromisoformat(start_utc_str)
        if only_unstarted and dt_utc <= now_utc:
            continue

        try:
            p = json.loads(r["decision_payload_json"] or "{}")
        except (ValueError, TypeError):
            # A malformed open-pick payload must not abort the audit; it just means this
            # candidate's own quote provenance is unknown and is reported as "missing".
            p = {}
        away = p.get("away_team") or p.get("original_away_team") or ""
        home = p.get("home_team") or p.get("original_home_team") or ""
        quote_provenance = p.get("market_quote_provenance") or ""
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
            model_id=r["model_id"],
            quote_provenance=quote_provenance,
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
                "bucket_verdict": audit["bucket_verdict"],
                "flags": audit["flags"],
                "bucket": audit["bucket"],
                "basis": audit["basis"],
                "evidence": audit["evidence"],
                "quote_provenance": audit["quote_provenance"],
            }
        )
    return audited_picks


def audit_open_picks(
    db_path: Path = DB_PATH, only_unstarted: bool = True, pick_id: str | None = None
) -> None:
    audited_picks = collect_open_pick_audits(db_path, only_unstarted=only_unstarted, pick_id=pick_id)
    now_utc = datetime.now(UTC)

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

    print(
        f"\n[1] CONFIRMED BEST PICKS ({len(best_picks)} picks; verified bucket evidence and verified candidate quote)"
    )
    print("-" * 80)
    if not best_picks:
        print("  None found.")
    for p in sorted(best_picks, key=lambda x: (x["tier"] != "Main", -x["edge"])):
        sel_name = _sel_name(p)
        line_str = _line_str(p)
        print(f"  [{p['tier']}] [{p['start_et']}] {p['sport']} | {p['away']} @ {p['home']}")
        print(
            f"      {p['market']} -> {sel_name} {line_str} | Mod: {p['model_prob'] * 100:.1f}% | Mkt: {p['market_prob'] * 100:.1f}% | Edge: {p['edge'] * 100:+.1f}% | Stake: {p['units']:.2f}u"
        )
        for f in p["flags"]:
            print(f"      >>> EVIDENCE: {f}")
        print(_evidence_line(p))
        if p["bucket_verdict"] != p["verdict"]:
            print(f"      >>> mechanical bucket verdict: {p['bucket_verdict']}")
        print()

    print(f"\n[2] DOWNGRADE / FADE CANDIDATES ({len(trap_picks)} nominal edge traps)")
    print("-" * 80)
    if not trap_picks:
        print("  None found.")
    for p in trap_picks:
        sel_name = _sel_name(p)
        line_str = _line_str(p)
        print(f"  [{p['tier']}] [{p['start_et']}] {p['sport']} | {p['away']} @ {p['home']}")
        print(
            f"      {p['market']} -> {sel_name} {line_str} | Nominal Edge: {p['edge'] * 100:+.1f}% | Stake: {p['units']:.2f}u"
        )
        for f in p["flags"]:
            print(f"      >>> WARNING: {f}")
        print(_evidence_line(p))
        if p["bucket_verdict"] != p["verdict"]:
            print(f"      >>> mechanical bucket verdict: {p['bucket_verdict']}")
        print()

    print(f"\n[3] QUALIFIED MODERATE PICKS ({len(moderate_picks)} baseline picks)")
    print("-" * 80)
    for p in moderate_picks[:10]:
        sel_name = _sel_name(p)
        line_str = _line_str(p)
        print(
            f"  [{p['tier']}] [{p['start_et']}] {p['sport']:<5} | {p['away']} @ {p['home']} | {p['market']} {sel_name} {line_str} | Edge: {p['edge'] * 100:+.1f}% | Stake: {p['units']:.2f}u"
        )
        for f in p["flags"]:
            print(f"      >>> NOTE: {f}")
        print(_evidence_line(p))
        if p["bucket_verdict"] != p["verdict"]:
            print(f"      >>> mechanical bucket verdict: {p['bucket_verdict']}")
    if len(moderate_picks) > 10:
        print(f"  ... and {len(moderate_picks) - 10} more.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit open or candidate picks against historical edge-tier performance."
    )
    parser.add_argument(
        "--all-open", action="store_true", help="Audit all open picks including games already underway."
    )
    parser.add_argument("--pick-id", help="Audit only this open pick_id.")
    args = parser.parse_args()

    audit_open_picks(only_unstarted=not args.all_open, pick_id=args.pick_id)


if __name__ == "__main__":
    main()
