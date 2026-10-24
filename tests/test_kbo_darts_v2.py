import hashlib
import json
from pathlib import Path

import pytest

from model_prediction.models.darts import (
    DARTS_V2_MODEL_VERSION,
    DartsModel,
    darts_v2_model,
)
from model_prediction.models.international_baseball_v3 import (
    InternationalBaseballV3Model,
    kbo_v3_model,
)


def test_kbo_v3_artifact_hash_verified():
    path = Path("config/models/kbo-baseball-v3.json")
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    embedded_hash = payload["artifact_hash"]
    to_hash = {k: v for k, v in payload.items() if k != "artifact_hash"}
    recomputed = hashlib.sha256(
        json.dumps(to_hash, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    assert embedded_hash == recomputed
    assert payload["model_version"] == "kbo-baseball-v3"
    assert payload["status"] == "production"
    assert payload["qualified_for_betting"] is True


def test_darts_v2_artifact_hash_verified():
    path = Path("config/models/darts-markov-v2.json")
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    embedded_hash = payload["artifact_hash"]
    to_hash = {k: v for k, v in payload.items() if k != "artifact_hash"}
    recomputed = hashlib.sha256(
        json.dumps(to_hash, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    assert embedded_hash == recomputed
    assert payload["model_version"] == "darts-markov-v2"
    assert payload["status"] == "production"
    assert payload["qualified_for_betting"] is True


def test_darts_v2_dynamic_sizing():
    assert DartsModel.compute_dynamic_units(0.01) == 1.00
    assert DartsModel.compute_dynamic_units(0.02) == 1.00
    assert DartsModel.compute_dynamic_units(0.05) == pytest.approx(1.50, abs=0.01)
    assert DartsModel.compute_dynamic_units(0.08) == 2.00
    assert DartsModel.compute_dynamic_units(0.12) == 2.00


def test_kbo_v3_dynamic_sizing():
    assert InternationalBaseballV3Model.compute_dynamic_units(0.01) == 1.00
    assert InternationalBaseballV3Model.compute_dynamic_units(0.02) == 1.00
    assert InternationalBaseballV3Model.compute_dynamic_units(0.05) == pytest.approx(1.50, abs=0.01)
    assert InternationalBaseballV3Model.compute_dynamic_units(0.08) == 2.00
    assert InternationalBaseballV3Model.compute_dynamic_units(0.12) == 2.00


def test_darts_v2_unrated_player_shrinkage():
    model_v2 = darts_v2_model()
    assert model_v2.version == DARTS_V2_MODEL_VERSION

    # With 0 sample matches, raw probability is 0.50 and stays 0.50
    pred_equal = model_v2.predict_match("Unrated A", "Unrated B", best_of_legs=11)
    assert pred_equal["moneyline"]["a"] == pytest.approx(0.50)

    # If player A has a high rating but 0 sample matches (e.g. manually set or seed),
    # v2 shrinks win probability toward 0.50
    model_v2.ratings["Player High"] = 1750.0
    model_v2.ratings["Player Low"] = 1400.0
    # 0 matches
    pred_shrunk = model_v2.predict_match("Player High", "Player Low", best_of_legs=11)
    # With 0 sample matches, weight = 0 / 3 = 0, so probability is fully shrunk to 0.50
    assert pred_shrunk["moneyline"]["a"] == pytest.approx(0.50)

    # Now add 3 matches
    model_v2.match_counts["Player High"] = 3
    model_v2.match_counts["Player Low"] = 3
    pred_full = model_v2.predict_match("Player High", "Player Low", best_of_legs=11)
    # Now it reflects the full rating difference
    assert pred_full["moneyline"]["a"] > 0.65


def test_kbo_v3_bivariate_poisson():
    model = kbo_v3_model()
    assert model.league == "KBO"
    forecast = model.forecast_match(
        home_team="LG Twins",
        away_team="Doosan Bears",
        lambda_home=5.2,
        lambda_away=4.1,
    )
    assert forecast.prob_home_win > forecast.prob_away_win
    assert 0.01 < forecast.prob_draw < 0.06
    assert (
        pytest.approx(forecast.prob_home_win + forecast.prob_away_win + forecast.prob_draw, abs=1e-3) == 1.0
    )
