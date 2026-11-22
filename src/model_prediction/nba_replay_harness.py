"""NBA research replay harness: point-in-time engine replay scored against real closing lines.

Replaces the unreproducible v9 qualification metrics (NBA review B8, 2026-11-20). An engine is
replayed one ET game date at a time with exactly the production history cutoff
(``FeatureStore.games_before``: ET midnight before the game date), and every game's margin,
total and probabilities are scored against the archived closing lines.

The closing lines carry no capture timestamp, so they are a BENCHMARK only. They are handed
to engines as ``spread_away_line`` / ``total_line`` purely so the engines can price the
contract; any engine scored here must have ``market_blend_weight == 0`` (asserted), otherwise
the close would leak into the forecast mean.

Split: complete-date 60/20/20 over the replayed dates (``validation.chronological_split``
semantics), with the holdout start capped at 2026-02-23 (the NBA registry holdout, touched
twice already). The replay stops before the holdout, so holdout games are never priced or
scored. Everything reported is train or validation and exploratory.

Line convention: ``home_margin_line`` is the closing home margin (+6.5 = home favoured by 6.5),
which is the value the v9 cover formula expects in ``spread_away_line`` (away covers iff the
home margin finishes below it).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import ArrayLike

from .display_names import EDGE_BUCKETS as SHARED_EDGE_BUCKETS
from .domain import EASTERN
from .features.base import FeatureStore, GameRecord
from .models.base import GamePrediction
from .models.basketball import UpcomingGame

NBA_HOLDOUT_START = "2026-02-23"
MIN_HISTORY_GAMES = 200
DEFAULT_LINES_PATH = Path("data/odds/nba/historical_closing_lines/nba_closing_lines_2024_2026.jsonl")

EnginePredict = Callable[
    [Sequence[GameRecord], Sequence[UpcomingGame], Sequence[GameRecord]], list[GamePrediction]
]


@dataclass(frozen=True)
class ClosingLine:
    event_id: str
    home_margin_line: float
    total_line: float


@dataclass(frozen=True)
class EngineSpec:
    """A named engine. ``predict`` gets (history, upcoming, the day's GameRecords).

    The day's GameRecords are passed so an engine can read schedule-only fields such as
    ``season_type`` (known before tip). Engines must not read scores from them.
    """

    name: str
    predict: EnginePredict
    market_blend_weight: float = 0.0


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_closing_lines(path: Path) -> dict[str, ClosingLine]:
    out: dict[str, ClosingLine] = {}
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            row = json.loads(raw)
            try:
                spread = float(row["spread"])
                total = float(row["total"])
            except (TypeError, ValueError, KeyError):
                continue
            home_line = spread if row.get("whos_favored") == "home" else -spread
            out[str(row["event_id"])] = ClosingLine(str(row["event_id"]), home_line, total)
    return out


def replay_dates(store: FeatureStore, end_date: str = NBA_HOLDOUT_START) -> dict[str, list[GameRecord]]:
    """ET game dates (non-preseason games only) eligible for replay, strictly before ``end_date``."""
    by_date: dict[str, list[GameRecord]] = defaultdict(list)
    for game in store.load_games("nba"):
        if game.season_type == "preseason":
            continue
        by_date[game.start.astimezone(EASTERN).date().isoformat()].append(game)
    return {day: by_date[day] for day in sorted(by_date) if day < end_date}


def split_boundaries(all_dates: Sequence[str], holdout_cap: str = NBA_HOLDOUT_START) -> tuple[str, str]:
    """(validation_start, holdout_start) on complete dates, 60/20/20, holdout capped.

    Same arithmetic as ``validation.chronological_split`` and the 2026-11-20 diagnostics.
    """
    dates = sorted(set(all_dates))
    n_train = math.floor(len(dates) * 0.60)
    n_val = math.floor(len(dates) * 0.20)
    return dates[n_train], min(dates[n_train + n_val], holdout_cap)


def replay(
    store: FeatureStore,
    engines: Sequence[EngineSpec],
    lines: Mapping[str, ClosingLine],
    *,
    end_date: str = NBA_HOLDOUT_START,
    min_history: int = MIN_HISTORY_GAMES,
    progress: Callable[[str], None] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Replay every engine day by day; one row per game with a closing line, per engine."""
    for spec in engines:
        if spec.market_blend_weight != 0.0:
            raise ValueError(f"{spec.name}: market_blend_weight must be 0 when scored against closes")
    out: dict[str, list[dict[str, Any]]] = {spec.name: [] for spec in engines}
    for day, day_games in replay_dates(store, end_date).items():
        history = store.games_before("nba", day)
        if len(history) < min_history:
            continue
        priced = [g for g in day_games if g.event_id in lines]
        if not priced:
            continue
        upcoming = [
            UpcomingGame(
                event_id=g.event_id,
                event_start_utc=g.event_start_utc,
                away_team=g.away_team,
                home_team=g.home_team,
                spread_away_line=lines[g.event_id].home_margin_line,
                total_line=lines[g.event_id].total_line,
            )
            for g in priced
        ]
        for spec in engines:
            by_event: dict[str, dict[str, GamePrediction]] = defaultdict(dict)
            for pred in spec.predict(history, upcoming, priced):
                by_event[pred.event_id][pred.market_type] = pred
            for g in priced:
                markets = by_event.get(g.event_id, {})
                out[spec.name].append(_row(day, g, lines[g.event_id], markets, len(history)))
        if progress is not None and day.endswith("-01"):
            progress(day)
    return out


def _row(
    day: str, game: GameRecord, line: ClosingLine, markets: Mapping[str, GamePrediction], history_n: int
) -> dict[str, Any]:
    spread = markets.get("spread")
    total = markets.get("total")
    moneyline = markets.get("moneyline")
    basis: dict[str, Any] = dict(moneyline.feature_basis) if moneyline is not None else {}
    return {
        "date": day,
        "event_id": game.event_id,
        "season_year": game.season_year,
        "season_type": game.season_type,
        "home": game.home_team,
        "away": game.away_team,
        "home_score": game.home_score,
        "away_score": game.away_score,
        "line_margin": line.home_margin_line,
        "line_total": line.total_line,
        "model_margin": spread.inference_inputs["mean"] if spread and spread.inference_inputs else None,
        "margin_sd": spread.inference_inputs["sd"] if spread and spread.inference_inputs else None,
        "model_total": total.inference_inputs["mean"] if total and total.inference_inputs else None,
        "total_sd": total.inference_inputs["sd"] if total and total.inference_inputs else None,
        "p_away_cover": spread.probabilities["away"] if spread else None,
        "p_over": total.probabilities["over"] if total else None,
        "p_home_ml": moneyline.probabilities["home"] if moneyline else None,
        "history_n": history_n,
        "basis": basis,
    }


def write_rows(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            count += 1
    return count


def read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(raw) for raw in handle]


# ---------------------------------------------------------------- scoring


@dataclass
class Interval:
    estimate: float
    low: float
    high: float

    def fmt(self, digits: int = 3) -> str:
        return f"{self.estimate:+.{digits}f} [{self.low:+.{digits}f}, {self.high:+.{digits}f}]"

    @property
    def excludes_zero_above(self) -> bool:
        return self.low > 0.0


@dataclass
class MarketScore:
    market: str
    n: int
    bias_model: float  # mean(actual - model)
    bias_line: float  # mean(actual - line)
    rmse_model: float
    rmse_line: float
    corr: Interval  # corr(model - line, actual - line), date-cluster bootstrap
    shrink_lambda: Interval  # through-origin OLS of (actual - line) on (model - line)
    brier: float | None
    brier_coin: float
    edge_buckets: list[dict[str, Any]] = field(default_factory=list)


def _date_codes(dates: Sequence[str]) -> tuple[np.ndarray, int]:
    unique = {d: i for i, d in enumerate(sorted(set(dates)))}
    return np.array([unique[d] for d in dates], dtype=np.int64), len(unique)


def _cluster_bootstrap(
    stats: dict[str, np.ndarray],
    dates: Sequence[str],
    fn: Callable[[dict[str, float]], float],
    *,
    draws: int = 2000,
    seed: int = 20261120,
) -> Interval:
    """Percentile CI by resampling whole ET dates; ``stats`` are per-game additive terms."""
    codes, n_dates = _date_codes(dates)
    per_date = {k: np.bincount(codes, weights=v, minlength=n_dates) for k, v in stats.items()}
    point = fn({k: float(v.sum()) for k, v in per_date.items()})
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, n_dates, size=(draws, n_dates))
    counts = np.apply_along_axis(lambda row: np.bincount(row, minlength=n_dates), 1, picks)
    sampled = {k: counts @ v for k, v in per_date.items()}
    values = np.array([fn({k: float(sampled[k][i]) for k in sampled}) for i in range(draws)])
    values = values[np.isfinite(values)]
    return Interval(point, float(np.percentile(values, 2.5)), float(np.percentile(values, 97.5)))


