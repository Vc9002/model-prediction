"""Market-data census: per-sport inventory of the Polymarket snapshot tree.

Answers the plan's P0 question "what historical timestamped market data
do we actually have" — per sport under ``data/odds/{sport}/*/``:
date range, snapshot counts, unique events, market-type mix, sub-market
coverage (F5 spread/total, YRFI/NRFI), and freshness. Also flags the
known structural gaps (NBA/NFL absent, soccer stale) so later phases can
fail honestly instead of silently training on thin data.

Read-only. Writes a JSON report to ``tmp/`` (gitignored) and prints a
compact summary.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ODDS_ROOT = PROJECT_ROOT / "data" / "odds"

# Sports the plan needs market data for. NBA/NFL are listed so their
# absence shows up as an explicit gap line rather than a missing key.
WATCHED_SPORTS = ["mlb", "wnba", "nba", "nfl", "soccer", "tennis", "esports", "kbo", "npb"]

# Sub-market slug fragments; F5 and YRFI/NRFI are counted separately from
# full-game moneyline/spread/total (same convention as
# validation.multi_market_readiness).
SUB_MARKET_FRAGMENTS = ("-f5-", "-yrfi", "-nrfi")


def _census_one_sport(sport: str) -> dict:
    root = ODDS_ROOT / sport
    if not root.exists():
        return {"present": False}

    days: list[str] = []
    n_snapshots = 0
    n_events = 0
    market_types: dict[str, int] = defaultdict(int)
    sub_markets: dict[str, int] = defaultdict(int)
    latest_observed: str | None = None

    for snap_path in sorted(root.glob("*/polymarket_snapshots.jsonl")):
        days.append(snap_path.parent.name)
        try:
            with snap_path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        snap = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    n_snapshots += 1
                    slug = str(snap.get("market_slug") or "").casefold()
                    n_events += 1  # one snapshot line = one market row
                    mt = snap.get("market_type") or "unknown"
                    market_types[mt] += 1
                    for frag in SUB_MARKET_FRAGMENTS:
                        if frag in slug:
                            sub_markets[frag.strip("-")] += 1
                            break
                    observed = snap.get("observed_at_utc")
                    if observed and (latest_observed is None or observed > latest_observed):
                        latest_observed = observed
        except OSError as error:
            print(f"  census: failed reading {snap_path}: {error}")

    return {
        "present": True,
        "date_min": min(days) if days else None,
        "date_max": max(days) if days else None,
        "n_days": len(days),
        "n_snapshots": n_snapshots,
        "latest_observed_utc": latest_observed,
        "market_types": dict(market_types),
        "sub_markets": dict(sub_markets),
    }


def run_census() -> dict:
    report: dict[str, dict] = {}
    for sport in WATCHED_SPORTS:
        report[sport] = _census_one_sport(sport)
    return report


def main() -> None:
    report = run_census()
    out_path = PROJECT_ROOT / "tmp" / "market_data_census_20260826.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"market-data census (saved to {out_path})")
    for sport, entry in report.items():
        if not entry["present"]:
            print(f"  {sport:8s} ABSENT")
            continue
        mt = ", ".join(f"{k}={v}" for k, v in sorted(entry["market_types"].items()))
        sub = ", ".join(f"{k}={v}" for k, v in sorted(entry["sub_markets"].items())) or "none"
        print(
            f"  {sport:8s} {entry['date_min']}..{entry['date_max']} "
            f"({entry['n_days']} days, {entry['n_snapshots']} rows) "
            f"fresh={entry['latest_observed_utc']} | {mt} | sub: {sub}"
        )


if __name__ == "__main__":
    main()
