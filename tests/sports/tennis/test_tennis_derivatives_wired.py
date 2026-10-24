from pathlib import Path

import yaml

from model_prediction.production_registry import ProductionModelRegistry
from model_prediction.sports.tennis.moneyline import TENNIS_V5_VERSION
from model_prediction.sports.tennis.spread import TENNIS_GAMES_V3_VERSION as SPREAD_V3
from model_prediction.sports.tennis.spread import tennis_games_model as spread_games_model
from model_prediction.sports.tennis.total import TENNIS_GAMES_V3_VERSION as TOTAL_V3
from model_prediction.sports.tennis.total import tennis_games_model as total_games_model

REPO = Path(__file__).resolve().parents[3]


def test_tennis_serving_models_wired_end_to_end():
    registry = ProductionModelRegistry.load(REPO)

    # 1. Moneyline champion wired to v5
    champ_ml = registry.champion("TENNIS", "moneyline")
    assert champ_ml is not None
    assert champ_ml.model_id == TENNIS_V5_VERSION
    assert champ_ml.model_id == "tennis-surface-elo-v5"

    # 2. Spread champion wired to v3
    champ_spread = registry.champion("TENNIS", "spread")
    assert champ_spread is not None
    assert champ_spread.model_id == SPREAD_V3
    assert champ_spread.model_id == "tennis-games-markov-v3"
    assert champ_spread.rollback_model == "tennis-games-markov-v2"

    # 3. Total champion wired to v3
    champ_total = registry.champion("TENNIS", "total")
    assert champ_total is not None
    assert champ_total.model_id == TOTAL_V3
    assert champ_total.model_id == "tennis-games-markov-v3"
    assert champ_total.rollback_model == "tennis-games-markov-v2"

    # 4. Model entry points instantiate cleanly
    m_spread = spread_games_model()
    assert m_spread.version == "tennis-games-markov-v3"

    m_total = total_games_model()
    assert m_total.version == "tennis-games-markov-v3"

    # 5. config/model.yaml has all 3 markets enabled for TENNIS
    model_yaml_path = REPO / "config" / "model.yaml"
    with open(model_yaml_path, encoding="utf-8") as f:
        model_cfg = yaml.safe_load(f)
    tennis_cfg = model_cfg["models"]["TENNIS"]
    assert set(tennis_cfg["markets"]) == {"moneyline", "spread", "total"}
