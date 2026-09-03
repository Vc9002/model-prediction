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
        "const esc=s=>String(s).replace(/[&<>]/g,c=>"
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


def _render_feature_registry(payload: dict) -> str:
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
        "const esc=s=>String(s).replace(/[&<>]/g,c=>"
        '({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));\n'
        + match.group(1)
        + "\nprocess.stdout.write(featureRegistryHtml(JSON.parse(process.argv[1])));"
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
    assert "<th>Entry</th><th>Decision</th><th>Spread</th><th>Slippage</th><th>Fees</th>" in html
    assert '"Odds"' not in html
    assert "shortDate" in html
    assert '"Game (ET)"' in html
    assert "gameTimeET(p.event_start_utc)" in html
    assert "timeZone:ET_ZONE" in html
    assert "Scan Open Ledger Prices" in html
    assert "Scan Today’s Prices" not in html
    assert "order-wrap" in html
    assert "Live Portfolio — Exchange Positions" in html
    assert "Model Picks Ledger" in html
    assert "Paper model forecasts" in html
    assert "Order shows exchange status only" in html
    assert "Model Bet Execution History" not in html
    assert ".filter(p=>p.model_pick)" not in html
    # Ledger/Flat/Research/Gated Research share one row/header renderer
    # (ledgerHeaderRow/ledgerRowHtml) so their columns and styling can never
    # drift apart again.
    assert "<th>Order</th><th>Matchup</th>" in html
    assert "function ledgerHeaderRow" in html
    assert "function ledgerRowHtml" in html
    assert "model picks are not shown here" in html
    assert "p.market_name||p.title||slugToTitle" in html
    assert 'id="posSellPrice-${i}"' in html
    assert 'id="posSellShares-${i}"' in html
    assert "openPositionSell" not in html
    assert "Resting order accepted" in html
    assert '"cancel pending"' in html
    assert 'canceled:"CXL"' in html
    assert 'rejected:"REJ"' in html
    assert "`B ${cents(bid)} / A ${cents(ask)} · `" in html
    assert "Current ask buys immediately" in html
    assert "immediate-or-cancel buy" in html
    assert 'value="open" selected>active only' in html
    # Ledger/Flat/Research each render their own independent filter bar
    # (filterBarHTML(tab)) rather than sharing one global set of controls.
    assert 'Settled (<span id="settledCount_${tab}">0</span>)' in html
    assert 'id="filters_L"' in html
    assert 'id="filters_F"' in html
    assert 'id="filters_R"' in html
    assert "No active picks remain today" in html
    assert 'id="todayShowSettled"' in html
    assert 'if(p.status!=="open")' in html


def test_dashboard_wires_live_production_evidence_tab_into_refresh() -> None:
    html = (ROOT / "dashboard.html").read_text(encoding="utf-8")

    assert 'data-tab="evidence"' in html
    assert 'id="tab-evidence"' in html
    assert 'id="productionEvidenceGenerated"' in html
    assert 'id="productionEvidence"' in html
    assert 'id="featureRegistry"' in html
    assert 'api("/api/production-evidence")' in html
    assert "renderProductionEvidence(evidenceResult.value)" in html
    assert "featureRegistryHtml" in html
    assert "ACTIVE MODEL · scope: exact model version only" in html
    assert "P&amp;L is shadow/hypothetical" in html
    assert "Predecessor rows excluded" in html
    assert "PROFITABILITY NOT ESTABLISHED" in html
    assert "keeps any positive out-of-sample contribution" in html
    assert "@media(max-width:600px)" in html


def test_dashboard_exposes_live_health_and_applies_display_settings() -> None:
    html = (ROOT / "dashboard.html").read_text(encoding="utf-8")

    assert 'data-tab="health"' in html
    assert 'id="tab-health"' in html
    assert 'id="healthDataTable"' in html
    assert "function renderHealth" in html
    assert "data_sources" in html
    assert "new ResizeObserver(syncStatusbarHeight)" in html
    assert "Set 1U to this percentage of the live balance; adjusts up or down" in html
    assert 'data-tab="perf">Performance<' in html

    # Settings were previously persisted but never applied. All ledger
    # filters (including the MLB v9 challenger ledger) and the Today
    # source loaders now honor the shared predicate.
    assert "const passesDisplaySettings" in html
    assert html.count("if(!passesDisplaySettings(p))return false;") == 5
    assert html.count("passesDisplaySettings(p)&&todayPassesControls(p)&&etDate(start)===todayET()") == 2

    # Today must use the real America/New_York conversion (including DST),
    # respect "show settled" for supplemental ledgers, and avoid inflating a
    # called-side probability with max(p, 1-p).
    assert "start.startsWith(todayET())" not in html
    assert "Math.max(p.model_probability,1-p.model_probability)" not in html
    assert 'className="today-source"' in html


