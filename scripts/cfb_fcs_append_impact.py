"""Does appending FCS-vs-FCS games (ESPN groups=81 only) to the live NCAAF history hurt serving v5?

Quick 261115-x4q. Same machinery and thresholds as scripts/cfb_v5_history_impact.py, new pinned rule.
The rule's sha256 is written to outputs/ncaaf/cfb_fcs_append_rule.json by `pin` and committed BEFORE
`run` computes any metric.

  arm (i)   copy of the CURRENT live history (already holds the 1,478 FBS-involved rows)
  arm (ii)  arm (i) plus the FCS-vs-FCS set, written to both copies as a live apply would

Read-only on the live tree: all work is in a temp copy under data/research/ncaaf_v6/fcs_impact/.
Informational only (not decision inputs): reference-line market check, log loss.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "src"))

import cfb_v5_history_impact as imp

ROOT = REPO / "data" / "research" / "ncaaf_v6" / "fcs_impact"
RULE_PATH = REPO / "outputs" / "ncaaf" / "cfb_fcs_append_rule.json"
OUT_JSON = REPO / "outputs" / "ncaaf" / "cfb_fcs_append_impact.json"

RULE: dict[str, Any] = {
    "id": "cfb-fcs-append-rule-1",
    "cohort": {"same_as": "cfb-v5-history-impact-rule-1", "expected_events": 1527},
    "arms": {
        "i": "current live history copy (includes the 1,478 FBS-involved backfill rows)",
        "ii": "arm i plus completed non-forfeit backfill rows whose feed_groups == [81], absent from arm i",
    },
    "delta_definition": "arm_ii_minus_arm_i",
    "bootstrap": imp.RULE["bootstrap"],
    "accept_if": imp.RULE["accept_if"],
    "arm_i_reproduction": "recorded, not gated (history changed since the 0.18933 measurement); n must equal 1527",
    "informational_only": ["log_loss", "arm_i_vs_recorded_0.18933"],
    "verdicts": ["ACCEPT", "REJECT"],
    "refit_note": "no refit is performed; the report only states whether the evidence warrants one",
}
RULE_SHA256 = hashlib.sha256(json.dumps(RULE, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_fcs_set(live_paths: list[Path]) -> list[dict[str, Any]]:
    got = imp.file_sha256(imp.BACKFILL_PATH)
    if got != imp.BACKFILL_SHA256:
        raise ValueError(f"backfill sha256 {got} != pinned")
    present: set[str] = set()
    for p in live_paths:
        present |= imp._ids_in(Path(p))
    rows, seen = [], set()
    for line in imp.BACKFILL_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        eid = str(r["event_id"])
        if list(r.get("feed_groups") or []) != [81] or r.get("is_forfeit") or r.get("status") != "completed":
            continue
        if eid in present or eid in seen:
            continue
        seen.add(eid)
        rows.append(imp.to_live_row(r))
    rows.sort(key=lambda x: (x["event_start_utc"], str(x["event_id"])))
    return rows


def cmd_pin(_: argparse.Namespace) -> int:
    RULE_PATH.write_text(json.dumps({"rule": RULE, "rule_sha256": RULE_SHA256}, indent=2, sort_keys=True) + "\n")
    print(RULE_SHA256)
    return 0


def cmd_run(_: argparse.Namespace) -> int:
    from cfb_v5_research import clustered_bootstrap_mean_delta, gauss_log_score

    from model_prediction.models.cfb_structural_v5 import (
        CFBStructuralV5Model,
        load_cfb_structural_v5_artifact,
    )
    from model_prediction.models.college_football import _load_cfb_history
    from model_prediction.roadmap_challenger import _cluster_bootstrap_brier_delta

    if json.loads(RULE_PATH.read_text())["rule_sha256"] != RULE_SHA256:
        raise SystemExit("pinned rule file does not match the script rule")
    t0 = time.time()
    if ROOT.exists():
        shutil.rmtree(ROOT)
    arm_i, arm_ii = ROOT / "arm_i", ROOT / "arm_ii"
    live_sha = {
        "processed": imp.file_sha256(imp.LIVE_PROCESSED),
        "historical": imp.file_sha256(imp.LIVE_HISTORICAL),
    }
    copies = {
        "processed": imp._copy_complete_lines(imp.LIVE_PROCESSED, arm_i / "processed" / "ncaaf" / "games.jsonl"),
        "historical": imp._copy_complete_lines(imp.LIVE_HISTORICAL, arm_i / "historical" / "ncaaf_games_all.jsonl"),
    }
    rows_set = build_fcs_set(
        [arm_i / "processed" / "ncaaf" / "games.jsonl", arm_i / "historical" / "ncaaf_games_all.jsonl"]
    )
    blob = imp.serialize(rows_set).encode()
    for rel in (("processed", "ncaaf", "games.jsonl"), ("historical", "ncaaf_games_all.jsonl")):
        dst = arm_ii.joinpath(*rel)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(arm_i.joinpath(*rel).read_bytes() + blob)
    set_sha = imp.append_set_sha256(rows_set)
    print(f"fcs set {len(rows_set)} sha256 {set_sha}", flush=True)

    art = load_cfb_structural_v5_artifact()
    model = CFBStructuralV5Model.from_artifact(art)
    hold = imp._holdout_rows()
    hist_i, hist_ii = _load_cfb_history(arm_i), _load_cfb_history(arm_ii)
    print(f"history rows arm_i {len(hist_i)} arm_ii {len(hist_ii)}", flush=True)
    if len(hist_ii) != len(hist_i) + len(rows_set):
        raise SystemExit("arm ii is not arm i plus the set exactly once")
    rows_i = imp.replay_holdout(model, hist_i, hold, "arm_i")
    rows_ii = imp.replay_holdout(model, hist_ii, hold, "arm_ii")
    m_i, m_ii = imp.arm_metrics(rows_i), imp.arm_metrics(rows_ii)
    ducks = [SimpleNamespace(date=r["date"], outcome=int(r["margin"] > 0)) for r in rows_i]
    paired = [
        {
            "date": a["date"],
            "d_margin_ls": gauss_log_score(b["margin"], b["mu"], b["margin_sd"])
            - gauss_log_score(a["margin"], a["mu"], a["margin_sd"]),
            "d_total_ls": gauss_log_score(b["total"], b["tmu"], b["total_sd"])
            - gauss_log_score(a["total"], a["tmu"], a["total_sd"]),
        }
        for a, b in zip(rows_i, rows_ii, strict=True)
    ]
    seeds = RULE["bootstrap"]
    deltas = {
        "brier": _cluster_bootstrap_brier_delta(
            [r["p_home"] for r in rows_i],
            [r["p_home"] for r in rows_ii],
            ducks,
            seed=seeds["brier_seed"],
            n_resamples=seeds["resamples"],
        ),
        "margin_log_score": clustered_bootstrap_mean_delta(
            paired, "d_margin_ls", seed=seeds["log_score_seed"], n=seeds["resamples"]
        ),
        "total_log_score": clustered_bootstrap_mean_delta(
            paired, "d_total_ls", seed=seeds["log_score_seed"], n=seeds["resamples"]
        ),
    }
    acc = RULE["accept_if"]
    checks = {
        "brier": deltas["brier"]["point_estimate"] <= acc["brier_delta_at_most"],
        "margin_log_score": deltas["margin_log_score"]["point_estimate"] >= acc["margin_log_score_delta_at_least"],
        "total_log_score": deltas["total_log_score"]["point_estimate"] >= acc["total_log_score_delta_at_least"],
    }
    verdict = "ACCEPT" if all(checks.values()) and m_i["n"] == 1527 else "REJECT"
    report = {
        "as_of_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rule_sha256": RULE_SHA256,
        "live_sha256_at_measurement": live_sha,
        "live_copies": copies,
        "fcs_set": {
            "count": len(rows_set),
            "sha256": set_sha,
            "by_season": dict(sorted(Counter(str(r["season_year"]) for r in rows_set).items())),
        },
        "history_rows": {"arm_i": len(hist_i), "arm_ii": len(hist_ii)},
        "arm_i": m_i,
        "arm_ii": m_ii,
        "deltas_arm_ii_minus_arm_i": deltas,
        "accept_checks": checks,
        "holdout_events_with_changed_probability": sum(
            1 for a, b in zip(rows_i, rows_ii, strict=True) if abs(a["p_home"] - b["p_home"]) > 1e-9
        ),
        "max_abs_probability_change": max(abs(a["p_home"] - b["p_home"]) for a, b in zip(rows_i, rows_ii, strict=True)),
        "verdict": verdict,
        "runtime_seconds": round(time.time() - t0, 1),
    }
    OUT_JSON.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print("verdict", verdict, checks)
    print(json.dumps({"arm_i": m_i, "arm_ii": m_ii, "deltas": deltas}, indent=1, default=str))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pin").set_defaults(fn=cmd_pin)
    sub.add_parser("run").set_defaults(fn=cmd_run)
    args = ap.parse_args()
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
