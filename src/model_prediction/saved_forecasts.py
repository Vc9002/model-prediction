"""Read existing pregame picks for started events; never recompute or mutate."""

import json
import sqlite3
from collections import Counter
from copy import copy
from pathlib import Path

from .domain import EASTERN, parse_utc, utc_now
from .runtime_paths import RuntimePaths


def preserve_started_args(args, saved, other_tier_saved=()):
    """A backfill flag must not regenerate events with existing pregame calls."""
    if (saved or other_tier_saved) and getattr(args, "force", False):
        args = copy(args)
        args.force = False
    return args


def _saved_pregame_picks(
    game_date, sports, *, ledger=None, flat=False, now=None, data_root=None, started_only=True
):
    now = now or utc_now()
    scope = {sport.casefold() for sport in sports}
    paths = RuntimePaths.resolve()
    db = paths.ledgers_db
    canonical_context = data_root is not None and Path(data_root).resolve() in {
        (paths.repo_root / "data").resolve(),
        paths.runtime_root.resolve(),
    }
    rows = []
    if db.is_file() and (canonical_context or ledger is None):
        # mode=ro prevents a diagnostic command from creating a second ledger.
        with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            for record in connection.execute(
                "SELECT * FROM ledger_records WHERE ledger_tier = ? AND status IN ('open', 'settled')",
                ("flat" if flat else "main",),
            ):
                row = json.loads(record["decision_payload_json"] or "{}")
                row.update(dict(record))
                row["league"] = record["sport"]
                rows.append(row)
    elif ledger is not None:
        rows = ledger.rows()
    preserved = []
    for row in rows:
        if str(row.get("league", "")).casefold() not in scope:
            continue
        if row.get("status") not in {"open", "settled"}:
            continue
        try:
            start = parse_utc(row["event_start_utc"])
            created = parse_utc(row["created_at_utc"])
        except (KeyError, TypeError, ValueError):
            continue
        if (
            (started_only and start > now)
            or created >= start
            or start.astimezone(EASTERN).date().isoformat() != game_date
        ):
            continue
        preserved.append(
            {
                **{key: value for key, value in row.items() if not key.endswith("_json")},
                "forecast_source": "existing_pregame_pick",
                "refreshed": False,
            }
        )
    return preserved


def existing_started_picks(game_date, sports, **kwargs):
    return _saved_pregame_picks(game_date, sports, **kwargs)


def stored_slate_coverage(game_date, sport, *, ledger=None, flat_ledger=None, data_root=None):
    """Keep complete Flat slate counts distinct from Main qualification counts."""
    coverage = {}
    for tier, source in (("main", ledger), ("flat", flat_ledger)):
        rows = _saved_pregame_picks(
            game_date,
            [sport],
            ledger=source,
            flat=tier == "flat",
            data_root=data_root,
            started_only=False,
        )
        slots = Counter((str(row.get("event_id")), row.get("market_type")) for row in rows)
        coverage[tier] = {
            "picks": len(rows),
            "games": len({row.get("event_id") for row in rows}),
            "markets": dict(Counter(row.get("market_type") for row in rows)),
            "duplicate_slots": [list(slot) for slot, count in slots.items() if count > 1],
        }
    return coverage
