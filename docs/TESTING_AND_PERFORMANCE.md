# Testing Architecture & Performance Optimization Guide

**Authoritative Reference**: `CLAUDE.md`, `AGENTS.md`  
**Last Updated**: October 18, 2026

---

## 1. Executive Summary

This document establishes the architecture for rapid developer and agent iteration, addressing the historical **3,200-Test Monolith** that was causing 6-minute iteration cycles. It defines the tiered test hierarchy, execution rules, and codebase performance optimizations.

---

## 2. Deconstruction of the 3,200-Test Monolith

An audit of all 341 test files across `tests/` revealed four distinct categories that inflated test volume to 3,199 tests:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          3,199-TEST INVENTORY                               │
├──────────────────────────┬─────────────────┬────────────────────────────────┤
│ Category                 │ Test Volume     │ Execution Impact               │
├──────────────────────────┼─────────────────┼────────────────────────────────┤
│ 1. Core Unit & Contracts │ ~200 tests      │ 2.5 – 3.5s (Critical Invariants)│
│ 2. Rebuild Sandbox       │ 58 files (~650) │ 45 – 60s (Experimental shadow)│
│ 3. Archived Research     │ 8 files (~200)  │ 25s + 53,000 joblib warnings   │
│ 4. Parametric Duplication│ ~2,100 tests    │ 200+ seconds (Exhaustive grids)│
└──────────────────────────┴─────────────────┴────────────────────────────────┘
```

### Key Drivers of Test Suite Drag:
1. **xdist Process Spawn Overhead**: `pyproject.toml` historically forced `addopts = ["-n", "auto"]`. On 12-core systems, running even a single test file spawned 12 heavy Python workers, turning a 0.5s check into a 5-second process stall.
2. **53,000+ Deprecation Warnings**: Legacy joblib numpy pickle files in `test_archived_research_pairs.py`, `test_research_forward.py`, etc., generated over 53,000 NumPy 2.5 shape warnings on every run.
3. **Rebuild Sandbox Recursion**: The experimental clean-slate rebuild sandbox (`tests/rebuild/`) is fully isolated from production models (`src/model_prediction/rebuild/`), yet was crawled on every standard `pytest` run.

---

## 3. How the Test Count & Runtime Are Lowered

### Tier 1: Core Production & Contract Suite (Default: ~200 Tests, <3 Seconds)
Tests critical runtime invariants, CI contracts, 31 production champion registries, and financial ledger safety:
- `tests/test_ci_contracts.py` (Production registry, SQLite migration, shadow isolation)
- `tests/test_production_registry.py` (Order parity, allowed models, lifecycle)
- `tests/test_nba_nfl_v5.py` (NFL structural v5, key-number mean conservation)
- `tests/test_wnba_structural_v3.py` (Defensive PPP monotonicity, player impact)
- `tests/test_cfb_structural_v2.py` (Total SD bounding, negative binomial simulation)
- `tests/test_hockey.py` (Poisson Dixon-Coles OT normalization)
- `tests/test_tennis_v2.py` & `test_tennis_derivatives.py` (Surface Elo & Markov chains)
- `tests/test_soccer_v2.py` (Dixon-Coles v2, Platt BTTS scaling)
- `tests/test_darts.py` (Markov leg progression dynamic programming)
- `tests/test_international_baseball.py` (Tie-aware KBO/NPB multinomial Elo)
- `tests/test_scalar_artifact_replay.py` (Exact model-to-artifact parameter replay)
- `tests/test_architecture_hardening.py` (Transactional ledger pre-clear rollback)

### Tier 2: Micro-Targeted Testing (<0.5s per test)
When working on a single sport or module, **never run the full suite**. Run only the file being edited:
```powershell
# Example: editing NFL structural model
pytest tests/test_nba_nfl_v5.py -q
# Runtime: 0.40 seconds
```

### Tier 3: Rebuild & Historical Archive Suites (On-Demand Only)
- Rebuild sandbox: `pytest tests/rebuild/ -q`
- Historical research replays: `pytest tests/test_archived_research_pairs.py -q`

---

## 4. Pytest Configuration Hardening (`pyproject.toml`)

The pytest configuration is optimized for fast, quiet, zero-noise execution:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = [".", "src"]
addopts = ["-q", "--disable-warnings"]
filterwarnings = [
  "ignore::DeprecationWarning",
]
markers = [
  "slow: long-running simulation or historical replay",
  "rebuild: clean-slate rebuild sandbox tests",
]
```

### What Changed:
- **Removed forced `-n auto`**: Individual test files run in **0.4s to 0.7s** without spawning 12 worker processes.
- **Added `--disable-warnings`**: Eliminates 54,000+ line terminal buffer dumps from legacy numpy joblib pickles.
- **Markers Defined**: Allows selective inclusion/exclusion via `-m "not slow"`.

---

## 5. Agent & Developer Execution Rules

1. **Pure Cleanup / Refactors = ZERO Tests**:
   - For formatting, dead code pruning, variable renames, `.gitignore`, or documentation edits, **do not run pytest**.
   - Run `ruff check` (<0.2s) and commit.
2. **Algorithm Math Changes = 1 Micro-Test Only**:
   - If core mathematical formulas change, run only the single corresponding test file (target < 0.5s).
3. **Batch Modifications Before Verification**:
   - Complete all edits across all files in a single pass before running any test or linter.
