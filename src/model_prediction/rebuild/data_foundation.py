"""Sport-pluggable backfill/audit registry for the `rebuild-data` CLI.

Mirrors `sport_adapter.py`'s `SportAdapter`/`build_adapter` pattern, but for
the data-ingestion half of the pipeline instead of the model/decision half:
one shared Protocol plus a per-sport registry, so a future curated
`rebuild/<sport>-v1-next` branch's only job is to implement one class and
register it here -- not touch `data_cli.py` itself.

A real, working `backfill`/`audit` implementation existed per-sport (mlb_v3,
wnba, nfl, soccer) on now-archived branches (`origin/rebuild/<sport>-v1` /
`archive/pre-runtime-cutover/<sport>-*`) -- PR #5 already promoted their
shared *provider client* dependencies (`providers/`) onto `main`. `mlb`,
`wnba`, `nfl`, `soccer`, and `tennis` are now registered for real (see
`mlb_v3/cli_adapter.py`, `wnba/cli_adapter.py`, `nfl/cli_adapter.py`,
`soccer/cli_adapter.py`, `tennis/cli_adapter.py`); every other sport
remains a `_NotImplementedFoundation` pending its own dedicated,
carefully-reviewed transplant rather than a rushed bulk import here. Note:
WNBA's transplant deliberately excludes the source branch's
feature-engineering/model-baseline modules
(features.py/horizon_builder.py/baselines.py) -- those are a different
concern from backfill/audit data ingestion and are left for a separate
rebuild-model decision. Tennis is the one exception to "transplant": the
archived `origin/rebuild/tennis-v1` branch predates the TennisMyLife/ESPN
provider replacement and is a fail-closed policy stub, not real ingestion
code, so `tennis/` is new authorship built directly against the real
providers already on `main`, not a port.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol, Self

SUPPORTED_DATA_SPORTS = ("mlb", "nba", "wnba", "nfl", "soccer", "tennis", "esports", "kbo", "npb")


class DataFoundationError(RuntimeError):
    """Raised when a data-foundation operation cannot honestly proceed."""


class DataFoundation(Protocol):
    """A context manager: implementations that own network/HTTP resources
    (e.g. MLBV3DataFoundation) release them on __exit__."""

    def __enter__(self) -> Self: ...

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None: ...

    def backfill(self, **kwargs: Any) -> dict[str, Any]: ...

    def audit(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class _NotImplementedFoundation:
    sport: str
    status: str

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        return None

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


def build_data_foundation(sport: str, data_root: str | Path, *, status: str, repo_root: str | Path) -> DataFoundation:
    if sport not in SUPPORTED_DATA_SPORTS:
        raise DataFoundationError(f"unsupported sport: {sport}")
    if sport == "mlb":
        from .mlb_v3.cli_adapter import MLBV3DataFoundation

        return MLBV3DataFoundation(Path(data_root), repo_root=Path(repo_root))
    if sport == "wnba":
        from .wnba.cli_adapter import WNBADataFoundation

        return WNBADataFoundation(Path(data_root), repo_root=Path(repo_root))
    if sport == "nfl":
        from .nfl.cli_adapter import NFLDataFoundation

        return NFLDataFoundation(Path(data_root), repo_root=Path(repo_root))
    if sport == "soccer":
        from .soccer.cli_adapter import SoccerDataFoundation

        return SoccerDataFoundation(Path(data_root), repo_root=Path(repo_root))
    if sport == "tennis":
        from .tennis.cli_adapter import TennisDataFoundation

        return TennisDataFoundation(Path(data_root), repo_root=Path(repo_root))
    return _NotImplementedFoundation(sport=sport, status=status)