def _corr_from_sums(s: dict[str, float]) -> float:
    n = s["n"]
    cov = s["xy"] / n - (s["x"] / n) * (s["y"] / n)
    vx = s["xx"] / n - (s["x"] / n) ** 2
    vy = s["yy"] / n - (s["y"] / n) ** 2
    if vx <= 0 or vy <= 0:
        return float("nan")
    return cov / math.sqrt(vx * vy)


def _lambda_from_sums(s: dict[str, float]) -> float:
    return s["xy"] / s["xx"] if s["xx"] > 0 else float("nan")


def gap_statistics(
    model: ArrayLike, line: ArrayLike, actual: ArrayLike, dates: Sequence[str], draws: int = 2000
) -> tuple[Interval, Interval]:
    """(corr(model-line, actual-line), through-origin shrink lambda), both with date-cluster CIs."""
    x = np.asarray(model, float) - np.asarray(line, float)
    y = np.asarray(actual, float) - np.asarray(line, float)
    stats = {"n": np.ones_like(x), "x": x, "y": y, "xx": x * x, "yy": y * y, "xy": x * y}
    corr = _cluster_bootstrap(stats, dates, _corr_from_sums, draws=draws)
    lam = _cluster_bootstrap(stats, dates, _lambda_from_sums, draws=draws)
    return corr, lam


