---
schema_version: 1
open_count: 1
waived_count: 0
fixed_count: 1
total_count: 2
last_updated: 2026-11-12T20:52:24.204Z
---

# Broken Windows Ledger

> Cross-phase defect register. With `workflow.windows_enforce` enabled, `/gsd-ship` blocks while `open_count > 0`.
> Waive with `gsd-tools windows waive <id> "<reason>"` (reason required).
> Mark fixed with `gsd-tools windows fixed <id>`.

| id | phase | kind | file | line | description | status | reason | recorded_at | resolved_at |
|----|-------|------|------|------|-------------|--------|--------|-------------|-------------|
| 1 | 1 | deviation | src/model_prediction/portfolio/auto_executor.py |  | _build_bought_index swallows read errors (except: pass) on every source, including the live-money buyer ledgers; an unreadable ledger silently becomes an empty index (fail-open). Needs an operator decision; not fixed by quick task 261112-0li. | fixed |  | 2026-11-12T05:49:11.884Z | 2026-11-12T20:52:24.204Z |
| 2 | 1 | deviation | data/ledgers/ledgers.db |  | 241 historical settled identity-duplicate rows (80 groups), 235 from the 2026-10-21 b77a563d transition, 6 pre-index rows from Aug/Sep 2026. Already correctly counted once by a9aac3cb on the read side; cleanup is a governed ledger mutation needing an operator decision. Not fixed by quick task 261112-0li. | open |  | 2026-11-12T05:49:12.501Z |  |

````json
[
  {
    "id": 1,
    "kind": "deviation",
    "phase": "1",
    "file": "src/model_prediction/portfolio/auto_executor.py",
    "line": null,
    "description": "_build_bought_index swallows read errors (except: pass) on every source, including the live-money buyer ledgers; an unreadable ledger silently becomes an empty index (fail-open). Needs an operator decision; not fixed by quick task 261112-0li.",
    "status": "fixed",
    "reason": "",
    "recorded_at": "2026-11-12T05:49:11.884Z",
    "resolved_at": "2026-11-12T20:52:24.204Z",
    "milestone": null
  },
  {
    "id": 2,
    "kind": "deviation",
    "phase": "1",
    "file": "data/ledgers/ledgers.db",
    "line": null,
    "description": "241 historical settled identity-duplicate rows (80 groups), 235 from the 2026-10-21 b77a563d transition, 6 pre-index rows from Aug/Sep 2026. Already correctly counted once by a9aac3cb on the read side; cleanup is a governed ledger mutation needing an operator decision. Not fixed by quick task 261112-0li.",
    "status": "open",
    "reason": "",
    "recorded_at": "2026-11-12T05:49:12.501Z",
    "resolved_at": null,
    "milestone": null
  }
]
````
