"""Capture the NBA v9/v7 spread and total serving outputs (quick-261123-h40 bit-identity pin).

Used two ways: ``tests/test_nba_total_v92.py`` compares the live outputs against
``tests/fixtures/nba_v92/serving_golden_pre_v92.json``, and the fixture itself was written by
running this file as a script against an untouched export of commit daeccd3e (the parent of the
v9.2 work), so the pin is the pre-change behaviour, not a self-comparison:

  git archive daeccd3e src tests config | tar -x -C <dir>
  PYTHONPATH=<dir>/src python <dir>/tests/nba_v92_golden_capture.py <out.json>   # from <dir>/tests

(the helper was copied into the export; it imports only fixtures that exist at daeccd3e).
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_nba_serving_plumbing as P

from model_prediction import calibration_pipeline
from model_prediction.cli import forecast as forecast_cli
from model_prediction.features import nba_player_availability
from model_prediction.models.basketball import UpcomingGame

SLATES = (
    ("total", "nba-total-margin-v9"),
    ("total", "nba-total-margin-v7"),
    ("spread", "nba-spread-margin-v9"),
    ("spread", "nba-spread-margin-v7"),
)
# File-byte hashes: they move with line endings and with any edit to a hashed engine file, which
# is not a behaviour change of the v9/v7 call. Everything else must match exactly.
VOLATILE_KEYS = frozenset({"engine_source_sha256", "snapshot_hash"})


def _json(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def run_slate(market: str, model_id: str, *, evidence_status: str = "predictively_qualified") -> dict:
    """One slate run on the 2026-11-19 LAC @ SA fixture with availability inputs switched on."""
    events = [P._event("401", P.CLIPPERS, P.SPURS)]
    rows = [P._spread_row("Los Angeles Clippers")] if market == "spread" else [P._total_row()]
    champion = SimpleNamespace(model_id=model_id, available=True, evidence_status=evidence_status)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(forecast_cli, "utc_now", lambda: P.NOW)
        mp.setattr(
            forecast_cli.ProductionModelRegistry,
            "load",
            lambda root: SimpleNamespace(champion=lambda *a: champion),
        )
        mp.setattr(forecast_cli.FeatureStore, "games_before", lambda *a: list(P.HISTORY))
        mp.setattr(forecast_cli, "_read_polymarket_snapshot_rows", lambda path: [dict(r) for r in rows])
        mp.setattr(
            nba_player_availability,
            "compute_nba_matchup_availability",
            lambda *a, **k: dict(P.AVAILABILITY),
        )
        mp.setattr(
            calibration_pipeline, "load_serving_calibrator", lambda *a, **k: (None, {"status": "test"})
        )
        slate = (
            forecast_cli._forecast_nba_spread_slate
            if market == "spread"
            else forecast_cli._forecast_nba_total_slate
        )
        with tempfile.TemporaryDirectory() as tmp:
            return _json(slate(Path(tmp), "2026-11-19", P._client(events)))


def run_model(market: str, model_id: str) -> list[dict]:
    """The engine's own output for the fixture game (pins v7 spread, whose slate snapshot fails)."""
    from model_prediction.models.nba_v7 import NBASpreadModelV7, NBATotalModelV7
    from model_prediction.models.nba_v9 import NBASpreadModelV9, NBATotalModelV9

    cls = {
        "nba-total-margin-v9": NBATotalModelV9,
        "nba-total-margin-v7": NBATotalModelV7,
        "nba-spread-margin-v9": NBASpreadModelV9,
        "nba-spread-margin-v7": NBASpreadModelV7,
    }[model_id]
    artifact, error = forecast_cli._load_exact_artifact_contract(model_id)
    if artifact is None:
        raise ValueError(error)
    model = cls.from_artifact(artifact)
    line = {"spread_away_line": 2.5} if market == "spread" else {"total_line": 197.5}
    game = UpcomingGame(
        event_id="401",
        event_start_utc=P.TIP_ESPN,
        away_team=P.CLIPPERS,
        home_team=P.SPURS,
        availability_points_gap=P.AVAILABILITY["availability_points_gap"],
        availability_total_loss=P.AVAILABILITY["availability_total_loss"],
        league="NBA",
        **line,
    )
    predict = model.predict_spreads if market == "spread" else model.predict_totals
    return [
        _json(
            {
                "market_type": p.market_type,
                "model_version": p.model_version,
                "probabilities": p.probabilities,
                "inference_inputs": p.inference_inputs,
                "feature_basis": p.feature_basis,
                "rationale": p.rationale,
            }
        )
        for p in predict(list(P.HISTORY), [game])
    ]


def capture() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for market, model_id in SLATES:
        entry: dict[str, Any] = {"model": run_model(market, model_id)}
        try:
            entry["slate"] = run_slate(market, model_id)
        except Exception as exc:  # noqa: BLE001 -- a pre-existing slate failure is pinned as-is
            entry["slate_error"] = f"{type(exc).__name__}: {exc}"
        out[model_id] = entry
    return out


def strip_volatile(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: strip_volatile(v) for k, v in value.items() if k not in VOLATILE_KEYS}
    if isinstance(value, list):
        return [strip_volatile(v) for v in value]
    return value


if __name__ == "__main__":
    Path(sys.argv[1]).write_text(
        json.dumps(capture(), indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
