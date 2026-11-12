"""Point-in-time walk-forward ablation for the NCAAB v6 review fixes (docs/MODEL_REVIEW_2026-11-11.md).

Why this exists: findings #7, #9, #10 and #11 change what ncaab-spread-v6 / ncaab-total-v6
serve. Each fix is a constructor parameter whose default reproduces the live model, so the
"control" variant here IS the champion and every candidate is the champion plus one or more
fixes. Selection happens on the validation split only; the holdout is reported once.

Reproduction gate (ablation-reproduction-gate rule): before any candidate number is trusted,
the control variant is re-run on the exact history the live daily used for its most recent
Flat rows and compared per row against the recorded feature_basis / probability. The report
leads with that table; a failed gate is a failed run.

Data: data/historical/ncaab_games_all.jsonl via FeatureStore (same loader as serving). There
are no market lines in that file, so the evaluation is distributional, not market-relative:
moneyline Brier / log loss, margin and total error, PIT coverage of the served distribution,
and cover / over Brier at pseudo-lines placed around the projection. The cross-season window
(2026-11) is the only early-season regime with a prior season behind it and is reported as a
diagnostic subset; it lands in the holdout by construction.

Usage:
    env PYTHONPATH=src:. python scripts/ncaab_v6_review_ablation.py [--workers 4] [--no-write]
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import sqlite3
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

sys.path.insert(0, "src")

from scipy import stats

from model_prediction.domain import EASTERN
from model_prediction.features.base import FeatureStore, GameRecord
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.ncaab_spread_v6 import NCAABSpreadModelV6
from model_prediction.models.ncaab_total_v6 import NCAABTotalModelV6

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "outputs" / "ncaab"
GRADE_FROM = "2025-12-01"  # four weeks of warm-up history before the first graded day
RUNTIME_LEDGER = Path(r"E:\model-prediction-runtime\ledgers\ledgers.db")

SPREAD_VARIANTS: dict[str, dict[str, Any]] = {
    "control": {},
    "artifact_retention_0.20": {"offseason_retention": 0.20},
    "coherent_ml": {"coherent_moneyline": True},
    "t_scale_matched": {"student_t_scale_matches_sd": True},
    "elo_ramp_8": {"elo_weight_ramp_games": 8.0},
    "tiered_trend_priors": {"tiered_trend_priors": True},
    "combo": {
        "coherent_moneyline": True,
        "student_t_scale_matches_sd": True,
        "elo_weight_ramp_games": 8.0,
        "tiered_trend_priors": True,
    },
    "additive_matchup": {"additive_matchup": True},
    "tiered_priors_additive": {"tiered_trend_priors": True, "additive_matchup": True},
    "combo_additive": {
        "coherent_moneyline": True,
        "student_t_scale_matches_sd": True,
        "elo_weight_ramp_games": 8.0,
        "tiered_trend_priors": True,
        "additive_matchup": True,
    },
    # pass 2: the t-scale change is dropped (it narrows a distribution that already covers
    # 50/80/90% within a point); the finalists combine the fixes that helped on validation.
    "final": {
        "coherent_moneyline": True,
        "elo_weight_ramp_games": 8.0,
        "tiered_trend_priors": True,
        "additive_matchup": True,
    },
    "final_hca_6.0": {
        "coherent_moneyline": True,
        "elo_weight_ramp_games": 8.0,
        "tiered_trend_priors": True,
        "additive_matchup": True,
        "home_court_points": 6.0,
    },
    "final_hca_5.0_elo45": {
        "coherent_moneyline": True,
        "elo_weight_ramp_games": 8.0,
        "tiered_trend_priors": True,
        "additive_matchup": True,
        "home_court_points": 5.0,
        "elo_home_advantage": 45.0,
    },
    # SHIPPED 2026-11-12: validation-selected. The Elo ramp is left off because the validation
    # split (samples >= 8 everywhere) cannot distinguish it; the t-scale change is rejected.
    "shipped": {
        "coherent_moneyline": True,
        "tiered_trend_priors": True,
        "additive_matchup": True,
        "home_court_points": 5.0,
        "elo_home_advantage": 45.0,
    },
    "final_ret_0.20": {
        "coherent_moneyline": True,
        "elo_weight_ramp_games": 8.0,
        "tiered_trend_priors": True,
        "additive_matchup": True,
        "offseason_retention": 0.20,
    },
    "combo_hca_8.5": {
        "coherent_moneyline": True,
        "student_t_scale_matches_sd": True,
        "elo_weight_ramp_games": 8.0,
        "tiered_trend_priors": True,
        "home_court_points": 8.5,
        "elo_home_advantage": 70.0,
    },
    "combo_ret_0.20": {
        "coherent_moneyline": True,
        "student_t_scale_matches_sd": True,
        "elo_weight_ramp_games": 8.0,
        "tiered_trend_priors": True,
        "offseason_retention": 0.20,
    },
}

TOTAL_VARIANTS: dict[str, dict[str, Any]] = {
    "control": {},
    "pace_vs_league": {"pace_baseline": "league"},
    "conference_trend_priors": {"conference_trend_priors": True},
    "conf_priors_wleague_0.15": {"conference_trend_priors": True, "league_total_weight": 0.15},
    "conf_priors_ret_0.35": {"conference_trend_priors": True, "offseason_retention": 0.35},
    # pass 2: conference prior as the shrinkage target, league mean as the pace reference (a
    # no-information Northeast team is slow, which is what its conference baseline says).
    "final": {"conference_trend_priors": True, "pace_baseline": "league"},
    "final_sd_15.5": {"conference_trend_priors": True, "pace_baseline": "league", "total_sd": 15.5},
    "final_sd_16.5": {"conference_trend_priors": True, "pace_baseline": "league", "total_sd": 16.5},
    "final_sd_17.5": {"conference_trend_priors": True, "pace_baseline": "league", "total_sd": 17.5},
    "final_wleague_0.15": {
        "conference_trend_priors": True,
        "pace_baseline": "league",
        "league_total_weight": 0.15,
    },
    # SHIPPED 2026-11-12: validation-selected (total_sd 16.5 gives 0.489/0.816/0.919 PIT coverage).
    "shipped": {"conference_trend_priors": True, "pace_baseline": "league", "total_sd": 16.5},
}


# ----------------------------------------------------------------------------- data


def _et_cutoff(day: str) -> datetime:
    return datetime.combine(date.fromisoformat(day), time.min, tzinfo=EASTERN)


def _history_digest(history: list[GameRecord]) -> str:
    """Same digest college_basketball_forward.py stamps into feature_basis."""
    return hashlib.sha256(
        json.dumps(
            sorted((g.event_id, g.event_start_utc, g.home_score, g.away_score) for g in history),
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def load_games() -> list[GameRecord]:
    games = FeatureStore(ROOT / "data").load_games("ncaab")
    return sorted(games, key=lambda g: g.start)


def graded_days(games: list[GameRecord]) -> list[str]:
    days = sorted({g.start.astimezone(EASTERN).date().isoformat() for g in games})
    return [d for d in days if d >= GRADE_FROM]


def split_days(days: list[str]) -> dict[str, str]:
    n = len(days)
    train_end = days[int(n * 0.6) - 1]
    val_end = days[int(n * 0.8) - 1]
    out: dict[str, str] = {}
    for d in days:
        out[d] = "train" if d <= train_end else ("validation" if d <= val_end else "holdout")
    return out


# ----------------------------------------------------------------------------- scoring


def _cdf(x: float, mean: float, scale: float, df: float | None) -> float:
    z = (x - mean) / scale
    return float(stats.t.cdf(z, df=df)) if df else float(stats.norm.cdf(z))


def _coverage(pits: list[float], central: float) -> float:
    lo, hi = (1 - central) / 2, 1 - (1 - central) / 2
    return sum(1 for u in pits if lo <= u <= hi) / len(pits) if pits else float("nan")


def _slope_intercept(probs: list[float], outcomes: list[int]) -> tuple[float, float]:
    """Logistic recalibration slope/intercept via a few Newton steps (1.0 / 0.0 = calibrated)."""
    xs = [math.log(p / (1 - p)) for p in probs]
    a, b = 1.0, 0.0
    for _ in range(25):
        g_a = g_b = h_aa = h_ab = h_bb = 0.0
        for x, y in zip(xs, outcomes, strict=True):
            p = 1 / (1 + math.exp(-(a * x + b)))
            w = p * (1 - p)
            g_a += (y - p) * x
            g_b += y - p
            h_aa += w * x * x
            h_ab += w * x
            h_bb += w
        det = h_aa * h_bb - h_ab * h_ab
        if abs(det) < 1e-12:
            break
        da = (g_a * h_bb - g_b * h_ab) / det
        db = (g_b * h_aa - g_a * h_ab) / det
        a, b = a + da, b + db
        if abs(da) < 1e-9 and abs(db) < 1e-9:
            break
    return a, b


def summarise_spread(rows: list[dict[str, Any]], sd_fit: float | None = None) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    resid = [r["margin"] - r["mean"] for r in rows]
    p = [min(0.9999, max(0.0001, r["p_home"])) for r in rows]
    y = [1 if r["margin"] > 0 else 0 for r in rows]
    brier = sum((pi - yi) ** 2 for pi, yi in zip(p, y, strict=True)) / len(rows)
    logloss = -sum(
        yi * math.log(pi) + (1 - yi) * math.log(1 - pi) for pi, yi in zip(p, y, strict=True)
    ) / len(rows)
    slope, intercept = _slope_intercept(p, y)
    pits = [_cdf(r["margin"] + 0.5, r["mean"], r["scale"], r["df"]) for r in rows]
    cover_sq: list[float] = []
    for r in rows:
        for off in (-8.0, -4.0, 0.0, 4.0, 8.0):
            line = math.floor(r["mean"]) + 0.5 + off
            p_home_cover = 1 - _cdf(line, r["mean"], r["scale"], r["df"])
            cover_sq.append((p_home_cover - (1 if r["margin"] > line else 0)) ** 2)
    out = {
        "n": len(rows),
        "ml_brier": round(brier, 4),
        "ml_logloss": round(logloss, 4),
        "ml_slope": round(slope, 3),
        "ml_intercept": round(intercept, 3),
        "home_win_rate": round(sum(y) / len(y), 3),
        "mean_p_home": round(sum(p) / len(p), 3),
        "margin_mae": round(sum(abs(e) for e in resid) / len(resid), 3),
        "margin_bias": round(sum(resid) / len(resid), 3),
        "resid_sd": round(math.sqrt(sum(e * e for e in resid) / len(resid)), 3),
        "mean_served_scale": round(sum(r["scale"] for r in rows) / len(rows), 3),
        "pit_cov50": round(_coverage(pits, 0.5), 3),
        "pit_cov80": round(_coverage(pits, 0.8), 3),
        "pit_cov90": round(_coverage(pits, 0.9), 3),
        "cover_brier": round(sum(cover_sq) / len(cover_sq), 4),
    }
    if sd_fit is not None:
        pits_fit = []
        for r in rows:
            widen = r["scale"] / r["base_scale"]  # keep the dynamic-volatility widening, swap the base
            scale = sd_fit * (
                math.sqrt((r["df"] - 2) / r["df"]) if (r["df"] and r["t_scale_matched"]) else 1.0
            )
            pits_fit.append(_cdf(r["margin"] + 0.5, r["mean"], scale * widen, r["df"]))
        out["pit_cov50_sdfit"] = round(_coverage(pits_fit, 0.5), 3)
        out["pit_cov80_sdfit"] = round(_coverage(pits_fit, 0.8), 3)
        out["pit_cov90_sdfit"] = round(_coverage(pits_fit, 0.9), 3)
    return out


def summarise_total(rows: list[dict[str, Any]], sd_fit: float | None = None) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    resid = [r["total"] - r["mean"] for r in rows]
    pits = [_cdf(r["total"] + 0.5, r["mean"], r["sd"], None) for r in rows]
    over_sq: list[float] = []
    for r in rows:
        for off in (-8.0, -4.0, 0.0, 4.0, 8.0):
            line = math.floor(r["mean"]) + 0.5 + off
            p_over = 1 - _cdf(line, r["mean"], r["sd"], None)
            over_sq.append((p_over - (1 if r["total"] > line else 0)) ** 2)
    out = {
        "n": len(rows),
        "total_mae": round(sum(abs(e) for e in resid) / len(resid), 3),
        "total_bias": round(sum(resid) / len(resid), 3),
        "resid_sd": round(math.sqrt(sum(e * e for e in resid) / len(resid)), 3),
        "mean_served_sd": round(sum(r["sd"] for r in rows) / len(rows), 3),
        "pit_cov50": round(_coverage(pits, 0.5), 3),
        "pit_cov80": round(_coverage(pits, 0.8), 3),
        "pit_cov90": round(_coverage(pits, 0.9), 3),
        "over_brier": round(sum(over_sq) / len(over_sq), 4),
    }
    if sd_fit is not None:
        pits_fit = [_cdf(r["total"] + 0.5, r["mean"], sd_fit * r["sd"] / r["base_sd"], None) for r in rows]
        out["pit_cov50_sdfit"] = round(_coverage(pits_fit, 0.5), 3)
        out["pit_cov80_sdfit"] = round(_coverage(pits_fit, 0.8), 3)
        out["pit_cov90_sdfit"] = round(_coverage(pits_fit, 0.9), 3)
    return out


# ----------------------------------------------------------------------------- walk-forward


def _run_variant(args: tuple[str, str, dict[str, Any]]) -> tuple[str, str, list[dict[str, Any]]]:
    market, name, kwargs = args
    games = load_games()
    starts = [g.start for g in games]
    days = graded_days(games)
    splits = split_days(days)
    by_day: dict[str, list[GameRecord]] = defaultdict(list)
    for g in games:
        by_day[g.start.astimezone(EASTERN).date().isoformat()].append(g)
    model: Any = NCAABSpreadModelV6(**kwargs) if market == "spread" else NCAABTotalModelV6(**kwargs)
    rows: list[dict[str, Any]] = []
    for day in days:
        history = games[: bisect.bisect_left(starts, _et_cutoff(day))]
        todays = by_day[day]
        if not history or not todays:
            continue
        upcoming = [
            UpcomingGame(g.event_id, g.event_start_utc, g.away_team, g.home_team, league="NCAAB")
            for g in todays
        ]
        preds = model.predict_games(history, upcoming)
        actual = {g.event_id: g for g in todays}
        for p in preds:
            g = actual[p.event_id]
            fb = p.feature_basis
            if market == "spread":
                if p.market_type != "moneyline":
                    continue
                rows.append(
                    {
                        "day": day,
                        "split": splits[day],
                        "sample": fb.get("current_season_sample"),
                        "mean": float(p.inference_inputs["mean"]),
                        "scale": float(p.inference_inputs.get("scale", p.inference_inputs["sd"])),
                        "base_scale": float(fb.get("base_margin_scale", model.margin_sd)),
                        "df": model.student_t_df if model.use_student_t_spreads else None,
                        "t_scale_matched": bool(getattr(model, "student_t_scale_matches_sd", False)),
                        "p_home": float(p.probabilities["home"]),
                        "margin": g.margin,
                    }
                )
            else:
                rows.append(
                    {
                        "day": day,
                        "split": splits[day],
                        "sample": fb.get("current_season_sample"),
                        "mean": float(p.inference_inputs["mean"]),
                        "sd": float(p.inference_inputs["sd"]),
                        "base_sd": model.total_sd,
                        "total": g.total,
                    }
                )
    return market, name, rows


def evaluate(market: str, name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    summarise = summarise_spread if market == "spread" else summarise_total
    key = "margin" if market == "spread" else "total"
    train = [r for r in rows if r["split"] == "train"]
    sd_fit = math.sqrt(sum((r[key] - r["mean"]) ** 2 for r in train) / len(train)) if train else None
    out: dict[str, Any] = {"variant": name, "train_resid_sd": round(sd_fit, 3) if sd_fit else None}
    for split in ("train", "validation", "holdout"):
        out[split] = summarise([r for r in rows if r["split"] == split], sd_fit)
    out["holdout_thin_sample_lt8"] = summarise(
        [r for r in rows if r["split"] == "holdout" and isinstance(r["sample"], int) and r["sample"] < 8],
        sd_fit,
    )
    out["cross_season_2026-11"] = summarise([r for r in rows if r["day"] >= "2026-11-01"], sd_fit)
    return out


# ----------------------------------------------------------------------------- reproduction gate


def reproduction_gate() -> dict[str, Any]:
    """Re-run the control models on the live daily's exact history and diff against its Flat rows."""
    if not RUNTIME_LEDGER.exists():
        return {"status": "skipped", "reason": f"{RUNTIME_LEDGER} not found"}
    con = sqlite3.connect(f"file:{RUNTIME_LEDGER.as_posix()}?mode=ro", uri=True)
    cur = con.cursor()
    latest = cur.execute(
        "select max(created_at_utc) from ledger_records where ledger_tier='flat' "
        "and model_id in ('ncaab-spread-v6','ncaab-total-v6')"
    ).fetchone()[0]
    rows = cur.execute(
        "select model_id, market_type, selection, line, model_probability, event_start_utc, "
        "feature_payload_json from ledger_records where ledger_tier='flat' and created_at_utc=? "
        "and model_id in ('ncaab-spread-v6','ncaab-total-v6')",
        (latest,),
    ).fetchall()
    con.close()
    store = FeatureStore(ROOT / "data")
    spread = NCAABSpreadModelV6()
    total = NCAABTotalModelV6()
    table: list[dict[str, Any]] = []
    mismatches = 0
    parsed = []
    for model_id, market, selection, line, prob, start, payload_json in rows:
        payload = json.loads(payload_json or "{}")
        snap = payload.get("model_input_snapshot") or {}
        parsed.append((model_id, market, selection, line, prob, start, snap, snap.get("features") or {}))
    # Older total rows did not record neutral_site; take it from the same event's spread/ML row.
    neutral_by_event = {
        (snap.get("away_team"), snap.get("home_team"), start): bool(feats.get("neutral_site"))
        for _m, market, _s, _l, _p, start, snap, feats in parsed
        if market != "total" and "neutral_site" in feats
    }
    for model_id, market, selection, line, prob, start, snap, feats in parsed:
        cutoff = feats.get("history_cutoff_date")
        if not cutoff:
            continue
        history = store.games_before("ncaab", cutoff)
        digest_ok = _history_digest(history) == feats.get("history_scores_sha256")
        if "neutral_site" in feats:
            neutral = bool(feats["neutral_site"])
        else:
            neutral = neutral_by_event.get((snap.get("away_team"), snap.get("home_team"), start), False)
        # The ledger stores the selected side's own line; the model takes the away line.
        away_line = None
        if market == "spread" and line is not None:
            away_line = float(line) if selection == "away" else -float(line)
        game = UpcomingGame(
            "repro",
            start,
            snap["away_team"],
            snap["home_team"],
            spread_away_line=away_line,
            total_line=line if market == "total" else None,
            neutral_site=neutral,
            league="NCAAB",
        )
        if market == "total":
            pred = total.predict_games(history, [game])[0]
            compared = {
                k: feats.get(k)
                for k in ("expected_total", "raw_model_total", "expected_pace", "effective_total_sd")
            }
        else:
            preds = spread.predict_games(history, [game])
            pred = next(p for p in preds if p.market_type == market)
            compared = {
                k: feats.get(k)
                for k in (
                    "expected_margin",
                    "elo_margin",
                    "trend_margin",
                    "rest_diff",
                    "effective_margin_sd",
                    "elo_home",
                    "elo_away",
                )
            }
        deltas = {
            k: round(float(pred.feature_basis[k]) - float(v), 4) for k, v in compared.items() if v is not None
        }
        p_delta = round(float(pred.probabilities[selection]) - float(prob), 6)
        ok = digest_ok and all(abs(d) < 1e-6 for d in deltas.values()) and abs(p_delta) < 1e-6
        mismatches += 0 if ok else 1
        table.append(
            {
                "model": model_id,
                "market": market,
                "teams": f"{snap['away_team']} @ {snap['home_team']}",
                "history_digest_match": digest_ok,
                "history_games": len(history),
                "feature_deltas": deltas,
                "probability_delta": p_delta,
                "match": ok,
            }
        )
    return {
        "status": "pass" if table and not mismatches else "FAIL",
        "rows": len(table),
        "mismatches": mismatches,
        "created_at_utc": latest,
        "table": table,
    }


