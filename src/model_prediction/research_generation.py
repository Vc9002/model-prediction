"""Independent, fitted research candidates for every registered production market.

This is historical model development, not prospective qualification. Source
scoreboards lack historical observation timestamps and executable quotes. The
runner consequently never claims PIT, incumbent superiority, or profitability.
It does not import the execution, ledger-writing, or promotion surfaces.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import joblib  # type: ignore[import-untyped]
import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import softmax
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .rebuild.validation import date_cluster_split

SEED = 20260909
ESPORTS = {"CS2", "DOTA2", "LOL", "VALORANT", "RAINBOW_SIX"}
THREE_WAY = {"SOCCER", "KBO", "NPB"}
FEATURE_NAMES = (
    "rating_gap",
    "log_history_a",
    "log_history_b",
    "recent_win_rate_a",
    "recent_win_rate_b",
    "short_win_rate_a",
    "short_win_rate_b",
    "score_for_a",
    "score_for_b",
    "score_against_a",
    "score_against_b",
    "score_margin_a",
    "score_margin_b",
    "score_sd_a",
    "score_sd_b",
    "log_days_since_available_result_a",
    "log_days_since_available_result_b",
    "surface_win_rate_a",
    "surface_win_rate_b",
    "surface_count_a",
    "surface_count_b",
)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class Game:
    event_id: str
    date: str
    a: str
    b: str
    score_a: float
    score_b: float
    surface: str = "unknown"


@dataclass(frozen=True)
class ResearchRow:
    event_id: str
    date: str
    features: tuple[float, ...]
    outcome: int
    margin: float
    total: float
    feature_max_event_date: str


def source_path(root: Path, sport: str) -> Path:
    if sport in ESPORTS:
        return root / "data/esports" / sport.lower() / "matches.jsonl"
    if sport in {"KBO", "NPB"}:
        return root / "data/international_baseball" / sport.lower() / "games.jsonl"
    return root / "data/historical" / f"{sport.lower()}_games_all.jsonl"


def normalize_game(raw: dict[str, Any], sport: str, as_of: str) -> Game:
    """Stable participant orientation must never depend on the winner label."""
    timestamp = raw.get("event_start_utc") or raw.get("start_utc") or raw.get("game_date")
    if not timestamp:
        raise ValueError("missing_event_date")
    parsed = datetime.fromisoformat(str(timestamp))
    day = (parsed.astimezone(UTC) if parsed.tzinfo else parsed).date().isoformat()
    if day >= as_of:
        raise ValueError("current_or_future_date")
    if raw.get("season_type") in {"preseason", "all-star"}:
        raise ValueError("non_regular_competition")
    # The legacy MLB scoreboard includes unlabeled spring-training games.
    # Use the incumbent FeatureStore's conservative April--October population.
    if sport == "MLB" and not "04-01" <= day[5:] <= "10-31":
        raise ValueError("mlb_outside_april_october")
    if str(raw.get("status", "completed")).lower() not in {"completed", "final", "closed"}:
        raise ValueError("not_completed")
    event_id = raw.get("event_id") or raw.get("match_id") or raw.get("game_id")
    if not event_id:
        raise ValueError("missing_event_id")
    league = str(raw.get("league", sport)) if sport in {"SOCCER", "TENNIS"} else sport
    if sport == "TENNIS":
        winner = str(raw.get("winner_id") or raw.get("winner") or "")
        loser = str(raw.get("loser_id") or raw.get("loser") or "")
        a, b = sorted((winner, loser))
        sa, sb = float(a == winner), float(b == winner)
    elif sport in ESPORTS:
        a, b = str(raw.get("team1_id") or ""), str(raw.get("team2_id") or "")
        sa, sb = float(raw["team1_score"]), float(raw["team2_score"])
        if sa == sb or str(raw.get("winner_id")) != (a if sa > sb else b):
            raise ValueError("ambiguous_winner")
    else:
        a = str(raw.get("home_team_id") or raw.get("home_team") or "")
        b = str(raw.get("away_team_id") or raw.get("away_team") or "")
        sa, sb = float(raw["home_score"]), float(raw["away_score"])
    if not a or not b or a == b:
        raise ValueError("invalid_participants")
    if not math.isfinite(sa + sb) or min(sa, sb) < 0:
        raise ValueError("invalid_scores")
    if sa == sb and sport not in THREE_WAY:
        raise ValueError("tie_without_three_way_target")
    return Game(
        str(event_id),
        day,
        f"{league}:{a}",
        f"{league}:{b}",
        sa,
        sb,
        str(raw.get("surface") or "unknown").lower(),
    )


def load_games(path: Path, sport: str, as_of: str) -> tuple[list[Game], dict[str, Any]]:
    by_id: dict[str, Game] = {}
    conflicts: set[str] = set()
    excluded: Counter[str] = Counter()
    raw_count = 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        raw_count += 1
        try:
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise TypeError("non_object_record")
            game = normalize_game(raw, sport, as_of)
        except (ValueError, TypeError, KeyError) as exc:
            excluded[str(exc)] += 1
            continue
        if game.event_id in by_id:
            if by_id[game.event_id] != game:
                conflicts.add(game.event_id)
            else:
                excluded["duplicate_identical"] += 1
        else:
            by_id[game.event_id] = game
    for event_id in conflicts:
        del by_id[event_id]
    games = sorted(by_id.values(), key=lambda g: (g.date, g.event_id))
    return games, {
        "source": str(path),
        "source_sha256": file_hash(path),
        "raw_rows": raw_count,
        "normalized_games": len(games),
        "conflicting_event_ids": sorted(conflicts),
        "excluded": dict(excluded),
        "evidence_origin": "historical_backtest",
        "historical_observation_times_verified": False,
    }


@dataclass(frozen=True)
class Fixture:
    """Unplayed event in the same participant namespace as normalized history.

    There are deliberately no score or outcome fields. ``a`` is home except
    esports (source team1) and tennis (lexically sorted player IDs).
    """

    event_id: str
    date: str
    a: str
    b: str
    surface: str = "unknown"


class FeatureState:
    """The shared, target-free calculation used for training and inference."""

    def __init__(self) -> None:
        self.history: dict[str, deque[tuple[float, float, float]]] = defaultdict(lambda: deque(maxlen=20))
        self.surfaces: dict[tuple[str, str], deque[float]] = defaultdict(lambda: deque(maxlen=20))
        self.counts: Counter[str] = Counter()
        self.rating: dict[str, float] = defaultdict(float)
        self.last_day: dict[str, str] = {}
        self.latest = ""

    def observe(self, past: Game) -> None:
        y = 1.0 if past.score_a > past.score_b else 0.0 if past.score_a < past.score_b else 0.5
        p = 1 / (1 + 10 ** ((self.rating[past.b] - self.rating[past.a]) / 400))
        change = 20 * (y - p)  # Fixed Elo feature recipe, not a fitted coefficient.
        self.rating[past.a] += change
        self.rating[past.b] -= change
        for team, sf, sa, win in (
            (past.a, past.score_a, past.score_b, y),
            (past.b, past.score_b, past.score_a, 1 - y),
        ):
            self.history[team].append((sf, sa, win))
            self.surfaces[team, past.surface].append(win)
            self.counts[team] += 1
            self.last_day[team] = past.date
        self.latest = past.date

    def features(self, fixture: Fixture) -> tuple[float, ...] | None:
        if min(self.counts[fixture.a], self.counts[fixture.b]) < 3:
            return None

        def state(team: str) -> list[float]:
            h = np.asarray(self.history[team])
            surface = list(self.surfaces[team, fixture.surface])
            rest = (datetime.fromisoformat(fixture.date) - datetime.fromisoformat(self.last_day[team])).days
            return [
                math.log1p(self.counts[team]),
                float(h[:, 2].mean()),
                float(h[-5:, 2].mean()),
                float(h[:, 0].mean()),
                float(h[:, 1].mean()),
                float((h[:, 0] - h[:, 1]).mean()),
                float(h[:, 0].std()),
                math.log1p(rest),
                (sum(surface) + 1) / (len(surface) + 2),
                float(len(surface)),
            ]

        a, b = state(fixture.a), state(fixture.b)
        return (self.rating[fixture.a] - self.rating[fixture.b], *[v for pair in zip(a, b) for v in pair])


def fixture_features(
    games: list[Game], fixtures: list[Fixture], *, available_before: str | None = None
) -> dict[str, tuple[tuple[float, ...], str] | None]:
    """Build features without inserting fabricated results into history.

    ``available_before`` is an exclusive UTC date cutoff on results; callers
    collecting a prospective slate must supply their actual observation date.
    Each fixture also retains the training recipe's two-day result embargo.
    This proves the calculation, not when historical source rows first existed.
    """
    if len({f.event_id for f in fixtures}) != len(fixtures):
        raise ValueError("duplicate_fixture_event_id")
    state = FeatureState()
    pending = deque(sorted(games, key=lambda g: (g.date, g.event_id)))
    result = {}
    for fixture in sorted(fixtures, key=lambda f: (f.date, f.event_id)):
        if not fixture.a or not fixture.b or fixture.a == fixture.b or not fixture.event_id:
            raise ValueError("invalid_fixture_identity")
        cutoff = (datetime.fromisoformat(fixture.date) - timedelta(days=1)).date().isoformat()
        if available_before is not None:
            datetime.strptime(available_before, "%Y-%m-%d").replace(tzinfo=UTC)
            cutoff = min(cutoff, available_before)
        while pending and pending[0].date < cutoff:
            state.observe(pending.popleft())
        features = state.features(fixture)
        result[fixture.event_id] = None if features is None else (features, state.latest)
    return result


def build_rows(games: list[Game], sport: str) -> list[ResearchRow]:
    """Historical labels joined only after target-free features are calculated."""
    fixtures = [Fixture(g.event_id, g.date, g.a, g.b, g.surface) for g in games]
    built = fixture_features(games, fixtures)
    result = []
    for game in sorted(games, key=lambda g: (g.date, g.event_id)):
        pair = built[game.event_id]
        if pair is None:
            continue
        features, latest = pair
        outcome = 2 if game.score_a > game.score_b else 1 if game.score_a == game.score_b else 0
        if sport not in THREE_WAY:
            outcome = int(game.score_a > game.score_b)
        result.append(
            ResearchRow(
                game.event_id,
                game.date,
                features,
                outcome,
                game.score_a - game.score_b,
                game.score_a + game.score_b,
                latest,
            )
        )
    return result


def partition(
    rows: list[ResearchRow], *, split_event_ids: dict[str, list[str]] | None = None
) -> dict[str, list[int]]:
    """60/10/10/20 complete-date train/select/calibrate/evaluate split."""
    if split_event_ids is not None:
        names = ("train", "select", "calibrate", "test")
        positions = {row.event_id: i for i, row in enumerate(rows)}
        if set(split_event_ids) != set(names) or len(positions) != len(rows):
            raise ValueError("invalid_fixed_partition_identity")
        masks = {name: [positions[event] for event in split_event_ids[name]] for name in names}
        seen: set[int] = set()
        previous = ""
        for name in names:
            indices = masks[name]
            if len(indices) < 20 or len(set(indices)) != len(indices) or seen.intersection(indices):
                raise ValueError("invalid_fixed_partition_membership")
            days = [rows[i].date for i in indices]
            if min(days) <= previous:
                raise ValueError("invalid_fixed_partition_dates")
            seen.update(indices)
            previous = max(days)
        if len(seen) != len(rows):
            raise ValueError("incomplete_fixed_partition_coverage")
        return masks
    dates = [r.date for r in rows]
    n = len(set(dates))
    if n < 50:
        raise ValueError("insufficient_distinct_dates")
    train, validation, test = date_cluster_split(dates, test_size=n // 5, calib_size=n // 5)
    split = len(validation) // 2
    groups = {"train": train, "select": validation[:split], "calibrate": validation[split:], "test": test}
    sets = {name: set(days) for name, days in groups.items()}
    masks = {name: [i for i, row in enumerate(rows) if row.date in days] for name, days in sets.items()}
    if any(len(indices) < 20 for indices in masks.values()):
        raise ValueError("insufficient_split_rows")
    return masks


def classification_losses(y: np.ndarray, p: np.ndarray, classes: np.ndarray) -> np.ndarray:
    positions = {int(c): i for i, c in enumerate(classes)}
    if any(int(v) not in positions for v in y):
        raise ValueError("unseen_target_class")
    return -np.log(np.clip(p[np.arange(len(y)), [positions[int(v)] for v in y]], 1e-12, 1))


def temperature_scale(p: np.ndarray, temperature: float) -> np.ndarray:
    if not math.isfinite(temperature) or temperature <= 0:
        raise ValueError("invalid_temperature")
    return softmax(np.log(np.clip(p, 1e-12, 1)) / temperature, axis=1)


def cluster_interval(deltas: np.ndarray, dates: list[str]) -> dict[str, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for day, delta in zip(dates, deltas, strict=True):
        grouped[day].append(float(delta))
    sums = np.array([sum(v) for v in grouped.values()])
    counts = np.array([len(v) for v in grouped.values()])
    rng = np.random.default_rng(SEED)
    draws = rng.integers(0, len(sums), size=(1000, len(sums)))
    means = sums[draws].sum(axis=1) / counts[draws].sum(axis=1)
    return {
        "mean": float(deltas.mean()),
        "lower_95": float(np.quantile(means, 0.025)),
        "upper_95": float(np.quantile(means, 0.975)),
    }


def variants(classification: bool) -> list[tuple[str, Any]]:
    if classification:
        linear = [
            (f"logistic_C{c}", make_pipeline(StandardScaler(), LogisticRegression(C=c, max_iter=2000)))
            for c in (0.1, 1.0, 10.0)
        ]
        trees = [
            (
                f"hist_tree_{leaves}",
                HistGradientBoostingClassifier(
                    max_leaf_nodes=leaves,
                    max_iter=150,
                    l2_regularization=10,
                    early_stopping=False,
                    random_state=SEED,
                ),
            )
            for leaves in (7, 15)
        ]
    else:
        linear = [
            (f"ridge_{alpha}", make_pipeline(StandardScaler(), Ridge(alpha=alpha)))
            for alpha in (1.0, 10.0, 100.0)
        ]
        trees = [
            (
                f"hist_tree_{leaves}",
                HistGradientBoostingRegressor(
                    max_leaf_nodes=leaves,
                    max_iter=150,
                    l2_regularization=10,
                    early_stopping=False,
                    random_state=SEED,
                ),
            )
            for leaves in (7, 15)
        ]
    return linear + trees


def train_candidate(
    rows: list[ResearchRow],
    sport: str,
    market: str,
    incumbent: str,
    directory: Path,
    feature_names: tuple[str, ...] = FEATURE_NAMES,
    source_metadata: dict[str, Any] | None = None,
    *,
    model_id_override: str | None = None,
    research_family: str | None = None,
    split_event_ids: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Select without test access, persist fitted model, then evaluate once.

    The statistical reference is a constant training prior, never mislabeled
    as the incumbent. Missing exact incumbent reproduction blocks promotion.
    """
    directory.mkdir(parents=True, exist_ok=False)
    masks = partition(rows, split_event_ids=split_event_ids)
    x = np.asarray([r.features for r in rows], dtype=float)
    if x.shape[1] != len(feature_names) or not np.isfinite(x).all():
        raise ValueError("invalid_feature_matrix")
    classification = market in {"moneyline", "nrfi"}
    y = np.asarray(
        [r.outcome if classification else r.margin if market == "spread" else r.total for r in rows]
    )
    tr, va, ca, te = (masks[k] for k in ("train", "select", "calibrate", "test"))
    expected_classes = np.array([0, 1, 2] if sport in THREE_WAY and market == "moneyline" else [0, 1])
    if classification and not np.array_equal(np.unique(y[tr]), expected_classes):
        raise ValueError("missing_training_target_class")
    baseline = DummyClassifier(strategy="prior") if classification else DummyRegressor(strategy="median")
    baseline.fit(x[tr], y[tr])
    candidates: list[tuple[float, str, Any]] = []
    selection = []
    for name, estimator in variants(classification):
        estimator.fit(x[tr], y[tr])
        if classification:
            loss = float(
                classification_losses(y[va], estimator.predict_proba(x[va]), estimator.classes_).mean()
            )
        else:
            loss = float(np.abs(estimator.predict(x[va]) - y[va]).mean())
        selection.append({"variant": name, "selection_loss": loss})
        candidates.append((loss, name, estimator))
    _, chosen_name, model = min(candidates, key=lambda item: (item[0], item[1]))
    temperature = 1.0
    residuals: list[float] = []
    if classification:
        cal_prob = model.predict_proba(x[ca])
        opt = minimize_scalar(
            lambda t: classification_losses(
                y[ca], temperature_scale(cal_prob, float(t)), model.classes_
            ).mean(),
            bounds=(0.5, 3.0),
            method="bounded",
        )
        if not opt.success:
            raise ValueError("calibration_failed")
        temperature = float(opt.x)
    else:
        residuals = (y[ca] - model.predict(x[ca])).tolist()
    model_id = model_id_override or f"{sport.lower()}-{market}-research-20260909-v1"
    recipe = {
        "model_id": model_id,
        "variant": chosen_name,
        "features": list(feature_names),
        "temperature": temperature,
        "residuals": residuals,
        "seed": SEED,
        "classes": model.classes_.tolist() if classification else None,
        "class_labels": (
            ["b_win", "draw", "a_win"]
            if sport in THREE_WAY and classification
            else ["yrfi", "nrfi"]
            if market == "nrfi"
            else ["b_win", "a_win"]
            if classification
            else None
        ),
        "split_event_ids": {k: [rows[i].event_id for i in ids] for k, ids in masks.items()},
        "dataset_hash": digest([asdict(r) for r in rows]),
        "source": source_metadata,
        "incumbent_model_id": incumbent,
        "evidence_origin": "historical_backtest",
        "test_is_new_prospective_evidence": False,
        "selection": selection,
        "target": "three_way_outcome"
        if sport in THREE_WAY and classification
        else "nrfi"
        if market == "nrfi"
        else "a_win"
        if classification
        else market,
    }
    if research_family is not None:
        recipe["research_family"] = research_family
    # Serialization happens before any test outcomes are scored.
    model_path = directory / "model.joblib"
    joblib.dump({"model": model, "recipe": recipe}, model_path)
    recipe["model_file_sha256"] = file_hash(model_path)
    (directory / "recipe.json").write_text(json.dumps(recipe, indent=2, allow_nan=False) + "\n")
    loaded = joblib.load(model_path)
    restored = loaded["model"]
    predict_method = "predict_proba" if classification else "predict"
    prediction = getattr(model, predict_method)(x[te])
    reloaded_prediction = getattr(restored, predict_method)(x[te])
    if not np.array_equal(prediction, reloaded_prediction):
        raise RuntimeError("serialization_prediction_parity_failed")
    test_dates = [rows[i].date for i in te]
    if classification:
        p = temperature_scale(prediction, temperature)
        reference = baseline.predict_proba(x[te])
        losses = classification_losses(y[te], p, model.classes_)
        ref_losses = classification_losses(y[te], reference, baseline.classes_)
        positions = {int(c): i for i, c in enumerate(model.classes_)}
        onehot = np.eye(len(model.classes_))[[positions[int(v)] for v in y[te]]]
        confidence = p.max(axis=1)
        correctness = model.classes_[p.argmax(axis=1)] == y[te]
        ece = 0.0
        for low in np.linspace(0, 0.9, 10):
            mask = (confidence >= low) & (confidence < low + 0.1 + (1e-10 if low > 0.89 else 0))
            if mask.any():
                ece += float(mask.mean() * abs(confidence[mask].mean() - correctness[mask].mean()))
        metrics = {
            "log_loss": float(losses.mean()),
            "multiclass_brier_sum": float(((p - onehot) ** 2).sum(1).mean()),
            "accuracy": float(correctness.mean()),
            "top_label_ece": ece,
            "constant_prior_log_loss": float(ref_losses.mean()),
        }
        if len(model.classes_) == 2:
            metrics["binary_brier"] = float(((p[:, 1] - y[te]) ** 2).mean())
        outputs = p.tolist()
    else:
        residual_array = np.asarray(residuals)
        corrected = prediction + float(np.median(residual_array))
        losses = np.abs(corrected - y[te])
        ref_losses = np.abs(baseline.predict(x[te]) - y[te])
        low, high = np.quantile(residual_array, [0.1, 0.9])
        metrics = {
            "mae": float(losses.mean()),
            "rmse": float(np.sqrt(((corrected - y[te]) ** 2).mean())),
            "bias": float((corrected - y[te]).mean()),
            "interval_80_coverage": float(
                ((y[te] >= prediction + low) & (y[te] <= prediction + high)).mean()
            ),
            "constant_median_mae": float(ref_losses.mean()),
        }
        outputs = corrected.tolist()
    with (directory / "evaluation_predictions.jsonl").open("w") as handle:
        for i, prediction_value in zip(te, outputs, strict=True):
            handle.write(
                json.dumps(
                    {
                        "event_id": rows[i].event_id,
                        "date": rows[i].date,
                        "prediction": prediction_value,
                        "target": float(y[i]),
                        "feature_max_event_date": rows[i].feature_max_event_date,
                    }
                )
                + "\n"
            )
    report = {
        "model_id": model_id,
        "sport": sport,
        "market": market,
        "incumbent": incumbent,
        "status": "TRAINED_RESEARCH_ONLY",
        "selected_variant": chosen_name,
        "rows": len(rows),
        "splits": {
            k: {
                "n": len(ids),
                "dates": len({rows[i].date for i in ids}),
                "start": rows[ids[0]].date,
                "end": rows[ids[-1]].date,
            }
            for k, ids in masks.items()
        },
        "metrics": metrics,
        "gain_vs_constant_reference_date_bootstrap": cluster_interval(ref_losses - losses, test_dates),
        "incumbent_reproduction_gate": {
            "status": "UNVERIFIED",
            "reason": "No exact stored/replayed incumbent row pairs supplied; no superiority verdict permitted.",
        },
        "profitability": {
            "status": "UNVERIFIED",
            "roi": None,
            "clv": None,
            "reason": "No matched executable decision quotes, fees, and contract settlements supplied.",
        },
        "prospective_status": "NOT_STARTED",
        "promote": False,
        "artifact": str(model_path),
        "artifact_sha256": file_hash(model_path),
        "serialization_parity": "PASS",
        "feature_names": list(feature_names),
    }
    (directory / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report


def predict_bundle(path: Path, features: np.ndarray, *, line: float | None = None) -> np.ndarray:
    """Research inference. Continuous markets require an explicit exact line.

    Returns class probabilities, [under, push, over] for totals, or
    [away_cover, push, home_cover] for a signed home spread (home -3.5
    requires a home scoring margin above +3.5).
    The empirical distribution is fitted on a separate calibration period.
    Only locally generated, trusted joblib artifacts should be loaded.
    """
    sidecar = json.loads(path.with_name("recipe.json").read_text())
    if sidecar["model_file_sha256"] != file_hash(path):
        raise ValueError("artifact_hash_mismatch")
    bundle = joblib.load(path)
    model, recipe = bundle["model"], bundle["recipe"]
    if {k: v for k, v in sidecar.items() if k != "model_file_sha256"} != recipe:
        raise ValueError("artifact_recipe_mismatch")
    x = np.asarray(features, dtype=float)
    if x.ndim != 2 or x.shape[1] != len(recipe["features"]) or not np.isfinite(x).all():
        raise ValueError("invalid_features")
    if recipe.get("research_family") == "ncaaf_joint_score_v2":
        from .joint_score_research import probabilities

        market = recipe["market"]
        joint = recipe["joint_score_artifact"]
        if joint["model_ids"][market] != recipe["model_id"] or joint["feature_names"] != recipe["features"]:
            raise ValueError("joint_recipe_identity_mismatch")
        if recipe["target"] != market or recipe["class_labels"] != (
            ["b_win", "a_win"] if market == "moneyline" else None
        ):
            raise ValueError("joint_recipe_outcome_space_mismatch")
        return probabilities(joint, x, market, line=line)
    if recipe["classes"] is not None:
        return temperature_scale(model.predict_proba(x), recipe["temperature"])
    if line is None or not math.isfinite(line):
        raise ValueError("exact_contract_line_required")
    threshold = -line if recipe["target"] == "spread" else line
    samples = model.predict(x)[:, None] + np.asarray(recipe["residuals"])[None, :]
    if float(threshold).is_integer():
        below = (samples < threshold - 0.5).mean(1)
        above = (samples >= threshold + 0.5).mean(1)
        push = np.maximum(0, 1 - below - above)
    elif (threshold * 2).is_integer():
        below = (samples < threshold).mean(1)
        above, push = 1 - below, np.zeros(len(samples))
    else:
        raise ValueError("unsupported_contract_line")
    return np.stack((below, push, above), axis=1)
