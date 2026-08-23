"""Tests for MLB v9 point-in-time feature extraction pipeline."""

from __future__ import annotations

from datetime import UTC, datetime

from model_prediction.features.mlb_v9_features import (
    MLBv9FeatureVector,
    extract_mlb_v9_features,
)


def test_mlb_v9_feature_vector_extraction() -> None:
    now = datetime(2026, 6, 1, 18, 0, tzinfo=UTC)
    vec = extract_mlb_v9_features(
        home_team="New York Yankees",
        away_team="Boston Red Sox",
        as_of=now,
        home_starter_name="Gerrit Cole",
        away_starter_name="Brayan Bello",
        home_starter_throws="R",
        away_starter_throws="R",
    )

    assert isinstance(vec, MLBv9FeatureVector)
    d = vec.to_dict()
    assert "starter_k_pct_gap" in d
    assert "projected_woba_gap" in d
    assert "bullpen_fip_advantage" in d
    assert "platoon_woba_advantage" in d
    assert "park_factor" in d
    assert len(d) == 20
