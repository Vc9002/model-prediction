"""Leaf helpers split out of forecast.py (no clock/slate call sites, so no test-patch targets)."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from ..config import (
    PROJECT_ROOT,
)
from ..domain import (
    parse_utc,
)
from ..models.market_residual import MarketResidualModel

logger = logging.getLogger("model_prediction.cli")


def _nba_volatility_features(artifact: dict, history, prediction) -> dict:
    """Capture exact replay inputs without changing the protected NBA engine."""
    features = dict(prediction.feature_basis)
    if artifact.get("dynamic_volatility"):
        from ..features.trends import TrendEngine

        trend = TrendEngine(
            history,
            offseason_gap_days=artifact.get("offseason_gap_days"),
            offseason_retention=float(artifact.get("offseason_retention", 0.50)),
            isolate_postseason=bool(artifact.get("isolate_postseason", False)),
        )
        features["volatility_away_consistency"] = trend.team_trend(prediction.away_team).consistency
        features["volatility_home_consistency"] = trend.team_trend(prediction.home_team).consistency
    return features


def _select_mlb_nrfi_market(rows: list[dict]) -> dict | None:
    """Select the latest prospective executable Polymarket NRFI market."""
    valid = [
        row
        for row in rows
        if isinstance(row.get("short"), dict)
        and row["short"].get("ask") is not None
        and isinstance(row.get("long"), dict)
        and row["long"].get("ask") is not None
        and 0 < float(row["short"]["ask"]) < 1
        and 0 < float(row["long"]["ask"]) < 1
    ]
    if not valid:
        return None
    return max(
        valid,
        key=lambda row: str(row.get("observed_at_utc") or row.get("transact_time_utc") or ""),
    )


def _select_wnba_spread_market(rows: list[dict]) -> dict | None:
    """Among alternate WNBA spread lines for one event, the main line is the
    one whose long-side ask sits closest to a coin flip -- same "most
    balanced line wins" rule mlb_market_odds._market_balance uses for MLB's
    alternate lines, adapted to this snapshot format's embedded long/short
    asks instead of a two-sided quote list."""
    partial_tokens = ("-1h-", "-2h-", "-1q-", "-2q-", "-3q-", "-4q-")
    candidates = [
        row
        for row in rows
        if isinstance(row.get("long"), dict)
        and row["long"].get("ask") is not None
        and row.get("line") is not None
        and not any(token in str(row.get("market_slug", "")).lower() for token in partial_tokens)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda row: abs(float(row["long"]["ask"]) - 0.5))


def _select_wnba_total_market(rows: list[dict]) -> dict | None:
    """Select the most balanced total market line for a WNBA game."""
    partial_tokens = ("-1h-", "-2h-", "-1q-", "-2q-", "-3q-", "-4q-", "-tt-", "-tt1h-", "-tt2h-")
    candidates = [
        row
        for row in rows
        if isinstance(row.get("long"), dict)
        and row["long"].get("ask") is not None
        and row.get("line") is not None
        and not any(token in str(row.get("market_slug", "")).lower() for token in partial_tokens)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda row: abs(float(row["long"]["ask"]) - 0.5))


def _quotes_known_at(rows: list[dict], cutoff: datetime) -> list[dict]:
    """Keep only quotes observed at or before ``cutoff``.

    The day's snapshot file keeps growing after a run starts, so rows stamped
    later than the run would otherwise be priced and then rejected as future.
    """
    known = []
    for row in rows:
        try:
            observed = parse_utc(str(row.get("observed_at_utc") or ""))
        except ValueError:
            continue
        if observed <= cutoff:
            known.append(row)
    return known


def _select_nba_spread_market(rows: list[dict], *, as_of: datetime | None = None) -> dict | None:
    """Among alternate NBA spread lines for one event, the main line is the
    one whose long-side ask sits closest to a coin flip -- most balanced line wins."""
    partial_tokens = ("-1h-", "-2h-", "-1q-", "-2q-", "-3q-", "-4q-")
    candidates = [
        row
        for row in rows
        if isinstance(row.get("long"), dict)
        and row["long"].get("ask") is not None
        and row.get("line") is not None
        and not any(token in str(row.get("market_slug", "")).lower() for token in partial_tokens)
    ]
    candidates = _fresh_latest_per_slug(candidates, as_of)
    if not candidates:
        return None
    return min(candidates, key=lambda row: abs(float(row["long"]["ask"]) - 0.5))


def _fresh_latest_per_slug(candidates: list[dict], as_of: datetime | None) -> list[dict]:
    """Keep only the newest capture of each market slug, and -- when ``as_of`` is
    given -- only captures inside the auto-buyer freshness window (falling back to
    the wider source window when nothing fresh exists). Without this, the
    "closest to 50%" pick could land on a stale morning capture whose ask no
    longer exists, which then trips STALE_DATA downstream or prices a dead quote
    (spread fixed in 6c078b08; totals had the same hole, MODEL_REVIEW #8d)."""
    if as_of is not None:
        from ..portfolio.auto_executor import (
            MAX_AUTO_BUYER_QUOTE_AGE_SECONDS,
            MAX_SOURCE_QUOTE_AGE_SECONDS,
        )

        def _within(row: dict, limit: float) -> bool:
            observed = row.get("observed_at_utc")
            if not observed:
                return False
            age = (as_of - parse_utc(str(observed))).total_seconds()
            return 0 <= age <= limit

        recent = [row for row in candidates if _within(row, MAX_AUTO_BUYER_QUOTE_AGE_SECONDS)]
        fallback = [row for row in candidates if _within(row, MAX_SOURCE_QUOTE_AGE_SECONDS)]
        candidates = recent or fallback
    latest_by_slug: dict[str, dict] = {}
    for row in candidates:
        slug = str(row.get("market_slug") or row.get("line"))
        previous = latest_by_slug.get(slug)
        if previous is None or str(row.get("observed_at_utc") or "") > str(
            previous.get("observed_at_utc") or ""
        ):
            latest_by_slug[slug] = row
    return list(latest_by_slug.values())


def _select_nba_total_market(rows: list[dict], *, as_of: datetime | None = None) -> dict | None:
    """Select the most balanced total market line for an NBA game, using only
    fresh, latest-per-slug captures (same contract as the spread selector)."""
    partial_tokens = ("-1h-", "-2h-", "-1q-", "-2q-", "-3q-", "-4q-", "-tt-", "-tt1h-", "-tt2h-")
    candidates = [
        row
        for row in rows
        if isinstance(row.get("long"), dict)
        and row["long"].get("ask") is not None
        and row.get("line") is not None
        and not any(token in str(row.get("market_slug", "")).lower() for token in partial_tokens)
    ]
    candidates = _fresh_latest_per_slug(candidates, as_of)
    if not candidates:
        return None
    return min(candidates, key=lambda row: abs(float(row["long"]["ask"]) - 0.5))


def _load_market_residual_model(config) -> MarketResidualModel | None:
    """Fail-soft load of the market-residual artifact (P0-4), diagnostic use only.

    A missing config block, missing file, or hash mismatch all fall back to
    None (no market_residual_probability recorded on the row) rather than
    raising into the primary forecast path -- this layer must never be able
    to block a real pick from being logged.
    """
    artifact_value = (config.get("models", {}).get("market_residual") or {}).get("artifact")
    if not artifact_value:
        return None
    artifact_path = Path(artifact_value)
    if not artifact_path.is_absolute():
        artifact_path = PROJECT_ROOT / artifact_path
    try:
        return MarketResidualModel.load(artifact_path)
    except (FileNotFoundError, ValueError, KeyError, json.JSONDecodeError):
        return None
