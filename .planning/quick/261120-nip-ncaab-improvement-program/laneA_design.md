# Lane A design: v7 identity, NCAAB artifact hash, served-probability readers and pre-tip close

This is a read-only design. Repo HEAD is c0cc1b6a, working tree as of 2026-11-20. Line numbers refer to the current working tree.
Everything was checked against code and the live runtime, read-only: `ledgers.db` and `market_quotes.db` opened `mode=ro`, state files only read.

## 0. Corrections to the brief (verified; they change the design)

1. **`model_probability` is two different numbers.**
   - The SQLite column `ledger_records.model_probability` holds the RAW value. `ledger.py:1163` writes `model_probability_raw or model_probability`.
   - The payload field `decision_payload_json.model_probability` holds the SERVED value. `college_basketball_forward.py:917` passes `model_probability=probability`, and `ledger.py:955` copies it into the payload.
   - `MultiSportPickLedger.rows()` returns the payload, and fills a field from the column only when the payload lacks it (`runtime_ledger_store.py:509-531`).
   - **So the calibrate step fits on SERVED values, not raw.** `calibration_pipeline.py:161-173` reads `model_probability` first from `rows()`.
   - Live count: 222 settled NCAAB rows since 11-11 were priced under a non-identity calibrator and have payload `model_probability` (served) different from raw. They break down as beta 142, temperature 77, platt 3.
   - The auto-buyer states the intended contract at `auto_executor.py:2159-2163` ("pick ledgers stay raw ... calibrated values there would be calibrated again"). NCAAB's payload breaks that contract.
2. **NCAAB calibrators are refit on every Daily run, not by the calibrate job.**
   - `cli/daily.py:1552-1561` calls `run_full_calibration_pipeline(update_artifacts=True)` after forecast and settlement, 8 times a day.
   - The standalone `calibrate --all` task has no `--update-artifacts`, so it is report-only.
   - The NCAAB forward loads `{mv}.{kind}_calibrator.json` with `load_calibrator_artifact` (`college_basketball_forward.py:636-643`). That path has no serving guard: no n>=100 floor and no Brier-gain requirement.
   - Result: the served spread/moneyline calibrator changes from day to day. Spread rows ran under beta (11-11..11-14), temperature (11-14/15), platt (11-15), identity (11-16/17), beta (11-18..11-20) and identity again (now, n=394/219).
   - "Behaviour" for the calibrator therefore means "the same fit the v6 group would have produced", not a fixed transform.
3. **The CLV root cause is proven, and it is capture cadence, not the lookup.**
   - All 339 settled NCAAB rows with a stored close: the close snapshot was observed at or before the row's own decision time. 0 of 339 have a later snapshot.
   - 304 of them have the same price. The other 35 differ only because the decision used the indicative slate price.
   - NCAAB snapshots are written only by the Daily runs (2026-11-19: 05:37, 11:01, 14:00, 17:00, 20:02, 21:01, 23:33, 01:30 UTC).
   - The surviving settled row is the last pre-tip re-forecast, which priced off the same capture that `closing_snapshot` returns. The median is 58 min before tip.
   - `market_quotes.db` has no writer in `src`. `scripts/backfill_multi_sport_market_quotes.py` fills it from those same jsonl files, so it is not an independent close source.
4. **`margin_engine: v7` is the only uncommitted `config/model.yaml` hunk** (lines 720-723).
5. **No `ncaab-spread-v7` id exists anywhere yet.** I checked src, config, tests, the ledger and the calibrator dir.
   - `models/ncaab_v7_shadow.py` and `ncaab_v7_multiseason.py` use other names.
   - No ledger row has a model_id like `%v7%` for NCAAB.

## Risk ranking (highest first)

| # | Change | Risk | Why |
|---|---|---|---|
| 1 | v7 id plus promotion | High | It touches serving dispatch, registry, promotion, both buyer layers, the paper trial and calibrator routing. A partial apply between Daily runs either raises (champion/version check) or silently serves the v6 engine. |
| 3b | Calibrator fit source raw instead of served | Medium (behaviour change) | It changes future calibrators. **Operator decision.** |
| 3c-ii | Pre-tip close capture in the 15-min buyer worker | Medium | New exchange traffic (BBO capture is 429-limited). **Operator decision.** |
| 2 | `model_artifact_hash` for NCAAB | Low | NCAAB bypasses `evaluate_eligibility`, so the `not request.model_artifact_hash` gates at `eligibility.py:83/170` never apply. The hash only adds provenance and evidence-dedupe keys. |
| 3a / 3c-i | Readers use the served probability; CLV not counted when the close is not after the decision | Low | Accounting only, never a decision input. |

---

## Change 1: record v7 as `ncaab-spread-v7` (moneyline and spread), with `ncaab-spread-v6` as rollback

### Semantics (decision)

- **The champion id selects the engine.** `ncaab-spread-v7` means the v6 artifact plus margin_engine "v7". `ncaab-spread-v6` means margin_engine "v6".
- `models.NCAAB.margin_engine` in model.yaml is **retired**. If the key is present, the forward raises `ValueError` (fail closed), so nobody can believe that `margin_engine: v6` reverts anything.
- Rollback is `model_promotion rollback` for both markets, which returns to the v6 engine.
- Moneyline and spread price off one model instance. If their champions disagree, the forward raises. This follows the `cfb_model()` precedent at `models/college_football.py:146-168`.
- Non-D1 and fallback games under the v7 id are still priced by v6 math (per-game fallback). `feature_basis.margin_engine` stays the per-row truth, so cohort splits keep using it.
- Existing rows are not relabelled.

### Code edits

**A. `src/model_prediction/models/ncaab_spread_v6.py`**
- Line 54, after `NCAAB_SPREAD_V6_VERSION = "ncaab-spread-v6"`: add `NCAAB_SPREAD_V7_VERSION = "ncaab-spread-v7"`.
- In `__init__`, after line 267 (`self.margin_engine = margin_engine`): add `self.model_artifact_hash = ""  # set by the production factories; stamped on ledger rows`.
- Replace the factory at lines 701-703:

