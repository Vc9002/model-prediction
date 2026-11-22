---
quick_id: 261122-int
slug: nba-intl-lead-replay
date: 2026-11-22
mode: quick (inline, ralph round 3)
---

# Polymarket international as a PIT reference for Polymarket US (every settled NBA game)

Add `scripts/nba_v10/intl_lead_settled_replay.py` (read-only on the ledger; caches public
gamma/clob data under `data/research/nba_v10/polymarket_intl/`). Rules are pinned in the script
docstring before outcomes: US decision quote as in tb_settled_replay, international reference =
last prices-history point <= decision and <= 30 min old for the identical proposition, signal =
intl price - US ask >= 3.0 points, one pick per game/market (largest gap), 1u at the US ask,
gross and net of fee, weekly.
