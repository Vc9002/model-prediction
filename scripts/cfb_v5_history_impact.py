"""v5 history-impact check with a pinned acceptance rule (quick-261115-0pq, item B measure).

Question: does completing the live NCAAF history (FBS-involved games listed under ESPN groups=80 that
the live ingest never fetched) hurt the serving champion cfb-structural-v5?

The rule below is pinned as a constant and committed BEFORE the first run, so the verdict cannot be tuned
to the numbers. Two arms replay the v5 serving artifact point-in-time on the same locked-holdout event ids
and the same 575/576 reference lines:

  arm (i)   the live history as it stands (a copy, so a running daily cannot move it mid-measurement)
  arm (ii)  arm (i) plus the recorded append set, written to both files exactly as the live apply would

Read-only: nothing here writes under data/processed or data/historical (the script refuses), no order
endpoint is touched. The same `build_append_set` is imported by scripts/cfb_backfill_live_history.py so the
measured set and the applied set are one object, tied by `append_set_sha256`.

Usage (repo root, OPENBLAS_NUM_THREADS=1):
  .venv/Scripts/python scripts/cfb_v5_history_impact.py run
  .venv/Scripts/python scripts/cfb_v5_history_impact.py append-set     # print the set's count and sha256
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

BACKFILL_PATH = REPO / "data" / "research" / "ncaaf_v6" / "ncaaf_games_backfill.jsonl"
BACKFILL_SHA256 = "cc704059e4684edb9e580dff5033433424ee34e6a5fe96995c7c272ae82d8bf4"
LIVE_PROCESSED = REPO / "data" / "processed" / "ncaaf" / "games.jsonl"
LIVE_HISTORICAL = REPO / "data" / "historical" / "ncaaf_games_all.jsonl"
BASE_ROWS = REPO / "outputs" / "ncaaf" / "cfb_v5_base_rows.jsonl"
PINNED_PATH = REPO / "data" / "research" / "ncaaf_v6" / "market_reference_pinned.json"
IMPACT_ROOT = REPO / "data" / "research" / "ncaaf_v6" / "impact"
OUT_JSON = REPO / "outputs" / "ncaaf" / "cfb_v5_history_impact.json"
OUT_MD = REPO / "outputs" / "ncaaf" / "cfb_v5_history_impact.md"

# The live-row schema the ingest, arm (ii) and the apply script all share (20 keys).
LIVE_ROW_KEYS = (
    "away_score",
    "away_team",
    "away_team_id",
    "competition_type",
    "espn_season_type_id",
    "event_id",
    "event_start_utc",
    "feed_groups",
    "home_score",
    "home_team",
    "home_team_id",
    "is_neutral_site",
    "league",
    "parser_version",
    "raw_hash",
    "raw_source",
    "season_type",
    "season_year",
    "status",
    "week",
)

# --------------------------------------------------------------------------- the pinned rule
RULE: dict[str, Any] = {
    "id": "cfb-v5-history-impact-rule-1",
    "cohort": {
        "name": "v5 locked holdout",
        "source": "outputs/ncaaf/cfb_v5_base_rows.jsonl",
        "date_from": "2024-11-15",
        "date_to": "2026-11-12",
        "expected_events": 1527,
        "same_event_ids_in_both_arms": True,
    },
    "delta_definition": "arm_ii_minus_arm_i",
    "bootstrap": {
        "resamples": 2000,
        "cluster": "date",
        "brier_seed": 11,
        "log_score_seed": 7,
        "helpers": [
            "model_prediction.roadmap_challenger._cluster_bootstrap_brier_delta",
            "cfb_v5_research.clustered_bootstrap_mean_delta",
        ],
        "decision_statistic": "point_estimate",
    },
    "accept_if": {
        "brier_delta_at_most": 0.001,
        "margin_log_score_delta_at_least": -0.005,
        "total_log_score_delta_at_least": -0.005,
        "note": "positive Brier delta is worse; log score is the mean Gaussian log density, higher is better",
    },
    "informational_only": ["log_loss", "market_relative_corr_575_576"],
    "reproduction_gate_arm_i": {
        "n": 1527,
        "brier": {"want": 0.18933, "tol": 0.0005},
        "margin_log_score": {"want": -4.21265, "tol": 0.005},
        "total_log_score": {"want": -4.21155, "tol": 0.005},
        "reference_gate": "cfb_v6_market_check.gate_check passes on the 575/576 reference projections",
    },
    "verdicts": ["ACCEPT", "REJECT", "STOP_REPRODUCTION_FAILED"],
    "append_scope": {
        "feed_groups_contains": 80,
        "exclude_forfeits": True,
        "status": "completed",
        "existing_event_ids_win": True,
    },
}
RULE_SHA256 = hashlib.sha256(json.dumps(RULE, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

_FORBIDDEN_WRITE_ROOTS = (REPO / "data" / "processed", REPO / "data" / "historical")


# --------------------------------------------------------------------------- shared builder
def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ids_in(path: Path) -> set[str]:
    ids: set[str] = set()
    with path.open("rb") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                ids.add(str(json.loads(line).get("event_id")))
            except (ValueError, AttributeError):
                continue
    return ids


def to_live_row(r: dict[str, Any]) -> dict[str, Any]:
    out = {k: r[k] for k in LIVE_ROW_KEYS if k != "is_neutral_site"}
    out["is_neutral_site"] = r["neutral_site"]
    if set(out) != set(LIVE_ROW_KEYS):
        raise ValueError(f"live row key drift: {sorted(set(out) ^ set(LIVE_ROW_KEYS))}")
    return out


def build_append_set(backfill_path: Path, live_paths: list[Path] | tuple[Path, ...]) -> list[dict[str, Any]]:
    """FBS-involved (feed group 80), non-forfeit, completed backfill rows absent from every live file."""
    got = file_sha256(backfill_path)
    if got != BACKFILL_SHA256:
        raise ValueError(f"backfill sha256 {got} != pinned {BACKFILL_SHA256}")
    present: set[str] = set()
    for p in live_paths:
        present |= _ids_in(Path(p))
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    with backfill_path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            eid = str(r["event_id"])
            if 80 not in (r.get("feed_groups") or []):
                continue
            if r.get("is_forfeit") or r.get("status") != "completed":
                continue
            if eid in present or eid in seen:
                continue
            seen.add(eid)
            rows.append(to_live_row(r))
    rows.sort(key=lambda x: (x["event_start_utc"], str(x["event_id"])))
    return rows


def serialize(rows: list[dict[str, Any]]) -> str:
    """The ingest's exact line format."""
    return "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in rows)