```python
def ncaab_spread_v6_model() -> NCAABSpreadModelV6:
    """Production factory: the served parameters are the hash-verified artifact's, nothing else."""
    artifact = load_verified_artifact(ARTIFACT_PATH)
    model = NCAABSpreadModelV6.from_artifact(artifact)
    model.model_artifact_hash = str(artifact["artifact_hash"])
    return model


def ncaab_spread_v7_artifact_hash(base_artifact_hash: str) -> str:
    """Identity of ncaab-spread-v7: the v6 artifact it reuses plus the frozen v7 engine (pin 2)."""
    from .ncaab_v7_serving import v7_config_hash

    payload = {
        "model_id": NCAAB_SPREAD_V7_VERSION,
        "base_artifact_hash": base_artifact_hash,
        "v7_config_hash": v7_config_hash(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def ncaab_spread_v7_model() -> NCAABSpreadModelV6:
    """ncaab-spread-v7: the unchanged, hash-verified v6 artifact with the v7 margin engine on.

    Same parameters as v6 by construction (nothing is re-read or re-fit); only the id, the engine
    and the recorded model hash differ. Promoted 2026-11-21 to give the engine that served under
    the v6 id since 2026-11-16 its own identity; rollback is ncaab-spread-v6 (engine v6).
    """
    artifact = load_verified_artifact(ARTIFACT_PATH)
    model = NCAABSpreadModelV6.from_artifact(artifact, version=NCAAB_SPREAD_V7_VERSION, margin_engine="v7")
    model.model_artifact_hash = ncaab_spread_v7_artifact_hash(str(artifact["artifact_hash"]))
    return model
```

- `from_artifact` already applies `params.update(overrides)`, and `__init__` accepts both `version` and `margin_engine`. The artifact file is untouched, so the "every artifact key read" pin in `tests/test_ncaab_v6.py` is unaffected.

**B. `src/model_prediction/models/ncaab_v7_serving.py`**: add after line 58 (`TAIL_KNOT`)

```python
def v7_config_hash(archive_path: Path | str | None = None) -> str:
    """Hash of everything that fixes v7's output besides the v6 artifact: the frozen combiner and the archive.

    The archive's meta line carries games_sha256 and every season's conference map. A missing archive
    hashes as "unavailable" (v7 then falls back to v6 for the slate anyway); this never raises.
    """
    path = ARCHIVE_PATH if archive_path is None else Path(archive_path)
    try:
        with gzip.open(path, "rb") as fh:
            meta_sha = hashlib.sha256(fh.readline()).hexdigest()
    except (OSError, EOFError):  # truncated gzip raises EOFError; must not kill the slate
        meta_sha = "unavailable"
    payload = {
        "variant": V7_VARIANT,
        "coef": list(V7_COEF),
        "scales": list(V7_SCALES),
        "bucket_cuts": list(BUCKET_CUTS),
        "tail_knot": TAIL_KNOT,
        "archive_meta_sha256": meta_sha,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
```

Note on the module docstring at line 1-5 ("Off by default ... Nothing here is registered, promoted"): change it to "Served by ncaab-spread-v7 (promoted 2026-11-21)".

**C. `src/model_prediction/models/ncaab_total_v6.py`**
- After line 201 (`self.version = version`), add `self.model_artifact_hash = ""`.
- The factory at 570-572 becomes:
  `artifact = load_verified_artifact(ARTIFACT_PATH); model = NCAABTotalModelV6.from_artifact(artifact); model.model_artifact_hash = str(artifact["artifact_hash"]); return model`

**D. `src/model_prediction/college_basketball_forward.py`**
- Line 26: `from .models.ncaab_spread_v6 import NCAAB_SPREAD_V7_VERSION, ncaab_spread_v6_model, ncaab_spread_v7_model`
- Lines 30-32: keep them as they are. `tests/test_ncaab_v6_review_fixes.py:247` pins `ncaab_spread_model is ncaab_spread_v6_model`, and tests patch `ncaab_model` / `ncaab_spread_model`. The v7 hook is the module-global name `ncaab_spread_v7_model`, which tests can monkeypatch.
- Add after `_main_sample_eligible` (line 43):

```python
def _ncaab_spread_champion_id() -> str:
    """The one spread-model id serving NCAAB moneyline AND spread (both price off one instance).

    Serving follows the champion map, so model_promotion promote/rollback alone switches the margin
    engine (ncaab-spread-v7 = v7, ncaab-spread-v6 = v6); see cfb_model() for the NCAAF precedent.
    """
    registry = ProductionModelRegistry.load(PROJECT_ROOT)
    ids = {m: getattr(registry.champion("NCAAB", m), "model_id", None) for m in ("moneyline", "spread")}
    if ids["moneyline"] != ids["spread"]:
        raise ValueError(f"NCAAB moneyline and spread champions must name one spread model, got {ids}")
    return str(ids["spread"])
```

- Lines 349-353 (spread model construction) become:

```python
    if "margin_engine" in (config.get("models", {}).get("NCAAB") or {}):
        raise ValueError(
            "models.NCAAB.margin_engine is retired: the NCAAB spread champion in config/production.yaml "
            "selects the engine (ncaab-spread-v7 = v7, ncaab-spread-v6 = v6)"
        )
    if _ncaab_spread_champion_id() == NCAAB_SPREAD_V7_VERSION:
        spread_model = ncaab_spread_v7_model()
    else:
        spread_model = (
            ncaab_spread_model()
            if ncaab_model in (ncaab_spread_v5_model, ncaab_spread_v6_model)
            else ncaab_model()
        )
```

- Keep the champion/version check at 385-395 unchanged. It now also proves that the dispatched model's version equals the champion.
- Delete lines 397-400 (the `margin_engine` setter and its comment).
- Calibrator lookup at 634-643 is unchanged. It now resolves `ncaab-spread-v7.{moneyline,spread}_calibrator.json` (see "Calibrators" below).
- The change 2 edits in the same file are listed under Change 2.

**E. `src/model_prediction/production_registry.py:71`**: add `"ncaab-spread-v7": "model_prediction.models.ncaab_spread_v6:ncaab_spread_v7_model",` after the v6 line.

**F. `src/model_prediction/sports/naming.py:162-169`**
- v6 description becomes "NCAAB moneyline+spread rollback (v6 margin engine)."
- Add after it:

