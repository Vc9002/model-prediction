"""Modular sport dispatcher for forecast and settlement pipelines.

Routes CLI forecasting and settlement requests through standardized
SportAdapter instances in SportRegistry, decoupling the CLI from
bespoke per-sport implementation logic.
"""

from __future__ import annotations

import logging
from typing import Any

from model_prediction.core.time_context import TimeContext
from model_prediction.sports.base import SportAdapter, SportRegistry

logger = logging.getLogger(__name__)


class SportDispatcher:
    """Central dispatcher executing forecasts and settlements via SportRegistry adapters."""

    @classmethod
    def dispatch_forecast(
        cls,
        sport: str,
        context: TimeContext,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Dispatch a forecast request to the appropriate SportAdapter.

        Guarantees Point-in-Time safety by requiring an explicit TimeContext.
        """
        sport_key = sport.strip().casefold()
        adapter: SportAdapter | None = SportRegistry.get(sport_key)

        if adapter is None:
            logger.warning("No registered SportAdapter found for sport: %s", sport)
            return {
                "sport": sport_key,
                "status": "unsupported",
                "candidates": [],
                "count": 0,
                "error": f"no adapter registered for {sport_key}",
            }

        try:
            # 1. Fetch slate observable at decision time
            slate = adapter.fetch_slate(context, **kwargs)

            # 2. Build Point-in-Time features
            features = adapter.build_features(slate, context)

            # 3. Generate probabilities across all supported markets
            predictions = adapter.generate_probabilities(features, context)

            return {
                "sport": sport_key,
                "status": "ok",
                "candidates": predictions,
                "count": len(predictions),
                "decision_time_utc": context.decision_time_utc.isoformat(),
            }
        except Exception as exc:
            logger.exception("Error executing forecast for %s", sport_key)
            return {
                "sport": sport_key,
                "status": "error",
                "candidates": [],
                "count": 0,
                "error": str(exc),
            }

    @classmethod
    def dispatch_settlement(
        cls,
        sport: str,
        event_id: str,
        box_score: dict[str, Any],
        open_pick: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Dispatch a grading request to the appropriate SportAdapter."""
        sport_key = sport.strip().casefold()
        adapter: SportAdapter | None = SportRegistry.get(sport_key)

        if adapter is None:
            logger.warning("No registered SportAdapter found to grade sport: %s", sport)
            return None

        try:
            return adapter.grade_event(event_id, box_score, open_pick)
        except Exception:
            logger.exception(
                "Error grading pick %s on %s",
                open_pick.get("pick_id"),
                sport_key,
            )
            return None
