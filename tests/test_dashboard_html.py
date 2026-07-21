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
    assert 'id="unitValueInput"' in html
    assert 'id="unitValueLabel"' in html
    assert '"/api/settings/unit-value"' in html
    assert "Historical unit records and existing orders will not change" in html
    assert '"Pregame close"' in html
    assert '"Bought at"' in html
    assert '"Decision edge"' in html
    assert "const pregamePrice" in html
    assert "const entryPrice" in html
    assert "side-adjusted realized P&amp;L" in html
    assert "Your-side price" in html
    assert '"Odds"' not in html
    assert "shortDate" in html
    assert '"Game (ET)"' in html
    assert "gameTimeET(p.event_start_utc)" in html
    assert 'timeZone:ET_ZONE' in html
    assert "Scan Open Ledger Prices" in html
    assert "Scan Today’s Prices" not in html
    assert "Validated model variants" in html
    assert "Results remain separate because binary and 1X2 calls" in html
    assert "order-wrap" in html
    assert "Live Portfolio — Exchange Positions" in html
    assert "Model Picks Ledger" in html
    assert "Paper model forecasts" in html
    assert "Order shows exchange status only" in html
    assert "Model Bet Execution History" not in html
    assert ".filter(p=>p.model_pick)" not in html
    assert 'join("")+"<th>Order</th><th>Matchup</th>' in html
    assert "model picks are not shown here" in html
    assert "p.market_name||p.title||slugToTitle" in html
    assert 'id="posSellPrice-${i}"' in html
    assert 'id="posSellShares-${i}"' in html
    assert "openPositionSell" not in html
    assert "Resting order accepted" in html
    assert '"cancel pending"' in html
    assert 'canceled:"CXL"' in html
    assert 'rejected:"REJ"' in html
    assert '`B ${cents(bid)} / A ${cents(ask)} · `' in html
    assert "holdout games" in html
    assert "TOTAL VALIDATION" in html
    assert "Validation cohorts" in html
    assert "Model validation details" in html
    assert 'c.state==="research_only"' in html
    assert "research only · 0U" in html
    assert "const baseballCell" in html
    assert "const basketballCell" in html
    assert "Current ask buys immediately" in html
    assert "immediate-or-cancel buy" in html
    assert 'value="open" selected>active only' in html
    assert "Settled (<span id=\"settledCount\">0</span>)" in html
    assert "No active picks remain today" in html
    assert 'id="todayShowSettled"' in html
    assert 'if(p.status!=="open")' in html
