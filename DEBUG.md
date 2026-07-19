# DEBUG.md — System Audit & Repair Protocol

> **Run ALL checks in order. Fix every failure. Update input/TODO.md after each fix.**
> **Tests + ruff must pass before and after every change.**

Current verified failures are tracked in `docs/PROJECT_STATUS.md`. This protocol
must expose them; a clean exit caused by a weak check is itself a failure.

---

## PRE-FLIGHT

```bash
cd /Users/vincentc9002/Documents/Poly\ \&\ Kalshi/model\ prediction
PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check src/ tests/
```
FAIL if: any test fails or ruff reports errors. Fix before proceeding.

---

## CHECKS

### 1. AUDIT CHAIN INTEGRITY
```bash
PYTHONPATH=src:. .venv/bin/python -c "
import json, hashlib
from pathlib import Path
events = [json.loads(line) for line in Path('data/events.jsonl').read_text().splitlines() if line.strip()]
previous = '0' * 64
broken = []
for i, event in enumerate(events):
    payload = {key: value for key, value in event.items() if key != 'event_hash'}
    expected_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    ).hexdigest()
    if event.get('previous_hash') != previous or event.get('event_hash') != expected_hash:
        broken.append(i + 1)
    previous = event['event_hash']
if broken:
    print(f'CHAIN BROKEN: {len(broken)}; first lines={broken[:10]}')
else:
    print(f'CHAIN INTACT: {len(events)} events')
raise SystemExit(1 if broken else 0)
"
```
**PASS:** "CHAIN INTACT" printed. **FAIL:** Any break. If broken, trace to source.

### 2. ARTIFACT HASH VERIFICATION
```bash
PYTHONPATH=src:. .venv/bin/python -c "
import json, hashlib
from pathlib import Path
broken = []
for f in sorted(Path('config/models').glob('*.json')):
    data = json.loads(f.read_text())
    ah = data.pop('artifact_hash', None)
    if not ah:
        print(f'  NO HASH: {f.name}')
        broken.append(f.name)
        continue
    canonical = json.dumps(data, sort_keys=True, separators=(',',':'))
    expected = hashlib.sha256(canonical.encode()).hexdigest()
    status = 'OK' if ah == expected else 'BROKEN'
    print(f'  {status}: {f.name}')
    if status == 'BROKEN': broken.append(f.name)
print(f'BROKEN: {len(broken)}')
raise SystemExit(1 if broken else 0)
"
```
**PASS:** 0 broken. **FAIL:** Any count > 0. Regenerate broken artifacts.

### 3. MODULE IMPORT CHECK
```bash
PYTHONPATH=src:. .venv/bin/python -c "
import importlib, pkgutil, model_prediction
failures = []
modules = list(pkgutil.walk_packages(model_prediction.__path__, model_prediction.__name__ + '.'))
for module in modules:
    try:
        importlib.import_module(module.name)
    except Exception as error:
        failures.append((module.name, type(error).__name__, str(error)))
for failure in failures:
    print('IMPORT FAILED:', failure)
status_text = 'OK' if not failures else 'FAILED'
print(f'IMPORTS: {status_text}; modules={len(modules)}')
raise SystemExit(1 if failures else 0)
"
```
**PASS:** `IMPORTS: OK`. **FAIL:** Any import failure.

### 4. DATA INTEGRITY
```bash
PYTHONPATH=src:. .venv/bin/python -c "
import json
from pathlib import Path
for sport in ['mlb','nba','wnba','nfl']:
    path = Path(f'data/historical/{sport}_games_all.jsonl')
    if not path.exists(): print(f'{sport}: NO FILE'); continue
    games = [json.loads(l) for l in path.read_text().strip().split('\n')]
    no_score = sum(1 for g in games if g.get('home_score') is None or g.get('away_score') is None)
    no_date = sum(1 for g in games if not g.get('event_start_utc'))
    asg = sum(1 for g in games if 'All-Star' in str(g.get('home_team','')) or 'All-Star' in str(g.get('away_team','')))
    dupes = len(games) - len(set(g.get('event_id','') for g in games))
    print(f'{sport}: {len(games)} games, {no_score} no-score, {no_date} no-date, {asg} all-star, {dupes} dupes')
" 2>&1
```
**PASS:** 0 no-score, 0 no-date, 0 dupes. All-Star count tracked. **FAIL:** Any data corruption.

### 5. STALE REFERENCES
```bash
if stale=$(rg -n "0\.85p\+0\.075|30\.pick|CLAUDE_PROMPT" src --glob '*.py'); then
  echo "$stale"
  exit 1
else
  rg_status=$?
  test "$rg_status" -eq 1 || exit "$rg_status"
  echo "NO STALE REFERENCES"
fi
rg -n "legacy-measured-edge" src/model_prediction/cli.py config/model.yaml
```
**PASS:** First command prints `NO STALE REFERENCES`; second command shows only the intentional explicit rollback path. Versioned legacy rollback names and Python `dataclass(frozen=True)` are not stale references. **FAIL:** Active code references to deleted models outside the rollback path. Update or remove.

### 6. MODEL ARTIFACT VALIDATION
```bash
PYTHONPATH=src:. .venv/bin/python -c "
import json
from pathlib import Path
configs = list(Path('config/models').glob('*.json'))
yaml = Path('config/models/mlb-analyst-poisson-trend-v0.2.yaml')
print(f'JSON artifacts: {len(configs)}')
print(f'YAML formula: {\"present\" if yaml.exists() else \"MISSING\"}')
# Load and validate each
for f in configs:
    d = json.loads(f.read_text())
    version = d.get('model_version') or d.get('calibration_version') or d.get('base_model_version', '?')
    print(f'  {f.name}: {version}')
"
```
**PASS:** All expected artifacts present with valid versions. **FAIL:** Any missing.

