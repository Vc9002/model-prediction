"""Sampled train/serve feature parity for v8's six features (layers D-I).

For a sample of real holdout games, recompute each feature through the
SERVING path (learned_forward's definitions) and compare against the
TRAINING-side value stored on the walk-forward row. Reports per-feature
max/mean |delta| and any rows where the definitions diverge.

Layer L (orientation): the artifacts record positive_class='home';
learned_forward never reads the field — serving always returns the
home-win probability. Consistent today, but the field is inert at
serving time (noted in the report).

Run from the research worktree:
    env PYTHONPATH=src:. <venv>/python scripts/mlb_v8_feature_parity_sample.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mlb_research_common import pinned_cohort  # noqa: E402
from model_prediction import learned_forward  # noqa: E402
from model_prediction.config import PROJECT_ROOT  # noqa: E402
from model_prediction.features.elo_ratings import build_elo  # noqa: E402
from model_prediction.features.trends import TrendEngine  # noqa: E402

SAMPLE = 40


def _history_before(store, event_id: str, game_date: str):
    # Training convention: history = strictly prior completed DATES in ET
    # (build_walk_forward_rows groups by EASTERN dates — local-time dates
    # on a non-ET machine shift games across days and change history).
    from zoneinfo import ZoneInfo

    games = store.load_games("mlb")
    return [
        g for g in games
        if g.start.astimezone(ZoneInfo("America/New_York")).date().isoformat() < game_date
    ]


def main() -> int:
    from model_prediction.features.base import FeatureStore

    cohort = pinned_cohort()
    exact_holdout = cohort["exact_holdout"]
    store = FeatureStore(PROJECT_ROOT / "data")
    games = {g.event_id: g for g in store.load_games("mlb")}

    sample = [r for r in exact_holdout if r.event_id in games][:SAMPLE]
    print(f"sample: {len(sample)} holdout games")

    report = {"sample": len(sample), "features": {}, "layer_L": {}}
    deltas: dict[str, list[float]] = {}
    mismatches: dict[str, list[str]] = {}

    for row in sample:
        game = games[row.event_id]
        history = _history_before(store, row.event_id, row.date)
        elo = build_elo(history, "mlb")
        trends = TrendEngine(history)
        home_trend = trends.team_trend(game.home_team)
        away_trend = trends.team_trend(game.away_team)

        serve = {
            "elo_probability": elo.expected_home_win(game.home_team, game.away_team),
            "trend_gap": home_trend.offensive_momentum - away_trend.offensive_momentum,
        }
        learned_forward._init_providers()
        park_provider = learned_forward._FEATURE_PROVIDERS.get("park_factor")
        weather_provider = learned_forward._FEATURE_PROVIDERS.get("weather_factor")
        if park_provider:
            serve["park_factor"] = float(
                park_provider(game.home_team, game.away_team, row.event_id, row.date, game.start)
            )
        if weather_provider:
            try:
                serve["weather_factor"] = float(
                    weather_provider(game.home_team, game.away_team, row.event_id, row.date, game.start)
                )
            except Exception as error:  # noqa: BLE001 — availability differs per row by design; count it
                serve["weather_factor"] = None
                mismatches.setdefault("weather_factor", []).append(
                    f"{row.event_id}: {type(error).__name__}"
                )

        for feature, value in serve.items():
            if value is None:
                continue
            train_value = getattr(row, feature)
            delta = abs(float(value) - float(train_value))
            deltas.setdefault(feature, []).append(delta)
            if delta > 1e-9:
                mismatches.setdefault(feature, []).append(
                    f"{row.event_id} train={train_value:.6f} serve={value:.6f}"
                )

    for feature, values in sorted(deltas.items()):
        report["features"][feature] = {
            "rows_compared": len(values),
            "max_abs_delta": round(max(values), 9),
            "mean_abs_delta": round(sum(values) / len(values), 9),
            "exact_matches": sum(1 for v in values if v < 1e-9),
        }
        print(f"{feature:16s} n={len(values):3d} max|d|={max(values):.2e} "
              f"exact={sum(1 for v in values if v < 1e-9)}/{len(values)}")

    # starter_era_gap: training map vs serving live function (same snapshots)
    from model_prediction.features.starter_history import starter_era_gap_live
    from model_prediction.validation import _load_starter_era_map

    starter_map = _load_starter_era_map()
    # Same crosswalk the training map uses: (start, home, away) -> event_id,
    # reversed here to fetch each event's probable starter names.
    names_by_event: dict[str, tuple[str, str]] = {}
    crosswalk = {}
    crosswalk_path = PROJECT_ROOT / "data/processed/mlb/games.jsonl"
    if crosswalk_path.is_file():
        for line in crosswalk_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                g = json.loads(line)
            except json.JSONDecodeError:
                continue
            crosswalk[
                (str(g.get("event_start_utc") or "")[:16], g.get("home_team"), g.get("away_team"))
            ] = g.get("event_id")
    snap_path = PROJECT_ROOT / "data/mlb_statsapi/game_snapshots.jsonl"
    if snap_path.is_file():
        for line in snap_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                snap = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = (
                str(snap.get("game_start_utc") or "")[:16],
                (snap.get("home") or {}).get("team_name"),
                (snap.get("away") or {}).get("team_name"),
            )
            event_id = crosswalk.get(key)
            if event_id:
                names_by_event[event_id] = (
                    (snap.get("home") or {}).get("probable_pitcher_name"),
                    (snap.get("away") or {}).get("probable_pitcher_name"),
                )

    compared = 0
    exact = 0
    starter_mismatches = []
    for row in sample:
        train_gap = starter_map.get(row.event_id)
        if train_gap is None:
            continue
        pair = names_by_event.get(row.event_id)
        if not pair or pair[0] is None or pair[1] is None:
            continue
        try:
            serve_gap = starter_era_gap_live(
                pair[0], pair[1], games[row.event_id].start
            )
        except ValueError:
            continue
        compared += 1
        delta = abs(train_gap - serve_gap)
        if delta < 1e-9:
            exact += 1
        else:
            starter_mismatches.append(
                f"{row.event_id} train={train_gap:.6f} serve={serve_gap:.6f}"
            )
    report["features"]["starter_era_gap"] = {
        "rows_compared": compared,
        "exact_matches": exact,
        "mismatches": starter_mismatches[:5],
    }
    print(f"starter_era_gap    n={compared:3d} exact={exact}/{compared}")

    report["layer_L"] = {
        "note": "artifacts record positive_class='home'; learned_forward never "
        "reads the field and always returns the home-win probability. "
        "Consistent with every shipped artifact today, but the orientation "
        "field is inert at serving time.",
    }
    print(report["layer_L"]["note"])

    out = PROJECT_ROOT / "outputs" / "research" / "mlb_v8_parity" / "feature_parity_sample.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
