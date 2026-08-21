"""Experiment design tooling (model_improvements.md section 11).

- ``ablation_table``: the standard reporting format for a set of feature-set
  variants, so every experiment is compared the same way instead of being
  ordered only by P&L.
- ``bootstrap_by_date``: a bootstrap CI that resamples whole dates/weeks
  rather than individual (correlated) games.
- ``ExperimentLog``: an append-only record of every variant tried, so
  "repeated search raises the bar for a claimed win" is auditable rather
  than a matter of memory.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .economic_gate import bootstrap_ci

ABLATION_COLUMNS = (
    "variant",
    "feature_coverage",
    "brier",
    "log_loss",
    "ece",
    "market_brier",
    "calls_at_executable_ev",
    "net_roi",
    "clv",
    "status",
)


def ablation_table(variants: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Build the standard ablation-report rows for a dict of {variant_name: metrics}.

    ``metrics`` values are looked up loosely (missing keys become ``None``)
    so callers can pass whatever they've measured so far without every
    column being computed up front. Rows are returned in insertion order --
    callers should *not* pre-sort by P&L (section 11: "Do not order the
    table only by P&L; that encourages selection on the noisiest metric.").
    """
    rows = []
    for name, metrics in variants.items():
        row = {"variant": name}
        for column in ABLATION_COLUMNS[1:]:
            row[column] = metrics.get(column)
        rows.append(row)
    return rows


def bootstrap_by_date(
    rows: Sequence[dict[str, Any]],
    *,
    date_key: str,
    value_key: str,
    statistic: Callable[[Sequence[float]], float] = lambda xs: sum(xs) / len(xs) if xs else 0.0,
    n_resamples: int = 1000,
    confidence: float = 0.95,
    seed: int | None = 0,
) -> dict[str, Any]:
    """Bootstrap CI resampling by date, not by row (section 11's explicit warning
    against treating correlated same-day games as independent observations).

    Aggregates ``value_key`` per unique ``date_key`` first (summing same-day
    values, e.g. a day's total P&L), then bootstraps over the per-date series.
    """
    by_date: dict[str, float] = defaultdict(float)
    for row in rows:
        by_date[str(row[date_key])] += float(row[value_key] or 0)
    daily_values = [by_date[day] for day in sorted(by_date)]
    if not daily_values:
        return {"status": "insufficient_sample", "dates": 0}
    lower, upper = bootstrap_ci(
        daily_values, statistic, n_resamples=n_resamples, confidence=confidence, seed=seed
    )
    return {
        "status": "ok",
        "dates": len(daily_values),
        "point_estimate": round(statistic(daily_values), 6),
        "confidence": confidence,
        "ci_low": lower,
        "ci_high": upper,
    }


@dataclass
class ExperimentTrial:
    variant_name: str
    sport: str
    metrics: dict[str, Any]
    recorded_at_utc: str
    notes: str = ""


@dataclass
class ExperimentLog:
    """Append-only trial log for one research question (e.g. "MLB starter quality").

    Persisted as JSONL so a fresh test's development history is reviewable --
    if the locked holdout has already influenced a decision, section 1/11 say
    to relabel it as development evidence and reserve a new final cohort;
    this log is what makes that determination possible instead of relying on
    memory.
    """

    path: Path
    _trials: list[ExperimentTrial] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        if self.path.exists():
            with self.path.open(encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        payload = json.loads(line)
                        self._trials.append(ExperimentTrial(**payload))

    def record(
        self, variant_name: str, sport: str, metrics: dict[str, Any], notes: str = ""
    ) -> ExperimentTrial:
        from .domain import utc_now

        trial = ExperimentTrial(
            variant_name=variant_name,
            sport=sport,
            metrics=metrics,
            recorded_at_utc=utc_now().isoformat().replace("+00:00", "Z"),
            notes=notes,
        )
        self._trials.append(trial)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(vars(trial), default=str) + "\n")
        return trial

    def trial_count(self, sport: str | None = None) -> int:
        if sport is None:
            return len(self._trials)
        return sum(1 for trial in self._trials if trial.sport == sport)

    def trials(self, sport: str | None = None) -> list[ExperimentTrial]:
        if sport is None:
            return list(self._trials)
        return [trial for trial in self._trials if trial.sport == sport]
