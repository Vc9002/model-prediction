"""Read-only, fail-closed esports audit and retrospective model development.

Historical result END timestamps proxy availability; source collection timing is
not verified. Diagnostics do not establish production superiority or profits.
"""

from __future__ import annotations

import gzip
import hashlib
import heapq
import json
import math
import os
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

TITLES = ("cs2", "lol", "dota2", "valorant", "rainbow_six")
FEATURES = ("elo_logit", "recent_result_residual_gap", "log_history_count_gap", "minimum_log_history_count")
MINIMUM_BRIER_GAIN = 0.002


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def write_artifact(path: Path, value: dict) -> None:
    """Write new evidence only; never replace an existing research artifact."""
    payload = dict(value)
    payload["artifact_hash"] = digest(value)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    expected = loaded.pop("artifact_hash")
    if digest(loaded) != expected:
        raise ValueError("artifact_hash_verification_failed")


def timestamp(value: str) -> float:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("naive_timestamp")
    return parsed.timestamp()


def utc_date(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("naive_timestamp")
    return parsed.astimezone(UTC).date().isoformat()


def normalized_line(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        line = Decimal(str(value).strip())
        if not line.is_finite():
            raise ValueError("invalid_line")
        return format(line.normalize(), "f") if line else "0"
    except InvalidOperation as error:
        raise ValueError("invalid_line") from error


def chronological_partition(rows, boundary: str, next_date: str):
    cutoff = timestamp(boundary + "T00:00:00Z")
    train = np.array(
        [
            i
            for i, r in enumerate(rows)
            if timestamp(r["start_utc"]) < cutoff and timestamp(r["end_utc"]) < cutoff
        ],
        dtype=int,
    )
    validate = np.array(
        [i for i, r in enumerate(rows) if boundary <= utc_date(r["start_utc"]) < next_date], dtype=int
    )
    if set(train) & set(validate):
        raise ValueError("train_validation_overlap")
    return train, validate


def canonical_cohort(rows: list[dict]) -> tuple[list[dict], dict]:
    """Use Flat as canonical; latest within chosen tier, never count copies."""
    grouped = defaultdict(list)
    for row in rows:
        key = (
            str(row.get("sport", "")).strip().lower(),
            str(row.get("event_id", "")).strip(),
            str(row.get("market_type", "")).strip().lower(),
            str(row.get("selection", "")).strip().lower(),
            normalized_line(row.get("line")),
            str(row.get("model_id", "")).strip(),
        )
        grouped[key].append(row)
    rank = {"flat": 0, "main": 1, "research": 2}
    cohort = []
    for group in grouped.values():
        tier = min(rank.get(str(r.get("ledger_tier")).strip().lower(), 3) for r in group)
        eligible = [r for r in group if rank.get(str(r.get("ledger_tier")).strip().lower(), 3) == tier]
        cohort.append(
            max(eligible, key=lambda r: (timestamp(r["created_at_utc"]), str(r.get("pick_id", ""))))
        )
    return sorted(cohort, key=lambda r: (str(r.get("event_start_utc")), str(r.get("pick_id")))), {
        "input_records": len(rows),
        "unique_pick_keys": len(cohort),
        "duplicates_removed": len(rows) - len(cohort),
        "tier_counts_input": dict(Counter(r.get("ledger_tier") for r in rows)),
        "tier_counts_canonical": dict(Counter(r.get("ledger_tier") for r in cohort)),
        "keys_across_tiers": sum(len({r.get("ledger_tier") for r in g}) > 1 for g in grouped.values()),
        "settlement_conflicts": sum(len({r.get("result") for r in g}) > 1 for g in grouped.values()),
    }


def audit_history(raw: list[dict]) -> tuple[list[dict], dict]:
    groups = defaultdict(list)
    excluded = Counter()
    for row in raw:
        if not row.get("match_id"):
            excluded["missing_match_id"] += 1
        else:
            groups[row["match_id"]].append(row)
    valid = []
    duplicates = 0
    for group in groups.values():
        if len({digest(row) for row in group}) != 1:
            excluded["conflicting_match_id"] += len(group)
            continue
        duplicates += len(group) - 1
        row = group[0]
        reason = None
        if not row.get("end_utc"):
            reason = "missing_end"
        elif not row.get("start_utc"):
            reason = "missing_start"
        elif not row.get("team1_id") or not row.get("team2_id") or row["team1_id"] == row["team2_id"]:
            reason = "ambiguous_team_identity"
        elif row.get("winner_id") not in (row["team1_id"], row["team2_id"]):
            reason = "unresolved_or_draw_outcome"
        else:
            try:
                if timestamp(row["end_utc"]) <= timestamp(row["start_utc"]):
                    reason = "invalid_end_order"
            except (ValueError, TypeError):
                reason = "invalid_timestamp"
        if reason:
            excluded[reason] += 1
        else:
            valid.append(row)
    valid.sort(key=lambda row: (timestamp(row["start_utc"]), str(row["match_id"])))
    return valid, {
        "raw_records": len(raw),
        "eligible_unique_events": len(valid),
        "exact_duplicates_removed": duplicates,
        "excluded": dict(excluded),
        "metadata_best_of_counts": dict(Counter(str(r.get("best_of")) for r in raw)),
        "metadata_tier_counts": dict(Counter(str(r.get("tier")) for r in raw)),
        "source_match_identity_present": sum(bool(r.get("source_match_id")) for r in raw),
        "source_url_present": sum(bool(r.get("source_url")) for r in raw),
        "source_observed_at_present": sum(
            bool(r.get("observed_at_utc") or r.get("collected_at_utc")) for r in raw
        ),
        "map_score_pairs_present": sum(
            r.get("team1_score") is not None and r.get("team2_score") is not None for r in raw
        ),
        "pre_event_observation_timestamp_verified": False,
        "result_availability_policy": "end_utc strictly before decision; provider END proxy, collection/publication delay unverified",
        "contextual_feature_policy": "tier and best_of excluded: metadata observation time unverified; audit counts only",
    }


def chronological_features(rows: list[dict], k: float, half_life: float | None) -> np.ndarray:
    """Generate features without exposing pending results, even equal timestamps."""
    rating: dict[str, float] = defaultdict(float)
    last: dict[str, float] = {}
    form: dict[str, list[float]] = defaultdict(list)
    counts: Counter = Counter()
    pending: list[tuple] = []
    features = []

    def strength(team: str, now: float) -> float:
        decay = (
            1.0 if half_life is None or team not in last else 2 ** (-(now - last[team]) / (86400 * half_life))
        )
        return rating[team] * decay

    def probability(a: float, b: float) -> float:
        return 1 / (1 + math.exp(-max(-30, min(30, (a - b) * math.log(10) / 400))))

    for index, row in enumerate(rows):
        now = timestamp(row["start_utc"])
        while pending and pending[0][0] < now:
            end, _, event = heapq.heappop(pending)
            a, b = event["team1_id"], event["team2_id"]
            sa, sb = strength(a, end), strength(b, end)
            p = probability(sa, sb)
            y = float(event["winner_id"] == a)
            rating[a], rating[b] = sa + k * (y - p), sb - k * (y - p)
            last[a] = last[b] = end
            counts[a] += 1
            counts[b] += 1
            form[a].append(y - p)
            form[b].append(p - y)
        a, b = row["team1_id"], row["team2_id"]
        fa = sum(form[a][-10:]) / len(form[a][-10:]) if form[a] else 0.0
        fb = sum(form[b][-10:]) / len(form[b][-10:]) if form[b] else 0.0
        features.append(
            [
                (strength(a, now) - strength(b, now)) * math.log(10) / 400,
                fa - fb,
                math.log1p(counts[a]) - math.log1p(counts[b]),
                min(math.log1p(counts[a]), math.log1p(counts[b])),
            ]
        )
        heapq.heappush(pending, (timestamp(row["end_utc"]), index, row))
    return np.asarray(features, dtype=float)


def clustered_verdict(gains, groups, threshold=MINIMUM_BRIER_GAIN, seed=20261023) -> dict:
    grouped = defaultdict(list)
    for gain, group in zip(gains, groups, strict=True):
        grouped[group].append(float(gain))
    if not grouped:
        return {"verdict": "insufficient_evidence", "clusters": 0, "threshold": threshold}
    sums = np.array([sum(v) for v in grouped.values()])
    counts = np.array([len(v) for v in grouped.values()])
    rng = np.random.default_rng(seed)
    samples = []
    for _ in range(2000):
        draw = rng.integers(0, len(sums), len(sums))
        samples.append(float(sums[draw].sum() / counts[draw].sum()))
    lo, hi = np.quantile(samples, [0.025, 0.975]).tolist()
    verdict = "eligible_improvement" if len(grouped) >= 30 and lo > threshold else "insufficient_evidence"
    return {
        "verdict": verdict,
        "clusters": len(grouped),
        "mean_brier_gain": float(np.mean(gains)),
        "ci95": [lo, hi],
        "minimum_effect": threshold,
        "rule": "at least 30 date clusters and lower CI bound > minimum effect",
    }


def scores(y, p) -> dict:
    return {
        "n": len(y),
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, np.clip(p, 1e-9, 1 - 1e-9), labels=[0, 1])),
    }


