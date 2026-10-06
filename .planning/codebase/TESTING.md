---
last_mapped_commit: 3aaa58949aca8dabf49593091243d6719d859452
last_mapped_at: 2026-09-20
---
# Testing Patterns

**Analysis Date:** 2026-09-20

## Test Framework

**Runner:**

- pytest 8.x (from `pyproject.toml` dev dependencies)
- Config: `pyproject.toml` under `[tool.pytest.ini_options]`
- Python path: `pythonpath = [".", "src"]` — tests can import both project root and src/ modules

**Assertion Library:**

- pytest's built-in `assert` statements (no separate library)

**Run Commands:**

```bash
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -q        # Run all tests quietly
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -v        # Run all tests verbosely
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -k "name" # Run tests matching pattern
env PYTHONPATH=src:. .venv/bin/python -m pytest tests/ -x        # Stop on first failure
.venv/bin/pytest tests/                                          # Using installed entry point
```

**Coverage:**

- No explicit pytest-cov configuration in pyproject.toml
- Coverage measurement not enforced; run manually if needed: `pytest --cov=src tests/`

## Test File Organization

**Location:**

- Tests live in `tests/` directory at repo root (configured in `pyproject.toml` as `testpaths = ["tests"]`)
- One test file per major module: `test_backtester.py` tests `backtester.py`, `test_validation.py` tests `validation.py`
- Special shared fixtures in `conftest.py`

**Naming:**

- Test files: `test_*.py`
- Test functions: `def test_*()` — pytest auto-discovers by this convention
- Parametrized test functions use `@pytest.mark.parametrize` decorator
- Fixtures: use `@pytest.fixture` decorator

**Structure:**

```
tests/
├── conftest.py                           # Shared fixtures, setup/teardown
├── test_backtester.py                    # Tests for backtester.py
├── test_validation.py                    # Tests for validation.py
├── test_lifecycle.py                     # Tests for lifecycle.py
├── test_market_blend.py                  # Tests for market_blend.py
├── test_model_promotion.py               # Tests for model_promotion.py
├── test_dashboard_cache_integrity.py     # Tests for dashboard cache
├── test_mlb_availability.py              # Tests for mlb_player_availability.py
└── test_*.py                             # One per major module
```

## Test Structure

**Suite Organization:**

```python
def test_lifecycle_transitions_and_qualification() -> None:
    """Test that model state transitions are valid and qualification gates."""
    validate_transition(ModelState.RESEARCH, ModelState.SHADOW_CANDIDATE)
    validate_transition(ModelState.SHADOW_QUALIFIED, ModelState.DEGRADED)
    # ...
    with pytest.raises(ValueError):
        validate_transition(ModelState.RETIRED, ModelState.RESEARCH)
    assert can_create_qualified_call(ModelState.SHADOW_QUALIFIED, ModelOrigin.STATISTICAL_MODEL)
```

**Patterns:**

- One logical assertion group per test function (one test per behavior, not one per assertion)
- Arrange-Act-Assert structure when needed, but often the structure is linear
- Use descriptive test function names that read like documentation: `test_qualification_base_gate_is_locked_holdout_accuracy()`

**Parametrized Tests:**

```python
@pytest.mark.parametrize(
    ("calls", "hits", "locked_holdout", "failure_fragment"),
    [
        (49, 40, True, "below required 50"),
        (50, 29, True, "below required 60.00%"),
        (50, 40, False, "not a locked holdout"),
    ],
)
def test_qualification_rejects_only_primary_gate_failures(
    calls: int, hits: int, locked_holdout: bool, failure_fragment: str
) -> None:
    decision = evaluate_locked_holdout(
        calls=calls,
        hits=hits,
        total_predictions=100,
        locked_holdout=locked_holdout,
    )
    assert decision.qualified is False
    assert any(failure_fragment in failure for failure in decision.failures)
```

## Mocking

**Framework:** pytest's `monkeypatch` fixture (built-in, no external library needed)

**Patterns:**

```python
def test_promote_leaves_champion_unchanged_when_yaml_write_fails(tmp_path: Path, monkeypatch) -> None:
    """Mock the atomic_write_yaml function to simulate failure."""
    monkeypatch.setattr(model_promotion, "_atomic_write_yaml", _boom)
    # ... test that promotion rollback happens correctly
```

**Common Mocking Targets:**

- Environment variables: `monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(tmp_path / "runtime"))`
- Module attributes: `monkeypatch.setattr(module_name, "attribute", mock_value)`
- Filesystem paths: `monkeypatch.setattr(picks, "ROOT", tmp_path)` to redirect where code looks for files

**What to Mock:**

- External I/O (file reads, env vars, database connections)
- System calls (time, random state for reproducibility)
- Third-party service calls (ESPN API, odds providers)

**What NOT to Mock:**

- Internal domain logic functions (test them directly with real inputs)
- Dataclass construction (use real objects)
- Feature computation (use synthetic test data, not mocks)

## Fixtures and Factories

**Test Data:**
Helper function to build test objects:

```python
def seed_games(tmp_path, count: int = 240) -> FeatureStore:
    """Synthetic two-tier league: Strong teams beat Weak ones ~75% of the time."""
    rng = random.Random(3)
    teams = [f"Strong{i}" for i in range(4)] + [f"Weak{i}" for i in range(4)]
    store = FeatureStore(tmp_path)
    path = store.processed_path("test")
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    day = 0
    for index in range(count):
        if index % 4 == 0:
            day += 1
        home, away = rng.sample(teams, 2)
        home_edge = ("Strong" in home) - ("Strong" in away)
        home_win_probability = 0.5 + 0.25 * home_edge + rng.gauss(0, 0.05)
        home_wins = rng.random() < home_win_probability
        home_score = rng.randint(80, 100) + (5 if home_wins else -5)
        away_score = home_score - rng.randint(1, 12) if home_wins else home_score + rng.randint(1, 12)
        # ... build and append row
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    return store
```