```python
    "ncaab-spread-v7": ModelDescriptor(
        model_id="ncaab-spread-v7",
        sport="NCAAB",
        market="spread",
        standard_name="ncaab-spread-spread-v7",
        display_name="NCAAB Spread (Spread v7)",
        description="NCAAB moneyline+spread champion: v6 artifact with the frozen v7 multi-season margin engine.",
    ),
```

**G. `config/production.yaml`** (pre-promotion, hand-added entry; the champion flip itself is done by the promote command)
- Insert after line 766 (end of the `ncaab-spread-v6` entry):

```yaml
  - model_id: ncaab-spread-v7
    sport: NCAAB
    market: multi
    implementation: code_backed_model
    entry: model_prediction.models.ncaab_spread_v6:ncaab_spread_v7_model
    enabled: true
    serving_status: production
    evidence_status: historical_only
    replacement_priority: low
    rollback_model: ncaab-spread-v6
```

- `allowed_models` must mirror `models` order exactly (`production_registry.py:316-321`). Insert `  - ncaab-spread-v7` after line 1181 (`  - ncaab-spread-v6`).
- `artifact_map` is not touched, because the entry is code_backed.
- `evidence_status: historical_only` follows the house convention for recent promotions without forward evidence (NCAAF v5, NFL v7). For NCAAB it is display-only: evidence-gated downgrades are NBA-only (`cli/forecast.py:3845`, `:4219`).

**H. `config/model.yaml`** (NCAAB block)
- Line 706: `active_production_version: ncaab-spread-v7`.
- Line 708: `rollback_production_version: ncaab-spread-v6`.
- Why this matters: `cmd_execute` (`cli/commands.py:1190-1199`) refuses manual research orders unless `row.model_version == active_production_version`, and the dashboard reads the field (`dashboard/common.py:587`, `matrix.py:268`, `evidence.py:983`).
- Delete lines 720-723 (comment plus `margin_engine: v7`). Replace them with:
  `    # Margin engine follows the production.yaml champion: ncaab-spread-v7 = v7 engine (promoted 2026-11-21), rollback ncaab-spread-v6 = v6 engine.`
- `model_promotion rollback` does not edit model.yaml. After a rollback, set these two lines back by hand (dashboard label and the manual-order gate).

**I. Buyer: code constant** in `src/model_prediction/portfolio/auto_executor.py`
- Line 151, after `"ncaab-spread-v6",`: add `"ncaab-spread-v7",` to `EXPLICIT_BLACKLIST_MODELS`.
- Line 422-431, trial bypass. Accept the trial's configured id. Leave the live bucket (434-441) v6-only, unchanged.

```python
NCAAB_SPREAD_TRIAL_MODEL_IDS: tuple[str, ...] = ("ncaab-spread-v6", "ncaab-spread-v7")

def _is_paper_ncaab_spread_v6_trial(row: dict[str, Any], config: AutoExecutionConfig) -> bool:
    """Allow one quarantined model through only in the isolated paper run."""
    model_id = str(row.get("model_id") or row.get("model_version") or "")
    return (
        config.paper_ncaab_spread_v6_trial
        and config.mode == "paper"
        and not config.execute_live
        and model_id in NCAAB_SPREAD_TRIAL_MODEL_IDS
        and model_id in config.whitelisted_models   # the trial whitelists exactly its configured id
        and _auto_buyer_sport_market(row) == ("ncaab", "spread")
    )
```

**J. Paper trial: `src/model_prediction/portfolio/ncaab_paper_trial.py`**
- Import `NCAAB_SPREAD_TRIAL_MODEL_IDS` from `.auto_executor`.
- Add:

```python
_DEFAULT_TRIAL_MODEL_ID = "ncaab-spread-v7"

def _trial_model_id(settings: dict[str, Any]) -> str:
    model_id = settings.get("model_id")
    if model_id not in NCAAB_SPREAD_TRIAL_MODEL_IDS or settings.get("market_type") != "spread":
        raise ValueError("NCAAB paper trial config must name ncaab-spread-v6 or ncaab-spread-v7, spread")
    return str(model_id)
```

- Line 42 default dict: `"model_id": _DEFAULT_TRIAL_MODEL_ID`.
- Lines 44-45 become `_trial_model_id(settings)`.
- `eligible_trial_pick` (64-67) gains a keyword: `model_id: str = _DEFAULT_TRIAL_MODEL_ID`. Line 66 becomes `... != model_id`.
- `run_ncaab_spread_paper_trial`: lines 231-232 become `model_id = _trial_model_id(settings)`. Line 259 becomes `eligible_trial_pick(row, min_edge=min_edge, max_edge=max_edge, model_id=model_id)`. Line 275 becomes `whitelisted_models=(model_id,)`.
- Keep `trial_root` (`paper_trials/ncaab_spread_v6`) and the order_id prefix `PAPER-NCAAB-V6-`. Existing tests and dedupe history reference them; dedupe is by game-market keys, not the prefix.
- `config/auto_buyer_ncaab_spread_paper_trial.json` (repo seed copy, read by no code): set `"model_id": "ncaab-spread-v7"`.

**Why the trial buys at all, and why that is id-independent.**
- The buyer's decision gate rejects `PAPER_CALL_*` reason codes (`auto_executor.py:1877-1881`).
- NCAAB Flat rows carry `reason_code='FLAT_CALL'`, because `ledger.py:797` rewrites every Flat append to `FLAT_CALL`, and that rewrite is not keyed on the model. Live: 2,183 of 2,183 NCAAB rows since 11-16 are `FLAT_CALL` / `CALL` / flat.
- `read_picks` and `read_flat_picks` do not filter on `active_production_version`. Only `dashboard/common.py:_manual_research_eligibility` reads it, and only for the manual-order button.
- So once the trial bypass and whitelist accept the id, v7 rows follow the identical path.
- Add one test through the real `AutoPolymarketBuyer.evaluate_and_execute` with quote/market fakes. Feed identical v6 and v7 rows under v6 and v7 trial configs, and assert identical rejection counters and preview counts. The existing :96 test mocks the buyer.