# ----------------------------------------------------------------------------- report


def _fmt_table(results: list[dict[str, Any]], split: str, keys: list[str]) -> str:
    head = "| variant | " + " | ".join(keys) + " |\n|---|" + "---|" * len(keys) + "\n"
    body = ""
    for r in results:
        s = r[split]
        body += f"| {r['variant']} | " + " | ".join(str(s.get(k, "")) for k in keys) + " |\n"
    return head + body


def write_report(
    gate: dict[str, Any], spread: list[dict[str, Any]], total: list[dict[str, Any]], days: list[str]
) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    splits = split_days(days)
    bounds = {
        s: (min(d for d in days if splits[d] == s), max(d for d in days if splits[d] == s))
        for s in ("train", "validation", "holdout")
    }
    s_keys = [
        "n",
        "ml_brier",
        "ml_logloss",
        "ml_slope",
        "mean_p_home",
        "home_win_rate",
        "margin_mae",
        "margin_bias",
        "resid_sd",
        "mean_served_scale",
        "pit_cov50",
        "pit_cov80",
        "pit_cov90",
        "cover_brier",
    ]
    t_keys = [
        "n",
        "total_mae",
        "total_bias",
        "resid_sd",
        "mean_served_sd",
        "pit_cov50",
        "pit_cov80",
        "pit_cov90",
        "over_brier",
    ]
    md = [
        f"# NCAAB v6 review ablation -- point-in-time walk-forward\n\nGenerated {datetime.now(UTC).isoformat()}.\n"
    ]
    md.append("## Reproduction gate (control vs live Flat rows)\n")
    md.append(
        f"Status: **{gate['status']}** -- {gate.get('rows', 0)} rows from {gate.get('created_at_utc')}, {gate.get('mismatches', 'n/a')} mismatches.\n"
    )
    for row in gate.get("table", []):
        md.append(
            f"- {row['model']} {row['market']} {row['teams']}: digest {row['history_digest_match']}, history {row['history_games']}, feature deltas {row['feature_deltas']}, probability delta {row['probability_delta']} -> {'ok' if row['match'] else 'MISMATCH'}"
        )
    md.append("")
    md.append(f"## Splits (complete ET dates, graded from {GRADE_FROM})\n")
    for s, (a, b) in bounds.items():
        md.append(f"- {s}: {a} -> {b}")
    md.append(
        "\nPIT coverage = share of actual outcomes inside the served distribution's central 50/80/90% interval (ideal 0.50/0.80/0.90). `cover_brier` / `over_brier`: Brier of the served cover/over probability at pseudo-lines floor(mean)+0.5 + {-8,-4,0,4,8}.\n"
    )
    for label, res, keys in (
        ("Spread / moneyline (ncaab-spread-v6)", spread, s_keys),
        ("Total (ncaab-total-v6)", total, t_keys),
    ):
        md.append(f"## {label}\n")
        md.append(
            "Train residual SD per variant: "
            + ", ".join(f"{r['variant']}={r['train_resid_sd']}" for r in res)
            + "\n"
        )
        for split in ("validation", "holdout", "holdout_thin_sample_lt8", "cross_season_2026-11"):
            md.append(f"### {split}\n")
            md.append(_fmt_table(res, split, keys))
    (OUT_DIR / "ncaab_v6_review_ablation.md").write_text("\n".join(md), encoding="utf-8")
    (OUT_DIR / "ncaab_v6_review_ablation.json").write_text(
        json.dumps(
            {
                "generated_at_utc": datetime.now(UTC).isoformat(),
                "gate": gate,
                "spread": spread,
                "total": total,
                "splits": bounds,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--only", choices=["spread", "total"], default=None)
    ap.add_argument(
        "--variants",
        default=None,
        help="comma-separated variant names to run; results are merged into the existing report",
    )
    args = ap.parse_args()
    wanted = set(args.variants.split(",")) if args.variants else None

    gate = reproduction_gate()
    print(
        f"reproduction gate: {gate['status']} ({gate.get('rows')} rows, {gate.get('mismatches')} mismatches)"
    )
    for row in gate.get("table", []):
        if not row["match"]:
            print("  MISMATCH", row)
    if gate["status"] == "FAIL":
        print("Control does not reproduce the live rows; candidate numbers below are NOT trustworthy.")

    jobs: list[tuple[str, str, dict[str, Any]]] = []
    if args.only != "total":
        jobs += [("spread", n, k) for n, k in SPREAD_VARIANTS.items() if wanted is None or n in wanted]
    if args.only != "spread":
        jobs += [("total", n, k) for n, k in TOTAL_VARIANTS.items() if wanted is None or n in wanted]
    results: dict[str, list[dict[str, Any]]] = {"spread": [], "total": []}
    previous_path = OUT_DIR / "ncaab_v6_review_ablation.json"
    if wanted is not None and previous_path.exists():
        # Partial run: start from the existing report so the merged output stays complete.
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
        results = {
            m: [r for r in previous.get(m, []) if r["variant"] not in wanted] for m in ("spread", "total")
        }
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for market, name, rows in pool.map(_run_variant, jobs):
            ev = evaluate(market, name, rows)
            results[market].append(ev)
            print(f"{market:6s} {name:28s} val={ev['validation']}  holdout={ev['holdout']}")
    for market, variants in (("spread", SPREAD_VARIANTS), ("total", TOTAL_VARIANTS)):
        order = list(variants)
        results[market].sort(key=lambda r: order.index(r["variant"]) if r["variant"] in order else len(order))
    days = graded_days(load_games())
    if not args.no_write:
        write_report(gate, results["spread"], results["total"], days)
        print(f"wrote {OUT_DIR / 'ncaab_v6_review_ablation.md'}")
    if gate["status"] == "FAIL":
        sys.exit(2)


if __name__ == "__main__":
    main()
