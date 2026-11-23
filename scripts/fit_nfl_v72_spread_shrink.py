"""nfl-spread-structural-v7.2: v7 with the cover probability shrunk, the slope fitted on the VALIDATION window.

Operator-approved protocol exception (2026-11-23, quick-261123-6q6). The train-window fit in
scripts/fit_nfl_v71_shrink.py clipped at 1.0 (MLE 1.11, SE 0.67), so v7.1 spread priced exactly as v7. The
overconfidence appears only after the train window (calibration slope 1.07 train, 0.48 validation, 0.40
holdout), so the operator chose to fit s on validation (2024-09-29 to 2025-09-25) and report the locked
holdout once. The holdout was already read by the 2026-11-12 replay and by the v7.1 fit: design-informed.

Same cohort, pricing, split and Main rule as fit_nfl_v71_shrink.py (imported, not copied).

  python scripts/fit_nfl_v72_spread_shrink.py --data-root E:/MP/data [--write-artifact]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "src"))

import fit_nfl_v71_shrink as F

from model_prediction.models.nfl_spread_v7 import _logit_shrink
from model_prediction.production_registry import compute_artifact_hash
from model_prediction.validation import chronological_split

PARENT_ID = "nfl-spread-structural-v7"
MODEL_ID = "nfl-spread-structural-v7.2"
OUT_JSON = REPO / "outputs" / "nfl" / "nfl_v72_spread_shrink.json"
OUT_MD = REPO / "outputs" / "nfl" / "nfl_v72_spread_shrink.md"
P_KEY, Y_KEY = "p_home_cover", "home_cover"


def fit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    split_rows = sorted(
        (F._SplitRow(date=r["date"], event_id=r["event_id"]) for r in rows), key=lambda r: r.date
    )
    train, validation, holdout, split_meta = chronological_split(split_rows)
    window_of = {r.event_id: "train" for r in train}
    window_of.update({r.event_id: "validation" for r in validation})
    window_of.update({r.event_id: "locked_holdout" for r in holdout})
    decided = [r for r in rows if r[Y_KEY] is not None]
    by_window: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in decided:
        by_window[window_of[r["event_id"]]].append(r)
    val = by_window["validation"]
    slope = F.fit_slope([r[P_KEY] for r in val], [r[Y_KEY] for r in val])
    stored = round(slope["clipped"], F.SLOPE_DECIMALS)
    windows: dict[str, Any] = {}
    for name in ("train", "validation", "locked_holdout"):
        sub = by_window[name]
        raw = [r[P_KEY] for r in sub]
        ys = [r[Y_KEY] for r in sub]
        windows[name] = {
            "start": min(r["date"] for r in sub),
            "end": max(r["date"] for r in sub),
            "raw": F.metrics(raw, ys),
            "shrunk": F.metrics([_logit_shrink(p, stored) for p in raw], ys),
        }
    rule = F.main_rule()
    seasons: dict[str, dict[str, int]] = {}
    for r in sorted(rows, key=lambda r: r["season"]):
        season = seasons.setdefault(str(r["season"]), {"games": 0, "main_raw": 0, "main_shrunk": 0})
        season["games"] += 1
        season["main_raw"] += int(F.clears_main(r, "spread", r[P_KEY], rule))
        season["main_shrunk"] += int(F.clears_main(r, "spread", _logit_shrink(r[P_KEY], stored), rule))
    return {
        "split": split_meta,
        "main_rule": rule,
        "fit_window": "validation (operator-approved protocol exception, 2026-11-23)",
        "slope_fit_validation": {
            "mle": slope["mle"],
            "standard_error": slope["se"],
            "clipped": slope["clipped"],
            "stored_probability_logit_slope": stored,
            "n_validation": len(val),
        },
        "windows": windows,
        "main_rule_clears_by_season": seasons,
    }


def render_markdown(report: dict[str, Any]) -> str:
    s = report["slope_fit_validation"]
    lines = [
        "# nfl-spread-structural-v7.2: cover probability shrink fitted on validation",
        "",
        (
            "Operator-approved protocol exception (2026-11-23): the train-window fit clipped at 1.0 "
            "(scripts/fit_nfl_v71_shrink.py), so the slope is fitted on the validation window and the locked "
            "holdout is reported once. The holdout was read before (2026-11-12 replay, v7.1 fit): design-informed."
        ),
        "",
        (
            f"Fitted s = {s['stored_probability_logit_slope']} (MLE {s['mle']:.4f}, "
            f"SE {s['standard_error']:.4f}, n {s['n_validation']})."
        ),
        "",
        "| window | dates | n | Brier raw -> shrunk | log loss raw -> shrunk | calibration slope raw -> shrunk |",
        "|---|---|---|---|---|---|",
    ]
    for name, w in report["windows"].items():
        raw, sh = w["raw"], w["shrunk"]
        lines.append(
            f"| {name} | {w['start']} to {w['end']} | {raw['n']} | {F._fmt(raw['brier'])} -> {F._fmt(sh['brier'])} "
            f"| {F._fmt(raw['log_loss'])} -> {F._fmt(sh['log_loss'])} "
            f"| {F._fmt(raw['calibration_slope'], 3)} -> {F._fmt(sh['calibration_slope'], 3)} |"
        )
    lines += ["", "Main-rule clears per season (raw -> shrunk):", ""]
    for season, c in report["main_rule_clears_by_season"].items():
        lines.append(f"- {season}: {c['main_raw']} -> {c['main_shrunk']} of {c['games']} games")
    lines.append("")
    return "\n".join(lines)


def write_artifact(report: dict[str, Any], fit_sha256: str, force: bool) -> Path:
    path = REPO / "config" / "models" / f"{MODEL_ID}.json"
    # config/models is protected once shipped (docs/AGENTS.md); a rerun must not silently redefine the id.
    if path.exists() and not force:
        raise SystemExit(f"{path} exists; use a new version id or pass --force")
    parent = json.loads((REPO / "config" / "models" / f"{PARENT_ID}.json").read_text(encoding="utf-8"))
    if compute_artifact_hash(parent) != parent["artifact_hash"]:
        raise SystemExit(f"{PARENT_ID} artifact does not self-verify")
    s = report["slope_fit_validation"]
    split = report["split"]
    hold = report["windows"]["locked_holdout"]
    artifact = {k: v for k, v in parent.items() if k != "artifact_hash"}
    artifact["model_version"] = MODEL_ID
    artifact["probability_logit_slope"] = s["stored_probability_logit_slope"]
    artifact["parent_model_version"] = PARENT_ID
    artifact["parent_artifact_hash"] = parent["artifact_hash"]
    artifact["note"] = (
        f"{PARENT_ID} engine unchanged; the cover probability is served as "
        f"sigmoid({s['stored_probability_logit_slope']} * logit(p)), intercept 0. The slope is fitted on the "
        "VALIDATION window by operator-approved protocol exception (2026-11-23, quick-261123-6q6) because the "
        "train-window fit clipped at 1.0 (v7.1). See outputs/nfl/nfl_v72_spread_shrink.md."
    )
    artifact["probability_shrink_provenance"] = {
        "fit_script": "scripts/fit_nfl_v72_spread_shrink.py",
        "fit_output": "outputs/nfl/nfl_v72_spread_shrink.json",
        "fit_output_sha256": fit_sha256,
        "fit_output_sha256_of": "LF-normalised file bytes (the committed git blob content)",
        "fitted_on": P_KEY,
        "fit_window": "validation",
        "protocol_exception": "operator-approved 2026-11-23: fit on validation, not train",
        "split": "model_prediction.validation.chronological_split 60/20/20 on complete ET dates",
        "train": [split["train"]["start"], split["train"]["end"]],
        "validation": [split["validation"]["start"], split["validation"]["end"]],
        "locked_holdout": [split["locked_holdout"]["start"], split["locked_holdout"]["end"]],
        "validation_mle": round(s["mle"], 6),
        "validation_standard_error": round(s["standard_error"], 6),
        "holdout_design_informed": True,
    }
    qualification = dict(parent.get("qualification") or {})
    qualification["model_version"] = MODEL_ID
    qualification["inherited_from"] = PARENT_ID
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
        "note": "design-informed: this window was read before the fit",
    }
    artifact["qualification"] = qualification
    artifact["artifact_hash"] = compute_artifact_hash(artifact)
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, default=REPO / "data")
    ap.add_argument("--write-artifact", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if not (args.data_root / "processed" / "nfl" / "games.jsonl").is_file():
        raise SystemExit(f"no processed/nfl/games.jsonl under {args.data_root}")
    F.R.DATA = args.data_root  # the replay helpers resolve DATA at call time
    rows, failures = F.price_cohort()
    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "data_root": str(args.data_root),
        "cohort": {"games_priced": len(rows), "failures": failures},
        **fit(rows),
    }
    OUT_JSON.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    OUT_MD.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    fit_sha256 = hashlib.sha256(OUT_JSON.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    print(render_markdown(report))
    print("wrote", OUT_JSON, "sha256", fit_sha256)
    if args.write_artifact:
        print("wrote", write_artifact(report, fit_sha256, args.force))


if __name__ == "__main__":
    main()