**Decision taken: the trial accepts only v7 going forward** (config `model_id: ncaab-spread-v7`). The code accepts either id, so a rollback is one versioned state edit back to v6.
- Transition gap: v6-labelled open rows from the 00:30 ET Daily are not bought after the flip.
- In the 01:15-06:00 ET apply window, no NCAAB game is inside `max_event_lookahead_hours`, and the 06:00 Daily supersedes those rows. The gap is therefore empty in practice, except a slot `restore_unreplaced_picks` keeps open (slots are keyed (league, event, market), not model).
- Consequence: **auto-quarantine history restarts.** The breaker groups paper fills by model_id (`auto_executor.py:1237-1276`: 15-fill minimum, ROI < -10% over the last 30).
  - v6 is at -0.8% over its last 30 settled fills, so it is not quarantined today and nothing changes immediately.
  - v7 cannot trip until it has 15 settled fills.
  - Operator decision if that matters.

### Calibrators

- **Day 1, bit-identical.** Right before the promote, after the 00:30 ET Daily has finished its calibration step at the end of that run, copy byte-for-byte:
  - `E:\MP\data\calibration\models\ncaab-spread-v6.spread_calibrator.json` to `...\ncaab-spread-v7.spread_calibrator.json`
  - `...\ncaab-spread-v6.moneyline_calibrator.json` to `...\ncaab-spread-v7.moneyline_calibrator.json`
- Both are currently identity (n=394 / 219). The loader ignores the file's `model_version` field (`calibration.py:638-661`), so served probabilities and `calibration_artifact_hash` stay identical.
- Do not commit them. They are runtime output that the Daily rewrites.
- **What happens next, without a code change.** Each Daily's calibration step groups rows by `model_version`.
  - The v7 group has no settled rows until the first v7-labelled games settle (n < 20 gives no record, so the copy persists). v7 has no esports-style revoke.
  - At about 30-50 settled spread rows a day, the v7 group passes n >= 20 within one or two Daily runs. It is then refit on about 20-40 rows and served unguarded. That is noisier than today's 394-row fit and is **not** behaviour-preserving.
  - Meanwhile the v6 group keeps refitting on frozen v6-labelled rows, which is harmless and keeps rollback calibrated.
- **Options (operator decision; I recommend A for this lane):**
  - **A. Exact continuity (implement now).** v7 fits on every v6-labelled row plus every v7-labelled row. This is the exact population the v6 group would have had without the rename, in the same ledger order, so the next fits equal the counterfactual. Edit in `src/model_prediction/calibration_pipeline.py`:
    - After line 41, add:

      ```python
      # A renamed model keeps fitting on its predecessor's settled rows, so the rename alone cannot move its
      # calibrator: ncaab-spread-v7 (2026-11-21) is the v6 artifact plus the v7 engine that served as v6 since 11-16.
      CALIBRATION_POOL_SOURCES: dict[str, tuple[str, ...]] = {"ncaab-spread-v7": ("ncaab-spread-v6",)}
      ```

    - Lines 195-198 become:

      ```python
      for target in (model_version, *(t for t, src in CALIBRATION_POOL_SOURCES.items() if model_version in src)):
          key = (sport, market, target, tier_name)
          if key not in results:
              results[key] = ([], [])
          results[key][0].append(prob)
          results[key][1].append(outcome)
      ```

    - Line 533: `"fit_model_versions": [r.model_version, *CALIBRATION_POOL_SOURCES.get(r.model_version, ())],`. This is honest provenance.
    - Side effect: `serving_guard_reason` (`:661-667`) now refuses the pooled artifact. Only `calibrate_for_serving` in the buyer uses the guard. The trial's data_root (`runtime/paper_trials/ncaab_spread_v6`) has no `calibration/` dir, and the main buyer blocks NCAAB, so served values are unchanged.
  - **B. Engine-pure.** Pool v6-labelled rows whose `model_input_snapshot_json.features.margin_engine == "v7"` (settled now: 153 spread, 47 moneyline). This is the right long-run pool, but it changes the next fit, because the 160 v6-engine spread rows drop out. It costs about 8 lines (`json.loads` per NCAAB row).
  - **C. Do nothing.** v7 is refit on 20+ v7-only rows within about a day.

### Promotion commands

Run from E:\MP in PowerShell, after the code commit; the promote records HEAD as `git_sha`. Run moneyline then spread back-to-back, with no Daily in between.

```powershell
$env:MODEL_PREDICTION_RUNTIME_ROOT = 'E:/model-prediction-runtime'
python -m model_prediction.model_promotion promote --new ncaab-spread-v7 --sport NCAAB --market moneyline --approved-by "Vincent Chen" --evidence outputs/ncaab/ncaab_v7_multiseason_frozen.json --market-evidence outputs/ncaab/ncaab_v7_multiseason.md
python -m model_prediction.model_promotion promote --new ncaab-spread-v7 --sport NCAAB --market spread --approved-by "Vincent Chen" --evidence outputs/ncaab/ncaab_v7_multiseason_frozen.json --market-evidence outputs/ncaab/ncaab_v7_multiseason.md
python -m model_prediction.model_promotion history 5
```

