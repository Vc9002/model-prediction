# Model Improvement Process

Standard operating procedure for updating, improving, or adding features to any
sport model in the prediction pipeline. This document is a living guide —
deviate when the situation calls for it, but default to this.

---

## Pre-flight

```bash
cd "model prediction"
PYTHONPATH=src .venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check src/ tests/
```

Do not proceed if tests fail or ruff reports errors. Fix first.

---

## Step 1 — Data validation

Verify the data pipeline is healthy before touching any model:

```bash
PYTHONPATH=src .venv/bin/python -c "
import json
from pathlib import Path
for sport in ['mlb','nba','wnba','nfl']:
    path = Path(f'data/historical/{sport}_games_all.jsonl')
    games = [json.loads(l) for l in path.read_text().strip().split('\n') if l.strip()]
    no_score = sum(1 for g in games if g.get('home_score') is None)
    dupes = len(games) - len(set(g.get('event_id','') for g in games))
    print(f'{sport}: {len(games)} games, {no_score} no-score, {dupes} dupes')
"
```

**Pass:** 0 no-score, 0 duplicates across all sports.
**Fail:** Fix data corruption before proceeding.

Check artifact integrity:

```bash
PYTHONPATH=src .venv/bin/python -c "
import json, hashlib
from pathlib import Path
for f in sorted(Path('config/models').glob('*.json')):
    data = json.loads(f.read_text())
    ah = data.pop('artifact_hash', None)
    canonical = json.dumps(data, sort_keys=True, separators=(',',':'))
    expected = hashlib.sha256(canonical.encode()).hexdigest()
    print(f'  {\"OK\" if ah == expected else \"BROKEN\"}: {f.name}')
"
```

**Pass:** All artifacts OK.
**Fail:** Regenerate broken artifacts with `validate-models --write-artifacts`.

---

## Step 2 — Baseline model (control)

Run validation on the current production model to establish the baseline:

```bash
PYTHONPATH=src .venv/bin/python -m model_prediction.cli validate-models \
    --sports <sport> --write-artifacts
```

Record the locked holdout metrics:

| Metric | Baseline |
|--------|----------|
| Calls | |
| Hit rate | |
| Brier | |
| P&L (-110) | |
| Selectivity | |
| Threshold | |

This is the control. Every change must be compared against this.

---

## Step 3 — Feature ablation (if adding features)

If adding N new features, test each independently AND combined on top of the
baseline model. Never promote a variant without ablation evidence.

For 2 new features (A, B):

| Variant | Features |
|---------|----------|
| baseline | elo, trend |
| +A | elo, trend, A |
| +B | elo, trend, B |
| +A+B | elo, trend, A, B |

Add variants to `FEATURE_VARIANTS` in `validation.py`:

```python
"variant_name": ("elo_probability", "trend_gap", "new_feature"),
```

Add populations to `ValidationRow` and `build_walk_forward_rows`. Add the
variant to `variants_to_run` in `run_sport_validation`. Run validation.

Compare results:

| Variant | Calls | HR | Brier | P&L |
|---------|-------|-----|-------|-----|
| baseline | | | | |
| +A | | | | |
| +B | | | | |
| +A+B | | | | |

**Decision rules:**
- If any variant improves Brier AND P&L vs baseline → promote
- If a variant degrades both → drop it
- If results are mixed → use judgment (sample size, sport context)
- If a feature adds zero variance (same value for all rows) → it cannot be
  evaluated in backtest; only use in live forecasting

---

## Step 4 — Filter testing (if adding/modifying filters)

If the model has filters (rest-fatigue, weather, edge gate), test them:

1. Apply the filter to the baseline model's holdout calls
2. Compare filtered vs unfiltered:
   - Did calls decrease? By how much?
   - Did hit rate improve?
   - Did P&L improve?
3. Check for false positives (good bets incorrectly filtered)

Record:

| Filter | Calls Before | Calls After | HR Before | HR After | P&L Δ |
|--------|-------------|-------------|-----------|----------|-------|
| rest-fatigue | | | | | |
| edge gate | | | | | |

---

## Step 5 — Select production variant

Once the best variant is identified:

1. Update `build_production_artifact` in `validation.py` to point to it
2. Regenerate the artifact:
   ```bash
   PYTHONPATH=src .venv/bin/python -m model_prediction.cli validate-models \
       --sports <sport> --write-artifacts
   ```
3. Verify the new artifact:
   ```bash
   python3 -c "
   import json
   with open('config/models/<sport>-elo-trend-lr-v3.json') as f:
       d=json.load(f)
   q=d['qualification']
   print(f'{d[\"market_models\"][\"moneyline\"][\"feature_names\"]}')
   print(f'threshold={d[\"market_models\"][\"moneyline\"][\"confidence_threshold\"]}')
   print(f'qualified={q[\"qualified\"]} calls={q[\"calls\"]} hr={q[\"hit_rate\"]}')
   "
   ```
