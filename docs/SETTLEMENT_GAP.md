# Settlement Gap — Investigation 2026-08-13

## Finding

The canonical per-model ledger directory `data/model_ledgers/` is **frozen** —
its newest file is dated 2026-08-03, while the live pipeline writes to per-tier
mirrors that are current through 2026-08-13.

## Evidence

| Directory | Settled | Open | Freshness |
|---|---:|---:|---|
| `data/model_ledgers/` (canonical) | 432 | 1697 | frozen 08-03 |
| `data/flat/model_ledgers/` | 507 | 2390 | current 08-13 |
| `data/research/model_ledgers/` | 393 | 3068 | current 08-13 |
| `data/gated_research/model_ledgers/` | — | — | current 08-13 |

Thousands of picks sit "open" past their start time across every sport
(cs2 263, mlb-ml 189, tennis 353, soccer 131, lol 184, valorant 111, wnba 65…).

## Root cause

The settlement mirror is `settle_from_pick_row(self.path.parent / "model_ledgers", row)`
in `ledger.py:856`. For the main ledger at `data/picks.xlsx`, `self.path.parent`
is `data/`, so it writes `data/model_ledgers/` (the canonical directory the
dashboard and `load_settled_predictions` read).

That mirror is guarded by `if not self.retired` (ledger.py:851). Since
`main_ledger_enabled: false` (config/model.yaml:16) retired the main ledger,
its settle hook — the ONLY writer to the canonical `data/model_ledgers/` — no
longer fires. The flat/research/gated ledgers still settle, but they mirror to
their own per-tier subdirectories, not the canonical one.

## Impact

- Dashboard "model evidence" reads the stale canonical directory.
- `champion_challenger.load_settled_predictions` / `settled_champion_calibration`
  read the stale canonical directory (numbers reflect data through 08-03 only).
- Thousands of open picks are effectively orphaned in the canonical ledger.

## Fix direction (not yet applied)

Point the settled-picks loader and dashboard at the live per-tier mirrors, or
merge the mirrors back into the canonical directory during settlement. This is
a production data-path change that needs an explicit decision before landing —
not a silent routing change.

## Update 2026-08-13 (same session)

The routing fix has now been applied: `PickLedger` accepts a
`model_ledgers_dir`, and `main_ledgers.py` / `research_ledgers.py` pass the
canonical `data/model_ledgers/`. New mirrors land in the canonical directory
again. The 08-03→present stale window is not backfilled (settled rows already
in per-tier mirrors are not merged back), and the fix is a production
data-path change that should run under observation before it is trusted
unattended.
