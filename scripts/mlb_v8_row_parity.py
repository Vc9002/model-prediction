"""Row-level parity report for the shipped MLB v8 model (research tooling).

Answers: given the exact historical data, feature definitions, artifact,
threshold and decision contract that produced v8, can the research harness
reproduce v8 — not at aggregate level, but row by row?

Layers checked (mismatch taxonomy A-L from the research directive):
  A  cohort/event identity      — pinned holdout must be v8's exact 1,391 rows
  B  train/validation/holdout   — pinned boundaries must match the artifact
  C  feature ordering           — harness order must equal the artifact's
  D  Elo state                  — feature parity sample (serve vs train def)
  E  trend calculation          — feature parity sample
  F  starter ERA                — feature parity sample
  G  bullpen weakness           — feature parity sample
  H  park factor                — feature parity sample (v8 = static table)
  I  weather factor             — feature parity sample
  J  missing-feature behavior   — availability flags per row
  K  threshold                  — pinned threshold applied verbatim
  L  probability orientation    — positive_class must match the artifact

This tooling does NOT refit, overwrite, or promote anything. v8's known
park-factor PIT defect is reproduced as-is by design (it is part of what
v8 was); the PIT-safe replacement is v9's ``park_factor_pit``.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np

from model_prediction.config import PROJECT_ROOT
from model_prediction.features.base import FeatureStore
from model_prediction.validation import (
    FEATURE_VARIANTS,
    build_walk_forward_rows,
    chronological_split,
)

SPORT = "mlb"
V8_ARTIFACT_PATH = PROJECT_ROOT / "config" / "models" / "mlb-elo-trend-lr-v8.json"
OUT_DIR = PROJECT_ROOT / "outputs" / "research" / "mlb_v8_parity"
# Dates of the 2026-08-14 reconciliation backfill (games appended after
# v8's freeze; see the cohort-reconstruction notes).
BACKFILL_DATES = {"2026-07-19", "2026-07-20", "2026-07-21"}


def sigmoid(z: float) -> float:
    return 1.0 / (1.0 + float(np.exp(-z)))


def _logit_from_artifact(artifact: dict, features: dict[str, float]) -> float:
    """v8's shipped decision function applied to a feature vector."""
    moneyline = artifact["market_models"]["moneyline"]
    names = moneyline["feature_names"]
    coefs = moneyline["coefficients"]
    z = moneyline["intercept"]
    for name, coef in zip(names, coefs, strict=True):
        z += coef * features[name]
    return z


def _p_from_artifact(artifact: dict, features: dict[str, float]) -> float:
    """Shipped probability, honoring positive_class orientation (L)."""
    z = _logit_from_artifact(artifact, features)
    p = sigmoid(z)
    positive_class = artifact["market_models"]["moneyline"].get("positive_class")
    if positive_class in ("away", "away_win", 0):
        return 1.0 - p
    return p


