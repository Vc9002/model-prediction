import json
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from model_prediction.cli.forecast import _append_secondary_ledger
from model_prediction.domain import MarketType
from model_prediction.forward import MLBForwardCandidate
from model_prediction.mlb_champion_forward import champion_candidates
from tests.test_mlb_structural_v10 import _mock_feature_vector


@pytest.mark.parametrize(
    "market,line,selection,version",
    [
        (MarketType.TOTAL, 8.5, "over", "mlb-structural-v10-frozen"),
        (MarketType.SPREAD, -1.5, "home", "mlb-structural-runline-v4"),
    ],
)
def test_exact_champion_consumes_observed_snapshot(tmp_path, monkeypatch, market, line, selection, version):
    now = datetime(2026, 10, 20, 12, tzinfo=UTC)
    start = (now + timedelta(hours=2)).isoformat()
    values = {field.name: "" for field in fields(MLBForwardCandidate)}
    values.update(
        event_id="fixture",
        event_start_utc=start,
        home_team="Home",
        away_team="Away",
        market_type=market,
        line=line,
        selection=selection,
        shrunk_probability=0.99,
        raw_probability=0.99,
        uncertainty=0.05,
        no_vig_probability=0.5,
    )
    candidate = MLBForwardCandidate(**values)
    vector = replace(
        _mock_feature_vector(), event_id="fixture", home_team="Home", away_team="Away", game_start_utc=start
    )
    monkeypatch.setattr(
        "model_prediction.mlb_champion_forward.MLBv10FeatureExtractor.extract_features_for_matchup",
        lambda *a, **k: vector,
    )
    source = tmp_path / "mlb_statsapi/game_snapshots.jsonl"
    source.parent.mkdir()
    row = {
        "home": {"team_name": "Home"},
        "away": {"team_name": "Away"},
        "game_start_utc": start,
        "observed_at_utc": now.isoformat(),
        "snapshot_type": "forward_pregame",
    }
    source.write_text(json.dumps(row) + "\n")
    result, skipped = champion_candidates([candidate], tmp_path, Path(__file__).parents[1], now)
    assert not skipped
    assert result[0].model_version == version
    assert result[0].model_input_snapshot
    assert result[0].shrunk_probability != 0.99
    # A snapshot observed later must never enter today's decision.
    row["observed_at_utc"] = (now + timedelta(minutes=1)).isoformat()
    source.write_text(json.dumps(row) + "\n")
    result, skipped = champion_candidates([candidate], tmp_path, Path(__file__).parents[1], now)
    assert result == []
    assert skipped[0]["reason"] == "structural_pregame_snapshot_unavailable"


def test_ledger_rejection_is_never_counted_as_success():
    class RejectedLedger:
        def append_evaluated(self, *args, **kwargs):
            raise ValueError("unsupported sport")

    with pytest.raises(ValueError, match="unsupported sport"):
        _append_secondary_ledger(RejectedLedger(), None, None, datetime.now(UTC), "fixture")
