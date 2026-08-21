"""MLB Pitch Arsenal Feature Engineering and Fixed-Size Tensor Representation.

Point-In-Time (PIT) feature representations for pitcher repertoires:
1. Canonical 8-pitch types (4-seam, sinker, cutter, slider, sweeper, changeup, curveball, splitter).
2. Per-pitch metrics: usage rate, average velocity, horizontal break (pfx_x),
   induced vertical break (pfx_z), whiff%, and CSW%.
3. PitchArsenal: composite repertoire representation with repertoire entropy,
   max velocity, stuff proxy, and pitch-mix distribution.
4. PitchArsenalTensor: aggregate repertoire metrics into a fixed-size 1D / 2D
   tensor for machine learning models and discrete-event simulation input.
5. Strict PIT sequential tracking with credibility shrinkage toward empirical league priors.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

# Canonical 8 pitch types in standardized ordering
CANONICAL_PITCH_TYPES: tuple[str, ...] = (
    "4-seam",
    "sinker",
    "cutter",
    "slider",
    "sweeper",
    "changeup",
    "curveball",
    "splitter",
)

METRIC_NAMES: tuple[str, ...] = (
    "usage_rate",
    "velocity",
    "horizontal_break",
    "vertical_break",
    "whiff_rate",
    "csw_rate",
)

SUMMARY_METRIC_NAMES: tuple[str, ...] = (
    "total_pitches_log",
    "max_velocity",
    "weighted_velocity",
    "overall_whiff_rate",
    "overall_csw_rate",
    "repertoire_entropy",
    "fastball_usage",
    "breaking_ball_usage",
    "offspeed_usage",
    "stuff_plus_proxy",
)

# Statcast / MLB Pitch Type Abbreviation to Canonical Mapping
_PITCH_TYPE_SYNONYMS: dict[str, str] = {
    # 4-seam fastball
    "ff": "4-seam",
    "fa": "4-seam",
    "4-seam": "4-seam",
    "four_seam": "4-seam",
    "four-seam": "4-seam",
    "four-seamer": "4-seam",
    "4seam": "4-seam",
    # Sinker / 2-seam
    "si": "sinker",
    "sinker": "sinker",
    "2-seam": "sinker",
    "two_seam": "sinker",
    "two-seam": "sinker",
    "two-seamer": "sinker",
    "2seam": "sinker",
    # Cutter
    "fc": "cutter",
    "cutter": "cutter",
    "cut": "cutter",
    "cut-fastball": "cutter",
    # Slider
    "sl": "slider",
    "slider": "slider",
    # Sweeper
    "st": "sweeper",
    "sv": "sweeper",
    "sweeper": "sweeper",
    "sweep": "sweeper",
    # Changeup
    "ch": "changeup",
    "changeup": "changeup",
    "change": "changeup",
    "change-up": "changeup",
    # Curveball
    "cu": "curveball",
    "kc": "curveball",
    "cs": "curveball",
    "curveball": "curveball",
    "curve": "curveball",
    "knuckle-curve": "curveball",
    "slow-curve": "curveball",
    # Splitter
    "fs": "splitter",
    "fo": "splitter",
    "splitter": "splitter",
    "split": "splitter",
    "split-finger": "splitter",
    "forkball": "splitter",
}

# MLB Empirical League Average Benchmarks (Statcast calibration)
LEAGUE_PITCH_BENCHMARKS: dict[str, dict[str, float]] = {
    "4-seam": {
        "usage_rate": 0.325,
        "velocity": 94.2,
        "horizontal_break": -3.5,
        "vertical_break": 15.6,
        "whiff_rate": 0.224,
        "csw_rate": 0.272,
    },
    "sinker": {
        "usage_rate": 0.160,
        "velocity": 93.6,
        "horizontal_break": -7.8,
        "vertical_break": 8.2,
        "whiff_rate": 0.151,
        "csw_rate": 0.260,
    },
    "cutter": {
        "usage_rate": 0.080,
        "velocity": 89.2,
        "horizontal_break": 1.6,
        "vertical_break": 8.0,
        "whiff_rate": 0.238,
        "csw_rate": 0.274,
    },
    "slider": {
        "usage_rate": 0.185,
        "velocity": 85.1,
        "horizontal_break": 4.6,
        "vertical_break": 2.1,
        "whiff_rate": 0.342,
        "csw_rate": 0.312,
    },
    "sweeper": {
        "usage_rate": 0.055,
        "velocity": 82.3,
        "horizontal_break": 12.8,
        "vertical_break": 1.2,
        "whiff_rate": 0.358,
        "csw_rate": 0.320,
    },
    "changeup": {
        "usage_rate": 0.110,
        "velocity": 85.4,
        "horizontal_break": -8.6,
        "vertical_break": 5.8,
        "whiff_rate": 0.308,
        "csw_rate": 0.281,
    },
    "curveball": {
        "usage_rate": 0.065,
        "velocity": 79.2,
        "horizontal_break": 3.2,
        "vertical_break": -9.4,
        "whiff_rate": 0.302,
        "csw_rate": 0.304,
    },
    "splitter": {
        "usage_rate": 0.020,
        "velocity": 86.4,
        "horizontal_break": -4.2,
        "vertical_break": 3.2,
        "whiff_rate": 0.352,
        "csw_rate": 0.298,
    },
}


def normalize_pitch_type(raw_type: str | None) -> str | None:
    """Normalize raw pitch code or name to canonical 8-pitch type."""
    if not raw_type:
        return None
    cleaned = str(raw_type).strip().lower()
    return _PITCH_TYPE_SYNONYMS.get(cleaned)


def _parse_timestamp(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


@dataclass(slots=True)
class PitchMetrics:
    """Per-pitch type metrics for a pitcher."""

    pitch_type: str
    count: int = 0
    usage_rate: float = 0.0
    velocity: float = 0.0
    horizontal_break: float = 0.0
    vertical_break: float = 0.0
    whiff_rate: float = 0.0
    csw_rate: float = 0.0
    swings: int = 0
    whiffs: int = 0
    called_strikes: int = 0

    def with_shrinkage(
        self,
        prior: PitchMetrics | dict[str, float] | None = None,
        prior_pitches: float = 40.0,
    ) -> PitchMetrics:
        """Shrink pitch metrics toward prior by sample size."""
        if prior is None:
            prior_dict = LEAGUE_PITCH_BENCHMARKS.get(self.pitch_type, {})
        elif isinstance(prior, PitchMetrics):
            prior_dict = {
                "velocity": prior.velocity,
                "horizontal_break": prior.horizontal_break,
                "vertical_break": prior.vertical_break,
                "whiff_rate": prior.whiff_rate,
                "csw_rate": prior.csw_rate,
                "usage_rate": prior.usage_rate,
            }
        else:
            prior_dict = prior

        if self.count <= 0 or not prior_dict:
            return PitchMetrics(
                pitch_type=self.pitch_type,
                count=self.count,
                usage_rate=prior_dict.get("usage_rate", self.usage_rate),
                velocity=prior_dict.get("velocity", self.velocity),
                horizontal_break=prior_dict.get("horizontal_break", self.horizontal_break),
                vertical_break=prior_dict.get("vertical_break", self.vertical_break),
                whiff_rate=prior_dict.get("whiff_rate", self.whiff_rate),
                csw_rate=prior_dict.get("csw_rate", self.csw_rate),
                swings=self.swings,
                whiffs=self.whiffs,
                called_strikes=self.called_strikes,
            )

        weight = self.count / (self.count + prior_pitches)
        return PitchMetrics(
            pitch_type=self.pitch_type,
            count=self.count,
            usage_rate=self.usage_rate,
            velocity=weight * self.velocity + (1.0 - weight) * prior_dict.get("velocity", self.velocity),
            horizontal_break=weight * self.horizontal_break
            + (1.0 - weight) * prior_dict.get("horizontal_break", self.horizontal_break),
            vertical_break=weight * self.vertical_break
            + (1.0 - weight) * prior_dict.get("vertical_break", self.vertical_break),
            whiff_rate=weight * self.whiff_rate
            + (1.0 - weight) * prior_dict.get("whiff_rate", self.whiff_rate),
            csw_rate=weight * self.csw_rate + (1.0 - weight) * prior_dict.get("csw_rate", self.csw_rate),
            swings=self.swings,
            whiffs=self.whiffs,
            called_strikes=self.called_strikes,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PitchArsenalTensor:
    """Fixed-size feature representation of a pitcher's pitch arsenal."""

    pitch_matrix: np.ndarray  # Shape: (8, 6)
    summary_vector: np.ndarray  # Shape: (10,)
    vector: np.ndarray  # Shape: (58,)
    feature_names: list[str] = field(default_factory=list)

    @classmethod
    def from_matrix_and_summary(
        cls,
        pitch_matrix: np.ndarray,
        summary_vector: np.ndarray,
    ) -> PitchArsenalTensor:
        mat = np.asarray(pitch_matrix, dtype=np.float32)
        if mat.shape != (len(CANONICAL_PITCH_TYPES), len(METRIC_NAMES)):
            raise ValueError(f"pitch_matrix shape must be (8, 6), got {mat.shape}")
        summary = np.asarray(summary_vector, dtype=np.float32)
        if summary.shape != (len(SUMMARY_METRIC_NAMES),):
            raise ValueError(f"summary_vector shape must be (10,), got {summary.shape}")

        flat = np.concatenate([mat.flatten(), summary])
        names: list[str] = []
        for p in CANONICAL_PITCH_TYPES:
            for m in METRIC_NAMES:
                names.append(f"{p}_{m}")
        names.extend(SUMMARY_METRIC_NAMES)

        return cls(
            pitch_matrix=mat,
            summary_vector=summary,
            vector=flat,
            feature_names=names,
        )

    def to_numpy(self) -> np.ndarray:
        """Return 1D flat tensor vector (58-dim)."""
        return np.copy(self.vector)

    @classmethod
    def from_numpy(cls, arr: np.ndarray | Sequence[float]) -> PitchArsenalTensor:
        """Reconstruct tensor from flat numpy vector."""
        a = np.asarray(arr, dtype=np.float32)
        expected_len = len(CANONICAL_PITCH_TYPES) * len(METRIC_NAMES) + len(SUMMARY_METRIC_NAMES)
        if a.shape != (expected_len,):
            raise ValueError(f"Expected array of length {expected_len}, got {a.shape}")
        n_pitch_feats = len(CANONICAL_PITCH_TYPES) * len(METRIC_NAMES)
        mat = a[:n_pitch_feats].reshape((len(CANONICAL_PITCH_TYPES), len(METRIC_NAMES)))
        summary = a[n_pitch_feats:]
        return cls.from_matrix_and_summary(mat, summary)

    def to_dict(self) -> dict[str, float]:
        """Export as dictionary mapping feature names to floats."""
        return {name: float(val) for name, val in zip(self.feature_names, self.vector, strict=True)}

    @classmethod
    def from_dict(cls, data: dict[str, float]) -> PitchArsenalTensor:
        """Build tensor from dictionary."""
        mat = np.zeros((len(CANONICAL_PITCH_TYPES), len(METRIC_NAMES)), dtype=np.float32)
        for i, p in enumerate(CANONICAL_PITCH_TYPES):
            for j, m in enumerate(METRIC_NAMES):
                mat[i, j] = float(data.get(f"{p}_{m}", 0.0))
        summary = np.zeros((len(SUMMARY_METRIC_NAMES),), dtype=np.float32)
        for k, s in enumerate(SUMMARY_METRIC_NAMES):
            summary[k] = float(data.get(s, 0.0))
        return cls.from_matrix_and_summary(mat, summary)

    def to_simulation_modifiers(self) -> dict[str, float]:
        """Convert repertoire features into matchup PA modifiers for Monte Carlo simulation.

        Returns:
            dict containing:
            - k_rate_mult: multiplier on baseline strikeout rate
            - bb_rate_mult: multiplier on baseline walk rate
            - hr_suppression: factor suppressing/inflating home run probability
            - whiff_factor: relative whiff potency
            - stuff_index: composite pitcher quality index
        """
        # Overall whiff rate relative to ~0.25 league average
        whiff = float(self.summary_vector[3])
        csw = float(self.summary_vector[4])
        stuff = float(self.summary_vector[9])  # 100 is league average
        fastball_usage = float(self.summary_vector[6])
        breaking_usage = float(self.summary_vector[7])

        # K multiplier: driven by whiff%, CSW%, and stuff+
        k_delta = (whiff - 0.25) * 1.5 + (csw - 0.28) * 1.8 + (stuff - 100.0) / 150.0
        k_mult = float(np.clip(1.0 + k_delta, 0.65, 1.55))

        # BB multiplier: higher CSW / command reduces walk rate
        bb_delta = -(csw - 0.28) * 2.0 - (stuff - 100.0) / 300.0
        bb_mult = float(np.clip(1.0 + bb_delta, 0.60, 1.40))

        # HR factor: high fastball usage and lower stuff elevates HR risk; heavy breaking balls suppress HR
        hr_delta = (fastball_usage - 0.50) * 0.15 - (breaking_usage - 0.30) * 0.15 - (stuff - 100.0) / 250.0
        hr_mult = float(np.clip(1.0 + hr_delta, 0.70, 1.45))

        # Contact suppression
        contact_mult = float(np.clip(1.0 - (whiff - 0.25) * 0.8, 0.75, 1.25))

        return {
            "k_rate_mult": k_mult,
            "bb_rate_mult": bb_mult,
            "hr_suppression": hr_mult,
            "contact_mult": contact_mult,
            "whiff_factor": whiff / 0.25 if whiff > 0 else 1.0,
            "stuff_index": stuff,
        }


