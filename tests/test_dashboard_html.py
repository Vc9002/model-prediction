from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _render_production_evidence(payload: dict) -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    html = (ROOT / "dashboard.html").read_text(encoding="utf-8")
    match = re.search(
        r"/\* ---------- production model evidence ---------- \*/([\s\S]*?)"
        r"/\* ---------- matrix ---------- \*/",
        html,
    )
    assert match, "production evidence renderer is missing"
    script = (
        'const esc=s=>String(s).replace(/[&<>]/g,c=>'
        '({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));\n'
        + match.group(1)
        + "\nprocess.stdout.write(productionEvidenceHtml(JSON.parse(process.argv[1])));"
    )
    result = subprocess.run(
        [node, "-e", script, json.dumps(payload)],
        text=True,
        check=True,
        capture_output=True,
    )
    return result.stdout


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


def test_dashboard_wires_live_production_evidence_tab_into_refresh() -> None:
    html = (ROOT / "dashboard.html").read_text(encoding="utf-8")

    assert 'data-tab="evidence"' in html
    assert 'id="tab-evidence"' in html
    assert 'id="productionEvidenceGenerated"' in html
    assert 'id="productionEvidence"' in html
    assert 'api("/api/production-evidence")' in html
    assert "renderProductionEvidence(evidence)" in html
    assert "ACTIVE MODEL · scope: exact model version only" in html
    assert "P&amp;L is shadow/hypothetical" in html
    assert "Predecessor rows excluded" in html
    assert "PROFITABILITY NOT ESTABLISHED" in html
    assert "@media(max-width:600px)" in html


def test_production_evidence_renders_exact_version_metrics_and_escapes_text() -> None:
    rendered = _render_production_evidence(
        {
            "generated_at": "2026-07-22T09:00:00Z",
            "models": [
                {
                    "sport": "mlb<script>",
                    "model_version": "mlb-v5<&",
                    "status": "active_production",
                    "features": {
                        "feature_names": ["elo_probability", "trend_gap<script>"],
                        "coefficients": [3.5, -0.25],
                        "elo_parameters": {"k": 20, "home_advantage": 35},
                    },
                    "backfill": {
                        "observations": 120,
                        "calls": 80,
                        "hit_rate": 0.625,
                        "brier_score": 0.21234,
                        "qualified": True,
                    },
                    "main_ledger": {
                        "settled": 12,
                        "wins": 7,
                        "losses": 5,
                        "pushes": 0,
                        "hit_rate": 7 / 12,
                        "pnl_units": 1.25,
                        "predecessor_rows_excluded": 9,
                    },
                    "flat_ledger": {
                        "settled": 20,
                        "wins": 11,
                        "losses": 8,
                        "pushes": 1,
                        "hit_rate": 11 / 19,
                        "pnl_units": -0.5,
                        "predecessor_rows_excluded": 4,
                    },
                    "artifact": {
                        "path": "config/models/mlb-v5.json",
                        "health": "healthy",
                        "sha256": "abc123",
                        "hash_verified": True,
                        "lineage": {"status": "healthy", "parent_version": "mlb-v4"},
                    },
                    "profitability": {
                        "status": "not_established",
                        "blockers": ["missing executable <BBO>"],
                    },
                    "warnings": ["warning <unsafe>"],
                }
            ],
        }
    )

    assert "MLB&lt;SCRIPT&gt; · mlb-v5&lt;&amp;" in rendered
    assert "trend_gap&lt;script&gt;" in rendered
    assert "62.5%" in rendered
    assert "0.2123" in rendered
    assert "QUALIFIED" in rendered
    assert "Main ledger" in rendered and "Flat ledger" in rendered
    assert "1.25" in rendered and "-0.50" in rendered
    assert "abc123" in rendered and "VERIFIED" in rendered
    assert "missing executable &lt;BBO&gt;" in rendered
    assert "warning &lt;unsafe&gt;" in rendered
    assert "<script>" not in rendered


def test_production_evidence_missing_values_are_dashes_not_zero_percent() -> None:
    rendered = _render_production_evidence(
        {
            "generated_at": None,
            "models": [
                {
                    "sport": "wnba",
                    "model_version": "wnba-v4",
                    "status": None,
                    "artifact": {},
                    "features": [],
                    "backfill": {},
                    "main_ledger": {},
                    "flat_ledger": {},
                    "profitability": {},
                    "warnings": [],
                }
            ],
        }
    )

    assert "--" in rendered
    assert "0.0%" not in rendered
    assert "PROFITABILITY NOT ESTABLISHED" in rendered


def test_production_evidence_collapses_identical_duplicate_models() -> None:
    model = {
        "sport": "mlb",
        "model_version": "mlb-v5",
        "status": "active_production",
        "features": {"feature_names": ["identical-marker"]},
        "backfill": {"calls": 41, "hit_rate": 0.61},
        "artifact": {"path": "config/models/mlb-v5.json", "hash_verified": True},
    }

    rendered = _render_production_evidence(
        {"models": [model, json.loads(json.dumps(model))]}
    )

    assert rendered.count('class="card evidence-card"') == 1
    assert rendered.count("identical-marker") == 1
    assert 'role="alert"' not in rendered
    assert "Conflicting production evidence" not in rendered


def test_production_evidence_warns_on_conflict_and_uses_first_verified_record() -> None:
    def record(marker: str, calls: int, verified: bool) -> dict:
        return {
            "sport": "wnba",
            "model_version": "wnba-v4",
            "status": "active_production",
            "features": {"feature_names": [marker]},
            "backfill": {"calls": calls, "hit_rate": calls / 100},
            "artifact": {
                "path": f"config/models/{marker}.json",
                "hash_verified": verified,
            },
        }

    rendered = _render_production_evidence(
        {
            "models": [
                record("discarded-unverified", 11, False),
                record("selected-first-verified", 22, True),
                record("discarded-later-verified", 33, True),
            ]
        }
    )

    assert rendered.count('class="card evidence-card"') == 1
    assert rendered.count('role="alert"') == 1
    assert "Conflicting production evidence for WNBA · wnba-v4" in rendered
    assert "first artifact-hash-verified record" in rendered
    assert "metrics were not merged" in rendered
    assert "selected-first-verified" in rendered
    assert "discarded-unverified" not in rendered
    assert "discarded-later-verified" not in rendered
