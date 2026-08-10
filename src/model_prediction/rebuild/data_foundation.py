"""Sport-pluggable backfill/audit registry for the `rebuild-data` CLI.

Mirrors `sport_adapter.py`'s `SportAdapter`/`build_adapter` pattern, but for
the data-ingestion half of the pipeline instead of the model/decision half:
one shared Protocol plus a per-sport registry, so a future curated
`rebuild/<sport>-v1-next` branch's only job is to implement one class here
and register it -- not touch `data_cli.py` itself.

Every sport is currently a `_NotImplementedFoundation`. A real, working
`backfill`/`audit` implementation existed per-sport (mlb_v3, wnba, nfl,
soccer) on now-archived branches (`origin/rebuild/<sport>-v1` /
`archive/pre-runtime-cutover/<sport>-*`) -- PR #5 already promoted their
shared *provider client* dependencies (`providers/`) onto `main`, but
deliberately left the per-sport ingestion/orchestration layer (each
branch's `<sport>/foundation.py` plus `normalize.py`/`store.py`/`audit.py`)
behind for its own dedicated, carefully-reviewed transplant rather than a
rushed bulk import here. See docs/rebuild/README.md's roadmap section.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

SUPPORTED_DATA_SPORTS = ("mlb", "nba", "wnba", "nfl", "soccer", "tennis", "esports", "kbo", "npb")


class DataFoundationError(RuntimeError):
    """Raised when a data-foundation operation cannot honestly proceed."""


class DataFoundation(Protocol):
    def backfill(self, **kwargs: Any) -> dict[str, Any]: ...

    def audit(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class _NotImplementedFoundation:
    sport: str
    status: str

    def backfill(self, **kwargs: Any) -> dict[str, Any]:
        return self._report("backfill")

    def audit(self, **kwargs: Any) -> dict[str, Any]:
        return self._report("audit")

    def _report(self, operation: str) -> dict[str, Any]:
        return {
            "sport": self.sport,
            "operation": operation,
            "status": "NOT_IMPLEMENTED",
            "reason": (
                f"no data foundation is registered for {self.sport} yet "
                f"(config/rebuild.yaml sports.{self.sport}.status={self.status!r}); "
                "see data_foundation.py's module docstring"
            ),
        }


def build_data_foundation(sport: str, data_root: str | Path, *, status: str) -> DataFoundation:
    if sport not in SUPPORTED_DATA_SPORTS:
        raise DataFoundationError(f"unsupported sport: {sport}")
    del data_root  # unused until a real foundation is registered for some sport
    return _NotImplementedFoundation(sport=sport, status=status)
