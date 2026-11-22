"""Fetch Torvik (T-Rank) daily ratings archives from the cbbdata API into the research cache.

One request per season, a pause between seasons, cached under
``data/research/ncaab_external/cbbdata/torvik__ratings__archive/`` with a ``.meta.json``
(fetched_at_utc, sha256) beside each parquet. Rerunning skips cached seasons (``--refresh`` to
refetch, e.g. the in-progress season). The key is read by the project's dotenv loader into
``os.environ``; it is never printed or written.

Usage:
    .venv/Scripts/python scripts/fetch_ncaab_cbbdata_archive.py [--years 2022 2023 2024 2025 2026] [--refresh]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from model_prediction.data_sources.ncaab_cbbdata import CBBDataAuthError, CBBDataClient
from model_prediction.runtime_paths import load_dotenv_into_environ


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, nargs="+", default=[2022, 2023, 2024, 2025, 2026])
    ap.add_argument("--pause", type=float, default=2.0)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    load_dotenv_into_environ(ROOT / ".env")
    try:
        client = CBBDataClient()
    except CBBDataAuthError as exc:
        print(f"no credentials: {exc}")
        return 2
    for year in args.years:
        try:
            snapshots = client.torvik_ratings_archive(year, refresh=args.refresh)
        except CBBDataAuthError as exc:
            print(f"{year}: auth rejected: {exc}")
            return 2
        dates = sorted({s.snapshot_date for s in snapshots})
        span = f"{dates[0]}..{dates[-1]}" if dates else "empty"
        print(
            f"{year}: {len(snapshots)} rows, {len({s.team for s in snapshots})} teams, {len(dates)} dates {span}"
        )
        time.sleep(args.pause)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
