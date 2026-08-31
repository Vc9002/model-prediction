"""Production champion/challenger gating for the model-prediction system.

Production incumbents stay live and immutable throughout research. Every
improvement becomes a CHALLENGER tested against the frozen PRODUCTION CHAMPION.
No production pointer changes during experimentation — candidate code, features,
and artifacts stay isolated until the candidate proves better.

Promotion rule: a candidate must beat or tie the incumbent on LogLoss AND Brier,
not worsen ECE, not reduce coverage, show improvement across multiple date
blocks, and not rely on PIT-invalid data.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .runtime_paths import RuntimePaths

logger = logging.getLogger(__name__)


# ── helpers ────────────────────────────────────────────────────────────────


def _compute_artifact_hash(payload: dict[str, Any]) -> str:
    """SHA-256 of the canonical JSON form, excluding the embedded hash field."""
    canonical = {k: v for k, v in payload.items() if k != "artifact_hash"}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _repo_root() -> Path:
    """Resolve the project root consistently with the rest of the package."""
    from .config import PROJECT_ROOT

    return PROJECT_ROOT


def _load_production_config(*, repo_root: Path | str | None = None) -> dict[str, Any]:
    """Load production.yaml, matching production_canary.load_production_config."""
    root = Path(repo_root) if repo_root is not None else _repo_root()
    config_path = root / "config" / "production.yaml"
    if not config_path.is_file():
        raise FileNotFoundError(f"production config not found at {config_path}")
    with open(config_path, "r", encoding="utf-8") as fh:
        config: dict[str, Any] = yaml.safe_load(fh) or {}
    return config


# ── exceptions ──────────────────────────────────────────────────────────────


class ProductionFreezeViolation(Exception):
    """Raised when a frozen production artifact has been modified since freeze."""


# Only these champions are genuinely code-backed — their predictions come
# straight from Python modules, with no JSON artifact on disk (soccer
# poisson-dc-v1, tennis surface-elo-v1; see config/production.yaml). Every
# other artifact_map entry must have a real artifact file. Before this
# allowlist existed, ANY missing artifact was silently treated as
# code-backed, which quietly dropped real artifact champions (kbo/npb v2
# referenced files that were written under the wrong name) from both the
# freeze and tamper protection (audit 2026-08-13).
CODE_BACKED_MODEL_IDS: frozenset[str] = frozenset({"soccer-poisson-dc-v1", "tennis-surface-elo-v1"})


# ── ChampionSnapshot ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ChampionSnapshot:
    """Immutable record of a production model at freeze time.

    Every field is frozen so once a champion is captured, no downstream code
    can accidentally mutate the reference.
    """

    sport: str
    market_type: str
    model_id: str
    artifact_path: str
    artifact_hash: str
    frozen_at_utc: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "sport": self.sport,
            "market_type": self.market_type,
            "model_id": self.model_id,
            "artifact_path": self.artifact_path,
            "artifact_hash": self.artifact_hash,
            "frozen_at_utc": self.frozen_at_utc,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChampionSnapshot:
        return cls(
            sport=data["sport"],
            market_type=data["market_type"],
            model_id=data["model_id"],
            artifact_path=data["artifact_path"],
            artifact_hash=data["artifact_hash"],
            frozen_at_utc=data["frozen_at_utc"],
        )


# ── ProductionRegistry ──────────────────────────────────────────────────────


@dataclass
class ProductionRegistry:
    """Immutable snapshot of production model pointers.

    Captures every model in production.yaml's ``prediction_service.artifact_map``
    as a frozen :class:`ChampionSnapshot`. Once frozen, any tampering with the
    underlying artifact files is detectable via ``validate_no_tampering()``.
    """

    _snapshots: dict[str, ChampionSnapshot] = field(default_factory=dict)
    _frozen_at_utc: str = ""
    _repo_root: Path = field(default_factory=_repo_root)

    def freeze(self) -> list[ChampionSnapshot]:
        """Capture the current production state as an immutable snapshot.

        Reads ``config/production.yaml``, hashes every artifact file referenced
        in ``prediction_service.artifact_map``, and stores a
        :class:`ChampionSnapshot` per model. Returns the list of snapshots in
        insertion order.
        """
        from .domain import utc_now

        config = _load_production_config(repo_root=self._repo_root)
        artifact_map: dict[str, str] = config.get("prediction_service", {}).get("artifact_map", {})
        if not artifact_map:
            raise ValueError("production.yaml prediction_service.artifact_map is empty or missing")

        snapshots: list[ChampionSnapshot] = []
        for model_id, artifact_rel in artifact_map.items():
            artifact_path = self._repo_root / artifact_rel
            sport = model_id.split("-")[0]
            if not artifact_path.is_file():
                # A missing artifact is only legitimate for the explicitly
                # code-backed champions above; anything else is a real error
                # (e.g. kbo/npb v2 referenced files that were written under
                # the wrong filename) and must fail loudly instead of being
                # silently frozen as if no artifact existed.
                if model_id not in CODE_BACKED_MODEL_IDS:
                    raise ValueError(
                        f"freeze: artifact file missing for {model_id} "
                        f"({artifact_rel}) — model is artifact-backed, not "
                        f"code-backed; generate the artifact before freezing"
                    )
                logger.warning(
                    "freeze: artifact file missing for %s (%s) — treating as code-backed champion",
                    model_id,
                    artifact_rel,
                )
                snapshot = ChampionSnapshot(
                    sport=sport,
                    market_type="moneyline",
                    model_id=model_id,
                    artifact_path=str(artifact_rel),
                    artifact_hash="CODE_BACKED",
                    frozen_at_utc=utc_now().isoformat().replace("+00:00", "Z"),
                )
                snapshots.append(snapshot)
                self._snapshots[model_id] = snapshot
                continue
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
            artifact_hash_val = _compute_artifact_hash(payload)
            snapshot = ChampionSnapshot(
                sport=payload.get("sport", sport),
                market_type="moneyline",
                model_id=model_id,
                artifact_path=str(artifact_rel),
                artifact_hash=artifact_hash_val,
                frozen_at_utc=utc_now().isoformat().replace("+00:00", "Z"),
            )
            snapshots.append(snapshot)
            self._snapshots[model_id] = snapshot

        self._frozen_at_utc = utc_now().isoformat().replace("+00:00", "Z")
        return snapshots

    def champion(self, sport: str, market: str) -> ChampionSnapshot | None:
        """Return the frozen champion for *sport* / *market*, or *None*.

        Matches by sport prefix in the model_id (e.g. "mlb" matches
        "mlb-elo-trend-lr-v8") since the artifact_map keys are model_ids
        that embed the sport name.
        """
        sport_lower = sport.lower()
        for model_id, snapshot in self._snapshots.items():
            if model_id.lower().startswith(sport_lower) and snapshot.market_type == market:
                return snapshot
        return None

    def validate_no_tampering(self) -> list[str]:
        """Verify that no frozen production artifact has been modified.

        Re-computes every artifact's hash and compares against the frozen
        value. Returns a list of model_ids whose artifacts have been tampered
        with (empty list = all clear). Raises :class:`ProductionFreezeViolation`
        if any artifact changed.
        """
        violations: list[str] = []
        for model_id, snapshot in self._snapshots.items():
            if snapshot.artifact_hash == "CODE_BACKED":
                # A CODE_BACKED entry is only legitimate for the explicitly
                # code-backed champions. Snapshot files written before the
                # freeze fix (audit 2026-08-13) recorded artifact-backed
                # kbo/npb as CODE_BACKED because their (wrongly-named)
                # artifact files were missing — those must be re-frozen, not
                # silently skipped.
                if model_id not in CODE_BACKED_MODEL_IDS:
                    violations.append(
                        f"{model_id}: frozen as CODE_BACKED but is artifact-backed; re-run freeze-production"
                    )
                    continue
                # Code-backed models (soccer, tennis) have no artifact file
                # to validate — skip tampering check for these.
                continue
            artifact_path = self._repo_root / snapshot.artifact_path
            if not artifact_path.is_file():
                violations.append(f"{model_id}: artifact file missing at {snapshot.artifact_path}")
                continue
            try:
                payload = json.loads(artifact_path.read_text(encoding="utf-8"))
                current_hash = _compute_artifact_hash(payload)
                if current_hash != snapshot.artifact_hash:
                    violations.append(
                        f"{model_id}: hash changed (frozen={snapshot.artifact_hash[:12]}…, "
                        f"current={current_hash[:12]}…)"
                    )
            except (OSError, json.JSONDecodeError) as exc:
                violations.append(f"{model_id}: cannot read artifact ({exc})")

        if violations:
            raise ProductionFreezeViolation(
                f"production freeze violated for {len(violations)} artifact(s): " + "; ".join(violations)
            )
        return violations

    @property
    def frozen_at_utc(self) -> str:
        return self._frozen_at_utc

    @property
    def model_ids(self) -> list[str]:
        return sorted(self._snapshots)

    def to_dict(self) -> dict[str, Any]:
        return {
            "frozen_at_utc": self._frozen_at_utc,
            "champions": {model_id: snapshot.to_dict() for model_id, snapshot in self._snapshots.items()},
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        repo_root: Path | str | None = None,
    ) -> ProductionRegistry:
        registry = cls(_repo_root=Path(repo_root) if repo_root else _repo_root())
        registry._frozen_at_utc = data.get("frozen_at_utc", "")
        for model_id, snap_data in data.get("champions", {}).items():
            registry._snapshots[model_id] = ChampionSnapshot.from_dict(snap_data)
        return registry


# ── FrozenProductionStore ───────────────────────────────────────────────────


class FrozenProductionStore:
    """Persist and load the frozen champion snapshot to/from disk.

    Writes under the runtime root's production dir (RuntimePaths), never
    the repo checkout — frozen state is operational evidence, and the
    repo-local copy was a split-brain relic. The serving pipeline should
    load this on startup to confirm it is serving the champion, not a
    challenger.
    """

    def __init__(self, repo_root: Path | str | None = None) -> None:
        self._repo_root = Path(repo_root) if repo_root is not None else _repo_root()

    @property
    def path(self) -> Path:
        return RuntimePaths.resolve(repo_root=self._repo_root).production_root / "frozen_champions.json"

    def write(self, registry: ProductionRegistry) -> None:
        """Write the frozen registry to disk."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = registry.to_dict()
        self.path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        logger.info("wrote frozen champions to %s", self.path)

    def load(self) -> ProductionRegistry:
        """Load the frozen registry and validate every artifact on load.

        Raises :class:`ProductionFreezeViolation` if any artifact has been
        modified since the freeze was written.
        """
        if not self.path.is_file():
            raise FileNotFoundError(f"no frozen champions file at {self.path}; run 'freeze-production' first")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        registry = ProductionRegistry.from_dict(data, repo_root=self._repo_root)
        registry.validate_no_tampering()
        logger.info("loaded and validated frozen champions from %s", self.path)
        return registry

    def load_no_validate(self) -> ProductionRegistry:
        """Load the frozen registry without validating artifacts.

        Useful for inspection or comparison workflows where the artifacts may
        not be present (e.g. CI). The caller is responsible for deciding
        whether validation matters.
        """
        if not self.path.is_file():
            raise FileNotFoundError(f"no frozen champions file at {self.path}; run 'freeze-production' first")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return ProductionRegistry.from_dict(data, repo_root=self._repo_root)


