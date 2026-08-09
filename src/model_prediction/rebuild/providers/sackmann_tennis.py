"""Jeff Sackmann ATP/WTA historical tennis data -- currently SOURCE_UNAVAILABLE.

CLAUDE.md/the rebuild plan names `github.com/JeffSackmann/tennis_atp` and
`tennis_wta` as the intended free historical source (match results,
rankings, player identities, match stats). Verified live before writing any
fetch code (per this project's own "don't pretend a brittle undocumented
source is guaranteed" rule): as of this module's writing,

    GET https://api.github.com/users/JeffSackmann/repos  -> only
    `tennis_MatchChartingProject` (point-by-point charting, not the
    match-results/rankings CSVs this provider needs). `tennis_atp` and
    `tennis_wta` both 404 directly (github.com/JeffSackmann/tennis_atp),
    and the account (created 2010, updated as recently as 2026-06-15, so
    not abandoned) has only 1 public repo total.

Several third-party mirrors exist (e.g. a GitHub search turns up an
"archival mirror of Jeff Sackmann's tennis datasets" under a different
owner, licensed CC BY-NC-SA 4.0 -- noncommercial only), but none has been
verified here for completeness, currency, or a trustworthy chain of
custody back to the original data. Silently pointing this provider at an
unverified mirror would be exactly the kind of "looks done, quietly wrong"
mistake this codebase explicitly hunts for elsewhere -- it fails closed
instead. If a verified replacement source is found, replace this module's
body (not just its URL) with a real implementation and update this
docstring's evidence trail, don't just swap a constant.

Fail-closed policy, matching `statsbomb.py`'s POLICY_BLOCKED pattern for a
known-unusable source: every method returns UNAVAILABLE with an explicit
reason, no network request is made, and `ProviderResult.available` is
always False.
"""

from __future__ import annotations

from typing import Any

from .base import ProviderResult
from .rights import SourceRightsProfile

SACKMANN_TENNIS_RIGHTS = SourceRightsProfile(
    source_asset="Jeff Sackmann ATP/WTA match results, rankings, and player identities",
    provider_chain="github.com/JeffSackmann (tennis_atp / tennis_wta -- currently unreachable)",
    license_id="sackmann-tennis-source-unverified",
    license_url="https://github.com/JeffSackmann",
    attribution_required=True,
    attribution_text="Jeff Sackmann tennis data",
    subscription_required=False,
    subscription_scope="none",
    upstream_rights_status="unresolved",
    commercial_use_status="unresolved",
    use_scope="research_shadow_only",
    production_allowed=False,
    policy_note=(
        "The canonical source repositories are not currently reachable "
        "(verified via GitHub API, see module docstring). No mirror has "
        "been verified as trustworthy or currently-maintained. Collection "
        "is disabled until a real, verified source is wired in."
    ),
)

SOURCE_UNAVAILABLE_REASON = (
    "SOURCE_UNAVAILABLE: github.com/JeffSackmann/tennis_atp and tennis_wta "
    "are not reachable (verified via GitHub API; see this module's "
    "docstring). No verified replacement source is wired in. No network "
    "request was made."
)


class SackmannTennisProvider:
    """Intentionally non-networking until a real, verified source exists."""

    provider_id = "sackmann_tennis"
    rights = SACKMANN_TENNIS_RIGHTS

    def matches(self, *, tour: str, season: int, **_kwargs: Any) -> ProviderResult:
        if tour.lower() not in {"atp", "wta"}:
            return ProviderResult.unavailable(f"unsupported tour: {tour}")
        return ProviderResult.unavailable(SOURCE_UNAVAILABLE_REASON)

    def rankings(self, *, tour: str, **_kwargs: Any) -> ProviderResult:
        if tour.lower() not in {"atp", "wta"}:
            return ProviderResult.unavailable(f"unsupported tour: {tour}")
        return ProviderResult.unavailable(SOURCE_UNAVAILABLE_REASON)

    def players(self, *, sport: str, season: int, **_kwargs: Any) -> ProviderResult:
        return ProviderResult.unavailable(SOURCE_UNAVAILABLE_REASON)

    def schedule(self, *, sport: str, season: int, **_kwargs: Any) -> ProviderResult:
        return ProviderResult.unavailable(SOURCE_UNAVAILABLE_REASON)
