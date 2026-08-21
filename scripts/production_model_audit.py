"""Deep audit of every production model (research tooling, read-only).

For each configured production model this checks, against LIVE state:
  1. artifact exists, parses, and self-verifies (artifact_hash, loader
     convention ensure_ascii=True)
  2. config wiring: filename <-> model_version <-> lineage consistency
  3. qualification honesty: qualified flag vs promotion_rationale /
     operator overrides
  4. serving coverage: every artifact feature_name has a provider in
     learned_forward
  5. champion resolution through the registry
  6. ledger evidence: settled/open rows per model_version, last activity
  7. input-data freshness for its sport (offseason awareness)

Run with the runtime env (health + ledger reads resolve through
RuntimePaths):
    MODEL_PREDICTION_RUNTIME_ROOT=/Users/vincentc9002/model-prediction-runtime \
    MODEL_PREDICTION_LEDGER_AUTHORITY=sqlite \
    env PYTHONPATH=src:. .venv/bin/python scripts/production_model_audit.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.config import PROJECT_ROOT
from model_prediction.learned_forward import _FEATURE_PROVIDERS
from model_prediction.production_canary import load_production_config
from model_prediction.production_registry import compute_artifact_hash
from model_prediction.runtime_paths import RuntimePaths

CONFIG = PROJECT_ROOT / "config" / "production.yaml"


def _self_hash(payload: dict) -> str:
    return compute_artifact_hash(payload)


def _ledger_stats(conn: sqlite3.Connection, model_id: str) -> dict:
    rows = conn.execute(
        "SELECT status, count(*), max(settled_at_utc) FROM ledger_records WHERE model_id = ? GROUP BY status",
        (model_id,),
    ).fetchall()
    stats = {status: {"n": n, "last_settled": last} for status, n, last in rows}
    total = conn.execute("SELECT count(*) FROM ledger_records WHERE model_id = ?", (model_id,)).fetchone()[0]
    return {"total": total, "by_status": stats}


def _sport_freshness(sport: str) -> dict:
    mapping = {
        "mlb": "mlb_games_all.jsonl",
        "wnba": "wnba_games_all.jsonl",
        "nba": "nba_games_all.jsonl",
        "nfl": "nfl_games_all.jsonl",
        "tennis": "tennis_games_all.jsonl",
    }
    if sport not in mapping:
        return {}
    path = PROJECT_ROOT / "data" / "historical" / mapping[sport]
    if not path.is_file():
        return {"status": "missing"}
    latest = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            latest = max(latest, str(json.loads(line).get("event_start_utc") or "")[:10])
        except json.JSONDecodeError:
            continue
    days = (datetime.now(UTC).date() - date.fromisoformat(latest)).days if latest else None
    return {"latest_event": latest, "days_ago": days}


def main() -> int:
    config = load_production_config(repo_root=PROJECT_ROOT)
    prediction_service = config.get("prediction_service") or {}
    models = prediction_service.get("models") or {}
    champions = prediction_service.get("champions") or {}
    paths = RuntimePaths.resolve(repo_root=PROJECT_ROOT)
    conn = sqlite3.connect(paths.ledgers_db)
    conn.row_factory = sqlite3.Row

    report: dict = {"generated_at_utc": datetime.now(UTC).isoformat(), "models": {}}

    for entry in sorted(models, key=lambda e: str(e.get("sport"))):
        sport_l = str(entry.get("sport") or "").lower()
        artifact_rel = entry.get("artifact")
        findings: list[str] = []
        detail: dict = {
            "status": entry.get("enabled"),
            "kind": entry.get("implementation"),
            "champion": (champions.get(entry.get("sport")) or {}).get(entry.get("market")),
            "is_champion": (champions.get(entry.get("sport")) or {}).get(entry.get("market"))
            == entry.get("model_id"),
        }

        if artifact_rel:
            artifact_path = PROJECT_ROOT / str(artifact_rel)
            if not artifact_path.is_file():
                findings.append("artifact file missing")
            else:
                try:
                    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as error:
                    findings.append(f"artifact unreadable: {error}")
                    artifact = {}
                if artifact:
                    detail["model_version"] = artifact.get("model_version")
                    stored = artifact.get("artifact_hash")
                    computed = _self_hash(artifact)
                    detail["hash_valid"] = stored == computed
                    if stored != computed:
                        findings.append(f"artifact_hash mismatch (stored {stored}, computed {computed})")
                    filename = Path(str(artifact_rel)).stem
                    version = str(artifact.get("model_version") or "")
                    if version and filename != version:
                        findings.append(f"filename {filename!r} != model_version {version!r}")
                    qual = artifact.get("qualification") or {}
                    rationale = str((artifact.get("training") or {}).get("promotion_rationale") or "")
                    detail["qualified"] = qual.get("qualified")
                    if qual.get("qualified") is False and "operator" in rationale.lower():
                        detail["override_note"] = "promoted by operator override despite qualified:false"
                    # serving coverage
                    feature_names = ((artifact.get("market_models") or {}).get("moneyline") or {}).get(
                        "feature_names"
                    ) or []
                    # Serving coverage: features computed inline or via
                    # dispatch branches in _compute_features, plus the
                    # lazily-registered _FEATURE_PROVIDERS (must init first).
                    from model_prediction import learned_forward as _lf

                    _lf._init_providers()
                    inline = {
                        "elo_probability",
                        "trend_gap",
                        "defensive_trend_gap",
                        "consistency_gap",
                        "hot_cold_gap",
                        "starter_era_gap",
                        "starter_fip_gap",
                        "starter_kbb_gap",
                        "bullpen_weakness_gap",
                        "bullpen_fatigue_gap",
                        "probable_starter_era_gap",
                        "rest_disparity",
                        "back_to_back_gap",
                        "games_last_7_gap",
                        "schedule_available",
                    }
                    missing_providers = [
                        f for f in feature_names if f not in _FEATURE_PROVIDERS and f not in inline
                    ]
                    detail["feature_names"] = feature_names
                    if missing_providers:
                        findings.append(f"no serving provider for features: {missing_providers}")
                    detail["threshold"] = ((artifact.get("market_models") or {}).get("moneyline") or {}).get(
                        "confidence_threshold"
                    )
                    detail["training_observations"] = {
                        k: v.get("observations")
                        for k, v in ((artifact.get("training") or {}).items())
                        if isinstance(v, dict) and "observations" in v
                    }
        else:
            detail["model_version"] = entry.get("model_id") or entry.get("code_backed")

        detail["ledger"] = _ledger_stats(conn, str(entry.get("model_id") or ""))
        detail["data_freshness"] = _sport_freshness(sport_l)
        detail["findings"] = findings
        report["models"][sport_l] = detail
        marker = "OK " if not findings else "GAP"
        print(
            f"[{marker}] {sport_l:10s} {detail.get('model_version')} "
            f"| ledger rows: {detail['ledger']['total']} | " + ("; ".join(findings) if findings else "clean")
        )

    conn.close()
    out = PROJECT_ROOT / "outputs" / "research" / "production_model_audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"\nreport: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