def snapshot_from_row(row: dict):
    try:
        features = json.loads(row.get("feature_payload_json") or "{}")
        payload = json.loads(row.get("decision_payload_json") or "{}")
        if not isinstance(features, dict) or not isinstance(payload, dict):
            raise TypeError("invalid_snapshot_structure")
        snapshot = features.get("model_input_snapshot")
        if snapshot is None and payload.get("model_input_snapshot_json"):
            snapshot = json.loads(payload["model_input_snapshot_json"])
        if snapshot is not None and not isinstance(snapshot, dict):
            raise TypeError("invalid_snapshot_structure")
        if snapshot is not None:
            inputs = snapshot.get("inference_inputs", {})
            if not isinstance(inputs, dict) or not isinstance(inputs.get("code_sha256", {}), dict):
                raise TypeError("invalid_snapshot_structure")
            if any(
                not isinstance(value, str)
                or len(value) != 64
                or any(char not in "0123456789abcdef" for char in value)
                for value in inputs.get("code_sha256", {}).values()
            ):
                raise TypeError("invalid_snapshot_structure")
        return snapshot
    except json.JSONDecodeError as error:
        raise ValueError("malformed_snapshot_json") from error


def _replay_subprocess(cohort: list[dict], source_root: Path) -> dict:
    """Execute unchanged incumbent source, independently from corrected worktree."""
    code = """
import json,sys,hashlib
from pathlib import Path
from model_prediction.esports_replay import validate_decision_snapshot,code_hashes
hashes_before=code_hashes()
results={}
for row in json.load(sys.stdin):
    record={"probability":None,"mismatch":"missing_snapshot","feature_deltas":None}
    try:
        payload=json.loads(row.get("decision_payload_json") or "{}")
        feature=json.loads(row.get("feature_payload_json") or "{}")
        snapshot=feature.get("model_input_snapshot")
        if not snapshot and payload.get("model_input_snapshot_json"):
            snapshot=json.loads(payload["model_input_snapshot_json"])
        if snapshot:
            decision=dict(payload)
            decision.update(model_version=row["model_id"],model_artifact_hash=row["model_artifact_hash"],
                model_probability=row["model_probability"],event_id=row["event_id"],
                event_start_utc=row["event_start_utc"],market_type=row["market_type"],
                selection=row["selection"],line=row["line"])
            probability=validate_decision_snapshot(snapshot,decision)
            book=snapshot["inference_inputs"]["book_state"]
            ids=snapshot["inference_inputs"]["team_ids"]
            derived={"home_elo":book["ratings"].get(ids[0],1500.),
                "away_elo":book["ratings"].get(ids[1],1500.),
                "home_history_count":book["games_played"].get(ids[0],0),
                "away_history_count":book["games_played"].get(ids[1],0)}
            deltas={k:derived[k]-v for k,v in snapshot.get("features",{}).items() if k in derived}
            record.update(probability=probability,mismatch="exact_match" if all(abs(v)<1e-9 for v in deltas.values()) else "feature_mismatch",feature_deltas=deltas)
    except (ValueError,KeyError,TypeError,OverflowError,AttributeError) as error:
        if isinstance(error,json.JSONDecodeError):
            record["mismatch"]="malformed_snapshot_json"
        elif isinstance(error,(TypeError,AttributeError)):
            record["mismatch"]="invalid_snapshot_structure"
        elif isinstance(error,KeyError):
            record["mismatch"]="missing_snapshot_field"
        else:
            record["mismatch"]=str(error)
    results[row["pick_id"]+":"+row["ledger_tier"]]=record
if code_hashes()!=hashes_before:
    raise RuntimeError("incumbent_source_changed_during_replay")
print(json.dumps({"rows":results,"code_sha256":hashes_before}))
"""
    env = dict(os.environ, PYTHONPATH=str(source_root), PYTHONDONTWRITEBYTECODE="1")
    completed = subprocess.run(
        [sys.executable, "-c", code],
        input=json.dumps(cohort),
        capture_output=True,
        text=True,
        env=env,
        check=True,
        cwd=source_root.parent,
    )
    return json.loads(completed.stdout)


