from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_generator_accepts_output_paths_outside_repository(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    json_output = tmp_path / "multisport_status.json"
    markdown_output = tmp_path / "multisport_status.md"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/generate_multisport_status.py",
            "--repo-root",
            str(repo),
            "--json-output",
            str(json_output),
            "--markdown-output",
            str(markdown_output),
        ],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json_output.is_file()
    assert markdown_output.is_file()
    assert str(json_output) in result.stdout
    assert str(markdown_output) in result.stdout