def _rmse(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.sqrt(np.mean((a - b) ** 2)))


# The shared bands (display_names); this edge is model confidence over 50%, so never negative.
EDGE_BUCKETS = tuple((lo, hi, label) for lo, hi, label in SHARED_EDGE_BUCKETS if lo is not None)


def score_market(rows: Sequence[Mapping[str, Any]], market: str, draws: int = 2000) -> MarketScore | None:
    """Score ``market`` ('spread' or 'total') on rows that carry the model mean."""
    key_model, key_line, key_prob = (
        ("model_margin", "line_margin", "p_away_cover")
        if market == "spread"
        else ("model_total", "line_total", "p_over")
    )
    sub = [r for r in rows if r.get(key_model) is not None]
    if len(sub) < 30:
        return None
    if market == "spread":
        actual = np.array([r["home_score"] - r["away_score"] for r in sub], float)
    else:
        actual = np.array([r["home_score"] + r["away_score"] for r in sub], float)
    model = np.array([r[key_model] for r in sub], float)
    line = np.array([r[key_line] for r in sub], float)
    dates = [r["date"] for r in sub]
    corr, lam = gap_statistics(model, line, actual, dates, draws=draws)
    # Probability side: spread probabilities are away-cover (away covers iff margin < line);
    # total probabilities are over. Pushes are excluded.
    probs, outcomes = [], []
    for r, act, ln in zip(sub, actual, line, strict=True):
        p = r.get(key_prob)
        if p is None or act == ln:
            continue
        hit = (act < ln) if market == "spread" else (act > ln)
        probs.append(float(p))
        outcomes.append(1.0 if hit else 0.0)
    p_arr, o_arr = np.array(probs), np.array(outcomes)
    brier = float(np.mean((p_arr - o_arr) ** 2)) if len(p_arr) else None
    buckets = []
    if len(p_arr):
        side_p = np.maximum(p_arr, 1.0 - p_arr)
        side_hit = np.where(p_arr >= 0.5, o_arr, 1.0 - o_arr)
        edge = side_p - 0.5
        for lo, hi, label in EDGE_BUCKETS:
            mask = (edge >= lo) & (edge < hi) if hi is not None else (edge >= lo)
            k = int(mask.sum())
            buckets.append(
                {
                    "bucket": label,
                    "n": k,
                    "claimed": float(side_p[mask].mean()) if k else None,
                    "hit": float(side_hit[mask].mean()) if k else None,
                }
            )
    return MarketScore(
        market=market,
        n=len(sub),
        bias_model=float(np.mean(actual - model)),
        bias_line=float(np.mean(actual - line)),
        rmse_model=_rmse(model, actual),
        rmse_line=_rmse(line, actual),
        corr=corr,
        shrink_lambda=lam,
        brier=brier,
        brier_coin=0.25,
        edge_buckets=buckets,
    )


