# Champion/Challenger Production Architecture

## Core Invariant & Philosophy

> **Every supported sport/market always has a production-serving champion.** Weak evidence increases replacement priority; it does not remove the market from production. Challengers are developed, evaluated, frozen, and promoted alongside the incumbent. Promotion is an atomic pointer change, and the former champion becomes the rollback model. Current weakness causes parallel rebuilding, not deletion.

---

## 1. Four Conceptual Slots per Supported Sport/Market

```text
PRODUCTION CHAMPION
    ↓
Current model serving predictions (fail-closed, 100% uptime requirement)

PROSPECTIVE CHALLENGER
    ↓
Frozen replacement candidate running alongside champion on identical decision contexts

RESEARCH
    ↓
Unfrozen experimental models / features / architectures

ROLLBACK
    ↓
Previous production champion (atomic one-command fallback)
```

---

## 2. Decoupled Lifecycle State Model

The framework strictly decouples **serving availability** from **evidence qualification**:

### Serving Status (`serving_status`)
- `production`: Active serving predictions for production pipelines.
- `shadow`: Running alongside production for prospective validation.
- `research`: Offline backtesting and experimental evaluation.
- `rollback`: Maintained as immediate fallback if champion degrades.
- `retired`: Decommissioned model.

### Evidence Status (`evidence_status`)
- `unverified`: Untested on real prospective cohort.
- `historical_only`: Backtest/OOF validated only.
- `predictively_qualified`: Proven lower LogLoss/Brier on out-of-sample data.
- `market_qualified`: Proven positive CLV and market edge on real quotes.
- `prospectively_qualified`: Evaluated and passed on frozen prospective decision windows.
- `degraded`: Known statistical deficiency or domain gap (e.g. NCAAF synthetic pricing, MLB totals negative CLV). **Does NOT remove model from serving**; elevates replacement priority.

### Replacement Priority (`replacement_priority`)
- `low`: Stable, well-calibrated champion.
- `medium`: Scheduled for prospective challenger evaluation.
- `high`: Known edge decay or negative CLV against live books.
- `critical`: Degraded evidence status requiring active parallel replacement.

---

## 3. Fail-Isolated Prospective Execution

The live prediction loop guarantees fail isolation:

```python
# Champion runs with fail-closed safety
champion_prediction = run_champion_or_fail_closed(context)

# Challenger failure NEVER impacts or blocks champion prediction
try:
    challenger_prediction = run_challenger(context)
except Exception as exc:
    record_challenger_failure(context, exc)
```

Both models receive the exact same immutable `DecisionContext` identified by a deterministic `decision_context_id` (e.g. `MLB_2026-09-12_NYY_BOS_TMINUS30`).

---

## 4. Settled-Picks Evaluation (No Backfill Contamination)

Challenger models are evaluated strictly against **already settled picks** from the model ledger:

1. **Identical Decision Windows**: Paired on exact `event_id` and decision horizon.
2. **Three Evaluation Dimensions**:
   - **Predictive**: $\Delta\text{LogLoss} \le 0$, $\Delta\text{Brier} \le 0$, $\Delta\text{ECE} \le 0.01$, calibration slope $\approx 1.0$.
   - **Market-Relative**: $\Delta\text{LogLoss}$ vs market closing price, CLV $> 0$, beat-close rate $> 52.4\%$.
   - **Stability**: Stratified by month, home/away, favorite/underdog, price tier.
3. **Preregistered Sample Requirements**:
   - Initial look: $N \ge 300$, $\ge 30$ distinct dates.
   - Full qualification: $N \ge 500$, $\ge 50$ distinct dates.
   - Statistical confidence: Bootstrap $P(\text{Challenger Better}) \ge 0.90$.

---

## 5. Promotion, Rollback, and Rejection Lifecycle

All lifecycle mutations are atomic YAML writes with SQLite audit logging:

```bash
# 1. Promote a qualified challenger (swaps champion, sets old champion to rollback)
python -m model_prediction.model_promotion promote \
    --new mlb-structural-v10-frozen \
    --sport MLB --market total \
    --approved-by "lead_quant" \
    --evidence "reports/mlb_v10_qualification.json"

# 2. Reject an unqualified challenger (clears challenger pointer; champion untouched)
python -m model_prediction.model_promotion reject \
    --challenger mlb-v9-candidate \
    --sport MLB --market moneyline \
    --reason "Bullpen variance worsened OOF logloss" \
    --approved-by "lead_quant"

# 3. Rollback champion to previous model
python -m model_prediction.model_promotion rollback \
    --sport MLB --market total

# 4. View full qualification registry across all 14 sports
python -m model_prediction.qualification_registry
```
