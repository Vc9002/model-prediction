"""Fit the v7.1 probability shrink for the NFL v7 spread/total engines (quick-261123-2w2).

v7.1 serves p' = sigmoid(s * logit(p)) with intercept 0 and 0 <= s <= 1, where p is the v7
engine's final cover/over probability. s is fitted here, honestly:

1. Rows: the same point-in-time cohort as scripts/replay_nfl_v7_all_settled.py (every
   completed 2021+ game with closing spread and total lines in games.jsonl), priced by
   that script's own price_game() with the frozen v7 artifacts. Its main() is NOT called:
   it rewrites the v7 artifacts and outputs/nfl/nfl_v7_replay.*.
2. Split: model_prediction.validation.chronological_split, 60/20/20 on complete
   Eastern-time kickoff dates, once over all priced games, so spread and total share
   the same boundaries.
3. Fit: s maximises the train-window log likelihood of a slope-only logistic on logit(p),
   then is clipped to [0, 1] and rounded to 3 decimals. Every reported number uses the
   rounded value, the one stored in the artifact.
4. Compare raw vs shrunk on validation; report the locked holdout once. That holdout
   (2025-09-28 onward under the 11-12 replay's 80/20 split) was already read by the
   11-12 replay whose 0.24 / 0.13 holdout slopes motivated this change, so the holdout
   numbers here are design-informed, not a clean confirmation.

Totals are fitted on p_over_b (recorded game weather), the variant the 11-12 replay
calibrated on; serving now uses a point-in-time pregame forecast, which B approximates.
Variant A (no weather term) is reported alongside.

Usage (read-only on the data root):
  python scripts/fit_nfl_v71_shrink.py --data-root E:/MP/data [--write-artifacts]

Writes outputs/nfl/nfl_v71_shrink.json and .md; with --write-artifacts also
config/models/nfl-spread-structural-v7.1.json and nfl-total-structural-v7.1.json
(copies of the v7 artifacts plus probability_logit_slope and provenance, re-hashed).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "src"))

import polars as pl
import replay_nfl_v7_all_settled as R

from model_prediction.calibration import calibration_metrics
from model_prediction.cli.forecast_nfl_support import STANDARD_LAY_ASK
from model_prediction.config import load_config
from model_prediction.domain import EASTERN, parse_utc
from model_prediction.models.nfl_spread_v7 import _logit_shrink
from model_prediction.production_registry import compute_artifact_hash
from model_prediction.validation import chronological_split

OUT_DIR = REPO / "outputs" / "nfl"
OUT_JSON = OUT_DIR / "nfl_v71_shrink.json"
OUT_MD = OUT_DIR / "nfl_v71_shrink.md"
SLOPE_DECIMALS = 3
MARKETS = {
    # market: (v7 id, v7.1 id, probability key, outcome key)
    "spread": ("nfl-spread-structural-v7", "nfl-spread-structural-v7.1", "p_home_cover", "home_cover"),
    "total": ("nfl-total-structural-v7", "nfl-total-structural-v7.1", "p_over_b", "over"),
}


@dataclass(frozen=True)
class _SplitRow:
    """The two fields chronological_split reads."""

    date: str
    event_id: str


def _logit(p: float) -> float:
    return math.log(p / (1.0 - p))


def fit_slope(probs: list[float], outcomes: list[int]) -> dict[str, float]:
    """Slope-only logistic MLE of y on logit(p) (intercept fixed at 0), by Newton's method."""
    xs = [_logit(p) for p in probs]
    s = 1.0
    for _ in range(100):
        grad = hess = 0.0
        for x, y in zip(xs, outcomes, strict=True):
            q = 1.0 / (1.0 + math.exp(-s * x))
            grad += x * (y - q)
            hess += x * x * q * (1.0 - q)
        step = grad / hess
        s += step
        if abs(step) < 1e-12:
            break
    info = sum(x * x * (q := 1.0 / (1.0 + math.exp(-s * x))) * (1.0 - q) for x in xs)
    return {"mle": s, "se": 1.0 / math.sqrt(info), "clipped": min(1.0, max(0.0, s))}