def test_dashboard_operational_views_are_null_safe_filterable_and_accessible() -> None:
    html = (ROOT / "dashboard.html").read_text(encoding="utf-8")

    # One shared Performance surface replaces four drifting top-level tabs.
    assert html.count('data-tab="perf"') == 1
    assert 'data-tab="flat-perf"' not in html
    assert 'data-tab="research-perf"' not in html
    assert 'data-tab="gated-research-perf"' not in html
    assert 'id="perfLedgerSelect"' in html
    assert 'id="perfSportFilter"' in html
    assert 'id="perfMode"' in html
    assert "renderPerformanceCompare" in html
    assert 'id="perfCompare"' in html

    # Empty active-ledger states stay undefined instead of becoming green zeroes.
    assert "const hasSettled=wins+losses>0" in html
    assert "const winRate=hasSettled?wins/(wins+losses):null" in html
    assert "const totalPnl=hasSettled?" in html
    assert "const rowPnl=isSettled?" in html
    assert 'rowPnl==null?"—":fmt(rowPnl)' in html

    # Purchase controls fail closed in the row instead of inviting a rejected click.
    assert "if(p.buy_ready!==true)" in html
    assert 'class="blocked-order"' in html

    pnl_helper = html.split("const pickPnl=p=>{", 1)[1].split("const pnlClass", 1)[0]
    assert "p.quote?.ask" not in pnl_helper
    assert "Number(p.units)||1.0" not in pnl_helper
    assert "if(p.pnl_units!=null)return Number(p.pnl_units)" in pnl_helper
    assert "Number(p.display_units??p.units)||Number(p.suggested_paper_units)" not in html
    assert 'class="pill loss">Blocked' in html
    assert 'aria-label="Purchase blocked:' in html


def test_v9_kpis_exclude_unpriced_no_calls_from_betting_performance() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    html = (ROOT / "dashboard.html").read_text(encoding="utf-8")
    pnl_source = "const pickPnl=p=>{" + html.split("const pickPnl=p=>{", 1)[1].split("const pnlClass", 1)[0]
    metrics_source = (
        "function v9LedgerMetrics"
        + html.split("function v9LedgerMetrics", 1)[1].split("function renderV9LedgerTable", 1)[0]
    )
    price_source = (
        "const pregamePrice"
        + html.split("const pregamePrice", 1)[1].split("// Single shared row/header renderer", 1)[0]
    )
    scored = [
        {
            "status": "settled",
            "result": "win" if index < 8 else "loss",
            "units": 1.0,
            "display_units": 1.0,
            "pnl_units": 0.725 if index < 8 else -1.0,
            "display_pnl_units": 0.725 if index < 8 else -1.0,
        }
        for index in range(11)
    ]
    unpriced = [
        {
            "status": "settled",
            "result": "win",
            "decision": "NO_CALL",
            "reason_code": "NO_CALL_MARKET_PRICE_UNAVAILABLE",
            "units": 0.0,
            "pnl_units": 0.0,
            "edge": 0.0,
            "display_units": 0.0,
            "display_pnl_units": None,
            "economics_status": "unscored_no_price",
        }
        for _ in range(4)
    ]
    script = (
        pnl_source
        + "\n"
        + price_source
        + "\n"
        + metrics_source
        + "\nconst rows=JSON.parse(process.argv[1]);"
        + "process.stdout.write(JSON.stringify({metrics:v9LedgerMetrics(rows),"
        + "unpricedPnl:pickPnl(rows.at(-1)),unpricedEdge:edgePoints(rows.at(-1))}));"
    )

    result = subprocess.run(
        [node, "-e", script, json.dumps(scored + unpriced)],
        text=True,
        check=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)

    assert payload["metrics"] == {
        "outcomes": 15,
        "scored": 11,
        "unscored": 4,
        "wins": 8,
        "losses": 3,
        "winRate": pytest.approx(8 / 11),
        "pnl": pytest.approx(2.8),
        "risked": 11,
        "roi": pytest.approx(2.8 / 11),
    }
    assert payload["unpricedPnl"] is None
    assert payload["unpricedEdge"] is None

    # Market/Today/Portfolio controls and their URL-backed state are first class.
    for element_id in (
        "marketMode",
        "marketStaleMinutes",
        "todaySport",
        "todayMarket",
        "todayActionable",
        "folioFrom",
        "folioTo",
        "folioSport",
        "folioMarket",
        "folioType",
        "folioAttribution",
        "folioPageSize",
    ):
        assert f'id="{element_id}"' in html
    assert "api/live" in html
    assert "quoteStateBadge" in html
    assert "timeToStart" in html
    assert "setUrlState" in html
    assert "history.replaceState" in html

    # Large/raw row payloads use a keyboard-accessible detail drawer.
    assert 'id="drawer"' in html
    assert 'role="dialog"' in html
    assert "showPickDrawer" in html
    assert "showMarketDrawer" in html
    assert "showFolioDrawer" in html
    assert 'event.key==="Escape"' in html
    assert 'role="button"' in html

    # Panels expose independent freshness/error/retry state and integrity checks.
    for meta_id in ("healthMeta", "perfMeta", "todayMeta", "marketMeta", "folioMeta"):
        assert f'id="{meta_id}"' in html
    assert "Promise.allSettled" in html
    assert "dashboardIntegrity" in html
    assert "Duplicate ledger rows" in html
    assert "Missing event timestamps" in html
    assert "Open rows missing quotes" in html
    assert "Past events still unsettled" in html
    assert "status-health-link" in html