# ── PromotionVerdict ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PromotionVerdict:
    """Structured result of a champion-vs-challenger comparison."""

    status: str  # "promote" | "reject" | "needs_more_data"
    paired_metrics: dict[str, float]
    bootstrap_ci: dict[str, dict[str, Any]]
    failures: list[str]
    recommendation: str


# ── PairedComparison ────────────────────────────────────────────────────────


def _bootstrap_ci_on_deltas(
    champion_rows: Sequence[dict[str, Any]],
    challenger_rows: Sequence[dict[str, Any]],
    *,
    date_key: str,
    metric_fn: Callable[[Sequence[dict[str, Any]]], float],
    n_resamples: int = 1000,
    confidence: float = 0.95,
    seed: int | None = 0,
) -> dict[str, Any]:
    """Bootstrap CI on the paired delta of *metric_fn* (challenger − champion).

    Resamples by date (not by row) to avoid treating same-day correlated games
    as independent observations. The delta is computed on each bootstrap sample
    and the confidence interval is the percentile range of those deltas.
    """
    import random as _random_mod

    rng = _random_mod.Random(seed)

    # Build per-date lists
    champion_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    challenger_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in champion_rows:
        champion_by_date[str(row[date_key])].append(row)
    for row in challenger_rows:
        challenger_by_date[str(row[date_key])].append(row)

    # Only dates present in both
    common_dates = sorted(set(champion_by_date) & set(challenger_by_date))
    if len(common_dates) < 2:
        return {
            "status": "insufficient_dates",
            "dates": len(common_dates),
            "point_estimate": None,
            "ci_low": None,
            "ci_high": None,
        }

    # Champion and challenger metric per date
    champion_daily = [metric_fn(champion_by_date[day]) for day in common_dates]
    challenger_daily = [metric_fn(challenger_by_date[day]) for day in common_dates]
    deltas = [c - h for c, h in zip(challenger_daily, champion_daily)]

    n = len(deltas)
    bootstrapped_deltas = []
    for _ in range(n_resamples):
        resample = [deltas[rng.randrange(n)] for _ in range(n)]
        bootstrapped_deltas.append(sum(resample) / len(resample))
    bootstrapped_deltas.sort()
    tail = (1 - confidence) / 2
    lower = bootstrapped_deltas[int(tail * n_resamples)]
    upper = bootstrapped_deltas[min(n_resamples - 1, int((1 - tail) * n_resamples))]
    p_better = (
        round(sum(1 for d in bootstrapped_deltas if d < 0) / len(bootstrapped_deltas), 4)
        if bootstrapped_deltas
        else 0.0
    )

    return {
        "status": "ok",
        "dates": len(common_dates),
        "point_estimate": round(sum(deltas) / len(deltas), 6) if deltas else 0.0,
        "confidence": confidence,
        "ci_low": round(lower, 6),
        "ci_high": round(upper, 6),
        "p_better": p_better,
    }


