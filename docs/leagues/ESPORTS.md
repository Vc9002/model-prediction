# Esports research contract

## Scope

- Modelled now: League of Legends (`lol`) and Counter-Strike 2 (`cs2`).
- Market discovery only: Call of Duty, Valorant, Dota 2, Rocket League,
  Overwatch, and Rainbow Six Siege.
- Target: pre-match best-of match/series winner.
- State: research-only, zero units, never eligible for execution.

Titles are isolated. No result, rating, calibration parameter, or validation
cohort is pooled across games.

## Free source contract

The baseline backfill reads completed series from BO3's public website data
endpoint. It needs no account or API key. Every run writes a normalized JSONL,
the used team identity catalog, retrieval time, source/terms links, and content
hashes under `data/esports/<title>/`.

BO3 is replaceable: it does not publish a stable API contract. Schema failure
must stop the refresh rather than silently returning a partial "successful"
model. Reproduction requires an active attribution link to BO3.

Oracle's Elixir is the preferred later LoL enrichment source for patch, draft,
player, champion, and game-state features. Its rows are game-level; they cannot
enter the series model until games are grouped into series without allowing a
later game in a series to leak into the pre-series forecast.

Liquipedia is excluded. Its published free-plan eligibility rejects betting-
related projects. Riot's developer API is also excluded from the default path
because it requires signup and an API key.

## Baseline workflow

```bash
# Build normalized histories (CS2 is automatically clipped to the CS2 era and
# legacy game_version=1 CS:GO rows are excluded).
PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli esports-backfill \
  --all --from 2024-01-01 --to 2026-07-19

# Select K on validation data, grade the chronological locked test once, and
# write separate research artifacts.
PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli validate-esports \
  --titles lol cs2 --write-artifacts

# Discover current US contracts and begin prospective BBO evidence collection.
PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli polymarket-slate \
  --sport esports --date 2026-07-19

# Price only exact-identity, future match-winner contracts. Output remains a
# zero-unit research observation even when model probability exceeds the ask.
PYTHONPATH=src:. .venv/bin/python -m model_prediction.cli esports-forecast \
  --title lol --date 2026-07-19
```

Artifacts:

- `config/models/lol-neutral-series-elo-v1.json`
- `config/models/cs2-neutral-series-elo-v1.json`
- `outputs/latest/esports-baseline-validation.json`

## Baseline semantics

- Rating: neutral-site Elo, initial 1500.
- Model selection: K factor chosen by validation Brier score, log loss as
  tiebreaker.
- Confidence threshold: selected on validation by the flat-`-110` diagnostic
  unit result (`units_at_minus_110`), not by raw observation count. The
  earlier selector picked whichever threshold had the most observations,
  which always resolved to the loosest (0.0) threshold and made the gate a
  no-op; fixed 2026-07-20.
- Split: chronological 60% train / 20% validation / 20% locked test.
- Feature timing: each probability uses only completed prior series.
- Economics: not established until timestamp-valid pre-match executable asks
  have accumulated prospectively. Real per-side moneyline BBO capture started
  2026-07-20 (`data/odds/esports/<date>/`); real esports lines are frequently
  skewed (e.g. 70/30, 60/40) rather than flat `-110`, so the diagnostic units
  below overstate edge until enough executable-price history accumulates to
  replace the flat-stake assumption.

The artifact remains `research` even if its locked-test hit rate exceeds 60%.
Without roster continuity, market identity, and point-in-time price evidence,
that number is not a trading edge. `_metrics()` now reports `units_at_minus_110`
alongside `calls`/`hits` for both the unfiltered baseline and every swept
confidence-gate threshold, so the diagnostic profitability effect of gating is
visible directly rather than inferred from hit rate alone.

## Next feature order

### Shared

1. Effective-dated roster/stand-in state and roster continuity.
2. Tournament tier, region, stage, elimination status, best-of format, and
   LAN/online context.
3. Time-decayed form and inactivity regression.
4. Exact Polymarket-to-source team identity aliases with validity dates.
5. Prospective BBO, depth, spread, and price-age features in a separate market
   residual layer.

### League of Legends

1. Player-role priors and lineup strength.
2. Region/tournament strength partial pooling.
3. Patch-aware team form.
4. Opponent-adjusted early-game gold/xp, objectives, tempo, and side strength.
5. Draft model only at a declared post-draft horizon.

### Counter-Strike 2

1. Five-player lineup and stand-in effects.
2. Per-map ratings and veto simulation.
3. LAN/online, event tier, region, travel, and schedule load.
4. Map-pool and economy-patch regimes.
5. Shrunk round-level pistol, anti-eco, opening-duel, side, and clutch features.

## Failure policy

- Unresolved team identity: no forecast and no call.
- Unknown roster or new organization: widen uncertainty or no-call.
- Missing series format: baseline may report a probability, but it stays
  research-only and cannot be market-aligned automatically.
- Missing/stale BBO: no economic score and no execution.
- Source schema/hash drift: retain the last versioned snapshot, mark the refresh
  failed, and investigate before retraining.