def test_feature_registry_renders_retention_strict_decision_and_safety() -> None:
    rendered = _render_feature_registry(
        {
            "valid": True,
            "last_updated": "2026-07-22",
            "sha256": "abc123",
            "errors": [],
            "counts_by_verdict": {"keep": 1},
            "retention_policy": {"keep_when": "keep any positive contribution"},
            "features": [
                {
                    "name": "weather_factor<script>",
                    "sports": ["MLB"],
                    "verdict": "keep",
                    "status": "research_keep_provenance_blocked",
                    "evidence_grade": "A",
                }
            ],
            "production_ablation_summary": [
                {
                    "sport": "mlb",
                    "feature": "weather_factor<script>",
                    "retention_decision": "KEEP",
                    "strict_decision": "REMOVE CANDIDATE",
                    "point_in_time_provenance": "blocked",
                    "production_safe": False,
                }
            ],
        }
    )

    assert "VERIFIED" in rendered
    assert "weather_factor&lt;script&gt;" in rendered
    assert "KEEP" in rendered
    assert "strict: REMOVE CANDIDATE" in rendered
    assert "PIT BLOCKED" in rendered
    assert "<script>" not in rendered


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


def test_production_evidence_renders_neutral_elo_model_spec_when_feature_alias_is_empty() -> None:
    rendered = _render_production_evidence(
        {
            "models": [
                {
                    "sport": "lol",
                    "model_version": "lol-neutral-series-elo-v2",
                    "status": "shadow_qualified",
                    "features": [],
                    "model_spec": {
                        "kind": "neutral_series_elo",
                        "feature_schema_status": "not_declared_in_artifact",
                        "features": [],
                        "parameters": {
                            "initial_rating": 1500.0,
                            "k": 48.0,
                            "home_or_order_advantage": 0.0,
                            "confidence_threshold": 0.05,
                            "target": "series winner",
                        },
                    },
                    "backfill": {},
                    "main_ledger": {},
                    "flat_ledger": {},
                    "artifact": {},
                    "profitability": {},
                }
            ]
        }
    )

    assert "model_kind" in rendered and "neutral_series_elo" in rendered
    assert "feature_schema_status" in rendered and "not_declared_in_artifact" in rendered
    assert "initial_rating" in rendered and "1500" in rendered
    assert "home_or_order_advantage" in rendered and "0" in rendered
    assert "confidence_threshold" in rendered and "0.05" in rendered
    assert "series winner" in rendered


