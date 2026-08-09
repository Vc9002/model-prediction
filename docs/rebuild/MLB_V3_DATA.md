# MLB v3 free-first data foundation

MLB v3 is a separate research lane. It does not modify, evaluate, consume, or
replace the frozen `mlb_moneyline_v2` candidate. The first milestone contains
data providers and PIT contracts only; it contains no model and cannot submit
orders.

## Source and licensing boundary

Public reachability is not commercial clearance. Every raw response records
`commercial_use_status` and `production_allowed`. The current providers are
research/shadow-only:

| Provider | Use status | Production allowed |
|---|---|---:|
| MLB Stats API | terms review required | no |
| Baseball Savant / Statcast | terms review required | no |
| Open-Meteo forecasts / previous runs | attribution and terms review required | no |
| SportsDataverse release assets / ESPN endpoints | external asset terms unresolved | no |

The MIT license on client code does not grant rights to third-party data.
Economic or production use fails closed until a provider-specific review is
documented and its metadata is deliberately changed.

## Point-in-time boundary

All captures preserve the exact raw bytes, content hash, source record/event
identity, retrieval time, and observation time before parsing. Historical APIs
usually expose their latest representation, not a replayable historical
vintage. Those rows therefore carry `availability_basis = capture_time_only`.
They can support structural research and future prospective collection, but
they do not prove that a probable pitcher, lineup, transaction, Statcast row,
or forecast was known at an earlier prediction horizon.

Authoritative event identity is `game_pk`. The canonical identity also includes
the market period, and schedule rows preserve doubleheader number,
postponement, reschedule, resume, and original-date fields. Team plus date is
never an identity key.

## Commands

Only the explicit v3 research lane is exposed:

```bash
rebuild-data backfill \
  --sport mlb \
  --version v3 \
  --provider mlb_stats \
  --start 2026-08-01 \
  --end 2026-08-07 \
  --table schedule \
  --resume

rebuild-data backfill \
  --sport mlb \
  --version v3 \
  --provider statcast \
  --start 2026-08-01 \
  --end 2026-08-07 \
  --resume

rebuild-data audit --sport mlb --version v3 --season 2026
```

Statcast is automatically partitioned into requests of at most seven days.
The audit reports `NO_DATA` when nothing has been captured and `DEGRADED` when
core starter or Statcast coverage is absent. It never reports an empty dataset
as healthy.

## Deliberately not included

- no MLB v3 model, calibration, candidate, or prospective test;
- no v2 sealed-test or shadow-ledger reads;
- no realized-weather historical backfill;
- no market or execution integration;
- no claim that capture-time historical snapshots are retrospective PIT data;
- no production-cleared provider.
