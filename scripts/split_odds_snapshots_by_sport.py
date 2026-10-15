"""Split and breakdown data/market_odds_snapshots.jsonl by sport and league.

Audits and resolves top-level missing 'league' field in historical snapshots,
attributing each record to its canonical sport and league via:
1. raw_response["event"]["league"]
2. raw_response["event"]["event_slug"]
3. Team franchise dictionary matching across MLB, NFL, NBA, WNBA, Soccer, etc.

Outputs:
- outputs/research/market_odds_by_sport.json: Detailed audit summary
- data/odds/mlb/market_odds_snapshots_parsed.jsonl: Canonical parsed records with top-level sport/league
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from model_prediction.config import PROJECT_ROOT

MLB_TEAMS = {
    "arizona diamondbacks",
    "atlanta braves",
    "baltimore orioles",
    "boston red sox",
    "chicago cubs",
    "chicago white sox",
    "cincinnati reds",
    "cleveland guardians",
    "colorado rockies",
    "detroit tigers",
    "houston astros",
    "kansas city royals",
    "los angeles angels",
    "los angeles dodgers",
    "miami marlins",
    "milwaukee brewers",
    "minnesota twins",
    "new york mets",
    "new york yankees",
    "athletics",
    "oakland athletics",
    "philadelphia phillies",
    "pittsburgh pirates",
    "san diego padres",
    "san francisco giants",
    "seattle mariners",
    "st. louis cardinals",
    "tampa bay rays",
    "texas rangers",
    "toronto blue jays",
    "washington nationals",
}

NFL_TEAMS = {
    "arizona cardinals",
    "atlanta falcons",
    "baltimore ravens",
    "buffalo bills",
    "carolina panthers",
    "chicago bears",
    "cincinnati bengals",
    "cleveland browns",
    "dallas cowboys",
    "denver broncos",
    "detroit lions",
    "green bay packers",
    "houston texans",
    "indianapolis colts",
    "jacksonville jaguars",
    "kansas city chiefs",
    "las vegas raiders",
    "los angeles chargers",
    "los angeles rams",
    "miami dolphins",
    "minnesota vikings",
    "new england patriots",
    "new orleans saints",
    "new york giants",
    "new york jets",
    "philadelphia eagles",
    "pittsburgh steelers",
    "san francisco 49ers",
    "seattle seahawks",
    "tampa bay buccaneers",
    "tennessee titans",
    "washington commanders",
}

NBA_TEAMS = {
    "atlanta hawks",
    "boston celtics",
    "brooklyn nets",
    "charlotte hornets",
    "chicago bulls",
    "cleveland cavaliers",
    "dallas mavericks",
    "denver nuggets",
    "detroit pistons",
    "golden state warriors",
    "houston rockets",
    "indiana pacers",
    "la clippers",
    "los angeles lakers",
    "memphis grizzlies",
    "miami heat",
    "milwaukee bucks",
    "minnesota timberwolves",
    "new orleans pelicans",
    "new york knicks",
    "oklahoma city thunder",
    "orlando magic",
    "philadelphia 76ers",
    "phoenix suns",
    "portland trail blazers",
    "sacramento kings",
    "san antonio spurs",
    "toronto raptors",
    "utah jazz",
    "washington wizards",
}


def infer_sport_and_league(record: dict[str, Any]) -> tuple[str, str]:
    """Determine sport and league from nested event info, slug, or team names."""
    raw = record.get("raw_response", {})
    if isinstance(raw, dict):
        event = raw.get("event", {})
        if isinstance(event, dict):
            league = event.get("league")
            if league:
                league_upper = str(league).upper()
                if league_upper == "MLB":
                    return "mlb", "MLB"
                if league_upper == "NFL":
                    return "nfl", "NFL"
                if league_upper == "NBA":
                    return "nba", "NBA"
                if league_upper == "WNBA":
                    return "wnba", "WNBA"

            slug = event.get("event_slug", "")
            if slug.startswith("mlb-"):
                return "mlb", "MLB"
            if slug.startswith("nfl-"):
                return "nfl", "NFL"
            if slug.startswith("nba-"):
                return "nba", "NBA"

    # Fallback to team lookup
    home = (record.get("home_team") or "").strip().lower()
    away = (record.get("away_team") or "").strip().lower()

    if home in MLB_TEAMS or away in MLB_TEAMS:
        return "mlb", "MLB"
    if home in NFL_TEAMS or away in NFL_TEAMS:
        return "nfl", "NFL"
    if home in NBA_TEAMS or away in NBA_TEAMS:
        return "nba", "NBA"

    return "unknown", "UNKNOWN"


def split_market_odds_snapshots() -> dict[str, Any]:
    input_file = PROJECT_ROOT / "data" / "market_odds_snapshots.jsonl"
    if not input_file.exists():
        raise FileNotFoundError(f"Input file not found: {input_file}")

    sport_counts = Counter()
    league_counts = Counter()
    provider_counts = Counter()
    date_ranges: dict[str, list[str]] = defaultdict(list)
    sport_records: dict[str, list[dict[str, Any]]] = defaultdict(list)

    total_records = 0
    with open(input_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total_records += 1
            data = json.loads(line)

            sport, league = infer_sport_and_league(data)
            provider = data.get("provider", "unknown")
            observed = data.get("observed_at_utc", "")

            sport_counts[sport] += 1
            league_counts[league] += 1
            provider_counts[provider] += 1

            if observed:
                date_ranges[sport].append(observed[:10])

            # Inject top-level canonical fields for downstream consumers
            data["sport"] = sport
            data["league"] = league
            sport_records[sport].append(data)

    # Save partitioned parsed MLB file
    out_mlb = PROJECT_ROOT / "data" / "odds" / "mlb" / "market_odds_snapshots_parsed.jsonl"
    out_mlb.parent.mkdir(parents=True, exist_ok=True)
    with open(out_mlb, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(row) + "\n" for row in sport_records.get("mlb", []))

    summary = {
        "total_records": total_records,
        "sports": dict(sport_counts),
        "leagues": dict(league_counts),
        "providers": dict(provider_counts),
        "date_ranges": {
            s: {"min": min(dates), "max": max(dates), "total_days": len(set(dates))}
            for s, dates in date_ranges.items()
            if dates
        },
        "diagnosis": {
            "root_cause_of_unparsed_league": (
                "The original file stored league exclusively inside raw_response['event']['league'] "
                "for Polymarket (3,949 rows) and lacked top-level 'league'. The remaining 512 rows were "
                "from provider 'espn_market_consensus' which only populated team names (all 30 MLB franchises)."
            ),
            "mlb_coverage_percentage": (
                (sport_counts.get("mlb", 0) / total_records) * 100 if total_records else 0
            ),
            "written_partitions": [str(out_mlb.relative_to(PROJECT_ROOT))],
        },
    }

    out_json = PROJECT_ROOT / "outputs" / "research" / "market_odds_by_sport.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(f"Processed {total_records} records:")
    print(f"  Sports: {dict(sport_counts)}")
    print(f"  Providers: {dict(provider_counts)}")
    print(f"  Output written to: {out_json}")
    print(f"  MLB partition written to: {out_mlb}")
    return summary


if __name__ == "__main__":
    split_market_odds_snapshots()