**Fixture Pattern:**

```python
@pytest.fixture
def registry() -> EntityRegistry:
    """Load canonical team registry for tests."""
    return EntityRegistry.from_json(PROJECT_ROOT / "data/entities/teams.json")


@pytest.fixture
def bans(tmp_path: Path, registry: EntityRegistry, monkeypatch) -> TeamBanList:
    """Create an in-memory ban list for testing."""
    path = tmp_path / "model.yaml"
    config = {
        "team_ban_list": {
            "MLB": {...},
            "NBA": {...},
            # ...
        }
    }
    path.write_text(yaml.safe_dump(config, encoding="utf-8"))
    return TeamBanList(path, AuditLog(tmp_path / "events.jsonl"))
```

**Location:** Fixtures live in `tests/conftest.py` for shared use across test files, or in the test file itself for fixtures used by a single test file.

## Coverage

**Requirements:** No explicit target enforced by CI; pre-existing coverage goal was ~80% for critical paths. CLAUDE.md guidance emphasizes regression testing when bugs are fixed: "temporarily revert the fix, confirm the new test fails, then restore the fix."

**View Coverage:**

```bash
.venv/bin/pytest tests/ --cov=src --cov-report=html

# Open htmlcov/index.html in browser

```

## Test Types

**Unit Tests:**

- Scope: Individual functions/classes in isolation
- Approach: Use synthetic test data (helper functions like `seed_games()`, parametrized fixtures)
- Examples: `test_lifecycle_transitions_and_qualification()`, `test_confidence_gap_is_an_exact_reparameterization()`
- Run: `pytest tests/test_lifecycle.py::test_lifecycle_transitions_and_qualification`

**Integration Tests:**

- Scope: Multiple modules working together (e.g., validation pipeline with feature computation)
- Approach: Use real data files when available, or extensive synthetic fixtures
- Examples: `test_validation_uses_three_disjoint_chronological_cohorts()`, `test_train_serve_parity_for_v9_features()`
- Run: `pytest tests/test_validation.py`

**E2E Tests:**

- Not formally structured as a separate tier; integration tests serve this role
- The daily pipeline (launchd job `com.modelprediction.daily`) is the real E2E test

**Dashboard Tests:**

- `test_dashboard_cache_integrity.py` tests cache layer separation (SQLite canonical vs. xlsx export)
- `test_rebuild_dashboard.py` tests rebuild-mode dashboard state
- Monkeypatch strategy: inject custom cache implementations to verify fallback behavior

## Common Patterns

**Async Testing:**
Not used in this codebase (synchronous Python only; concurrent I/O uses ThreadPoolExecutor, not async/await).

**Error Testing:**

```python
def test_chronological_split_rejects_empty_data() -> None:
    with pytest.raises(ValueError, match="empty"):
        chronological_split([])
```

**Fixture Dependencies:**

```python
@pytest.fixture
def bans(tmp_path: Path, registry: EntityRegistry, monkeypatch) -> TeamBanList:
    """bans depends on registry and uses tmp_path; pytest resolves in order."""
    # ... use registry.resolve(...) to look up teams
    # ... use tmp_path to write config
```

**State Isolation:**

- `tmp_path` fixture (from pytest) provides an isolated temporary directory for each test
- Tests using real files should write to `tmp_path`, not repo-local `data/`
- Parametrized tests each get their own `tmp_path` instance

**Deterministic Randomness:**

```python
rng = random.Random(3)  # Fixed seed for reproducible test data
```

**Mocking Time/Now:**

- CLAUDE.md warns: "Mocking `utc_now()` to one fixed value everywhere hides timestamp-ordering bugs"
- When testing point-in-time correctness (critical invariant), use advancing mock clocks
- Example: `test_train_serve_parity_for_v9_features()` compares training vs. serving computations, ensuring no timestamp leaks

**Database/File State:**

- Tests that use FeatureStore, ledgers, or production stores should pass `tmp_path` and create new instances
- Example: `store = FeatureStore(tmp_path)` creates a test-isolated feature store

## Regression Testing

When a bug is fixed, CLAUDE.md requires:

1. Add a test that reproduces the bug (should fail before the fix)
2. Verify the test fails without the fix: temporarily revert the fix, run the test
3. Restore the fix and verify the test passes
4. Keep the test in the suite to prevent regression

Example workflow:

```bash

# Add new test that reproduces bug

vim tests/test_x.py

# Verify it fails

pytest tests/test_x.py::test_bug_reproduction

# ❌ FAILED

# Temporarily revert the fix to domain.py

git diff src/model_prediction/domain.py
git checkout src/model_prediction/domain.py

# Re-run to confirm test still fails

pytest tests/test_x.py::test_bug_reproduction

# ❌ FAILED (good, test caught the bug)

# Restore the fix

git checkout HEAD^ src/model_prediction/domain.py

# Verify test now passes

pytest tests/test_x.py::test_bug_reproduction

# ✅ PASSED

```

## Test Execution Guarantees

- Tests run WITHOUT env vars `MODEL_PREDICTION_*` set (repo-colocated defaults used)
- Setting launchd env vars can redirect tests to live runtime root and cause false failures
- Test isolation verified: fixture cleanup (tmp_path auto-deleted, monkeypatch auto-reverted)
- Parallel execution: pytest-xdist enabled (`pytest-xdist>=3.5,<4` in dev dependencies); use `-n auto` to parallelize

---

*Testing analysis: 2026-09-20*
