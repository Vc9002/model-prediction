# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users
A single operator (Vincent) who checks the dashboard daily, often before games start: scanning today's picks and pipeline health, then occasionally digging into ledgers, performance and model evidence.

## Product Purpose
The dashboard (`dashboard.html`, served by `dashboard_server.py` on port 8765) is the control and monitoring surface for `model-prediction`, a multi-sport quantitative prediction, ledger and portfolio research engine with Polymarket US CLOB integration. Its job is to monitor and act: trust-check pipeline and model health, review picks, trigger runs (daily forecast, price scans, tests, Auto-Buyer), and audit ledgers and performance.

## Positioning
A single-operator cockpit for a paper-trading research engine that behaves with real-capital fidelity (Kelly/edge-scaled unit sizing, live CLOB executable prices, mark-to-market PnL, CLV benchmarking, drawdown circuit breakers) without any real money at risk.

## Operating Context
- Runs locally on Windows; started with `dash.ps1`, stopped with `stop.ps1`.
- Auto-refreshes on a countdown; data comes from `/api/*` endpoints backed by the SQLite ledger and runtime files.
- Tabs today: Production, Matrix, Health, Today, Market, Model Picks Ledger, Flat Ledger, MLB v9 Ledger, Validation & Audit, Performance, Models, Portfolio, Auto-Buyer Ledger, Manual Bets Ledger, Rebuild Shadow, Evidence, Settings. Polymarket Edge Scanner/Ledger tabs are disabled by operator.

## Capabilities and Constraints
- 100% paper trading ("fugazi"): no real orders; `automated_orders: false`, `mode: "paper"`. The Auto-Buyer has PAPER and LIVE modes in the UI, so paper vs LIVE state must never be ambiguous and live/destructive actions must stay explicit.
- Existing tabs, element IDs and `/api` endpoint contracts are to be preserved unless changed deliberately.
- 1U (unit) dollar value is user-configurable and affects future dollar displays and order sizing only.
- Still undecided: whether to split the single HTML file, and whether to keep or retire the disabled Polymarket tabs.

## Evidence on Hand
Live data from the local engine (`E:\MP\data`, `E:\MP\dashboard\*.json`, SQLite ledger). Documentation in `E:\MP\docs\` (`PROJECT_STATUS.md`, `MASTER.md`, `INDEX.md`). No external users, testimonials or benchmarks exist; none should be invented.

## Product Principles
1. Trust before speed: status, staleness and paper/live mode are always visible and unambiguous.
2. Scan first, drill second: the landing view answers "is everything healthy and what should I act on today" at a glance.
3. Honest numbers: never imply real money, never hide failed or stale data.
4. Actions are deliberate: anything that triggers runs or touches live mode is explicit and confirmable.
