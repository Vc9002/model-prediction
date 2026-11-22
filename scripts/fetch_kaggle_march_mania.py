"""Download the Kaggle March Machine Learning Mania data and write a manifest.

Needs the operator's Kaggle token (``~/.kaggle/access_token``) AND the competition rules accepted
once in a browser at the URL printed on a 403. This script never accepts rules on anyone's behalf.

Output: ``data/research/ncaab_external/kaggle_mm/raw/`` (untracked) plus ``manifest.json``
(source, slug, per-file sha256, fetched_at_utc).

Usage:
    .venv/Scripts/python scripts/fetch_kaggle_march_mania.py [--slug march-machine-learning-mania-2026]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from model_prediction.data_sources.ncaab_massey_ordinals import KAGGLE_COMPETITION, manifest_entry

OUT = ROOT / "data" / "research" / "ncaab_external" / "kaggle_mm"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default=KAGGLE_COMPETITION)
    args = ap.parse_args()
    raw = OUT / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    kaggle = Path(sys.executable).with_name("kaggle.exe" if sys.platform == "win32" else "kaggle")
    proc = subprocess.run(
        [str(kaggle), "competitions", "download", "-c", args.slug, "-p", str(raw)],
        capture_output=True,
        text=True,
        check=False,
    )
    print(proc.stdout.strip()[-500:], proc.stderr.strip()[-500:])
    archive = raw / f"{args.slug}.zip"
    if proc.returncode != 0 or "403" in proc.stdout + proc.stderr or not archive.exists():
        print(f"download refused; accept the rules at https://www.kaggle.com/competitions/{args.slug}/rules")
        return 2
    fetched_at = datetime.now(UTC).isoformat()
    with zipfile.ZipFile(archive) as zf:
        for member in zf.namelist():
            # Flat CSV names only; refuse anything that would escape the output folder.
            if "/" in member or "\\" in member or member.startswith(".."):
                raise ValueError(f"unexpected archive member {member!r}")
        zf.extractall(raw)
    files = sorted(p for p in raw.iterdir() if p.is_file())
    manifest = {
        "source": "kaggle competitions download",
        "slug": args.slug,
        "url": f"https://www.kaggle.com/competitions/{args.slug}/data",
        "fetched_at_utc": fetched_at,
        "files": [manifest_entry(p) for p in files],
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print(f"{len(files)} files, manifest at {OUT / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
