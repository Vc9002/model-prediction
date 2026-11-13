"""Replay the fixed NCAAF champion (cfb-structural-v4 as now served) point-in-time.

Two cohorts, nothing skipped, nothing written except the report:

1. Every completed NCAAF game the serving history loader knows about
   (data/historical/ncaaf_games_all.jsonl + data/processed/ncaaf/games.jsonl, 2016 -> today).
   For each game the model sees only games that kicked off strictly before it. No market
   lines exist for this cohort, so it scores the moneyline (Brier, log loss, calibration,
   by season) against the model's own Elo channel and a home-constant baseline, and it
   checks the score-distribution variance: realized margin / total residual SD against the
   effective SDs the served engine uses. The file's weather fields are placeholders
   (65 F / 5 mph everywhere), so weather is treated as unavailable (no weather term), as
   production does when no verified forecast exists.

2. Every settled NCAAF ledger row (Flat and Main, every model version ever served),
   re-priced by the fixed model at the row's own line and executable ask with history as
   of the row's decision time: "as if every settled pick had been produced by this model".
   Reports old vs new Brier on the recorded selection, how often the fixed model would
   have fired under the artifact gates (2% <= edge <= 8%, FCS block, 21.5 spread cap,
   33.3% moneyline ask floor), and the flat P&L of those fires at the recorded ask.

Caveats carried into the report: history rows are not vintaged (scores only; the
production snapshots carry the same historical_source_observation_times_verified=False);
QB availability has no point-in-time source before 2026-11 (channel inert here); week
numbers are the file's where present, else derived from days since the season's first game.

Usage: PYTHONPATH=src .venv/Scripts/python scripts/replay_cfb_v4_all_settled.py [--limit N]
Writes outputs/ncaaf/cfb_v4_replay.json and outputs/ncaaf/cfb_v4_replay.md
"""

from __future__ import annotations

import argparse
import bisect
import json
import sqlite3
import statistics
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, "src")

from model_prediction.calibration import calibration_metrics
from model_prediction.domain import parse_utc
from model_prediction.models.cfb_structural_v4 import (
    load_cfb_structural_v4_artifact,
    main_gates_from_artifact,
)
from model_prediction.models.college_football import (
    CFB_POSTSEASON_WEEK,
    UpcomingCFBGame,
    _load_cfb_history,
    cfb_model,
    cfb_serving_code_hash,
)

REPO = Path(__file__).resolve().parents[1]
LEDGER_DB = Path(r"E:\model-prediction-runtime\ledgers\ledgers.db")
OUT_DIR = REPO / "outputs" / "ncaaf"


def _pnl(ask: float, won: bool) -> float:
    return (1.0 / ask - 1.0) if won else -1.0


def season_of(start: datetime) -> int:
    return start.year - 1 if start.month in (1, 2) else start.year


