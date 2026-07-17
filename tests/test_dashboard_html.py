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


def test_dashboard_exposes_two_decimal_limit_price_and_unit_value() -> None:
    html = (ROOT / "dashboard.html").read_text(encoding="utf-8")

    assert 'class="order-price"' in html
    assert 'step="0.01"' in html
    assert 'placeholder=".50"' in html
    assert "1U = $" in html
    assert '"Market price"' in html
    assert '"Odds"' not in html
    assert "shortDate" in html
    assert "order-wrap" in html
    assert "Live Portfolio — Exchange Positions" in html
    assert "Model Picks Ledger" in html
    assert "Model Bet Execution History" in html
    assert ".filter(p=>p.model_pick)" in html
    assert "model picks are not shown here" in html
    assert 'id="posSellPrice-${i}"' in html
    assert 'id="posSellShares-${i}"' in html
    assert "openPositionSell" not in html
    assert "Resting order accepted" in html
    assert '"cancel pending"' in html
    assert '["canceled","rejected","expired","replaced"]' in html
    assert "holdout games" in html
    assert "TOTAL VALIDATION" in html
    assert "Validation cohorts" in html
    assert "Model validation details" in html
