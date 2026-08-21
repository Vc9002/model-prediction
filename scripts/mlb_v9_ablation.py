"""MLB v9 Phase 1 ablation experiments (DEPRECATED).

This script is deprecated and superseded by scripts/mlb_evaluator.py.
mlb_v9_ablation.py used an unpinned 60/20/20 split without frozen cohort manifests.
Use scripts/mlb_evaluator.py instead:
  PYTHONPATH=src:. uv run python scripts/mlb_evaluator.py --variants <variant_name>
"""

from __future__ import annotations

import sys


def main() -> int:
    sys.stderr.write(
        "ERROR: scripts/mlb_v9_ablation.py is deprecated and superseded by scripts/mlb_evaluator.py.\n"
        "mlb_v9_ablation.py used an unpinned 60/20/20 split without frozen cohort manifests.\n"
        "Use scripts/mlb_evaluator.py instead:\n"
        "  PYTHONPATH=src:. uv run python scripts/mlb_evaluator.py --variants <variant_name>\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
