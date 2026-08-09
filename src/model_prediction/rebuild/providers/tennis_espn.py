"""Raw-first ESPN tennis scoreboard (ATP/WTA) -- current-event/PIT cross-check.

Undocumented public endpoint (same family as `soccer_espn.py`/
`sportsdataverse.py`'s WNBA scoreboard call) -- `SourceGrade.C`, rights stay
unresolved/research_shadow_only regardless of public reachability. This is a
*current-event* source (today's/this-week's matches with live status), not a
historical archive -- see `tennis_mylife.py` for that.

Verified live before writing this module:
`GET https://site.api.espn.com/apis/site/v2/sports/tennis/{atp,wta}/scoreboard`
returns real structured 2026 event data: `events[].groupings[].competitions[]`
each carrying a real competition id, start time, status
(`STATUS_FINAL`/etc.), venue, and two `competitors[]` entries with a real
ESPN athlete id/name/country and `linescores` (set scores) plus a `winner`
flag. There is no home/away court semantics in tennis -- ESPN's own
`homeAway` field is source ordering, not a meaningful venue relationship,
so it is preserved as `espn_home_away` (raw, disclosed) rather than
reinterpreted as `player_a`/`player_b` by any assumption other than the
source's own listed order.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any, Final, Literal

import polars as pl

from .base import ProviderResult, ProviderStatus, SourceGrade, SourceResponseMetadata, dataframe_schema_hash
from .cache import ProviderRawCache
from .http import HttpProviderClient
from .rights import SourceRightsProfile

Tour = Literal["atp", "wta"]

TENNIS_ESPN_RIGHTS = SourceRightsProfile(
    source_asset="ESPN Site v2 tennis scoreboard (ATP/WTA)",
    provider_chain="site.api.espn.com",
    license_id="ESPN-data-rights-unresolved",
    license_url="https://disneytermsofuse.com/",
    attribution_required=False,
    attribution_text=None,
    subscription_required=False,
    subscription_scope="none",
    upstream_rights_status="unresolved",
    commercial_use_status="unresolved",
    use_scope="research_shadow_only",
    production_allowed=False,
    policy_note=(
        "Public, undocumented endpoint. Reachability does not grant "
        "commercial rights to ESPN-derived data."
    ),
)

REQUIRED_MATCH_COLUMNS: Final[frozenset[str]] = frozenset({
    "espn_event_id", "espn_competition_id", "tour", "start_date",
    "status_name", "status_completed",
    "competitor_1_id", "competitor_1_name", "competitor_2_id", "competitor_2_name",
})


class ESPNTennisProvider:
    provider_id = "espn_tennis"
    rights = TENNIS_ESPN_RIGHTS

    def __init__(self, http: HttpProviderClient, cache: ProviderRawCache) -> None:
        self.http = http
        self.cache = cache

    def scoreboard(self, tour: Tour, *, force: bool = False) -> ProviderResult:
        endpoint = f"{tour}_scoreboard"
        parameters: dict[str, Any] = {"tour": tour}
        cached = self.cache.latest_success(self.provider_id, "tennis", endpoint, parameters)
        if cached is not None and not force:
            return self._parse(cached.read_bytes(), cached.metadata)
        url = f"https://site.api.espn.com/apis/site/v2/sports/tennis/{tour}/scoreboard"
        try:
            fetched = self.http.get(url)
        except Exception as exc:  # noqa: BLE001 -- external transport boundary
            return ProviderResult.unavailable(f"ESPN tennis transport failed ({type(exc).__name__})")
        metadata = SourceResponseMetadata(
            provider=self.provider_id,
            sport="tennis",
            endpoint_family=endpoint,
            requested_parameters=parameters,
            request_time_utc=fetched.request_time_utc,
            retrieved_at_utc=fetched.retrieved_at_utc,
            observed_at_utc=fetched.retrieved_at_utc,
            http_status=fetched.status_code,
            content_hash=hashlib.sha256(fetched.body).hexdigest(),
            schema_hash=None,
            content_type=fetched.headers.get("content-type"),
            source_version="espn-site-v2",
            source_grade=SourceGrade.C,
            **self.rights.metadata_kwargs(),
        )
        self.cache.store(metadata, fetched.body)
        if fetched.status_code != 200:
            return ProviderResult.unavailable(f"ESPN tennis returned HTTP {fetched.status_code}", metadata)
        return self._parse(fetched.body, metadata)

    @staticmethod
    def _parse(body: bytes, metadata: SourceResponseMetadata) -> ProviderResult:
        tour = str(metadata.requested_parameters.get("tour", ""))
        try:
            payload = json.loads(body)
            events = payload.get("events")
            if not isinstance(events, list):
                raise TypeError("scoreboard payload lacks an events list")
            rows: list[dict[str, Any]] = []
            for event in events:
                event_id = str(event["id"])
                event_name = event.get("name") or event.get("shortName") or ""
                season = event.get("season") or {}
                for grouping in event.get("groupings", []):
                    grouping_info = grouping.get("grouping") or {}
                    for competition in grouping.get("competitions", []):
                        status = (competition.get("status") or {}).get("type") or {}
                        venue = competition.get("venue") or {}
                        competitors = competition.get("competitors") or []
                        if len(competitors) != 2:
                            continue
                        first, second = competitors[0], competitors[1]

                        def _athlete(entry: dict[str, Any]) -> dict[str, Any]:
                            athlete = entry.get("athlete") or {}
                            return {
                                "id": str(athlete.get("guid") or entry.get("id") or ""),
                                "name": str(athlete.get("displayName") or athlete.get("fullName") or ""),
                                "country": ((athlete.get("flag") or {}).get("alt")),
                                "winner": bool(entry.get("winner", False)),
                                "espn_home_away": entry.get("homeAway"),
                            }

                        p1, p2 = _athlete(first), _athlete(second)
                        rows.append({
                            "espn_event_id": event_id,
                            "espn_competition_id": str(competition.get("id") or ""),
                            "tour": tour,
                            "tournament_name": str(event_name),
                            "grouping_slug": grouping_info.get("slug"),
                            "season_year": season.get("year"),
                            "start_date": str(competition.get("date") or competition.get("startDate") or ""),
                            "status_name": status.get("name") or "UNKNOWN",
                            "status_state": status.get("state"),
                            "status_completed": bool(status.get("completed", False)),
                            "venue_name": venue.get("fullName"),
                            "court": venue.get("court"),
                            "competitor_1_id": p1["id"],
                            "competitor_1_name": p1["name"],
                            "competitor_1_country": p1["country"],
                            "competitor_1_winner": p1["winner"],
                            "competitor_1_espn_home_away": p1["espn_home_away"],
                            "competitor_2_id": p2["id"],
                            "competitor_2_name": p2["name"],
                            "competitor_2_country": p2["country"],
                            "competitor_2_winner": p2["winner"],
                            "competitor_2_espn_home_away": p2["espn_home_away"],
                        })
            frame = (
                pl.DataFrame(rows)
                if rows
                else pl.DataFrame(schema={
                    "espn_event_id": pl.String, "espn_competition_id": pl.String,
                    "tour": pl.String, "start_date": pl.String,
                })
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return ProviderResult(ProviderStatus.DEGRADED, metadata, None, f"ESPN tennis schema drift: {exc}")
        if not frame.is_empty():
            missing = sorted(REQUIRED_MATCH_COLUMNS - set(frame.columns))
            if missing:
                return ProviderResult(ProviderStatus.DEGRADED, metadata, None, f"ESPN tennis schema drift: missing {missing}")
        updated = replace(metadata, schema_hash=dataframe_schema_hash(frame))
        return ProviderResult(ProviderStatus.AVAILABLE, updated, frame, "NO_EVENTS" if frame.is_empty() else None)

    def events(self, *, sport: str, **kwargs: Any) -> ProviderResult:
        tour = str(kwargs.get("tour", "atp"))
        if sport != "tennis" or tour not in ("atp", "wta"):
            return ProviderResult.unavailable("ESPN tennis events require sport=tennis and tour in {atp, wta}")
        return self.scoreboard(tour, force=bool(kwargs.get("force")))  # type: ignore[arg-type]