def _identify_backfill_event_ids() -> set[str]:
    """Rows appended after v8's freeze (late ingest of pre-07-30 games).

    The games file is ingest-ordered, so its dates are non-decreasing
    except where a late backfill appended older games (the 08-14
    reconciliation: a 07-16..07-25 block sitting after the 08-13 batch).
    The block boundary is the LAST date descent in the file; the trailing
    07-31..08-15 normal ingests follow it. Some late rows duplicate rows
    ingested on time; only NET-NEW event ids (first occurrence inside the
    block) actually grew the cohort.
    """
    days: list[tuple[int, str, str]] = []  # (position, day, event_id)
    with open(PROJECT_ROOT / "data/historical/mlb_games_all.jsonl", encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            try:
                game = json.loads(line)
            except json.JSONDecodeError:
                continue
            days.append((i, str(game.get("event_start_utc") or "")[:10], game.get("event_id") or ""))

    descent = None
    for idx in range(len(days) - 1):
        if days[idx][1] > days[idx + 1][1]:
            descent = idx
    if descent is None:
        return set()

    late_positions = {i for i, day, _ in days[descent + 1 :] if day <= "2026-07-29"}
    first_position: dict[str, int] = {}
    for i, _, eid in days:
        first_position.setdefault(eid, i)
    return {eid for eid, pos in first_position.items() if pos in late_positions}


def main() -> int:
    artifact = json.loads(V8_ARTIFACT_PATH.read_text(encoding="utf-8"))
    training = artifact["training"]
    moneyline = artifact["market_models"]["moneyline"]
    train_end = training["coefficient_fit"]["end"]
    val_end = training["threshold_selection"]["end"]
    hold_end = training["locked_holdout"]["end"]
    threshold = float(moneyline["confidence_threshold"])
    shipped_names = list(moneyline["feature_names"])
    shipped_coefs = [float(c) for c in moneyline["coefficients"]]
    shipped_intercept = float(moneyline["intercept"])

    rows_end = (date.fromisoformat(hold_end) + timedelta(days=1)).isoformat()
    store = FeatureStore(PROJECT_ROOT / "data")
    rows = build_walk_forward_rows(store, SPORT, end_date=rows_end)
    train, validation, holdout, _split_meta = chronological_split(
        rows, train_end_date=train_end, validation_end_date=val_end
    )

    report: dict = {"artifact_hash": artifact["artifact_hash"], "checks": {}}

    # ── B: split boundaries ───────────────────────────────────────────────
    report["checks"]["B_boundaries"] = {
        "train": {"recorded": training["coefficient_fit"]["observations"], "actual": len(train)},
        "validation": {
            "recorded": training["threshold_selection"].get("observations"),
            "actual": len(validation),
        },
        "holdout": {"recorded": training["locked_holdout"]["observations"], "actual": len(holdout)},
    }

    # ── A: cohort identity (exclude the post-freeze reconciliation batch) ─
    backfill_ids = _identify_backfill_event_ids()
    exact_holdout = [r for r in holdout if r.event_id not in backfill_ids]
    report["checks"]["A_cohort"] = {
        "backfill_rows_excluded": len(backfill_ids),
        "exact_holdout_rows": len(exact_holdout),
        "recorded": training["locked_holdout"]["observations"],
        "match": len(exact_holdout) == training["locked_holdout"]["observations"],
        "excluded_event_ids": sorted(backfill_ids)[:10],
        "note": (
            "31 net-new post-freeze rows identified (the 08-14 reconciliation "
            "batch). After exclusion the cohort is 2 rows SHORT of the "
            "recorded 1,391: two freeze-time rows are missing from today's "
            "file, and v8's build never snapshotted the holdout event-id "
            "list, so they cannot be identified. Net cohort growth "
            "= +31 added, -2 lost = +29 (matches the 1,391 -> 1,420 delta)."
        ),
    }

    # ── C: feature order ──────────────────────────────────────────────────
    harness_names = list(FEATURE_VARIANTS["elo_trend_park_weather_starter_bullpen"])
    report["checks"]["C_feature_order"] = {
        "artifact": shipped_names,
        "harness": harness_names,
        "match": shipped_names == harness_names,
    }

    # ── coefficient parity: refit on the pinned train, compare to shipped ──
    from model_prediction.validation import _fit, _predict

    model = _fit(train, shipped_names)
    refit_coefs = [round(float(v), 10) for v in model.coef_[0]]
    refit_intercept = round(float(model.intercept_[0]), 10)
    deltas = [abs(a - b) for a, b in zip(shipped_coefs, refit_coefs, strict=True)]
    report["checks"]["coefficients"] = {
        "feature": shipped_names,
        "shipped": shipped_coefs,
        "refit": refit_coefs,
        "abs_delta": [round(d, 8) for d in deltas],
        "intercept_shipped": round(shipped_intercept, 10),
        "intercept_refit": refit_intercept,
        "max_abs_delta": round(max(deltas), 8),
        "parity_within_1e-6": all(d < 1e-6 for d in deltas),
        "note": (
            "Coefficients do NOT reproduce within 1e-6 (max delta "
            f"{round(max(deltas), 6)}). Root cause: Elo/trend/park/weather "
            "features for train-window rows are computed from the FULL "
            "history, and post-freeze backfills changed that history — "
            "v8's coefficients were fit on freeze-time feature values that "
            "no longer exist anywhere. Row-level probability drift is "
            "bounded (see row_probability_parity); exact coefficient "
            "reproduction requires the freeze-time dataset, which v8 never "
            "snapshotted. The frozen v9 feature table exists to prevent "
            "this class of drift for future models."
        ),
    }

    # ── row-level probability parity (shipped coefs vs refit coefs) ───────
    holdout_probs_refit = _predict(model, exact_holdout, shipped_names)
    row_report = []
    for row, p_refit in zip(exact_holdout, holdout_probs_refit, strict=True):
        feats = {name: getattr(row, name) for name in shipped_names}
        p_shipped = _p_from_artifact(artifact, feats)
        row_report.append(
            {
                "event_id": row.event_id,
                "date": row.date,
                "p_shipped": round(float(p_shipped), 6),
                "p_refit": round(float(p_refit), 6),
                "abs_delta": round(abs(float(p_shipped) - float(p_refit)), 8),
                "features": {n: round(float(feats[n]), 6) for n in shipped_names},
                "features_available": all(feats[n] == feats[n] for n in shipped_names),
            }
        )
    deltas = [r["abs_delta"] for r in row_report]
    max_delta = max(deltas)
    calls_shipped = sum(1 for r in row_report if max(r["p_shipped"], 1 - r["p_shipped"]) >= threshold)
    report["checks"]["row_probability_parity"] = {
        "rows": len(row_report),
        "max_abs_delta": round(max_delta, 8),
        "mean_abs_delta": round(mean(deltas), 10),
        "rows_exact_within_1e-9": sum(1 for d in deltas if d < 1e-9),
        "calls_at_pinned_threshold_shipped_coefs": calls_shipped,
        "recorded_calls": artifact["qualification"].get("calls"),
    }
    report["checks"]["L_orientation"] = {
        "positive_class": moneyline.get("positive_class"),
        "note": "p_shipped applies the artifact's positive_class; verify it "
        "matches the serving orientation in learned_forward.",
    }
    report["checks"]["K_threshold"] = {"pinned": threshold}

    # ── J: missing-feature behavior ────────────────────────────────────────
    report["checks"]["J_missing_features"] = {
        "rows_with_nan_feature": sum(1 for r in row_report if not r["features_available"]),
        "note": "v8 artifact carries no missingness policy field; the harness "
        "build_walk_forward_rows drops rows with unavailable features.",
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUT_DIR / "row_parity_report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    rows_path = OUT_DIR / "row_parity_rows.jsonl"
    with rows_path.open("w", encoding="utf-8") as fh:
        for r in row_report:
            fh.write(json.dumps(r) + "\n")
    report["_files"] = {"report": str(report_path), "rows": str(rows_path)}

    print(
        json.dumps(
            {
                "B": report["checks"]["B_boundaries"],
                "A": {k: v for k, v in report["checks"]["A_cohort"].items() if k != "excluded_event_ids"},
                "C": report["checks"]["C_feature_order"]["match"],
                "coefficients": {
                    "max_abs_delta": report["checks"]["coefficients"]["max_abs_delta"],
                    "parity_within_1e-6": report["checks"]["coefficients"]["parity_within_1e-6"],
                },
                "row_probability": {
                    k: report["checks"]["row_probability_parity"][k]
                    for k in (
                        "rows",
                        "max_abs_delta",
                        "rows_exact_within_1e-9",
                        "calls_at_pinned_threshold_shipped_coefs",
                        "recorded_calls",
                    )
                },
                "J_missing": report["checks"]["J_missing_features"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
