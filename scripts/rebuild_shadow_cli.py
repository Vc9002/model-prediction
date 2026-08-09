"""Compatibility wrapper for the installed ``rebuild-shadow`` command."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from model_prediction.rebuild.cli import main, run

__all__ = ["main", "run"]


if __name__ == "__main__":
    main()
