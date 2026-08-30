from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .audit import AuditLog
from .domain import parse_utc
from .esports import (
    _fuzzy_match_team,
    _identity_key,
    _load_manual_aliases,
    _team_alias_index,
)
from .ledger import PickLedger
from .research_ledgers import (
    RESEARCH_LEDGER_SPORTS,
    research_ledger,
)

ESPORTS_RESEARCH_SPORTS = frozenset({"lol", "cs2", "dota2", "valorant"})


@dataclass(frozen=True)
class CleanupPlan:
    research_rows: dict[str, list[dict[str, str]]]
    gated_rows: dict[str, list[dict[str, str]]]
    rejected_research: dict[str, tuple[str, ...]]
    rejected_gated: dict[str, tuple[str, ...]]
    duplicate_research: dict[str, str]
    duplicate_gated: dict[str, str]

    def report(self) -> dict[str, Any]:
        return {
            "research_kept": {sport: len(rows) for sport, rows in self.research_rows.items()},
            "gated_kept": {sport: len(rows) for sport, rows in self.gated_rows.items()},
            "research_rejected": len(self.rejected_research),
            "gated_rejected": len(self.rejected_gated),
            "research_rejection_reasons": dict(
                sorted(
                    Counter(
                        reason for reasons in self.rejected_research.values() for reason in reasons
                    ).items()
                )
            ),
            "gated_rejection_reasons": dict(
                sorted(
                    Counter(reason for reasons in self.rejected_gated.values() for reason in reasons).items()
                )
            ),
            "research_duplicates_removed": len(self.duplicate_research),
            "gated_duplicates_removed": len(self.duplicate_gated),
        }


def _decision_key(row: dict[str, str]) -> tuple[str, ...]:
    return (
        row.get("league", "").upper(),
        row.get("event_id", ""),
        row.get("market_type", ""),
        row.get("selection", ""),
        row.get("line", ""),
        row.get("model_version", ""),
    )


def _sport_for_row(row: dict[str, str]) -> str | None:
    sport = row.get("league", "").casefold()
    return sport if sport in RESEARCH_LEDGER_SPORTS else None


def _float(row: dict[str, str], field: str) -> float | None:
    try:
        return float(row.get(field, ""))
    except (TypeError, ValueError):
        return None