def test_production_evidence_formats_nested_pnl_and_warning_objects_concisely() -> None:
    empty_pnl = {
        "shadow": {"label": "shadow_not_executed", "rows": 0, "pnl_units": None},
        "hypothetical": {
            "label": "hypothetical_fixed_unit_research",
            "rows": 0,
            "pnl_units": None,
        },
        "executed": {
            "label": "executed",
            "pnl_units": None,
            "status": "not_available_no_execution_attribution_in_ledgers",
        },
    }
    rendered = _render_production_evidence(
        {
            "models": [
                {
                    "sport": "cs2",
                    "model_version": "cs2-neutral-series-elo-v2",
                    "status": "shadow_qualified",
                    "features": [{"name": "neutral_elo_probability", "coefficient": None}],
                    "backfill": {},
                    "main_ledger": {"pnl_units": None, "pnl_basis": None, "pnl": empty_pnl},
                    "flat_ledger": {
                        "pnl_units": None,
                        "pnl_basis": "shadow",
                        "pnl": {
                            **empty_pnl,
                            "shadow": {
                                "label": "shadow_not_executed",
                                "rows": 2,
                                "pnl_units": -0.5,
                            },
                        },
                    },
                    "artifact": {},
                    "profitability": {},
                    "warnings": [
                        {
                            "code": "config_artifact_qualification_mismatch",
                            "scope": "qualification",
                            "artifact_qualified": False,
                        }
                    ],
                }
            ]
        }
    )

    assert ">-0.50</td>" in rendered
    assert "not_available_no_execution_attribution_in_ledgers" not in rendered
    assert '{"shadow"' not in rendered
    assert '"pnl_units"' not in rendered
    assert "config artifact qualification mismatch (qualification)" in rendered
    assert "config_artifact_qualification_mismatch" not in rendered
    assert '"artifact_qualified"' not in rendered


def test_production_evidence_collapses_identical_duplicate_models() -> None:
    model = {
        "sport": "mlb",
        "model_version": "mlb-v5",
        "status": "active_production",
        "features": {"feature_names": ["identical-marker"]},
        "backfill": {"calls": 41, "hit_rate": 0.61},
        "artifact": {"path": "config/models/mlb-v5.json", "hash_verified": True},
    }

    rendered = _render_production_evidence({"models": [model, json.loads(json.dumps(model))]})

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


