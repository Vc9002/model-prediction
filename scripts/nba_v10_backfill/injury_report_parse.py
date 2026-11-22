"""Parse the archived NBA injury report PDFs into PIT rows (quick-261120-nbf).

Reads ``injury_reports/pdf_index.jsonl`` and the stored PDFs, month by month, and writes:
    injury_reports/reports/<YYYY-MM>.jsonl  one row per published slot (incl. byte-identical repeats)
    injury_reports/rows/<YYYY-MM>.jsonl.gz  parsed rows of every distinct PDF, keyed by report_sha256
    injury_reports/parse_progress.json      months done + per-month failure counts

``observed_at_utc`` is the later of the filename slot time and the report's own header time
("Injury Report: 03/23/24 11:30 PM" in the 11PM file): the content is as of the header time,
so using the earlier filename slot would leak up to 30 minutes. Only months the downloader has
finished (a later day is in ``pdf_progress.json``) are parsed; re-run to pick up new months.

Parse failure accounting per PDF: ``n_unplaced`` (text that could not be placed in a row) and
``n_inconsistent`` (rows whose team is not in the matchup or that lack date/matchup/player).

    PYTHONPATH=E:/MP-wt/backfill/src E:/MP/.venv/Scripts/python.exe scripts/nba_v10_backfill/injury_report_parse.py
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from model_prediction.data_sources.nba_backfill_common import (
    ET,
    append_jsonl,
    read_json,
    resolve_out_root,
    utc_now_iso,
    write_json_atomic,
)
from model_prediction.data_sources.nba_injury_report import parse_report, row_is_consistent

_logger = logging.getLogger("nba_v10.injury_parse")


def header_time_utc(text: str | None) -> str | None:
    if not text:
        return None
    try:
        local = datetime.strptime(text, "%m/%d/%y %I:%M %p").replace(tzinfo=ET)
    except ValueError:
        return None
    return local.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def month_rows(root: Path) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with (root / "injury_reports" / "pdf_index.jsonl").open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            if row.get("http_status") == 200 and row.get("stored_path"):
                out[row["report_date"][:7]].append(row)
    return out


def parse_month(root: Path, month: str, index_rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows_dir = root / "injury_reports" / "rows"
    rows_dir.mkdir(parents=True, exist_ok=True)
    out_path = rows_dir / f"{month}.jsonl.gz"
    tmp = out_path.with_suffix(".tmp")
    reports: list[dict[str, Any]] = []
    parsed_by_path: dict[str, dict[str, Any]] = {}
    stats = {"pdfs": 0, "distinct": 0, "rows": 0, "unplaced": 0, "inconsistent": 0, "errors": 0}
    with gzip.open(tmp, "wt", encoding="utf-8") as gz:
        for idx in sorted(index_rows, key=lambda r: r["observed_at_utc"]):
            stats["pdfs"] += 1
            path = idx["stored_path"]
            info = parsed_by_path.get(path)
            if info is None:
                info = {
                    "header_time_et": None,
                    "n_rows": 0,
                    "n_unplaced": 0,
                    "n_inconsistent": 0,
                    "n_pages": 0,
                    "parse_error": None,
                }
                try:
                    parsed = parse_report(root / path)
                except Exception as exc:  # noqa: BLE001 -- one bad PDF must not stop the archive
                    info["parse_error"] = f"{type(exc).__name__}: {exc}"[:300]
                    stats["errors"] += 1
                else:
                    header_utc = header_time_utc(parsed.header_time_text)
                    observed = max(filter(None, (idx["observed_at_utc"], header_utc)))
                    bad = [r for r in parsed.rows if not row_is_consistent(r)]
                    info.update(
                        header_time_et=parsed.header_time_text,
                        header_time_utc=header_utc,
                        n_rows=len(parsed.rows),
                        n_unplaced=len(parsed.unplaced),
                        unplaced_sample=parsed.unplaced[:5],
                        n_inconsistent=len(bad),
                        n_pages=parsed.n_pages,
                    )
                    for r in parsed.rows:
                        r.update(
                            report_filename=idx["filename"],
                            report_sha256=idx["sha256"],
                            observed_at_utc=observed,
                            consistent=row_is_consistent(r),
                        )
                        gz.write(json.dumps(r, sort_keys=True) + "\n")
                    stats["distinct"] += 1
                    stats["rows"] += len(parsed.rows)
                    stats["unplaced"] += len(parsed.unplaced)
                    stats["inconsistent"] += len(bad)
                parsed_by_path[path] = info
            header_utc = info.get("header_time_utc")
            reports.append(
                {
                    "filename": idx["filename"],
                    "report_date": idx["report_date"],
                    "filename_time_utc": idx["observed_at_utc"],
                    "observed_at_utc": max(filter(None, (idx["observed_at_utc"], header_utc))),
                    "sha256": idx["sha256"],
                    "stored_path": path,
                    "duplicate_of_previous": bool(idx.get("duplicate_of_previous")),
                    **{k: v for k, v in info.items() if k != "header_time_utc"},
                }
            )
    tmp.replace(out_path)
    reports_path = root / "injury_reports" / "reports" / f"{month}.jsonl"
    reports_path.parent.mkdir(parents=True, exist_ok=True)
    reports_tmp = reports_path.with_suffix(".tmp")
    reports_tmp.unlink(missing_ok=True)
    append_jsonl(reports_tmp, reports)
    reports_tmp.replace(reports_path)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-root", default=None)
    parser.add_argument("--months", nargs="*", help="only these YYYY-MM (re-parses; for validation)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    root = resolve_out_root(args.out_root)
    progress_path = root / "injury_reports" / "parse_progress.json"
    progress = read_json(progress_path, {"months": {}})
    downloaded_days = sorted(read_json(root / "injury_reports" / "pdf_progress.json", {"days": {}})["days"])
    last_day = downloaded_days[-1] if downloaded_days else ""
    for month, rows in sorted(month_rows(root).items()):
        if args.months and month not in args.months:
            continue
        if not args.months and (month in progress["months"] or last_day[:7] <= month):
            continue  # done, or the downloader may still add days to this month
        stats = parse_month(root, month, rows)
        stats["parsed_at_utc"] = utc_now_iso()
        progress["months"][month] = stats
        write_json_atomic(progress_path, progress)
        _logger.info("%s %s", month, stats)


if __name__ == "__main__":
    main()