def metrics(probs: list[float], outcomes: list[int]) -> dict[str, Any]:
    m = calibration_metrics(probs, outcomes)
    return {
        "n": len(probs),
        "brier": m.get("brier_score"),
        "log_loss": m.get("log_loss"),
        "calibration_slope": m.get("calibration_slope"),
        "calibration_intercept": m.get("calibration_intercept"),
        "ece": m.get("expected_calibration_error"),
        "mean_abs_distance_from_50": round(sum(abs(p - 0.5) for p in probs) / len(probs), 5),
    }


def main_rule() -> dict[str, Any]:
    """The live Main edge band, read from config the way cli/forecast.py reads it.

    _forecast_nfl_spread_sport / _forecast_nfl_total_sport admit a row to Main when
    _main_call_eligible(...) holds: decision CALL, qualified, and
    min_edge <= model_probability - executable_ask <= max_edge, where min_edge is
    models.NFL.min_edge (+0.02 on a spread with |line| >= 8, or an over at a total of
    47+) and max_edge is project.maximum_unreviewed_market_disagreement.
    """
    config = load_config()
    return {
        "min_edge": float(config.get("models", {}).get("NFL", {}).get("min_edge", 0.05)),
        "max_edge": float(config.get("project", {}).get("maximum_unreviewed_market_disagreement", 0.10)),
        "heavy_spread_or_high_over_extra": 0.02,
        "ask": STANDARD_LAY_ASK,
        "source": "src/model_prediction/cli/forecast.py _forecast_nfl_{spread,total}_sport + "
        "cli/forecast_nfl_support.py _main_call_eligible; config/model.yaml",
        "not_modelled": "eligibility CALL/qualified (input freshness, registration); edge only",
    }


def clears_main(row: dict[str, Any], market: str, p_cover_or_over: float, rule: dict[str, Any]) -> bool:
    side_is_long = p_cover_or_over >= 0.5  # the slate takes the long side at exactly 50%
    side_p = p_cover_or_over if side_is_long else 1.0 - p_cover_or_over
    edge = side_p - rule["ask"]
    extra = 0.0
    if market == "spread" and abs(float(row["spread_home_line"])) >= 8.0:
        extra = rule["heavy_spread_or_high_over_extra"]
    if market == "total" and side_is_long and float(row["total_line"]) >= 47.0:
        extra = rule["heavy_spread_or_high_over_extra"]
    return rule["min_edge"] + extra <= edge <= rule["max_edge"]


