"""Ingest rich historical and ongoing ATP match data from TennisMyLife.

Expands ATP coverage from 11,490 matches to over 60,000+ matches across:
  - ATP Tour Main Draw (2021-2026)
  - ATP Challenger Tour (2021-2026)
  - ATP Qualifying Tournaments (2021-2026)
  - Ongoing ATP Tour and Challenger Tournaments

Deduplicates strictly against existing ESPN matches in `data/processed/tennis/games.jsonl`.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx
import polars as pl

from model_prediction.rebuild.tennis.normalize import _classify_tennismylife_result

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
GAMES_PATH = DATA_DIR / "processed/tennis/games.jsonl"
NORMALIZED_ROOT = DATA_DIR / "rebuild/normalized"


def norm_name(name: str) -> str:
    """Normalize player name for exact deduplication matching."""
    n = unicodedata.normalize("NFKD", str(name)).encode("ASCII", "ignore").decode("utf-8")
    return " ".join(re.findall(r"[a-z0-9]+", n.casefold()))


def run_ingest(
    min_year: int = 2021,
    max_year: int = 2026,
    backup: bool = True,
) -> dict[str, Any]:
    print("=" * 70)
    print("ATP DATA INGESTION & COVERAGE EXPANSION")
    print(f"Ingesting ATP Tour, Challengers, and Qualifiers ({min_year}-{max_year})")
    print("=" * 70)

    # 1. Load existing games and index deduplication keys
    existing_keys: set[tuple[str, str, str]] = set()
    existing_ids: set[str] = set()
    existing_rows: list[dict[str, Any]] = []

    if GAMES_PATH.exists():
        if backup:
            bak_path = GAMES_PATH.with_suffix(".jsonl.bak")
            shutil.copy2(GAMES_PATH, bak_path)
            print(f"Backed up existing games to {bak_path}")

        with open(GAMES_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                row = json.loads(line)
                existing_rows.append(row)
                existing_ids.add(str(row.get("event_id")))
                w = norm_name(row.get("winner", ""))
                l = norm_name(row.get("loser", ""))
                d = str(row.get("event_start_utc") or row.get("match_date") or "")[:10]
                if w and l and d:
                    existing_keys.add((w, l, d))

    atp_before = sum(1 for r in existing_rows if r.get("league") == "ATP")
    wta_before = sum(1 for r in existing_rows if r.get("league") == "WTA")
    print(
        f"Current match counts: ATP = {atp_before:,} | WTA = {wta_before:,} | Total = {len(existing_rows):,}\n"
    )

    # 2. Build files schedule
    client = httpx.Client(timeout=30.0)
    files_to_fetch: list[tuple[str, str]] = []
    for y in range(min_year, max_year + 1):
        files_to_fetch.append((f"{y}.csv", "main"))
        files_to_fetch.append((f"{y}_challenger.csv", "challenger"))
        files_to_fetch.append((f"atp_quali/{y}_atp_quali.csv", "qualifying"))
    files_to_fetch.append(("ongoing_tourneys.csv", "ongoing_main"))
    files_to_fetch.append(("challenger_ongoing_tourneys.csv", "ongoing_challenger"))

    new_matches: list[dict[str, Any]] = []
    skipped_dups = 0
    skipped_incomplete = 0

    # 3. Fetch and process each file
    for rel_path, kind in files_to_fetch:
        url = f"https://stats.tennismylife.org/data/{rel_path}"
        try:
            resp = client.get(url)
        except (httpx.HTTPError, OSError) as exc:
            print(f"Failed to fetch {rel_path}: {exc}")
            continue

        if resp.status_code != 200:
            print(f"Skipping {rel_path}: HTTP {resp.status_code}")
            continue

        try:
            df = pl.read_csv(io.BytesIO(resp.content), infer_schema_length=0)
        except (pl.exceptions.PolarsError, ValueError, OSError) as exc:
            print(f"Failed to parse CSV {rel_path}: {exc}")
            continue

        file_new = 0
        file_dups = 0

        for row in df.iter_rows(named=True):
            w = str(row.get("winner_name") or "").strip()
            l = str(row.get("loser_name") or "").strip()
            score = str(row.get("score") or "").strip()
            d_raw = str(row.get("tourney_date") or "").strip()
            surface = str(row.get("surface") or "Hard").strip()
            tourney = str(row.get("tourney_name") or "ATP Event").strip()
            t_id = str(row.get("tourney_id") or "tml")
            m_num = str(row.get("match_num") or len(new_matches))

            if surface not in {"Hard", "Clay", "Grass", "Carpet"}:
                surface = "Hard"

            if len(d_raw) == 8 and d_raw.isdigit():
                date_iso = f"{d_raw[:4]}-{d_raw[4:6]}-{d_raw[6:8]}"
            else:
                continue

            w_norm = norm_name(w)
            l_norm = norm_name(l)
            if not w_norm or not l_norm:
                continue

            _result_type, is_completed = _classify_tennismylife_result(score)
            if not is_completed:
                skipped_incomplete += 1
                continue

            key = (w_norm, l_norm, date_iso)
            event_id = f"tml:atp:{t_id}:{m_num}"
            if key in existing_keys or event_id in existing_ids:
                skipped_dups += 1
                file_dups += 1
                continue

            existing_keys.add(key)
            existing_ids.add(event_id)

            match_record = {
                "event_id": event_id,
                "event_start_utc": f"{date_iso}T12:00:00Z",
                "match_date": f"{date_iso}T12:00:00Z",
                "tournament": tourney,
                "round": str(row.get("round") or "unknown"),
                "surface": surface,
                "league": "ATP",
                "winner": w,
                "loser": l,
                "winner_id": str(row.get("winner_id") or ""),
                "loser_id": str(row.get("loser_id") or ""),
                "status": "completed",
                "score": score,
                "raw_source": f"tennismylife:{rel_path}",
                "parser_version": "tennismylife-csv-v1",
            }
            new_matches.append(match_record)
            file_new += 1

        print(f"  {rel_path:35s}: +{file_new:5d} new matches | {file_dups:5d} dups")

    print(f"\nTotal new unique ATP matches collected: {len(new_matches):,}")
    print(f"Total duplicates filtered: {skipped_dups:,}")
    print(f"Total incomplete/walkovers skipped: {skipped_incomplete:,}")

    # 4. Merge and sort all games chronologically
    all_games = existing_rows + new_matches
    all_games.sort(key=lambda g: str(g.get("event_start_utc") or g.get("match_date") or ""))

    # Write merged games.jsonl
    GAMES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(GAMES_PATH, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(game, sort_keys=True, separators=(",", ":")) + "\n" for game in all_games)

    print(f"\nSuccessfully wrote {len(all_games):,} matches to {GAMES_PATH}")

    # 5. Calculate player sample size distribution
    atp_counts: dict[str, int] = defaultdict(int)
    for g in all_games:
        if g.get("league") == "ATP":
            atp_counts[g.get("winner")] += 1
            atp_counts[g.get("loser")] += 1

    atp_after = sum(1 for r in all_games if r.get("league") == "ATP")
    ge_10 = sum(1 for c in atp_counts.values() if c >= 10)
    ge_20 = sum(1 for c in atp_counts.values() if c >= 20)
    ge_50 = sum(1 for c in atp_counts.values() if c >= 50)

    print("\n" + "=" * 70)
    print("ATP COVERAGE & SAMPLE SIZE EXPANSION SUMMARY")
    print("=" * 70)
    print(
        f"ATP Matches:           {atp_before:,} -> {atp_after:,} (+{atp_after - atp_before:,} / +{(atp_after / atp_before - 1) * 100:.1f}%)"
    )
    print(f"Total Tennis Matches:  {len(existing_rows):,} -> {len(all_games):,} (+{len(new_matches):,})")
    print(f"ATP Players tracked:   {len(atp_counts):,}")
    print(f"Players with >= 10 matches (Model Input Valid): {ge_10:,} (vs 330 previously)")
    print(f"Players with >= 20 matches (Mature Elo):        {ge_20:,} (vs 258 previously)")
    print(f"Players with >= 50 matches (Deep Form):         {ge_50:,} (vs 165 previously)")

    report = {
        "atp_matches_before": atp_before,
        "atp_matches_after": atp_after,
        "new_atp_matches": len(new_matches),
        "total_matches": len(all_games),
        "atp_players_tracked": len(atp_counts),
        "atp_players_ge_10_matches": ge_10,
        "atp_players_ge_20_matches": ge_20,
        "atp_players_ge_50_matches": ge_50,
    }
    report_file = DATA_DIR / "processed/tennis/atp_ingest_summary.json"
    report_file.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Summary saved to {report_file}")
    return report


if __name__ == "__main__":
    run_ingest()