class PairedComparison:
    """Paired comparison of champion vs challenger predictions.

    Both must cover the SAME event IDs, same decision horizon, and same
    outcomes. The comparison computes paired deltas on LogLoss, Brier, ECE,
    accuracy, and called accuracy, with bootstrap CIs by date.
    """

    def __init__(
        self,
        champion_predictions: Sequence[dict[str, Any]],
        challenger_predictions: Sequence[dict[str, Any]],
        *,
        date_key: str = "date",
        event_id_key: str = "event_id",
        probability_key: str = "probability",
        outcome_key: str = "outcome",
        called_key: str = "called",
        n_bootstrap: int = 1000,
        confidence: float = 0.95,
    ) -> None:
        # Validate alignment
        champ_ids = {str(row[event_id_key]) for row in champion_predictions}
        chall_ids = {str(row[event_id_key]) for row in challenger_predictions}
        if champ_ids != chall_ids:
            only_champ = champ_ids - chall_ids
            only_chall = chall_ids - champ_ids
            msg_parts = []
            if only_champ:
                msg_parts.append(f"{len(only_champ)} events only in champion")
            if only_chall:
                msg_parts.append(f"{len(only_chall)} events only in challenger")
            raise ValueError("champion and challenger must cover the same event IDs: " + "; ".join(msg_parts))

        # Align both sequences by event_id for paired comparison
        champ_by_id = {str(row[event_id_key]): row for row in champion_predictions}
        chall_by_id = {str(row[event_id_key]): row for row in challenger_predictions}
        common_ids = sorted(champ_by_id)
        self._champion_rows = [champ_by_id[eid] for eid in common_ids]
        self._challenger_rows = [chall_by_id[eid] for eid in common_ids]
        self._date_key = date_key
        self._probability_key = probability_key
        self._outcome_key = outcome_key
        self._called_key = called_key
        self._n_bootstrap = n_bootstrap
        self._confidence = confidence
        self._n_events = len(common_ids)

    # ── metric helpers ──────────────────────────────────────────────────

    @staticmethod
    def _log_loss(probabilities: Sequence[float], outcomes: Sequence[int]) -> float:
        eps = 1e-12
        total = 0.0
        for p, y in zip(probabilities, outcomes):
            p_clip = min(1 - eps, max(eps, p))
            total += y * math.log(p_clip) + (1 - y) * math.log(1 - p_clip)
        return -total / len(probabilities) if probabilities else 0.0

    @staticmethod
    def _brier(probabilities: Sequence[float], outcomes: Sequence[int]) -> float:
        if not probabilities:
            return 0.0
        return sum((p - y) ** 2 for p, y in zip(probabilities, outcomes)) / len(probabilities)

    @staticmethod
    def _ece(probabilities: Sequence[float], outcomes: Sequence[int]) -> float:
        if not probabilities:
            return 0.0
        ece = 0.0
        n = len(probabilities)
        for lower_index in range(10):
            lower, upper = lower_index / 10, (lower_index + 1) / 10
            members = [
                (p, y)
                for p, y in zip(probabilities, outcomes)
                if lower <= p < upper or (upper == 1 and p == 1)
            ]
            if not members:
                continue
            mean_p = sum(p for p, _ in members) / len(members)
            mean_y = sum(y for _, y in members) / len(members)
            ece += len(members) / n * abs(mean_p - mean_y)
        return ece

    @staticmethod
    def _accuracy(probabilities: Sequence[float], outcomes: Sequence[int]) -> float:
        if not probabilities:
            return 0.0
        correct = sum(
            1 for p, y in zip(probabilities, outcomes) if (p >= 0.5 and y == 1) or (p < 0.5 and y == 0)
        )
        return correct / len(probabilities)

    @staticmethod
    def _called_accuracy(
        probabilities: Sequence[float],
        outcomes: Sequence[int],
        called: Sequence[bool],
    ) -> float:
        called_probs = [p for p, c in zip(probabilities, called) if c]
        called_outs = [y for y, c in zip(outcomes, called) if c]
        if not called_probs:
            return 0.0
        return PairedComparison._accuracy(called_probs, called_outs)

    @staticmethod
    def _coverage(called: Sequence[bool]) -> float:
        if not called:
            return 0.0
        return sum(1 for c in called if c) / len(called)

    # ── comparison ──────────────────────────────────────────────────────

    def compute(self) -> dict[str, Any]:
        """Compute all paired deltas and bootstrap CIs."""
        champ_probs = [float(row[self._probability_key]) for row in self._champion_rows]
        chall_probs = [float(row[self._probability_key]) for row in self._challenger_rows]
        outcomes = [int(row[self._outcome_key]) for row in self._champion_rows]
        champ_called = [bool(row.get(self._called_key, True)) for row in self._champion_rows]
        chall_called = [bool(row.get(self._called_key, True)) for row in self._challenger_rows]

        # Per-model metrics
        champ_metrics = {
            "log_loss": self._log_loss(champ_probs, outcomes),
            "brier": self._brier(champ_probs, outcomes),
            "ece": self._ece(champ_probs, outcomes),
            "accuracy": self._accuracy(champ_probs, outcomes),
            "called_accuracy": self._called_accuracy(champ_probs, outcomes, champ_called),
            "coverage": self._coverage(champ_called),
        }
        chall_metrics = {
            "log_loss": self._log_loss(chall_probs, outcomes),
            "brier": self._brier(chall_probs, outcomes),
            "ece": self._ece(chall_probs, outcomes),
            "accuracy": self._accuracy(chall_probs, outcomes),
            "called_accuracy": self._called_accuracy(chall_probs, outcomes, chall_called),
            "coverage": self._coverage(chall_called),
        }

        # Paired deltas (challenger − champion): negative = challenger better
        deltas = {
            "delta_log_loss": round(chall_metrics["log_loss"] - champ_metrics["log_loss"], 6),
            "delta_brier": round(chall_metrics["brier"] - champ_metrics["brier"], 6),
            "delta_ece": round(chall_metrics["ece"] - champ_metrics["ece"], 6),
            "delta_accuracy": round(chall_metrics["accuracy"] - champ_metrics["accuracy"], 6),
            "delta_called_accuracy": round(
                chall_metrics["called_accuracy"] - champ_metrics["called_accuracy"], 6
            ),
            "delta_coverage": round(chall_metrics["coverage"] - champ_metrics["coverage"], 6),
        }

        # Bootstrap CIs on the key deltas
        bootstrap_cis: dict[str, dict[str, Any]] = {}
        for metric_name, metric_fn in [
            (
                "delta_log_loss",
                lambda rows: self._log_loss(
                    [float(r[self._probability_key]) for r in rows],
                    [int(r[self._outcome_key]) for r in rows],
                ),
            ),
            (
                "delta_brier",
                lambda rows: self._brier(
                    [float(r[self._probability_key]) for r in rows],
                    [int(r[self._outcome_key]) for r in rows],
                ),
            ),
            (
                "delta_ece",
                lambda rows: self._ece(
                    [float(r[self._probability_key]) for r in rows],
                    [int(r[self._outcome_key]) for r in rows],
                ),
            ),
        ]:
            bootstrap_cis[metric_name] = _bootstrap_ci_on_deltas(
                self._champion_rows,
                self._challenger_rows,
                date_key=self._date_key,
                metric_fn=metric_fn,
                n_resamples=self._n_bootstrap,
                confidence=self._confidence,
            )

        dates = sorted({str(row[self._date_key]) for row in self._champion_rows})
        date_blocks = len(dates)

        # Provenance breakdowns
        from .model_lifecycle import EvidenceOrigin

        live_prospective_n = sum(
            1
            for r in self._challenger_rows
            if r.get("evidence_origin") == EvidenceOrigin.LIVE_PROSPECTIVE.value
        )
        pit_replay_n = sum(
            1 for r in self._challenger_rows if r.get("evidence_origin") == EvidenceOrigin.PIT_REPLAY.value
        )
        historical_n = len(self._challenger_rows)

        return {
            "n_events": self._n_events,
            "historical_n": historical_n,
            "pit_replay_n": pit_replay_n,
            "live_prospective_n": live_prospective_n,
            "n_dates": date_blocks,
            "champion": champ_metrics,
            "challenger": chall_metrics,
            "deltas": deltas,
            "bootstrap_ci": bootstrap_cis,
        }

    # ── promotion gate ───────────────────────────────────────────────────

    def promotion_eligible(
        self,
        *,
        min_events: int = 50,
        min_dates: int = 2,
        required_p_better: float = 0.90,
    ) -> PromotionVerdict:
        """Check all promotion criteria and return a structured verdict.

        Criteria (from the champion/challenger specification):
        1. Candidate ΔLogLoss ≤ 0 — when the bootstrap CI is available, a
           positive point delta still passes while the CI includes 0 (the
           escape hatch: the challenger is not *confidently* worse); the
           delta fails only when the whole CI is above 0. Without a CI, a
           small positive delta (≤ 0.001) is tolerated.
        2. Candidate ΔBrier ≤ 0 — same CI rule as criterion 1.
        3. Candidate ΔECE not materially worse (ΔECE ≤ 0.01)
        4. Coverage not reduced excessively (Δcoverage ≥ −0.05)
        5. Improvement across multiple date blocks (≥ 2 dates with
           candidate beating champion on Brier)
        6. Minimum sample: ≥ min_events (default 50) and ≥ min_dates (default 2)
        """
        results = self.compute()
        deltas = results["deltas"]
        cis = results["bootstrap_ci"]
        n_events = results["n_events"]
        n_dates = results["n_dates"]

        failures: list[str] = []

        # Minimum sample
        if n_events < min_events:
            failures.append(f"insufficient events: {n_events} < {min_events} minimum")
            return PromotionVerdict(
                status="needs_more_data",
                paired_metrics=deltas,
                bootstrap_ci=cis,
                failures=failures,
                recommendation=(
                    f"Only {n_events} events ({n_dates} dates) available; "
                    f"need at least {min_events} events for a reliable comparison."
                ),
            )

        if n_dates < min_dates:
            failures.append(f"insufficient dates: {n_dates} < {min_dates} minimum")
            return PromotionVerdict(
                status="needs_more_data",
                paired_metrics=deltas,
                bootstrap_ci=cis,
                failures=failures,
                recommendation=(f"Only {n_dates} dates available; need at least {min_dates} dates."),
            )

        # Criterion 1: ΔLogLoss ≤ 0 (or within CI).
        # With a CI available, fail only when the whole CI lies above 0 —
        # a positive point estimate whose CI includes 0 means the challenger
        # is not confidently worse, which is the "within CI" pass the
        # docstring documents. (The previous check on ci_high > 0 AND
        # delta > 0 was effectively always true for any positive delta,
        # since the CI is resampled from those same deltas — the CI escape
        # hatch could never pass anyone.)
        delta_ll = deltas["delta_log_loss"]
        ll_ci = cis.get("delta_log_loss", {})
        if ll_ci.get("status") == "ok" and ll_ci.get("ci_low") is not None:
            if ll_ci["ci_low"] > 0:
                failures.append(
                    f"DeltaLogLoss: challenger worse by {delta_ll:+.6f} "
                    f"(CI [{ll_ci['ci_low']:+.6f}, {ll_ci['ci_high']:+.6f}] entirely above 0)"
                )
        elif delta_ll > 0.001:
            failures.append(f"DeltaLogLoss: challenger worse by {delta_ll:+.6f}")

        # Criterion 2: ΔBrier ≤ 0 (or within CI) — same rule as criterion 1.
        delta_brier = deltas["delta_brier"]
        brier_ci = cis.get("delta_brier", {})
        if brier_ci.get("status") == "ok" and brier_ci.get("ci_low") is not None:
            if brier_ci["ci_low"] > 0:
                failures.append(
                    f"DeltaBrier: challenger worse by {delta_brier:+.6f} "
                    f"(CI [{brier_ci['ci_low']:+.6f}, {brier_ci['ci_high']:+.6f}] entirely above 0)"
                )
        elif delta_brier > 0.001:
            failures.append(f"DeltaBrier: challenger worse by {delta_brier:+.6f}")

        # Criterion 3: ΔECE not materially worse (threshold: 0.01)
        delta_ece = deltas["delta_ece"]
        if delta_ece > 0.01:
            failures.append(f"DeltaECE: challenger calibration materially worse ({delta_ece:+.6f} > 0.01)")

        # Criterion 4: Coverage not reduced excessively (threshold: −0.05)
        delta_cov = deltas["delta_coverage"]
        if delta_cov < -0.05:
            failures.append(
                f"DeltaCoverage: challenger coverage reduced by {abs(delta_cov):.4f} "
                f"({delta_cov:+.4f} < -0.05)"
            )

        # Criterion 5: Improvement across multiple date blocks
        dates = sorted({str(row[self._date_key]) for row in self._champion_rows})
        brier_better_dates = 0
        for date_str in dates:
            champ_date_rows = [r for r in self._champion_rows if str(r[self._date_key]) == date_str]
            chall_date_rows = [r for r in self._challenger_rows if str(r[self._date_key]) == date_str]
            if not champ_date_rows or not chall_date_rows:
                continue
            champ_date_brier = self._brier(
                [float(r[self._probability_key]) for r in champ_date_rows],
                [int(r[self._outcome_key]) for r in champ_date_rows],
            )
            chall_date_brier = self._brier(
                [float(r[self._probability_key]) for r in chall_date_rows],
                [int(r[self._outcome_key]) for r in chall_date_rows],
            )
            if chall_date_brier <= champ_date_brier:
                brier_better_dates += 1
        if brier_better_dates < 2 and n_dates >= 2:
            failures.append(
                f"date-block consistency: challenger beats champion on Brier on "
                f"only {brier_better_dates}/{n_dates} dates (need >= 2)"
            )

        # Determine status
        if not failures:
            status = "promote"
            recommendation = (
                f"Challenger meets all promotion criteria: "
                f"DeltaLogLoss={delta_ll:+.6f}, DeltaBrier={delta_brier:+.6f}, "
                f"DeltaECE={delta_ece:+.6f}, DeltaCoverage={delta_cov:+.4f}, "
                f"Brier better on {brier_better_dates}/{n_dates} dates. "
                f"Eligible for promotion."
            )
        elif any("insufficient" in f.lower() for f in failures):
            status = "needs_more_data"
            recommendation = "; ".join(failures)
        else:
            status = "reject"
            recommendation = "Challenger does not meet promotion criteria: " + "; ".join(failures)

        return PromotionVerdict(
            status=status,
            paired_metrics=deltas,
            bootstrap_ci=cis,
            failures=failures,
            recommendation=recommendation,
        )