def resolve_git_sources(repo: Path, wanted: dict[str, set[str]]) -> dict:
    """Find immutable git source bytes, including unreachable Git objects.

    Legacy prediction records store a SHA256 but not a commit. Git garbage
    collection may leave the exact blob unreachable while it still exists in
    the object database, so history lookup alone is insufficient.
    """
    resolved = {}
    for relative, hashes in wanted.items():
        if relative not in {"esports.py", "features/elo_ratings.py"}:
            raise ValueError("unsupported_incumbent_source_path")
        path = "src/model_prediction/" + relative
        history = subprocess.run(
            ["git", "-C", str(repo), "log", "--all", "--format=%H", "--", path],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        for commit in history:
            blob = subprocess.run(
                ["git", "-C", str(repo), "show", commit + ":" + path], capture_output=True, check=False
            )
            if blob.returncode:
                continue
            sha = hashlib.sha256(blob.stdout).hexdigest()
            if sha in hashes and (relative, sha) not in resolved:
                blob_id = subprocess.run(
                    ["git", "-C", str(repo), "rev-parse", commit + ":" + path],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
                resolved[(relative, sha)] = {"commit": commit, "blob": blob_id, "bytes": blob.stdout}
            if all((relative, sha) in resolved for sha in hashes):
                break
    missing = {
        sha for relative, hashes in wanted.items() for sha in hashes if (relative, sha) not in resolved
    }
    if missing:
        # Stream every object; avoid a 4+ GB capture in memory. Exact SHA256
        # matching is the authority because unreachable blobs carry no path.
        process = subprocess.Popen(
            ["git", "-C", str(repo), "cat-file", "--batch-all-objects", "--batch"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdout is not None
        stopped_after_matches = False
        while header := process.stdout.readline():
            parts = header.rstrip(b"\n").split()
            if len(parts) != 3:
                process.kill()
                raise RuntimeError("invalid_git_object_stream")
            object_id, object_type, size_bytes = parts
            size = int(size_bytes)
            source_hash = hashlib.sha256()
            chunks = []
            remaining = size
            retain = size <= 1_048_576
            while remaining:
                chunk = process.stdout.read(min(1_048_576, remaining))
                if not chunk:
                    process.kill()
                    raise RuntimeError("truncated_git_object_stream")
                source_hash.update(chunk)
                if retain:
                    chunks.append(chunk)
                remaining -= len(chunk)
            if process.stdout.read(1) != b"\n":
                process.kill()
                raise RuntimeError("truncated_git_object_stream")
            if object_type == b"blob":
                sha = source_hash.hexdigest()
                if sha in missing:
                    if not retain:
                        process.kill()
                        raise ValueError("requested_incumbent_source_exceeds_1MiB")
                    content = b"".join(chunks)
                    matches = [relative for relative, hashes in wanted.items() if sha in hashes]
                    for relative in matches:
                        resolved[(relative, sha)] = {
                            "commit": None,
                            "blob": object_id.decode("ascii"),
                            "bytes": content,
                        }
                    missing.remove(sha)
                    if not missing:
                        stopped_after_matches = True
                        break
        if stopped_after_matches:
            process.terminate()
        stderr = process.communicate()[1]
        if not stopped_after_matches and process.returncode != 0:
            raise RuntimeError("git_object_scan_failed: " + stderr.decode("utf-8", "replace"))
    return resolved


def archived_control_replays(cohort: list[dict], source_root: Path, repo: Path, out: Path) -> dict:
    """Replay grouped rows with byte-verified historical source overlays."""
    grouped = defaultdict(list)
    wanted = defaultdict(set)
    for row in cohort:
        try:
            snapshot = snapshot_from_row(row)
            hashes = (snapshot or {}).get("inference_inputs", {}).get("code_sha256", {})
        except (ValueError, TypeError):
            hashes = {}
        key = tuple(hashes.get(name) for name in ("esports.py", "features/elo_ratings.py"))
        grouped[key].append(row)
        for relative, sha in hashes.items():
            if relative in {"esports.py", "features/elo_ratings.py"}:
                wanted[relative].add(sha)
    resolved = resolve_git_sources(repo, wanted)
    results = {}
    provenance = []
    for number, (key, group) in enumerate(grouped.items()):
        target = source_root
        recovered = all(
            (relative, sha) in resolved
            for relative, sha in zip(("esports.py", "features/elo_ratings.py"), key, strict=True)
        )
        source_record = {
            "archived_esports_sha256": key[0],
            "archived_elo_sha256": key[1],
            "rows": len(group),
            "historical_sources_recovered": recovered,
        }
        if recovered:
            target = out / "incumbent_sources" / str(number)
            package = target / "model_prediction"
            (package / "features").mkdir(parents=True)
            for folder in (package, package / "features"):
                fallback = (
                    source_root / "model_prediction" / ("features" if folder.name == "features" else "")
                )
                (folder / "__init__.py").write_text(
                    "__path__.append(" + repr(str(fallback.resolve())) + ")\n", encoding="utf-8"
                )
            source_record["git_sources"] = {}
            for relative, sha in zip(("esports.py", "features/elo_ratings.py"), key, strict=True):
                blob = resolved[(relative, sha)]
                (package / relative).write_bytes(blob["bytes"])
                if hashlib.sha256((package / relative).read_bytes()).hexdigest() != sha:
                    raise ValueError("recovered_source_hash_mismatch")
                source_record["git_sources"][relative] = {
                    "commit": blob["commit"],
                    "blob": blob["blob"],
                }
            (package / "esports_replay.py").write_bytes(
                (source_root / "model_prediction" / "esports_replay.py").read_bytes()
            )
        replayed = _replay_subprocess(group, target.resolve())
        results.update(replayed["rows"])
        source_record["actual_replay_code_sha256"] = replayed["code_sha256"]
        provenance.append(source_record)
    return {
        "rows": results,
        "source_groups": provenance,
        "dependency_scope": "archived esports.py and elo_ratings.py verified exactly; remaining dependencies from pinned original source",
    }


def audit_ledger(db: Path, out: Path, incumbent_source: Path, source_repo: Path) -> dict:

    connection = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("BEGIN")
    placeholders = ",".join("?" for _ in TITLES)
    all_rows = [
        dict(r)
        for r in connection.execute(
            f"SELECT * FROM ledger_records WHERE lower(sport) IN ({placeholders})", TITLES
        )
    ]
    connection.close()
    archive_bytes = json.dumps(all_rows, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    (out / "ledger_esports_snapshot.json.gz").write_bytes(gzip.compress(archive_bytes, mtime=0))
    if digest(json.loads(gzip.decompress((out / "ledger_esports_snapshot.json.gz").read_bytes()))) != digest(
        all_rows
    ):
        raise ValueError("ledger_snapshot_archive_verification_failed")
    active = [
        r
        for r in all_rows
        if str(r.get("status")).lower() not in {"removed", "superseded"}
        and str(r.get("result")).lower() in {"win", "loss"}
        and r.get("market_type") == "moneyline"
    ]
    cohort, overlap = canonical_cohort(active)
    control = archived_control_replays(cohort, incumbent_source, source_repo, out)
    parity = []
    reasons = Counter()
    by_title = {}
    for row in cohort:
        record = {
            k: row.get(k)
            for k in ("pick_id", "ledger_tier", "sport", "event_id", "model_id", "created_at_utc")
        }
        stored = row.get("model_probability")
        record.update(
            stored_probability=stored,
            reproduced_probability=None,
            probability_delta=None,
            per_feature_deltas=None,
            mismatch_class="missing_snapshot",
        )
        try:
            snapshot = snapshot_from_row(row)
            if snapshot:
                record["snapshot_hash"] = snapshot.get("snapshot_hash")
                record["archived_code_hashes"] = snapshot.get("inference_inputs", {}).get("code_sha256")
                record["archived_feature_values"] = snapshot.get("features")
                reproduced = control["rows"][row["pick_id"] + ":" + row["ledger_tier"]]
                p = reproduced["probability"]
                record.update(
                    reproduced_probability=p,
                    probability_delta=None if p is None else p - float(stored),
                    per_feature_deltas=reproduced["feature_deltas"],
                    mismatch_class=reproduced["mismatch"],
                )
        except (ValueError, KeyError, TypeError, OverflowError, AttributeError) as error:
            record["mismatch_class"] = (
                "invalid_snapshot_structure" if isinstance(error, (TypeError, AttributeError)) else str(error)
            )
        reasons[record["mismatch_class"]] += 1
        record["archived_quote_references_present"] = all(
            row.get(k)
            for k in ("market_snapshot_hash", "market_snapshot_archive_path", "market_snapshot_record_id")
        )
        record["decision_payload_hash"] = digest(row)
        parity.append(record)
    with (out / "incumbent_per_row_parity.jsonl").open("x", encoding="utf-8") as handle:
        for record in parity:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    for title in TITLES:
        selected = [r for r in cohort if r["sport"].lower() == title]
        flat = [r for r in active if r["sport"].lower() == title and str(r["ledger_tier"]).lower() == "flat"]
        flat, _ = canonical_cohort(flat)
        title_report = {
            "canonical_pick_keys": len(selected),
            "unique_events": len({r["event_id"] for r in selected}),
            "flat_proposal_cohort_n": len(flat),
        }
        for label, rows in (("canonical", selected), ("flat", flat)):
            if rows:
                y = [int(str(r["result"]).lower() == "win") for r in rows]
                title_report[label + "_stored_model"] = scores(
                    y, [float(r["model_probability"]) for r in rows]
                )
                quoted = [
                    r
                    for r in rows
                    if r.get("market_probability") is not None and 0 <= float(r["market_probability"]) <= 1
                ]
                if quoted:
                    title_report[label + "_stored_market_diagnostic"] = scores(
                        [int(str(r["result"]).lower() == "win") for r in quoted],
                        [float(r["market_probability"]) for r in quoted],
                    )
        title_report["reproduction_mismatch_classes"] = dict(
            Counter(r["mismatch_class"] for r in parity if r["sport"].lower() == title)
        )
        exact = {
            r["pick_id"]: r["reproduced_probability"]
            for r in parity
            if r["sport"].lower() == title and r["mismatch_class"] == "exact_match"
        }
        reproduced_rows = [r for r in selected if r["pick_id"] in exact]
        if reproduced_rows:
            outcomes = [int(str(r["result"]).lower() == "win") for r in reproduced_rows]
            title_report["exact_control_reproduced_metrics"] = scores(
                outcomes, [exact[r["pick_id"]] for r in reproduced_rows]
            )
            title_report["stored_metrics_on_same_reproduced_rows"] = scores(
                outcomes, [float(r["model_probability"]) for r in reproduced_rows]
            )
        by_title[title] = title_report
    passed = bool(parity) and reasons["exact_match"] == len(parity)
    return {
        "control_name": "exact_archived_v6_incumbent",
        "incumbent_source": str(incumbent_source),
        "incumbent_source_groups": control["source_groups"],
        "incumbent_dependency_scope": control["dependency_scope"],
        "gate": "PASS" if passed else "BLOCKED_REPRODUCTION",
        "ranking_allowed": passed,
        "mismatch_classes": dict(reasons),
        "cohort": overlap,
        "titles": by_title,
        "ledger_snapshot_rows_hash": digest(all_rows),
        "all_esports_records": len(all_rows),
        "eligible_settled_active_moneyline_records": len(active),
        "quote_references_present": sum(bool(r["archived_quote_references_present"]) for r in parity),
        "profitability_status": "NOT_EVALUATED: references alone do not certify executable contracts, costs or fills",
        "control_rule": "strict archived state/code identity replay; missing or drifted rows block ranking; no self-comparison",
        "per_feature_policy": "zero deltas only after independently verified replay; null when replay cannot establish lineage",
    }


def fit_logistic(x, y, c=1.0):
    model = make_pipeline(StandardScaler(), LogisticRegression(C=c, max_iter=1000, random_state=20261023))
    model.fit(x, y)
    return model


class LogisticResidual:
    """L2 residual with fixed coefficient one on the learned Elo logit."""

    def __init__(self, c: float):
        self.c = c
        self.scaler = StandardScaler()
        self.coef = None

    def fit(self, x, y):
        residual = self.scaler.fit_transform(x[:, 1:])
        design = np.column_stack([np.ones(len(x)), residual])
        offset = x[:, 0]

        def objective(beta):
            logits = offset + design @ beta
            loss = np.logaddexp(0, logits).sum() - y @ logits + 0.5 * (beta[1:] @ beta[1:]) / self.c
            gradient = design.T @ (expit(logits) - y)
            gradient[1:] += beta[1:] / self.c
            return float(loss), gradient

        result = minimize(objective, np.zeros(design.shape[1]), method="L-BFGS-B", jac=True)
        if not result.success:
            raise ValueError("residual_optimizer_failed:" + result.message)
        self.coef = result.x
        return self

    def decision_function(self, x):
        return x[:, 0] + self.coef[0] + self.scaler.transform(x[:, 1:]) @ self.coef[1:]

    def predict_proba(self, x):
        p = expit(self.decision_function(x))
        return np.column_stack([1 - p, p])

    def parameters(self):
        return {
            "fixed_elo_offset_coefficient": 1.0,
            "residual_feature_names": FEATURES[1:],
            "scaler_mean": self.scaler.mean_.tolist(),
            "scaler_scale": self.scaler.scale_.tolist(),
            "intercept": float(self.coef[0]),
            "coef": self.coef[1:].tolist(),
            "C": self.c,
        }


def model_parameters(model) -> dict:
    scaler, estimator = model.steps[0][1], model.steps[1][1]
    return {
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "coef": estimator.coef_.tolist(),
        "intercept": estimator.intercept_.tolist(),
        "C": estimator.C,
    }


def research_title(rows: list[dict], out: Path, title: str) -> dict:
    """Nested chronological development; final 20% of dates never scored."""
    dates = sorted({utc_date(r["start_utc"]) for r in rows})
    if len(dates) < 100:
        return {"status": "INSUFFICIENT_DATES"}
    final_date = dates[int(len(dates) * 0.8)]
    development = [r for r in rows if utc_date(r["start_utc"]) < final_date]
    # No final-window outcome or feature reaches model fitting or scoring.
    y = np.array([r["winner_id"] == r["team1_id"] for r in development], dtype=int)
    dev_dates = sorted({utc_date(r["start_utc"]) for r in development})
    start_array = np.array([timestamp(r["start_utc"]) for r in development])
    end_array = np.array([timestamp(r["end_utc"]) for r in development])
    boundaries = [dev_dates[int(len(dev_dates) * q)] for q in (0.45, 0.60, 0.75, 0.90)]
    fold_reports = []
    predictions = []
    artifacts = []
    feature_cache = {
        (k, h): chronological_features(development, k, h)
        for k in (8.0, 16.0, 32.0, 64.0)
        for h in (60.0, 180.0, None)
    }
    for fold, boundary in enumerate(boundaries):
        next_date = boundaries[fold + 1] if fold + 1 < len(boundaries) else final_date
        train, validate = chronological_partition(development, boundary, next_date)
        if len(train) < 200 or len(validate) < 30 or len(set(y[train])) < 2:
            continue
        train_dates = sorted({utc_date(development[i]["start_utc"]) for i in train})
        tune_date = train_dates[int(len(train_dates) * 0.65)]
        tune_cut = timestamp(tune_date + "T00:00:00Z")
        inner_train = train[(start_array[train] < tune_cut) & (end_array[train] < tune_cut)]
        inner_validate = train[start_array[train] >= tune_cut]
        inner_dates = sorted({utc_date(development[i]["start_utc"]) for i in inner_validate})
        cal_date = inner_dates[len(inner_dates) // 2]
        fit_end = timestamp(cal_date + "T00:00:00Z")
        tuning_indices = inner_validate[end_array[inner_validate] < fit_end]
        candidates = []
        for k in (8.0, 16.0, 32.0, 64.0):
            for half_life in (60.0, 180.0, None):
                x = feature_cache[(k, half_life)]
                p = 1 / (1 + np.exp(-np.clip(x[tuning_indices, 0], -30, 30)))
                candidates.append((float(log_loss(y[tuning_indices], p, labels=[0, 1])), k, half_life, x))
        best = min(candidates, key=lambda item: item[0])
        _, k, half_life, x = best
        # Fit/calibrate using genuinely out-of-time inner predictions only.
        selected_c = None
        best_loss = math.inf
        for c in (0.01, 0.1, 1.0, 10.0):
            estimator = LogisticResidual(c).fit(x[inner_train], y[inner_train])
            loss = float(
                log_loss(y[tuning_indices], estimator.predict_proba(x[tuning_indices])[:, 1], labels=[0, 1])
            )
            if loss < best_loss:
                selected_c, best_loss = c, loss
        # C-selection and calibration split inner validation by DATE. No same-row fit.
        inner_dates = sorted({utc_date(development[i]["start_utc"]) for i in inner_validate})
        cal_date = inner_dates[len(inner_dates) // 2]
        fit_end = timestamp(cal_date + "T00:00:00Z")
        fit_indices = train[(start_array[train] < fit_end) & (end_array[train] < fit_end)]
        cal_indices = train[start_array[train] >= fit_end]
        base_model = LogisticResidual(selected_c).fit(x[fit_indices], y[fit_indices])
        oof_residual = base_model.decision_function(x[cal_indices])[:, None]
        residual_cal = fit_logistic(oof_residual, y[cal_indices], 1.0)
        rating_cal = fit_logistic(x[cal_indices, :1], y[cal_indices], 1.0)
        # Keep same base model used for calibration: no train/full refit distribution shift.
        p_res = residual_cal.predict_proba(base_model.decision_function(x[validate])[:, None])[:, 1]
        p_rating = rating_cal.predict_proba(x[validate, :1])[:, 1]
        p_uncal = 1 / (1 + np.exp(-np.clip(x[validate, 0], -30, 30)))
        report = {
            "fold": fold,
            "validation_start": boundary,
            "validation_end_exclusive": next_date,
            "train_n_result_available": len(train),
            "base_fit_n": len(fit_indices),
            "calibration_n": len(cal_indices),
            "selected_k": k,
            "selected_inactivity_half_life_days": half_life,
            "selected_C": selected_c,
            "learned_rating": scores(y[validate], p_rating),
            "regularized_logistic": scores(y[validate], p_res),
            "uncalibrated_rating_diagnostic": scores(y[validate], p_uncal),
        }
        fold_reports.append(report)
        artifacts.append(
            {
                **report,
                "feature_names": FEATURES,
                "base_logistic_residual": base_model.parameters(),
                "residual_calibrator": model_parameters(residual_cal),
                "rating_calibrator": model_parameters(rating_cal),
            }
        )
        for j, index in enumerate(validate):
            predictions.append(
                {
                    "match_id": development[index]["match_id"],
                    "date_cluster": utc_date(development[index]["start_utc"]),
                    "fold": fold,
                    "y": int(y[index]),
                    "learned_rating": float(p_rating[j]),
                    "regularized_logistic": float(p_res[j]),
                }
            )
    write_artifact(
        out / (title + "_candidate_artifacts.json"),
        {
            "title": title,
            "fold_artifacts": artifacts,
            "status": "RESEARCH_ONLY_NOT_SERVING",
            "context_features_excluded": ["tier", "best_of"],
        },
    )
    with (out / (title + "_development_predictions.jsonl")).open("x", encoding="utf-8") as handle:
        for row in predictions:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    aggregate = {}
    if predictions:
        outcomes = [r["y"] for r in predictions]
        aggregate = {
            name: scores(outcomes, [r[name] for r in predictions])
            for name in ("learned_rating", "regularized_logistic")
        }
    return {
        "status": "RETROSPECTIVE_REUSED_HISTORY_DIAGNOSTIC_ONLY",
        "development_events": len(development),
        "final_window_start": final_date,
        "final_window_event_count": len(rows) - len(development),
        "final_window_status": "UNSCORED_NOT_A_FRESH_HOLDOUT: source history used by earlier generations",
        "folds": fold_reports,
        "aggregate_development": aggregate,
        "production_comparison": "BLOCKED: diagnostic histories not same executable decision cohort",
        "profitability": None,
        "context_features": "tier/best_of excluded pending timestamped metadata",
        "rating_initialization": "neutral zero log strength; zero form with explicit zero history count; no invented contextual inputs",
        "form_window": "last 10 result residuals per team, predeclared diagnostic feature; ablation required before use",
        "limitations": [
            "end_utc is provider completion proxy, not verified publication/collection timestamp",
            "stable team ID does not certify roster continuity",
            "no market residual training without audited quotes",
            "fixed learned-Elo offset plus regularized residual; not a market-offset model without audited quote histories",
        ],
    }


def run(data_root: Path, db: Path, output: Path, incumbent_source: Path, source_repo: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    source_bytes = Path(__file__).read_bytes()
    (output / "research_source_snapshot.txt").write_bytes(source_bytes)
    protocol = {
        "purpose": "retrospective reused history diagnostics and exact production reproduction audit",
        "minimum_brier_gain": MINIMUM_BRIER_GAIN,
        "effect_rationale": ".002 absolute Brier is 0.8% of the .25 neutral benchmark; conservative research screening floor, not an operator-approved economic risk budget",
        "comparison_rule": "date-cluster bootstrap lower 95% bound > .002; at least 30 clusters; exact control gate required",
        "final_window": "last 20% unique start dates reserved unscored; earlier inspected history not fresh prospective evidence",
        "split": "date grouped; all decisions for event same fold; model training outcomes end strictly before fold cutoff",
        "rating_search": {"k": [8, 16, 32, 64], "inactivity_half_life_days": [60, 180, None]},
        "regularization_C": [0.01, 0.1, 1, 10],
        "activation": False,
    }
    write_artifact(output / "protocol.json", protocol)
    ledger = audit_ledger(db, output, incumbent_source, source_repo)
    histories = {}
    for title in TITLES:
        path = data_root / title / "matches.jsonl"
        raw_bytes = path.read_bytes()
        archive = output / (title + "_history_snapshot.jsonl.gz")
        archive.write_bytes(gzip.compress(raw_bytes, mtime=0))
        if gzip.decompress(archive.read_bytes()) != raw_bytes:
            raise ValueError("history_snapshot_archive_verification_failed")
        raw = [json.loads(line) for line in raw_bytes.decode("utf-8").splitlines() if line.strip()]
        rows, coverage = audit_history(raw)
        coverage["source_path"] = str(path.resolve())
        coverage["immutable_input_archive"] = archive.name
        coverage["source_byte_sha256"] = hashlib.sha256(raw_bytes).hexdigest()
        histories[title] = {"coverage": coverage, "diagnostic_research": research_title(rows, output, title)}
    report = {
        "reproduction_gate": ledger["gate"],
        "production_ranking": "BLOCKED"
        if not ledger["ranking_allowed"]
        else "NO_ELIGIBLE_PAIRED_CHALLENGER_COHORT",
        "ledger": ledger,
        "histories": histories,
        "protocol_hash": json.loads((output / "protocol.json").read_text())["artifact_hash"],
        "comparative_verdict": "INSUFFICIENT_EVIDENCE_NO_ACTIVATION",
        "profitability": "NOT_ESTIMATED",
        "baseline_statuses": {
            "exact_v6": ledger["gate"],
            "corrected_v6": "REQUIRES_MATCHED_ARCHIVED_PIT_TRAINING_STATE",
            "recalibration_only_v6": "BLOCKED_UNTIL_CONTROL_AND_COHORT_VALID",
        },
    }
    write_artifact(output / "report.json", report)
    lines = [
        "# Esports research audit",
        "",
        "Reproduction gate: **" + ledger["gate"] + "**. Production rankings and activation are blocked.",
        "",
        "Actual archived serving states were attempted. Stored probabilities were not replayed as their own control. Missing snapshots and code drift remain explicit per-row failures.",
        "",
        "History results are reused retrospective development evidence. End timestamps strictly gate updates; provider publication and collection latency remain unverified. Tier and best-of lack observation timestamps and are excluded from predictive features.",
        "",
        "The final 20% of dates is unscored. It is not fresh prospective evidence. No profitability, synthetic prices, or activation is claimed.",
        "",
        "| Title | Canonical keys | Stored model Brier | Stored market Brier | Eligible history |",
        "|---|---:|---:|---:|---:|",
    ]
    for title in TITLES:
        summary = ledger["titles"][title]
        brier = summary.get("canonical_stored_model", {}).get("brier")
        market = summary.get("canonical_stored_market_diagnostic", {}).get("brier")
        lines.append(
            f"| {title} | {summary['canonical_pick_keys']} | {brier} | {market} | {histories[title]['coverage']['eligible_unique_events']} |"
        )
    lines.extend(
        [
            "",
            "Diagnostic fold metrics and learned parameters are in report.json and each title's hash-verified candidate artifact. They are not production rankings.",
            "",
            "Predeclared useful-effect floor: .002 absolute Brier (0.8% of neutral .25 Brier), conservative research screening only. A valid paired verdict additionally requires date-cluster lower 95% confidence bound above that floor and at least 30 clusters. No paired production comparison is eligible, so no bootstrap verdict was manufactured.",
        ]
    )
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = {
        str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in output.rglob("*")
        if p.is_file()
    }
    write_artifact(
        output / "manifest.json",
        {
            "files_sha256": manifest,
            "source_module_sha256": hashlib.sha256(source_bytes).hexdigest(),
        },
    )
    return report
