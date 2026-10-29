# NFL joint market v3 research challenger — 2026-10-29

## Theory and evidence

`nfl-joint-market-v2` repaired cross-market coherence and improved proper scores
on 15 selected historical games, but its spread and total parents were v5.
The new v6 spread/total snapshots have observed-time input manifests and
projected-QB/injury checks. V3 binds the existing joint-score and dated
market-anchor calculation to those v6 artifact hashes, requires a complete
three-market decision packet, and records exact executable asks. It is a
**research challenger**, not an established improvement in accuracy or P&L.

The prior spread-mean intercept experiment failed (Brier 0.3004 to 0.3559 on
15 date-forward calls). V3 does not incorporate that correction. Its
moneyline market weight is fitted from earlier complete dates whose outcomes
had settled by the packet timestamp. No current-game result or postgame
price enters the forecast.

## Frozen pregame shadow forecast

The first packet was captured at **2026-10-29 20:18:29 UTC**, before Carolina
at Green Bay's scheduled 2026-10-30 00:15 UTC start. All three model snapshots
and two-sided quote records passed hash, timestamp, and exact-line checks.
The shadow output seals 38 prior settled moneyline training observations,
their fitted weight, parent/source hashes, contract identities, and its own
content hash. Both the packet and forecast are preserved in the repo for later scoring:

- Packet: `outputs/analysis/nfl_joint_market_v3_packets/401873023-98c82fbeec3c6e4ab8c7e7392fe87b39875c567d17ce9f4fbd09e2a8a88eeec8.json`
- Forecast: `outputs/analysis/nfl_joint_market_v3_20261029_401873023_committed.json`
- Versioned artifact: `config/models/challengers/nfl-joint-market-v3.json`

| Exact saved contract | V6 or v4 parent | V3 probability | Selected ask | Raw probability minus ask |
| --- | ---: | ---: | ---: | ---: |
| Green Bay moneyline | 52.51% | 64.09% | 64.25% | -0.16 pp |
| Carolina +3.5 | 52.44% | 48.31% | 50.00% | -1.69 pp |
| Over 47.5 | 52.32% | 52.94% | 48.50% | +4.44 pp |

The as-of market weight was **1.0**, so v3's moneyline probability equals the
saved two-sided no-vig moneyline price. That is no independent moneyline edge.
The total's positive raw gap is **not** an order recommendation: no candidate
selection, stake, fee, or correlation policy has been qualified.

## Settled-game backtest status

The settled NFL Flat ledger currently has 56 games, including 41 v5 spread
calls and 34 v5 total calls. **Zero settled games have v6 spread and total
parent packets**, so an exact v3 backtest has `n = 0`. Replacing old v5 inputs
with today's v6 source capture would leak future data; substituting v5 parents
would no longer evaluate the hash-pinned v3 artifact.

The valid historical analogue is v2, rerun on the same ledger: 15 settled
three-market packets, with moneyline Brier 0.24104 to 0.22354, spread 0.29610
to 0.25351, and total 0.23698 to 0.23641. These are **v2** results on selected
v5-era calls, not evidence that v3 improved accuracy or profitability.

## Reproduction and next evidence gate

```powershell
$env:PYTHONPATH='src;.'
.venv\Scripts\python scripts/forecast_nfl_joint_market_v3.py <pregame-packet.json> E:\model-prediction-runtime\ledgers\ledgers.db
.venv\Scripts\python scripts/evaluate_nfl_joint_market_v3.py outputs/analysis/nfl_joint_market_v3_20261029_401873023_committed.json data/processed/nfl/games.jsonl
```

The scorer currently returns `pending`. After a completed scoreboard record
with matching event, teams, start, and score exists, it will compare v3,
the parent model, and the exact two-sided no-vig market on each saved line.
Pushes are excluded from binary proper scores. Do not infer P&L from those
scores; a prespecified executable bet policy and sufficient unselected
prospective games are separate requirements.

Failure modes: v6's projected QB may differ from the actual starter; upstream
historical feature observation times are not fully certified; NFL market
quotes and total/spread score distributions may move after the decision;
38 selected moneyline training calls are a small calibration basis. The model
fails closed on missing or tampered packet, quote archive, parent artifact,
source code, unqualified v6 input, future snapshot, or unavailable prior
settlement. The v2 and v6 artifacts remain rollback versions. No champion,
ledger, or order path was changed.
