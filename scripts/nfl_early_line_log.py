"""Manual run of the NFL early-line research log: python scripts/nfl_early_line_log.py [YYYY-MM-DD ...]

With no dates it logs the next seven days. The daily job already runs this once a day.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

from model_prediction.cli.nfl_early_line_log import log_nfl_early_lines

if __name__ == "__main__":
    print(
        log_nfl_early_lines(
            Path("data"), now=datetime.now(UTC), dates=sys.argv[1:] or None, once_per_day=False
        )
    )