def append_set_sha256(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(serialize(rows).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- arms
def _assert_writable(path: Path) -> None:
    p = path.resolve()
    for root in _FORBIDDEN_WRITE_ROOTS:
        if p == root.resolve() or root.resolve() in p.parents:
            raise SystemExit(f"refusing to write under {root}: {path}")


def _copy_complete_lines(src: Path, dst: Path) -> dict[str, Any]:
    """Copy src up to its last newline (a daily may be mid-append); record sha256 and rows."""
    _assert_writable(dst)
    data = src.read_bytes()
    cut = data.rfind(b"\n") + 1
    data = data[:cut]
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    return {
        "source": str(src.relative_to(REPO)).replace("\\", "/"),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "rows": sum(1 for line in data.splitlines() if line.strip()),
    }


def prepare_arms() -> dict[str, Any]:
    arm_i = IMPACT_ROOT / "arm_i"
    arm_ii = IMPACT_ROOT / "arm_ii"
    if IMPACT_ROOT.exists():
        _assert_writable(IMPACT_ROOT)
        shutil.rmtree(IMPACT_ROOT)
    copies = {
        "processed": _copy_complete_lines(LIVE_PROCESSED, arm_i / "processed" / "ncaaf" / "games.jsonl"),
        "historical": _copy_complete_lines(LIVE_HISTORICAL, arm_i / "historical" / "ncaaf_games_all.jsonl"),
    }
    live_copies = [
        arm_i / "processed" / "ncaaf" / "games.jsonl",
        arm_i / "historical" / "ncaaf_games_all.jsonl",
    ]
    append_rows = build_append_set(BACKFILL_PATH, live_copies)
    blob = serialize(append_rows).encode("utf-8")
    for rel in (("processed", "ncaaf", "games.jsonl"), ("historical", "ncaaf_games_all.jsonl")):
        src = arm_i.joinpath(*rel)
        dst = arm_ii.joinpath(*rel)
        _assert_writable(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes() + blob)
    return {
        "arm_i_root": arm_i,
        "arm_ii_root": arm_ii,
        "live_copies": copies,
        "append_rows": append_rows,
    }


# --------------------------------------------------------------------------- replay
def _holdout_rows() -> list[dict[str, Any]]:
    cohort = RULE["cohort"]
    rows = [json.loads(line) for line in BASE_ROWS.read_text(encoding="utf-8").splitlines() if line.strip()]
    hold = [r for r in rows if cohort["date_from"] <= r["date"] <= cohort["date_to"]]
    hold.sort(key=lambda r: (r["start_utc"], r["event_id"]))
    if len(hold) != cohort["expected_events"]:
        raise SystemExit(f"holdout cohort is {len(hold)} events, rule pins {cohort['expected_events']}")
    return hold


def replay_holdout(model: Any, history: list[Any], hold: list[dict[str, Any]], label: str) -> list[dict[str, Any]]:
    from model_prediction.models.college_football import UpcomingCFBGame

    starts = [h.start for h in history]
    by_id = {str(h.event_id): h for h in history}
    out: list[dict[str, Any]] = []
    t0 = time.time()
    for i, r in enumerate(hold):
        g = by_id[r["event_id"]]
        past = history[: bisect.bisect_left(starts, g.start)]
        up = UpcomingCFBGame(
            event_id=r["event_id"],
            event_start_utc=g.event_start_utc,
            away_team=g.away_team,
            home_team=g.home_team,
            is_neutral_site=bool(r["neutral"]),
            season_year=r["season"],
            week=r["week"],
        )
        preds = model.predict_matchup(past, up, project_missing_lines=True)
        ml = preds[0]
        ii = ml.inference_inputs
        row = dict(r)
        row["p_home"] = float(ml.probabilities["home"])
        row["mu"] = float(ii["mu_home"]) - float(ii["mu_away"])
        row["tmu"] = float(ii["mu_home"]) + float(ii["mu_away"])
        row["margin_sd"] = float(ii["parameters"]["margin_sd"])
        row["total_sd"] = float(ii["parameters"]["total_sd"])
        row["past_n"] = len(past)
        out.append(row)
        if (i + 1) % 500 == 0:
            print(f"  {label} holdout {i + 1}/{len(hold)} ({time.time() - t0:.0f}s)", flush=True)
    return out


def reference_values(
    model: Any, art: dict[str, Any], history: list[Any], season_first: dict[Any, Any], pinned: dict[str, Any]
) -> dict[tuple[str, str], dict[str, Any]]:
    import cfb_v6_market_check as mc

    replay = mc._replay_module()
    starts = [h.start for h in history]
    cache: dict[Any, Any] = {}
    values: dict[tuple[str, str], dict[str, Any]] = {}
    for eid, ev in pinned["events"].items():
        for market in ev["decision_utc"]:
            proj = mc.v5_raw_projection(model, replay, history, starts, season_first, ev, market, cache)
            values[(eid, market)] = {
                "value": mc.v5_calibrated_values(art, proj, market),
                "sg": proj["sg"],
                "fcs": proj["fcs"],
                "pm": proj["pm"],
                "pt": proj["pt"],
            }
    return values


def market_block(pinned: dict[str, Any], values: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    import cfb_v6_market_check as mc

    stats: dict[str, Any] = {}
    for market in ("total", "spread"):
        mv = {k: v for k, v in values.items() if k[1] == market}
        rows = mc.build_market_rows(pinned, mv, market)
        stats[market] = mc.market_stats(rows, market, "model")
    return stats


def arm_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from cfb_v5_research import score_rows

    return score_rows(rows, p_key="p_home", mu_key="mu", sd_key="margin_sd", tmu_key="tmu", tsd_key="total_sd")


# --------------------------------------------------------------------------- verdict
def decide(metrics_i: dict[str, Any], deltas: dict[str, Any], gate_ok: bool) -> tuple[str, dict[str, Any]]:
    gate = RULE["reproduction_gate_arm_i"]
    repro = {
        "n": metrics_i["n"] == gate["n"],
        "brier": abs(metrics_i["brier"] - gate["brier"]["want"]) <= gate["brier"]["tol"],
        "margin_log_score": abs(metrics_i["margin_log_score"] - gate["margin_log_score"]["want"])
        <= gate["margin_log_score"]["tol"],
        "total_log_score": abs(metrics_i["total_log_score"] - gate["total_log_score"]["want"])
        <= gate["total_log_score"]["tol"],
        "reference_gate": bool(gate_ok),
    }
    repro["all"] = all(repro.values())
    acc = RULE["accept_if"]
    checks = {
        "brier": deltas["brier"]["point_estimate"] <= acc["brier_delta_at_most"],
        "margin_log_score": deltas["margin_log_score"]["point_estimate"] >= acc["margin_log_score_delta_at_least"],
        "total_log_score": deltas["total_log_score"]["point_estimate"] >= acc["total_log_score_delta_at_least"],
    }
    checks["all"] = all(checks.values())
    if not repro["all"]:
        return "STOP_REPRODUCTION_FAILED", {"reproduction": repro, "accept_checks": checks}
    return ("ACCEPT" if checks["all"] else "REJECT"), {"reproduction": repro, "accept_checks": checks}


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=False).stdout.strip()


def cmd_append_set(_: argparse.Namespace) -> int:
    rows = build_append_set(BACKFILL_PATH, [LIVE_PROCESSED, LIVE_HISTORICAL])
    print(len(rows), append_set_sha256(rows))
    return 0


def cmd_run(_: argparse.Namespace) -> int:
    import cfb_v6_market_check as mc
    from cfb_v5_research import clustered_bootstrap_mean_delta, gauss_log_score

    from model_prediction.models.cfb_structural_v5 import (
        CFBStructuralV5Model,
        load_cfb_structural_v5_artifact,
    )
    from model_prediction.models.college_football import _load_cfb_history
    from model_prediction.roadmap_challenger import _cluster_bootstrap_brier_delta

    _assert_writable(OUT_JSON)
    _assert_writable(OUT_MD)
    t_start = time.time()
    as_of = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    prep = prepare_arms()
    append_rows = prep["append_rows"]
    set_sha = append_set_sha256(append_rows)
    by_class = Counter()
    by_season = Counter()
    for r in append_rows:
        by_season[str(r["season_year"])] += 1
    with BACKFILL_PATH.open("r", encoding="utf-8") as f:
        class_of = {json.loads(line)["event_id"]: json.loads(line)["game_class"] for line in f if line.strip()}
    for r in append_rows:
        by_class[class_of[r["event_id"]]] += 1
    print(f"append set {len(append_rows)} rows sha256 {set_sha}", dict(by_class), flush=True)

    art = load_cfb_structural_v5_artifact()
    model = CFBStructuralV5Model.from_artifact(art)
    hold = _holdout_rows()
    pinned = {"events": json.loads(PINNED_PATH.read_text(encoding="utf-8"))["events"]}

    hist_i = _load_cfb_history(prep["arm_i_root"])
    hist_ii = _load_cfb_history(prep["arm_ii_root"])
    print(f"history rows arm_i {len(hist_i)} arm_ii {len(hist_ii)}", flush=True)
    if len(hist_ii) != len(hist_i) + len(append_rows):
        raise SystemExit("arm (ii) does not hold arm (i) plus the append set exactly once")

    replay = mc._replay_module()
    season_first = mc.derive_weeks(replay, hist_i)

    rows_i = replay_holdout(model, hist_i, hold, "arm_i")
    vals_i = reference_values(model, art, hist_i, season_first, pinned)
    rows_ii = replay_holdout(model, hist_ii, hold, "arm_ii")
    vals_ii = reference_values(model, art, hist_ii, season_first, pinned)

    m_i, m_ii = arm_metrics(rows_i), arm_metrics(rows_ii)
    ducks = [SimpleNamespace(date=r["date"], outcome=int(r["margin"] > 0)) for r in rows_i]
    paired = []
    for a, b in zip(rows_i, rows_ii, strict=True):
        if a["event_id"] != b["event_id"]:
            raise SystemExit("holdout rows not aligned between arms")
        paired.append(
            {
                "date": a["date"],
                "d_margin_ls": gauss_log_score(b["margin"], b["mu"], b["margin_sd"])
                - gauss_log_score(a["margin"], a["mu"], a["margin_sd"]),
                "d_total_ls": gauss_log_score(b["total"], b["tmu"], b["total_sd"])
                - gauss_log_score(a["total"], a["tmu"], a["total_sd"]),
            }
        )
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
    changed = sum(1 for a, b in zip(rows_i, rows_ii, strict=True) if abs(a["p_home"] - b["p_home"]) > 1e-9)
    deltas["holdout_events_with_changed_probability"] = changed
    deltas["max_abs_probability_change"] = max(abs(a["p_home"] - b["p_home"]) for a, b in zip(rows_i, rows_ii, strict=True))
    deltas["log_loss_delta_informational"] = (
        None if m_i.get("log_loss") is None or m_ii.get("log_loss") is None else round(m_ii["log_loss"] - m_i["log_loss"], 6)
    )

    mk_i, mk_ii = market_block(pinned, vals_i), market_block(pinned, vals_ii)
    gate_ok, gate_lines = mc.gate_check(mk_i)
    verdict, decision = decide(m_i, deltas, gate_ok)

    pm_changed = sum(
        1
        for k in vals_i
        if abs(vals_i[k]["pm"] - vals_ii[k]["pm"]) > 1e-9 or abs(vals_i[k]["pt"] - vals_ii[k]["pt"]) > 1e-9
    )
    report: dict[str, Any] = {
        "as_of_utc": as_of,
        "script": {
            "path": "scripts/cfb_v5_history_impact.py",
            "git_blob_sha": _git("hash-object", "scripts/cfb_v5_history_impact.py"),
            "last_commit": _git("log", "-1", "--format=%H", "--", "scripts/cfb_v5_history_impact.py"),
            "head_at_run": _git("rev-parse", "HEAD"),
        },
        "rule": RULE,
        "rule_sha256": RULE_SHA256,
        "backfill_sha256": BACKFILL_SHA256,
        "live_copies_measured": prep["live_copies"],
        "append_set": {
            "count": len(append_rows),
            "append_set_sha256": set_sha,
            "by_game_class": dict(by_class),
            "by_season": dict(sorted(by_season.items())),
        },
        "history_rows": {"arm_i": len(hist_i), "arm_ii": len(hist_ii)},
        "arm_i": {"holdout": m_i, "market": mk_i},
        "arm_ii": {"holdout": m_ii, "market": mk_ii},
        "deltas_arm_ii_minus_arm_i": deltas,
        "reference_projection_pairs_changed": {"changed": pm_changed, "of": len(vals_i)},
        "arm_i_reference_gate": {"ok": bool(gate_ok), "lines": gate_lines},
        "decision_detail": decision,
        "verdict": verdict,
        "append_set_sha256": set_sha,
        "rule_sha256_repeat": RULE_SHA256,
        "runtime_seconds": round(time.time() - t_start, 1),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(report), encoding="utf-8")
    print("verdict", verdict, decision)
    print("\n".join(gate_lines))
    print(json.dumps({k: deltas[k] for k in ("brier", "margin_log_score", "total_log_score")}, indent=1))
    return 0


def _fmt(x: Any, nd: int = 5) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def render_md(rep: dict[str, Any]) -> str:
    i, ii = rep["arm_i"]["holdout"], rep["arm_ii"]["holdout"]
    d = rep["deltas_arm_ii_minus_arm_i"]
    a = rep["append_set"]
    dec = rep["decision_detail"]
    lines = [
        "# v5 history-impact check (quick-261115-0pq)",
        "",
        f"Verdict: **{rep['verdict']}** (rule `{RULE['id']}`, sha256 `{rep['rule_sha256']}`; pinned and committed before the run).",
        "",
        f"As of {rep['as_of_utc']}. Append set: {a['count']} FBS-involved completed non-forfeit events, sha256 `{a['append_set_sha256']}`.",
        f"By class {a['by_game_class']}; by season {a['by_season']}.",
        f"History rows: arm (i) {rep['history_rows']['arm_i']}, arm (ii) {rep['history_rows']['arm_ii']}.",
        "",
        "## Locked holdout (1,527 events, same ids both arms)",
        "",
        "| metric | arm (i) live | arm (ii) live + append | delta (ii - i) | 95% CI |",
        "|---|---|---|---|---|",
        f"| Brier (lower better) | {_fmt(i['brier'])} | {_fmt(ii['brier'])} | {d['brier']['point_estimate']:+.6f} | [{d['brier']['ci_95_low']:+.6f}, {d['brier']['ci_95_high']:+.6f}] |",
        f"| margin log score (higher better) | {_fmt(i['margin_log_score'])} | {_fmt(ii['margin_log_score'])} | {d['margin_log_score']['point_estimate']:+.6f} | [{d['margin_log_score']['ci_95_low']:+.6f}, {d['margin_log_score']['ci_95_high']:+.6f}] |",
        f"| total log score (higher better) | {_fmt(i['total_log_score'])} | {_fmt(ii['total_log_score'])} | {d['total_log_score']['point_estimate']:+.6f} | [{d['total_log_score']['ci_95_low']:+.6f}, {d['total_log_score']['ci_95_high']:+.6f}] |",
        f"| log loss (informational) | {_fmt(i.get('log_loss'), 6)} | {_fmt(ii.get('log_loss'), 6)} | {_fmt(d['log_loss_delta_informational'], 6)} | |",
        "",
        (
            f"Holdout events whose home-win probability changed: {d['holdout_events_with_changed_probability']} "
            f"of {i['n']} (max abs change {d['max_abs_probability_change']:.5f})."
        ),
        "",
        "## Accept rule",
        "",
        (
            f"Accept if Brier delta <= {RULE['accept_if']['brier_delta_at_most']:+} and each log-score delta >= "
            f"{RULE['accept_if']['margin_log_score_delta_at_least']:+}. Checks: {dec['accept_checks']}. "
            f"Arm (i) reproduction: {dec['reproduction']}."
        ),
        "",
        "## 575/576 reference lines (informational)",
        "",
        "| market | arm | n | corr | 95% CI | side W-L | MAE model | SD model |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for market in ("total", "spread"):
        for label, key in (("(i)", "arm_i"), ("(ii)", "arm_ii")):
            s = rep[key]["market"][market]
            ci = s["corr_ci"]
            lines.append(
                f"| {market} | {label} | {s['n']} | {s['corr']:+.4f} | [{ci['ci_95_low']:+.3f}, {ci['ci_95_high']:+.3f}] | "
                f"{s['side_wins']}-{s['side_losses']} | {s['mae_model']:.3f} | {s['sd_model']:.3f} |"
            )
    lines += [
        "",
        (
            "Reference projection pairs changed by the append: "
            f"{rep['reference_projection_pairs_changed']['changed']} of {rep['reference_projection_pairs_changed']['of']}."
        ),
        "",
        "Arm (i) reference gate:",
        "",
        "```",
        *rep["arm_i_reference_gate"]["lines"],
        "```",
        "",
        "Percentages and points only. No promotion; v5 parameters and gates unchanged.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run").set_defaults(fn=cmd_run)
    sub.add_parser("append-set").set_defaults(fn=cmd_append_set)
    args = ap.parse_args()
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())