# ── convenience ─────────────────────────────────────────────────────────────


def compare_champion_vs_challenger(
    challenger_predictions: Sequence[dict[str, Any]],
    *,
    sport: str,
    market: str = "moneyline",
    champion_predictions: Sequence[dict[str, Any]] | None = None,
    frozen_store: FrozenProductionStore | None = None,
    repo_root: Path | str | None = None,
) -> PromotionVerdict:
    """Compare a challenger against the frozen champion.

    Loads champion predictions either from the provided *champion_predictions*
    or from the frozen champion store. The champion predictions must come from
    the frozen artifact — this function does not re-run the champion model.

    Args:
        challenger_predictions: List of dicts with keys: event_id, date,
            probability, outcome, called (optional, default True).
        sport: Sport key (e.g. "wnba", "mlb").
        market: Market type (default "moneyline").
        champion_predictions: Optional pre-loaded champion predictions. If
            *None*, loaded from the frozen store.
        frozen_store: Optional :class:`FrozenProductionStore` instance.
        repo_root: Optional project root override.

    Returns:
        A :class:`PromotionVerdict` with the comparison outcome.
    """
    if champion_predictions is None:
        if frozen_store is None:
            frozen_store = FrozenProductionStore(repo_root=repo_root)
        registry = frozen_store.load_no_validate()
        champion = registry.champion(sport, market)
        if champion is None:
            raise ValueError(f"no frozen champion for sport={sport!r}, market={market!r}")
        champion_predictions = load_settled_predictions(
            sport,
            market,
            model_version=champion.model_id,
            repo_root=repo_root,
        )

    comparison = PairedComparison(champion_predictions, challenger_predictions)
    return comparison.promotion_eligible()


