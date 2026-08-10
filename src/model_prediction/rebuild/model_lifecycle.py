"""Sport-pluggable train/compare registry for the `rebuild-model` CLI.

Same seam pattern as `data_foundation.py`: one shared Protocol plus a
per-sport registry. Every sport currently reports `NOT_IMPLEMENTED`.

Real training code already exists on `main` (e.g.
`mlb_shadow_pipeline.py::train_through`), but it operates on an
already-built `polars.DataFrame` of point-in-time features -- wiring it up
honestly requires a real feature-assembly source (the rebuild `collect`/
`build_features` stages, or the still-not-yet-transplanted per-sport data
foundations), not a CLI-level stub. That real wiring is deliberately left
for the sport-specific curation branches (see `data_foundation.py`'s
docstring for the same reasoning applied to data ingestion) rather than
rushed here without real validation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

SUPPORTED_MODEL_SPORTS = ("mlb", "nba", "wnba", "nfl", "soccer", "tennis", "esports", "kbo", "npb")


class ModelLifecycleError(RuntimeError):
    """Raised when a model-lifecycle operation cannot honestly proceed."""


class ModelLifecycle(Protocol):
    def train(self, **kwargs: Any) -> dict[str, Any]: ...

    def compare(self, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True)
class _NotImplementedLifecycle:
    sport: str
    status: str

    def train(self, **kwargs: Any) -> dict[str, Any]:
        return self._report("train")

    def compare(self, **kwargs: Any) -> dict[str, Any]:
        return self._report("compare")

    def _report(self, operation: str) -> dict[str, Any]:
        return {
            "sport": self.sport,
            "operation": operation,
            "status": "NOT_IMPLEMENTED",
            "reason": (
                f"no model lifecycle is registered for {self.sport} yet "
                f"(config/rebuild.yaml sports.{self.sport}.status={self.status!r}); "
                "see model_lifecycle.py's module docstring"
            ),
        }


def build_model_lifecycle(sport: str, data_root: str | Path, *, status: str) -> ModelLifecycle:
    if sport not in SUPPORTED_MODEL_SPORTS:
        raise ModelLifecycleError(f"unsupported sport: {sport}")
    del data_root  # unused until a real lifecycle is registered for some sport
    return _NotImplementedLifecycle(sport=sport, status=status)
