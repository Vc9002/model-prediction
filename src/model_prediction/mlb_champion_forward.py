"""Bind existing MLB structural champions to real, already-matched contracts.

Market identities and lines come from the paired slate. Probabilities are
recomputed by the registered champions using an observed pregame snapshot.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from .domain import MarketType, parse_utc
from .features.mlb_v10_features import MLBv10FeatureExtractor
from .mlb_v10_replay import build_snapshot
from .models.mlb_runline_v4 import MLBStructuralRunlineV4Model
from .production_registry import ProductionModelRegistry


def champion_candidates(candidates, data_root: Path, root: Path, observed):
    if not candidates:
        return [], []
    registry = ProductionModelRegistry.load(root)
    total = registry.champion("MLB", "total")
    spread = registry.champion("MLB", "spread")
    if not total or not total.available or total.model_id != "mlb-structural-v10-frozen":
        raise ValueError("MLB structural total champion unavailable")
    if not spread or not spread.available or spread.model_id != "mlb-structural-runline-v4":
        raise ValueError("MLB structural spread champion unavailable")
    artifact = json.loads((root / total.artifact).read_text(encoding="utf-8"))
    source = data_root / "mlb_statsapi/game_snapshots.jsonl"
    snapshots = []
    if source.exists():
        with source.open(encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                snapshot = json.loads(line)
                when = parse_utc(snapshot["observed_at_utc"])
                start = parse_utc(snapshot["game_start_utc"])
                if when <= observed < start and snapshot.get("snapshot_type") == "forward_pregame":
                    snapshots.append(snapshot)
    extractor = MLBv10FeatureExtractor(snapshot_path=source)
    runline = MLBStructuralRunlineV4Model()
    converted, skipped = [], []
    for candidate in candidates:
        if parse_utc(candidate.event_start_utc) <= observed:
            skipped.append({"event_id": candidate.event_id, "reason": "event_started"})
            continue
        matches = [
            s
            for s in snapshots
            if s.get("home", {}).get("team_name") == candidate.home_team
            and s.get("away", {}).get("team_name") == candidate.away_team
            and parse_utc(s["game_start_utc"]) == parse_utc(candidate.event_start_utc)
        ]
        if not matches:
            skipped.append(
                {"event_id": candidate.event_id, "reason": "structural_pregame_snapshot_unavailable"}
            )
            continue
        snapshot = max(matches, key=lambda s: s["observed_at_utc"])
        features = extractor.extract_features_for_matchup(
            candidate.event_id,
            candidate.home_team,
            candidate.away_team,
            candidate.event_start_utc,
            observed,
            snapshot=snapshot,
        )
        # Replay uses the frozen artifact's exact existing inference and
        # empirical residual mapping; no new coefficients or calibration.
        replay = build_snapshot(artifact, features, float(candidate.line), observed.isoformat())
        if candidate.market_type == MarketType.TOTAL:
            probability = replay["probabilities"][candidate.selection]
            entry = total
        else:
            if candidate.line not in (-1.5, 1.5):
                skipped.append({"event_id": candidate.event_id, "reason": "unsupported_structural_runline"})
                continue
            pred = replay["structural_prediction"]
            cover = runline.forecast_runline(
                candidate.home_team,
                candidate.away_team,
                pred["projected_home_runs"],
                pred["projected_away_runs"],
            )
            field = f"prob_{candidate.selection}_{'minus' if candidate.line < 0 else 'plus'}_1_5"
            probability = getattr(cover, field)
            entry = spread
            replay = {
                "model_version": spread.model_id,
                "structural_input": replay,
                "line": candidate.line,
                "selection": candidate.selection,
                "probability": probability,
            }
        converted.append(
            replace(
                candidate,
                raw_probability=probability,
                shrunk_probability=probability,
                model_version=entry.model_id,
                model_name=entry.model_id,
                model_artifact_hash=entry.artifact_hash or "",
                calibration_version=entry.model_id,
                model_input_snapshot=replay,
                rationale=f"{entry.model_id}: frozen structural pregame inference at the exact quoted line.",
            )
        )
    return converted, skipped