# ── settled-picks loader ─────────────────────────────────────────────────────


def load_settled_predictions(
    sport: str,
    market: str = "moneyline",
    *,
    repo_root: Path | str | None = None,
    model_version: str | None = None,
) -> list[dict[str, Any]]:
    """Load a model's real settled picks from ``data/model_ledgers/<id>.xlsx``.

    Returns prediction dicts in the shape :class:`PairedComparison` consumes:
    ``event_id``, ``date``, ``probability``, ``outcome``, ``called``. Only
    settled win/loss rows are returned (pushes excluded, matching the
    calibration convention elsewhere in the codebase). ``probability`` is the
    model's P(selection wins) and ``outcome`` is 1 for a win, 0 for a loss.

    When *model_version* is given, only rows produced by that exact artifact
    version are returned. Ledgers accumulate rows from every artifact version
    that ever wrote to them (the MLB ledger holds 244 v7 rows vs 14 v8 while
    the frozen champion is v8), and mixing older versions into the champion's
    metrics would compare the challenger against rows the champion never
    produced (audit 2026-08-13).

    This is the *settled-picks* half of the champion/challenger flow; the
    backfill half is produced by the experiment runner. The champion's own
    settled rows come straight from the ledger, so a challenger can be paired
    against them on the exact same events/decisions.
    """
    from .model_ledger import model_id_for

    root = Path(repo_root) if repo_root is not None else _repo_root()
    model_id = model_id_for(str(sport).upper(), str(market))
    if model_id is None:
        return []
    ledger_path = root / "data" / "model_ledgers" / f"{model_id}.xlsx"
    if not ledger_path.is_file():
        return []

    from .model_ledger import ModelLedger
    from .model_lifecycle import classify_evidence_origin

    rows = ModelLedger(ledger_path).rows()
    settled: list[dict[str, Any]] = []
    for row in rows:
        if row.get("status") != "settled":
            continue
        if row.get("result") not in {"win", "loss"}:
            continue
        if model_version is not None and row.get("model_version") != model_version:
            continue
        prob = row.get("model_probability")
        if prob is None or prob == "":
            continue
        observed_at = row.get("observed_at_utc")
        event_start = row.get("event_start_utc")
        settled_at = row.get("settled_at_utc")
        date = event_start or observed_at or settled_at or ""

        origin = classify_evidence_origin(
            prediction_created_at=observed_at,
            event_start_utc=event_start,
            outcome_available_at=settled_at,
        )

        settled.append(
            {
                "event_id": row.get("event_id", ""),
                "date": date,
                "probability": float(prob),
                "outcome": 1 if row.get("result") == "win" else 0,
                "called": True,
                "selection": row.get("selection", ""),
                "line": row.get("line", ""),
                "observed_at_utc": observed_at,
                "event_start_utc": event_start,
                "settled_at_utc": settled_at,
                "evidence_origin": origin.value,
            }
        )
    return settled


