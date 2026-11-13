"""Point-in-time capture of ESPN's DraftKings consensus odds for upcoming NFL games.

Why this exists (2026-11-12): every NFL ledger row had an empty closing line and a
zero CLV because Polymarket quotes were only snapshotted on game day, and nothing
stored the sportsbook consensus at all. ESPN's scoreboard carries, for each game,
the opener and the current line with two-sided prices for spread, total and
moneyline. Capturing that every run for the week ahead gives three things the
engines cannot be judged without: a decision-time consensus price per row, a
closing consensus at kickoff for CLV, and the opening line the published
literature says is the only line a public model has ever beaten.

Storage: data/odds/nfl/<game_date_et>/espn_odds.jsonl, one record per event per
capture, append-only, each record hashed. Readers must filter on observed_at_utc.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..domain import EASTERN, parse_utc
from ..pricing import implied_probability

_logger = logging.getLogger(__name__)

ESPN_ODDS_FILENAME = "espn_odds.jsonl"


def _odds_int(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.upper() in ("", "OFF", "N/A", "NONE", "EVEN"):
        return 100 if text.upper() == "EVEN" else None
    try:
        number = int(text)
    except (TypeError, ValueError):
        return None
    return number if (number <= -100 or number >= 100) else None


def _line_float(value: Any) -> float | None:
    """ESPN writes total lines as 'o40.5' / 'u40.5' and spreads as '-2.5' / '+2.5'."""
    if value is None:
        return None
    text = str(value).strip().lower().lstrip("ou")
    try:
        return float(text)
    except ValueError:
        return None


def devig_two_sided(odds_a: int | None, odds_b: int | None) -> tuple[float | None, float | None]:
    """Return (p_a, p_b) with the overround removed proportionally; None when a side is missing."""
    if odds_a is None or odds_b is None:
        return None, None
    try:
        raw_a = implied_probability(odds_a)
        raw_b = implied_probability(odds_b)
    except ValueError:
        return None, None
    total = raw_a + raw_b
    if total <= 0:
        return None, None
    return round(raw_a / total, 6), round(raw_b / total, 6)


def parse_espn_consensus(event: dict[str, Any]) -> dict[str, Any] | None:
    """Flatten one ESPN scoreboard event's first odds provider into a consensus record.

    Returns None when the event carries no odds block. Lines are home-anchored for the
    spread (negative = home favoured, the same convention the NFL builders use) and
    plain numbers for the total.
    """
    competitions = event.get("competitions") or [{}]
    odds_list = (competitions[0] or {}).get("odds") or []
    if not odds_list:
        return None
    odds = odds_list[0] or {}
    spread = odds.get("pointSpread") or {}
    total = odds.get("total") or {}
    moneyline = odds.get("moneyline") or {}

    def _leg(block: dict[str, Any], side: str, phase: str, key: str) -> Any:
        return ((block.get(side) or {}).get(phase) or {}).get(key)

    home_team = away_team = ""
    for competitor in (competitions[0] or {}).get("competitors") or []:
        name = str((competitor.get("team") or {}).get("displayName") or "")
        if competitor.get("homeAway") == "home":
            home_team = name
        elif competitor.get("homeAway") == "away":
            away_team = name

    spread_home_line = _line_float(_leg(spread, "home", "close", "line"))
    if spread_home_line is None and odds.get("spread") is not None:
        spread_home_line = _line_float(odds.get("spread"))
    total_line = _line_float(_leg(total, "over", "close", "line"))
    if total_line is None and odds.get("overUnder") is not None:
        total_line = _line_float(odds.get("overUnder"))

    return {
        "event_id": str(event.get("id") or ""),
        "event_start_utc": str(event.get("date") or ""),
        "home_team": home_team,
        "away_team": away_team,
        "provider": str((odds.get("provider") or {}).get("name") or ""),
        "spread_home_line": spread_home_line,
        "spread_home_line_open": _line_float(_leg(spread, "home", "open", "line")),
        "spread_home_odds": _odds_int(_leg(spread, "home", "close", "odds")),
        "spread_away_odds": _odds_int(_leg(spread, "away", "close", "odds")),
        "spread_home_odds_open": _odds_int(_leg(spread, "home", "open", "odds")),
        "spread_away_odds_open": _odds_int(_leg(spread, "away", "open", "odds")),
        "total_line": total_line,
        "total_line_open": _line_float(_leg(total, "over", "open", "line")),
        "total_over_odds": _odds_int(_leg(total, "over", "close", "odds")),
        "total_under_odds": _odds_int(_leg(total, "under", "close", "odds")),
        "total_over_odds_open": _odds_int(_leg(total, "over", "open", "odds")),
        "total_under_odds_open": _odds_int(_leg(total, "under", "open", "odds")),
        "ml_home_odds": _odds_int(_leg(moneyline, "home", "close", "odds")),
        "ml_away_odds": _odds_int(_leg(moneyline, "away", "close", "odds")),
        "ml_home_odds_open": _odds_int(_leg(moneyline, "home", "open", "odds")),
        "ml_away_odds_open": _odds_int(_leg(moneyline, "away", "open", "odds")),
    }


def consensus_for_selection(
    record: dict[str, Any] | None, market_type: str, selection: str
) -> tuple[float | None, float | None]:
    """De-vigged consensus probability and consensus line for one pick side.

    The probability is the sportsbook's price for `selection` at the sportsbook's own
    line, so callers must compare lines before treating it as a price for their pick.
    """
    if not record:
        return None, None
    market = str(market_type).lower()
    side = str(selection).lower()
    if market == "spread":
        p_home, p_away = devig_two_sided(record.get("spread_home_odds"), record.get("spread_away_odds"))
        line = record.get("spread_home_line")
        if side == "home":
            return p_home, line
        if side == "away":
            return p_away, (-line if line is not None else None)
        return None, None
    if market == "total":
        p_over, p_under = devig_two_sided(record.get("total_over_odds"), record.get("total_under_odds"))
        line = record.get("total_line")
        if side == "over":
            return p_over, line
        if side == "under":
            return p_under, line
        return None, None
    if market in ("moneyline", "ml"):
        p_home, p_away = devig_two_sided(record.get("ml_home_odds"), record.get("ml_away_odds"))
        if side == "home":
            return p_home, None
        if side == "away":
            return p_away, None
    return None, None


def _record_hash(record: dict[str, Any]) -> str:
    payload = {k: v for k, v in record.items() if k != "record_hash"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def espn_odds_path(data_root: str | Path, game_date_et: str) -> Path:
    return Path(data_root) / "odds" / "nfl" / game_date_et / ESPN_ODDS_FILENAME


def capture_nfl_espn_odds(
    data_root: str | Path,
    start_date: str,
    *,
    days: int = 7,
    client: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append one consensus record per upcoming NFL game for `days` dates from start_date.

    Games that have already kicked off at capture time are skipped: the last record
    before kickoff is the closing consensus, and nothing after it may be mistaken for one.
    """
    from .espn import ESPNClient

    espn = client or ESPNClient()
    observed_at = now or datetime.now(UTC)
    start = date.fromisoformat(start_date)
    written = skipped_started = no_odds = 0
    dates_scanned: list[str] = []
    errors: list[dict[str, str]] = []
    for offset in range(max(1, days)):
        game_date = (start + timedelta(days=offset)).isoformat()
        dates_scanned.append(game_date)
        try:
            scoreboard = espn.scoreboard("nfl", game_date)
        except Exception as exc:  # noqa: BLE001 -- one bad date must not stop the week
            errors.append({"date": game_date, "error": f"{type(exc).__name__}: {exc}"[:200]})
            continue
        for event in scoreboard.get("events") or []:
            record = parse_espn_consensus(event)
            if record is None or not record["event_id"]:
                no_odds += 1
                continue
            try:
                kickoff = parse_utc(record["event_start_utc"])
            except (TypeError, ValueError):
                no_odds += 1
                continue
            if kickoff <= observed_at:
                skipped_started += 1
                continue
            record["observed_at_utc"] = observed_at.isoformat()
            record["source"] = "espn_scoreboard"
            record["record_hash"] = _record_hash(record)
            path = espn_odds_path(data_root, kickoff.astimezone(EASTERN).date().isoformat())
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            written += 1
    return {
        "status": "ok" if not errors else "partial",
        "observed_at_utc": observed_at.isoformat(),
        "dates_scanned": dates_scanned,
        "records_written": written,
        "skipped_started": skipped_started,
        "events_without_odds": no_odds,
        "errors": errors,
    }


