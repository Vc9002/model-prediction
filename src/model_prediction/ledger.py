from __future__ import annotations

import fcntl
import json
import shutil
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import datetime
from pathlib import Path

from .audit import AuditLog
from .calibration import calibration_metrics
from .domain import (
    EASTERN,
    LOSS_CLASSIFICATIONS,
    MarketType,
    ModelOrigin,
    PickRequest,
    PickResult,
    PickStatus,
    RecordType,
    iso_utc,
    parse_utc,
    utc_now,
)
from .eligibility import EligibilityResult
from .pricing import american_to_decimal, grade_pick, implied_probability, profit_units
from .units import Exposure, edge_scaled_units
from .xlsx_ledger import read_xlsx_rows, write_xlsx_rows_atomic

# Exposure "day" boundaries follow US Eastern time, not UTC. A 10pm ET slate
# would otherwise straddle two UTC cap windows and reset caps mid-slate.
EXPOSURE_TIMEZONE = EASTERN

# fcntl.flock(LOCK_EX) blocks indefinitely with no built-in timeout -- a
# hung holder (not crashed; POSIX auto-releases on process death) would
# otherwise wedge every other reader/writer forever with no diagnostic.
LOCK_TIMEOUT_SECONDS = 30
LOCK_POLL_INTERVAL_SECONDS = 0.1


class LedgerLockTimeout(TimeoutError):
    """Raised when a ledger file lock can't be acquired within the timeout."""


def _acquire_exclusive_lock(fileno: int, path: Path, timeout: float = LOCK_TIMEOUT_SECONDS) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fileno, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise LedgerLockTimeout(
                    f"could not acquire lock on {path} within {timeout}s -- "
                    "another process may be hung while holding it"
                ) from None
            time.sleep(LOCK_POLL_INTERVAL_SECONDS)

LEDGER_SCHEMA_VERSION = "3"
LEGACY_FIELDNAMES = [
    "pick_id",
    "created_at_utc",
    "event_start_utc",
    "event_id",
    "league",
    "away_team",
    "home_team",
    "market_type",
    "selection",
    "line",
    "sportsbook",
    "american_odds",
    "decimal_odds",
    "market_implied_probability",
    "model_probability",
    "model_uncertainty",
    "edge",
    "confidence_score",
    "units",
    "model_version",
    "status",
    "result",
    "away_score",
    "home_score",
    "closing_line",
    "closing_american_odds",
    "closing_implied_probability",
    "probability_clv",
    "pnl_units",
    "settled_at_utc",
    "rationale",
    "risks",
    "review_status",
    "loss_classification",
    "loss_cause",
    "corrective_action",
    "call_type",
]
FIELDNAMES = [*LEGACY_FIELDNAMES,
    "ledger_schema_version",
    "record_type",
    "decision",
    "reason_code",
    "canonical_away_team_id",
    "canonical_away_team_name",
    "original_away_team",
    "canonical_home_team_id",
    "canonical_home_team_name",
    "original_home_team",
    "banned_team_id",
    "banned_team_name",
    "model_origin",
    "baseline_identifier",
    "model_state_at_decision",
    "model_artifact_hash",
    "calibration_method",
    "calibration_version",
    "calibration_artifact_hash",
    "feature_schema_version",
    "entity_map_version",
    "code_revision",
    "observed_at_utc",
    "correlation_tags",
    "decision_line",
    "decision_american_odds",
    "decision_decimal_odds",
    "decision_raw_implied_probability",
    "decision_no_vig_probability",
    "decision_consensus_probability",
    "decision_consensus_line",
    "closing_decimal_odds",
    "closing_raw_implied_probability",
    "closing_no_vig_probability",
    "closing_consensus_probability",
    "closing_consensus_line",
    "legacy_units",
    "void_reason",
    "research_score_units",
    "research_pnl_units",
    "research_scored_at_utc",
    "research_scoring_note",
    # Original model_version when a migration rewrote lineage in place
    # (e.g. the 2026-07 v3->v4 MLB migration). Empty for native rows.
    "migrated_from_version",
    # Feature value columns — one per active model feature, populated at prediction time
    # Added 2026-07-22 for dashboard feature attribution
    "elo_probability",
    "trend_gap",
    "park_factor",
    "weather_factor",
    "pitcher_era_gap",
    "probable_starter_era_gap",
    "unavailable_features",
    "defensive_trend_gap",
    "neutral_elo_rating_difference",
    # Schedule / roadmap challenger feature columns
    "rest_disparity",
    "back_to_back_gap",
    "games_last_7_gap",
    "schedule_missingness",
]
DECISION_FIELDS = {
    "pick_id",
    "created_at_utc",
    "event_start_utc",
    "event_id",
    "league",
    "away_team",
    "home_team",
    "market_type",
    "selection",
    "line",
    "sportsbook",
    "american_odds",
    "decimal_odds",
    "market_implied_probability",
    "model_probability",
    "model_uncertainty",
    "edge",
    "confidence_score",
    "units",
    "model_version",
    "rationale",
    "risks",
    "call_type",
    "record_type",
    "decision",
    "reason_code",
    "canonical_away_team_id",
    "canonical_home_team_id",
    "model_origin",
    "baseline_identifier",
    "model_state_at_decision",
    "model_artifact_hash",
    "calibration_version",
    "calibration_artifact_hash",
    "feature_schema_version",
    "entity_map_version",
    "code_revision",
    "observed_at_utc",
    "correlation_tags",
    "decision_line",
    "decision_american_odds",
    "decision_decimal_odds",
    "decision_raw_implied_probability",
    "decision_no_vig_probability",
    "decision_consensus_probability",
    "decision_consensus_line",
    "legacy_units",
}


