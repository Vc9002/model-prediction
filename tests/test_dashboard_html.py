from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_dashboard_inline_javascript_parses() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    html = (ROOT / "dashboard.html").read_text(encoding="utf-8")
    scripts = re.findall(r"<script>([\s\S]*?)</script>", html)
    assert scripts, "dashboard has no inline script"
    checker = (
        "const vm=require('vm');"
        "let source='';"
        "process.stdin.on('data', chunk => source += chunk);"
        "process.stdin.on('end', () => new vm.Script(source));"
    )
    for script in scripts:
        subprocess.run(
            [node, "-e", checker],
            input=script,
            text=True,
            check=True,
            capture_output=True,
        )