def price_cohort() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The replay's cohort loop (scripts/replay_nfl_v7_all_settled.py main()), unchanged."""
    snapshot_dir = R.latest_snapshot_dir()
    store, _manifest = R.build_store(snapshot_dir)
    schedule = pl.read_parquet(snapshot_dir / "schedule.parquet")
    _sp_art, _tot_art, spread_model, total_model = R.load_models()
    if spread_model.probability_logit_slope != 1.0 or total_model.probability_logit_slope != 1.0:
        raise SystemExit("the v7 artifacts must price raw (no probability_logit_slope)")
    qbs = R.QBResolver(snapshot_dir)
    injuries = R.InjuryResolver()
    games = R.load_games()
    sign = R.spread_sign_convention(games, schedule)
    cohort = [
        g
        for g in games
        if g.get("status") == "completed"
        and int(g.get("season_year") or 0) >= 2021
        and g.get("home_score") is not None
        and g.get("away_score") is not None
        and g.get("spread_line") is not None
        and g.get("total_line") is not None
    ]
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for g in cohort:
        start = parse_utc(g["event_start_utc"])
        spread_home_line = sign * float(g["spread_line"])
        total_line = float(g["total_line"])
        is_dome = str(g.get("roof") or "") in ("dome", "closed")
        wind = None if is_dome or g.get("wind") is None else float(g["wind"])
        temp = None if is_dome or g.get("temp") is None else float(g["temp"])
        try:
            priced = R.price_game(
                store=store,
                spread_model=spread_model,
                total_model=total_model,
                qbs=qbs,
                injuries=injuries,
                home=g["home_team"],
                away=g["away_team"],
                start=start,
                decision_time=start - R.DECISION_LEAD,
                spread_home_line=spread_home_line,
                total_line=total_line,
                is_neutral=bool(g.get("is_neutral_site", False)),
                home_rest=int(g.get("home_rest_days") or 7),
                away_rest=int(g.get("away_rest_days") or 7),
                is_dome=is_dome,
                wind=wind,
                temp=temp,
            )
        except Exception as exc:  # noqa: BLE001 -- report every failure, as the replay does
            failures.append({"event_id": g["event_id"], "error": f"{type(exc).__name__}: {exc}"})
            continue
        margin = int(g["home_score"]) - int(g["away_score"])
        total = int(g["home_score"]) + int(g["away_score"])
        cover_val = margin + spread_home_line
        rows.append(
            {
                "event_id": str(g["event_id"]),
                "start": start.isoformat(),
                "date": start.astimezone(EASTERN).date().isoformat(),
                "season": int(g["season_year"]),
                "spread_home_line": spread_home_line,
                "total_line": total_line,
                "home_cover": None if cover_val == 0 else int(cover_val > 0),
                "over": None if total == total_line else int(total > total_line),
                "p_home_cover": priced["p_home_cover"],
                "p_over_a": priced["p_over_a"],
                "p_over_b": priced["p_over_b"],
            }
        )
    return rows, failures


def fit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    split_rows = sorted(
        (_SplitRow(date=r["date"], event_id=r["event_id"]) for r in rows), key=lambda r: r.date
    )
    train, validation, holdout, split_meta = chronological_split(split_rows)
    window_of = {r.event_id: "train" for r in train}
    window_of.update({r.event_id: "validation" for r in validation})
    window_of.update({r.event_id: "locked_holdout" for r in holdout})
    rule = main_rule()
    report: dict[str, Any] = {
        "split": split_meta,
        "main_rule": rule,
        "markets": {},
    }
    for market, (v7_id, v71_id, p_key, y_key) in MARKETS.items():
        decided = [r for r in rows if r[y_key] is not None]
        by_window: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in decided:
            by_window[window_of[r["event_id"]]].append(r)
        train_rows = by_window["train"]
        slope = fit_slope([r[p_key] for r in train_rows], [r[y_key] for r in train_rows])
        stored = round(slope["clipped"], SLOPE_DECIMALS)
        windows: dict[str, Any] = {}
        for name, sub in (
            ("train", by_window["train"]),
            ("validation", by_window["validation"]),
            ("locked_holdout", by_window["locked_holdout"]),
            ("all", decided),
        ):
            raw = [r[p_key] for r in sub]
            ys = [r[y_key] for r in sub]
            windows[name] = {
                "start": min(r["date"] for r in sub),
                "end": max(r["date"] for r in sub),
                "raw": metrics(raw, ys),
                "shrunk": metrics([_logit_shrink(p, stored) for p in raw], ys),
            }
        if market == "total":
            raw_a = [r["p_over_a"] for r in decided]
            ys = [r["over"] for r in decided]
            windows["all_variant_a_no_weather"] = {
                "raw": metrics(raw_a, ys),
                "shrunk": metrics([_logit_shrink(p, stored) for p in raw_a], ys),
            }
        seasons: dict[str, dict[str, int]] = {}
        for r in sorted(rows, key=lambda r: r["season"]):
            season = seasons.setdefault(str(r["season"]), {"games": 0, "main_raw": 0, "main_shrunk": 0})
            p = r[p_key]
            season["games"] += 1
            season["main_raw"] += int(clears_main(r, market, p, rule))
            season["main_shrunk"] += int(clears_main(r, market, _logit_shrink(p, stored), rule))
        report["markets"][market] = {
            "v7_model_version": v7_id,
            "v71_model_version": v71_id,
            "probability_key": p_key,
            "slope_fit_train": {
                "mle": slope["mle"],
                "standard_error": slope["se"],
                "clipped": slope["clipped"],
                "stored_probability_logit_slope": stored,
                "n_train": len(train_rows),
            },
            "served_probability_range_after_shrink": [
                round(_logit_shrink(0.35, stored), 6),
                round(_logit_shrink(0.65, stored), 6),
            ],
            "windows": windows,
            "main_rule_clears_by_season": seasons,
        }
    return report