def _market_duplicate_key(
    event_id: str,
    market_type: str,
    line: str,
    sportsbook: str,
    model_version: str,
) -> tuple[str, str, str, str, str]:
    normalized_line = line
    if line:
        with suppress(ValueError):
            normalized_line = str(abs(float(line)))
    return (event_id, market_type, normalized_line, sportsbook.casefold(), model_version)


class DuplicatePickError(ValueError):
    def __init__(self, pick_id: str) -> None:
        self.pick_id = pick_id
        super().__init__(f"duplicate call already logged as {pick_id}")


class PickLedger:
    def __init__(
        self,
        path: str | Path,
        audit_path: str | Path | None = None,
        research_score_units: float | None = None,
        research_scoring_mode: str = "fixed",
        research_scoring_note: str = "fixed-stake hypothetical research scoring",
    ) -> None:
        self.path = Path(path)
        if self.path.suffix.casefold() != ".xlsx":
            raise ValueError("the active picks ledger must be an .xlsx workbook")
        if research_score_units is not None and research_score_units <= 0:
            raise ValueError("research scoring units must be positive")
        if research_scoring_mode not in {"fixed", "model_recommended"}:
            raise ValueError("research scoring mode must be fixed or model_recommended")
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.audit = AuditLog(audit_path or self.path.with_name("events.jsonl"))
        self.research_score_units = research_score_units
        self.research_scoring_mode = research_scoring_mode
        self.research_scoring_note = research_scoring_note

    @contextmanager
    def _lock(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+") as lock:
            _acquire_exclusive_lock(lock.fileno(), self.lock_path)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def initialize(self) -> None:
        migrated_from: str | None = None
        with self._lock():
            if not self.path.exists() or self.path.stat().st_size == 0:
                self._write_rows([])
            else:
                migrated_from = self._migrate_if_needed()
        if migrated_from:
            self.audit.append(
                "ledger_migrated",
                str(self.path),
                {
                    "from_schema": migrated_from,
                    "to_schema": LEDGER_SCHEMA_VERSION,
                    "backup": str(self._backup_path(migrated_from)),
                },
            )

    def rows(self, filters: dict[str, str] | None = None) -> list[dict[str, str]]:
        self.initialize()
        _, rows = read_xlsx_rows(self.path)
        return self._filter_rows(rows, filters or {})

    def exposure(
        self,
        request: PickRequest,
        now: datetime | None = None,
        canonical_team_ids: tuple[str, str] | None = None,
    ) -> Exposure:
        current = now or utc_now()
        today = current.astimezone(EXPOSURE_TIMEZONE).date().isoformat()
        daily = league_daily = event = team_daily = 0.0
        request_teams = {request.away_team.casefold(), request.home_team.casefold()}
        if canonical_team_ids:
            request_teams.update(team_id.casefold() for team_id in canonical_team_ids)
        for row in self.rows():
            if row["record_type"] != RecordType.QUALIFIED_SHADOW_CALL.value or row["decision"] != "CALL":
                continue
            # An OPEN row for this exact same bet (event + market + selection)
            # is this decision's own still-pending prior instance, not
            # competing exposure -- e.g. flat re-evaluating a game main
            # already logged a real open call for. Without this exclusion it
            # double-counts against itself and collapses to a much smaller
            # size than the same decision gets when sized fresh (main clears
            # its own open row before re-forecasting the same event, so it
            # never sees this self-collision; only a second ledger reading
            # main's still-open row can). Settled rows are never excluded --
            # audit/reporting queries for an already-decided bet expect its
            # own settled exposure to still show up.
            is_same_bet = (
                row["status"] == PickStatus.OPEN.value
                and row["event_id"] == request.event_id
                and row["market_type"] == request.market_type.value
                and row["selection"] == request.selection.lower()
            )
            units = float(row["units"] or 0)
            try:
                row_day = parse_utc(row["created_at_utc"]).astimezone(EXPOSURE_TIMEZONE).date().isoformat()
            except (KeyError, ValueError):
                row_day = ""
            if row_day == today and not is_same_bet:
                daily += units
                if row["league"] == request.league.value:
                    league_daily += units
                row_teams = {
                    row["canonical_away_team_id"].casefold(),
                    row["canonical_home_team_id"].casefold(),
                    row["away_team"].casefold(),
                    row["home_team"].casefold(),
                    row["original_away_team"].casefold(),
                    row["original_home_team"].casefold(),
                }
                if request_teams & row_teams:
                    team_daily += units
            if row["event_id"] == request.event_id and not is_same_bet:
                event += units
        return Exposure(daily, league_daily, event, team_daily)

    def append_evaluated(
        self, request: PickRequest, eligibility: EligibilityResult, now: datetime | None = None
    ) -> dict[str, str]:
        return self._append_record(request, eligibility, now or utc_now())

    def append_call(
        self,
        request: PickRequest,
        units: float,
        confidence_score: int,
        call_type: str = "model_qualified",
        now: datetime | None = None,
    ) -> dict[str, str]:
        """Backward-compatible writer used by older callers and tests.

        Legacy forced calls are converted to zero-unit research observations.
        """
        from .entities import CanonicalTeam

        research = call_type == "forced_call" or units <= 0
        placeholder_away = CanonicalTeam(
            request.away_team, request.league, request.away_team, request.away_team, True, None, None, ()
        )
        placeholder_home = CanonicalTeam(
            request.home_team, request.league, request.home_team, request.home_team, True, None, None, ()
        )
        eligibility = EligibilityResult(
            RecordType.RESEARCH_OBSERVATION if research else RecordType.QUALIFIED_SHADOW_CALL,
            "RESEARCH_OBSERVATION" if research else "CALL",
            "NO_CALL_MODEL_UNVALIDATED" if research else "QUALIFIED",
            0 if research else units,
            confidence_score,
            request.model_probability - implied_probability(request.american_odds),
            request.model_probability
            - (request.model_uncertainty or 0)
            - implied_probability(request.american_odds),
            placeholder_away,
            placeholder_home,
        )
        row = self._append_record(request, eligibility, now or utc_now())
        if research and units:
            self._set_legacy_units(row["pick_id"], units)
            row["legacy_units"] = f"{units:.2f}"
        return row

    def _append_record(
        self, request: PickRequest, eligibility: EligibilityResult, created: datetime
    ) -> dict[str, str]:
        self.initialize()
        request.validate(created)
        decimal_odds = american_to_decimal(request.american_odds)
        raw_probability = implied_probability(request.american_odds)
        line_value = "" if request.line is None else str(request.line)
        # The duplicate key deliberately EXCLUDES the selection and normalizes the
        # line so that both sides of one market (over AND under of the same total,
        # away AND home of the same spread) can never both be logged.
        key = _market_duplicate_key(
            request.event_id,
            request.market_type.value,
            line_value,
            request.sportsbook,
            request.model_version,
        )
        with self._lock():
            self._migrate_if_needed()
            existing = self._read_unlocked()
            for item in existing:
                item_key = _market_duplicate_key(
                    item["event_id"],
                    item["market_type"],
                    item["line"],
                    item["sportsbook"],
                    item["model_version"],
                )
                if item_key == key:
                    raise DuplicatePickError(item["pick_id"])
            row = {field: "" for field in FIELDNAMES}
            request_values = request.as_dict()
            row.update(
                {
                    key: "" if value is None else str(value)
                    for key, value in request_values.items()
                    if key in row
                }
            )
            pick_id = uuid.uuid4().hex[:16]
            call_type = (
                "model_qualified"
                if eligibility.record_type is RecordType.QUALIFIED_SHADOW_CALL
                else "research_observation"
            )
            if eligibility.reason_code == "NO_CALL_TEAM_BANNED":
                call_type = "no_call"
            row.update(
                {
                    "pick_id": pick_id,
                    "created_at_utc": iso_utc(created),
                    "away_team": eligibility.away_team.canonical_name,
                    "home_team": eligibility.home_team.canonical_name,
                    "original_away_team": request.away_team,
                    "original_home_team": request.home_team,
                    "canonical_away_team_id": eligibility.away_team.canonical_team_id,
                    "canonical_away_team_name": eligibility.away_team.canonical_name,
                    "canonical_home_team_id": eligibility.home_team.canonical_team_id,
                    "canonical_home_team_name": eligibility.home_team.canonical_name,
                    "decimal_odds": f"{decimal_odds:.6f}",
                    "market_implied_probability": f"{raw_probability:.6f}",
                    "model_probability": f"{request.model_probability:.6f}",
                    "model_uncertainty": ""
                    if request.model_uncertainty is None
                    else f"{request.model_uncertainty:.6f}",
                    "edge": f"{eligibility.edge:.6f}",
                    "confidence_score": str(eligibility.confidence_score),
                    "units": f"{eligibility.units:.2f}",
                    "status": PickStatus.OPEN.value,
                    "review_status": "not_applicable",
                    "call_type": call_type,
                    "ledger_schema_version": LEDGER_SCHEMA_VERSION,
                    "record_type": eligibility.record_type.value,
                    "decision": eligibility.decision,
                    "reason_code": eligibility.reason_code,
                    "banned_team_id": eligibility.banned_team.canonical_team_id
                    if eligibility.banned_team
                    else "",
                    "banned_team_name": eligibility.banned_team.canonical_name
                    if eligibility.banned_team
                    else "",
                    "model_origin": request.model_origin.value,
                    "baseline_identifier": request.baseline_identifier or "",
                    "model_state_at_decision": request.model_state.value,
                    "model_artifact_hash": request.model_artifact_hash,
                    "calibration_method": request.calibration_method,
                    "calibration_version": request.calibration_version,
                    "calibration_artifact_hash": request.calibration_artifact_hash,
                    "feature_schema_version": request.feature_schema_version,
                    "entity_map_version": request.entity_map_version,
                    "code_revision": request.code_revision,
                    "observed_at_utc": request.observed_at_utc or iso_utc(created),
                    "correlation_tags": json.dumps(request.correlation_tags, separators=(",", ":")),
                    "decision_line": line_value,
                    "decision_american_odds": str(request.american_odds),
                    "decision_decimal_odds": f"{decimal_odds:.6f}",
                    "decision_raw_implied_probability": f"{raw_probability:.6f}",
                    "decision_no_vig_probability": ""
                    if request.decision_no_vig_probability is None
                    else f"{request.decision_no_vig_probability:.6f}",
                    "decision_consensus_probability": ""
                    if request.decision_consensus_probability is None
                    else f"{request.decision_consensus_probability:.6f}",
                    "decision_consensus_line": ""
                    if request.decision_consensus_line is None
                    else str(request.decision_consensus_line),
                }
            )
            existing.append(row)
            self._write_rows(existing)
        event_type = (
            "pick_created"
            if eligibility.record_type is RecordType.QUALIFIED_SHADOW_CALL
            else "research_observation_created"
        )
        self.audit.append(event_type, pick_id, self._decision_audit_payload(row))
        if eligibility.decision == "NO_CALL":
            self.audit.append(
                "pick_rejected",
                pick_id,
                {"reason_code": eligibility.reason_code, "record_type": eligibility.record_type.value},
            )
        return row

    def settle(
        self,
        pick_id: str,
        away_score: int,
        home_score: int,
        closing_line: float | None = None,
        closing_american_odds: int | None = None,
        settled_at: datetime | None = None,
        closing_no_vig_probability: float | None = None,
        closing_consensus_probability: float | None = None,
        closing_consensus_line: float | None = None,
        closing_raw_probability: float | None = None,
    ) -> dict[str, str]:
        self.initialize()
        if away_score < 0 or home_score < 0:
            raise ValueError("scores cannot be negative")
        with self._lock():
            self._migrate_if_needed()
            rows = self._read_unlocked()
            row = self._find(rows, pick_id)
            if row["status"] == PickStatus.SETTLED.value:
                if row["away_score"] == str(away_score) and row["home_score"] == str(home_score):
                    return row
                raise ValueError("pick is already settled with different scores")
            decision_snapshot = {field: row[field] for field in DECISION_FIELDS}
            line = (
                float(row["decision_line"] or row["line"]) if (row["decision_line"] or row["line"]) else None
            )
            result = grade_pick(
                MarketType(row["market_type"]), row["selection"], line, away_score, home_score
            )
            closing_decimal = (
                american_to_decimal(closing_american_odds) if closing_american_odds is not None else None
            )
            if closing_raw_probability is not None and not 0 < closing_raw_probability < 1:
                raise ValueError("closing raw probability must be between 0 and 1")
            closing_probability = closing_raw_probability
            if closing_probability is None and closing_american_odds is not None:
                closing_probability = implied_probability(closing_american_odds)
            # Every logged pick — qualified or research/diagnostic — carries a
            # real unit size (see cli._log_esports_forecast / _forecast_learned_sport:
            # "every pick must have units"), and every ledger settles that size
            # into a real pnl_units, main or flat or research or gated. Whether
            # a ledger's P&L represents actually-staked money is a property of
            # the ledger itself (main only), not something settle() decides.
            units = float(row["units"] or 0)
            pnl = profit_units(result, units, float(row["decision_decimal_odds"] or row["decimal_odds"]))
            research_units = None
            if (
                row["record_type"] == RecordType.RESEARCH_OBSERVATION.value
                and self.research_score_units is not None
            ):
                research_units = self.research_score_units
                if self.research_scoring_mode == "model_recommended":
                    raw_uncertainty = row["model_uncertainty"]
                    if raw_uncertainty in (None, ""):
                        # Uncertainty was never recorded for this row (e.g. a
                        # pre-model_uncertainty record); scoring it as 0 would
                        # manufacture false confidence, so leave it unscored.
                        research_units = None
                    else:
                        research_units = edge_scaled_units(
                            float(row["model_probability"]),
                            float(raw_uncertainty),
                            int(row["decision_american_odds"] or row["american_odds"]),
                        )
            research_pnl = (
                profit_units(
                    result,
                    research_units,
                    float(row["decision_decimal_odds"] or row["decimal_odds"]),
                )
                if research_units is not None
                else None
            )
            settled_at_utc = iso_utc(settled_at or utc_now())
            row.update(
                {
                    "status": PickStatus.SETTLED.value,
                    "result": result.value,
                    "away_score": str(away_score),
                    "home_score": str(home_score),
                    "closing_line": "" if closing_line is None else str(closing_line),
                    "closing_american_odds": ""
                    if closing_american_odds is None
                    else str(closing_american_odds),
                    "closing_implied_probability": ""
                    if closing_probability is None
                    else f"{closing_probability:.6f}",
                    "closing_decimal_odds": "" if closing_decimal is None else f"{closing_decimal:.6f}",
                    "closing_raw_implied_probability": ""
                    if closing_probability is None
                    else f"{closing_probability:.6f}",
                    "closing_no_vig_probability": ""
                    if closing_no_vig_probability is None
                    else f"{closing_no_vig_probability:.6f}",
                    "closing_consensus_probability": ""
                    if closing_consensus_probability is None
                    else f"{closing_consensus_probability:.6f}",
                    "closing_consensus_line": ""
                    if closing_consensus_line is None
                    else str(closing_consensus_line),
                    "probability_clv": ""
                    if closing_probability is None
                    else f"{closing_probability - float(row['decision_raw_implied_probability'] or row['market_implied_probability']):.6f}",
                    "pnl_units": f"{pnl:.4f}",
                    "settled_at_utc": settled_at_utc,
                    "review_status": "review_required" if result is PickResult.LOSS else "not_applicable",
                }
            )
            if research_units is not None:
                row.update(
                    {
                        "research_score_units": f"{research_units:.4f}",
                        "research_pnl_units": f"{research_pnl:.6f}",
                        "research_scored_at_utc": settled_at_utc,
                        "research_scoring_note": self.research_scoring_note,
                    }
                )
            self._assert_decision_snapshot(row, decision_snapshot)
            self._write_rows(rows)
        self.audit.append(
            "pick_settled",
            pick_id,
            {
                "away_score": away_score,
                "home_score": home_score,
                "result": result.value,
                "pnl_units": pnl,
                "research_score_units": research_units,
                "research_pnl_units": research_pnl,
            },
        )
        return row

    def recompute_research_sizing(self, policy=None) -> int:
        """Recompute ``units`` (and ``pnl_units`` for already-settled rows)
        for every RESEARCH_OBSERVATION row, from that row's own stored
        decision-time fields, using the current sizing rule in
        ``eligibility._research``: edge_scaled_units for reasons that still
        reflect a genuine model opinion (LOW_EDGE, EXPOSURE_LIMIT,
        LARGE_DISAGREEMENT_REVIEW), hard-zero for every other reason.

        One-time backfill for rows logged under an earlier, buggier version
        of that rule (a flat min_pick_units cap regardless of edge, or a
        hardcoded zero) -- brings already-logged history in line with the
        current rule without re-forecasting or touching decision/reason_code.
        QUALIFIED_SHADOW_CALL rows are never touched: their sizing always
        came from the real, validated, exposure-capped path, not this one.
        """
        from .config import load_config
        from .config import unit_policy as _unit_policy

        policy = policy or _unit_policy(load_config())
        sizable_reasons = {
            "NO_CALL_LOW_EDGE",
            "NO_CALL_EXPOSURE_LIMIT",
            "NO_CALL_LARGE_DISAGREEMENT_REVIEW",
        }
        changed = 0
        with self._lock():
            self._migrate_if_needed()
            rows = self._read_unlocked()
            for row in rows:
                if row["record_type"] != RecordType.RESEARCH_OBSERVATION.value:
                    continue
                try:
                    model_probability = float(row["model_probability"])
                    american_odds = int(float(row["decision_american_odds"] or row["american_odds"]))
                except (KeyError, ValueError, TypeError):
                    continue
                uncertainty_raw = row.get("model_uncertainty")
                uncertainty = float(uncertainty_raw) if uncertainty_raw not in (None, "") else 0.05
                new_units = (
                    edge_scaled_units(model_probability, uncertainty or 0.05, american_odds, policy)
                    if row.get("reason_code") in sizable_reasons
                    else 0.0
                )
                old_units = float(row["units"] or 0)
                if abs(new_units - old_units) < 1e-9:
                    continue
                row["units"] = f"{new_units:.4f}"
                if row["status"] == PickStatus.SETTLED.value and row.get("result"):
                    decimal_odds = float(row["decision_decimal_odds"] or row["decimal_odds"])
                    pnl = profit_units(PickResult(row["result"]), new_units, decimal_odds)
                    row["pnl_units"] = f"{pnl:.4f}"
                changed += 1
            if changed:
                self._write_rows(rows)
        if changed:
            self.audit.append(
                "ledger_units_recomputed",
                str(self.path),
                {"rows_changed": changed, "reason": "backfill fixed research-observation sizing rule"},
            )
        return changed

    def remove_open_rows(
        self, pick_ids: list[str], reason: str, allow_staked_removal: bool = False
    ) -> list[str]:
        """Physically remove OPEN rows under the ledger lock, with audit events.

        This is the ONLY sanctioned deletion path. Settled rows are always
        refused. Rows with positive units AND record_type QUALIFIED_SHADOW_CALL
        are refused too, UNLESS ``allow_staked_removal=True`` — that guard
        means "real staked exposure" only on the MAIN ledger (picks.xlsx),
        where QUALIFIED_SHADOW_CALL + units > 0 really does represent a bet
        that was placed. On flat_picks.xlsx (and research/gated ledgers),
        nothing is ever actually staked regardless of record_type — an MLB
        candidate strong enough to independently clear real eligibility still
        gets logged as QUALIFIED_SHADOW_CALL there, and without this override
        that row would silently never be replaced by any future re-forecast,
        permanently freezing its features/probability from whichever run
        first produced it. Callers pass True only for ledgers that can never
        hold real money.
        """
        if not reason.strip():
            raise ValueError("a removal reason is required")
        requested = {str(pick_id) for pick_id in pick_ids if str(pick_id)}
        if not requested:
            return []
        removed: list[dict[str, str]] = []
        with self._lock():
            self._migrate_if_needed()
            rows = self._read_unlocked()
            keep: list[dict[str, str]] = []
            for row in rows:
                if row["pick_id"] in requested and row["status"] == PickStatus.OPEN.value:
                    if (
                        not allow_staked_removal
                        and float(row["units"] or 0) > 0
                        and row["record_type"] == RecordType.QUALIFIED_SHADOW_CALL.value
                    ):
                        keep.append(row)  # never delete open staked exposure
                        continue
                    removed.append(row)
                    continue
                keep.append(row)
            if removed:
                self._write_rows(keep)
        for row in removed:
            self.audit.append(
                "pick_removed",
                row["pick_id"],
                {
                    "reason": reason,
                    "event_id": row["event_id"],
                    "league": row["league"],
                    "market_type": row["market_type"],
                    "selection": row["selection"],
                    "model_version": row["model_version"],
                    "record_type": row["record_type"],
                    "created_at_utc": row["created_at_utc"],
                },
            )
        return [row["pick_id"] for row in removed]

    def void(self, pick_id: str, reason: str) -> dict[str, str]:
        self.initialize()
        if not reason.strip():
            raise ValueError("void reason is required")
        with self._lock():
            self._migrate_if_needed()
            rows = self._read_unlocked()
            row = self._find(rows, pick_id)
            if row["status"] == PickStatus.SETTLED.value:
                if row["void_reason"] == reason:
                    return row
                raise ValueError("pick is already settled")
            row.update(
                {
                    "status": PickStatus.SETTLED.value,
                    "result": PickResult.PUSH.value,
                    "pnl_units": "0.0000",
                    "settled_at_utc": iso_utc(utc_now()),
                    "void_reason": reason,
                    "review_status": "not_applicable",
                }
            )
            self._write_rows(rows)
        self.audit.append("pick_voided", pick_id, {"reason": reason})
        return row

    def update_closing(
        self,
        pick_id: str,
        closing_line: float | None,
        closing_american_odds: int,
        *,
        closing_no_vig_probability: float | None = None,
        closing_consensus_probability: float | None = None,
        closing_consensus_line: float | None = None,
    ) -> dict[str, str]:
        self.initialize()
        closing_decimal = american_to_decimal(closing_american_odds)
        closing_probability = implied_probability(closing_american_odds)
        with self._lock():
            self._migrate_if_needed()
            rows = self._read_unlocked()
            row = self._find(rows, pick_id)
            if row["status"] != PickStatus.SETTLED.value:
                raise ValueError("closing data can be attached only after result settlement")
            proposed_line = "" if closing_line is None else str(closing_line)
            proposed_odds = str(closing_american_odds)
            if row["closing_american_odds"] and row["closing_american_odds"] != proposed_odds:
                raise ValueError("verified closing odds already exist with a different value")
            if row["closing_line"] and row["closing_line"] != proposed_line:
                raise ValueError("verified closing line already exists with a different value")
            decision_snapshot = {field: row[field] for field in DECISION_FIELDS}
            decision_probability = float(
                row["decision_raw_implied_probability"] or row["market_implied_probability"]
            )
            row.update(
                {
                    "closing_line": proposed_line,
                    "closing_american_odds": proposed_odds,
                    "closing_implied_probability": f"{closing_probability:.6f}",
                    "closing_decimal_odds": f"{closing_decimal:.6f}",
                    "closing_raw_implied_probability": f"{closing_probability:.6f}",
                    "closing_no_vig_probability": ""
                    if closing_no_vig_probability is None
                    else f"{closing_no_vig_probability:.6f}",
                    "closing_consensus_probability": ""
                    if closing_consensus_probability is None
                    else f"{closing_consensus_probability:.6f}",
                    "closing_consensus_line": ""
                    if closing_consensus_line is None
                    else str(closing_consensus_line),
                    "probability_clv": f"{closing_probability - decision_probability:.6f}",
                }
            )
            self._assert_decision_snapshot(row, decision_snapshot)
            self._write_rows(rows)
            updated = row.copy()
        self.audit.append(
            "closing_data_updated",
            pick_id,
            {
                "closing_line": closing_line,
                "closing_american_odds": closing_american_odds,
                "probability_clv": updated["probability_clv"],
            },
        )
        return updated

    def review_loss(
        self, pick_id: str, classification: str, cause: str, corrective_action: str
    ) -> dict[str, str]:
        self.initialize()
        if classification not in LOSS_CLASSIFICATIONS:
            raise ValueError(f"classification must be one of {sorted(LOSS_CLASSIFICATIONS)}")
        if not cause.strip() or not corrective_action.strip():
            raise ValueError("loss cause and corrective action are required")
        with self._lock():
            self._migrate_if_needed()
            rows = self._read_unlocked()
            row = self._find(rows, pick_id)
            if row["result"] != PickResult.LOSS.value:
                raise ValueError("only lost picks receive a loss review")
            if row["review_status"] == "complete":
                return row
            row.update(
                {
                    "review_status": "complete",
                    "loss_classification": classification,
                    "loss_cause": cause.strip(),
                    "corrective_action": corrective_action.strip(),
                }
            )
            self._write_rows(rows)
        self.audit.append(
            "loss_review_completed",
            pick_id,
            {"classification": classification, "cause": cause, "corrective_action": corrective_action},
        )
        return row

    def score_research(
        self,
        pick_ids: list[str],
        units: float,
        note: str = "retrospective fixed-stake research scoring",
    ) -> list[dict[str, str]]:
        self.initialize()
        if units <= 0:
            raise ValueError("research scoring units must be positive")
        if not pick_ids:
            raise ValueError("at least one pick id is required")
        scored_at = iso_utc(utc_now())
        updated: list[dict[str, str]] = []
        with self._lock():
            self._migrate_if_needed()
            rows = self._read_unlocked()
            for pick_id in pick_ids:
                row = self._find(rows, pick_id)
                if row["record_type"] != RecordType.RESEARCH_OBSERVATION.value:
                    raise ValueError(f"{pick_id} is not a research observation")
                if row["status"] != PickStatus.SETTLED.value:
                    raise ValueError(f"{pick_id} must be settled before research scoring")
                result = PickResult(row["result"])
                decimal_odds = float(row["decision_decimal_odds"] or row["decimal_odds"])
                research_pnl = profit_units(result, units, decimal_odds)
                row.update(
                    {
                        "research_score_units": f"{units:.4f}",
                        "research_pnl_units": f"{research_pnl:.6f}",
                        "research_scored_at_utc": scored_at,
                        "research_scoring_note": note,
                    }
                )
                updated.append(row.copy())
            self._write_rows(rows)
        for row in updated:
            self.audit.append(
                "research_score_updated",
                row["pick_id"],
                {
                    "research_score_units": row["research_score_units"],
                    "research_pnl_units": row["research_pnl_units"],
                    "note": note,
                },
            )
        return updated

    def report(
        self,
        filters: dict[str, str] | None = None,
        *,
        by_odds_range: bool = False,
    ) -> dict[str, object]:
        rows = self.rows(filters)
        qualified = [row for row in rows if row["record_type"] == RecordType.QUALIFIED_SHADOW_CALL.value]
        research = [row for row in rows if row["record_type"] == RecordType.RESEARCH_OBSERVATION.value]
        settled_qualified = [row for row in qualified if row["status"] == PickStatus.SETTLED.value]
        wagered = sum(float(row["units"] or 0) for row in settled_qualified)
        pnl = sum(float(row["pnl_units"] or 0) for row in settled_qualified)
        clv = [float(row["probability_clv"]) for row in settled_qualified if row["probability_clv"]]
        research_clv = [float(row["probability_clv"]) for row in research if row["probability_clv"]]
        scored_research = [row for row in research if row["research_score_units"]]
        research_staked = sum(float(row["research_score_units"]) for row in scored_research)
        research_pnl = sum(float(row["research_pnl_units"] or 0) for row in scored_research)
        calibration_rows = [
            row
            for row in rows
            if row["status"] == PickStatus.SETTLED.value and row["result"] in {"win", "loss"}
        ]
        calibration = calibration_metrics(
            [float(row["model_probability"]) for row in calibration_rows],
            [1 if row["result"] == "win" else 0 for row in calibration_rows],
        )
        output = {
            "records": len(rows),
            "qualified_shadow_calls": len(qualified),
            "research_observations": len(research),
            "no_calls": sum(row["decision"] == "NO_CALL" for row in rows),
            "no_call_team_banned_count": sum(row["reason_code"] == "NO_CALL_TEAM_BANNED" for row in rows),
            "open": sum(row["status"] == PickStatus.OPEN.value for row in rows),
            "settled_qualified": len(settled_qualified),
            "qualified_wins": sum(row["result"] == "win" for row in settled_qualified),
            "qualified_losses": sum(row["result"] == "loss" for row in settled_qualified),
            "qualified_pushes": sum(row["result"] == "push" for row in settled_qualified),
            "qualified_pnl_units": round(pnl, 4),
            "qualified_roi": round(pnl / wagered, 6) if wagered else None,
            "qualified_mean_raw_probability_clv": round(sum(clv) / len(clv), 6) if clv else None,
            "research_mean_raw_probability_clv": round(sum(research_clv) / len(research_clv), 6)
            if research_clv
            else None,
            "research_scored_records": len(scored_research),
            "research_staked_units": round(research_staked, 4),
            "research_pnl_units": round(research_pnl, 6),
            "research_roi": round(research_pnl / research_staked, 6) if research_staked else None,
            "research_wins": sum(row["result"] == "win" for row in scored_research),
            "research_losses": sum(row["result"] == "loss" for row in scored_research),
            "research_pushes": sum(row["result"] == "push" for row in scored_research),
            "calibration": calibration,
            "loss_reviews_required": sum(row["review_status"] == "review_required" for row in rows),
            "by_origin": self._counts(rows, "model_origin"),
            "by_record_type": self._counts(rows, "record_type"),
            "by_model_version": self._counts(rows, "model_version"),
            "by_calibration_version": self._counts(rows, "calibration_version"),
            "by_market_evaluation": {
                market.value: self._evaluation_metrics(
                    [row for row in rows if row["market_type"] == market.value]
                )
                for market in MarketType
            },
            "filters": filters or {},
            "clv_definition": "closing raw implied probability minus decision raw implied probability at the recorded selection; line movement reported separately",
        }
        if by_odds_range:
            output["by_odds_range"] = self._odds_range_metrics(rows)
        return output

    def _migrate_if_needed(self) -> str | None:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return None
        headers, rows = read_xlsx_rows(self.path)
        if headers == FIELDNAMES:
            return None
        if not set(headers).issubset(set(FIELDNAMES)):
            unknown = sorted(set(headers) - set(FIELDNAMES))
            raise ValueError(f"cannot migrate picks.xlsx with unknown fields: {unknown}")
        current_schema = next(
            (row.get("ledger_schema_version") for row in rows if row.get("ledger_schema_version")), "1"
        )
        backup = self._backup_path(str(current_schema))
        if not backup.exists():
            shutil.copy2(self.path, backup)
        migrated = self._migrated_rows(rows)
        self._write_rows(migrated)
        return str(current_schema)

    @staticmethod
    def _migrated_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
        migrated: list[dict[str, str]] = []
        for old in rows:
            row = {field: old.get(field, "") or "" for field in FIELDNAMES}
            forced = row["call_type"] == "forced_call"
            if not row["record_type"] and forced:
                row["legacy_units"] = row["units"]
                row["units"] = "0.00"
                row["record_type"] = RecordType.RESEARCH_OBSERVATION.value
                row["decision"] = "RESEARCH_OBSERVATION"
                row["reason_code"] = "NO_CALL_MODEL_UNVALIDATED"
                row["call_type"] = "research_observation"
            elif not row["record_type"]:
                row["record_type"] = RecordType.QUALIFIED_SHADOW_CALL.value
                row["decision"] = "CALL"
                row["reason_code"] = "QUALIFIED"
            row["ledger_schema_version"] = LEDGER_SCHEMA_VERSION
            version = row["model_version"].casefold()
            row["model_origin"] = row["model_origin"] or (
                ModelOrigin.SYNTHETIC_TEST.value
                if "test" in version or "smoke" in version
                else (
                    ModelOrigin.ANALYST_ESTIMATE.value
                    if "analyst" in version
                    else ModelOrigin.STATISTICAL_MODEL.value
                )
            )
            row["model_state_at_decision"] = row["model_state_at_decision"] or (
                "research" if forced else "shadow_qualified"
            )
            row["calibration_method"] = row["calibration_method"] or "identity"
            row["calibration_version"] = row["calibration_version"] or "identity-v1"
            row["feature_schema_version"] = row["feature_schema_version"] or "legacy-1"
            row["entity_map_version"] = row["entity_map_version"] or "legacy-unresolved"
            row["code_revision"] = row["code_revision"] or "legacy"
            row["original_away_team"] = row["away_team"]
            row["original_home_team"] = row["home_team"]
            row["decision_line"] = row["line"]
            row["decision_american_odds"] = row["american_odds"]
            row["decision_decimal_odds"] = row["decimal_odds"]
            row["decision_raw_implied_probability"] = row["market_implied_probability"]
            row["closing_decimal_odds"] = row["closing_decimal_odds"] or (
                f"{american_to_decimal(int(row['closing_american_odds'])):.6f}"
                if row["closing_american_odds"]
                else ""
            )
            row["closing_raw_implied_probability"] = row["closing_implied_probability"]
            migrated.append(row)
        return migrated

    def _write_rows(self, rows: list[dict[str, str]]) -> None:
        unknown = sorted({key for row in rows for key in row} - set(FIELDNAMES))
        if unknown:
            raise ValueError(f"cannot write picks.xlsx with unknown fields: {unknown}")
        write_xlsx_rows_atomic(self.path, FIELDNAMES, rows)

    def _read_unlocked(self) -> list[dict[str, str]]:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return []
        headers, rows = read_xlsx_rows(self.path)
        if headers != FIELDNAMES:
            raise ValueError("picks.xlsx schema does not match this code version")
        return rows

    def _set_legacy_units(self, pick_id: str, units: float) -> None:
        with self._lock():
            rows = self._read_unlocked()
            row = self._find(rows, pick_id)
            row["legacy_units"] = f"{units:.2f}"
            self._write_rows(rows)

    @staticmethod
    def _find(rows: list[dict[str, str]], pick_id: str) -> dict[str, str]:
        for row in rows:
            if row["pick_id"] == pick_id:
                return row
        raise KeyError(f"unknown pick id: {pick_id}")

    @staticmethod
    def _counts(rows: list[dict[str, str]], field: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for row in rows:
            key = row[field] or "unknown"
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    @staticmethod
    def _evaluation_metrics(rows: list[dict[str, str]]) -> dict[str, object]:
        binary = [
            row
            for row in rows
            if row["status"] == PickStatus.SETTLED.value and row["result"] in {"win", "loss"}
        ]
        if not binary:
            return {
                "count": 0,
                "win_rate": None,
                "model_brier": None,
                "market_brier": None,
                "flat_one_unit_pnl": 0.0,
                "mean_clv": None,
                "calibration": {"status": "insufficient_sample", "sample_size": 0},
            }
        outcomes = [1 if row["result"] == "win" else 0 for row in binary]
        model_probabilities = [float(row["model_probability"]) for row in binary]
        market_probabilities = [
            float(row["decision_raw_implied_probability"] or row["market_implied_probability"])
            for row in binary
        ]
        flat_pnl = sum(
            float(row["decision_decimal_odds"] or row["decimal_odds"]) - 1 if row["result"] == "win" else -1.0
            for row in binary
        )
        clv = [float(row["probability_clv"]) for row in binary if row["probability_clv"]]
        return {
            "count": len(binary),
            "win_rate": sum(outcomes) / len(outcomes),
            "model_brier": sum(
                (probability - outcome) ** 2
                for probability, outcome in zip(model_probabilities, outcomes, strict=True)
            )
            / len(binary),
            "market_brier": sum(
                (probability - outcome) ** 2
                for probability, outcome in zip(market_probabilities, outcomes, strict=True)
            )
            / len(binary),
            "flat_one_unit_pnl": round(flat_pnl, 6),
            "mean_clv": sum(clv) / len(clv) if clv else None,
            "calibration": calibration_metrics(model_probabilities, outcomes),
        }

    @classmethod
    def _odds_range_metrics(cls, rows: list[dict[str, str]]) -> list[dict[str, object]]:
        settled = [
            row
            for row in rows
            if row["status"] == PickStatus.SETTLED.value and row["result"] in {"win", "loss"}
        ]
        buckets = []
        for index in range(10):
            lower = index / 10
            upper = (index + 1) / 10
            members = []
            for row in settled:
                probability = float(
                    row["decision_raw_implied_probability"] or row["market_implied_probability"]
                )
                if lower <= probability < upper or (index == 9 and probability == 1.0):
                    members.append(row)
            buckets.append(
                {
                    "range": f"[{lower:.1f}-{upper:.1f}]",
                    "lower": lower,
                    "upper": upper,
                    **cls._evaluation_metrics(members),
                }
            )
        return buckets

    @staticmethod
    def _filter_rows(rows: list[dict[str, str]], filters: dict[str, str]) -> list[dict[str, str]]:
        aliases = {"origin": "model_origin", "market": "market_type"}
        normalized = {}
        for key, value in filters.items():
            field = aliases.get(key, key)
            if field == "record_type":
                value = {
                    "qualified": RecordType.QUALIFIED_SHADOW_CALL.value,
                    "research": RecordType.RESEARCH_OBSERVATION.value,
                }.get(value, value)
            normalized[field] = value
        return [row for row in rows if all(row.get(field) == value for field, value in normalized.items())]

    @staticmethod
    def _decision_audit_payload(row: dict[str, str]) -> dict[str, str]:
        return {field: row[field] for field in DECISION_FIELDS if field in row}

    @staticmethod
    def _assert_decision_snapshot(row: dict[str, str], snapshot: dict[str, str]) -> None:
        for field, value in snapshot.items():
            if row[field] != value:
                raise AssertionError(f"decision field changed during settlement: {field}")

    def _backup_path(self, version: str = "1") -> Path:
        return self.path.with_suffix(self.path.suffix + f".bak-v{version}")
