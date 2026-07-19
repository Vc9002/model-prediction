---
name: operate-model-dashboard
description: Start, inspect, test, troubleshoot, and safely modify the local model-prediction dashboard and its view state. Use when Vincent asks to open the dashboard, debug dashboard data or controls, inspect matrix, ledger, or order status, change display behavior, clear or restore local rows, update unit-value settings, or verify the dashboard in Dia. Never submit, cancel, or modify exchange orders without a separate explicit real-money request and confirmation.
---

# Operate Model Dashboard

Work from `/Users/vincentc9002/Documents/Poly & Kalshi/model prediction`.

## Diagnose before restarting

1. Read `docs/PROJECT_STATUS.md` and inspect the working tree.
2. Inspect `data/logs/dashboard.err`, `data/logs/dashboard.log`,
   `dashboard/server.log`, and current port health.
3. Check `http://127.0.0.1:8765/api/health` and `/api/status` before changing state.
4. Start a temporary server with:

   ```sh
   env PYTHONPATH=src:. .venv/bin/python dashboard_server.py --port 8765
   ```

5. Do not run `./dash`; it uses broad `pkill -f` and the system browser. Do not
   install or reload launchd services without explicit approval.

## Verify in Dia

Use the Codex Dia Bridge from
`/Users/vincentc9002/Documents/Codex Chrome Bridge`. Prefer `snapshot`, then
`click-ref` or `type-ref`, then verify with a fresh snapshot or API response.
Do not target Chrome, Safari, or the generic system browser.

## Preserve state boundaries

- `data/picks.xlsx` and `data/flat_picks.xlsx` are model ledgers.
- `dashboard/archive.json` controls local row visibility; Clear/Restore must not delete research history.
- `dashboard/orders.json` is local state; authoritative exchange lookup wins, while lookup failure preserves local state.
- `dashboard/portfolio_history.json`, `dashboard/jobs.json`, and unit-value settings are operational state. Preserve historical units and exposure.
- `/api/order/submit`, `/api/order/submit-position`, execution CLI commands, cancellation paths, and credentials are real-money surfaces. Never call them during ordinary dashboard work.

## Change and test

Trace both `dashboard.html` and `dashboard_server.py`; the server assembles much
of the displayed state. Add focused Python and HTML/JavaScript tests, then run:

```sh
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/test_dashboard_server.py tests/test_dashboard_html.py -q
.venv/bin/ruff check dashboard_server.py tests/test_dashboard_server.py tests/test_dashboard_html.py
```

Do not ask Ruff to parse `dashboard.html`. Verify final behavior in Dia and
confirm that ledger, archive, order, and settings files changed only as intended.