def _phi(z: np.ndarray) -> np.ndarray:
    from scipy.stats import norm

    return np.asarray(norm.cdf(z), float)


def score_moneyline(
    train: Sequence[Mapping[str, Any]], evaluate: Sequence[Mapping[str, Any]]
) -> dict[str, Any] | None:
    """Moneyline vs a SPREAD-IMPLIED probability (the archive has no moneyline prices).

    p_line = Phi(home_margin_line / sigma), sigma = RMS(actual - line) on train.
    Blend weight on the model is fitted on train (grid), applied to ``evaluate``.
    """
    tr = [r for r in train if r.get("p_home_ml") is not None and r["home_score"] != r["away_score"]]
    ev = [r for r in evaluate if r.get("p_home_ml") is not None and r["home_score"] != r["away_score"]]
    if len(tr) < 30 or len(ev) < 30:
        return None
    sigma = float(np.sqrt(np.mean([(r["home_score"] - r["away_score"] - r["line_margin"]) ** 2 for r in tr])))

    def arrays(rows: Sequence[Mapping[str, Any]]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        pm = np.array([r["p_home_ml"] for r in rows], float)
        pl = _phi(np.array([r["line_margin"] for r in rows], float) / sigma)
        won = np.array([1.0 if r["home_score"] > r["away_score"] else 0.0 for r in rows])
        return pm, pl, won

    pm_tr, pl_tr, w_tr = arrays(tr)
    grid = np.linspace(0.0, 1.0, 101)
    weight = float(grid[np.argmin([np.mean(((1 - g) * pl_tr + g * pm_tr - w_tr) ** 2) for g in grid])])
    pm, pl, won = arrays(ev)
    return {
        "n": len(ev),
        "sigma_line": sigma,
        "brier_line": float(np.mean((pl - won) ** 2)),
        "brier_model": float(np.mean((pm - won) ** 2)),
        "blend_weight_train": weight,
        "brier_blend": float(np.mean(((1 - weight) * pl + weight * pm - won) ** 2)),
        "corr_gap": float(np.corrcoef(pm - pl, won - pl)[0, 1]),
    }


def bias_table(rows: Sequence[Mapping[str, Any]], key: str) -> list[dict[str, Any]]:
    """Mean actual-minus-model and actual-minus-line for both markets, grouped by ``key``.

    ``key`` is 'season' (NBA season label from the date) or 'month'.
    """
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        if r.get("model_margin") is None or r.get("model_total") is None:
            continue
        groups[_season_label(r["date"]) if key == "season" else r["date"][:7]].append(r)
    out = []
    for label in sorted(groups):
        g = groups[label]
        am = np.array([r["home_score"] - r["away_score"] for r in g], float)
        at = np.array([r["home_score"] + r["away_score"] for r in g], float)
        out.append(
            {
                key: label,
                "n": len(g),
                "margin_act_minus_model": float(np.mean(am - np.array([r["model_margin"] for r in g]))),
                "margin_act_minus_line": float(np.mean(am - np.array([r["line_margin"] for r in g]))),
                "total_act_minus_model": float(np.mean(at - np.array([r["model_total"] for r in g]))),
                "total_act_minus_line": float(np.mean(at - np.array([r["line_total"] for r in g]))),
            }
        )
    return out


def _season_label(day: str) -> str:
    year, month = int(day[:4]), int(day[5:7])
    start = year if month >= 8 else year - 1
    return f"{start}-{str(start + 1)[2:]}"


def assign_splits(rows: list[dict[str, Any]], val_start: str, holdout_start: str) -> None:
    for r in rows:
        if r["date"] >= holdout_start:
            raise ValueError(f"holdout row {r['event_id']} on {r['date']} reached scoring")
        r["split"] = "train" if r["date"] < val_start else "val"


def score_engine(
    rows: list[dict[str, Any]], val_start: str, holdout_start: str, draws: int = 2000
) -> dict[str, Any]:
    """Full scorecard: per split x market, moneyline, bias by season and month."""
    assign_splits(rows, val_start, holdout_start)
    train = [r for r in rows if r["split"] == "train"]
    val = [r for r in rows if r["split"] == "val"]
    card: dict[str, Any] = {"val_start": val_start, "holdout_start": holdout_start, "markets": {}}
    for split_name, sub in (("train", train), ("val", val)):
        for market in ("spread", "total"):
            score = score_market(sub, market, draws=draws)
            if score is not None:
                card["markets"][f"{market}/{split_name}"] = score
    card["moneyline_val"] = score_moneyline(train, val)
    card["bias_season"] = bias_table(rows, "season")
    card["bias_month"] = bias_table(rows, "month")
    return card


def scorecard_markdown(name: str, card: Mapping[str, Any]) -> str:
    lines = [
        f"### {name}",
        "",
        f"Split: train < {card['val_start']} <= validation < {card['holdout_start']} (holdout not replayed).",
        "",
        (
            "| market/split | n | bias act-model | bias act-line | RMSE model | RMSE line | "
            "corr(model-line, act-line) [95% CI] | shrink lambda [95% CI] | Brier (coin 0.2500) |"
        ),
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for key, s in card["markets"].items():
        brier = f"{s.brier:.4f}" if s.brier is not None else "-"
        lines.append(
            f"| {key} | {s.n} | {s.bias_model:+.2f} | {s.bias_line:+.2f} | {s.rmse_model:.2f} | "
            f"{s.rmse_line:.2f} | {s.corr.fmt()} | {s.shrink_lambda.fmt()} | {brier} |"
        )
    ml = card.get("moneyline_val")
    if ml:
        lines += [
            "",
            (
                f"Moneyline (validation, vs spread-implied prob, sigma {ml['sigma_line']:.2f} from train; "
                f"the archive has no moneyline prices): n {ml['n']}, Brier line {ml['brier_line']:.4f}, "
                f"model {ml['brier_model']:.4f}, blend weight fit on train {ml['blend_weight_train']:.2f} "
                f"-> {ml['brier_blend']:.4f}, corr(model-line, won-line) {ml['corr_gap']:+.3f}."
            ),
        ]
    lines += ["", "Edge buckets vs the closing line (side prob - 50%), validation:", ""]
    lines += ["| market | bucket | n | claimed | hit |", "|---|---|---|---|---|"]
    for market in ("spread", "total"):
        s = card["markets"].get(f"{market}/val")
        for b in s.edge_buckets if s else []:
            if b["n"]:
                lines.append(
                    f"| {market} | {b['bucket']} | {b['n']} | {b['claimed'] * 100:.1f}% | {b['hit'] * 100:.1f}% |"
                )
    for key in ("bias_season", "bias_month"):
        label = "season" if key == "bias_season" else "month"
        lines += [
            "",
            f"Bias by {label} (train + validation):",
            "",
            f"| {label} | n | margin act-model | margin act-line | total act-model | total act-line |",
            "|---|---|---|---|---|---|",
        ]
        for b in card[key]:
            lines.append(
                f"| {b[label]} | {b['n']} | {b['margin_act_minus_model']:+.2f} | {b['margin_act_minus_line']:+.2f} "
                f"| {b['total_act_minus_model']:+.2f} | {b['total_act_minus_line']:+.2f} |"
            )
    return "\n".join(lines) + "\n"


GOLDEN_FIELDS = ("event_id", "date", "model_margin", "model_total", "p_away_cover", "p_over", "p_home_ml")


def golden_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [{k: r[k] for k in GOLDEN_FIELDS} for r in rows]


def diff_golden(golden: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """Exact comparison by event_id. Returns counts of missing/extra/changed events."""
    g = {r["event_id"]: r for r in golden}
    c = {r["event_id"]: r for r in rows}
    changed = sum(1 for eid in g.keys() & c.keys() if any(g[eid][k] != c[eid][k] for k in GOLDEN_FIELDS))
    return {
        "golden": len(g),
        "missing": len(g.keys() - c.keys()),
        "extra": len(c.keys() - g.keys()),
        "changed": changed,
    }


# ---------------------------------------------------------------- anchor gate G1


@dataclass
class G1Result:
    engine: str
    market: str
    n_train: int
    n_val: int
    lambda_train: Interval  # fit on train (the deployed lambda before clipping)
    lambda_val: Interval  # refit on validation, for the CI check
    corr_val: Interval
    sigma_train: float  # RMS residual around the anchored prediction on train
    rmse_val_line: float
    rmse_val_anchored: float  # line + clip(lambda_train) * (model - line)
    passed: bool
    min_val_games: int


def _market_arrays(rows: Sequence[Mapping[str, Any]], market: str) -> tuple[np.ndarray, ...]:
    if market == "spread":
        actual = np.array([r["home_score"] - r["away_score"] for r in rows], float)
        model = np.array([r["model_margin"] for r in rows], float)
        line = np.array([r["line_margin"] for r in rows], float)
    else:
        actual = np.array([r["home_score"] + r["away_score"] for r in rows], float)
        model = np.array([r["model_total"] for r in rows], float)
        line = np.array([r["line_total"] for r in rows], float)
    return actual, model, line


def gate_g1(
    engine: str,
    train: Sequence[Mapping[str, Any]],
    val: Sequence[Mapping[str, Any]],
    market: str,
    *,
    min_val_games: int = 700,
    draws: int = 2000,
) -> G1Result:
    """Gate G1 against closing lines (benchmark only).

    Pass needs: lambda fit on train with 95% CI above 0, lambda refit on validation with 95% CI
    above 0, corr(model - close, actual - close) on validation with 95% CI above 0, and at least
    ``min_val_games`` validation games. A component that fails ships with lambda = 0.
    """
    a_tr, m_tr, l_tr = _market_arrays(train, market)
    a_va, m_va, l_va = _market_arrays(val, market)
    _, lam_tr = gap_statistics(m_tr, l_tr, a_tr, [r["date"] for r in train], draws=draws)
    corr_va, lam_va = gap_statistics(m_va, l_va, a_va, [r["date"] for r in val], draws=draws)
    lam = max(0.0, lam_tr.estimate)
    sigma = float(np.sqrt(np.mean((a_tr - (l_tr + lam * (m_tr - l_tr))) ** 2)))
    passed = lam_tr.low > 0.0 and lam_va.low > 0.0 and corr_va.low > 0.0 and len(val) >= min_val_games
    return G1Result(
        engine=engine,
        market=market,
        n_train=len(train),
        n_val=len(val),
        lambda_train=lam_tr,
        lambda_val=lam_va,
        corr_val=corr_va,
        sigma_train=sigma,
        rmse_val_line=_rmse(l_va, a_va),
        rmse_val_anchored=_rmse(l_va + lam * (m_va - l_va), a_va),
        passed=passed,
        min_val_games=min_val_games,
    )