- Rollback: `python -m model_prediction.model_promotion rollback --sport NCAAB --market moneyline`, then the same with `--market spread`. Then revert model.yaml lines 706/708 (see H).
- Note that `--market-evidence` points at a report that shows **no** edge over the close (RMSE 11.50 against the close's 11.19). The record makes that explicit; it is not a claim of edge.
- **Promote side effects to handle:**
  1. `_atomic_write_yaml` re-dumps the file with `yaml.dump`. I verified the round-trip is byte-identical except for the 3 comment lines 13-15, which it drops. Re-add them after the second promote.
  2. promote never changes the old champion's `serving_status`. After the second promote, set the `ncaab-spread-v6` entry to `serving_status: rollback`, mirroring `cfb-structural-v4`. Doing it before the promote would put the champion in a non-production serving status.
  3. `new_artifact_hash` is NULL in the record, because code-backed entries carry no registry hash. The row-level hash comes from change 2.
  4. promote sets `rollback_model: ncaab-spread-v6` on the v7 entry, which is already the value.

### Live state files (after the promotes; versioned writer plus a named backup; never a text edit, because the buyer rewrites the file each 15-min cycle)

Save this as a scratch script `apply_state.py` and run it with `$env:MODEL_PREDICTION_RUNTIME_ROOT='E:/model-prediction-runtime'`:

```python
import json, shutil
from datetime import UTC, datetime
from pathlib import Path
from model_prediction.portfolio.auto_executor import write_versioned_state_file

stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
rt = Path(r"E:\model-prediction-runtime")
# 1) main buyer: blacklist the new id (NCAAB stays disabled in all three disabled_sport_markets)
p = rt / "auto_buyer_state.json"
shutil.copy2(p, rt / f"auto_buyer_state.json.pre-ncaab-v7-id-{stamp}.bak")
s = json.loads(p.read_text(encoding="utf-8"))
bl = s["blacklist_models"]
if "ncaab-spread-v7" not in bl:
    bl.insert(bl.index("ncaab-spread-v6") + 1, "ncaab-spread-v7")
write_versioned_state_file(p, s, temp_path=p.with_name(p.name + ".v7id.tmp"))
# 2) paper trial: trade v7 only
t = rt / "auto_buyer_ncaab_spread_paper_trial.json"
shutil.copy2(t, rt / f"auto_buyer_ncaab_spread_paper_trial.json.pre-ncaab-v7-id-{stamp}.bak")
ts = json.loads(t.read_text(encoding="utf-8"))
ts["model_id"] = "ncaab-spread-v7"
write_versioned_state_file(t, ts, temp_path=t.with_name(t.name + ".v7id.tmp"))
print(json.dumps({"state_version": s["_state_version"], "trial": ts}, indent=2))
```

- Expected: `auto_buyer_state.json` goes from `_state_version` 916 to 917. The trial file goes from no version to 1.
- `whitelist_models` (14 entries, no NCAAB), `disabled_sport_markets` (all three NCAAB markets) and `ncaab_spread_v6_live_bucket.enabled=false` are left as they are.

### Tests that pin ids/counts (update)

- `tests/test_production_registry.py`:
  - :102 `== 89` becomes `== 90`.
  - :106 `== 49` becomes `== 50`. :108 stays 40.
  - :215-216 become `"ncaab-spread-v7"`.
  - Add `assert registry.rollback("NCAAB", "spread").model_id == "ncaab-spread-v6"`.
- `tests/test_ci_contracts.py:101`: update the comment to 90 / 50. Also CLAUDE.md line 51 ("pins 89 entries, 49 available") becomes 90 / 50.
- `tests/test_college_basketball_forward.py`: these tests pin gates, not routing, and four of them hit the real registry (fake models at :331/:457/:538 carry `version = "ncaab-spread-v6"`, and :221 asserts v6). Add `import pytest` and a module-level autouse fixture mirroring `test_ncaab_main_totals.py:114-120`:

```python
@pytest.fixture(autouse=True)
def _v6_spread_champion(monkeypatch):
    """These tests pin gate behaviour, not routing; route NCAAB to v6 as test_ncaab_main_totals does."""
    monkeypatch.setattr(
        "model_prediction.college_basketball_forward.ProductionModelRegistry.load",
        lambda *a, **k: SimpleNamespace(
            champion=lambda sport, market: SimpleNamespace(
                available=True, model_id="ncaab-total-v6" if market == "total" else "ncaab-spread-v6"
            )
        ),
    )
```

- New tests in the same file:
  - `test_ncaab_v7_champion_serves_the_v7_factory`: re-patch the registry to v7 for moneyline/spread. Monkeypatch `college_basketball_forward.ncaab_spread_v7_model` with a fake (version `"ncaab-spread-v7"`, `model_artifact_hash="h7"`, `team_priors=_D1_TEAMS`, returning one moneyline prediction). Reuse the fixture slate and quote archive from `test_ncaab_records_real_model_output_and_rerun_deduplicates`. Assert Flat rows carry `model_version == "ncaab-spread-v7"` and `model_artifact_hash == "h7"`.
  - `test_ncaab_split_spread_champions_fail_closed`: registry with moneyline v6 and spread v7 raises `ValueError` matching "one spread model".
  - `test_ncaab_retired_margin_engine_key_fails_closed`: `config={"models":{"NCAAB":{"margin_engine":"v7"}}}` raises `ValueError` matching "retired".
  - Prove each against its revert, per CLAUDE.md: remove the dispatch, the agreement check and the key check one at a time.
- `tests/test_ncaab_v7_serving.py` (new tests; reuse `_history`, `_upcoming`, `_summ`, `_FakeServing`):

```python
def test_v7_id_is_the_v6_artifact_with_the_v7_engine() -> None:
    from model_prediction.models.ncaab_spread_v6 import NCAAB_SPREAD_V7_VERSION, ncaab_spread_v7_model
    v6, v7 = ncaab_spread_v6_model(), ncaab_spread_v7_model()
    assert (v7.version, v7.margin_engine, v6.margin_engine) == (NCAAB_SPREAD_V7_VERSION, "v7", "v6")
    skip = {"version", "margin_engine", "model_artifact_hash", "_stats", "v7_serving"}
    assert {k: v for k, v in vars(v7).items() if k not in skip} == {
        k: v for k, v in vars(v6).items() if k not in skip
    }
    v6.margin_engine = "v7"                       # exactly what served under the v6 id since 11-16
    v6.v7_serving = v7.v7_serving = _FakeServing()
    a, b = v6.predict_games(_history(), _upcoming()), v7.predict_games(_history(), _upcoming())
    assert _summ(a) == _summ(b)                   # served probabilities bit-identical
    assert {getattr(p, "model_version", None) for p in b} == {NCAAB_SPREAD_V7_VERSION}

def test_model_artifact_hashes() -> None:
    from model_prediction.models.ncaab_spread_v6 import ncaab_spread_v7_artifact_hash, ncaab_spread_v7_model
    from model_prediction.models.ncaab_total_v6 import ncaab_total_v6_model
    art = json.loads((PROJECT_ROOT / "config/models/ncaab-spread-v6.json").read_text(encoding="utf-8"))
    tot = json.loads((PROJECT_ROOT / "config/models/ncaab-total-v6.json").read_text(encoding="utf-8"))
    assert ncaab_spread_v6_model().model_artifact_hash == art["artifact_hash"]
    assert ncaab_total_v6_model().model_artifact_hash == tot["artifact_hash"]
    h7 = ncaab_spread_v7_model().model_artifact_hash
    assert len(h7) == 64 and h7 != art["artifact_hash"] and h7 == ncaab_spread_v7_artifact_hash(art["artifact_hash"])

def test_v7_config_hash_tracks_the_archive(tmp_path) -> None:
    assert serving.v7_config_hash() == serving.v7_config_hash()
    assert serving.v7_config_hash(tmp_path / "missing.gz") != serving.v7_config_hash()
```

  - If `_stats` compares unequal by identity, skip it as shown. `team_priors` is a dict and compares by value.
- `tests/test_ncaab_auto_buyer_paper_trial.py`: existing v6 cases stay valid (v6 is still allowed). Add:
  - a v7 config buys a v7 row and rejects a v6 row;
  - `whitelisted_models == ("ncaab-spread-v7",)`;
  - a config naming `ncaab-spread-v5` raises.
- `tests/test_calibration_pipeline.py`: add a pooling test (option A). Two settled v6 rows and one v7 row produce a v7 group of 3, the v6 group of 2, and `fit_model_versions == ["ncaab-spread-v7", "ncaab-spread-v6"]`.
- `tests/test_model_naming.py::test_catalog_covers_production_registry` passes once F lands. No edit needed.

---

## Change 2: populate `model_artifact_hash` on NCAAB rows

- **Where the row is written.**
  - `college_basketball_forward.py` builds `PickRequest` at :904-949 and appends it at :973 (Flat) and :1026 (research/gated/Main) through `PickLedger.append_evaluated`.
  - The payload gets `model_artifact_hash` at `ledger.py:979`. The SQLite column is set at `ledger.py:1158`.
  - The decision snapshot is built at :853-892 (`contract_snapshot_json`, which already takes `artifact_hash=`).
- **Pattern followed.** Other sports stamp the frozen artifact's embedded hash (`cli/forecast.py:1195`, `:1430`: `model_artifact_hash = str(artifact["artifact_hash"])`). The calibrator stays in its own column (`calibration_artifact_hash`, :930).
  - Folding the calibrator into the model hash would churn it up to 8 times a day (Daily refits), so it is **not** folded in.
- **Values.**
  - Spread/moneyline under v6: `7c6d6d1c551fc4a8768a98ae4f448260b3b95fc2db4bd622094571d35c5423a7`, the embedded hash of `config/models/ncaab-spread-v6.json`.
  - Spread/moneyline under v7: `sha256({model_id, base_artifact_hash: v6 hash, v7_config_hash})`, see 1-A and 1-B.
  - Total: `fe0155c5db84600a42cfc1077e2c7d3377d3888a637c80a02e3350a57b2cca61` (`config/models/ncaab-total-v6.json`).
  - It is a model-level hash, stable per id. The per-row engine actually used stays in `feature_basis.margin_engine`.
- **Edits in `college_basketball_forward.py`** (after 1-A and 1-C):
  - In the `contract_snapshot_json(...)` call, after line 891 (`observed_at_utc=observed.isoformat(),`), add `artifact_hash=str(getattr(model_inst, "model_artifact_hash", "") or ""),`.
  - In `PickRequest(...)`, after line 919 (`model_version=model_version,`), add `model_artifact_hash=str(getattr(model_inst, "model_artifact_hash", "") or ""),`.
  - Use `getattr` with a default, because test fakes carry only `version`.
- **Readers that branch on a non-empty hash, checked:**
  - `eligibility.py:83/170`: not on the NCAAB path, because NCAAB builds `EligibilityResult` directly.
  - `feature_capture.validate_decision_snapshot`: does not compare the hash.
  - `model_ledger.py:340`: copies it.
  - `dashboard/evidence.py:407` dedupe key and `:560` hash match against `artifact.declared_hash`: new rows form their own dedupe group, which is correct.
  - `cli/commands.py:720` manual-order qualification: requires `production_artifact` in model.yaml, which NCAAB lacks, so it returns False as today.
  - Restore slots are (league, event, market), and model_ledger dedupe does not use the hash.
- **Test:** `test_ncaab_v7_champion_serves_the_v7_factory` above asserts the row hash. `test_model_artifact_hashes` asserts the factory values.

---

## Change 3: served-probability semantics and CLV

### 3a. Readers (behaviour-preserving; do now)

- **Keep the payload as it is.** NCAAB `model_probability` in the payload stays the served value. The Flat/Main edge, the paper-trial saved edge, the dashboard pick tables and the snapshot check all use it:
  - `feature_capture.validate_decision_snapshot:148-153` requires `payload.model_probability == snapshot.probability`, which is served.
  - Switching the payload to raw would change the buyer's `min_model_probability` gate input (`auto_executor.py:2164`) and the dashboard.
- **Readers of the SQL column (raw), which should read served:**
  - `scripts/audit_best_picks.py` SELECTs at :281 and :576, with edge computed at :260, :329 and :605. The best-pick audit buckets NCAAB edges off raw while the ledger's own `edge` is served. Fix both SELECTs to select:

    ```sql
    COALESCE(CASE WHEN json_valid(decision_payload_json)
                  THEN NULLIF(json_extract(decision_payload_json, '$.serving_probability'), '') END,
             model_probability) AS model_probability
    ```

    **`NULLIF(..., '')` is mandatory.** The ledger writes `""` for None, so 22,325 of 27,711 payloads since 11-01 have `serving_probability = ''`. A plain COALESCE would return `''`, which becomes 0.0 on every non-NCAAB row.

    Add two cases to `tests/test_audit_best_picks.py`:
    - a row with raw 0.5719, served 0.5079 and market 0.5192 lands in the negative-edge bucket;
    - a row whose payload has `"serving_probability": ""` keeps its column `model_probability`.
  - `dashboard/data_service.py:207-213` (`_LEDGER_COLUMNS`, ledger API): append `, json_extract(decision_payload_json, '$.serving_probability') AS serving_probability` so the API shows both.
- **Readers through `rows()`/payload are already correct (served):** `dashboard/picks.py`, `orders.py`, `common.py:592`, `evidence.py:548/816`, `calibration_pipeline`.
- **Settlement/CLV** uses the decision price and close price, not `model_probability`.

### 3b. Calibrator fit source (operator decision; changes future calibrators)

- The pipeline fits on served values and serving applies the result to raw. On non-identity days that is calibrating a calibrated number.
- The fix is in `calibration_pipeline.py:161-173`: put `"model_probability_raw"` first in the name tuple.
- Only NCAAB rows carry `model_probability_raw`, so other sports are unchanged.
- Effect: 222 settled NCAAB spread/moneyline rows change fit value. The fitted transform changes from the next Daily on.
- Recommended, together with the serving-guard gap below, but not as part of this behaviour-preserving lane.

**Related (operator decision).** The NCAAB forward serves calibrators without `serving_guard_reason`, so there is no n>=100 floor and no Brier-gain requirement. Routing it through `calibration_pipeline.load_serving_calibrator` would stop the day-to-day flip between beta, temperature, platt and identity. It is a behaviour change.

### 3c. CLV: a close observed strictly before tip and after the decision

**3c-i (do now, accounting only).** Stop recording the decision's own quote as the close.
- `data_sources/polymarket_us.py:932-957`, `closing_snapshot`:
  - Change the signature to `closing_snapshot(self, slug: str, event_start_utc: str, *, after_utc: datetime | None = None)`.
  - Change line 955 to `if observed < event_start and (after_utc is None or observed > after_utc):` (strictly before tip; strictly after the decision when given).
- `cli/settle.py:599-607`:
  - Add the keyword `not_before_utc: str | None = None` to `_closing_probability_for_moneyline_pick`.
  - At line 641, call `store.closing_snapshot(slug, event_start_utc, after_utc=parse_utc(not_before_utc) if not_before_utc else None)`.
- `cli/settle.py:354` (the general settlement path): pass `not_before_utc=(row.get("market_quote_observed_at_utc") or row.get("observed_at_utc")) if str(row.get("league", "")).upper() == "NCAAB" else None`.
  - Scoped to NCAAB for this lane. Extending it to all sports is the obviously right follow-up, but it blanks degenerate CLV in other sports' dashboards: an operator call.
- Effect: **3c-i alone does not deliver a pre-tip close.** It makes NCAAB `closing_*`/`probability_clv` blank (`""`) instead of a fake 0, which is honest because no later quote exists. Only 3c-ii produces a real pre-tip close.
- The strict `<` at tip applies to every sport, because `closing_snapshot` is shared. The `after_utc` check is NCAAB-only.
- Settled rows are skipped on re-run, so the 339 historic values are untouched (not relabelled).
- Tests:
  - `tests/test_ncaab_settlement_clv.py`: the existing fixture decides at start-5h (`now=start - 5h`, :88). Its snapshots sit at start-2h, start-2min and start+30min (:40-45), all after the decision, so the existing tests still pass. Add a snapshot exactly at decision time and confirm it is excluded, with the start-2min snapshot still chosen.
  - New case: only an at-decision snapshot gives empty strings.
  - `tests/test_closing_quote_resolution.py`: a snapshot at exactly `event_start` is now excluded (pin it).

**3c-ii (recommended; operator decision because it adds exchange requests).** A pre-tip capture in the 15-min buyer worker.
- New module `src/model_prediction/pretip_close_capture.py`:

```python
def capture_pretip_closes(*, now=None, window_minutes: float = 25.0, sport: str = "ncaab",
                          ledgers_db: Path | None = None, runtime_root: Path | None = None,
                          client=None, capture_fn=None) -> dict[str, Any]:
    """Snapshot, shortly before tip, every market an open ledger row of `sport` was priced on.

    Settlement's closing_snapshot takes the last snapshot strictly before tip, and NCAAB snapshots
    were only written by the Daily runs, so the surviving row's close was its own decision quote
    (339 of 339 settled rows, 2026-11-20). Market data only, never an order; best effort.
    """
```

- Steps:
  1. Open `RuntimePaths.resolve().ledgers_db` with `mode=ro`.
  2. `SELECT event_start_utc, json_extract(json_extract(decision_payload_json,'$.model_input_snapshot_json'),'$.features."research_quote_join.market_slug"') FROM ledger_records WHERE lower(sport)=? AND status='open'`. Keep the rows with `now + 60s < parse_utc(start) <= now + window`, and collect slugs by ET game date. This is the same slug source settlement uses (`settle._extract_market_slug:720-726`).
  3. Per date, call `client.sport_slate(sport, date, "America/New_York")`.
  4. Keep each event's markets whose `market_slug` is wanted, then call `capture_slate_snapshots(client, wanted_by_league, runtime_root, game_date, max_capture_seconds=120)`. This mirrors `ncaab_paper_trial.capture_unmapped_cohort_markets:126-157`.
  5. Wrap the whole step in `try/except (httpx.HTTPError, OSError, RuntimeError, TypeError, ValueError, sqlite3.Error)`, log a warning, and return a status dict.
- Wiring: `auto_buyer_worker.py` after line 49 adds `result["pretip_close_capture"] = _safe(capture_pretip_closes)`. It is **not** included in `failed` at line 50, because accounting must never fail the buyer exit code.
- Traffic: 1 slate GET plus 1 book per wanted slug, every 15 min, only when NCAAB rows tip within 25 min. That is roughly 5-30 books per cycle at peak, against about 1,500 per Daily capture.
- Each close is then 0-15 min before tip and after every decision.
- Test `tests/test_pretip_close_capture.py`: a tmp ledgers.db, a fake client and a fake capture_fn. It checks:
  - only open, in-window, slug-matched markets are captured;
  - started games and games beyond the window are skipped;
  - a client exception returns `{"status": "error"}` without raising.

**Forward-check split (HANDOFF text for the orchestrator).** Engine = `COALESCE(json_extract(json_extract(decision_payload_json,'$.model_input_snapshot_json'),'$.features.margin_engine'),'v6')`. I verified it on the live ledger. Settled spread rows since 11-12: v6-engine 160, v7-engine 153; moneyline 128 / 47.
- v6 cohort = `model_id='ncaab-spread-v6'` AND engine 'v6'.
- v7 cohort = (`model_id='ncaab-spread-v6'` AND engine 'v7') OR `model_id='ncaab-spread-v7'`.
- Keep splitting on engine even under the v7 id, because non-D1 games still fall back to v6 math.

---

## Apply runbook (window: after the 00:37 ET Daily has finished, before the 06:01 ET Daily; check `E:\model-prediction-runtime\locks\daily.lock` is free)

1. Code edits 1-A..1-J (except H's model.yaml lines), change 2, 3a, 3c-i, and calibration option A if approved.
   - In the same window, model.yaml H **must** land together with the promotes. With the code in and the champion still v6, a Daily would serve the v6 engine. With model.yaml still carrying `margin_engine`, the forward raises.
2. Verify, all `-o addopts='' -q`:
   - `ruff check src/model_prediction/college_basketball_forward.py src/model_prediction/models/ncaab_spread_v6.py src/model_prediction/models/ncaab_total_v6.py src/model_prediction/models/ncaab_v7_serving.py src/model_prediction/production_registry.py src/model_prediction/sports/naming.py src/model_prediction/portfolio/auto_executor.py src/model_prediction/portfolio/ncaab_paper_trial.py src/model_prediction/calibration_pipeline.py src/model_prediction/cli/settle.py src/model_prediction/data_sources/polymarket_us.py src/model_prediction/auto_buyer_worker.py scripts/audit_best_picks.py src/model_prediction/dashboard/data_service.py tests/`
   - `pytest tests/test_ncaab_v7_serving.py tests/test_ncaab_v6.py tests/test_ncaab_v6_review_fixes.py -o addopts='' -q`
   - `pytest tests/test_college_basketball_forward.py tests/test_ncaab_main_totals.py tests/test_ncaab_tier1_guardrails.py tests/test_ncaab_restore_wiring.py -o addopts='' -q`
   - `pytest tests/test_ncaab_auto_buyer_paper_trial.py tests/test_auto_buyer_state_versioning.py tests/test_dual_auto_buyer_dashboard.py tests/test_auto_buyer_ordering_and_lineage.py -o addopts='' -q`
   - `pytest tests/test_calibration_pipeline.py tests/test_audit_best_picks.py tests/test_ncaab_settlement_clv.py tests/test_closing_quote_resolution.py -o addopts='' -q`
   - Run `test_production_registry.py`, `test_model_naming.py`, `test_ci_contracts.py` and `test_model_promotion.py` after step 5. They read the real yaml, and before the promote the champion pins will fail by design.
   - Revert-proof each new test, per CLAUDE.md.
3. Copy the two calibrator files (Calibrators, day 1).
4. Commit the code and the production.yaml entry, staging explicit paths. Shared tree: stage by blob, per the 2026-11-12 memory.
   - `.pre-commit-config.yaml` runs only ruff and ruff-format (9 lines; no pytest/mypy hook despite `.claude/CLAUDE.md`), so the pre-promote commit is not blocked by the champion pins.
   - Still, put the 90/50 count pins in this commit and the v7 champion pins (`test_production_registry.py:215-216`) in the post-promote commit, so every commit is green.
5. Run the two promote commands. Then re-add production.yaml comment lines 13-15, set v6 `serving_status: rollback`, apply the model.yaml H edits, and run the step-2 registry tests. Commit with the promotion ids in the message.
6. Run `apply_state.py` (state files).
7. **Slate replay (served-probability identity, real data).** A scratch script:
   - Build `history = FeatureStore(r"E:\MP\data").games_before("ncaab", D)` and the upcoming D1 games from today's open NCAAB ledger rows (read-only).
   - Run `predict_games` with (a) `ncaab_spread_v6_model()` with `margin_engine="v7"` (what served before) and (b) `ncaab_spread_v7_model()`. Use the real archive; expect about 200-300 s.
   - Assert identical `(event_id, market_type, inference_inputs, probabilities)` for every game.
   - Assert `load_calibrator_artifact` on the v6 and v7 files maps a 0.01..0.99 grid identically.
   - Run it outside 23:55-01:15 (memory pressure).
8. After the 06:01 ET Daily:
   - NCAAB rows carry `model_id='ncaab-spread-v7'` with a non-null `model_artifact_hash` (v7 hash on ML/spread, `fe0155c5...` on totals), and engine tags are 'v7' on D1 games.
   - The buyer cycle log shows the trial evaluating v7 candidates, and `rejected_blacklist` counts any v7 rows in the main buyer.

## Cut order if the two-hour limit binds

1. Change 1 (code, yaml, promotes, state files) plus the calibrator copy plus option A: the identity work, and the riskiest part.
2. Change 2 (two kwargs plus factory hashes).
3. 3a readers (`audit_best_picks`, `data_service`).
4. 3c-i, then 3c-ii (pending operator sign-off).

3b is not in this lane.

## Operator decisions needed

1. **Calibrator pool for v7:** A exact continuity (my recommendation for this lane), B engine-pure, or C none (fits on about 20 rows within a day).
2. **Paper trial:** v7-only (designed) against a transitional dual accept. The auto-quarantine history resets under the new id.
3. **Fit source to raw (3b)** and **guarded calibrator loading for NCAAB.** Both are behaviour changes, and both are recommended.
4. **Pre-tip capture (3c-ii)**, which adds exchange requests. Also whether 3c-i should apply to every sport, not just NCAAB.
5. **Evidence label for v7:** `historical_only` (designed; display-only) against `predictively_qualified`.
6. **The promotion itself** records a model whose own market evidence shows no edge over the close. The design records it honestly via `--market-evidence`. Approve explicitly.

## Out of scope / left as is (noted)

- `model_ledger.MODEL_ID_BY_LEAGUE_AND_MARKET` (:180-185) buckets the per-model xlsx mirror statically: NCAAB moneyline goes to `ncaab-tempo-v2` (stale) and spread to `ncaab-spread-v6`. v7 rows land in those same files. It is behaviour-preserving and a cosmetic follow-up.
- `sports/ncaab.py:136` keeps a v6 fallback label that is used only when a prediction lacks `model_version`.
- The live bucket (`_is_live_ncaab_spread_v6_bucket`) stays v6-only and disabled.
