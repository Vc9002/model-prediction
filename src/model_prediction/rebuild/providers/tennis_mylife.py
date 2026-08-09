"""TennisMyLife ATP/WTA historical match data -- replaces the dead Sackmann source.

`sackmann_tennis.py`'s docstring documents why the original
`github.com/JeffSackmann/tennis_atp`/`tennis_wta` repositories are no longer
usable (verified via GitHub API: both 404, the account has only one
unrelated public repo left). This module is the real, verified replacement.

Verified live before writing this module:
- `GET https://stats.tennismylife.org/api/data-files` returns a real,
  working index of 171 files as of this writing: `{name, url, size, mtime}`.
- File coverage includes ATP tour years back to 1967, WTA years from 1990,
  ATP Challenger years, ATP qualifying years (`atp_quali/<year>_atp_quali.csv`),
  and three "ongoing" files for in-progress tournaments
  (`ongoing_tourneys.csv` for ATP, `wta_ongoing_tourneys.csv`,
  `challenger_ongoing_tourneys.csv`) -- through 2026 at the time of writing.
- The CSV schema is the same well-known Sackmann-style column set
  (`tourney_id, tourney_name, surface, ..., winner_id, winner_name, ...,
  w_ace, w_df, ..., score, best_of, round, minutes, ...`), confirmed by
  fetching real 2026 ATP/WTA/ongoing files directly.

NOT verified, and deliberately not claimed as cleared: the site's licensing
terms. The claim that the database is "free to use"/MIT-licensed could not
be independently confirmed from the live site (`stats.tennismylife.org` and
its API response carry no license/terms text as of this writing) or from a
dedicated terms page. `TENNIS_MYLIFE_RIGHTS` below stays maximally
conservative (`unresolved`/`research_shadow_only`/`production_allowed=False`)
regardless of that claim -- rights fields describe what has been verified,
not what a third party asserts.

Uses the real file index rather than hardcoding a year->URL naming
convention, so a future naming change doesn't silently break requests.
"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import replace
from typing import Any, Final, Literal

import polars as pl

from .base import ProviderResult, ProviderStatus, SourceGrade, SourceResponseMetadata, dataframe_schema_hash
from .cache import ProviderRawCache
from .http import HttpProviderClient
from .rights import SourceRightsProfile

DATA_FILES_INDEX_URL = "https://stats.tennismylife.org/api/data-files"
DATA_FILE_BASE_URL = "https://stats.tennismylife.org/data/"
PARSER_VERSION = "tennis-mylife-csv-v1"

REQUIRED_MATCH_COLUMNS: Final[frozenset[str]] = frozenset({
    "tourney_id", "tourney_name", "surface", "tourney_date",
    "winner_id", "winner_name", "loser_id", "loser_name",
    "score", "best_of", "round",
})

Tour = Literal["atp", "wta"]
MatchKind = Literal["main", "challenger", "qualifying"]

TENNIS_MYLIFE_RIGHTS = SourceRightsProfile(
    source_asset="TennisMyLife ATP/WTA historical and ongoing match CSV database",
    provider_chain="stats.tennismylife.org",
    license_id="tennismylife-license-unverified",
    license_url="https://stats.tennismylife.org/",
    attribution_required=True,
    attribution_text="Match data via TennisMyLife (stats.tennismylife.org)",
    subscription_required=False,
    subscription_scope="none",
    upstream_rights_status="unresolved",
    commercial_use_status="unresolved",
    use_scope="research_shadow_only",
    production_allowed=False,
    policy_note=(
        "The provider's public materials claim the database is free/"
        "MIT-licensed, but this has not been independently verified from "
        "the live site (no license or terms text found on the homepage, "
        "data-file pages, or API response as of this writing). Treated as "
        "unresolved, not cleared, until that claim can be confirmed against "
        "an actual license/terms document."
    ),
)


class TennisMyLifeProvider:
    provider_id = "tennis_mylife"
    rights = TENNIS_MYLIFE_RIGHTS

    def __init__(self, http: HttpProviderClient, cache: ProviderRawCache) -> None:
        self.http = http
        self.cache = cache

    def data_files_index(self, *, force: bool = False) -> ProviderResult:
        """The real published file inventory -- source of truth for filenames/URLs."""
        endpoint = "data_files_index"
        parameters: dict[str, Any] = {}
        cached = self.cache.latest_success(self.provider_id, "tennis", endpoint, parameters)
        if cached is not None and not force:
            return self._parse_index(cached.read_bytes(), cached.metadata)
        try:
            fetched = self.http.get(DATA_FILES_INDEX_URL)
        except Exception as exc:  # noqa: BLE001 -- external transport boundary
            return ProviderResult.unavailable(f"TennisMyLife index request failed: {exc}")
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
            source_version="tennismylife-data-files-api-v1",
            source_grade=SourceGrade.B,
            **self.rights.metadata_kwargs(),
        )
        self.cache.store(metadata, fetched.body)
        if fetched.status_code != 200:
            return ProviderResult.unavailable(f"TennisMyLife index returned HTTP {fetched.status_code}", metadata)
        return self._parse_index(fetched.body, metadata)

    @staticmethod
    def _parse_index(body: bytes, metadata: SourceResponseMetadata) -> ProviderResult:
        try:
            payload = json.loads(body)
            files = payload["files"]
            if not isinstance(files, list):
                raise TypeError("data-files payload lacks a files list")
            frame = pl.DataFrame(files) if files else pl.DataFrame(schema={"name": pl.String, "url": pl.String})
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            return ProviderResult(ProviderStatus.DEGRADED, metadata, None, f"index schema drift: {exc}")
        enriched = replace(metadata, schema_hash=dataframe_schema_hash(frame))
        return ProviderResult(ProviderStatus.AVAILABLE, enriched, frame)

    def _resolve_file_url(self, filename: str, *, force: bool = False) -> ProviderResult | str:
        """Return the real URL for `filename` from the live index, or a ProviderResult failure."""
        index = self.data_files_index(force=force)
        if not index.available or index.frame is None:
            return index
        matches = index.frame.filter(pl.col("name") == filename)
        if matches.is_empty():
            return ProviderResult.unavailable(f"TennisMyLife index has no file named {filename!r}")
        return str(matches["url"][0])

    @staticmethod
    def _match_filename(tour: Tour, year: int, kind: MatchKind) -> str:
        if kind == "qualifying":
            if tour != "atp":
                raise ValueError("TennisMyLife only publishes qualifying files for ATP")
            return f"atp_quali/{year}_atp_quali.csv"
        if kind == "challenger":
            if tour != "atp":
                raise ValueError("TennisMyLife only publishes challenger files for ATP")
            return f"{year}_challenger.csv"
        return f"{year}.csv" if tour == "atp" else f"{year}_wta.csv"

    def year_matches(
        self, tour: Tour, year: int, *, kind: MatchKind = "main", force: bool = False
    ) -> ProviderResult:
        try:
            filename = self._match_filename(tour, year, kind)
        except ValueError as exc:
            return ProviderResult.unavailable(str(exc))
        return self._fetch_matches_csv(filename, sport_scope=f"{tour}_{kind}_{year}", force=force)

    def ongoing_matches(self, tour: Literal["atp", "wta", "challenger"], *, force: bool = False) -> ProviderResult:
        filename = {
            "atp": "ongoing_tourneys.csv",
            "wta": "wta_ongoing_tourneys.csv",
            "challenger": "challenger_ongoing_tourneys.csv",
        }[tour]
        return self._fetch_matches_csv(filename, sport_scope=f"{tour}_ongoing", force=force)

    def _fetch_matches_csv(self, filename: str, *, sport_scope: str, force: bool = False) -> ProviderResult:
        endpoint = f"matches_{sport_scope}"
        parameters = {"filename": filename}
        cached = self.cache.latest_success(self.provider_id, "tennis", endpoint, parameters)
        if cached is not None and not force:
            try:
                result = self._parse_matches(cached.read_bytes(), cached.metadata)
                self.cache.record_parse_result(
                    result.metadata or cached.metadata,
                    parser_version=PARSER_VERSION,
                    status=result.status.value,
                    schema_hash=(result.metadata or cached.metadata).schema_hash,
                    reason=result.reason,
                )
                return result
            except Exception as exc:  # noqa: BLE001 -- cache boundary
                return ProviderResult(ProviderStatus.DEGRADED, cached.metadata, None, f"cached parse failed: {exc}")

        resolved = self._resolve_file_url(filename, force=force)
        if isinstance(resolved, ProviderResult):
            return resolved
        url = resolved
        try:
            fetched = self.http.get(url)
        except Exception as exc:  # noqa: BLE001 -- external transport boundary
            return ProviderResult.unavailable(f"TennisMyLife request failed for {filename}: {exc}")
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
            source_version=f"tennismylife:{filename}",
            source_grade=SourceGrade.B,
            **self.rights.metadata_kwargs(),
        )
        self.cache.store(metadata, fetched.body)
        if fetched.status_code != 200:
            return ProviderResult.unavailable(f"TennisMyLife returned HTTP {fetched.status_code} for {filename}", metadata)
        result = self._parse_matches(fetched.body, metadata)
        self.cache.record_parse_result(
            result.metadata or metadata,
            parser_version=PARSER_VERSION,
            status=result.status.value,
            schema_hash=(result.metadata or metadata).schema_hash,
            reason=result.reason,
        )
        return result

    @staticmethod
    def _parse_matches(body: bytes, metadata: SourceResponseMetadata) -> ProviderResult:
        try:
            frame = pl.read_csv(io.BytesIO(body), infer_schema_length=10000, null_values=["", "NA"])
        except Exception as exc:  # noqa: BLE001 -- parser boundary
            return ProviderResult(ProviderStatus.DEGRADED, metadata, None, f"TennisMyLife CSV parse failed: {exc}")
        missing = sorted(REQUIRED_MATCH_COLUMNS - set(frame.columns))
        if missing:
            return ProviderResult(ProviderStatus.DEGRADED, metadata, None, f"TennisMyLife schema drift: missing {missing}")
        enriched = replace(metadata, schema_hash=dataframe_schema_hash(frame))
        return ProviderResult(ProviderStatus.AVAILABLE, enriched, frame, "NO_MATCHES" if frame.is_empty() else None)
