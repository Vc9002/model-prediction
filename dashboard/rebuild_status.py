#!/usr/bin/env python3
"""Read-only status projection for the isolated clean-slate rebuild.

This module deliberately does not import the rebuild ledger or metadata
classes: their constructors create and migrate databases. Dashboard reads use
SQLite's ``mode=ro`` URI and turn absent, partial, or malformed evidence into
explicit unavailable/degraded responses.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

import yaml

from model_prediction.runtime_paths import RuntimePaths

ROOT = Path(__file__).resolve().parents[1]
SPORTS = ("mlb", "wnba", "nba", "nfl", "soccer", "tennis", "esports", "kbo", "npb")
STAGES = ("collect", "build_features", "predict", "match_markets", "decide", "settle")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _unavailable(reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": "unavailable", "reason": reason, **extra}


def _safe_json(path: Path) -> tuple[Any | None, str | None]:
    if not path.is_file():
        return None, f"{path.name} does not exist"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as error:
        return None, f"{path.name} is unreadable or malformed: {error}"


class RebuildStatusReader:
    """Build API payloads without creating or modifying rebuild state."""

    def __init__(self, root: Path = ROOT, *, paths: RuntimePaths | None = None) -> None:
        # `root` (repo checkout) stays supported directly for callers that
        # don't care about the runtime-root split -- resolves the same way
        # RuntimePaths always has (runtime data colocated under repo/data
        # unless MODEL_PREDICTION_RUNTIME_ROOT is set). Pass `paths=` to
        # control the runtime root explicitly, e.g. in tests.
        resolved = paths or RuntimePaths.resolve(repo_root=root)
        self.root = resolved.repo_root
        self.output_root = resolved.rebuild_output_root
        self.data_root = resolved.rebuild_root
        self.shadow_db = resolved.rebuild_shadow_db
        self.metadata_db = resolved.rebuild_metadata_db

    def _json(self, name: str) -> tuple[Any | None, str | None]:
        return _safe_json(self.output_root / name)

    @staticmethod
    def _open_readonly(path: Path) -> sqlite3.Connection:
        # mode=ro fails if the file does not exist and cannot create WAL or DB files.
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        return connection

    def _query(self, path: Path, sql: str, params: tuple[Any, ...] = ()) -> tuple[list[dict], str | None]:
        if not path.is_file():
            return [], f"{path.name} does not exist"
        try:
            connection = self._open_readonly(path)
            try:
                rows = connection.execute(sql, params).fetchall()
            finally:
                connection.close()
            return [dict(row) for row in rows], None
        except sqlite3.Error as error:
            return [], f"{path.name} cannot satisfy the read-only query: {error}"

    def _table_exists(self, path: Path, table: str) -> bool:
        rows, error = self._query(
            path,
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
        return error is None and bool(rows)

    def _columns(self, path: Path, table: str) -> set[str]:
        rows, error = self._query(path, f"PRAGMA table_info({table})")
        return set() if error else {str(row.get("name")) for row in rows}

    def _config_sports(self) -> dict[str, dict[str, Any]]:
        path = self.root / "config" / "rebuild.yaml"
        if not path.is_file():
            return {}
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return {}
        sports = payload.get("sports") if isinstance(payload, dict) else None
        return sports if isinstance(sports, dict) else {}

    def _registry_tests(self) -> list[dict[str, Any]]:
        payload, _ = self._json("test_consumption_registry.json")
        active = payload.get("active_tests") if isinstance(payload, dict) else None
        if not isinstance(active, dict):
            return []

        prediction_rows: list[dict] = []
        if self._table_exists(self.shadow_db, "predictions"):
            prediction_rows, _ = self._query(
                self.shadow_db,
                """SELECT sport, decision_time_utc, model_artifact_hash,
                          calibration_artifact_hash, COUNT(DISTINCT event_id) AS prediction_count
                   FROM predictions
                   GROUP BY sport, decision_time_utc, model_artifact_hash,
                            calibration_artifact_hash""",
            )

        tests: list[dict[str, Any]] = []
        for test_id, raw in active.items():
            record = raw if isinstance(raw, dict) else {}
            consumed = record.get("consumed") is True
            item: dict[str, Any] = {
                "test_id": str(test_id),
                "consumed": consumed,
                "test_start": record.get("test_start"),
                "test_end": record.get("test_end"),
            }
            if not consumed:
                matching = [
                    row
                    for row in prediction_rows
                    if str(row.get("sport") or "").casefold() == str(test_id).split("_", 1)[0].casefold()
                    and (not record.get("test_start") or str(row.get("decision_time_utc") or "") >= str(record["test_start"]))
                    and (not record.get("test_end") or str(row.get("decision_time_utc") or "") < str(record["test_end"]))
                ]
                item.update(
                    {
                        "model_hash": matching[-1].get("model_artifact_hash") if matching else None,
                        "calibrator_hash": matching[-1].get("calibration_artifact_hash") if matching else None,
                        "prediction_count": sum(int(row.get("prediction_count") or 0) for row in matching),
                        "coverage": None,
                        "performance_hidden": True,
                    }
                )
            tests.append(item)
        return tests

    def benchmark(self) -> dict[str, Any]:
        cartesian, cartesian_error = self._json("mlb_head_distribution_cartesian.json")
        combo = None
        if isinstance(cartesian, dict):
            combinations = cartesian.get("combinations")
            if isinstance(combinations, dict):
                combo = combinations.get("xgboost__negative_binomial")
        if not isinstance(combo, dict):
            return _unavailable(
                cartesian_error or "authoritative corrected MLB benchmark is unavailable",
                authoritative=None,
            )

        baseline: float | None = None
        parquet_reason: str | None = None
        parquet_path = self.output_root / "model_benchmark.parquet"
        if parquet_path.is_file():
            try:
                import duckdb  # type: ignore[import-not-found]

                connection = duckdb.connect(database=":memory:")
                try:
                    columns = {
                        row[0]
                        for row in connection.execute(
                            "DESCRIBE SELECT * FROM read_parquet(?)", [str(parquet_path)]
                        ).fetchall()
                    }
                    metric_column = next((name for name in ("log_loss", "value", "metric_value") if name in columns), None)
                    model_column = next((name for name in ("model", "model_name", "baseline") if name in columns), None)
                    if metric_column and model_column:
                        row = connection.execute(
                            f"SELECT {metric_column} FROM read_parquet(?) "
                            f"WHERE lower(CAST({model_column} AS VARCHAR)) LIKE '%constant%0.5%' LIMIT 1",
                            [str(parquet_path)],
                        ).fetchone()
                        baseline = float(row[0]) if row and row[0] is not None else None
                finally:
                    connection.close()
            except Exception as error:  # noqa: BLE001 - malformed optional parquet must degrade safely
                parquet_reason = f"structured benchmark parquet unavailable: {error}"
        else:
            parquet_reason = "model_benchmark.parquet does not exist"

        return {
            "status": "ok" if baseline is not None else "degraded",
            "reason": parquet_reason if baseline is None else None,
            "authoritative": {
                "sport": "mlb",
                "head_family": combo.get("head_family"),
                "distribution": combo.get("distribution"),
                "calibration": combo.get("best_calibration_method"),
                "ensemble": "none",
                "oof_sample": combo.get("best_cross_fit_n"),
                "candidate_log_loss": combo.get("best_cross_fit_log_loss"),
                "constant_0_5_log_loss": baseline,
                "predictive_qualification": "NOT_ESTABLISHED",
                "economic_qualification": "NOT_ESTABLISHED",
                "source": "mlb_head_distribution_cartesian.json",
            },
        }

    def economics(self) -> dict[str, Any]:
        required = (
            "market_evaluations",
            "trade_decisions",
            "paper_orders",
            "settlements",
            "closing_prices",
        )
        missing = [table for table in required if not self._table_exists(self.shadow_db, table)]
        if missing:
            return _unavailable(
                "shadow database is absent or missing required tables: " + ", ".join(missing),
                market_evaluations=None,
                bet=None,
                no_bet=None,
                reason_codes=[],
                paper_orders=None,
                settlements=None,
                closing_prices=None,
                clv_coverage=None,
                pnl=None,
                roi=None,
                clv=None,
            )

        totals, totals_error = self._query(
            self.shadow_db,
            """SELECT
                 (SELECT COUNT(*) FROM market_evaluations) AS market_evaluations,
                 (SELECT COUNT(*) FROM trade_decisions WHERE action='BET') AS bet,
                 (SELECT COUNT(*) FROM trade_decisions WHERE action='NO_BET') AS no_bet,
                 (SELECT COUNT(*) FROM paper_orders) AS paper_orders,
                 (SELECT COUNT(*) FROM settlements) AS settlements,
                 (SELECT COUNT(*) FROM closing_prices WHERE closing_price IS NOT NULL) AS closing_prices,
                 (SELECT COUNT(*) FROM paper_orders WHERE COALESCE(filled_units, 0)>0) AS fills,
                 (SELECT COALESCE(SUM(filled_units * avg_fill_price), 0)
                    FROM paper_orders WHERE COALESCE(filled_units, 0)>0) AS capital,
                 (SELECT SUM(pnl) FROM settlements WHERE paper_order_id IS NOT NULL) AS pnl""",
        )
        if totals_error or not totals:
            return _unavailable(totals_error or "economic summary query returned no row")
        values = totals[0]
        reasons, _ = self._query(
            self.shadow_db,
            "SELECT COALESCE(reason_code, 'unspecified') AS reason, COUNT(*) AS count "
            "FROM trade_decisions GROUP BY reason ORDER BY count DESC",
        )
        fills = int(values.get("fills") or 0)
        settlements = int(values.get("settlements") or 0)
        closing = int(values.get("closing_prices") or 0)
        pnl = float(values["pnl"]) if fills and values.get("pnl") is not None else None
        capital = float(values.get("capital") or 0)
        return {
            "status": "ok",
            "market_evaluations": int(values.get("market_evaluations") or 0),
            "bet": int(values.get("bet") or 0),
            "no_bet": int(values.get("no_bet") or 0),
            "reason_codes": reasons,
            "paper_orders": int(values.get("paper_orders") or 0),
            "fills": fills,
            "settlements": settlements,
            "closing_prices": closing,
            "clv_coverage": (closing / settlements) if settlements else None,
            "pnl": pnl,
            "roi": (pnl / capital) if pnl is not None and capital > 0 else None,
            "clv": None,
        }

    def runs(self, limit: int = 50) -> dict[str, Any]:
        if not self._table_exists(self.shadow_db, "runs"):
            return _unavailable("shadow.db does not exist or has no runs table", runs=[])
        has_stages = self._table_exists(self.shadow_db, "run_stages")
        run_columns = self._columns(self.shadow_db, "runs")
        optional_run_columns = [
            name if name in run_columns else f"NULL AS {name}"
            for name in ("finished_at", "duration_seconds", "error", "mode")
        ]
        rows, error = self._query(
            self.shadow_db,
            "SELECT run_id, created_at, sport, run_type, horizon, status, params_json, "
            + ", ".join(optional_run_columns)
            + " "
            "FROM runs ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 250)),),
        )
        if error:
            return _unavailable(error, runs=[])
        stages: dict[str, list[dict]] = {}
        if has_stages and rows:
            stage_columns = self._columns(self.shadow_db, "run_stages")
            optional_stage_columns = [
                name if name in stage_columns else f"NULL AS {name}"
                for name in ("duration_seconds", "error", "mode", "row_count")
            ]
            identifiers = [row["run_id"] for row in rows]
            placeholders = ",".join("?" for _ in identifiers)
            stage_rows, _ = self._query(
                self.shadow_db,
                f"SELECT run_id, stage, status, completed_at, detail_json, "
                + ", ".join(optional_stage_columns)
                + " FROM run_stages "
                f"WHERE run_id IN ({placeholders}) ORDER BY completed_at DESC",
                tuple(identifiers),
            )
            for stage in stage_rows:
                detail: dict[str, Any] = {}
                try:
                    parsed = json.loads(stage.get("detail_json") or "{}")
                    detail = parsed if isinstance(parsed, dict) else {}
                except json.JSONDecodeError:
                    detail = {"error": "malformed stage detail"}
                stages.setdefault(str(stage["run_id"]), []).append(
                    {
                        "stage": stage.get("stage"),
                        "status": stage.get("status"),
                        "completed_at": stage.get("completed_at"),
                        "duration_seconds": stage.get("duration_seconds") or detail.get("duration_seconds"),
                        "rows": stage.get("row_count") if stage.get("row_count") is not None else detail.get("rows"),
                        "error": stage.get("error") or detail.get("error"),
                        "mode": stage.get("mode") or detail.get("mode"),
                    }
                )
        output = []
        for row in rows:
            params: dict[str, Any] = {}
            try:
                parsed = json.loads(row.get("params_json") or "{}")
                params = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                pass
            output.append(
                {
                    **{key: row.get(key) for key in ("run_id", "created_at", "sport", "run_type", "horizon", "status")},
                    "finished_at": row.get("finished_at"),
                    "duration_seconds": row.get("duration_seconds"),
                    "error": row.get("error"),
                    "mode": row.get("mode") or params.get("mode"),
                    "stages": stages.get(str(row["run_id"]), []),
                }
            )
        return {"status": "ok", "runs": output}

    def health(self) -> dict[str, Any]:
        sources: list[dict] = []
        source_error: str | None = None
        if self._table_exists(self.metadata_db, "sources"):
            sources, source_error = self._query(
                self.metadata_db,
                "SELECT source_id, name, tier, status, last_checked_utc, last_success_utc, last_error "
                "FROM sources ORDER BY source_id",
            )
        else:
            source_error = "metadata.db does not exist or has no sources table"
        run_payload = self.runs(limit=20)
        by_stage: list[dict[str, Any]] = []
        runs = run_payload.get("runs") if isinstance(run_payload, dict) else []
        for stage_name in STAGES:
            candidates = [
                stage
                for run in runs or []
                for stage in run.get("stages", [])
                if stage.get("stage") == stage_name
            ]
            latest = candidates[0] if candidates else {}
            by_stage.append(
                {
                    "stage": stage_name,
                    "last_run": latest.get("completed_at"),
                    "status": latest.get("status") or "unavailable",
                    "duration_seconds": latest.get("duration_seconds"),
                    "rows": latest.get("rows"),
                    "error": latest.get("error"),
                    "mode": latest.get("mode"),
                }
            )
        status = "ok"
        reasons = [reason for reason in (source_error, run_payload.get("reason")) if reason]
        if reasons:
            status = "degraded" if sources or runs else "unavailable"
        return {"status": status, "reason": "; ".join(reasons) or None, "sources": sources, "stages": by_stage}

    def sports(self) -> dict[str, Any]:
        payload, artifact_error = self._json("multisport_status.json")
        artifact_sports = payload.get("sports") if isinstance(payload, dict) else None
        if isinstance(artifact_sports, list):
            artifact_sports = {
                str(row.get("sport") or "").casefold(): row
                for row in artifact_sports
                if isinstance(row, dict) and row.get("sport")
            }
        if not isinstance(artifact_sports, dict):
            artifact_sports = {}
        config = self._config_sports()
        model_rows: list[dict] = []
        if self._table_exists(self.metadata_db, "models"):
            model_rows, _ = self._query(
                self.metadata_db,
                "SELECT sport, model_id, family, version, status FROM models ORDER BY created_utc DESC",
            )
        run_payload = self.runs(limit=250)
        runs = run_payload.get("runs") if isinstance(run_payload, dict) else []
        cards = []
        benchmark = self.benchmark().get("authoritative") or {}
        for sport in SPORTS:
            raw = artifact_sports.get(sport) or artifact_sports.get(sport.upper()) or {}
            raw = raw if isinstance(raw, dict) else {}
            raw_model = raw.get("model") if isinstance(raw.get("model"), dict) else {}
            cfg = config.get(sport) or {}
            cfg = cfg if isinstance(cfg, dict) else {}
            model = next((row for row in model_rows if str(row.get("sport") or "").casefold() == sport), None)
            sport_runs = [row for row in runs or [] if str(row.get("sport") or "").casefold() == sport]
            system_state = (
                raw.get("system_state")
                or raw.get("configured_status")
                or raw.get("status")
                or cfg.get("status")
                or "unavailable"
            )
            card = {
                "sport": sport,
                "enabled": cfg.get("enabled"),
                "system_state": system_state,
                "current_model": (
                    raw.get("current_model")
                    or raw_model.get("model_id")
                    or (model or {}).get("model_id")
                ),
                "dataset_size": raw.get("dataset_size"),
                "oof_sample": (
                    raw.get("oof_sample")
                    or raw_model.get("oof_sample")
                    or raw_model.get("benchmark_sample")
                ),
                "prospective_sample": raw.get("prospective_sample"),
                "last_run": sport_runs[0].get("created_at") if sport_runs else raw.get("last_run"),
                "predictive_qualification": raw.get("predictive_qualification") or "NOT_ESTABLISHED",
                "economic_qualification": raw.get("economic_qualification") or "NOT_ESTABLISHED",
                "main_blocker": raw.get("main_blocker") or (artifact_error if not raw else None),
            }
            if sport == "mlb" and benchmark:
                card.update(
                    {
                        "current_model": "XGBoost",
                        "distribution": benchmark.get("distribution"),
                        "calibration": benchmark.get("calibration"),
                        "ensemble": benchmark.get("ensemble"),
                        "oof_sample": benchmark.get("oof_sample"),
                    }
                )
            cards.append(card)
        return {
            "status": "ok" if artifact_sports else "degraded",
            "reason": artifact_error if not artifact_sports else None,
            "sports": cards,
        }

    def status(self) -> dict[str, Any]:
        verification, verification_error = self._json("verification.json")
        verification = verification if isinstance(verification, dict) else {}
        pytest_payload = verification.get("pytest")
        pytest_payload = pytest_payload if isinstance(pytest_payload, dict) else {}
        full_pytest = pytest_payload.get("full_repository")
        full_pytest = full_pytest if isinstance(full_pytest, dict) else None
        rebuild_pytest = pytest_payload.get("rebuild")
        rebuild_pytest = rebuild_pytest if isinstance(rebuild_pytest, dict) else None
        sports_payload = self.sports()
        benchmark = self.benchmark()
        cards = sports_payload.get("sports") or []
        has_data = sum(
            1
            for row in cards
            if isinstance(row.get("dataset_size"), (int, float))
            and not isinstance(row.get("dataset_size"), bool)
            and row["dataset_size"] > 0
        )
        has_models = sum(1 for row in cards if row.get("current_model"))
        prospective = sum(1 for test in self._registry_tests() if not test["consumed"] and test["test_start"])
        predictive = sum(1 for row in cards if row.get("predictive_qualification") == "QUALIFIED")
        economic = sum(1 for row in cards if row.get("economic_qualification") == "QUALIFIED")
        economics = self.economics()
        reasons = [
            reason
            for reason in (verification_error, sports_payload.get("reason"), benchmark.get("reason"))
            if reason
        ]
        return {
            "status": "ok" if not reasons else "degraded",
            "reason": "; ".join(reasons) or None,
            "generated_at_utc": _utc_now(),
            "shadow_only": True,
            "execution_enabled": False,
            "production_promotion": False,
            "code_revision": verification.get("code_revision"),
            "tests": full_pytest or {"status": "unavailable"},
            "rebuild_tests": rebuild_pytest or {"status": "unavailable"},
            "kpis": {
                "sports_with_data": has_data,
                "sports_with_models": has_models,
                "prospective_tests": prospective,
                "predictively_qualified_sports": predictive,
                "economically_qualified_sports": economic,
                "closing_price_coverage": economics.get("clv_coverage"),
            },
            "sealed_tests": self._registry_tests(),
            "benchmark": benchmark.get("authoritative"),
        }


_READERS: dict[str, Callable[[RebuildStatusReader], dict[str, Any]]] = {
    "status": RebuildStatusReader.status,
    "sports": RebuildStatusReader.sports,
    "benchmark": RebuildStatusReader.benchmark,
    "economics": RebuildStatusReader.economics,
    "runs": RebuildStatusReader.runs,
    "health": RebuildStatusReader.health,
}


def read_rebuild_view(view: str, root: Path = ROOT, *, paths: RuntimePaths | None = None) -> dict[str, Any]:
    reader = RebuildStatusReader(root, paths=paths)
    builder = _READERS.get(view)
    return builder(reader) if builder else _unavailable(f"unknown rebuild view: {view}")


def main() -> None:
    print(json.dumps(read_rebuild_view("status"), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
