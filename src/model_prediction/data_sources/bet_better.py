"""Bet Better (betbetter.world) open model-feed capture — research-only.

Third-party model win probabilities priced against real lines, per market
(moneyline/spread/total, plus player props and first-inning markets on some
sports). No API key, no sign-up, no rate limit; licence CC BY 4.0 —
attribution required, recorded in every snapshot's envelope entry and in the
report dict.

Wired into the daily pipeline's step1e_bet_better_models since 2026-08-26 as
inert research-only capture: day-bucketed provenance snapshots under
``data/providers/bet_better/<sport>/``, never written to any ledger and never
consumed by a live decision path. The feed is reference evidence only — Bet
Better mints no stable event ids, so nothing here may become a ledger
identity (team-name collisions are this repo's documented bug class), and no
cross-source reconciliation may treat its picks as market truth (they are a
model's estimates, not bookmaker prices — real-edge/CLV measurement still
needs captured market prices, e.g. Polymarket BBOs).

Contract verified live 2026-08-26, one unauthenticated call per feed path
(no key exists to leak; the site 301s the ``.aspx`` spelling to these pretty
URLs, so request the canonical form directly):

- URL: ``{base}/{feed_path}?format=json``, e.g. ``/mlb/picks?format=json``.
- Response envelope: site, page, sport, type, updatedUtc, licence,
  attribution, docs, disclaimer, count, picks[].
- Pick fields: game ("Away @ Home"), gameTimeUtc (ISO), market (e.g.
  "Head to Head", "Spread", "Total Points", "Moneyline - 1st 5 Innings"),
  selection, line (number|null), modelProbabilityPct (0-100), fairOdds
  (decimal|null), confidence (HIGH|LEAN|LONG-SHOT), verdict.
- ``count=0`` (empty picks) is the provider's "nothing on now"
  representation (offseason, no games): captured as an available=False
  entry so "provider empty" stays provably distinct from "we never asked".

Request budget: one GET per league per daily run (12 feeds). The 1s default
``request_delay`` between feeds is politeness, not quota — the provider
documents no rate limit; if that ever changes, per-league failures are
fail-soft either way.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from ..config import PROJECT_ROOT
from ..domain import utc_now
from .provider_capture import ProviderEntry, write_provider_snapshot

BET_BETTER_BASE_URL = "https://betbetter.world"
BET_BETTER_LICENCE = "CC BY 4.0"
BET_BETTER_ATTRIBUTION = "Bet Better — https://betbetter.world"

# League label -> picks feed path. Soccer labels reuse the repo's existing
# conventions (API_FOOTBALL_LEAGUE_IDS keys) so a later cross-check can join
# on the same league names. Every path verified live 2026-08-26 with one
# unauthenticated call each (the site redirects any .aspx spelling to these
# canonical URLs).
BET_BETTER_FEEDS: dict[str, str] = {
    "MLB": "/mlb/picks",
    "WNBA": "/wnba/picks",
    "NBA": "/nba/picks",
    "NFL": "/nfl/picks",
    "PREMIER_LEAGUE": "/soccer/epl/picks",
    "LA_LIGA": "/soccer/la-liga/picks",
    "SERIE_A": "/soccer/serie-a/picks",
    "BUNDESLIGA": "/soccer/bundesliga/picks",
    "LIGUE_1": "/soccer/ligue-1/picks",
    "MLS": "/soccer/mls/picks",
    "WORLD_CUP": "/soccer/world-cup/picks",
    "WTA": "/tennis/wta/picks",
}

# Snapshot-dir sport bucket per feed label (write_provider_snapshot's
# ``sport`` argument). One label must map to exactly one bucket — the
# snapshot layout is the durable evidence path, never a guess.
_BET_BETTER_SPORT: dict[str, str] = {
    "MLB": "mlb",
    "WNBA": "wnba",
    "NBA": "nba",
    "NFL": "nfl",
    "PREMIER_LEAGUE": "soccer",
    "LA_LIGA": "soccer",
    "SERIE_A": "soccer",
    "BUNDESLIGA": "soccer",
    "LIGUE_1": "soccer",
    "MLS": "soccer",
    "WORLD_CUP": "soccer",
    "WTA": "tennis",
}


class BetBetterClient:
    def __init__(
        self,
        base_url: str = BET_BETTER_BASE_URL,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=30, follow_redirects=True)

    def picks(self, feed_path: str) -> dict[str, Any]:
        """One picks feed as the provider's JSON envelope (site/picks/...)."""
        try:
            response = self.client.get(
                self.base_url + feed_path,
                params={"format": "json"},
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001 -- no credential anywhere on this request, so the message is safe to surface as-is; re-raise under the one httpx base class every caller catches (json decode of an HTML error page lands here too)
            raise httpx.HTTPError(str(exc)) from None


def _pick_entry(
    label: str,
    pick: dict[str, Any],
    observed_at: datetime,
) -> ProviderEntry:
    """One pick as a normalized ProviderEntry.

    ``source_entity_id`` is the provider's own identifying tuple verbatim
    (gameTimeUtc|market|selection|line) — the provider mints no stable id,
    and this repo must never fabricate one on its behalf.
    """
    game_time = str(pick.get("gameTimeUtc", ""))
    return ProviderEntry(
        source="bet_better",
        source_entity_id=(
            f"{game_time}|{pick.get('market', '')}|{pick.get('selection', '')}|{pick.get('line', '')}"
        ),
        effective_at_utc=game_time,
        observed_at_utc=observed_at.isoformat(),
        payload=pick,
    )


def _envelope_entry(
    label: str,
    envelope: dict[str, Any],
    observed_at: datetime,
    *,
    available: bool,
    missing_reason: str | None,
) -> ProviderEntry:
    """The feed's own envelope (licence/attribution/updatedUtc) minus the
    picks list, so the CC BY attribution travels with the raw capture and a
    live response's updatedUtc proves the fetch really happened."""
    payload = {key: value for key, value in envelope.items() if key != "picks"}
    return ProviderEntry(
        source="bet_better",
        source_entity_id=f"{label}:envelope",
        effective_at_utc=str(envelope.get("updatedUtc", "")),
        observed_at_utc=observed_at.isoformat(),
        payload=payload,
        available=available,
        missing_reason=missing_reason,
    )


def collect_bet_better_models(
    data_root: str | Path | None = None,
    *,
    feeds: dict[str, str] | None = None,
    client: BetBetterClient | None = None,
    request_delay: float = 1.0,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Fetch every Bet Better picks feed and write provenance snapshots.

    Report shape mirrors the daily pipeline's other capture steps:
    per-league ``{"status": "ok", "picks_returned", "raw_path",
    "snapshot_path"}`` or ``{"status": "error", "error"}``, plus totals and
    the licence/attribution lines. Per-league failures are fail-soft (the
    daily job logs and continues); this source has no key, so there is no
    no_api_key state.
    """
    if data_root is None:
        data_root = PROJECT_ROOT / "data"
    observed = observed_at or utc_now()
    if client is None:
        client = BetBetterClient()

    feed_map = feeds if feeds is not None else dict(BET_BETTER_FEEDS)
    results: dict[str, Any] = {}
    total_picks = 0
    for label, path in sorted(feed_map.items()):
        try:
            envelope = client.picks(path)
            picks = envelope.get("picks")
            if not isinstance(picks, list):
                raise TypeError("response envelope has no picks list")
        except Exception as exc:  # noqa: BLE001 -- per-league fail-soft: the caller needs the message as data, not a raised error (same contract as api_football.py)
            results[label] = {"status": "error", "error": str(exc)[:200]}
            continue

        entries: list[ProviderEntry] = []
        if picks:
            entries.append(_envelope_entry(label, envelope, observed, available=True, missing_reason=None))
            entries.extend(_pick_entry(label, pick, observed) for pick in picks)
        else:
            # count=0 is a real provider answer ("nothing on now"), not a
            # fetch failure -- record it as such so the empty feed is never
            # mistaken later for a capture that did not run.
            entries.append(
                _envelope_entry(
                    label,
                    envelope,
                    observed,
                    available=False,
                    missing_reason="provider returned 0 picks (offseason or no games on this feed)",
                )
            )

        _, raw_path, snapshot_path = write_provider_snapshot(
            data_root,
            source="bet_better",
            sport=_BET_BETTER_SPORT.get(label, "other"),
            entries=entries,
            observed_at=observed,
            source_url=f"{BET_BETTER_BASE_URL}{path}?format=json",
        )
        results[label] = {
            "status": "ok",
            "picks_returned": len(picks),
            "raw_path": str(raw_path),
            "snapshot_path": str(snapshot_path),
        }
        total_picks += len(picks)
        if request_delay:
            time.sleep(request_delay)

    results["total_picks"] = total_picks
    results["feeds"] = len(feed_map)
    results["licence"] = BET_BETTER_LICENCE
    results["attribution"] = BET_BETTER_ATTRIBUTION
    results["captured_at_utc"] = observed.isoformat()
    return results
