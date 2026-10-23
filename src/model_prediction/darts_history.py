"""Observed PDC darts match history for the darts Elo ratings.

Source: Wikipedia tournament brackets (see ``data_sources.wikipedia_darts``).
Cached as JSONL at ``processed/darts/matches.jsonl`` and deduplicated by
``match_id``. The first refresh backfills ``BOOTSTRAP_YEARS`` seasons; later
refreshes re-read only the current season so corrected or newly completed
brackets self-heal.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from .data_sources.wikipedia_darts import WikipediaDartsClient
from .models.darts import DartsMatchRecord

BOOTSTRAP_YEARS = 3
_FIELDS = ("match_id", "match_date", "league", "player_a", "player_b", "legs_a", "legs_b", "winner")


def history_path(data_root: str | Path) -> Path:
    return Path(data_root) / "processed" / "darts" / "matches.jsonl"


def _name_key(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return " ".join(re.findall(r"[a-z0-9]+", ascii_name.casefold()))


def load_darts_history(data_root: str | Path) -> list[DartsMatchRecord]:
    path = history_path(data_root)
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    # Wikipedia spells the same player differently across pages ("van"/"Van",
    # diacritics); keep one rating per player under the most common spelling.
    spellings: dict[str, Counter[str]] = {}
    for row in rows:
        for field in ("player_a", "player_b"):
            spellings.setdefault(_name_key(row[field]), Counter())[row[field]] += 1
    canonical = {key: counts.most_common(1)[0][0] for key, counts in spellings.items()}
    records: list[DartsMatchRecord] = []
    for row in rows:
        fields = {field: row[field] for field in _FIELDS}
        fields["player_a"] = canonical[_name_key(row["player_a"])]
        fields["player_b"] = canonical[_name_key(row["player_b"])]
        fields["winner"] = canonical[_name_key(row["winner"])]
        records.append(DartsMatchRecord(**fields))
    return records


def refresh_darts_history(
    data_root: str | Path,
    game_date: str,
    client: WikipediaDartsClient | None = None,
) -> dict[str, Any]:
    path = history_path(data_root)
    existing: dict[str, dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                existing[str(row["match_id"])] = row

    year = date.fromisoformat(game_date).year
    years = [year] if existing else list(range(year - BOOTSTRAP_YEARS + 1, year + 1))
    source = client or WikipediaDartsClient()
    errors: list[dict[str, Any]] = []
    added = 0
    for season in years:
        try:
            matches = source.year_matches(season)
        except (httpx.HTTPError, OSError, ValueError) as exc:
            errors.append({"year": season, "reason": str(exc)})
            continue
        for match in matches:
            if match["match_id"] not in existing:
                added += 1
            existing[match["match_id"]] = match

    if added:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".jsonl.tmp")
        ordered = sorted(existing.values(), key=lambda row: (row["match_date"], row["match_id"]))
        temporary.write_text(
            "\n".join(json.dumps(row, sort_keys=True) for row in ordered) + "\n", encoding="utf-8"
        )
        temporary.replace(path)
    return {
        "status": "error" if errors and not existing else "ok",
        "matches": len(existing),
        "added": added,
        "years": years,
        "errors": errors,
    }