def _esports_inputs_valid(
    data_root: Path,
    artifact_dir: Path,
    sport: str,
    row: dict[str, str],
) -> tuple[bool, str | None]:
    artifact_path = artifact_dir / f"{row.get('model_version', '')}.json"
    teams_path = data_root / "esports" / sport / "teams.json"
    if not artifact_path.exists() or not teams_path.exists():
        return False, "missing_exact_artifact_or_team_catalog"
    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
        teams = json.loads(teams_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "unreadable_exact_artifact_or_team_catalog"
    if str(artifact.get("model_version") or "") != row.get("model_version"):
        return False, "model_version_mismatch"
    if str(artifact.get("artifact_hash") or "") != row.get("model_artifact_hash"):
        return False, "model_artifact_hash_mismatch"
    try:
        if parse_utc(str(artifact["trained_through_utc"])) >= parse_utc(row["event_start_utc"]):
            return False, "artifact_not_point_in_time"
    except (KeyError, ValueError):
        return False, "invalid_artifact_cutoff"

    aliases = _team_alias_index(teams)
    manual_aliases = _load_manual_aliases(data_root).get(sport, {})
    ratings = artifact.get("ratings") or {}
    team_ids: list[str] = []
    names = (
        row.get("original_away_team") or row.get("away_team") or "",
        row.get("original_home_team") or row.get("home_team") or "",
    )
    for name in names:
        key = _identity_key(name)
        matches = aliases.get(key, set())
        if not matches:
            manual_name = manual_aliases.get(key)
            if manual_name:
                matches = aliases.get(_identity_key(manual_name), set())
        if not matches:
            fuzzy = _fuzzy_match_team(name, teams, aliases)
            matches = {fuzzy} if fuzzy else set()
        if len(matches) != 1:
            return False, "team_identity_unresolved"
        team_id = next(iter(matches))
        if team_id not in ratings:
            return False, "team_untrained_in_exact_artifact"
        team_ids.append(team_id)
    if len(set(team_ids)) != 2:
        return False, "teams_resolve_to_same_identity"
    return True, None


def _row_rejection_reasons(
    row: dict[str, str],
    *,
    config: dict[str, Any],
    data_root: Path,
    artifact_dir: Path,
    gated: bool,
) -> tuple[str, ...]:
    reasons: list[str] = []
    sport = _sport_for_row(row)
    if sport is None:
        return ("unsupported_research_sport",)

    model_probability = _float(row, "model_probability")
    market_probability = _float(row, "decision_raw_implied_probability")
    edge = _float(row, "edge")
    units = _float(row, "units")
    if model_probability is None or not 0 < model_probability < 1:
        reasons.append("invalid_model_probability")
    if market_probability is None or not 0 < market_probability < 1:
        reasons.append("invalid_executable_probability")
    if edge is None:
        reasons.append("missing_edge")
    elif (
        model_probability is not None
        and market_probability is not None
        and abs(edge - (model_probability - market_probability)) > 0.002
    ):
        reasons.append("edge_probability_mismatch")
    if units is None or units < 0:
        reasons.append("invalid_units")

    try:
        observed = parse_utc(row.get("observed_at_utc", ""))
        event_start = parse_utc(row.get("event_start_utc", ""))
        if observed >= event_start:
            reasons.append("decision_not_pregame")
    except ValueError:
        reasons.append("invalid_decision_timestamp")

    required_provenance = (
        "model_version",
        "model_artifact_hash",
        "calibration_artifact_hash",
        "code_revision",
        "observed_at_utc",
    )
    if any(not row.get(field) for field in required_provenance):
        reasons.append("missing_provenance")
    if row.get("sportsbook") != "polymarket_us":
        reasons.append("not_executable_polymarket_quote")

    if sport in ESPORTS_RESEARCH_SPORTS:
        inputs_valid, input_reason = _esports_inputs_valid(
            data_root,
            artifact_dir,
            sport,
            row,
        )
        if not inputs_valid:
            reasons.append(input_reason or "model_inputs_invalid")

    decision = row.get("decision")
    record_type = row.get("record_type")
    if gated:
        if decision != "CALL":
            reasons.append("gated_row_not_call")
        if record_type not in {"QUALIFIED_SHADOW_CALL", "RESEARCH_OBSERVATION"}:
            reasons.append("gated_row_invalid_record_type")
        if units is not None and units <= 0:
            reasons.append("gated_row_nonpositive_units")
    elif decision == "CALL":
        if record_type not in {"QUALIFIED_SHADOW_CALL", "RESEARCH_OBSERVATION"}:
            reasons.append("call_invalid_record_type")
        if units is not None and units <= 0:
            reasons.append("call_nonpositive_units")
    elif decision == "NO_CALL":
        if record_type != "RESEARCH_OBSERVATION":
            reasons.append("no_call_not_research_record")
        if units is not None and units != 0:
            reasons.append("no_call_has_units")
    else:
        reasons.append("invalid_decision")

    if (decision == "CALL" or gated) and record_type == "QUALIFIED_SHADOW_CALL":
        model_config = config.get("models", {}).get(sport.upper(), {})
        minimum_edge = float(model_config.get("min_edge", 0.0))
        minimum_confidence = float(model_config.get("research_confidence_gate", 0.0))
        if edge is None or edge < minimum_edge:
            reasons.append("below_configured_edge_gate")
        if model_probability is None or abs(model_probability - 0.5) < minimum_confidence:
            reasons.append("below_configured_confidence_gate")

    return tuple(dict.fromkeys(reasons))


def _deduplicate(
    rows: list[dict[str, str]],
) -> tuple[list[dict[str, str]], dict[str, str]]:
    kept_by_key: dict[tuple[str, ...], dict[str, str]] = {}
    duplicates: dict[str, str] = {}
    for row in sorted(
        rows,
        key=lambda item: (
            item.get("observed_at_utc", ""),
            item.get("created_at_utc", ""),
            item.get("pick_id", ""),
        ),
    ):
        key = _decision_key(row)
        kept = kept_by_key.get(key)
        if kept is None:
            kept_by_key[key] = row
        else:
            duplicates[row["pick_id"]] = kept["pick_id"]
    return list(kept_by_key.values()), duplicates


def build_cleanup_plan(
    data_root: str | Path,
    artifact_dir: str | Path,
    config: dict[str, Any],
) -> CleanupPlan:
    root = Path(data_root)
    artifact_root = Path(artifact_dir)
    legacy_research = PickLedger(
        root / "research.xlsx",
        audit_path=root / "events.jsonl",
    )
    legacy_gated = PickLedger(
        root / "gated_research.xlsx",
        audit_path=root / "events.jsonl",
    )
    research_rows = legacy_research.rows() if legacy_research.path.exists() else []
    gated_rows = legacy_gated.rows() if legacy_gated.path.exists() else []

    valid_research: dict[str, list[dict[str, str]]] = {sport: [] for sport in RESEARCH_LEDGER_SPORTS}
    rejected_research: dict[str, tuple[str, ...]] = {}
    for row in research_rows:
        reasons = _row_rejection_reasons(
            row,
            config=config,
            data_root=root,
            artifact_dir=artifact_root,
            gated=False,
        )
        sport = _sport_for_row(row)
        if reasons or sport is None:
            rejected_research[row["pick_id"]] = reasons or ("unsupported_research_sport",)
        else:
            valid_research[sport].append(row)

    duplicate_research: dict[str, str] = {}
    for sport, rows in valid_research.items():
        valid_research[sport], duplicates = _deduplicate(rows)
        duplicate_research.update(duplicates)

    valid_research_keys = {_decision_key(row) for rows in valid_research.values() for row in rows}
    valid_gated: dict[str, list[dict[str, str]]] = {sport: [] for sport in RESEARCH_LEDGER_SPORTS}
    rejected_gated: dict[str, tuple[str, ...]] = {}
    for row in gated_rows:
        gated_reasons = list(
            _row_rejection_reasons(
                row,
                config=config,
                data_root=root,
                artifact_dir=artifact_root,
                gated=True,
            )
        )
        if _decision_key(row) not in valid_research_keys:
            gated_reasons.append("missing_valid_research_pair")
        sport = _sport_for_row(row)
        if gated_reasons or sport is None:
            rejected_gated[row["pick_id"]] = tuple(dict.fromkeys(gated_reasons)) or (
                "unsupported_research_sport",
            )
        else:
            valid_gated[sport].append(row)

    duplicate_gated: dict[str, str] = {}
    for sport, rows in valid_gated.items():
        valid_gated[sport], duplicates = _deduplicate(rows)
        duplicate_gated.update(duplicates)

    return CleanupPlan(
        research_rows=valid_research,
        gated_rows=valid_gated,
        rejected_research=rejected_research,
        rejected_gated=rejected_gated,
        duplicate_research=duplicate_research,
        duplicate_gated=duplicate_gated,
    )


def apply_cleanup_plan(
    data_root: str | Path,
    plan: CleanupPlan,
) -> dict[str, Any]:
    root = Path(data_root)
    for sport in RESEARCH_LEDGER_SPORTS:
        destination = research_ledger(root, sport)
        destination.initialize()
        destination.import_rows(
            plan.research_rows[sport],
            source="data/research.xlsx",
            reason="clean split of legacy mixed research ledger by sport",
        )
        gated_destination = research_ledger(root, sport, gated=True)
        gated_destination.initialize()
        gated_destination.import_rows(
            plan.gated_rows[sport],
            source="data/gated_research.xlsx",
            reason="clean split of legacy mixed gated-research ledger by sport",
        )

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    archive = root / "archive" / f"research-ledger-split-{timestamp}"
    archive.mkdir(parents=True, exist_ok=False)
    archived: dict[str, str] = {}
    digests: dict[str, str] = {}
    for name in ("research.xlsx", "gated_research.xlsx"):
        source = root / name
        if not source.exists():
            continue
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        dest_path = archive / name
        source.replace(dest_path)
        archived[name] = str(dest_path)
        digests[name] = digest

    report = {
        **plan.report(),
        "archive": str(archive),
        "archived_legacy_ledgers": archived,
        "archived_sha256": digests,
    }
    (archive / "cleanup-report.json").write_text(
        json.dumps(
            {
                **report,
                "rejected_research": plan.rejected_research,
                "rejected_gated": plan.rejected_gated,
                "duplicate_research": plan.duplicate_research,
                "duplicate_gated": plan.duplicate_gated,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    AuditLog(root / "events.jsonl").append(
        "research_ledgers_split_cleaned",
        str(archive),
        report,
    )
    return report