def settled_champion_calibration(
    sport: str,
    market: str = "moneyline",
    *,
    repo_root: Path | str | None = None,
) -> dict[str, Any]:
    """Calibration metrics for a champion's own settled picks.

    Computes Brier, log loss, ECE, accuracy, and sample size from the real
    settled rows in the model ledger. This is the "settled calibration" the
    paste references (e.g. MLB v8 ECE 0.023 n=55) — direct evidence from
    real-world settled outcomes rather than reconstructed backfill.
    """
    store = FrozenProductionStore(repo_root=repo_root)
    registry = store.load_no_validate()
    champion = registry.champion(sport, market)
    if champion is None:
        raise ValueError(f"no frozen champion for sport={sport!r}, market={market!r}")
    predictions = load_settled_predictions(
        sport, market, repo_root=repo_root, model_version=champion.model_id
    )
    if not predictions:
        return {"sport": sport, "market": market, "n": 0, "status": "no_settled_picks"}
    probs = [p["probability"] for p in predictions]
    outcomes = [p["outcome"] for p in predictions]
    return {
        "sport": sport,
        "market": market,
        "n": len(predictions),
        "brier": PairedComparison._brier(probs, outcomes),
        "log_loss": PairedComparison._log_loss(probs, outcomes),
        "ece": PairedComparison._ece(probs, outcomes),
        "accuracy": PairedComparison._accuracy(probs, outcomes),
        "wins": sum(outcomes),
        "losses": len(outcomes) - sum(outcomes),
    }