def test_dashboard_ledger_filter_system_node_execution() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    html = (ROOT / "dashboard.html").read_text(encoding="utf-8")

    # Verify filter container sections and template strings exist in HTML
    for tab in ["L", "F", "R", "G", "PL"]:
        assert f'id="filters_{tab}"' in html

    assert 'id="fSport_${tab}"' in html
    assert 'id="fMarket_${tab}"' in html
    assert 'id="fStatus_${tab}"' in html
    assert 'id="fResult_${tab}"' in html
    assert 'id="fSearch_${tab}"' in html
    assert 'id="fFrom_${tab}"' in html
    assert 'id="fTo_${tab}"' in html
    assert 'id="fArchived_${tab}"' in html

    assert "function filterLedgerRow" in html
    assert "function filterLedgerRows" in html
    assert "function populateFilters" in html
    assert "function resetFilters" in html
    assert "function updateCounts" in html

    # Execute in Node.js VM to test filtering invariants
    script = (
        """
    const dom = {};
    const $ = id => {
      const key = id.replace(/^#/, '');
      if (!dom[key]) {
        dom[key] = { value: '', checked: false, textContent: '', innerHTML: '', style: {} };
      }
      return dom[key];
    };
    const etParts = value => {
      if (!value) return null;
      const str = String(value);
      if (str.startsWith('2026-08-22')) return { year: '2026', month: '08', day: '22' };
      if (str.startsWith('2026-08-20')) return { year: '2026', month: '08', day: '20' };
      return { year: '2026', month: '08', day: '22' };
    };
    const etDate = value => {
      const p = etParts(value);
      return p ? `${p.year}-${p.month}-${p.day}` : '';
    };
    const ALL_SPORTS = ['mlb', 'wnba', 'nba', 'nfl', 'soccer', 'tennis', 'lol', 'cs2', 'dota2', 'valorant', 'rainbow_six', 'kbo', 'npb'];
    const state = { settings: { sports: ALL_SPORTS, minConf: 0.5 }, picks: [] };
    const visibleSports = () => state.settings.sports || ALL_SPORTS;
    const passesDisplaySettings = p => {
      const league = String(p.league || p.Sport || '').toLowerCase();
      if (league && !visibleSports().includes(league)) return false;
      const probability = Number(p.model_probability);
      const minimum = Number(state.settings.minConf ?? 0.5);
      return !Number.isFinite(probability) || probability >= minimum;
    };
    const marketLabel = s => s;
    """
        + (
            "function filterLedgerRow(tab, p) {\n"
            + html.split("function filterLedgerRow(tab, p){")[1].split(
                "function filterLedgerRows(tab, rows){"
            )[0]
        )
        + """
    // Test rows
    const rows = [
      { pick_id: 'p1', league: 'MLB', market_type: 'moneyline', status: 'open', away_team: 'NYY', home_team: 'BOS', event_start_utc: '2026-08-22T19:00:00Z', archived: false },
      { pick_id: 'p2', Sport: 'mlb', Type: 'spread', status: 'settled', result: 'win', away_team: 'LAD', home_team: 'SF', event_start_utc: '2026-08-20T19:00:00Z', archived: false },
      { pick_id: 'p3', sport: 'CS2', market: 'moneyline', status: 'settled', result: 'loss', away_team: 'Navi', home_team: 'FaZe', event_start_utc: '2026-08-22T12:00:00Z', archived: true },
    ];

    // 1. Default filter (status=open)
    $('#fStatus_L').value = 'open';
    const openPicks = rows.filter(p => filterLedgerRow('L', p));
    if (openPicks.length !== 1 || openPicks[0].pick_id !== 'p1') {
      throw new Error('Default open filter failed: got ' + JSON.stringify(openPicks));
    }

    // 2. Filter by sport case-insensitively
    $('#fStatus_L').value = '';
    $('#fSport_L').value = 'mlb';
    const mlbPicks = rows.filter(p => filterLedgerRow('L', p));
    if (mlbPicks.length !== 2) {
      throw new Error('Sport filter failed: got ' + JSON.stringify(mlbPicks));
    }

    // 3. Filter by search query
    $('#fSport_L').value = '';
    $('#fSearch_L').value = 'faze';
    $('#fArchived_L').checked = true;
    const searchPicks = rows.filter(p => filterLedgerRow('L', p));
    if (searchPicks.length !== 1 || searchPicks[0].pick_id !== 'p3') {
      throw new Error('Search filter failed: got ' + JSON.stringify(searchPicks));
    }

    // 4. Filter by date range
    $('#fSearch_L').value = '';
    $('#fFrom_L').value = '2026-08-22';
    $('#fTo_L').value = '2026-08-22';
    const datePicks = rows.filter(p => filterLedgerRow('L', p));
    if (datePicks.length !== 2) {
      throw new Error('Date filter failed: got ' + JSON.stringify(datePicks));
    }

    // 5. Filter by market type case-insensitively
    $('#fFrom_L').value = '';
    $('#fTo_L').value = '';
    $('#fMarket_L').value = 'spread';
    const spreadPicks = rows.filter(p => filterLedgerRow('L', p));
    if (spreadPicks.length !== 1 || spreadPicks[0].pick_id !== 'p2') {
      throw new Error('Market filter failed: got ' + JSON.stringify(spreadPicks));
    }

    // 6. Filter by result (win/loss/push)
    $('#fMarket_L').value = '';
    $('#fResult_L').value = 'win';
    const winPicks = rows.filter(p => filterLedgerRow('L', p));
    if (winPicks.length !== 1 || winPicks[0].pick_id !== 'p2') {
      throw new Error('Result filter failed: got ' + JSON.stringify(winPicks));
    }

    process.stdout.write('OK');
    """
    )

    result = subprocess.run(
        [node, "-e", script],
        text=True,
        check=True,
        capture_output=True,
    )
    assert result.stdout == "OK"


def test_auto_buyer_consolidated_tab_markup_and_routing() -> None:
    html = (ROOT / "dashboard.html").read_text(encoding="utf-8")
    assert 'data-tab="auto-buyer-ledger"' in html
    assert '<section id="tab-auto-buyer-ledger"' in html
    assert 'id="autoBuyerKpis"' in html
    assert 'id="btnAbsSettled"' in html
    assert 'id="btnAbsPending"' in html
    assert 'id="btnAbsAll"' in html
    assert 'id="absPendingTable"' in html
    assert 'id="absSettledTable"' in html
    assert 'id="btnAutoBuyerPerformanceView"' in html
    assert 'id="autoBuyerPerformancePanel"' in html
    assert 'id="autoBuyerDailyPerformanceTable"' in html
    assert 'id="autoBuyerUnitValueInput"' in html
    assert 'id="autoBuyerUnitBadge"' in html
    assert '"/api/auto-buyer/unit-value"' in html
    assert "saveAutoBuyerUnitValue" in html
    assert "historical rows retain their execution-time unit value" in html
    assert "Future automated sizing and unit-based risk caps will become" in html
    assert "max_game_stake_units" in html
    assert "max_daily_spend_units" in html
    assert "setAutoBuyerView('performance')" in html
    assert "MLB cohort = MLB sport or MLB model ID" in html
    assert 'timeZone: "America/New_York"' in html
    assert "pending positions excluded" in html
    assert "renderAutoBuyer" in html
    assert "renderAutoBuyerSettle" in html
    assert "renderAutoBuyerLedger" in html
    assert "settleAutoBuyerTrades" in html
    assert "runAutoBuyerNow" in html
    assert '"No positions changed."' in html
    assert "Moved from pending: ${newlySettled}" in html
    assert "if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);" in html
    assert "const remainingPending = Number(data.remaining_pending);" in html
    assert "Still pending: ${remainingPending}" in html
    assert "Still pending: ${Number(data.pending || 0)}" not in html
    assert "Settled: ${data.settled}" not in html


