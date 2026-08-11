"""WNBA free-data foundation plus recovered research feature path.

`features.py`/`horizon_builder.py` were recovered from the archived
`origin/rebuild/wnba-v1` branch per
`docs/model_audit/models/WNBA_ARCHIVED_BASELINES.md`'s RECOVER verdict
(rolling Four-Factors team form, PIT-safe, dependencies verified
byte-identical to what they were written against). They remain
research-only -- `horizon_builder.py`'s own
`_assert_research_source_provenance` hard-fails on any row that isn't
`capture_time_only`/`commercial_use_status="unresolved"`/
`production_allowed=False`, so this path cannot itself produce anything
that would pass as production-cleared until the upstream
SportsDataverse/ESPN commercial-use question is resolved.

`baselines.py` was **not** recovered -- it is a comparison/evaluation
harness (`AUDIT_ONLY` verdict, not `RECOVER`), still rights-blocked
(`production_allowed: False`), and not merged here; see the archive-review
doc for what it contains. Nothing in this package imports it.

Feature/model *promotion* work still belongs behind
`model_lifecycle.py`'s rebuild-model seam, not this data-foundation
package -- recovering the feature-computation code is not the same
decision as wiring a trained challenger."""

from .audit import audit_wnba_season
from .features import build_team_form_snapshot
from .foundation import WNBAFoundation
from .horizon_builder import (
    WNBAFeatureBuildResult,
    build_wnba_live_features,
    build_wnba_replay_features,
)
from .normalize import normalize_wnba_table
from .pit import eligible_prior_team_games

__all__ = [
    "WNBAFeatureBuildResult",
    "WNBAFoundation",
    "audit_wnba_season",
    "build_team_form_snapshot",
    "build_wnba_live_features",
    "build_wnba_replay_features",
    "eligible_prior_team_games",
    "normalize_wnba_table",
]
