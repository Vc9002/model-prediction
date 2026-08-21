"""Free, no-signup data-source policy (model_improvements.md section 4).

Codifies the source hierarchy and the per-league default source stack so a
new dependency can be checked against policy in code instead of only in the
markdown roadmap. The default feature stack must not require a new account,
API key, paid subscription, or browser login; paid/keyed sources are
optional-upgrade-only and must be explicitly marked as such.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class SourceTier(IntEnum):
    """Preference order: lower number is preferred. Mirrors section 4's list."""

    CACHED_REPOSITORY_DATA = 1
    OFFICIAL_PUBLIC_ENDPOINT = 2
    VERSIONED_RELEASE = 3
    KEYLESS_OPEN_DATA_API = 4
    THROTTLED_SCRAPER = 5
    PAID_OR_KEYED_OPTIONAL = 6


@dataclass(frozen=True)
class SourceSpec:
    name: str
    tier: SourceTier
    requires_key: bool
    leagues: tuple[str, ...]
    notes: str = ""


# The in-use, no-signup default stack. Keep in sync with model_improvements.md
# section 4's "Default source stack" and "League assignment" tables.
DEFAULT_SOURCES: dict[str, SourceSpec] = {
    "espn": SourceSpec(
        "ESPN public scoreboard/site API",
        SourceTier.OFFICIAL_PUBLIC_ENDPOINT,
        False,
        ("mlb", "nba", "wnba", "nfl", "soccer", "tennis"),
        "Undocumented and unsupported; schemas can change.",
    ),
    "polymarket_us": SourceSpec(
        "Polymarket US public gateway",
        SourceTier.OFFICIAL_PUBLIC_ENDPOINT,
        False,
        ("mlb", "nba", "wnba", "nfl", "soccer", "tennis", "esports", "kbo", "npb"),
        "Public read data only; trading/private-portfolio endpoints require authentication.",
    ),
    "open_meteo": SourceSpec(
        "Open-Meteo historical/previous-runs forecast API",
        SourceTier.KEYLESS_OPEN_DATA_API,
        False,
        ("mlb", "nfl", "kbo", "npb"),
        "Free endpoint is non-commercial, rate-limited, no uptime guarantee.",
    ),
    "sportsdataverse": SourceSpec(
        "SportsDataverse hoopR/wehoop GitHub releases",
        SourceTier.VERSIONED_RELEASE,
        False,
        ("nba", "wnba"),
        "Derived pipeline; release lag and upstream schema changes must be monitored.",
    ),
    "nflverse": SourceSpec(
        "nflverse play-by-play GitHub releases",
        SourceTier.VERSIONED_RELEASE,
        False,
        ("nfl",),
        "Dataset-specific licenses/attribution; in-season release lag.",
    ),
    "pybaseball": SourceSpec(
        "Baseball Savant via pybaseball/direct CSV",
        SourceTier.THROTTLED_SCRAPER,
        False,
        ("mlb",),
        "Not a contractual API; cache and throttle.",
    ),
    "bo3": SourceSpec(
        "BO3 public website data endpoint",
        SourceTier.THROTTLED_SCRAPER,
        False,
        ("esports",),
        "No published stable API contract; cache, hash, attribute, keep replaceable.",
    ),
    "oracles_elixir": SourceSpec(
        "Oracle's Elixir public downloads",
        SourceTier.VERSIONED_RELEASE,
        False,
        ("esports",),
        "Game-level rows; must prevent later-series games leaking into earlier predictions.",
    ),
    "kbo_official": SourceSpec(
        "Official KBO schedule/results",
        SourceTier.OFFICIAL_PUBLIC_ENDPOINT,
        False,
        ("kbo",),
        "Not a promised bulk API; cache and hash extractions.",
    ),
    "npb_official": SourceSpec(
        "Official NPB English calendar",
        SourceTier.OFFICIAL_PUBLIC_ENDPOINT,
        False,
        ("npb",),
        "October excluded (mixes regular season and postseason).",
    ),
    "the_odds_api": SourceSpec(
        "The Odds API",
        SourceTier.PAID_OR_KEYED_OPTIONAL,
        True,
        ("soccer",),
        "Keyed; used only for soccer score lookback, not as a default MLB/NBA/WNBA/NFL dependency.",
    ),
    "sportsdataio": SourceSpec(
        "SportsDataIO",
        SourceTier.PAID_OR_KEYED_OPTIONAL,
        True,
        (),
        "Deliberately excluded from the default build.",
    ),
}

# Sources whose free-use terms explicitly forbid this project's use case, or
# that are excluded on principle -- never silently substitute one of these.
EXCLUDED_SOURCES = frozenset(
    {
        "riot_developer_api",  # requires an account/key as a default dependency
        "liquipedia",  # published free-use policy excludes betting-related projects
        "sportradar",
        "stats_perform",
        "pff",
        "second_spectrum",
    }
)


def is_default_stack_compliant(source_key: str) -> bool:
    """True if a source can be part of the default (no-signup) feature stack."""
    if source_key in EXCLUDED_SOURCES:
        return False
    spec = DEFAULT_SOURCES.get(source_key)
    if spec is None:
        return False
    return not spec.requires_key


def assert_no_unapproved_paid_source(source_keys: list[str]) -> None:
    """Raise if any source in ``source_keys`` requires a key/account and isn't
    explicitly registered as an approved optional-paid upgrade.

    Call this from a league's default forecast path (not from an explicitly
    opt-in paid-source code path) to fail closed on an undeclared dependency.
    """
    for key in source_keys:
        if key in EXCLUDED_SOURCES:
            raise ValueError(f"source {key!r} is explicitly excluded by policy, see EXCLUDED_SOURCES")
        spec = DEFAULT_SOURCES.get(key)
        if spec is None:
            raise ValueError(f"source {key!r} is not registered in DEFAULT_SOURCES or EXCLUDED_SOURCES")
        if spec.requires_key and spec.tier is not SourceTier.PAID_OR_KEYED_OPTIONAL:
            raise ValueError(f"source {key!r} requires a key but is not marked PAID_OR_KEYED_OPTIONAL")


def sources_for_league(league: str) -> list[SourceSpec]:
    league = league.lower()
    return sorted(
        (spec for spec in DEFAULT_SOURCES.values() if league in spec.leagues),
        key=lambda spec: spec.tier,
    )