def _fmt(value: Any, digits: int = 4) -> str:
    return "n/a" if value is None else f"{float(value):.{digits}f}"


def render_markdown(report: dict[str, Any]) -> str:
    split = report["split"]
    rule = report["main_rule"]
    lines = [
        "# NFL v7.1 probability shrink fit (quick-261123-2w2)",
        "",
        f"Generated {report['generated_at_utc']} by `scripts/fit_nfl_v71_shrink.py`.",
        "",
        (
            f"Cohort: {report['cohort']['games_priced']} games priced point-in-time by the v7 engines "
            f"({len(report['cohort']['failures'])} pricing failures), seasons {report['cohort']['seasons']}. "
            "Same loop and inputs as `scripts/replay_nfl_v7_all_settled.py`."
        ),
        "",
        (
            "Shrink: `p' = sigmoid(s * logit(p))`, intercept 0. s is fitted on train only (slope-only "
            "logistic MLE on logit(p)), clipped to [0, 1], rounded to 3 decimals; every number below "
            "uses the rounded value."
        ),
        "",
        "## Split (chronological_split, 60/20/20 on complete ET dates)",
        "",
        "| Window | Start | End | Games |",
        "|---|---|---|---|",
    ]
    for name in ("train", "validation", "locked_holdout"):
        meta = split[name]
        lines.append(f"| {name} | {meta['start']} | {meta['end']} | {meta['observations']} |")
    lines += [
        "",
        (
            "**Disclosure:** the locked holdout overlaps the 2025-09-28+ window that the 2026-11-12 "
            "replay already read (its 0.24 / 0.13 holdout calibration slopes motivated this change). "
            "The holdout numbers are design-informed, not a clean confirmation."
        ),
        "",
        (
            "The 11-12 replay split 80/20 by row count, which cut inside 2025-09-28; this split keeps "
            "whole dates, so its holdout holds a few more games and its calibration slopes differ from "
            "the 11-12 figures. Slopes on ~300 games whose probabilities sit within a few points of 50% "
            "are noisy."
        ),
        "",
    ]
    for market, block in report["markets"].items():
        fit_block = block["slope_fit_train"]
        lo, hi = block["served_probability_range_after_shrink"]
        lines += [
            f"## {market.title()} ({block['v71_model_version']}, fitted on `{block['probability_key']}`)",
            "",
            (
                f"Train MLE s = {fit_block['mle']:.4f} (SE {fit_block['standard_error']:.4f}, "
                f"n = {fit_block['n_train']}); stored `probability_logit_slope` = "
                f"**{fit_block['stored_probability_logit_slope']}**. "
                f"The engine's [35%, 65%] clamp maps to [{lo:.2%}, {hi:.2%}] after the shrink."
            ),
            "",
            (
                "| Window | n | Brier raw | Brier shrunk | Log loss raw | Log loss shrunk | "
                "Cal. slope raw | Cal. slope shrunk |"
            ),
            "|---|---|---|---|---|---|---|---|",
        ]
        if fit_block["stored_probability_logit_slope"] == 1.0:
            lines[-3:-3] = [
                "",
                (
                    "**No shrink.** The train-window MLE is above 1, so s clips to 1.0 and "
                    f"{block['v71_model_version']} prices identically to {block['v7_model_version']}. "
                    "The overconfidence shows only after the train window (calibration slope by window "
                    "below); fitting s on those windows would break the protocol."
                ),
            ]
        for name, w in block["windows"].items():
            raw, shr = w["raw"], w["shrunk"]
            lines.append(
                f"| {name} | {raw['n']} | {_fmt(raw['brier'])} | {_fmt(shr['brier'])} | "
                f"{_fmt(raw['log_loss'])} | {_fmt(shr['log_loss'])} | "
                f"{_fmt(raw['calibration_slope'], 3)} | {_fmt(shr['calibration_slope'], 3)} |"
            )
        lines += [
            "",
            (
                f"Games clearing the Main edge band at a {rule['ask']:.1%} ask "
                f"({rule['min_edge']:.0%} to {rule['max_edge']:.0%} edge, +2 points on |line| >= 8 "
                "spreads / overs at 47+; edge only, eligibility not modelled):"
            ),
            "",
            "| Season | Games | Main raw | Main shrunk |",
            "|---|---|---|---|",
        ]
        for season, counts in block["main_rule_clears_by_season"].items():
            lines.append(f"| {season} | {counts['games']} | {counts['main_raw']} | {counts['main_shrunk']} |")
        lines.append("")
    return "\n".join(lines) + "\n"


