"""Score frozen NFL v3 shadow forecasts after explicit final scoreboard capture."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from model_prediction.domain import parse_utc
from model_prediction.models.nfl_joint_market_v3 import forecast_packet
from model_prediction.models.nfl_market_anchor_v1 import SettledQuote
from model_prediction.production_registry import compute_artifact_hash
from scripts.forecast_nfl_joint_market_v3 import ARTIFACT, ROOT, load_verified_packet


def _scores(probability: float, outcome: int) -> tuple[float, float]:
    if not math.isfinite(probability) or not 0 < probability < 1:
        raise ValueError("invalid_probability")
    return (probability - outcome) ** 2, -math.log(probability if outcome else 1 - probability)


def score_forecast(forecast: dict, packet: dict, final: dict) -> dict[str, dict]:
    if final.get("status") != "completed" or not final.get("raw_hash"):
        raise ValueError("event_not_completed")
    if final.get("event_id") != forecast.get("event_id") or packet.get("event_id") != forecast.get("event_id"):
        raise ValueError("event_identity_mismatch")
    home = int(final["home_score"])
    away = int(final["away_score"])
    if min(home, away) < 0:
        raise ValueError("invalid_final_score")
    result = {}
    for market in ("moneyline", "spread", "total"):
        contract = forecast["contracts"][market]
        selection = contract["selection"]
        if market == "moneyline":
            if selection not in ("home", "away"):
                raise ValueError("invalid_moneyline_selection")
            selected_margin = home - away if selection == "home" else away - home
        elif market == "spread":
            if selection not in ("home", "away"):
                raise ValueError("invalid_spread_selection")
            selected_margin = (home - away if selection == "home" else away - home) + float(contract["line"])
        else:
            if selection not in ("over", "under"):
                raise ValueError("invalid_total_selection")
            selected_margin = (home + away - float(contract["line"])) * (1 if selection == "over" else -1)
        if selected_margin == 0:
            result[market] = {"status": "push_excluded"}
            continue
        outcome = int(selected_margin > 0)
        quote = packet["markets"][market]["quote"]
        if market == "moneyline":
            market_fair = float(quote["no_vig_selected"])
        else:
            asks = float(quote["long_ask"]) + float(quote["short_ask"])
            if asks <= 0:
                raise ValueError("invalid_two_sided_quote")
            market_fair = float(contract["ask"]) / asks
        incumbent = float(packet["markets"][market]["model_probability"])
        challenger = float(forecast["selected_probabilities"][market])
        inc_brier, inc_logloss = _scores(incumbent, outcome)
        challenger_brier, challenger_logloss = _scores(challenger, outcome)
        market_brier, market_logloss = _scores(market_fair, outcome)
        result[market] = {
            "status": "scored",
            "outcome": outcome,
            "incumbent_brier": inc_brier,
            "challenger_brier": challenger_brier,
            "market_brier": market_brier,
            "incumbent_log_loss": inc_logloss,
            "challenger_log_loss": challenger_logloss,
            "market_log_loss": market_logloss,
            "market_no_vig_probability": market_fair,
        }
    return result


def evaluate(shadow_path: Path, games_path: Path) -> dict:
    forecast = json.loads(shadow_path.read_text(encoding="utf-8"))
    shadow_body = {key: value for key, value in forecast.items() if key != "shadow_sha256"}
    shadow_hash = hashlib.sha256(
        json.dumps(shadow_body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if forecast.get("shadow_sha256") != shadow_hash:
        raise ValueError("shadow_forecast_hash_mismatch")
    packet, digest = load_verified_packet(Path(forecast["source_packet_path"]))
    artifact = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    if forecast["artifact_hash"] != artifact["artifact_hash"] or artifact["artifact_hash"] != compute_artifact_hash(artifact):
        raise ValueError("shadow_artifact_mismatch")
    for relative_path, expected_hash in artifact["source_sha256"].items():
        if hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest() != expected_hash:
            raise ValueError(f"v3_source_hash_mismatch:{relative_path}")
    if forecast["packet_sha256"] != digest or forecast["decision_at_utc"] != packet["captured_at_utc"]:
        raise ValueError("shadow_packet_mismatch")
    history = [
        SettledQuote(
            date.fromisoformat(item["event_date"]),
            float(item["model_probability"]),
            float(item["market_probability"]),
            int(item["outcome"]),
            parse_utc(item["settled_at_utc"]),
        )
        for item in forecast["training_evidence"]
    ]
    decision = parse_utc(packet["captured_at_utc"])
    game_day = parse_utc(packet["event_start_utc"]).astimezone(ZoneInfo("America/New_York")).date()
    if any(row.settled_at_utc > decision or row.event_date >= game_day for row in history):
        raise ValueError("future_training_evidence")
    reproduced = forecast_packet(packet, artifact, history)
    for key in ("market_weight", "selected_probabilities", "selected_asks", "contracts"):
        if forecast[key] != reproduced[key]:
            raise ValueError("shadow_forecast_replay_mismatch")
    for market in ("moneyline", "spread", "total"):
        original = packet["markets"][market]
        contract = forecast["contracts"][market]
        if (
            contract["selection"] != original["selection"]
            or contract["line"] != original.get("line")
            or contract["quote_record_id"] != original["quote"]["lineage"]["market_snapshot_record_id"]
        ):
            raise ValueError("shadow_contract_mismatch")
    finals = []
    with games_path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                game = json.loads(line)
            except json.JSONDecodeError:
                continue
            if game.get("event_id") == forecast["event_id"] and game.get("status") == "completed":
                finals.append(game)
    if not finals:
        return {"status": "pending", "event_id": forecast["event_id"], "packet_sha256": digest}
    identities = {(game.get("home_team"), game.get("away_team"), game.get("home_score"), game.get("away_score")) for game in finals}
    if len(identities) != 1:
        raise ValueError("conflicting_final_game_versions")
    expected = packet["markets"]["spread"]["model_snapshot"]["features"]
    final = finals[-1]
    if final.get("home_team") != expected.get("home_team") or final.get("away_team") != expected.get("away_team"):
        raise ValueError("final_team_identity_mismatch")
    if parse_utc(final["event_start_utc"]) != parse_utc(packet["event_start_utc"]):
        raise ValueError("final_event_start_mismatch")
    return {
        "status": "scored",
        "event_id": forecast["event_id"],
        "packet_sha256": digest,
        "score_source_hash": final["raw_hash"],
        "final_score": {"home": final["home_score"], "away": final["away_score"]},
        "markets": score_forecast(forecast, packet, final),
        "scope": "prospective probability scoring; no hypothetical P&L or orders",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("shadow_forecast", type=Path)
    parser.add_argument("completed_games", type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.shadow_forecast, args.completed_games), indent=2, sort_keys=True))
