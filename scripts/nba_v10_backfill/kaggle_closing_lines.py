"""NBA v10 research closing lines 2007-08 .. 2025-26 from Kaggle (quick-261120-nbf).

Sources (free, open licences; the same two datasets behind
``data/odds/nba/historical_closing_lines/``):
    cviaxmiwnptr/nba-betting-data-october-2007-to-june-2024  (CC0-1.0; the file now runs to 2026-06)
    minmaxer/nba-closing-lines-ats-2025-26                    (CC BY 4.0; open + close, 2025-26)

Commands:
    download    Kaggle HTTP API (token from ~/.kaggle/access_token) -> ``closing_lines/raw/*.zip``
    normalize   -> ``closing_lines/nba_closing_lines_2007_2026.jsonl`` in the schema of
                ``nba_closing_lines_2024_2026.jsonl`` plus research fields, and
                ``closing_lines/nba_open_close_2025_26.jsonl`` (minmaxer open/close lines).

Event ids come from the research scoreboard index (``games/scoreboard_index.jsonl``): a Kaggle
row matches the ESPN game on the same ET date (+/-1 day) with identical final scores, else on
team ids via a code->ESPN-team-id map learned from the score matches. Moneylines are American in
the source; the ``moneyline_*`` fields stay blank (as in every existing row) and implied
probabilities are added instead. These are closing-line benchmarks with no capture time: never
decision inputs.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import logging
import zipfile
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

from model_prediction.data_sources.nba_backfill_common import (
    american_to_implied,
    devig_pair,
    resolve_out_root,
    utc_now_iso,
    write_json_atomic,
)

_logger = logging.getLogger("nba_v10.kaggle_lines")
DATASETS = {
    "cviaxmiwnptr/nba-betting-data-october-2007-to-june-2024": "CC0-1.0",
    "minmaxer/nba-closing-lines-ats-2025-26": "CC BY 4.0",
}
API = "https://www.kaggle.com/api/v1/datasets/download/{ref}"


def download(root: Path) -> None:
    token = (Path.home() / ".kaggle" / "access_token").read_text(encoding="utf-8").strip()
    raw = root / "closing_lines" / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    meta = {}
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        for ref, licence in DATASETS.items():
            resp = client.get(API.format(ref=ref), headers={"Authorization": f"Bearer {token}"})
            resp.raise_for_status()
            path = raw / f"{ref.split('/')[1]}.zip"
            path.write_bytes(resp.content)
            meta[ref] = {
                "licence": licence,
                "url": f"https://www.kaggle.com/datasets/{ref}",
                "zip": path.name,
                "bytes": len(resp.content),
                "sha256": hashlib.sha256(resp.content).hexdigest(),
                "downloaded_at_utc": utc_now_iso(),
            }
            _logger.info("downloaded %s (%d bytes)", ref, len(resp.content))
    write_json_atomic(raw / "download_meta.json", meta)


def _csv_rows(zip_path: Path) -> list[dict[str, str]]:
    with zipfile.ZipFile(zip_path) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".csv"))
        return list(csv.DictReader(io.StringIO(zf.read(name).decode("utf-8"))))


def _int(v: Any) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_index(root: Path) -> dict[str, list[dict[str, Any]]]:
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[str] = set()
    with (root / "games" / "scoreboard_index.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if row["event_id"] in seen:
                continue
            seen.add(row["event_id"])
            by_date[row["scoreboard_date"]].append(row)
    return by_date


def _near(by_date: dict[str, list[dict[str, Any]]], day: str) -> list[dict[str, Any]]:
    d = date.fromisoformat(day)
    out: list[dict[str, Any]] = []
    for off in (0, -1, 1):
        out.extend(by_date.get((d + timedelta(days=off)).isoformat(), []))
    return out


def normalize(root: Path) -> None:
    raw = root / "closing_lines" / "raw"
    by_date = load_index(root)
    rows = _csv_rows(raw / "nba-betting-data-october-2007-to-june-2024.zip")
    # pass 1: score matches, learning code -> ESPN team id
    code_votes: dict[str, Counter[str]] = defaultdict(Counter)
    matched: dict[int, tuple[dict[str, Any], str]] = {}
    for i, r in enumerate(rows):
        sa, sh = _int(r["score_away"]), _int(r["score_home"])
        if sa is None or sh is None:
            continue
        cands = [g for g in _near(by_date, r["date"]) if g["away_score"] == sa and g["home_score"] == sh]
        exact = [g for g in cands if g["scoreboard_date"] == r["date"]]
        pick = exact if len(exact) == 1 else cands
        if len(pick) == 1:
            g = pick[0]
            matched[i] = (g, "date_scores")
            code_votes[r["away"]][g["away_team_id"]] += 1
            code_votes[r["home"]][g["home_team_id"]] += 1
    code_map = {code: votes.most_common(1)[0][0] for code, votes in code_votes.items()}
    # pass 2: unmatched rows by team ids
    for i, r in enumerate(rows):
        if i in matched:
            continue
        aid, hid = code_map.get(r["away"]), code_map.get(r["home"])
        cands = [
            g for g in _near(by_date, r["date"]) if g["away_team_id"] == aid and g["home_team_id"] == hid
        ]
        if len(cands) == 1:
            matched[i] = (cands[0], "date_team_ids")
    out_path = root / "closing_lines" / "nba_closing_lines_2007_2026.jsonl"
    stats: Counter[str] = Counter()
    per_season: dict[str, Counter[str]] = defaultdict(Counter)
    with out_path.with_suffix(".tmp").open("w", encoding="utf-8") as fh:
        for i, r in enumerate(rows):
            g, method = matched.get(i, (None, "unmatched"))
            spread = _float(r["spread"])
            fav = r["whos_favored"]
            home_spread = None
            if spread is not None and fav in ("home", "away"):
                home_spread = -abs(spread) if fav == "home" else abs(spread)
            p_away = american_to_implied(r.get("moneyline_away"))
            p_home = american_to_implied(r.get("moneyline_home"))
            _, devig_home = devig_pair(p_away, p_home)
            rec = {
                "event_id": g["event_id"] if g else "",
                # The existing file's date is the UTC date of the ESPN start; the source's ET date is kept too.
                "date": (g.get("event_start_utc") or "")[:10] or r["date"] if g else r["date"],
                "date_et": r["date"],
                "away_team": g["away_team"] if g else "",
                "home_team": g["home_team"] if g else "",
                "spread": r["spread"],
                "total": r["total"],
                "moneyline_away": "",
                "moneyline_home": "",
                "whos_favored": fav,
                # research extras
                "season": _int(r["season"]),
                "regular": r["regular"] == "True",
                "playoffs": r["playoffs"] == "True",
                "source_away_code": r["away"],
                "source_home_code": r["home"],
                "away_score": _int(r["score_away"]),
                "home_score": _int(r["score_home"]),
                "home_spread": home_spread,
                "total_line": _float(r["total"]),
                "ml_implied_prob_away": p_away,
                "ml_implied_prob_home": p_home,
                "ml_devig_prob_home": devig_home,
                "h2_spread": _float(r.get("h2_spread")),
                "h2_total": _float(r.get("h2_total")),
                "match_method": method,
                "source": "kaggle:cviaxmiwnptr/nba-betting-data-october-2007-to-june-2024",
            }
            fh.write(json.dumps(rec, sort_keys=False) + "\n")
            stats[method] += 1
            per_season[r["season"]][method] += 1
            per_season[r["season"]]["rows"] += 1
            if spread is None:
                per_season[r["season"]]["no_spread"] += 1
            if _float(r["total"]) is None:
                per_season[r["season"]]["no_total"] += 1
            if p_home is None:
                per_season[r["season"]]["no_moneyline"] += 1
    out_path.with_suffix(".tmp").replace(out_path)
    _logger.info("closing lines: rows=%d %s", len(rows), dict(stats))
    mm = _normalize_minmaxer(root, by_date)
    write_json_atomic(
        root / "closing_lines" / "normalize_stats.json",
        {
            "rows": len(rows),
            "match_methods": dict(stats),
            "per_season": {k: dict(v) for k, v in sorted(per_season.items())},
            "code_map": code_map,
            "minmaxer": mm,
            "built_at_utc": utc_now_iso(),
        },
    )


def _normalize_minmaxer(root: Path, by_date: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    rows = _csv_rows(root / "closing_lines" / "raw" / "nba-closing-lines-ats-2025-26.zip")
    out_path = root / "closing_lines" / "nba_open_close_2025_26.jsonl"
    counts: Counter[str] = Counter()
    with out_path.open("w", encoding="utf-8") as fh:
        for r in rows:
            sa, sh = _int(r["away_score"]), _int(r["home_score"])
            cands = [
                g
                for g in _near(by_date, r["date_et"])
                if g["home_team"] == r["home_team"] and g["away_team"] == r["away_team"]
            ] or [g for g in _near(by_date, r["date_et"]) if g["away_score"] == sa and g["home_score"] == sh]
            event_id = cands[0]["event_id"] if len(cands) == 1 else ""
            counts["matched" if event_id else "unmatched"] += 1
            fh.write(
                json.dumps(
                    {
                        "event_id": event_id,
                        "date": r["date_et"],
                        "start_time_utc": r["start_time_utc"],
                        "season_type": r["season_type"],
                        "away_team": r["away_team"],
                        "home_team": r["home_team"],
                        "bookmakers": _int(r["bookmakers"]),
                        "open_home_spread": _float(r["open_spread_home"]),
                        "close_home_spread": _float(r["close_spread_home"]),
                        "open_total": _float(r["open_total"]),
                        "close_total": _float(r["close_total"]),
                        "open_home_win_prob": _float(r["open_home_win_prob"]),
                        "close_home_win_prob": _float(r["close_home_win_prob"]),
                        "close_home_cover_prob": _float(r["close_home_cover_prob"]),
                        "close_over_prob": _float(r["close_over_prob"]),
                        "source": "kaggle:minmaxer/nba-closing-lines-ats-2025-26",
                    }
                )
                + "\n"
            )
    return dict(counts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("download", "normalize"))
    parser.add_argument("--out-root", default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    root = resolve_out_root(args.out_root)
    if args.command == "download":
        download(root)
    else:
        normalize(root)


if __name__ == "__main__":
    main()