@dataclass
class PitchArsenal:
    """Complete pitch repertoire for a pitcher."""

    pitcher_id: str | int
    as_of_utc: datetime | None = None
    total_pitches: int = 0
    pitches: dict[str, PitchMetrics] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Ensure all 8 canonical pitch types exist
        for p in CANONICAL_PITCH_TYPES:
            if p not in self.pitches:
                bench = LEAGUE_PITCH_BENCHMARKS[p]
                self.pitches[p] = PitchMetrics(
                    pitch_type=p,
                    count=0,
                    usage_rate=0.0,
                    velocity=bench["velocity"],
                    horizontal_break=bench["horizontal_break"],
                    vertical_break=bench["vertical_break"],
                    whiff_rate=bench["whiff_rate"],
                    csw_rate=bench["csw_rate"],
                )

    @property
    def primary_pitch(self) -> str:
        """Pitch type with the highest usage rate."""
        return max(CANONICAL_PITCH_TYPES, key=lambda p: self.pitches[p].usage_rate)

    @property
    def secondary_pitch(self) -> str:
        """Pitch type with the 2nd highest usage rate."""
        sorted_types = sorted(CANONICAL_PITCH_TYPES, key=lambda p: self.pitches[p].usage_rate, reverse=True)
        return sorted_types[1] if len(sorted_types) > 1 else sorted_types[0]

    @property
    def repertoire_diversity(self) -> float:
        """Shannon entropy of pitch usage distribution."""
        usages = [self.pitches[p].usage_rate for p in CANONICAL_PITCH_TYPES if self.pitches[p].usage_rate > 0]
        if not usages:
            return 0.0
        total = sum(usages)
        if total <= 0:
            return 0.0
        probs = [u / total for u in usages]
        return float(-sum(p * math.log(p) for p in probs if p > 0))

    @property
    def max_velocity(self) -> float:
        """Maximum velocity across thrown pitch types (or primary fastball)."""
        active_velos = [
            self.pitches[p].velocity for p in CANONICAL_PITCH_TYPES if self.pitches[p].usage_rate > 0.02
        ]
        return float(max(active_velos)) if active_velos else float(self.pitches["4-seam"].velocity)

    @property
    def weighted_velocity(self) -> float:
        """Usage-weighted average velocity across all pitch types."""
        usages = [self.pitches[p].usage_rate for p in CANONICAL_PITCH_TYPES]
        total_usage = sum(usages)
        if total_usage <= 0:
            return float(self.pitches["4-seam"].velocity)
        return float(
            sum(self.pitches[p].velocity * self.pitches[p].usage_rate for p in CANONICAL_PITCH_TYPES)
            / total_usage
        )

    @property
    def overall_whiff_rate(self) -> float:
        """Usage-weighted whiff rate across repertoire."""
        usages = [self.pitches[p].usage_rate for p in CANONICAL_PITCH_TYPES]
        total_usage = sum(usages)
        if total_usage <= 0:
            return 0.25
        return float(
            sum(self.pitches[p].whiff_rate * self.pitches[p].usage_rate for p in CANONICAL_PITCH_TYPES)
            / total_usage
        )

    @property
    def overall_csw_rate(self) -> float:
        """Usage-weighted CSW rate across repertoire."""
        usages = [self.pitches[p].usage_rate for p in CANONICAL_PITCH_TYPES]
        total_usage = sum(usages)
        if total_usage <= 0:
            return 0.28
        return float(
            sum(self.pitches[p].csw_rate * self.pitches[p].usage_rate for p in CANONICAL_PITCH_TYPES)
            / total_usage
        )

    @property
    def fastball_usage(self) -> float:
        """Combined usage of 4-seam, sinker, and cutter."""
        return float(
            self.pitches["4-seam"].usage_rate
            + self.pitches["sinker"].usage_rate
            + self.pitches["cutter"].usage_rate
        )

    @property
    def breaking_ball_usage(self) -> float:
        """Combined usage of slider, sweeper, and curveball."""
        return float(
            self.pitches["slider"].usage_rate
            + self.pitches["sweeper"].usage_rate
            + self.pitches["curveball"].usage_rate
        )

    @property
    def offspeed_usage(self) -> float:
        """Combined usage of changeup and splitter."""
        return float(self.pitches["changeup"].usage_rate + self.pitches["splitter"].usage_rate)

    @property
    def stuff_plus_proxy(self) -> float:
        """Estimated composite stuff metric scaled around 100 (MLB average = 100)."""
        velo_z = (self.weighted_velocity - 90.0) / 3.5
        whiff_z = (self.overall_whiff_rate - 0.25) / 0.06
        csw_z = (self.overall_csw_rate - 0.28) / 0.04
        diversity_z = (self.repertoire_diversity - 1.2) / 0.35
        composite = 100.0 + 10.0 * (0.35 * velo_z + 0.35 * whiff_z + 0.20 * csw_z + 0.10 * diversity_z)
        return float(np.clip(composite, 60.0, 150.0))

    def get_metric_matrix(self) -> np.ndarray:
        """Generate 8x6 matrix of canonical pitch metrics."""
        mat = np.zeros((len(CANONICAL_PITCH_TYPES), len(METRIC_NAMES)), dtype=np.float32)
        for i, p in enumerate(CANONICAL_PITCH_TYPES):
            pm = self.pitches[p]
            mat[i, 0] = pm.usage_rate
            mat[i, 1] = pm.velocity
            mat[i, 2] = pm.horizontal_break
            mat[i, 3] = pm.vertical_break
            mat[i, 4] = pm.whiff_rate
            mat[i, 5] = pm.csw_rate
        return mat

    def to_tensor(self) -> PitchArsenalTensor:
        """Convert arsenal into fixed-size PitchArsenalTensor."""
        mat = self.get_metric_matrix()
        summary = np.array(
            [
                math.log1p(self.total_pitches),
                self.max_velocity,
                self.weighted_velocity,
                self.overall_whiff_rate,
                self.overall_csw_rate,
                self.repertoire_diversity,
                self.fastball_usage,
                self.breaking_ball_usage,
                self.offspeed_usage,
                self.stuff_plus_proxy,
            ],
            dtype=np.float32,
        )
        return PitchArsenalTensor.from_matrix_and_summary(mat, summary)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pitcher_id": self.pitcher_id,
            "as_of_utc": self.as_of_utc.isoformat() if self.as_of_utc else None,
            "total_pitches": self.total_pitches,
            "primary_pitch": self.primary_pitch,
            "secondary_pitch": self.secondary_pitch,
            "repertoire_diversity": self.repertoire_diversity,
            "max_velocity": self.max_velocity,
            "weighted_velocity": self.weighted_velocity,
            "overall_whiff_rate": self.overall_whiff_rate,
            "overall_csw_rate": self.overall_csw_rate,
            "fastball_usage": self.fastball_usage,
            "breaking_ball_usage": self.breaking_ball_usage,
            "offspeed_usage": self.offspeed_usage,
            "stuff_plus_proxy": self.stuff_plus_proxy,
            "pitches": {p: self.pitches[p].to_dict() for p in CANONICAL_PITCH_TYPES},
        }


