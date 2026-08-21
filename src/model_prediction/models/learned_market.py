"""Hash-verified learned probability calibration and selective market gating."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _sigmoid(value: float) -> float:
    if value >= 0:
        inverse = math.exp(-value)
        return 1.0 / (1.0 + inverse)
    exponential = math.exp(value)
    return exponential / (1.0 + exponential)


def artifact_hash(payload: Mapping[str, Any]) -> str:
    canonical = {key: value for key, value in payload.items() if key != "artifact_hash"}
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class GatedMarketDecision:
    call: bool
    market_type: str
    selection: str
    probability: float
    confidence_threshold: float
    market_probability: float | None
    edge: float | None
    expected_value: float | None
    reason: str


class LearnedMarketArtifact:
    """Per-sport, per-market logistic models with learned confidence gates."""

    def __init__(self, raw: dict[str, Any]) -> None:
        if raw.get("artifact_hash") != artifact_hash(raw):
            raise ValueError("learned market artifact hash mismatch")
        if raw.get("method") != "logistic_regression":
            raise ValueError("learned market artifact must use logistic_regression")
        if not raw.get("market_models"):
            raise ValueError("learned market artifact has no market models")
        self.raw = raw

    @classmethod
    def load(cls, path: str | Path) -> LearnedMarketArtifact:
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    @property
    def sport(self) -> str:
        return str(self.raw["sport"]).lower()

    @property
    def version(self) -> str:
        return str(self.raw["model_version"])

    @property
    def hash(self) -> str:
        return str(self.raw["artifact_hash"])

    @property
    def qualified(self) -> bool:
        """Whether the pinned locked-holdout evaluation qualified this version.

        Checks the nested qualification block first (standard for most artifacts),
        then falls back to the top-level qualified key (used by MLB v4 and esports).
        """
        qual_block = self.raw.get("qualification", {})
        if "qualified" in qual_block:
            return bool(qual_block["qualified"])
        return bool(self.raw.get("qualified", False))

    @property
    def qualification(self) -> dict[str, Any]:
        return dict(self.raw.get("qualification", {}))

    def supports(self, market_type: str) -> bool:
        return market_type in self.raw["market_models"]

    def probability(self, market_type: str, features: Mapping[str, float]) -> float:
        try:
            model = self.raw["market_models"][market_type]
        except KeyError as error:
            raise ValueError(f"artifact does not support {market_type}") from error
        names = tuple(model["feature_names"])
        coefficients = tuple(float(value) for value in model["coefficients"])
        if len(names) != len(coefficients):
            raise ValueError(f"{market_type} feature/coefficient length mismatch")
        missing = [name for name in names if name not in features]
        if missing:
            raise ValueError(f"{market_type} missing learned features: {missing}")
        linear = float(model["intercept"]) + sum(
            coefficient * float(features[name]) for name, coefficient in zip(names, coefficients, strict=True)
        )
        probability = min(0.999999, max(0.000001, _sigmoid(linear)))
        # Optional post-hoc calibration, applied only when the artifact's own
        # moneyline block carries a "calibration" entry -- every existing
        # artifact without one follows the exact legacy path above,
        # bit-for-bit. Unknown methods fail closed: the artifact refuses to
        # serve rather than silently serving uncalibrated probabilities.
        calibration = model.get("calibration") or {}
        method = calibration.get("method")
        if method in (None, "identity"):
            return probability
        if method == "temperature":
            temperature = float(calibration["temperature"])
            if temperature <= 0:
                raise ValueError(f"invalid {market_type} calibration temperature")
            logit = math.log(probability / (1 - probability))
            return min(0.999999, max(0.000001, _sigmoid(logit / temperature)))
        raise ValueError(f"unsupported {market_type} calibration method: {method}")

    def threshold(self, market_type: str) -> float:
        model = self.raw["market_models"][market_type]
        threshold = float(model["confidence_threshold"])
        if not 0.5 <= threshold < 1:
            raise ValueError(f"invalid {market_type} confidence threshold")
        return threshold

    def decide_binary(
        self,
        market_type: str,
        features: Mapping[str, float],
        *,
        positive_selection: str = "home",
        negative_selection: str = "away",
        market_probabilities: Mapping[str, float] | None = None,
    ) -> GatedMarketDecision:
        """Evaluate one binary LR once, then choose its positive or negative class.

        The learned moneyline artifacts predict the probability of the home
        outcome from home-oriented features. Applying the same classifier once
        per side would be mathematically wrong; the away probability is the
        complement of the home probability.
        """
        positive_probability = self.probability(market_type, features)
        if positive_probability >= 0.5:
            selection = positive_selection
            probability = positive_probability
        else:
            selection = negative_selection
            probability = 1 - positive_probability
        market_probability = (
            float(market_probabilities[selection])
            if market_probabilities is not None and selection in market_probabilities
            else None
        )
        edge = probability - market_probability if market_probability is not None else None
        threshold = self.threshold(market_type)
        call = probability >= threshold
        return GatedMarketDecision(
            call=call,
            market_type=market_type,
            selection=selection,
            probability=round(probability, 6),
            confidence_threshold=threshold,
            market_probability=round(market_probability, 6) if market_probability is not None else None,
            edge=round(edge, 6) if edge is not None else None,
            expected_value=None,
            reason="CALL_LEARNED_CONFIDENCE" if call else "NO_CALL_BELOW_LEARNED_CONFIDENCE",
        )

    def decide(
        self,
        market_type: str,
        sides: Mapping[str, Mapping[str, Any]],
        minimum_edge: float | None = None,
    ) -> GatedMarketDecision:
        """Choose the most probable side and gate only on learned confidence.

        ``minimum_edge`` remains accepted for API compatibility but is not a
        qualification or call/no-call gate. Market price, edge, and EV are
        optional diagnostics for a later trade-profitability evaluation.
        """
        if not self.supports(market_type):
            raise ValueError(f"artifact does not support {market_type}")
        evaluated = []
        for selection, values in sides.items():
            probability = self.probability(market_type, values["features"])
            market_raw = values.get("market_probability")
            odds_raw = values.get("decimal_odds")
            market_probability = float(market_raw) if market_raw is not None else None
            decimal_odds = float(odds_raw) if odds_raw is not None else None
            edge = probability - market_probability if market_probability is not None else None
            expected_value = (
                probability * (decimal_odds - 1.0) - (1.0 - probability) if decimal_odds is not None else None
            )
            evaluated.append((probability, selection, market_probability, edge, expected_value))
        probability, selection, market_probability, edge, expected_value = max(evaluated)
        threshold = self.threshold(market_type)
        call = probability >= threshold
        reason = "CALL_LEARNED_CONFIDENCE" if call else "NO_CALL_BELOW_LEARNED_CONFIDENCE"
        return GatedMarketDecision(
            call=call,
            market_type=market_type,
            selection=selection,
            probability=round(probability, 6),
            confidence_threshold=threshold,
            market_probability=round(market_probability, 6) if market_probability is not None else None,
            edge=round(edge, 6) if edge is not None else None,
            expected_value=round(expected_value, 6) if expected_value is not None else None,
            reason=reason,
        )


def build_artifact(
    *,
    sport: str,
    model_version: str,
    market_models: Mapping[str, Mapping[str, Any]],
    training: Mapping[str, Any],
    qualification: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "1",
        "sport": sport.lower(),
        "model_version": model_version,
        "method": "logistic_regression",
        "market_models": dict(market_models),
        "training": dict(training),
    }
    if qualification is not None:
        payload["qualification"] = dict(qualification)
    payload["artifact_hash"] = artifact_hash(payload)
    return payload


def learn_confidence_threshold(
    probabilities: Sequence[float],
    outcomes: Sequence[int],
    *,
    target_hit_rate: float,
    minimum_calls: int,
) -> tuple[float, dict[str, Any]]:
    """Choose the least-selective observed threshold meeting validation targets."""
    if len(probabilities) != len(outcomes):
        raise ValueError("probability/outcome length mismatch")
    if not 0.5 < target_hit_rate < 1:
        raise ValueError("target_hit_rate must be between 0.5 and 1")
    rows = sorted(
        (
            (max(value, 1 - value), int((value >= 0.5) == bool(outcome)))
            for value, outcome in zip(probabilities, outcomes, strict=True)
        ),
        reverse=True,
    )
    eligible: list[tuple[float, int, float]] = []
    hits = calls = 0
    cursor = 0
    while cursor < len(rows):
        confidence = rows[cursor][0]
        tied = []
        while cursor < len(rows) and rows[cursor][0] == confidence:
            tied.append(rows[cursor])
            cursor += 1
        calls += len(tied)
        hits += sum(correct for _, correct in tied)
        # Evaluate only at complete confidence boundaries. Evaluating inside a
        # tie would report a threshold whose actual >= selection includes rows
        # the validation calculation silently omitted.
        if calls >= minimum_calls and hits / calls >= target_hit_rate:
            eligible.append((confidence, calls, hits / calls))
    if not eligible:
        raise ValueError(f"no confidence threshold reaches {target_hit_rate:.1%} with {minimum_calls}+ calls")
    threshold, calls, hit_rate = eligible[-1]
    return round(threshold, 8), {
        "validation_calls": calls,
        "validation_hit_rate": round(hit_rate, 6),
        "target_hit_rate": target_hit_rate,
        "minimum_calls": minimum_calls,
        "selectivity": round(calls / len(rows), 6),
    }
