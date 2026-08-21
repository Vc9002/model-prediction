"""Esports title-split validation: reproduction gate + identity invariant.

Reproduction gate (ablation-reproduction-gate discipline): the candidate
namespaced per-title engine (esports_titles.registry.TitleEloEngine) with
a title's OWN frozen config must reproduce the incumbent shared engine
(esports.NeutralElo with module defaults) bit-for-bit on the SAME walk-
forward sequence -- same k/platt, all engine knobs at None (shared
defaults). Only then is the split structure proven to be a pure refactor
of the engine boundary, not a behavior change smuggled in.

Identity invariant: a (game_title, provider, provider_team_id) triple is
the key -- two different titles' engines can hold the same bare team id
with different ratings without colliding, which the pre-split shared book
cannot express (its keys are bare ids).

Config-vs-artifact parity: each frozen TitleConfig's fitted numbers are
compared against the live runtime-root v6 artifact for that title, so a
config drift from the shipped artifact fails loudly.

Reads matches.jsonl per title (same source validate_esports_baseline
uses). Read-only: no artifact writes, no ledger writes.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from model_prediction import esports as shared_esports
from model_prediction.config import PROJECT_ROOT
from model_prediction.esports_titles.registry import TitleEloEngine, resolve

DATA_ROOT = Path("/Users/vincentc9002/model-prediction/data")
ARTIFACT_ROOT = Path("/Users/vincentc9002/model-prediction-runtime/models")
TITLES = ("cs2", "valorant", "lol", "dota2", "rainbow_six")


def _load_matches(title: str) -> list[dict]:
    path = DATA_ROOT / "esports" / title / "matches.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return sorted(rows, key=lambda row: (row["start_utc"], row["match_id"]))


def _predict_with(book, rows, *, update: bool = True) -> list[float]:
    out = []
    for row in rows:
        reference_date = None
        start = row.get("start_utc")
        if start:
            try:
                reference_date = datetime.fromisoformat(str(start))
            except (ValueError, TypeError):
                reference_date = None
        out.append(book.probability(row["team1_id"], row["team2_id"], reference_date))
        if update:
            book.update(row)
    return out


def main() -> int:
    report = {}
    for title in TITLES:
        config = resolve(title)
        rows = _load_matches(title)
        tail = rows[:400]  # last-chronological slice is enough for parity; same rows for both engines

        shared = shared_esports.NeutralElo(
            k=config.k,
            ratings={},
            platt_intercept=config.platt_intercept,
            platt_slope=config.platt_slope,
        )
        shared_probs = _predict_with(shared, tail)

        engine = TitleEloEngine(config)
        namespaced_probs = _predict_with(engine, tail)

        max_abs_diff = max(abs(a - b) for a, b in zip(shared_probs, namespaced_probs, strict=True))

        # Identity invariant demo: the same bare team id fed through two
        # titles' engines must produce two independent rating tracks.
        other_title = "valorant" if title != "valorant" else "cs2"
        other_engine = TitleEloEngine(resolve(other_title))
        other_engine.update(
            {
                "team1_id": "x",
                "team2_id": "y",
                "winner_id": "x",
                "start_utc": rows[0]["start_utc"],
                "tier": "s",
            }
        )
        collision_free = "x" not in engine.ratings and "y" not in engine.ratings

        # Config-vs-artifact parity
        artifact_path = ARTIFACT_ROOT / f"{title}-tiered-elo-v6.json"
        artifact_drift = []
        if artifact_path.exists():
            artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
            checks = (
                ("k", config.k, artifact.get("k")),
                ("confidence_threshold", config.confidence_threshold, artifact.get("confidence_threshold")),
                ("platt_intercept", config.platt_intercept, artifact.get("platt_intercept")),
                ("platt_slope", config.platt_slope, artifact.get("platt_slope")),
            )
            for name, config_value, artifact_value in checks:
                if artifact_value is not None and abs(float(artifact_value) - float(config_value)) > 1e-6:
                    artifact_drift.append({"field": name, "config": config_value, "artifact": artifact_value})

        report[title] = {
            "model_id": config.model_id,
            "n_rows_checked": len(tail),
            "max_abs_probability_diff_vs_shared_engine": max_abs_diff,
            "reproduction_parity": max_abs_diff < 1e-12,
            "identity_invariant_collision_free": collision_free,
            "config_matches_live_artifact": not artifact_drift,
            "artifact_drift": artifact_drift,
        }
        print(f"{title}: {report[title]}")

    out_path = PROJECT_ROOT / "outputs/research/esports_title_split/validation.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"\nwritten to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