@dataclass(slots=True)
class PitchTrackingEvent:
    """Single tracked pitch event."""

    pitcher_id: str | int
    timestamp_utc: datetime
    pitch_type: str
    velocity: float
    horizontal_break: float = 0.0
    vertical_break: float = 0.0
    is_swing: bool = False
    is_whiff: bool = False
    is_called_strike: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PitchTrackingEvent | None:
        pitcher_id = data.get("pitcher_id") or data.get("pitcher")
        if pitcher_id is None:
            return None
        raw_type = data.get("pitch_type") or data.get("pitch_name")
        canon_type = normalize_pitch_type(str(raw_type) if raw_type else None)
        if not canon_type:
            return None

        ts = data.get("timestamp_utc") or data.get("game_date") or data.get("game_start_utc")
        if not ts:
            return None
        dt = _parse_timestamp(ts)

        velo = float(data.get("velocity") or data.get("release_speed") or 0.0)
        pfx_x = float(data.get("horizontal_break") or data.get("pfx_x") or 0.0)
        pfx_z = float(data.get("vertical_break") or data.get("pfx_z") or 0.0)

        # Detect swing / whiff / called strike from Statcast description if present
        desc = str(data.get("description", "")).lower()
        is_swing = bool(
            data.get("is_swing", False)
            or "swinging_strike" in desc
            or "hit_into_play" in desc
            or "foul" in desc
            or "in_play" in desc
        )
        is_whiff = bool(
            data.get("is_whiff", False)
            or "swinging_strike" in desc
            or "swinging_strike_blocked" in desc
            or "missed_bunt" in desc
        )
        is_called_strike = bool(data.get("is_called_strike", False) or "called_strike" in desc)

        return cls(
            pitcher_id=pitcher_id,
            timestamp_utc=dt,
            pitch_type=canon_type,
            velocity=velo,
            horizontal_break=pfx_x,
            vertical_break=pfx_z,
            is_swing=is_swing,
            is_whiff=is_whiff,
            is_called_strike=is_called_strike,
        )


