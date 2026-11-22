"""NBA v10 forward research report: tests A and B of PREREG_FORWARD.md (read-only).

  MODEL_PREDICTION_RUNTIME_ROOT=E:/model-prediction-runtime \\
  python scripts/nba_v10/forward_report.py [--as-of 2026-11-30T12:00:00Z] [--forward-root DIR]

Writes ``outputs/nba/v10_forward/FORWARD_<ET date>.md`` and ``.json``. Refuses to run unless
``outputs/nba/v10_forward/PREREG_FORWARD.md`` is committed. Every verdict is PENDING until the
pre-registered n (400 games per market for A, 100 events per market for B). The scoring lives in
``model_prediction.nba_v10_forward_report``; this script only reads logs and writes the report.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from model_prediction.domain import EASTERN, parse_utc, utc_now
from model_prediction.nba_v10_forward_report import build_report, render_markdown
from model_prediction.runtime_paths import RuntimePaths

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "outputs" / "nba" / "v10_forward"
PREREG = OUT_DIR / "PREREG_FORWARD.md"


def _prereg_committed() -> bool:
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "log", "--format=%H", "--", str(PREREG.relative_to(REPO_ROOT))],
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(out.stdout.strip())


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--as-of", default=None, help="UTC time to score as of (default: now)")
    parser.add_argument(
        "--forward-root", type=Path, default=None, help="forward log directory (default: runtime)"
    )
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args(argv)
    if not _prereg_committed():
        sys.exit("PREREG_FORWARD.md is not committed: nothing is scored before the pre-registration")
    paths = RuntimePaths.resolve(require_external_runtime=True)
    as_of = parse_utc(args.as_of) if args.as_of else utc_now()
    forward_root = args.forward_root or (paths.research_root / "nba_v10_forward")
    report = build_report(forward_root, paths.runtime_root, REPO_ROOT, paths.ledgers_db, as_of)
    day = as_of.astimezone(EASTERN).date().isoformat()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / f"FORWARD_{day}.md").write_text(render_markdown(report), encoding="utf-8")
    (args.out_dir / f"FORWARD_{day}.json").write_text(
        json.dumps(_jsonable(report), indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    print(f"wrote {args.out_dir / f'FORWARD_{day}.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
