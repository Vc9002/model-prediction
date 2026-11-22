"""College Football Structural v6.1 research challenger (cfb-structural-v6.1). INERT: nothing routes to it.

v6.1 is frozen v6 plus ONE walk-forward correction on the total: the mean residual (actual total minus the v6
calibrated total) over a trailing window of games that finished before the decision. The window, the 5 hour result
buffer and the day-window fallback all come from the hash-verified artifact (``config/models/cfb-structural-v6.1.json``,
built from ``outputs/ncaaf/cfb_v61_spec.json``); nothing here is hardcoded. Spreads and every other v6 behaviour are
unchanged: the class overrides ``calibrate_means`` only, so the v6 joint simulation engine prices it.

The tracker enters by operator instruction, not because a test passed: it removes much of v6's over lean but adds no
MAE gain and does not beat the closing line (``outputs/ncaaf/cfb_v61_comparison.md``). No source beyond the tracker
(line movement, weather, QB) passed the pinned point-in-time rule, so ``components`` is empty.

Not a champion, not in ``config/production.yaml`` or ``config/model.yaml``, not imported by ``learned_forward.py``
or any CLI module (``tests/test_cfb_structural_v61.py`` proves this in a clean subprocess). Promotion would be a
separate governed decision.

Offset semantics (identical to ``scripts/cfb_totals_lib.py::tracker_offsets``, which produced the parity fixture):
``cs = [0, cumsum(residuals in kickoff order)]``, ``hi = bisect_right(starts, T - buffer)``, a game counts only if
``start + buffer <= T`` (inclusive, v6's D-RES-BUFFER); ``('days', N)`` takes eligible games with ``start >= T - N
days`` and falls back to the last ``fallback_games`` eligible games when fewer than ``min_day_games`` qualify;
``('games', N)`` takes the last N; offset = ``(cs[hi] - cs[lo]) / (hi - lo)`` and 0.0 when nothing is eligible. The
cumulative-sum form is kept on purpose: a plain slice mean differs at the 1e-14 level and would break bit-exact parity.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from ..config import PROJECT_ROOT
from ..production_registry import compute_artifact_hash
from .cfb_structural_v6 import (
    MIN_CALIBRATED_TOTAL,
    CFBStructuralV6Model,
    load_cfb_structural_v6_artifact,
)

CFB_V61_MODEL_VERSION = "cfb-structural-v6.1"
CFB_V61_ARTIFACT_PATH = PROJECT_ROOT / "config" / "models" / f"{CFB_V61_MODEL_VERSION}.json"
_TRACKER_KEYS = frozenset(
    {"window_kind", "window_size", "result_buffer_hours", "day_window_min_games", "day_window_fallback_games"}
)
_WINDOW_KINDS = ("days", "games")


def load_cfb_structural_v61_artifact(path: str | Path | None = None) -> dict[str, Any]:
    artifact_path = Path(path) if path is not None else CFB_V61_ARTIFACT_PATH
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    if payload.get("model_version") != CFB_V61_MODEL_VERSION:
        raise ValueError(f"cfb v6.1 artifact model_version mismatch: {payload.get('model_version')!r}")
    if payload.get("artifact_hash") != compute_artifact_hash(payload):
        raise ValueError(f"cfb v6.1 artifact_hash mismatch for {artifact_path}")
    return payload


def _validate_tracker(tracker: Mapping[str, Any]) -> dict[str, Any]:
    if set(tracker) != _TRACKER_KEYS:
        raise ValueError(f"tracker block must carry exactly {sorted(_TRACKER_KEYS)}")
    kind = str(tracker["window_kind"])
    if kind not in _WINDOW_KINDS:
        raise ValueError(f"unknown tracker window kind {kind!r}")
    out = {
        "window_kind": kind,
        "window_size": int(tracker["window_size"]),
        "result_buffer_hours": float(tracker["result_buffer_hours"]),
        "day_window_min_games": int(tracker["day_window_min_games"]),
        "day_window_fallback_games": int(tracker["day_window_fallback_games"]),
    }
    if out["window_size"] <= 0 or out["result_buffer_hours"] < 0:
        raise ValueError("tracker window size must be positive and the buffer non-negative")
    if out["day_window_min_games"] < 0 or out["day_window_fallback_games"] <= 0:
        raise ValueError("tracker fallback settings are out of range")
    return out


class ResidualFeed:
    """Kickoff-ordered (start, residual) history with the cumulative sum the tracker averages over.

    ``start_s`` are epoch seconds sorted ascending (ties by event id, the caller's order), ``resid`` the
    residual ``actual total - v6 calibrated total`` of each game. Built once; ``offset_at`` is then O(log n).
    """

    def __init__(self, start_s: Sequence[int] | np.ndarray, resid: Sequence[float] | np.ndarray) -> None:
        starts = np.asarray(start_s, np.int64)
        res = np.asarray(resid, float)
        if starts.shape != res.shape or starts.ndim != 1:
            raise ValueError("feed starts and residuals must be one-dimensional and the same length")
        if len(starts) > 1 and np.any(np.diff(starts) < 0):
            raise ValueError("feed must be sorted by kickoff")
        self.starts = starts
        self.cs = np.concatenate([[0.0], np.cumsum(res)])

    @classmethod
    def from_games(cls, games: Iterable[tuple[datetime, float, float]]) -> ResidualFeed:
        """Build from (kickoff, actual total, v6 calibrated total) triples in any order."""
        ordered = sorted(games, key=lambda g: g[0])
        starts = []
        for kickoff, _, _ in ordered:
            if kickoff.tzinfo is None:
                raise ValueError("feed kickoffs must be timezone-aware")
            starts.append(int(kickoff.timestamp()))
        return cls(starts, np.array([actual - v6 for _, actual, v6 in ordered], float))

    def __len__(self) -> int:
        return len(self.starts)

    def offset_at(self, decision_s: int, tracker: Mapping[str, Any]) -> tuple[float, int]:
        """(offset, games averaged) at decision time ``decision_s`` (epoch seconds)."""
        cfg = _validate_tracker(tracker)
        buffer_s = int(cfg["result_buffer_hours"] * 3600)
        t = np.int64(decision_s)
        hi = int(np.searchsorted(self.starts, t - buffer_s, side="right"))
        if cfg["window_kind"] == "games":
            lo = max(0, hi - cfg["window_size"])
        else:
            lo = int(np.searchsorted(self.starts, t - cfg["window_size"] * 86400, side="left"))
            lo = min(lo, hi)
            if (hi - lo) < cfg["day_window_min_games"]:
                lo = max(0, hi - cfg["day_window_fallback_games"])
        n = hi - lo
        if n <= 0:
            return 0.0, 0
        return float((self.cs[hi] - self.cs[lo]) / n), n


def level_offset(
    start_s: Sequence[int] | np.ndarray,
    resid: Sequence[float] | np.ndarray,
    decision_s: int,
    tracker: Mapping[str, Any],
) -> tuple[float, int]:
    """Pure form of the tracker: the offset at ``decision_s`` for a kickoff-sorted (start, residual) history."""
    return ResidualFeed(start_s, resid).offset_at(decision_s, tracker)


class CFBStructuralV61Model(CFBStructuralV6Model):
    """Frozen v6 with the calibrated total shifted by the walk-forward level offset (default offset 0.0 = v6)."""

    version: str = CFB_V61_MODEL_VERSION

    def __init__(self, *, tracker: Mapping[str, Any], **v6_params: Any) -> None:
        super().__init__(**v6_params)
        self.tracker = _validate_tracker(tracker)
        self.offset = 0.0

    @classmethod
    def from_artifact(cls, artifact: Mapping[str, Any], **overrides: Any) -> CFBStructuralV61Model:
        if artifact.get("artifact_hash") != compute_artifact_hash(dict(artifact)):
            raise ValueError("cfb v6.1 artifact_hash mismatch")
        base = load_cfb_structural_v6_artifact()
        if base["artifact_hash"] != artifact["base_artifact_hash"]:
            raise ValueError("cfb v6.1 artifact was built on a different frozen v6 artifact")
        if artifact.get("components"):
            raise ValueError("this port implements v6.1 = v6 + tracker only; the artifact lists components")
        built = super().from_artifact(base, tracker=_validate_tracker(artifact["tracker"]), **overrides)
        return built  # type: ignore[return-value]

    def set_offset(self, offset: float) -> None:
        """Offset the next ``calibrate_means`` calls add to the calibrated total (the model holds no other state)."""
        self.offset = float(offset)

    def set_decision(self, feed: ResidualFeed, decision_time: datetime) -> tuple[float, int]:
        """Set the offset from ``feed`` as of ``decision_time`` (timezone-aware); returns (offset, games averaged)."""
        if decision_time.tzinfo is None:
            raise ValueError("decision_time must be timezone-aware")
        offset, n = feed.offset_at(int(decision_time.timestamp()), self.tracker)
        self.offset = offset
        return offset, n

    def calibrate_means(self, projected_margin_home: float, projected_total: float) -> tuple[float, float]:
        margin, _ = super().calibrate_means(projected_margin_home, projected_total)
        total = (
            self.total_calibration["intercept"]
            + self.total_calibration["slope"] * projected_total
            + self.offset
        )
        return margin, max(MIN_CALIBRATED_TOTAL, total)


def cfb_structural_v61_model() -> CFBStructuralV61Model:
    return CFBStructuralV61Model.from_artifact(load_cfb_structural_v61_artifact())