class PitchArsenalTracker:
    """Strict Point-in-Time (PIT) Pitch Arsenal Repository and Sequential Updater."""

    def __init__(self) -> None:
        # pitcher_id -> chronological list of PitchTrackingEvent
        self._events_by_pitcher: dict[str | int, list[PitchTrackingEvent]] = {}
        self._is_sorted: dict[str | int, bool] = {}

    def add_pitch_event(self, event: PitchTrackingEvent) -> None:
        """Add a single pitch tracking event."""
        pid = event.pitcher_id
        if pid not in self._events_by_pitcher:
            self._events_by_pitcher[pid] = []
        self._events_by_pitcher[pid].append(event)
        self._is_sorted[pid] = False

    def add_pitch_events(self, events: Iterable[PitchTrackingEvent]) -> int:
        """Add multiple pitch tracking events."""
        count = 0
        for ev in events:
            self.add_pitch_event(ev)
            count += 1
        return count

    def _ensure_sorted(self, pitcher_id: str | int) -> list[PitchTrackingEvent]:
        events = self._events_by_pitcher.get(pitcher_id, [])
        if not self._is_sorted.get(pitcher_id, False) and events:
            events.sort(key=lambda e: e.timestamp_utc)
            self._is_sorted[pitcher_id] = True
        return events

    def get_pit_events(
        self,
        pitcher_id: str | int,
        as_of_utc: datetime | str,
        lookback_pitches: int | None = 2500,
        lookback_days: int | None = None,
    ) -> list[PitchTrackingEvent]:
        """Retrieve pitch events strictly preceding as_of_utc."""
        cutoff = _parse_timestamp(as_of_utc)
        all_events = self._ensure_sorted(pitcher_id)
        if not all_events:
            return []

        # Strict PIT filter: event timestamp must be strictly before cutoff
        valid_events = [e for e in all_events if e.timestamp_utc < cutoff]

        if lookback_days is not None:
            min_time = cutoff.timestamp() - (lookback_days * 86400)
            valid_events = [e for e in valid_events if e.timestamp_utc.timestamp() >= min_time]

        if lookback_pitches is not None and len(valid_events) > lookback_pitches:
            valid_events = valid_events[-lookback_pitches:]

        return valid_events

    def get_arsenal(
        self,
        pitcher_id: str | int,
        as_of_utc: datetime | str,
        *,
        shrinkage_prior_pitches: float = 50.0,
        lookback_pitches: int | None = 2500,
        lookback_days: int | None = None,
    ) -> PitchArsenal:
        """Compute Point-In-Time PitchArsenal with Bayesian shrinkage.

        Strict PIT Guarantee: Reads ONLY pitch events strictly prior to as_of_utc.
        """
        cutoff = _parse_timestamp(as_of_utc)
        events = self.get_pit_events(
            pitcher_id,
            as_of_utc=cutoff,
            lookback_pitches=lookback_pitches,
            lookback_days=lookback_days,
        )

        total_pitches = len(events)
        if total_pitches == 0:
            # Return baseline repertoire shrunk entirely to league averages
            pitches: dict[str, PitchMetrics] = {}
            for p in CANONICAL_PITCH_TYPES:
                bench = LEAGUE_PITCH_BENCHMARKS[p]
                pitches[p] = PitchMetrics(
                    pitch_type=p,
                    count=0,
                    usage_rate=bench["usage_rate"],
                    velocity=bench["velocity"],
                    horizontal_break=bench["horizontal_break"],
                    vertical_break=bench["vertical_break"],
                    whiff_rate=bench["whiff_rate"],
                    csw_rate=bench["csw_rate"],
                )
            return PitchArsenal(
                pitcher_id=pitcher_id,
                as_of_utc=cutoff,
                total_pitches=0,
                pitches=pitches,
            )

        # Aggregate raw metrics by pitch type
        by_type: dict[str, list[PitchTrackingEvent]] = {p: [] for p in CANONICAL_PITCH_TYPES}
        for ev in events:
            if ev.pitch_type in by_type:
                by_type[ev.pitch_type].append(ev)

        raw_pitches: dict[str, PitchMetrics] = {}
        for p in CANONICAL_PITCH_TYPES:
            p_events = by_type[p]
            count = len(p_events)
            usage = count / total_pitches if total_pitches > 0 else 0.0

            if count > 0:
                velos = [e.velocity for e in p_events if e.velocity > 50.0]
                avg_velo = float(np.mean(velos)) if velos else LEAGUE_PITCH_BENCHMARKS[p]["velocity"]
                h_breaks = [e.horizontal_break for e in p_events]
                avg_h = (
                    float(np.mean(h_breaks)) if h_breaks else LEAGUE_PITCH_BENCHMARKS[p]["horizontal_break"]
                )
                v_breaks = [e.vertical_break for e in p_events]
                avg_v = float(np.mean(v_breaks)) if v_breaks else LEAGUE_PITCH_BENCHMARKS[p]["vertical_break"]

                swings = sum(1 for e in p_events if e.is_swing)
                whiffs = sum(1 for e in p_events if e.is_whiff)
                called = sum(1 for e in p_events if e.is_called_strike)

                whiff_rate = whiffs / swings if swings > 0 else LEAGUE_PITCH_BENCHMARKS[p]["whiff_rate"]
                csw_rate = (whiffs + called) / count if count > 0 else LEAGUE_PITCH_BENCHMARKS[p]["csw_rate"]

                raw_pm = PitchMetrics(
                    pitch_type=p,
                    count=count,
                    usage_rate=usage,
                    velocity=avg_velo,
                    horizontal_break=avg_h,
                    vertical_break=avg_v,
                    whiff_rate=whiff_rate,
                    csw_rate=csw_rate,
                    swings=swings,
                    whiffs=whiffs,
                    called_strikes=called,
                )
            else:
                bench = LEAGUE_PITCH_BENCHMARKS[p]
                raw_pm = PitchMetrics(
                    pitch_type=p,
                    count=0,
                    usage_rate=0.0,
                    velocity=bench["velocity"],
                    horizontal_break=bench["horizontal_break"],
                    vertical_break=bench["vertical_break"],
                    whiff_rate=bench["whiff_rate"],
                    csw_rate=bench["csw_rate"],
                )

            # Apply Bayesian shrinkage toward league benchmarks
            shrunk_pm = raw_pm.with_shrinkage(prior=None, prior_pitches=shrinkage_prior_pitches)
            raw_pitches[p] = shrunk_pm

        # Normalize usage rates if nonzero, or retain empirical usage
        total_shrunk_usage = sum(pm.usage_rate for pm in raw_pitches.values())
        if total_shrunk_usage > 0:
            for p in CANONICAL_PITCH_TYPES:
                raw_pitches[p].usage_rate /= total_shrunk_usage

        return PitchArsenal(
            pitcher_id=pitcher_id,
            as_of_utc=cutoff,
            total_pitches=total_pitches,
            pitches=raw_pitches,
        )

    def ingest_records(self, records: Iterable[dict[str, Any]]) -> int:
        """Ingest arbitrary dictionary records (e.g. from Statcast JSON/CSV)."""
        valid_events = []
        for row in records:
            ev = PitchTrackingEvent.from_dict(row)
            if ev is not None:
                valid_events.append(ev)
        return self.add_pitch_events(valid_events)

    def dump_jsonl(self, filepath: Path | str) -> int:
        """Persist pitch tracker events to JSONL."""
        path = Path(filepath)
        path.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with path.open("w", encoding="utf-8") as f:
            for pid in self._events_by_pitcher:
                events = self._ensure_sorted(pid)
                for ev in events:
                    row = {
                        "pitcher_id": ev.pitcher_id,
                        "timestamp_utc": ev.timestamp_utc.isoformat(),
                        "pitch_type": ev.pitch_type,
                        "velocity": ev.velocity,
                        "horizontal_break": ev.horizontal_break,
                        "vertical_break": ev.vertical_break,
                        "is_swing": ev.is_swing,
                        "is_whiff": ev.is_whiff,
                        "is_called_strike": ev.is_called_strike,
                    }
                    f.write(json.dumps(row) + "\n")
                    count += 1
        return count

    @classmethod
    def load_jsonl(cls, filepath: Path | str) -> PitchArsenalTracker:
        """Load pitch tracker events from JSONL."""
        path = Path(filepath)
        tracker = cls()
        if not path.exists():
            return tracker
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    ev = PitchTrackingEvent.from_dict(data)
                    if ev is not None:
                        tracker.add_pitch_event(ev)
                except (json.JSONDecodeError, ValueError, KeyError):
                    continue
        return tracker