def _iter_records(path: Path):
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("record_hash") != _record_hash(record):
                continue  # tampered or truncated line: never serve it as evidence
            yield record


def consensus_history(data_root: str | Path, event_id: str, event_start_utc: str) -> list[dict[str, Any]]:
    """Every verified consensus record for an event observed before kickoff, oldest first."""
    kickoff = parse_utc(event_start_utc)
    game_date = kickoff.astimezone(EASTERN).date()
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for offset in (0, -1, 1):
        path = espn_odds_path(data_root, (game_date + timedelta(days=offset)).isoformat())
        for record in _iter_records(path) or ():
            if str(record.get("event_id")) != str(event_id):
                continue
            try:
                observed = parse_utc(record["observed_at_utc"])
            except (KeyError, TypeError, ValueError):
                continue
            if observed > kickoff or record["record_hash"] in seen:
                continue
            seen.add(record["record_hash"])
            out.append(record)
    out.sort(key=lambda r: r["observed_at_utc"])
    return out


def closing_espn_consensus(
    data_root: str | Path, event_id: str, event_start_utc: str
) -> dict[str, Any] | None:
    """Last verified consensus record observed at or before kickoff, or None."""
    history = consensus_history(data_root, event_id, event_start_utc)
    return history[-1] if history else None


def consensus_known_at(
    data_root: str | Path, event_id: str, event_start_utc: str, decision_time_utc: str | datetime
) -> dict[str, Any] | None:
    """Latest verified consensus record observed at or before `decision_time_utc` (PIT read)."""
    cutoff = decision_time_utc if isinstance(decision_time_utc, datetime) else parse_utc(decision_time_utc)
    latest = None
    for record in consensus_history(data_root, event_id, event_start_utc):
        if parse_utc(record["observed_at_utc"]) <= cutoff:
            latest = record
    return latest


__all__ = [
    "capture_nfl_espn_odds",
    "closing_espn_consensus",
    "consensus_for_selection",
    "consensus_history",
    "consensus_known_at",
    "devig_two_sided",
    "espn_odds_path",
    "parse_espn_consensus",
]
