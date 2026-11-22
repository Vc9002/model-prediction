"""NBA v10 player layer: train-only fits, walk-forward features and gate G1 (research only).

  python scripts/nba_v10/player_fit.py fit-minutes      # blowout slopes, fringe rates, P(play) table
  python scripts/nba_v10/player_fit.py fit-box          # box-score prior per half-life in the grid
  python scripts/nba_v10/player_fit.py cv               # train-only walk-forward CV of (half-life, ridge)
  python scripts/nba_v10/player_fit.py walk [--through YYYY-MM-DD]
  python scripts/nba_v10/player_fit.py g1               # step 4/5 fit on train, G1 on validation

Spec: outputs/nba/v10_player/PREREG.md (committed before any validation outcome was joined).
Split: the replay harness's pinned split. Train = every game before 2025-11-03 (rows from
2017-10-01; 2016-17 is rating warm-up); validation = 2025-11-03 .. 2026-02-22; the locked
holdout (>= 2026-02-23) is never loaded. ``walk`` refuses to pass 2025-11-02 until PREREG.md is
committed.

Derived data goes to E:/MP/data/research/nba_v10/player/ (untracked). Reports go to
outputs/nba/v10_player/. Do not run between 00:20 and 01:20 ET.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from model_prediction.domain import EASTERN
from model_prediction.features.nba_player_ratings import (
    HOLDOUT_START,
    N_STATS,
    STAT_KEYS,
    BoxPrior,
    GameBox,
    InjuryReportIndex,
    MinutesModel,
    PlayerLayerState,
    RatingParams,
    decision_time,
    fit_play_table,
    game_features,
    load_box_games,
    match_report,
    walk,
)
from model_prediction.models.nba_player_v10 import MODEL_VERSION, PlayerV10Coefficients, fit, predict
from model_prediction.nba_replay_harness import Interval, gap_statistics, gate_g1

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "outputs" / "nba" / "v10_player"
PREREG = OUT_DIR / "PREREG.md"
DATA = Path("E:/MP/data/research/nba_v10")
PLAYER = DATA / "player"
FITS = PLAYER / "fits"
BOX = PLAYER / "box_games.jsonl"
INJURIES = PLAYER / "injury_index.jsonl"
FEATURES = PLAYER / "features.jsonl"
ARTIFACT = REPO / "config" / "models" / "research" / "nba-player-v10.json"

VAL_START = "2025-11-03"  # pinned harness split (scripts/nba_v10/replay_harness.py)
MODEL_START = "2017-10-01"  # first modelled season 2017-18; 2016-17 warms the ratings up
CV_START = "2018-10-01"  # CV folds: seasons 2018-19 .. 2024-25 (+ train part of 2025-26)
V9_MATCHED_START = "2024-01-29"  # first harness replay date (v9-comparable train window)
HALF_LIVES = (120.0, 365.0, 730.0)
RIDGES = (3.0, 10.0, 30.0, 100.0, math.inf)
FRINGE_SEASON_MINUTES = 500.0
BLOWOUT_MIN_GAMES = 10
GATED_VARIANTS = ("exavail", "avail")


def _blackout_guard() -> None:
    now = datetime.now(EASTERN)
    if 20 <= now.hour * 60 + now.minute <= 80:
        sys.exit(f"refusing heavy compute at {now:%H:%M} ET (00:20-01:20 blackout)")


def _prereg_committed() -> bool:
    out = subprocess.run(
        ["git", "-C", str(REPO), "log", "--format=%H", "--", str(PREREG.relative_to(REPO))],
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(out.stdout.strip())


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _injuries() -> InjuryReportIndex | None:
    return InjuryReportIndex.from_jsonl(INJURIES) if INJURIES.exists() else None


# ---------------------------------------------------------------- fits on train


def _blowout_slopes(games: Sequence[GameBox]) -> tuple[float, float, dict[str, int]]:
    """Minutes ratio to the player-season mean vs max(0, |margin| - 10), starters and bench
    separately; slope expressed relative to the intercept so a close game has factor 1."""
    played: dict[tuple[str, int], list[float]] = defaultdict(list)
    for g in games:
        for p in g.players or ():
            if p.played:
                played[(p.player_id, g.season_year)].append(p.minutes)
    means = {k: float(np.mean(v)) for k, v in played.items() if len(v) >= BLOWOUT_MIN_GAMES}
    xs: dict[bool, list[float]] = {True: [], False: []}
    ys: dict[bool, list[float]] = {True: [], False: []}
    for g in games:
        excess = max(0.0, abs(g.home_score - g.away_score) - 10.0)
        for p in g.players or ():
            key = (p.player_id, g.season_year)
            if p.played and key in means:
                xs[p.starter].append(excess)
                ys[p.starter].append(p.minutes / means[key])
    slopes = []
    for starter in (True, False):
        X = np.column_stack([np.ones(len(xs[starter])), xs[starter]])
        a, b = np.linalg.lstsq(X, np.array(ys[starter]), rcond=None)[0]
        slopes.append(float(b / a))
    return slopes[0], slopes[1], {"starter_rows": len(xs[True]), "bench_rows": len(xs[False])}


def _fringe_rates(games: Sequence[GameBox]) -> list[float]:
    stats: dict[tuple[str, int], np.ndarray] = defaultdict(lambda: np.zeros(N_STATS + 1))
    for g in games:
        for p in g.players or ():
            if p.played:
                stats[(p.player_id, g.season_year)] += np.array([*p.stats, p.minutes])
    fringe = [v for v in stats.values() if v[-1] < FRINGE_SEASON_MINUTES]
    total = np.sum(fringe, axis=0)
    return [float(x) for x in 48.0 * total[:-1] / total[-1]]


def cmd_fit_minutes() -> None:
    _blackout_guard()
    games = load_box_games(BOX, end_date=VAL_START)
    starter, bench, counts = _blowout_slopes(games)
    fringe = _fringe_rates(games)
    model = MinutesModel(starter, bench, {})
    state = PlayerLayerState(RatingParams(365.0), None, model, fringe_rates=fringe)
    injuries = _injuries()
    obs: list[tuple[str, str, bool]] = []
    tally: dict[str, int] = defaultdict(int)

    def collect(day: str, day_games: list[GameBox], st: PlayerLayerState) -> None:
        for g in day_games:
            if not g.players:
                continue
            when = decision_time(g)
            for home in (True, False):
                team = g.home_id if home else g.away_id
                if st.team_status_reason(team, g.season_year) is not None:
                    continue
                cands = [c for c in st.candidates(team, g.season_year) if c.mpg > 0]
                played_now = {p.player_id for p in g.side(home) if p.played}
                for c in cands:
                    obs.append(("unknown", c.bucket, c.player_id in played_now))
                if injuries is None or not injuries.covers(g.date):
                    continue
                name = g.home_name if home else g.away_name
                rep = injuries.team_report(g.date, g.away_name, g.home_name, name, when)
                if rep.players is None:
                    tally[str(rep.reason)] += 1
                    continue
                matched, unmatched = match_report({c.player_id: c.name for c in cands}, rep.players)
                tally["unmatched_rows"] += unmatched
                if matched is None:
                    tally["NO_CALL_INJURY_NAME_AMBIGUOUS"] += 1
                    continue
                tally["team_reports_used"] += 1
                for c in cands:
                    obs.append((matched.get(c.player_id, "unlisted"), c.bucket, c.player_id in played_now))

    walk(games, state, start=MODEL_START, end=VAL_START, on_day=collect)
    table = fit_play_table(obs)
    out = {
        "fit_window": f"{MODEL_START} .. < {VAL_START} (train)",
        "minutes_model": MinutesModel(starter, bench, table).to_dict(),
        "fringe_rates": dict(zip(STAT_KEYS, fringe, strict=True)),
        "blowout_rows": counts,
        "observations": len(obs),
        "availability_tally": dict(tally),
        "injury_index": _read_json(PLAYER / "injury_index.provenance.json") if injuries else None,
    }
    _write_json(FITS / "minutes.json", out)
    print(json.dumps({k: v for k, v in out.items() if k != "injury_index"}, indent=1))


def _minutes_model() -> tuple[MinutesModel, list[float]]:
    d = _read_json(FITS / "minutes.json")
    return MinutesModel.from_dict(d["minutes_model"]), [d["fringe_rates"][k] for k in STAT_KEYS]


def _box_path(h: float) -> Path:
    return FITS / f"box_h{int(h)}.json"


def cmd_fit_box() -> None:
    _blackout_guard()
    games = load_box_games(BOX, end_date=VAL_START)
    minutes_model, fringe = _minutes_model()
    for h in HALF_LIVES:
        state = PlayerLayerState(RatingParams(h), None, minutes_model, fringe_rates=fringe)
        rows: list[tuple[np.ndarray, np.ndarray, float, float]] = []

        def collect(day: str, day_games: list[GameBox], st: PlayerLayerState, rows: list = rows) -> None:
            for g in day_games:
                poss = g.possessions()
                if not g.players or poss is None:
                    continue
                eff = 100.0 * (g.home_score + g.away_score) / poss - 2.0 * st.eff_base
                rows.append(
                    (st.box_aggregate(g, True), st.box_aggregate(g, False), g.home_score - g.away_score, eff)
                )

        walk(games, state, start=MODEL_START, end=VAL_START, on_day=collect)
        zh = np.array([r[0] for r in rows])
        za = np.array([r[1] for r in rows])
        ym = np.array([r[2] for r in rows], float)
        yt = np.array([r[3] for r in rows], float)
        Xm = np.column_stack([np.ones(len(rows)), zh - za])
        Xt = np.column_stack([np.ones(len(rows)), zh + za])
        cm = np.linalg.lstsq(Xm, ym, rcond=None)[0]
        ct = np.linalg.lstsq(Xt, yt, rcond=None)[0]
        prior = BoxPrior(
            gamma=tuple(float(c) for c in cm[1:]),
            delta=tuple(float(c) for c in ct[1:]),
            hca=float(cm[0]),
            total_const=float(ct[0]),
            fringe_rates=tuple(fringe),
        )
        r2m = 1.0 - np.var(ym - Xm @ cm) / np.var(ym)
        r2t = 1.0 - np.var(yt - Xt @ ct) / np.var(yt)
        _write_json(
            _box_path(h),
            {
                "half_life_days": h,
                "fit_window": f"{MODEL_START} .. < {VAL_START} (train)",
                "n_games": len(rows),
                "r2_margin": float(r2m),
                "r2_efficiency": float(r2t),
                "prior": prior.to_dict(),
                "gamma_by_stat": dict(zip(STAT_KEYS, prior.gamma, strict=True)),
                "delta_by_stat": dict(zip(STAT_KEYS, prior.delta, strict=True)),
            },
        )
        print(f"h={h:.0f}: n={len(rows)} hca={cm[0]:+.2f} R2 margin {r2m:.3f} eff {r2t:.3f}", flush=True)


def _prior(h: float) -> BoxPrior:
    return BoxPrior.from_dict(_read_json(_box_path(h))["prior"])


# ---------------------------------------------------------------- CV (train only)


def cmd_cv() -> None:
    _blackout_guard()
    games = load_box_games(BOX, end_date=VAL_START)
    minutes_model, _ = _minutes_model()
    results: dict[str, Any] = {"grid": {"half_life_days": HALF_LIVES, "ridge": [str(r) for r in RIDGES]}}
    table: list[dict[str, Any]] = []
    for h in HALF_LIVES:
        prior = _prior(h)
        state = PlayerLayerState(RatingParams(h, RIDGES, RIDGES), prior, minutes_model)
        sse_m = np.zeros(len(RIDGES))
        sse_t = np.zeros(len(RIDGES))
        count = [0]
        t0 = time.time()

        def collect(
            day: str,
            day_games: list[GameBox],
            st: PlayerLayerState,
            prior: BoxPrior = prior,
            sse_m: np.ndarray = sse_m,
            sse_t: np.ndarray = sse_t,
            count: list[int] = count,
        ) -> None:
            for g in day_games:
                poss = g.possessions()
                if not g.players or poss is None:
                    continue
                count[0] += 1
                margin = g.home_score - g.away_score
                eff = 100.0 * (g.home_score + g.away_score) / poss
                for i in range(len(RIDGES)):
                    pm = (
                        prior.hca + st.oracle_strength(g, True, "m", i) - st.oracle_strength(g, False, "m", i)
                    )
                    pt = (
                        prior.total_const
                        + 2.0 * st.eff_base
                        + st.oracle_strength(g, True, "t", i)
                        + st.oracle_strength(g, False, "t", i)
                    )
                    sse_m[i] += (margin - pm) ** 2
                    sse_t[i] += (eff - pt) ** 2

        walk(games, state, start=CV_START, end=VAL_START, on_day=collect)
        n = count[0]
        for i, lam in enumerate(RIDGES):
            table.append(
                {
                    "half_life_days": h,
                    "ridge": str(lam),
                    "n": n,
                    "rmse_margin": float(math.sqrt(sse_m[i] / n)),
                    "rmse_efficiency": float(math.sqrt(sse_t[i] / n)),
                }
            )
            print(json.dumps(table[-1]), flush=True)
        print(f"h={h:.0f} done in {time.time() - t0:.0f}s", flush=True)
    best_m = min(table, key=lambda r: (round(r["rmse_margin"], 6), -float(r["ridge"])))
    best_t = min(table, key=lambda r: (round(r["rmse_efficiency"], 6), -float(r["ridge"])))
    results["table"] = table
    results["choice"] = {
        "margin": {"half_life_days": best_m["half_life_days"], "ridge": best_m["ridge"]},
        "total": {"half_life_days": best_t["half_life_days"], "ridge": best_t["ridge"]},
    }
    results["metric"] = "pooled walk-forward RMSE with actual minutes (oracle), train games >= 2018-10-01"
    _write_json(FITS / "cv.json", results)
    print(json.dumps(results["choice"], indent=1))


# ---------------------------------------------------------------- final walk


def cmd_walk(through: str) -> None:
    _blackout_guard()
    if through > VAL_START and not _prereg_committed():
        sys.exit("PREREG.md is not committed: the walk stays inside train (< 2025-11-03)")
    if through > HOLDOUT_START:
        sys.exit(f"--through {through} reaches the locked holdout")
    games = load_box_games(BOX, end_date=through)
    minutes_model, _ = _minutes_model()
    choice = _read_json(FITS / "cv.json")["choice"]
    injuries = _injuries()
    per_h: dict[float, dict[str, float]] = defaultdict(dict)
    for market in ("margin", "total"):
        per_h[float(choice[market]["half_life_days"])][market] = float(choice[market]["ridge"])
    merged: dict[str, dict[str, Any]] = {}
    for h, ridges in per_h.items():
        params = RatingParams(h, (ridges.get("margin", math.inf),), (ridges.get("total", math.inf),))
        state = PlayerLayerState(params, _prior(h), minutes_model)
        rows: dict[str, dict[str, Any]] = {}

        def collect(day: str, day_games: list[GameBox], st: PlayerLayerState, rows: dict = rows) -> None:
            for g in day_games:
                row = game_features(st, g, injuries, with_oracle=True)
                row["home_score"] = g.home_score
                row["away_score"] = g.away_score
                row["actual_poss"] = g.possessions()
                rows[g.event_id] = row

        t0 = time.time()
        walk(games, state, start=MODEL_START, end=through, on_day=collect)
        print(f"walk h={h:.0f} {ridges}: {len(rows)} games in {time.time() - t0:.0f}s", flush=True)
        for eid, row in rows.items():
            base = merged.setdefault(eid, row)
            if "total" in ridges:
                # The total constant c belongs to the same box-prior fit as delta (PREREG 3.5).
                base["total_const"] = row["total_const"]
            for market, keys in (("margin", ("S_home", "S_away")), ("total", ("U_home", "U_away"))):
                if market not in ridges:
                    continue
                for name, variant in row["variants"].items():
                    target = base["variants"].setdefault(name, dict(variant))
                    if variant.get("reason") is not None:
                        target["reason"] = variant["reason"]
                    for k in keys:
                        if k in variant:
                            target[k] = variant[k]
    with FEATURES.open("w", encoding="utf-8") as handle:
        for row in sorted(merged.values(), key=lambda r: (r["date"], r["event_id"])):
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    _write_json(
        PLAYER / "features.meta.json",
        {
            "through_exclusive": through,
            "choice": choice,
            "rows": len(merged),
            "injury_index": _read_json(PLAYER / "injury_index.provenance.json") if injuries else None,
            "box_provenance_sha256": _read_json(PLAYER / "box_games.provenance.json")["sha256"],
        },
    )
    print(f"wrote {len(merged)} feature rows -> {FEATURES}")


# ---------------------------------------------------------------- G1


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(raw) for raw in handle]


def _closes_2007() -> dict[str, tuple[float, float]]:
    out = {}
    for r in _read_jsonl(DATA / "closing_lines" / "nba_closing_lines_2007_2026.jsonl"):
        if r.get("home_spread") is not None and r.get("total_line") is not None and r["date"] < HOLDOUT_START:
            out[str(r["event_id"])] = (-float(r["home_spread"]), float(r["total_line"]))
    return out


def _harness_rows(name: str) -> dict[str, dict[str, Any]]:
    return {
        r["event_id"]: r for r in _read_jsonl(DATA / "harness" / f"{name}.jsonl") if r["date"] < HOLDOUT_START
    }


def _opens() -> dict[str, tuple[float | None, float | None]]:
    out: dict[str, tuple[float | None, float | None]] = {}
    for r in _read_jsonl(DATA / "espn_odds" / "espn_lines.jsonl"):
        s = r.get("selected") or {}
        spread, total = s.get("open_home_spread_books"), s.get("open_total_books")
        if spread is None and total is None:
            continue
        out[str(r["event_id"])] = (-float(spread) if spread is not None else None, total)
    return out


def _actual(r: Mapping[str, Any], market: str) -> float:
    return float(
        r["home_score"] - r["away_score"] if market == "spread" else r["home_score"] + r["away_score"]
    )


def _metric_row(label: str, rows: Sequence[Mapping[str, Any]], market: str, draws: int) -> dict[str, Any]:
    key_m, key_l = ("model_margin", "line_margin") if market == "spread" else ("model_total", "line_total")
    act = np.array([_actual(r, market) for r in rows])
    model = np.array([r[key_m] for r in rows], float)
    line = np.array([r[key_l] for r in rows], float)
    corr, lam = gap_statistics(model, line, act, [r["date"] for r in rows], draws=draws)
    return {
        "label": label,
        "n": len(rows),
        "rmse": float(np.sqrt(np.mean((act - model) ** 2))),
        "bias": float(np.mean(act - model)),
        "rmse_line": float(np.sqrt(np.mean((act - line) ** 2))),
        "bias_line": float(np.mean(act - line)),
        "corr": corr,
        "lambda": lam,
    }


def _fmt_metric(m: Mapping[str, Any]) -> str:
    return (
        f"| {m['label']} | {m['n']} | {m['rmse']:.3f} | {m['bias']:+.2f} | {m['rmse_line']:.3f} | "
        f"{m['bias_line']:+.2f} | {m['corr'].fmt()} | {m['lambda'].fmt()} |"
    )


def cmd_g1(draws: int) -> None:
    if not _prereg_committed():
        sys.exit("PREREG.md must be committed before G1 joins validation outcomes")
    feats = [r for r in _read_jsonl(FEATURES) if r["date"] < HOLDOUT_START]
    train = [r for r in feats if MODEL_START <= r["date"] < VAL_START]
    val = [r for r in feats if VAL_START <= r["date"] < HOLDOUT_START]
    closes = _closes_2007()
    v9 = _harness_rows("v9")
    v91 = {"spread": _harness_rows("v91_spread_final"), "total": _harness_rows("v91_total_final")}
    opens = _opens()
    val_lines = {eid: (r["line_margin"], r["line_total"]) for eid, r in v9.items() if r["date"] >= VAL_START}

    coefs: dict[str, PlayerV10Coefficients] = {}
    for variant in (*GATED_VARIANTS, "oracle"):
        rows = [r for r in train if r["variants"].get(variant, {}).get("reason", "x") is None]
        if len(rows) >= 100:
            coefs[variant] = fit(rows, variant)

    def shaped(
        rows: Sequence[Mapping[str, Any]], variant: str, lines: Mapping[str, tuple[float, float]]
    ) -> dict:
        out: dict[str, list[dict[str, Any]]] = {"spread": [], "total": []}
        for r in rows:
            if variant not in coefs or r["event_id"] not in lines:
                continue
            margin, total = predict(r, coefs[variant])
            lm, lt = lines[r["event_id"]]
            base = {"event_id": r["event_id"], "date": r["date"], "home_score": r["home_score"]}
            base["away_score"] = r["away_score"]
            if margin is not None:
                out["spread"].append({**base, "model_margin": margin, "line_margin": lm})
            if total is not None:
                out["total"].append({**base, "model_total": total, "line_total": lt})
        return out

    lines_md = [
        "# Gate G1 for nba-player-v10 (player layer), validation 2025-11-03 .. 2026-02-22",
        "",
        "Spec: `outputs/nba/v10_player/PREREG.md` (committed before this run). Split = the replay",
        "harness's pinned split: train < 2025-11-03 <= validation < 2026-02-23; the locked holdout is",
        "never loaded. Train rows start 2017-10-01 (2016-17 warms the ratings up). Closing lines are a",
        "benchmark only (no capture time). Validation closes are the harness's own (same file v9 was",
        f"scored on); train closes come from the 2007-2026 archive. CIs: 95% percentile, {draws}",
        "date-cluster bootstrap draws (`nba_replay_harness.gap_statistics`, seed 20261120).",
        "",
        "## G1 (gating)",
        "",
        (
            "| variant | market | n train | n val | lambda train [95% CI] | lambda val [95% CI] | corr val [95% CI] "
            "| sigma (train) | val RMSE close | val RMSE anchored | lambda train, v9-matched window | G1 |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    g1: dict[str, dict[str, Any]] = {}
    pending: list[str] = []
    for variant in GATED_VARIANTS:
        tr = shaped(train, variant, closes)
        va = shaped(val, variant, val_lines)
        for market in ("spread", "total"):
            if len(va[market]) < 30 or len(tr[market]) < 100:
                pending.append(f"{variant}/{market}: n train {len(tr[market])}, n val {len(va[market])}")
                continue
            res = gate_g1(variant, tr[market], va[market], market, draws=draws)
            matched = [r for r in tr[market] if r["date"] >= V9_MATCHED_START]
            key_m, key_l = (
                ("model_margin", "line_margin") if market == "spread" else ("model_total", "line_total")
            )
            _, lam_matched = gap_statistics(
                [r[key_m] for r in matched],
                [r[key_l] for r in matched],
                [_actual(r, market) for r in matched],
                [r["date"] for r in matched],
                draws=draws,
            )
            g1[f"{variant}/{market}"] = {
                "result": res,
                "lambda_train_v9_window": lam_matched,
                "n_v9_window": len(matched),
            }
            lines_md.append(
                f"| {variant} | {market} | {res.n_train} | {res.n_val} | {res.lambda_train.fmt()} | "
                f"{res.lambda_val.fmt()} | {res.corr_val.fmt()} | {res.sigma_train:.2f} | {res.rmse_val_line:.3f} | "
                f"{res.rmse_val_anchored:.3f} | {lam_matched.fmt()} (n {len(matched)}) | "
                f"{'PASS' if res.passed else 'FAIL'} |"
            )
    if pending:
        lines_md += ["", "Not evaluated (data not available yet): " + "; ".join(pending) + "."]
    lines_md += [
        "",
        "Pass rule (identical to `outputs/nba/v10_harness/G1.md`): lambda (train) CI > 0, lambda",
        "(validation refit) CI > 0, corr (validation) CI > 0, n val >= 700. A failing component ships",
        "with lambda = 0 and is not re-tuned on validation.",
        "",
        "## Same validation games: player layer vs v9 / v9.1 / close",
        "",
        "Bias = mean(actual - model). corr = corr(model - close, actual - close); lambda = through-origin",
        "weight of (actual - close) on (model - close), both refit on these validation games.",
    ]
    for market in ("spread", "total"):
        variant_rows = {
            v: {r["event_id"]: r for r in shaped(val, v, val_lines)[market]} for v in GATED_VARIANTS
        }
        common = (
            set(variant_rows["exavail"]) & {e for e in v9 if v9[e]["date"] >= VAL_START} & set(v91[market])
        )
        key_m = "model_margin" if market == "spread" else "model_total"
        sets: list[tuple[str, list[dict[str, Any]]]] = [
            ("player exavail", [variant_rows["exavail"][e] for e in sorted(common)]),
            ("v9", [v9[e] for e in sorted(common)]),
            ("v9.1", [v91[market][e] for e in sorted(common)]),
        ]
        lines_md += [
            "",
            f"### {market} (n common = {len(common)})",
            "",
            "| model | n | RMSE vs actual | bias | close RMSE | close bias | corr [95% CI] | lambda [95% CI] |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for label, rows in sets:
            if len(rows) >= 30:
                lines_md.append(_fmt_metric(_metric_row(label, rows, market, draws)))
        avail_common = sorted(common & set(variant_rows["avail"]))
        if len(avail_common) >= 30:
            lines_md.append("")
            lines_md.append(f"Availability subset (n = {len(avail_common)}):")
            lines_md.append("")
            lines_md.append(
                "| model | n | RMSE vs actual | bias | close RMSE | close bias | corr [95% CI] | lambda [95% CI] |"
            )
            lines_md.append("|---|---|---|---|---|---|---|---|")
            for label, src in (
                ("player avail", variant_rows["avail"]),
                ("player exavail", variant_rows["exavail"]),
                ("v9", v9),
                ("v9.1", v91[market]),
            ):
                rows = [src[e] for e in avail_common]
                lines_md.append(_fmt_metric(_metric_row(label, rows, market, draws)))
        # Exploratory: ESPN opening line (DraftKings, the historical decision-time anchor).
        open_rows: list[tuple[str, list[dict[str, Any]]]] = []
        idx = 0 if market == "spread" else 1
        with_open = [e for e in sorted(common) if e in opens and opens[e][idx] is not None]
        key_l = "line_margin" if market == "spread" else "line_total"
        for label, src in (("player exavail", variant_rows["exavail"]), ("v9", v9), ("v9.1", v91[market])):
            open_rows.append(
                (label, [{**src[e], key_l: opens[e][idx], key_m: src[e][key_m]} for e in with_open])
            )
        open_rows.append(("close", [{**v9[e], key_l: opens[e][idx], key_m: v9[e][key_l]} for e in with_open]))
        lines_md += [
            "",
            f"Exploratory (not gating): versus the ESPN opening line, n = {len(with_open)}. 'close RMSE'",
            "here is the opening line's RMSE; the 'close' row uses the closing line as the model.",
            "",
            "| model | n | RMSE vs actual | bias | open RMSE | open bias | corr vs open [95% CI] | lambda vs open [95% CI] |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for label, rows in open_rows:
            if len(rows) >= 30:
                lines_md.append(_fmt_metric(_metric_row(label, rows, market, draws)))

    if "oracle" in coefs:
        va = shaped(val, "oracle", val_lines)
        lines_md += [
            "",
            "## Hindsight diagnostic (NOT a model): ratings with the game's actual minutes",
            "",
            "Uses the box score of the game being priced, so it can never be a decision input. It is",
            "an upper bound that leaks outcome (actual minutes respond to the result, e.g. blowouts).",
            "",
            "| market | n | RMSE vs actual | bias | close RMSE | close bias | corr [95% CI] | lambda [95% CI] |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for market in ("spread", "total"):
            if len(va[market]) >= 30:
                lines_md.append(_fmt_metric(_metric_row(f"oracle {market}", va[market], market, draws)))

    lines_md += ["", "## Coefficients (fit on train)", ""]
    for variant, c in coefs.items():
        lines_md.append(f"- `{variant}`: `{json.dumps(c.to_dict(), sort_keys=True)}`")
    out = OUT_DIR / "G1_PLAYER.md"
    out.write_text("\n".join(lines_md) + "\n", encoding="utf-8")
    print("\n".join(lines_md))
    _write_artifact(coefs, g1)


def _interval(i: Interval) -> list[float]:
    return [round(i.estimate, 4), round(i.low, 4), round(i.high, 4)]


def _write_artifact(coefs: Mapping[str, PlayerV10Coefficients], g1: Mapping[str, Mapping[str, Any]]) -> None:
    from model_prediction.production_registry import compute_artifact_hash

    cv = _read_json(FITS / "cv.json")
    choice = cv["choice"]
    markets: dict[str, Any] = {}
    for key, entry in g1.items():
        res = entry["result"]
        markets[key] = {
            "g1": "PASS" if res.passed else "FAIL",
            "lambda": round(max(0.0, res.lambda_train.estimate), 4) if res.passed else 0.0,
            "lambda_train": _interval(res.lambda_train),
            "lambda_val": _interval(res.lambda_val),
            "corr_val": _interval(res.corr_val),
            "lambda_train_v9_window": _interval(entry["lambda_train_v9_window"]),
            "n_train": res.n_train,
            "n_val": res.n_val,
            "sigma_anchored_train": round(res.sigma_train, 3),
        }
    artifact: dict[str, Any] = {
        "model_version": MODEL_VERSION,
        "status": "research",
        "serving": "none: not registered, not whitelisted, no champion route; research artifact only",
        "engine": "model_prediction.models.nba_player_v10",
        "features": "model_prediction.features.nba_player_ratings",
        "spec": "outputs/nba/v10_player/PREREG.md",
        "report": "outputs/nba/v10_player/G1_PLAYER.md",
        "split": {
            "train": f"{MODEL_START} .. < {VAL_START}",
            "validation": f"{VAL_START} .. < {HOLDOUT_START}",
        },
        "rating_params": choice,
        "box_prior": {
            str(int(h)): _read_json(_box_path(h))["prior"]
            for h in {float(choice["margin"]["half_life_days"]), float(choice["total"]["half_life_days"])}
        },
        "minutes_model": _read_json(FITS / "minutes.json")["minutes_model"],
        "coefficients": {k: c.to_dict() for k, c in coefs.items() if k != "oracle"},
        "g1": markets,
        "note": "lambda ships at 0 for every market/variant that failed G1; not re-tuned on validation.",
    }
    artifact["artifact_hash"] = compute_artifact_hash(artifact)
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {ARTIFACT}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["fit-minutes", "fit-box", "cv", "walk", "g1"])
    parser.add_argument("--through", default=VAL_START, help="exclusive end date of the walk")
    parser.add_argument("--draws", type=int, default=2000)
    args = parser.parse_args()
    if args.command == "fit-minutes":
        cmd_fit_minutes()
    elif args.command == "fit-box":
        cmd_fit_box()
    elif args.command == "cv":
        cmd_cv()
    elif args.command == "walk":
        cmd_walk(args.through)
    else:
        cmd_g1(args.draws)


if __name__ == "__main__":
    main()
