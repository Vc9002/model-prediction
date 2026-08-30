"""MLB Market Quote Backfill & Coverage Quality Audit (Phase F1).

Ingests all available historical MLB prospective and executable quotes into
MarketQuoteWarehouse (market_quotes.db) from:
1. data/odds/mlb/*/polymarket_snapshots.jsonl
2. data/market_odds_snapshots.jsonl

Produces:
1. Season-by-season coverage table (scheduled, matched, quotes >= 1, valid consensus,
   sharp quote, soft quote, two-sided pricing).
2. Clean, common-sample candidate set for M0/M2 experiments.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from model_prediction.data_sources.market_warehouse import MarketQuoteWarehouse
from model_prediction.domain import MarketQuote
from model_prediction.runtime_paths import RuntimePaths

MLB_NAME_TO_ABBR = {
    "arizona diamondbacks": "az",
    "atlanta braves": "atl",
    "baltimore orioles": "bal",
    "boston red sox": "bos",
    "chicago cubs": "chc",
    "chicago white sox": "cws",
    "cincinnati reds": "cin",
    "cleveland guardians": "cle",
    "colorado rockies": "col",
    "detroit tigers": "det",
    "houston astros": "hou",
    "kansas city royals": "kc",
    "los angeles angels": "laa",
    "los angeles dodgers": "lad",
    "miami marlins": "mia",
    "milwaukee brewers": "mil",
    "minnesota twins": "min",
    "new york mets": "nym",
    "new york yankees": "nyy",
    "athletics": "oak",
    "oakland athletics": "oak",
    "philadelphia phillies": "phi",
    "pittsburgh pirates": "pit",
    "san diego padres": "sd",
    "san francisco giants": "sf",
    "seattle mariners": "sea",
    "st. louis cardinals": "stl",
    "tampa bay rays": "tb",
    "texas rangers": "tex",
    "toronto blue jays": "tor",
    "washington nationals": "wsh",
}


def build_mlb_slug(away_name: str, home_name: str, date_str: str) -> str:
    away = MLB_NAME_TO_ABBR.get(away_name.strip().lower(), away_name[:3].lower())
    home = MLB_NAME_TO_ABBR.get(home_name.strip().lower(), home_name[:3].lower())
    return f"mlb-{away}-{home}-{date_str}"


def backfill_and_audit_mlb_quotes() -> dict[str, Any]:
    runtime_paths = RuntimePaths.resolve()
    data_dir = runtime_paths.repo_root / "data"
    warehouse = MarketQuoteWarehouse(db_path=runtime_paths.runtime_root / "market_quotes.db")

    ingested_quotes_count = 0
    quotes_batch: list[MarketQuote] = []

    # 1. Ingest all MLB odds from data/odds/mlb/*/polymarket_snapshots.jsonl
    odds_root = data_dir / "odds" / "mlb"
    snapshot_files = sorted(odds_root.glob("*/polymarket_snapshots.jsonl")) if odds_root.exists() else []

    for sfile in snapshot_files:
        try:
            with open(sfile, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    eid = row.get("event_slug") or row.get("event_id")
                    if not eid:
                        continue

                    long_side = row.get("long") or {}
                    long_bid = long_side.get("bid")
                    long_ask = long_side.get("ask")
                    long_mid = long_side.get("midpoint")
                    short_side = row.get("short") or {}
                    short_mid = short_side.get("midpoint")
                    no_vig_prob = None
                    if long_mid is not None and short_mid is not None and (long_mid + short_mid) > 0:
                        no_vig_prob = long_mid / (long_mid + short_mid)

                    q = MarketQuote(
                        event_id=str(eid),
                        sport="mlb",
                        market_type=str(row.get("market_type") or "unknown").lower(),
                        selection=str(long_side.get("description") or row.get("team") or "Over"),
                        source=str(row.get("provider") or "polymarket_us").lower(),
                        observed_at_utc=str(row.get("observed_at_utc") or row.get("transact_time_utc") or ""),
                        line=row.get("line"),
                        best_bid=long_bid,
                        best_ask=long_ask,
                        no_vig_probability=no_vig_prob,
                    )
                    quotes_batch.append(q)
                    if len(quotes_batch) >= 2000:
                        warehouse.record_quotes_batch(quotes_batch)
                        ingested_quotes_count += len(quotes_batch)
                        quotes_batch.clear()
        except (OSError, json.JSONDecodeError):
            continue

    # 2. Ingest from data/market_odds_snapshots.jsonl
    odds_archive = data_dir / "market_odds_snapshots.jsonl"
    if odds_archive.exists():
        try:
            with open(odds_archive, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    raw_resp = row.get("raw_response") or {}
                    ev = raw_resp.get("event") or {}
                    slug = ev.get("event_slug")
                    if not slug:
                        away = row.get("away_team", "")
                        home = row.get("home_team", "")
                        dt = (row.get("event_start_utc") or "")[:10]
                        slug = build_mlb_slug(away, home, dt)

                    obs = str(row.get("observed_at_utc") or "")
                    provider = str(row.get("provider") or "polymarket_us").lower()

                    for m in ev.get("markets", []):
                        mtype = str(m.get("market_type") or "").lower()
                        mline = m.get("line")
                        sides = m.get("sides", [])
                        long_s = next((s for s in sides if s.get("is_long")), None)
                        short_s = next((s for s in sides if not s.get("is_long")), None)

                        if long_s:
                            long_prob = long_s.get("price_probability")
                            short_prob = short_s.get("price_probability") if short_s else None
                            no_vig = None
                            if (
                                long_prob is not None
                                and short_prob is not None
                                and (long_prob + short_prob) > 0
                            ):
                                no_vig = long_prob / (long_prob + short_prob)

                            q = MarketQuote(
                                event_id=str(slug),
                                sport="mlb",
                                market_type=mtype,
                                selection=str(long_s.get("description") or long_s.get("selection") or "Over"),
                                source=provider,
                                observed_at_utc=obs,
                                line=mline,
                                best_bid=long_prob,
                                best_ask=long_prob,
                                no_vig_probability=no_vig,
                            )
                            quotes_batch.append(q)
                            if len(quotes_batch) >= 2000:
                                warehouse.record_quotes_batch(quotes_batch)
                                ingested_quotes_count += len(quotes_batch)
                                quotes_batch.clear()
        except (OSError, json.JSONDecodeError):
            pass

    if quotes_batch:
        warehouse.record_quotes_batch(quotes_batch)
        ingested_quotes_count += len(quotes_batch)
        quotes_batch.clear()

    # 3. Audit Market Quality by Season
    games_by_season: dict[str, list[dict[str, Any]]] = defaultdict(list)
    mlb_games_file = data_dir / "mlb_statsapi/game_snapshots.jsonl"
    if mlb_games_file.exists():
        with open(mlb_games_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    g = json.loads(line)
                    g_start = g.get("game_start_utc") or ""
                    season = g_start[:4] if len(g_start) >= 4 else "unknown"
                    games_by_season[season].append(g)
                except json.JSONDecodeError:
                    continue

    season_reports: dict[str, Any] = {}
    total_matched_games = 0

    for season, games in sorted(games_by_season.items()):
        n_scheduled = len(games)
        n_matched = 0
        n_with_quotes = 0
        n_valid_consensus = 0
        n_sharp = 0
        n_soft = 0
        n_two_sided = 0

        for g in games:
            g_start = g.get("game_start_utc")
            if not g_start:
                continue

            away_name = (g.get("away") or {}).get("team_name", "")
            home_name = (g.get("home") or {}).get("team_name", "")
            date_str = g_start[:10]
            slug = build_mlb_slug(away_name, home_name, date_str)

            quotes = warehouse.get_quotes_for_event(event_id=slug, market_type="total")
            if not quotes:
                quotes = warehouse.get_quotes_for_event(event_id=slug, market_type="spread")
            if not quotes:
                continue

            n_matched += 1
            n_with_quotes += 1
            sources = {q.source.lower() for q in quotes}
            if any(s in ("pinnacle", "circa", "polymarket_us", "kalshi") for s in sources):
                n_sharp += 1
            if any(s in ("draftkings", "fanduel", "betmgm") for s in sources):
                n_soft += 1
            if any(q.best_bid is not None and q.best_ask is not None for q in quotes):
                n_two_sided += 1
            if len(sources) >= 1:
                n_valid_consensus += 1

        total_matched_games += n_matched
        season_reports[season] = {
            "games_scheduled": n_scheduled,
            "games_matched": n_matched,
            "games_with_quotes": n_with_quotes,
            "games_with_valid_consensus": n_valid_consensus,
            "games_with_sharp_quote": n_sharp,
            "games_with_soft_quote": n_soft,
            "games_with_two_sided_pricing": n_two_sided,
        }

    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "total_quotes_ingested": ingested_quotes_count,
        "database_file": str(runtime_paths.runtime_root / "market_quotes.db"),
        "total_matched_games_across_all_seasons": total_matched_games,
        "seasons_coverage": season_reports,
    }
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill MLB Quotes and Audit Market Quality")
    parser.add_argument("--out", type=str, default="outputs/latest/mlb_backfill_quality_report.json")
    args = parser.parse_args()

    rep = backfill_and_audit_mlb_quotes()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(rep, indent=2))
    print(f"Backfill and quality audit complete. Saved to {out_path}")
    print(json.dumps(rep, indent=2))
