"""Manual Bet Ledger: tracks bets the operator places by hand (any sportsbook/exchange),
separate from the automated Auto-Buyer and the disabled Polymarket Edge Scanner.

Uses the standard project Pick Ledger format (shared FIELDNAMES/xlsx helpers) so it plugs
into the same settlement machinery as the Polymarket Edge Ledger, but stakes are entered
and stored in real dollars -- there's no model behind these picks, so a unit-based size
would be meaningless.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

_SLUG_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_SLUG_LINE_RE = re.compile(r"-(\d+)pt(\d+)$")
_TEAM_DISAMBIGUATOR_RE = re.compile(r"\s*\([^)]*\)\s*$")

# Polymarket's own league tokens (team.league, eventSlug prefix) don't match this
# project's canonical ledger league keys (see cli/state.py::_LEDGER_LEAGUE_TO_ESPN) --
# settlement's ESPN lookup silently finds nothing without this normalization.
_POLYMARKET_LEAGUE_TO_LEDGER_LEAGUE = {
    "cfb": "NCAAF",
    "mlb": "MLB",
    "nba": "NBA",
    "wnba": "WNBA",
    "nfl": "NFL",
    "nhl": "NHL",
    "cbb": "NBA",
}


def _normalize_synced_league(raw_league: str, event_slug: str) -> str:
    token = raw_league.strip().lower()
    if not token and event_slug:
        token = event_slug.split("-", 1)[0].strip().lower()
    return _POLYMARKET_LEAGUE_TO_LEDGER_LEAGUE.get(token, token.upper() or "UNKNOWN")


from ..config import PROJECT_ROOT
from ..domain import utc_now
from ..ledger import FIELDNAMES
from ..pricing import american_to_decimal
from ..xlsx_ledger import read_xlsx_rows, write_xlsx_rows_atomic
from .polymarket_ledger import _probability_to_american, settle_ledger_rows_against_espn

logger = logging.getLogger(__name__)

DEFAULT_MANUAL_LEDGER_PATH = PROJECT_ROOT / "data" / "manual_bets.xlsx"
DEFAULT_MANUAL_STATE_PATH = PROJECT_ROOT / "data" / "manual_bets_state.json"
DEFAULT_BANKROLL_USD = 350.0


def _get_ledger_path(data_root: Path | str | None = None) -> Path:
    if data_root is not None:
        p = Path(data_root)
        if p.is_dir():
            return p / "manual_bets.xlsx"
        return p
    return DEFAULT_MANUAL_LEDGER_PATH


def _get_state_path(data_root: Path | str | None = None) -> Path:
    if data_root is not None:
        p = Path(data_root)
        if p.is_dir():
            return p / "manual_bets_state.json"
    return DEFAULT_MANUAL_STATE_PATH


def read_manual_bets(data_root: Path | str | None = None) -> list[dict[str, Any]]:
    """Read all rows from the manual bet ledger."""
    path = _get_ledger_path(data_root)
    if not path.exists():
        return []
    _, rows = read_xlsx_rows(path)
    return rows


def get_manual_bankroll(data_root: Path | str | None = None) -> float:
    path = _get_state_path(data_root)
    if not path.exists():
        return DEFAULT_BANKROLL_USD
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        value = float(data.get("bankroll_usd", DEFAULT_BANKROLL_USD))
        return value if value > 0 else DEFAULT_BANKROLL_USD
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return DEFAULT_BANKROLL_USD


def set_manual_bankroll(value: Any, data_root: Path | str | None = None) -> dict[str, Any]:
    try:
        bankroll = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"bankroll must be numeric: {value!r}") from exc
    if not (bankroll > 0):
        raise ValueError("bankroll must be a positive number")
    path = _get_state_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps({"bankroll_usd": round(bankroll, 2)}, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)
    return {"status": "ok", "bankroll_usd": round(bankroll, 2)}


def record_manual_bet(bet: dict[str, Any], data_root: Path | str | None = None) -> dict[str, Any]:
    """Record a single manually-placed bet to the manual bet ledger.

    ``bet`` is a small, human-entered dict -- league/teams/selection/price/stake -- not
    the full model-decision schema. Missing model-facing fields (model_probability, edge)
    are left blank rather than defaulted to a fake value, since there is no model behind
    a manual pick and a fabricated 0.50 would masquerade as a real estimate.
    """
    path = _get_ledger_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        _, existing_rows = read_xlsx_rows(path)
    else:
        existing_rows = []
    existing_by_id = {str(r.get("pick_id")): r for r in existing_rows}

    league = str(bet.get("league") or "").upper().strip()
    away = str(bet.get("away_team") or "").strip()
    home = str(bet.get("home_team") or "").strip()
    market_type = str(bet.get("market_type") or "moneyline").lower().strip()
    selection = str(bet.get("selection") or "").lower().strip()
    event_start = str(bet.get("event_start_utc") or "").strip()
    sportsbook = str(bet.get("sportsbook") or "polymarket").lower().strip()
    line = bet.get("line", "")

    if not (league and away and home and selection and event_start):
        raise ValueError("league, away_team, home_team, selection, and event_start_utc are required")

    try:
        entry_price = float(bet.get("entry_price", ""))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"entry_price must be numeric: {bet.get('entry_price')!r}") from exc
    if not (0.0 < entry_price < 1.0):
        raise ValueError("entry_price must be a probability between 0 and 1 (e.g. 0.55)")

    try:
        stake_usd = round(float(bet.get("stake_usd", "")), 2)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"stake_usd must be numeric: {bet.get('stake_usd')!r}") from exc
    if not (stake_usd > 0):
        raise ValueError("stake_usd must be a positive dollar amount")

    now_iso = utc_now().isoformat()
    raw_key = f"manual:{league}:{away}:{home}:{market_type}:{selection}:{event_start}:{sportsbook}"
    pick_id = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]
    if pick_id in existing_by_id:
        return {
            "status": "duplicate",
            "pick_id": pick_id,
            "reason": "an identical open manual bet already exists for this event/selection",
        }

    american = _probability_to_american(entry_price)
    decimal_odds = american_to_decimal(american)

    row: dict[str, Any] = {
        "pick_id": pick_id,
        "created_at_utc": now_iso,
        "event_start_utc": event_start,
        "event_id": "",
        "league": league,
        "away_team": away,
        "home_team": home,
        "market_type": market_type,
        "selection": selection,
        "line": line,
        "sportsbook": sportsbook,
        "american_odds": american,
        "decimal_odds": round(decimal_odds, 4),
        "market_implied_probability": round(entry_price, 4),
        "model_probability": "",
        "model_uncertainty": "",
        "edge": "",
        "confidence_score": "",
        # The shared schema's "units" is a model-stake multiplier elsewhere in this
        # codebase; here it holds the real dollar stake directly since there is no
        # per-model unit_value_usd to multiply against for a manual bet.
        "units": stake_usd,
        "model_version": "manual",
        "status": "open",
        "result": "",
        "away_score": "",
        "home_score": "",
        "probability_clv": "",
        "pnl_units": "",
        "settled_at_utc": "",
        "record_type": "manual_bet",
        "decision": "take",
        "reason_code": "MANUAL_ENTRY",
        "rationale": str(bet.get("note") or ""),
        "ledger_schema_version": 4,
    }
    all_rows = existing_rows + [row]
    all_rows.sort(key=lambda r: str(r.get("event_start_utc") or r.get("created_at_utc") or ""), reverse=True)
    write_xlsx_rows_atomic(path, FIELDNAMES, all_rows)

    return {"status": "ok", "pick_id": pick_id, "stake_usd": stake_usd, "ledger_path": str(path)}


def settle_manual_bets(
    data_root: Path | str | None = None,
    espn_client: Any | None = None,
    resettle_all: bool = False,
) -> dict[str, Any]:
    """Settle open manual bets against ESPN/tennis/Polymarket-resolution scores."""
    return settle_ledger_rows_against_espn(
        _get_ledger_path(data_root), espn_client=espn_client, resettle_all=resettle_all
    )


def sync_manual_bets_from_polymarket(
    data_root: Path | str | None = None,
    executor: Any | None = None,
) -> dict[str, Any]:
    """Pull the live Polymarket US account and auto-record any open position that isn't
    already accounted for by either the Auto-Buyer ledger or this manual ledger.

    A position is "manual" purely by elimination: the Auto-Buyer ledger is the
    authoritative record of every order the automated system itself placed (keyed by
    market_slug), so any live position with a market_slug the Auto-Buyer never touched
    must have been placed by hand. This intentionally does not try to distinguish by
    order metadata (Polymarket's own API doesn't expose "who/what placed this order") --
    ledger cross-reference is the only reliable signal available.
    """
    from ..audit import AuditLog
    from .auto_buyer_ledger import read_auto_buyer_ledger

    path = _get_ledger_path(data_root)
    audit_root = (
        Path(data_root) if data_root is not None and Path(data_root).is_dir() else PROJECT_ROOT / "data"
    )

    if executor is None:
        from ..data_sources.polymarket_execute import PolymarketExecutor

        executor = PolymarketExecutor(audit=AuditLog(audit_root / "audit.jsonl"))

    snapshot = executor.portfolio_snapshot()
    live_positions: dict[str, Any] = snapshot.get("positions") or {}

    auto_buyer_jsonl = (Path(data_root) / "auto_buyer_ledger.jsonl") if data_root is not None else None
    auto_buyer_slugs = {
        str(r.get("market_slug")) for r in read_auto_buyer_ledger(jsonl_path=auto_buyer_jsonl)
    }
    known_manual_slugs = {str(r.get("event_id")) for r in read_manual_bets(data_root) if r.get("event_id")}

    synced: list[str] = []
    skipped_existing = 0
    skipped_zero = 0

    for slug, position in live_positions.items():
        if slug in auto_buyer_slugs:
            continue
        if slug in known_manual_slugs:
            skipped_existing += 1
            continue
        try:
            net_qty = float(position.get("netPosition") or 0.0)
            cost_usd = float((position.get("cost") or {}).get("value") or 0.0)
        except (TypeError, ValueError):
            continue
        if abs(net_qty) < 1e-9 or cost_usd <= 0:
            skipped_zero += 1
            continue

        meta = position.get("marketMetadata") or {}
        team = meta.get("team") or {}
        league = _normalize_synced_league(str(team.get("league") or ""), str(meta.get("eventSlug") or ""))
        outcome = str(meta.get("outcome") or "").strip()
        title = str(meta.get("title") or "").strip()
        away, _, home = title.partition(" vs. ")
        # Polymarket disambiguates same-named schools with a trailing "(FL)"/"(OH)" etc.
        # that ESPN's own team.displayName never carries -- left in place, it breaks the
        # settlement team-name matcher's substring comparison entirely (e.g. "Miami (FL)"
        # matches neither "Miami Hurricanes" nor "Miami (OH) RedHawks").
        away = _TEAM_DISAMBIGUATOR_RE.sub("", away)
        home = _TEAM_DISAMBIGUATOR_RE.sub("", home)
        entry_price = round(min(0.99, max(0.01, cost_usd / abs(net_qty))), 4)

        market_type = "moneyline"
        selection = outcome.lower() if outcome else ("long" if net_qty > 0 else "short")
        line: float | str = ""
        line_match = _SLUG_LINE_RE.search(slug)
        slug_line = float(f"{line_match.group(1)}.{line_match.group(2)}") if line_match else None

        if slug.startswith("asc-"):
            market_type = "spread"
            # The outcome field IS the signed line for these alt-spread markets (e.g.
            # "-40.50"); grade_pick needs selection="home"/"away" + a signed line, but
            # Polymarket's payload doesn't say which side the position is on -- "home"
            # is a best-effort assumption (matches every sample observed so far), flagged
            # below for the operator to verify.
            try:
                line = float(outcome) if outcome else (slug_line or "")
            except ValueError:
                line = slug_line or ""
            selection = "home"
        elif slug.startswith("tsc-"):
            market_type = "total"
            selection = outcome.lower() if outcome.lower() in ("over", "under") else selection
            line = slug_line if slug_line is not None else ""

        # Polymarket's position payload has no real kickoff time -- only the slug's
        # embedded event date (no time-of-day) and this position's last-update
        # timestamp. Neither is the true game time, so this is a best-effort
        # placeholder (noon UTC on the slug's date) flagged for the operator to
        # correct; it exists only so settlement's ESPN day-lookup has a date to use.
        date_match = _SLUG_DATE_RE.search(str(meta.get("eventSlug") or slug))
        note_parts = [
            f"auto-synced from Polymarket account (position updated {position.get('updateTime', '')})"
        ]
        if date_match:
            event_start_utc = f"{date_match.group(1)}T12:00:00Z"
            note_parts.append(
                "event_start_utc is a placeholder (noon UTC on the market's slug date, not the real kickoff) -- correct if needed"
            )
        else:
            event_start_utc = str(position.get("updateTime") or "")
            note_parts.append(
                "no date found in market slug -- event_start_utc defaulted to last position update time"
            )
        if not title:
            note_parts.append("market metadata unavailable from Polymarket API -- identify manually")
        if market_type == "spread":
            note_parts.append(
                "selection='home' is an unverified assumption for alt-spread syncs -- confirm which side this position is actually on"
            )
        if market_type == "total" and "-tt-" in slug:
            note_parts.append(
                "TEAM TOTAL (not game total) -- automated ESPN settlement grades this against the "
                "combined game score, which is WRONG for a single-team total; verify and use the "
                "manual Win/Loss/Push override instead of trusting an automated result"
            )

        bet = {
            "league": league,
            "away_team": away.strip() or "Unknown",
            "home_team": (home.strip() or title or slug),
            "market_type": market_type,
            "selection": selection,
            "line": line,
            "entry_price": entry_price,
            "stake_usd": round(cost_usd, 2),
            "event_start_utc": event_start_utc,
            "sportsbook": "polymarket",
            "note": "; ".join(note_parts),
        }
        try:
            result = record_manual_bet(bet, data_root=data_root)
        except ValueError as exc:
            logger.warning("Could not auto-sync Polymarket position %s: %s", slug, exc)
            continue
        if result.get("status") == "ok":
            # Tag with the market_slug via event_id so re-runs recognize this position
            # (record_manual_bet's own dedup key doesn't include the exchange slug).
            rows = read_manual_bets(data_root)
            for row in rows:
                if row.get("pick_id") == result.get("pick_id"):
                    row["event_id"] = slug
            write_xlsx_rows_atomic(path, FIELDNAMES, rows)
            synced.append(slug)

    return {
        "status": "ok",
        "synced_count": len(synced),
        "synced_slugs": synced,
        "skipped_existing": skipped_existing,
        "skipped_zero_position": skipped_zero,
        "live_position_count": len(live_positions),
    }


def mark_manual_bet_result(
    pick_id: str,
    result: str,
    data_root: Path | str | None = None,
) -> dict[str, Any]:
    """Manually override a bet's result (win/loss/push) for markets ESPN can't grade
    (esports, obscure leagues, futures). Bypasses score-matching entirely -- this is an
    explicit operator assertion, not an inference.
    """
    result = result.lower().strip()
    if result not in ("win", "loss", "push"):
        raise ValueError(f"result must be one of win/loss/push, got {result!r}")

    path = _get_ledger_path(data_root)
    if not path.exists():
        raise ValueError("manual bet ledger does not exist yet")
    _, rows = read_xlsx_rows(path)

    target = next((r for r in rows if str(r.get("pick_id")) == pick_id), None)
    if target is None:
        raise ValueError(f"no manual bet found with pick_id={pick_id!r}")

    units = float(target.get("units") or 0.0)
    decimal_odds = float(target.get("decimal_odds") or 1.909)
    if result == "win":
        pnl = units * (decimal_odds - 1.0)
    elif result == "loss":
        pnl = -units
    else:
        pnl = 0.0

    target["status"] = "settled"
    target["result"] = result
    target["pnl_units"] = str(round(pnl, 2))
    target["settled_at_utc"] = utc_now().isoformat()
    target["void_reason"] = "manual_override"

    write_xlsx_rows_atomic(path, FIELDNAMES, rows)
    return {"status": "ok", "pick_id": pick_id, "result": result, "pnl_usd": round(pnl, 2)}


def _run_scheduled_sync() -> None:
    """Entry point for the periodic sync worker (see run_supervisor.WORKERS)."""
    result = sync_manual_bets_from_polymarket()
    logger.info("Manual bet sync: %s", json.dumps(result))
    print(json.dumps(result))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    _run_scheduled_sync()