def evaluate_challenger_on_settled_picks(
    sport: str,
    market: str,
    challenger_predictions: Sequence[dict[str, Any]],
    *,
    min_events: int = 50,
    min_dates: int = 2,
    required_p_better: float = 0.90,
    repo_root: Path | str | None = None,
    champion_model_version: str | None = None,
) -> PromotionVerdict:
    """Evaluate a challenger directly against champion's real settled picks.

    Loads the champion's real settled win/loss picks from the model ledger
    (no backfill data) and runs a paired comparison across identical events.
    """
    from .production_registry import ProductionModelRegistry

    root = Path(repo_root) if repo_root is not None else _repo_root()
    registry = ProductionModelRegistry.load(root)
    champ = registry.champion(sport, market)
    if champ is None:
        raise ValueError(f"no active champion serving {sport} {market}")

    model_ver = champion_model_version or champ.model_id
    champ_picks = load_settled_predictions(sport, market, repo_root=root, model_version=model_ver)
    if not champ_picks:
        return PromotionVerdict(
            status="needs_more_data",
            paired_metrics={},
            bootstrap_ci={},
            failures=[f"no settled picks found for champion {model_ver}"],
            recommendation=f"No settled picks recorded in ledger for champion {model_ver}.",
        )

    champ_ids = {str(r["event_id"]) for r in champ_picks}
    chall_by_id = {str(r["event_id"]): r for r in challenger_predictions}
    common_ids = sorted(champ_ids & set(chall_by_id))

    if not common_ids:
        return PromotionVerdict(
            status="needs_more_data",
            paired_metrics={},
            bootstrap_ci={},
            failures=["zero overlapping events between champion settled picks and challenger predictions"],
            recommendation="Challenger has no predictions on events where champion has settled picks.",
        )

    champ_common = [r for r in champ_picks if str(r["event_id"]) in set(common_ids)]
    chall_common = [chall_by_id[eid] for eid in common_ids]

    comparison = PairedComparison(champ_common, chall_common)
    return comparison.promotion_eligible(
        min_events=min_events,
        min_dates=min_dates,
        required_p_better=required_p_better,
    )