def write_artifacts(report: dict[str, Any], fit_sha256: str, force: bool = False) -> list[Path]:
    split = report["split"]
    written: list[Path] = []
    for market, block in report["markets"].items():
        v7_path = REPO / "config" / "models" / f"{block['v7_model_version']}.json"
        parent = json.loads(v7_path.read_text(encoding="utf-8"))
        if compute_artifact_hash(parent) != parent["artifact_hash"]:
            raise SystemExit(f"{v7_path} does not self-verify")
        slope = block["slope_fit_train"]["stored_probability_logit_slope"]
        hold = block["windows"]["locked_holdout"]
        artifact = {k: v for k, v in parent.items() if k != "artifact_hash"}
        artifact["model_version"] = block["v71_model_version"]
        artifact["probability_logit_slope"] = slope
        artifact["parent_model_version"] = parent["model_version"]
        artifact["parent_artifact_hash"] = parent["artifact_hash"]
        fit_block = block["slope_fit_train"]
        if slope == 1.0:
            artifact["note"] = (
                f"{parent['model_version']} engine unchanged and NO shrink: the train-window fit "
                f"(MLE {fit_block['mle']:.3f}, SE {fit_block['standard_error']:.3f}) clipped at 1.0, so "
                f"this artifact prices identically to {parent['model_version']}. Built for the "
                "operator-approved v7.1 shrink (2026-11-23, quick-261123-2w2); see "
                "outputs/nfl/nfl_v71_shrink.md for why the honest fit did not shrink."
            )
        else:
            artifact["note"] = (
                f"{parent['model_version']} engine unchanged; the final {market} probability is served "
                f"as sigmoid({slope} * logit(p)), intercept 0, so no side is favoured. Train-window fit "
                f"MLE {fit_block['mle']:.3f} (SE {fit_block['standard_error']:.3f}). Operator-approved "
                "2026-11-23 (quick-261123-2w2) after the point-in-time replay showed the v7 engines "
                "overconfident."
            )
        artifact["probability_shrink_provenance"] = {
            "fit_script": "scripts/fit_nfl_v71_shrink.py",
            "fit_output": "outputs/nfl/nfl_v71_shrink.json",
            "fit_output_sha256": fit_sha256,
            "fit_output_sha256_of": "LF-normalised file bytes (the committed git blob content)",
            "fitted_on": block["probability_key"],
            "split": "model_prediction.validation.chronological_split 60/20/20 on complete ET dates",
            "train": [split["train"]["start"], split["train"]["end"]],
            "validation": [split["validation"]["start"], split["validation"]["end"]],
            "locked_holdout": [split["locked_holdout"]["start"], split["locked_holdout"]["end"]],
            "train_mle": round(block["slope_fit_train"]["mle"], 6),
            "train_standard_error": round(block["slope_fit_train"]["standard_error"], 6),
            "holdout_design_informed": True,
        }
        qualification = dict(parent.get("qualification") or {})
        qualification["model_version"] = block["v71_model_version"]
        # The flag and the replay block are the parent's, earned by its unshrunk output; say so in
        # the artifact rather than let them read as v7.1's own qualification (review 2026-11-23).
        qualification["inherited_from"] = parent["model_version"]
        qualification["inherited_note"] = (
            "qualified and replay are inherited from the parent: the engine is unchanged and only "
            "the final probability is shrunk; replay figures describe the parent's unshrunk output"
        )
        qualification["probability_shrink_locked_holdout"] = {
            "n": hold["raw"]["n"],
            "start": hold["start"],
            "brier_raw": hold["raw"]["brier"],
            "brier_shrunk": hold["shrunk"]["brier"],
            "log_loss_raw": hold["raw"]["log_loss"],
            "log_loss_shrunk": hold["shrunk"]["log_loss"],
            "note": "design-informed: this window was read by the 2026-11-12 replay before the fit",
        }
        artifact["qualification"] = qualification
        artifact["artifact_hash"] = compute_artifact_hash(artifact)
        path = REPO / "config" / "models" / f"{block['v71_model_version']}.json"
        # config/models is protected once shipped (docs/AGENTS.md): a rerun on a grown cohort would
        # silently change what this id means, so refuse unless explicitly forced.
        if path.exists() and not force:
            raise SystemExit(f"{path} exists; use a new version id or pass --force")
        path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path)
    return written


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--data-root",
        type=Path,
        default=REPO / "data",
        help="read-only data root holding processed/nfl and availability/nfl (e.g. E:/MP/data)",
    )
    ap.add_argument("--write-artifacts", action="store_true", help="also write the two v7.1 artifacts")
    ap.add_argument("--force", action="store_true", help="overwrite existing v7.1 artifacts")
    args = ap.parse_args()
    if not (args.data_root / "processed" / "nfl" / "games.jsonl").is_file():
        raise SystemExit(f"no processed/nfl/games.jsonl under {args.data_root}")
    R.DATA = args.data_root  # the replay helpers resolve DATA at call time

    rows, failures = price_cohort()
    spread_rows = [r for r in rows if r["home_cover"] is not None]
    total_rows = [r for r in rows if r["over"] is not None]
    reproduction = {
        "spread_brier_all": calibration_metrics(
            [r["p_home_cover"] for r in spread_rows], [r["home_cover"] for r in spread_rows]
        ).get("brier_score"),
        "total_brier_all_variant_b": calibration_metrics(
            [r["p_over_b"] for r in total_rows], [r["over"] for r in total_rows]
        ).get("brier_score"),
        "replay_2026_11_12": {
            "spread_brier": 0.24962,
            "spread_n": 1451,
            "total_brier": 0.25060,
            "total_n": 1476,
        },
        "spread_n": len(spread_rows),
        "total_n": len(total_rows),
    }
    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "data_root": str(args.data_root),
        "cohort": {
            "games_priced": len(rows),
            "failures": failures,
            "seasons": sorted({r["season"] for r in rows}),
        },
        "reproduction": reproduction,
        **fit(rows),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    OUT_MD.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    # LF-normalised so the sha matches the committed blob whatever core.autocrlf leaves on disk.
    fit_sha256 = hashlib.sha256(OUT_JSON.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    print(render_markdown(report))
    print("reproduction:", json.dumps(reproduction))
    print("wrote", OUT_JSON, "sha256", fit_sha256)
    if args.write_artifacts:
        for path in write_artifacts(report, fit_sha256, force=args.force):
            print("wrote", path)


if __name__ == "__main__":
    main()