### 7. FEATURE PIPELINE HEALTH
```bash
PYTHONPATH=src:. .venv/bin/python -c "
import yaml
from pathlib import Path
from model_prediction.models.learned_market import LearnedMarketArtifact
from model_prediction.features.park_factors import park_factor
from model_prediction.features.confidence_gate import evaluate
# Verify known values
pf = park_factor('Colorado Rockies')
assert abs(pf['park_factor'] - 1.12) < 0.05, f'Coors wrong: {pf}'
pf = park_factor('Seattle Mariners')
assert abs(pf['park_factor'] - 0.92) < 0.05, f'Seattle wrong: {pf}'
# Verify every threshold comes from the active hash-verified artifact
cfg = yaml.safe_load(Path('config/model.yaml').read_text())
for s in ['mlb','nba','wnba','nfl']:
    model_cfg = cfg['models'][s.upper()]
    artifact = LearnedMarketArtifact.load(model_cfg['production_artifact'])
    threshold = artifact.threshold('moneyline')
    assert evaluate(threshold, s, threshold=threshold).call
    assert not evaluate(threshold - 1e-6, s, threshold=threshold).call
print('FEATURE PIPELINE: OK')
"
```
**PASS:** "FEATURE PIPELINE: OK". **FAIL:** Any assertion error.

### 8. LEARNED ARTIFACT LOADING
```bash
PYTHONPATH=src:. .venv/bin/python -c "
import yaml
from pathlib import Path
from model_prediction.models.learned_market import LearnedMarketArtifact
cfg = yaml.safe_load(Path('config/model.yaml').read_text())
for sport in ['mlb','nba','wnba','nfl']:
    path = Path(cfg['models'][sport.upper()]['production_artifact'])
    artifact = LearnedMarketArtifact.load(path)
    model = artifact.raw['market_models']['moneyline']
    print(f'{sport}: version={artifact.version}, coeffs={model[\"coefficients\"]}, intercept={model[\"intercept\"]}, threshold={model[\"confidence_threshold\"]}, qualified={artifact.qualified}')
"
```
**PASS:** All four artifacts present with coefficients. **FAIL:** Any missing.

### 9. CONFIG CONSISTENCY
```bash
PYTHONPATH=src:. .venv/bin/python -c "
import yaml
from pathlib import Path
cfg = yaml.safe_load(Path('config/model.yaml').read_text())
print(f'Schema: {cfg.get(\"schema_version\",\"?\")}')
print(f'Active sports: {cfg.get(\"active_sports\",\"?\")}')
print(f'Weights hardcoded: {cfg.get(\"hardcoded_weights\",\"?\")}')
assert cfg.get('hardcoded_weights') == False or cfg.get('hardcoded_weights') is None, 'Hardcoded weights detected in config'
print('CONFIG: OK')
"
```
**PASS:** "CONFIG: OK". **FAIL:** Hardcoded weights or missing schema.

### 10. SPRING TRAINING FILTER
```bash
PYTHONPATH=src:. .venv/bin/python -c "
from zoneinfo import ZoneInfo
from model_prediction.domain import parse_utc
from model_prediction.features.base import FeatureStore
games = FeatureStore('data').load_games('mlb')
off_season = [g for g in games if parse_utc(g.event_start_utc).astimezone(ZoneInfo('America/New_York')).month not in range(4, 11)]
print(f'Off-season games in active modeling set: {len(off_season)}')
for g in off_season[:5]:
    print(f'  {g.event_start_utc[:10]} {g.away_team} @ {g.home_team}')
assert not off_season, 'Spring Training or off-season game reached FeatureStore modeling data'
"
```
**PASS:** 0 off-season games in active dataset. **FAIL:** Spring Training contaminating modeling.

---

## POST-SCAN DOCUMENTATION UPDATE

After every scan, update `docs/PROJECT_STATUS.md` and `input/TODO.md`:
1. Add any new failures found
2. Mark any fixed items as ✅ DONE
3. Update model metrics only from a report reproduced by the current checkout
4. Record scan timestamp in the "Scan record" section
5. Update `input/CHANGELOG.md` with a dated entry for meaningful changes

Do not append an unverified issue count.

---

## KNOWN FAILURE SIGNATURES

| Symptom | Check # | Root Cause | Fix |
|---|---|---|---|
| Audit chain broken | 1 | Missing event or corrupted hash | Rebuild from last good event |
| Artifact hash mismatch | 2 | Config was manually edited | Regenerate with valid hash |
| ImportError | 3 | Missing __init__.py or renamed module | Fix import path |
| Duplicate event IDs | 4 | Bootstrap ran twice on same date | Deduplicate data file |
| Stale references in code | 5 | Old model not fully removed | Remove/update references |
| Park factor wrong | 7 | Hardcoded table outdated | Update park_factors.py |
| Artifact missing | 8 | Production wiring incomplete | Regenerate from validation.py |
| Schema mismatch | 9 | Config manually edited | Restore from template |

---

## POST-CHECKS

```bash
PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q
.venv/bin/ruff check src/ tests/
```
Both must pass after every fix.
