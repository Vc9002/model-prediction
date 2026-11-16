"""Research log of NFL spread/total prices taken days before kickoff.

At the closing line the v7 spread and total engines are a coin flip over 2,826 games
(docs/handoffs/HANDOFF.md sections 9-11). The one lead is early in the week: against the
opening line the spread engine's lean matched the later move 55-58% of the time, but that
replay could not prove the engine knew nothing the opener did not. This log is the clean
prospective version: the first price each game is seen at, with its own timestamp, to be
scored later against the close and the result.

It never builds a ledger and never blocks the daily run: every failure is a warning.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..data_sources.espn import ESPNClient

_logger = logging.getLogger(__name__)

EARLY_LOG_RELATIVE_PATH = Path("research") / "nfl_early" / "early_lines.jsonl"  # under the data root
EARLY_LOOKAHEAD_DAYS = 7  # one NFL week: Thursday through the following Monday
_KEPT_FIELDS = (
    "event_id",
    "home_team",
    "away_team",
    "event_start_utc",
    "selection",
    "line",
    "model_probability",
    "executable_ask",
    "decision_consensus_line",
    "model_version",
    "sportsbook",
    "market_slug",
)


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text("utf-8").splitlines() if line.strip()]


def log_nfl_early_lines(
    data_root: str | Path,
    *,
    now: datetime,
    dates: list[str] | None = None,
    once_per_day: bool = True,
) -> dict[str, Any]:
    """Append the first-seen price of each upcoming NFL spread and total to the research log."""
    from .forecast import _forecast_nfl_spread_slate, _forecast_nfl_total_slate

    path = Path(data_root) / EARLY_LOG_RELATIVE_PATH
    rows = _read_rows(path)
    today = now.astimezone(UTC).date()
    if once_per_day and any(str(row.get("logged_at_utc", ""))[:10] == today.isoformat() for row in rows):
        # The daily job fires several times a day; one look per day is enough for a first-seen log.
        return {"status": "skipped", "reason": "already_logged_today", "appended": 0}
    if dates is None:
        # Today's games are the game-day run's job; this log is only for games still days away.
        dates = [
            (today + timedelta(days=offset)).isoformat() for offset in range(1, EARLY_LOOKAHEAD_DAYS + 1)
        ]
    seen = {(str(row.get("event_id")), str(row.get("market"))) for row in rows}
    appended: list[dict[str, Any]] = []
    failures: list[str] = []
    for date in dates:
        for market, slate in (("spread", _forecast_nfl_spread_slate), ("total", _forecast_nfl_total_slate)):
            try:
                forecast = slate(
                    Path(data_root), date, ESPNClient(), observed_at=now, max_input_age_hours=12.0
                )
            except Exception as error:
                failures.append(f"{date}:{market}:{type(error).__name__}")
                _logger.warning("NFL early-line slate failed for %s %s", date, market, exc_info=True)
                continue
            for contract in forecast.get("priced_contracts") or []:
                key = (str(contract.get("event_id")), market)
                if key in seen:
                    continue
                seen.add(key)
                appended.append(
                    {
                        "market": market,
                        "logged_at_utc": now.isoformat(),
                        "slate_date": date,
                        **{field: contract.get(field) for field in _KEPT_FIELDS},
                    }
                )
    if appended:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for row in appended:
                handle.write(json.dumps(row, default=str) + "\n")
    return {"status": "ok", "appended": len(appended), "failures": failures, "path": str(path)}
