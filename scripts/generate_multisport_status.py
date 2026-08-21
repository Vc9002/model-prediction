"""Generate canonical rebuild sport status from configuration and artifacts."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

SPORT_ORDER = ("mlb", "wnba", "nba", "nfl", "soccer", "tennis", "esports", "kbo", "npb")
NO_MODEL = {"status": "unavailable", "reason": "no rebuild challenger artifact is present"}


def _json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _revision(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=False, capture_output=True, text=True
    )
    if result.returncode != 0:
        return "unavailable"
    dirty = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, check=False, capture_output=True, text=True
    ).stdout.strip()
    return result.stdout.strip() + ("-dirty" if dirty else "")


def _mlb_model(repo: Path) -> dict[str, Any]:
    artifact_path = repo / "config/models/challengers/mlb-xgb_two_head_negative_binomial-calibrator-v1.json"
    artifact = _json(artifact_path)
    benchmark = _json(repo / "outputs/rebuild/mlb_head_distribution_cartesian.json")
    if artifact is None:
        return dict(NO_MODEL)

    selected = None
    if benchmark:
        combinations = benchmark.get("combinations")
        if isinstance(combinations, dict):
            selected = combinations.get("xgboost__negative_binomial")
    return {
        "status": "available",
        "model_id": artifact.get("model_name", "xgb_two_head_negative_binomial"),
        "head_family": artifact.get("head_family", "xgboost"),
        "distribution": artifact.get("distribution", "negative_binomial"),
        "calibration": artifact.get("method", "unavailable"),
        "ensemble": "none",
        "model_hash": artifact.get("base_model_hash"),
        "calibrator_hash": artifact.get("calibrator_hash"),
        "dataset_hash": artifact.get("dataset_hash"),
        "oof_sample": artifact.get("n_training_oof"),
        "dataset_size": benchmark.get("matched_games") if benchmark else None,
        "benchmark_sample": selected.get("best_cross_fit_n") if isinstance(selected, dict) else None,
        "artifact": artifact_path.relative_to(repo).as_posix(),
    }


def build_status(repo: Path, config_path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise TypeError("config/rebuild.yaml must contain an object")
    rebuild = raw.get("rebuild", {})
    execution = raw.get("execution", {})
    if not isinstance(rebuild, dict) or not isinstance(execution, dict):
        raise TypeError("rebuild and execution sections must be objects")
    hard_gates = {
        "shadow_only": rebuild.get("shadow_only") is True,
        "execution_disabled": rebuild.get("execution_enabled") is False,
        "production_promotion_disabled": rebuild.get("production_promotion") is False,
        "live_orders_disabled": execution.get("allow_live_orders") is False,
        "production_ledger_writes_disabled": execution.get("allow_production_ledger_write") is False,
    }
    if not all(hard_gates.values()):
        failed = ", ".join(name for name, valid in hard_gates.items() if not valid)
        raise ValueError(f"unsafe or missing rebuild hard gate(s): {failed}")

    configured_sports = raw.get("sports", {})
    if not isinstance(configured_sports, dict):
        raise TypeError("sports must be an object")
    models = {"mlb": _mlb_model(repo)}
    sports: list[dict[str, Any]] = []
    for name in SPORT_ORDER:
        configured = configured_sports.get(name, {})
        if not isinstance(configured, dict):
            configured = {}
        model = models.get(name, dict(NO_MODEL))
        sports.append(
            {
                "sport": name,
                "enabled": configured.get("enabled") is True,
                "configured_status": configured.get("status", "unavailable"),
                "model": model,
                "dataset_size": model.get("dataset_size"),
                "prospective_sample": None,
                "last_run": None,
                "predictive_qualification": "NOT_ESTABLISHED",
                "economic_qualification": "NOT_ESTABLISHED",
                "main_blocker": (
                    "sport disabled; research-only"
                    if configured.get("enabled") is False
                    else (
                        "prospective and economic evidence not established"
                        if model.get("status") == "available"
                        else model.get("reason", "rebuild implementation is incomplete")
                    )
                ),
            }
        )

    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "code_revision": _revision(repo),
        "mode": "SHADOW_ONLY",
        "execution_enabled": False,
        "production_promotion": False,
        "hard_gates": hard_gates,
        "sports": sports,
        "sources": [
            config_path.relative_to(repo).as_posix(),
            "config/models/challengers/",
            "outputs/rebuild/mlb_head_distribution_cartesian.json",
        ],
    }


def render_markdown(status: dict[str, Any]) -> str:
    lines = [
        "# Clean-Slate Rebuild Multi-Sport Status",
        "",
        "> SHADOW ONLY — NO LIVE EXECUTION — NO PRODUCTION PROMOTION",
        "",
        f"Generated from repository configuration/artifacts at `{status['code_revision']}`.",
        "",
        "| Sport | Enabled | Configured state | Rebuild model | Predictive | Economic | Main blocker |",
        "|---|---:|---|---|---|---|---|",
    ]
    for sport in status["sports"]:
        model = sport["model"]
        model_name = model.get("model_id") if model.get("status") == "available" else "unavailable"
        lines.append(
            "| {sport} | {enabled} | {state} | {model} | {predictive} | {economic} | {blocker} |".format(
                sport=str(sport["sport"]).upper(),
                enabled="yes" if sport["enabled"] else "no",
                state=sport["configured_status"],
                model=model_name,
                predictive=sport["predictive_qualification"],
                economic=sport["economic_qualification"],
                blocker=sport["main_blocker"],
            )
        )
    lines.extend(
        [
            "",
            "Unavailable fields are deliberately left unavailable; this generator does not infer runtime health or qualification.",
            "",
        ]
    )
    return "\n".join(lines)


def _display_path(path: Path, repo: Path) -> str:
    """Render repository outputs compactly without rejecting external CI paths."""
    try:
        return path.relative_to(repo).as_posix()
    except ValueError:
        return str(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--config", type=Path, default=Path("config/rebuild.yaml"))
    parser.add_argument("--json-output", type=Path, default=Path("outputs/rebuild/multisport_status.json"))
    parser.add_argument("--markdown-output", type=Path, default=Path("outputs/rebuild/multisport_status.md"))
    args = parser.parse_args()

    repo = args.repo_root.resolve()
    config_path = args.config if args.config.is_absolute() else repo / args.config
    json_output = args.json_output if args.json_output.is_absolute() else repo / args.json_output
    markdown_output = (
        args.markdown_output if args.markdown_output.is_absolute() else repo / args.markdown_output
    )
    status = build_status(repo, config_path)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    markdown_output.write_text(render_markdown(status), encoding="utf-8")
    print(f"Wrote {_display_path(json_output, repo)} and {_display_path(markdown_output, repo)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