def derive_week(start: datetime, season_first_kick: dict[int, datetime]) -> int:
    """Week from days since the season's first kickoff; bowls/CFP (mid-Dec onward, Jan) are postseason."""
    season = season_of(start)
    if start.month in (1, 2) or (start.month == 12 and start.day >= 14):
        return CFB_POSTSEASON_WEEK
    first = season_first_kick.get(season)
    if first is None:
        return 1
    return max(0, min(14, 1 + (start - first).days // 7))


def metrics_block(probs: list[float], outs: list[int]) -> dict[str, Any]:
    m = (
        calibration_metrics(probs, outs)
        if len(probs) >= 30
        else {"status": "insufficient_sample", "n": len(probs)}
    )
    hits = sum(1 for p, y in zip(probs, outs, strict=True) if (p >= 0.5) == bool(y))
    return {
        "n": len(probs),
        "brier": m.get("brier_score"),
        "log_loss": m.get("log_loss"),
        "ece": m.get("expected_calibration_error"),
        "calibration_slope": m.get("calibration_slope"),
        "calibration_intercept": m.get("calibration_intercept"),
        "accuracy_favoured_side": round(hits / len(probs), 4) if probs else None,
        "reliability_buckets": m.get("reliability_buckets"),
    }


def load_schedule_facts() -> dict[str, dict[str, Any]]:
    """event_id -> {week, is_neutral_site} from the raw history files (CFBGameRecord drops both)."""
    facts: dict[str, dict[str, Any]] = {}
    for rel in ("data/historical/ncaaf_games_all.jsonl", "data/processed/ncaaf/games.jsonl"):
        path = REPO / rel
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            eid = str(row.get("event_id"))
            cur = facts.setdefault(eid, {})
            if row.get("week") is not None and "week" not in cur:
                cur["week"] = int(row["week"])
            if row.get("is_neutral_site") is not None and "is_neutral_site" not in cur:
                cur["is_neutral_site"] = bool(row["is_neutral_site"])
    return facts


def _sd(values: list[float]) -> float | None:
    return round(statistics.pstdev(values), 3) if len(values) >= 2 else None


# --------------------------------------------------------------------------- cohort 1
def replay_history(model, history, facts: dict[str, dict[str, Any]], *, limit: int | None) -> dict[str, Any]:
    starts = [h.start for h in history]
    season_first: dict[int, datetime] = {}
    for h in history:
        s = season_of(h.start)
        if s not in season_first or h.start < season_first[s]:
            season_first[s] = h.start
    rows: list[dict[str, Any]] = []
    t0 = time.time()
    games = history if limit is None else history[-limit:]
    for i, g in enumerate(games):
        if g.away_score is None or g.home_score is None:
            continue
        cut = bisect.bisect_left(starts, g.start)
        past = history[:cut]
        fact = facts.get(str(g.event_id), {})
        week = fact.get("week")
        if week is None:
            week = derive_week(g.start, season_first)
        up = UpcomingCFBGame(
            event_id=str(g.event_id),
            event_start_utc=g.event_start_utc,
            away_team=g.away_team,
            home_team=g.home_team,
            spread_home_line=None,
            total_line=None,
            wind_mph=None,
            temperature_f=None,
            precipitation_in=None,
            is_neutral_site=bool(fact.get("is_neutral_site", False)),
            season_year=season_of(g.start),
            week=int(week),
        )
        preds = model.predict_matchup(past, up, project_missing_lines=True)
        if not preds:
            continue
        ml = preds[0]
        fb = ml.feature_basis
        margin = float(g.home_score) - float(g.away_score)
        total = float(g.home_score) + float(g.away_score)
        rows.append(
            {
                "event_id": str(g.event_id),
                "season": season_of(g.start),
                "p_home": float(ml.probabilities["home"]),
                "elo_p_home": float(fb["elo_home_win_prob"]),
                "home_win": int(margin > 0),
                "tie": int(margin == 0),
                "proj_margin": float(fb["projected_margin_home"]),
                "proj_total": float(fb["projected_total"]),
                "margin": margin,
                "total": total,
                "eff_margin_sd": float(ml.inference_inputs["parameters"]["margin_sd"]),
                "eff_total_sd": float(ml.inference_inputs["parameters"]["total_sd"]),
                "uncertainty": float(fb["uncertainty"]),
                "is_conf": bool(fb["is_conference_matchup"]),
                "is_fcs": bool(fb["is_fbs_vs_fcs"]),
                "sample_games": int(fb["sample_games"]),
                "neutral": up.is_neutral_site,
            }
        )
        if (i + 1) % 500 == 0:
            print(f"  cohort1 {i + 1}/{len(games)} ({time.time() - t0:.0f}s)", flush=True)
    scored = [r for r in rows if not r["tie"]]

    def block(sub: list[dict[str, Any]]) -> dict[str, Any]:
        probs = [r["p_home"] for r in sub]
        outs = [r["home_win"] for r in sub]
        out = {
            "model": metrics_block(probs, outs),
            "elo_channel_only": metrics_block([r["elo_p_home"] for r in sub], outs),
            "home_constant_baseline_brier": round(
                sum((sum(outs) / len(outs) - y) ** 2 for y in outs) / len(outs), 4
            )
            if outs
            else None,
            "margin_residual_sd": _sd([r["margin"] - r["proj_margin"] for r in sub]),
            "mean_effective_margin_sd": round(statistics.fmean(r["eff_margin_sd"] for r in sub), 3)
            if sub
            else None,
            "total_residual_sd": _sd([r["total"] - r["proj_total"] for r in sub]),
            "mean_effective_total_sd": round(statistics.fmean(r["eff_total_sd"] for r in sub), 3)
            if sub
            else None,
            "margin_bias": round(statistics.fmean(r["margin"] - r["proj_margin"] for r in sub), 3)
            if sub
            else None,
            "total_bias": round(statistics.fmean(r["total"] - r["proj_total"] for r in sub), 3)
            if sub
            else None,
        }
        return out

    by_season = {
        str(s): block([r for r in scored if r["season"] == s]) for s in sorted({r["season"] for r in scored})
    }
    established = [r for r in scored if r["sample_games"] >= 4 and not r["is_fcs"]]
    variance = {
        "all": block(scored),
        "established_fbs_vs_fbs_sample_ge_4": block(established),
        "conference_matchups": block([r for r in established if r["is_conf"]]),
        "non_conference": block([r for r in established if not r["is_conf"]]),
        "fbs_vs_fcs": block([r for r in scored if r["is_fcs"]]),
        "neutral_site": block([r for r in scored if r["neutral"]]),
    }
    unc_buckets = {}
    for lo, hi in ((0.04, 0.07), (0.07, 0.10), (0.10, 0.14), (0.14, 0.19), (0.19, 0.31)):
        sub = [r for r in scored if lo <= r["uncertainty"] < hi]
        if sub:
            unc_buckets[f"{lo:.2f}-{hi:.2f}"] = {
                "n": len(sub),
                "margin_residual_sd": _sd([r["margin"] - r["proj_margin"] for r in sub]),
                "mean_effective_margin_sd": round(statistics.fmean(r["eff_margin_sd"] for r in sub), 3),
                "brier": metrics_block([r["p_home"] for r in sub], [r["home_win"] for r in sub])["brier"],
            }
    return {
        "games_scored": len(scored),
        "ties_excluded": len(rows) - len(scored),
        "overall": block(scored),
        "by_season": by_season,
        "variance_check": variance,
        "by_uncertainty_bucket": unc_buckets,
        "elapsed_s": round(time.time() - t0, 1),
    }


# --------------------------------------------------------------------------- cohort 2
def load_settled_rows() -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{LEDGER_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        select pick_id, ledger_tier, event_id, event_start_utc, market_type, selection, line, model_id,
               model_probability, market_probability, edge, units, result, pnl_units, created_at_utc,
               decision_payload_json, feature_payload_json
        from ledger_records
        where lower(sport)='ncaaf' and status='settled' and ledger_tier in ('flat','main')
        order by event_start_utc, created_at_utc
        """
    ).fetchall()
    return [dict(r) for r in rows]


def replay_ledger(
    model, history, gates, rows: list[dict[str, Any]], facts: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    starts = [h.start for h in history]
    by_event = {str(h.event_id): h for h in history}
    season_first: dict[int, datetime] = {}
    for h in history:
        s = season_of(h.start)
        if s not in season_first or h.start < season_first[s]:
            season_first[s] = h.start
    # Lines of the same event's other markets give the moneyline its spread/total context.
    lines_by_event: dict[str, dict[str, float]] = defaultdict(dict)
    for r in rows:
        if r["line"] is None:
            continue
        sel = str(r["selection"]).lower()
        if r["market_type"] == "spread":
            lines_by_event[str(r["event_id"])]["spread_home"] = float(r["line"]) * (
                1 if sel == "home" else -1
            )
        elif r["market_type"] == "total":
            lines_by_event[str(r["event_id"])]["total"] = float(r["line"])
    scored: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    t0 = time.time()
    for i, r in enumerate(rows):
        if r["result"] not in ("win", "loss"):
            skipped.append({"pick_id": r["pick_id"], "reason": f"result={r['result']}"})
            continue
        decision = json.loads(r["decision_payload_json"] or "{}")
        feats = json.loads(r["feature_payload_json"] or "{}")
        snap_feat = ((feats.get("model_input_snapshot") or {}).get("features")) or {}
        game = by_event.get(str(r["event_id"]))
        home = str(
            decision.get("home_team") or snap_feat.get("home_team") or getattr(game, "home_team", "") or ""
        )
        away = str(
            decision.get("away_team") or snap_feat.get("away_team") or getattr(game, "away_team", "") or ""
        )
        if not home or not away:
            skipped.append({"pick_id": r["pick_id"], "reason": "teams unresolved"})
            continue
        start = parse_utc(str(r["event_start_utc"]))
        observed = parse_utc(str(decision.get("observed_at_utc") or r["created_at_utc"]))
        observed = min(start, observed)  # never later than kickoff; history cut stays strictly pregame
        past = history[: bisect.bisect_left(starts, observed)]
        market = str(r["market_type"]).lower()
        sel = str(r["selection"]).lower()
        ask = float(r["market_probability"]) if r["market_probability"] not in (None, "") else None
        if ask is None or not 0 < ask < 1:
            skipped.append({"pick_id": r["pick_id"], "reason": "no executable ask"})
            continue
        ctx = lines_by_event.get(str(r["event_id"]), {})
        spread_home = ctx.get("spread_home")
        total_line = ctx.get("total")
        if market == "spread":
            spread_home = float(r["line"]) * (1 if sel == "home" else -1)
        elif market == "total":
            total_line = float(r["line"])
        fact = facts.get(str(r["event_id"]), {})
        week = fact.get("week")
        if week is None:
            week = derive_week(start, season_first)
        neutral = bool(snap_feat.get("is_neutral_site", fact.get("is_neutral_site", False)))
        up = UpcomingCFBGame(
            event_id=str(r["event_id"]),
            event_start_utc=str(r["event_start_utc"]),
            away_team=away,
            home_team=home,
            spread_home_line=spread_home,
            total_line=total_line,
            is_neutral_site=neutral,
            season_year=season_of(start),
            week=int(week),
        )
        preds = model.predict_matchup(past, up, project_missing_lines=True)
        pred = next((p for p in preds if p.market_type == market), None)
        if pred is None or sel not in pred.probabilities:
            skipped.append({"pick_id": r["pick_id"], "reason": "no prediction for market/selection"})
            continue
        new_p = float(pred.probabilities[sel])
        old_p = float(r["model_probability"])
        won = r["result"] == "win"
        fb = pred.feature_basis
        edge = new_p - ask
        gate_reasons = []
        if not gates.min_edge <= edge <= gates.max_edge:
            gate_reasons.append("edge_outside_band")
        if gates.block_fcs and fb["is_fbs_vs_fcs"]:
            gate_reasons.append("fcs_blocked")
        if market == "spread" and abs(float(r["line"])) > gates.max_spread_line:
            gate_reasons.append("spread_line_above_max")
        if market == "moneyline" and ask < gates.min_moneyline_ask:
            gate_reasons.append("moneyline_ask_below_floor")
        scored.append(
            {
                "pick_id": r["pick_id"],
                "tier": r["ledger_tier"],
                "model_id": r["model_id"],
                "market": market,
                "selection": sel,
                "line": r["line"],
                "ask": ask,
                "old_p": old_p,
                "new_p": new_p,
                "won": int(won),
                "edge_new": round(edge, 4),
                "edge_old": round(old_p - ask, 4),
                "fires_new": not gate_reasons,
                "gate_reasons": gate_reasons,
                "sportsbook": decision.get("sportsbook"),
                "recorded_pnl": float(r["pnl_units"] or 0.0),
                "recorded_units": float(r["units"] or 0.0),
            }
        )
        if (i + 1) % 250 == 0:
            print(f"  cohort2 {i + 1}/{len(rows)} ({time.time() - t0:.0f}s)", flush=True)

    def summary(sub: list[dict[str, Any]]) -> dict[str, Any]:
        n = len(sub)
        if not n:
            return {"n": 0}
        fires = [s for s in sub if s["fires_new"]]
        agree = sum(1 for s in sub if (s["new_p"] >= s["ask"]) == (s["old_p"] >= s["ask"]))
        out = {
            "n": n,
            "recorded_hit_rate": round(sum(s["won"] for s in sub) / n, 4),
            "recorded_units_pnl": round(sum(s["recorded_pnl"] for s in sub), 2),
            "old_model_brier": round(sum((s["old_p"] - s["won"]) ** 2 for s in sub) / n, 4),
            "new_model_brier": round(sum((s["new_p"] - s["won"]) ** 2 for s in sub) / n, 4),
            "ask_brier": round(sum((s["ask"] - s["won"]) ** 2 for s in sub) / n, 4),
            "mean_old_p": round(statistics.fmean(s["old_p"] for s in sub), 4),
            "mean_new_p": round(statistics.fmean(s["new_p"] for s in sub), 4),
            "mean_ask": round(statistics.fmean(s["ask"] for s in sub), 4),
            "side_agreement_vs_ask": round(agree / n, 4),
            "new_model_fires_under_gates": {
                "bets": len(fires),
                "wins": sum(f["won"] for f in fires),
                "hit_rate": round(sum(f["won"] for f in fires) / len(fires), 4) if fires else None,
                "flat_units_at_ask": round(sum(_pnl(f["ask"], bool(f["won"])) for f in fires), 2),
                "mean_edge": round(statistics.fmean(f["edge_new"] for f in fires), 4) if fires else None,
            },
            "gate_reason_counts": dict(
                sorted(
                    defaultdict(
                        int,
                        {
                            k: sum(1 for s in sub for g in s["gate_reasons"] if g == k)
                            for k in {g for s in sub for g in s["gate_reasons"]}
                        },
                    ).items()
                )
            ),
        }
        return out

    report: dict[str, Any] = {
        "rows_scored": len(scored),
        "rows_skipped": len(skipped),
        "skipped": skipped[:20],
    }
    for tier in ("flat", "main"):
        tier_rows = [s for s in scored if s["tier"] == tier]
        report[tier] = {
            "all": summary(tier_rows),
            "by_market": {
                m: summary([s for s in tier_rows if s["market"] == m])
                for m in ("moneyline", "spread", "total")
            },
            "by_model": {
                mid: summary([s for s in tier_rows if s["model_id"] == mid])
                for mid in sorted({s["model_id"] for s in tier_rows})
            },
            "by_quote_source": {
                src: summary([s for s in tier_rows if s["sportsbook"] == src])
                for src in sorted({str(s["sportsbook"]) for s in tier_rows})
            },
        }
    report["elapsed_s"] = round(time.time() - t0, 1)
    return report


# --------------------------------------------------------------------------- report
def _f(x: Any, nd: int = 4) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def render(report: dict[str, Any]) -> str:
    c1 = report["cohort_1_all_completed_games"]
    c2 = report["cohort_2_settled_ledger_rows"]
    lines = [
        f"# NCAAF fixed-model replay — {report['generated_at_utc'][:19]}Z",
        "",
        (
            f"Model: `{report['model']['version']}` built from the artifact (margin_sd {report['model']['margin_sd']}, "
            f"total_sd {report['model']['total_sd']}, HFA {report['model']['home_advantage_points']}, "
            f"{report['model']['n_simulations']:,} sims), serving code hash `{report['model']['code_hash']}`, "
            f"artifact hash `{report['model']['artifact_hash'][:12]}…`. Point-in-time: each game sees only games that "
            "kicked off before it (cohort 1) or before the pick's decision time (cohort 2). No artifact, calibrator "
            "or ledger was written."
        ),
        "",
        "## Cohort 1 — every completed game, 2016 → today (no market lines exist for this cohort)",
        "",
        (
            f"Games scored: {c1['games_scored']} (ties excluded: {c1['ties_excluded']}). Weather treated as unavailable "
            "(the history file's weather fields are placeholders)."
        ),
        "",
        "| cohort | n | Brier | log loss | ECE | slope | acc. | Elo-only Brier | home-rate Brier | margin resid SD | eff. margin SD | total resid SD | eff. total SD | margin bias | total bias |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    def vrow(label: str, b: dict[str, Any]) -> str:
        m = b["model"]
        return (
            f"| {label} | {m['n']} | {_f(m['brier'])} | {_f(m['log_loss'])} | {_f(m['ece'])} | {_f(m['calibration_slope'], 3)} | "
            f"{_f(m['accuracy_favoured_side'])} | {_f(b['elo_channel_only']['brier'])} | {_f(b['home_constant_baseline_brier'])} | "
            f"{_f(b['margin_residual_sd'], 2)} | {_f(b['mean_effective_margin_sd'], 2)} | {_f(b['total_residual_sd'], 2)} | "
            f"{_f(b['mean_effective_total_sd'], 2)} | {_f(b['margin_bias'], 2)} | {_f(b['total_bias'], 2)} |"
        )

    lines.append(vrow("all", c1["overall"]))
    for season, b in c1["by_season"].items():
        lines.append(vrow(season, b))
    lines += [
        "",
        "### Variance check (is the served SD the realized SD?)",
        "",
        "| slice | n | margin resid SD | eff. margin SD | total resid SD | eff. total SD | Brier |",
        "|---|---|---|---|---|---|---|",
    ]
    for label, b in c1["variance_check"].items():
        m = b["model"]
        lines.append(
            f"| {label} | {m['n']} | {_f(b['margin_residual_sd'], 2)} | {_f(b['mean_effective_margin_sd'], 2)} | "
            f"{_f(b['total_residual_sd'], 2)} | {_f(b['mean_effective_total_sd'], 2)} | {_f(m['brier'])} |"
        )
    lines += [
        "",
        "| uncertainty bucket | n | margin resid SD | eff. margin SD | Brier |",
        "|---|---|---|---|---|",
    ]
    for label, b in c1["by_uncertainty_bucket"].items():
        lines.append(
            f"| {label} | {b['n']} | {_f(b['margin_residual_sd'], 2)} | {_f(b['mean_effective_margin_sd'], 2)} | {_f(b['brier'])} |"
        )
    rb = c1["overall"]["model"].get("reliability_buckets") or []
    if rb:
        lines += [
            "",
            "### Moneyline reliability (all games)",
            "",
            "| bucket | n | mean p(home) | observed |",
            "|---|---|---|---|",
        ]
        for bucket in rb:
            if isinstance(bucket, dict):
                lines.append(
                    f"| {_f(bucket.get('lower'), 1)}-{_f(bucket.get('upper'), 1)} | {bucket.get('count')} | "
                    f"{_f(bucket.get('mean_p'))} | {_f(bucket.get('hit_rate'))} |"
                )
    lines += [
        "",
        "## Cohort 2 — every settled ledger row re-priced by the fixed model",
        "",
        (
            f"Rows scored: {c2['rows_scored']} (skipped {c2['rows_skipped']}: pushes / no ask). Each row is re-priced at its "
            "recorded line and executable ask with history as of its decision time. 'Fires' = the fixed model would have "
            "produced a Main-eligible call under the artifact gates (2% ≤ edge ≤ 8%, FCS block, 21.5 spread cap, "
            "33.3% moneyline ask floor). Flat units = 1u per fire at the recorded ask."
        ),
        "",
    ]

    def srows(title: str, groups: dict[str, dict[str, Any]]) -> None:
        lines.append(f"### {title}")
        lines.append("")
        lines.append(
            "| group | n | recorded hit | recorded units | old Brier | new Brier | ask Brier | mean old p | mean new p | mean ask | fires | fire hit | fire units | mean fire edge |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for label, s in groups.items():
            if not s.get("n"):
                continue
            f = s["new_model_fires_under_gates"]
            lines.append(
                f"| {label} | {s['n']} | {_f(s['recorded_hit_rate'])} | {_f(s['recorded_units_pnl'], 2)} | {_f(s['old_model_brier'])} | "
                f"{_f(s['new_model_brier'])} | {_f(s['ask_brier'])} | {_f(s['mean_old_p'])} | {_f(s['mean_new_p'])} | {_f(s['mean_ask'])} | "
                f"{f['bets']} | {_f(f['hit_rate'])} | {_f(f['flat_units_at_ask'], 2)} | {_f(f['mean_edge'])} |"
            )
        lines.append("")

    for tier in ("flat", "main"):
        t = c2[tier]
        srows(f"{tier.title()} ledger — all rows", {"all": t["all"]})
        srows(f"{tier.title()} ledger — by market", t["by_market"])
        srows(f"{tier.title()} ledger — by model version that produced the row", t["by_model"])
        srows(f"{tier.title()} ledger — by quote source", t["by_quote_source"])
        lines.append(f"Gate reasons ({tier}): `{json.dumps(t['all'].get('gate_reason_counts'))}`")
        lines.append("")
    lines += [
        "## Caveats",
        "",
        "- Cohort 1 has no market lines: moneyline is scored against outcomes only; spread/total are assessed through residual variance, not cover probabilities.",
        "- History rows are scores only and not vintaged; the production snapshots carry the same `historical_source_observation_times_verified=False`.",
        "- Weather is unavailable in both cohorts (placeholder fields / no PIT forecast in old snapshots), so the weather term is zero — a total that would have been weather-adjusted live is unadjusted here.",
        "- QB availability has no point-in-time source before 2026-11; the channel is inert in this replay.",
        "- Cohort 2 asks are the recorded decision-time asks (mostly ESPN consensus, labelled per row); 'fire units' assume that price was executable, which for consensus rows it was not.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="only the last N historical games (smoke test)")
    ap.add_argument("--skip-cohort-1", action="store_true")
    args = ap.parse_args()

    artifact = load_cfb_structural_v4_artifact()
    gates = main_gates_from_artifact(artifact)
    model = cfb_model()
    history = _load_cfb_history(REPO / "data")
    facts = load_schedule_facts()
    print(
        f"history games: {len(history)}; model margin_sd={model.margin_sd} total_sd={model.total_sd} sims={model.n_simulations}"
    )

    report: dict[str, Any] = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "model": {
            "version": model.version,
            "margin_sd": model.margin_sd,
            "total_sd": model.total_sd,
            "home_advantage_points": model.home_advantage_points,
            "n_simulations": model.n_simulations,
            "artifact_hash": artifact["artifact_hash"],
            "code_hash": cfb_serving_code_hash(),
        },
        "gates": gates.__dict__,
        "history_games": len(history),
    }
    if args.skip_cohort_1:
        report["cohort_1_all_completed_games"] = {"skipped": True}
    else:
        print("cohort 1: all completed games")
        report["cohort_1_all_completed_games"] = replay_history(model, history, facts, limit=args.limit)
    print("cohort 2: settled ledger rows")
    rows = load_settled_rows()
    print(f"settled rows: {len(rows)}")
    report["cohort_2_settled_ledger_rows"] = replay_ledger(model, history, gates, rows, facts)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "cfb_v4_replay.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    if not args.skip_cohort_1:
        (OUT_DIR / "cfb_v4_replay.md").write_text(render(report), encoding="utf-8")
    print("wrote", OUT_DIR / "cfb_v4_replay.json")


if __name__ == "__main__":
    main()