def create_sample_pitch_arsenal(
    pitcher_id: str | int = "sample_pitcher",
    primary_type: str = "4-seam",
    primary_velo: float = 95.5,
    secondary_type: str = "slider",
    secondary_velo: float = 86.0,
    whiff_boost: float = 0.05,
    total_pitches: int = 1200,
    as_of_utc: datetime | None = None,
) -> PitchArsenal:
    """Helper to synthesize a realistic pitcher arsenal for testing and simulations."""
    pitches: dict[str, PitchMetrics] = {}

    # Normalize pitch distribution with custom primaries
    usage_map: dict[str, float] = {p: 0.0 for p in CANONICAL_PITCH_TYPES}
    usage_map[primary_type] = 0.48
    usage_map[secondary_type] = 0.32
    remaining_types = [p for p in CANONICAL_PITCH_TYPES if p not in (primary_type, secondary_type)]
    for r in remaining_types[:2]:
        usage_map[r] = 0.10

    for p in CANONICAL_PITCH_TYPES:
        bench = LEAGUE_PITCH_BENCHMARKS[p]
        usage = usage_map.get(p, 0.0)
        count = int(usage * total_pitches)
        velo = (
            primary_velo
            if p == primary_type
            else (secondary_velo if p == secondary_type else bench["velocity"])
        )
        whiff = float(np.clip(bench["whiff_rate"] + whiff_boost, 0.05, 0.60))
        csw = float(np.clip(bench["csw_rate"] + (whiff_boost * 0.6), 0.10, 0.50))
        pitches[p] = PitchMetrics(
            pitch_type=p,
            count=count,
            usage_rate=usage,
            velocity=velo,
            horizontal_break=bench["horizontal_break"],
            vertical_break=bench["vertical_break"],
            whiff_rate=whiff,
            csw_rate=csw,
            swings=int(count * 0.45),
            whiffs=int(count * 0.45 * whiff),
            called_strikes=int(count * 0.16),
        )

    return PitchArsenal(
        pitcher_id=pitcher_id,
        as_of_utc=as_of_utc or datetime.now(UTC),
        total_pitches=total_pitches,
        pitches=pitches,
    )
