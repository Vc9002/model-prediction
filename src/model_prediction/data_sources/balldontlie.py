"""BALLDONTLIE (api.balldontlie.io) client -- raw capture only.

BALLDONTLIE is a secondary data *provider*, not an automatic feature source
(see MASTER.md's API-expansion note). It exposes MLB player injuries, plate
appearances, pitch-type splits, player splits/versus, and odds -- alongside
20+ other sports/leagues -- through one consistent per-sport REST API,
verified live against its published OpenAPI spec (api.balldontlie.io,
2026-08-19: MLB alone covers players, teams, games, stats, season_stats,
player_injuries, plate_appearances, hitter/pitcher_pitch_type_game_stats,
players/splits, players/versus, odds).

This module only fetches and returns raw JSON -- it does not decide
availability, does not resolve entities, and is never called from a live
forecast path. Point-in-time capture (writing an immutable, hashed snapshot
with its own ``observed_at_utc``) is a separate concern; see
``provider_capture.py`` for the shared snapshot-writing contract new
providers should reuse instead of reimplementing it per source (this is the
same raw-capture shape ``mlb_injuries.py`` already uses).

Requires ``BALLDONTLIE_API_KEY`` -- no free-tier access has been verified for
the MLB advanced endpoints this module targets (player_injuries,
plate_appearances); confirm plan coverage at balldontlie.io/pricing before
wiring this into any scheduled job.
"""

from __future__ import annotations

from typing import Any

import httpx

BASE_URL = "https://api.balldontlie.io"


class BallDontLieClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = BASE_URL,
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("BALLDONTLIE_API_KEY is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=30)

    def _safe_get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        """GET + raise_for_status, with the API key redacted from any error message."""
        try:
            response = self.client.get(
                f"{self.base_url}{path}",
                params=params,
                headers={"Authorization": self.api_key},
            )
            response.raise_for_status()
            return response
        except Exception as exc:  # noqa: BLE001 -- both transport and HTTP errors may embed the key in the request repr; redact before re-raising (same pattern as the_odds_api.py's _safe_get)
            msg = str(exc).replace(self.api_key, "[REDACTED]")
            raise httpx.HTTPError(msg) from None

    def _paginated(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Walk BALLDONTLIE's cursor-based pagination (``meta.next_cursor``) to exhaustion."""
        results: list[dict[str, Any]] = []
        cursor: int | None = None
        while True:
            page_params = dict(params)
            if cursor is not None:
                page_params["cursor"] = cursor
            response = self._safe_get(path, page_params)
            payload = response.json()
            results.extend(payload.get("data", []))
            cursor = (payload.get("meta") or {}).get("next_cursor")
            if not cursor:
                return results

    def mlb_player_injuries(
        self, *, team_ids: list[int] | None = None, player_ids: list[int] | None = None
    ) -> list[dict[str, Any]]:
        """Current injury report rows. Live-only (no as-of-date parameter published) --
        each call reflects "now", same caveat as MLB Stats API's roster endpoint
        (see mlb_injuries.py's module docstring): must be captured with its own
        observed_at_utc, never treated as retroactively queryable."""
        params: dict[str, Any] = {"per_page": 100}
        if team_ids:
            params["team_ids[]"] = team_ids
        if player_ids:
            params["player_ids[]"] = player_ids
        return self._paginated("/mlb/v1/player_injuries", params)

    def mlb_plate_appearances(
        self, *, game_ids: list[int] | None = None, player_ids: list[int] | None = None
    ) -> list[dict[str, Any]]:
        """Per-plate-appearance rows (batter, pitcher, pitch sequence, outcome) --
        the batter-vs-pitch-type / platoon signal the totals-cohort research
        direction wants (see MASTER.md's API-expansion note). Genuinely
        point-in-time queryable by completed game_id, unlike the injury feed."""
        params: dict[str, Any] = {"per_page": 100}
        if game_ids:
            params["game_ids[]"] = game_ids
        if player_ids:
            params["player_ids[]"] = player_ids
        return self._paginated("/mlb/v1/plate_appearances", params)

    def mlb_hitter_pitch_type_game_stats(
        self, *, game_ids: list[int] | None = None, player_ids: list[int] | None = None
    ) -> list[dict[str, Any]]:
        """Per-game batter performance broken out by pitch type faced."""
        params: dict[str, Any] = {"per_page": 100}
        if game_ids:
            params["game_ids[]"] = game_ids
        if player_ids:
            params["player_ids[]"] = player_ids
        return self._paginated("/mlb/v1/hitter_pitch_type_game_stats", params)

    def mlb_pitcher_pitch_type_game_stats(
        self, *, game_ids: list[int] | None = None, player_ids: list[int] | None = None
    ) -> list[dict[str, Any]]:
        """Per-game pitcher arsenal usage/results broken out by pitch type."""
        params: dict[str, Any] = {"per_page": 100}
        if game_ids:
            params["game_ids[]"] = game_ids
        if player_ids:
            params["player_ids[]"] = player_ids
        return self._paginated("/mlb/v1/pitcher_pitch_type_game_stats", params)