def test_auto_buyer_performance_cohorts_and_et_day_grouping() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    html = (ROOT / "dashboard.html").read_text(encoding="utf-8")
    match = re.search(
        r"function isSettledAutoBuyerRow[\s\S]*?(?=function populateAbsSportDropdown)",
        html,
    )
    assert match, "Auto-Buyer performance helpers are missing"
    rows = [
        {
            "status": "settled",
            "result": "win",
            "sport": "MLB",
            "model_id": "mlb-first-inning-v2",
            "event_start_utc": "2026-09-02T02:00:00Z",
            "cost_usd": 0.5,
            "pnl_usd": 0.5,
            "pnl_units": 1.0,
        },
        {
            "status": "settled",
            "result": "loss",
            "sport": "CS2",
            "model_id": "cs2-tiered-elo-v6",
            "event_start_utc": "2026-09-02T03:00:00Z",
            "cost_usd": 0.5,
            "pnl_usd": -0.5,
            "pnl_units": -1.0,
        },
        {
            "status": "settled",
            "result": "win",
            "sport": "CS2",
            "model_id": "cs2-tiered-elo-v6",
            "event_start_utc": "2026-09-02T15:00:00Z",
            "cost_usd": 0.4,
            "pnl_usd": 0.6,
            "pnl_units": 1.2,
        },
        {
            "status": "open",
            "result": "open",
            "sport": "MLB",
            "event_start_utc": "2026-09-02T15:00:00Z",
            "cost_usd": 10,
        },
    ]
    script = (
        match.group(0)
        + "\nconst rows=JSON.parse(process.argv[1]);"
        + "const settled=rows.filter(isSettledAutoBuyerRow);"
        + "const output={all:autoBuyerCohortMetrics(settled),"
        + "mlb:autoBuyerCohortMetrics(settled.filter(isMlbAutoBuyerRow)),"
        + "without:autoBuyerCohortMetrics(settled.filter(r=>!isMlbAutoBuyerRow(r))),"
        + "dates:settled.map(autoBuyerEventDateEt)};"
        + "process.stdout.write(JSON.stringify(output));"
    )
    result = subprocess.run(
        [node, "-e", script, json.dumps(rows)],
        text=True,
        check=True,
        capture_output=True,
    )
    output = json.loads(result.stdout)
    assert output["all"]["settled"] == 3
    assert output["all"]["wins"] == 2
    assert output["all"]["losses"] == 1
    assert output["all"]["pnlUsd"] == pytest.approx(0.6)
    assert output["mlb"]["settled"] == 1
    assert output["mlb"]["roiPct"] == pytest.approx(100.0)
    assert output["without"]["settled"] == 2
    assert output["without"]["pnlUsd"] == pytest.approx(0.1)
    assert output["dates"] == ["2026-09-01", "2026-09-01", "2026-09-02"]


def test_active_portfolio_tab_is_rendered_during_initial_refresh() -> None:
    html = (ROOT / "dashboard.html").read_text(encoding="utf-8")
    assert 'else if(active==="folio") renderFolio();' in html


def test_polymarket_edge_navigation_is_pinned_disabled() -> None:
    html = (ROOT / "dashboard.html").read_text(encoding="utf-8")
    assert 'data-tab="polymarket" disabled title="Disabled by operator">Edge Scanner — OFF' in html
    assert 'data-tab="poly-ledger" disabled title="Disabled by operator">Edge Ledger — OFF' in html
    assert 'data-tab="poly-ledger" disabled title="Disabled by operator">Polymarket — OFF' in html
