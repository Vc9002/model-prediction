# Free/Open Data Architecture

The rebuild must train, backtest, predict, run shadow mode, settle, and calculate
CLV without a paid sports-data subscription. A missing free feature is explicit
missingness or an unavailable prediction; it is never fabricated.

## Provider boundary

Model and feature modules do not call external APIs. They consume normalized,
versioned Polars tables produced through:

```text
pipeline -> SportsDataProvider -> shared HTTP/cache layer -> external source
```

Every captured response records provider, endpoint family, normalized request
parameters, request/retrieval/observation times, HTTP status, exact byte hash,
schema hash, source grade, and source version. Exact response bytes are written
before parsing. The blob is content-addressed; each later observation receives a
separate append-only manifest even when the bytes have not changed.

Provider states are `AVAILABLE`, `DEGRADED`, `STALE`, or `UNAVAILABLE`. An empty
DataFrame is not used to impersonate an unavailable source.

## WNBA v1

- Current schedule: ESPN Site v2 endpoint cataloged by SportsDataverse.
- Historical schedule, team box, player box, rosters, and play-by-play:
  versioned SportsDataverse release assets.
- Advanced stats.nba.com/stats.wnba.com data is optional and is not required by
  the core provider.
- Markets remain a separate Polymarket provider/decision-stage concern.

`sportsdataverse==0.0.72` is pinned as the tested catalog/parser baseline. The
rebuild does not eagerly import its package-wide namespace: that release imports
thousands of cross-sport functions and is slow on a cold interpreter. Historical
evidence is fetched as exact release bytes through our HTTPX transport so an
external cache or parser cannot become the untracked source of truth.

## Point-in-time limitation

A historical release downloaded today was observed today. Its game date does not
prove that exact row or correction was available at an earlier prediction time.
Normalized rows therefore retain `availability_basis = capture_time_only` and
the real capture timestamp. They are valid inputs only for decisions after that
timestamp unless a replay-safe archived vintage supplies stronger evidence.

This allows structural research and future prospective operation while blocking
false retrospective qualification. Do not rewrite `observed_at_utc` to a game
date or inferred publication time.

## Commands

```bash
rebuild-data backfill --sport wnba --season 2024 --resume
rebuild-data audit --sport wnba --season 2024
```

Raw and normalized runtime files remain under ignored `data/rebuild/**` paths.
The audit reports duplicates, missing identities/scores/boxscores, timestamp
violations, and the current qualification limitation.

## Test policy

Normal tests are fixture-only and require no network. A tiny live health check is
opt-in:

```bash
REBUILD_LIVE_API_TESTS=1 pytest -q tests/rebuild/test_live_provider_health.py
```

The scheduled provider-health workflow runs separately from deterministic PR
CI. External downtime must not make ordinary unit tests nondeterministic.

## Licensing gate

Open access is not the same as permission for commercial or trading use. A source
with noncommercial, share-alike, redistribution, or derived-analysis restrictions
must remain policy-blocked until its intended use is reviewed and approved. A
public mirror does not cure an upstream licensing restriction.