4. If any feature requires live API data (weather, pitchers), verify it's wired
   in `learned_forward.py` under the dynamic feature block
5. Run the daily pipeline to verify no import errors or data gaps
6. Check the dashboard matrix shows the new model as qualified

---

## Step 6 — Commit

```bash
git add config/models/<sport>-elo-trend-lr-v3.json \
        outputs/latest/learned-model-validation.json \
        src/model_prediction/validation.py \
        src/model_prediction/learned_forward.py \
        # any new feature modules
git commit -m "<sport>: <summary of change>

Feature ablation:
  baseline:  Nc @ HR%  +P&L  Brier
  +A:        Nc @ HR%  +P&L  Brier
  +B:        Nc @ HR%  +P&L  Brier
  +A+B:      Nc @ HR%  +P&L  Brier"
```

---

## Post-improvement checklist

- [ ] Tests pass (161+)
- [ ] Artifact hashes valid
- [ ] Dashboard matrix shows qualified
- [ ] Daily pipeline runs without errors
- [ ] New features have real variance in backtest (not all zeros)
- [ ] Live features wired in `learned_forward.py`
- [ ] Feature ablation recorded in commit message

---

## Example: MLB feature ablation (2026-07-18)

Added park_factor, weather_factor, and pitcher_era_gap on top of elo+trend
baseline:

| Variant | Calls | HR | Brier | P&L |
|---------|-------|-----|-------|-----|
| elo+trend (baseline) | 112 | 57.1% | 0.246 | +10.18U |
| + park | 93 | 60.2% | 0.239 | +13.91U |
| + weather | 90 | 61.1% | 0.237 | +15.00U |
| + pitcher | 91 | 61.5% | 0.236 | +15.91U |
| **+ all three (production)** | **92** | **62.0%** | **0.235** | **+16.82U** |

All three features independently improved Brier and P&L. Combined variant
promoted to production.

---

## Step 7 — Results & Recommendations

After the ablation and filter tests complete, produce a readout with two
sections — data review, then suggestion. Do not proceed to implementation
until the user confirms.

### 7a — Data Review & Summary

One table showing every variant tested, ordered by P&L (best first).
Include before/after metrics so the user can see what changed:

| Variant | Calls | HR | Brier | P&L | Δ vs baseline |
|---------|-------|-----|-------|-----|---------------|
| +A+B | 92 | 62.0% | 0.235 | +16.82U | +6.64U |
| +B | 91 | 61.5% | 0.236 | +15.91U | +5.73U |
| +A | 90 | 61.1% | 0.237 | +15.00U | +4.82U |
| baseline | 112 | 57.1% | 0.246 | +10.18U | — |

### 7b — Key observations

Bullet-point the most important observations from the data. Be direct.
These support the suggestion in 7c. Examples:

- **Park factor is the single largest improvement** — +3.73U on 19 fewer calls.
  Coors Field and T-Mobile Park effects are real and the model now captures them.
- **Weather degrades slightly in backtest** — it has zero variance (all 1.0).
  Only useful in live forecasting via Open-Meteo.
- **Pitcher ERA gap stops Phoenix-like collapses** — coefficient -0.018 means
  teams with better pitching win more.
- **Combined model is the clear winner** — improves every metric vs baseline.

### 7c — Suggestion

One of these verdicts, with rationale:

| Verdict | When | Action |
|---------|------|--------|
| **Promote** | Variant improves Brier AND P&L vs baseline | Switch production variant, regenerate artifact, commit |
| **Hold** | Results are mixed or sample is too small | Keep as research variant, re-evaluate with more data |
| **Drop** | Variant degrades both Brier and P&L | Remove from variants, do not promote |
| **Live only** | Feature needs real-time data (weather, pitchers) | Keep in `learned_forward.py` for daily forecasts, do not add to production artifact |

Always include the rationale. Example:

> **Suggestion: Promote the combined variant (elo+trend+park+weather+pitcher).**
> Every feature independently improves Brier and P&L. The combined variant
> produces the best hit rate (62.0%) and P&L (+16.82U) on the locked holdout.
> Weather and pitcher gap are live-only enhancements that do not degrade backtest
> performance. No feature should be removed.

If the recommendation is to hold or drop, explain what would change the verdict:

> **Suggestion: Hold.** July sample is only 28 games across 3 teams.
> Re-evaluate after 2 more weeks of data. If July P&L stays positive, promote.

---

## ⚠️  User Confirmation Required

**Do not implement any changes until the user explicitly confirms.**

The readout in Step 7 is a proposal — it presents findings and a recommendation,
but the user has final authority. Do not:

- Switch production variants
- Regenerate artifacts with `--write-artifacts`
- Commit model changes
- Modify thresholds or filters

Until the user says "promote it," "apply the changes," or equivalent.

The workflow is:

1. Run ablation/filter tests
2. **Data review & summary** — present the results table with before/after metrics
3. **Suggestion** — make a clear recommendation (promote/hold/drop/live-only)
4. **Wait for user confirmation**
5. Only then execute the change
