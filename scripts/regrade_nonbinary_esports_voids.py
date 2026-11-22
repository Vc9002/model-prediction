"""Re-grade esports moneyline picks voided on Polymarket's non-binary terminal price.

Before 2026-09-14 (and for the CS2/Dota/LoL rows below, until BO3 confirmation was added),
`cli/settle.py` booked a 0U push whenever a terminal esports market showed non-binary prices.
Polymarket US reports pre-close trading prices on markets that are already decided, so that signal
is not evidence of a forfeit. Each affected row carries the stored signature
``void_reason`` containing ``esports market settled to a non-binary price``.

This script finds exactly those rows (identity-scoped by the stored void reason, never a time
window), looks up the real result in BO3 (bo3.gg, the results provider behind the markets), and
re-grades the rows it can confirm through ``PickLedger.settle(..., correction_reason=...)``.
A BO3 match counts only when both teams resolve unambiguously, exactly one finished series with a
winner starts within ``MATCH_WINDOW_HOURS`` of the pick's event start, and it has a winner. Anything
else stays a push (fail closed, never guessed). Non-esports leagues (the one darts row) are not
touched.

Dry-run is the default and writes nothing. ``--apply`` requires ``--expect-count`` equal to the
number of planned re-grades, holds the daily-writer lock, takes a SQLite backup, re-grades, then
verifies the event chain and that every re-graded row left the push signature.

Run with the runtime root set and the SQLite authority named explicitly::

    MODEL_PREDICTION_RUNTIME_ROOT=E:/model-prediction-runtime \
    MODEL_PREDICTION_LEDGER_AUTHORITY=sqlite PYTHONPATH=src \
    python scripts/regrade_nonbinary_esports_voids.py [--manifest out.json] \
        [--apply --expect-count N]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from model_prediction.daily_lock import acquire_lock
from model_prediction.domain import parse_utc
from model_prediction.esports import (
    LEAGUE_TO_ESPORTS_TITLE,
    Bo3EsportsClient,
    _load_manual_aliases,
    _resolve_bo3_team_id,
    _team_alias_index,
)
from model_prediction.main_ledgers import flat_ledger, main_ledger
from model_prediction.research_ledgers import existing_research_ledgers
from model_prediction.runtime_ledger_store import RuntimeLedgerStore
from model_prediction.runtime_paths import RuntimePaths

VOID_SIGNATURE = "esports market settled to a non-binary price"
# BO3 can list a series a little off the Polymarket start time; a rematch of the same two teams is
# days away, so this window separates them without being tight enough to drop real matches.
MATCH_WINDOW_HOURS = 12
TIERS = ("main", "flat", "research", "gated_research")
CORRECTION_REASON = (
    "2026-11-22 re-grade: the original void treated Polymarket's non-binary terminal price as a "
    "forfeit/postponement, which is not reliable for esports moneylines (a decided market can keep "
    "pre-close prices); BO3 now confirms a finished series with a decisive winner "
    "(scripts/regrade_nonbinary_esports_voids.py)."
)


@dataclass(frozen=True)
class RegradeAction:
    pick_id: str
    ledger_tier: str
    sport: str
    league: str
    event_id: str
    event_start_utc: str
    home_team: str
    away_team: str
    selection: str
    prior_result: str
    prior_pnl_units: str
    away_score: int
    home_score: int
    bo3_match_id: str
    bo3_start_utc: str


@dataclass(frozen=True)
class Skipped:
    pick_id: str
    ledger_tier: str
    league: str
    reason: str


def is_candidate(row: dict[str, Any]) -> bool:
    """The stored void signature, on a still-voided esports row."""
    return (
        str(row.get("status") or "").casefold() == "settled"
        and str(row.get("result") or "").casefold() == "push"
        and VOID_SIGNATURE in str(row.get("void_reason") or "")
        and str(row.get("league") or "").upper() in LEAGUE_TO_ESPORTS_TITLE
    )


def candidate_rows(store: RuntimeLedgerStore) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tier in TIERS:
        rows.extend(r for r in store.pick_rows(tier=tier) if is_candidate(r))
    return sorted(rows, key=lambda r: (str(r["ledger_tier"]), str(r["pick_id"])))


class _Bo3Cache:
    """One teams/finished_matches fetch per title, widened to the earliest candidate."""

    def __init__(self, client: Any, data_root: Path, earliest: dict[str, datetime]) -> None:
        self._client = client
        self._aliases = _load_manual_aliases(data_root)
        self._earliest = earliest
        self._cache: dict[str, tuple[dict, dict, dict, list[dict]]] = {}

    def title_data(self, title: str) -> tuple[dict, dict, dict, list[dict]]:
        if title not in self._cache:
            teams, _ = self._client.teams(title)
            start = (self._earliest[title] - timedelta(days=2)).date()
            matches, _ = self._client.finished_matches(title, start, datetime.now(UTC).date())
            self._cache[title] = (
                teams,
                _team_alias_index(teams),
                self._aliases.get(title, {}),
                matches,
            )
        return self._cache[title]


def find_series(cache: _Bo3Cache, row: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """Return (the single confirming BO3 series, "") or (None, why it stays void)."""
    league = str(row["league"]).upper()
    title = LEAGUE_TO_ESPORTS_TITLE[league]
    teams, index, aliases, matches = cache.title_data(title)
    home_id = _resolve_bo3_team_id(str(row["home_team"]), teams, index, aliases)
    away_id = _resolve_bo3_team_id(str(row["away_team"]), teams, index, aliases)
    if not home_id or not away_id:
        return None, "team name does not resolve to a BO3 team"
    if home_id == away_id:
        return None, "both teams resolve to the same BO3 team"
    event_start = parse_utc(str(row["event_start_utc"]))
    window = timedelta(hours=MATCH_WINDOW_HOURS)
    hits = [
        m
        for m in matches
        if {m["team1_id"], m["team2_id"]} == {home_id, away_id}
        and m["winner_id"] in {home_id, away_id}
        and abs(parse_utc(str(m["start_utc"])) - event_start) <= window
    ]
    if not hits:
        return None, "no finished BO3 series with a winner near the event start"
    if len(hits) > 1:
        return None, f"{len(hits)} BO3 series match within {MATCH_WINDOW_HOURS}h"
    return hits[0], ""


def plan(
    rows: list[dict[str, Any]], client: Any, data_root: Path
) -> tuple[list[RegradeAction], list[Skipped]]:
    earliest: dict[str, datetime] = {}
    for row in rows:
        title = LEAGUE_TO_ESPORTS_TITLE[str(row["league"]).upper()]
        start = parse_utc(str(row["event_start_utc"]))
        earliest[title] = min(earliest.get(title, start), start)
    cache = _Bo3Cache(client, data_root, earliest)
    actions: list[RegradeAction] = []
    skipped: list[Skipped] = []
    for row in rows:
        common = (str(row["pick_id"]), str(row["ledger_tier"]), str(row["league"]))
        if str(row.get("market_type") or "").casefold() != "moneyline":
            skipped.append(Skipped(*common, "not a moneyline pick"))
            continue
        if str(row.get("selection") or "").casefold() not in {"home", "away"}:
            skipped.append(Skipped(*common, "selection is not home/away"))
            continue
        match, why = find_series(cache, row)
        if match is None:
            skipped.append(Skipped(*common, why))
            continue
        home_id = _resolve_bo3_team_id(str(row["home_team"]), *_title_args(cache, row))
        home_win = match["winner_id"] == home_id
        actions.append(
            RegradeAction(
                pick_id=str(row["pick_id"]),
                ledger_tier=str(row["ledger_tier"]),
                sport=str(row.get("sport") or row["league"]).casefold(),
                league=str(row["league"]).upper(),
                event_id=str(row["event_id"]),
                event_start_utc=str(row["event_start_utc"]),
                home_team=str(row["home_team"]),
                away_team=str(row["away_team"]),
                selection=str(row["selection"]),
                prior_result=str(row["result"]),
                prior_pnl_units=str(row.get("pnl_units") or ""),
                away_score=0 if home_win else 1,
                home_score=1 if home_win else 0,
                bo3_match_id=str(match["match_id"]),
                bo3_start_utc=str(match["start_utc"]),
            )
        )
    return actions, skipped


def _title_args(cache: _Bo3Cache, row: dict[str, Any]) -> tuple[dict, dict, dict]:
    teams, index, aliases, _ = cache.title_data(LEAGUE_TO_ESPORTS_TITLE[str(row["league"]).upper()])
    return teams, index, aliases


def _backup_database(paths: RuntimePaths) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = paths.runtime_root / "backups" / f"ledgers.pre-esports-regrade.{stamp}.db"
    destination.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(paths.ledgers_db)
    backup = sqlite3.connect(destination)
    try:
        source.backup(backup)
        status = backup.execute("PRAGMA integrity_check").fetchone()
        if status != ("ok",):
            raise RuntimeError(f"backup integrity check failed: {status}")
    finally:
        backup.close()
        source.close()
    return destination


def _ledger_for(action: RegradeAction, paths: RuntimePaths, researches: dict[tuple[str, str], Any]):
    data_root = paths.repo_root / "data"
    if action.ledger_tier == "main":
        return main_ledger(data_root, action.sport)
    if action.ledger_tier == "flat":
        return flat_ledger(data_root, action.sport)
    return researches[(action.ledger_tier, action.sport)]


def apply_regrades(actions: list[RegradeAction], paths: RuntimePaths) -> dict[str, Any]:
    data_root = paths.repo_root / "data"
    researches: dict[tuple[str, str], Any] = {}
    for gated in (False, True):
        for ledger in existing_research_ledgers(data_root, gated=gated):
            researches[("gated_research" if gated else "research", str(ledger.sport).casefold())] = ledger
    results: dict[str, int] = {}
    for action in actions:
        ledger = _ledger_for(action, paths, researches)
        out = ledger.settle(
            action.pick_id,
            action.away_score,
            action.home_score,
            correction_reason=CORRECTION_REASON,
        )
        results[str(out["result"])] = results.get(str(out["result"]), 0) + 1
    return {"regraded": len(actions), "results": dict(sorted(results.items()))}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--apply", action="store_true", help="apply the preflighted re-grade")
    root.add_argument("--expect-count", type=int, help="required with --apply; planned re-grade count")
    root.add_argument("--manifest", type=Path, help="optional path for the enumerated plan")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    paths = RuntimePaths.resolve(require_external_runtime=True)
    # Dry-run reads only, so it must not hold the daily-writer lock (a held lock makes the
    # scheduled daily run skip); only --apply takes it, before it plans the rows it will write.
    lock = acquire_lock(paths.lock_root / "daily.lock") if args.apply else None
    if args.apply and lock is None:
        raise RuntimeError("daily writer is active; refusing esports re-grade")
    try:
        store = RuntimeLedgerStore(paths)
        try:
            rows = candidate_rows(store)
        finally:
            store.close()
        actions, skipped = plan(rows, Bo3EsportsClient(), paths.repo_root / "data")
        manifest = {
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "correction_reason": CORRECTION_REASON,
            "candidate_count": len(rows),
            "regrade_count": len(actions),
            "left_void_count": len(skipped),
            "actions": [asdict(a) for a in actions],
            "left_void": [asdict(s) for s in skipped],
        }
        if args.manifest:
            args.manifest.parent.mkdir(parents=True, exist_ok=True)
            args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {k: manifest[k] for k in ("candidate_count", "regrade_count", "left_void_count")},
                indent=2,
            )
        )
        if not args.apply:
            return 0
        if args.expect_count is None:
            raise ValueError("--apply requires --expect-count")
        if len(actions) != args.expect_count:
            raise ValueError(
                f"planned re-grade count changed: expected {args.expect_count}, found {len(actions)}; refusing"
            )
        backup = _backup_database(paths)
        applied = apply_regrades(actions, paths)
        verifier = RuntimeLedgerStore(paths)
        try:
            chain_ok, problems = verifier.verify_integrity()
            still_push = {(r["ledger_tier"], r["pick_id"]) for r in candidate_rows(verifier)}
        finally:
            verifier.close()
        regraded_ids = {(a.ledger_tier, a.pick_id) for a in actions}
        if not chain_ok or still_push & regraded_ids:
            raise RuntimeError(
                f"post-regrade verification failed: chain_ok={chain_ok} "
                f"unchanged={len(still_push & regraded_ids)} problems={problems[:1]}"
            )
        print(
            json.dumps(
                {
                    "status": "regraded",
                    "backup": str(backup),
                    "event_chain_intact": chain_ok,
                    "left_void": len(still_push),
                    **applied,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    finally:
        if lock is not None:
            lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
