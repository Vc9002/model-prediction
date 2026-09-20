---
last_mapped_commit: 3aaa58949aca8dabf49593091243d6719d859452
last_mapped_at: 2026-09-20
---
# Coding Conventions

**Analysis Date:** 2026-09-20

## Naming Patterns

**Files:**

- Module files use `snake_case`: `production_canary.py`, `learned_forward.py`, `feature_contract.py`
- Directories use `snake_case`: `data_sources/`, `features/`, `cli/`, `rebuild/`
- Test files use `test_` prefix: `test_backtester.py`, `test_validation.py`, `test_lifecycle.py`

**Functions:**

- Use `snake_case` for all function names: `walk_forward_backtest()`, `_acquire_exclusive_lock()`, `build_learned_moneyline_slate()`
- Private functions prefixed with single underscore: `_parse_snapshot_time()`, `_component_stats()`, `_load_indexes()`
- Public functions have no prefix: `evaluate_locked_holdout()`, `can_create_qualified_call()`
- Functions that return boolean frequently use `is_` or `can_` prefixes: `can_create_qualified_call()`, `is_expired()`
- Builder functions use `build_` prefix: `build_walk_forward_rows()`, `build_policy_artifact()`, `build_production_artifact()`

**Variables:**

- Use `snake_case` for all variable names: `home_team`, `market_probability`, `locked_holdout_accuracy`
- Constants use `UPPER_SNAKE_CASE`: `LOCK_TIMEOUT_SECONDS`, `AUDIT_SCHEMA_VERSION`, `UNIT_MIN_EDGE`
- Module-level constants document their purpose inline: `LOCK_TIMEOUT_SECONDS = 30  # blocks indefinitely with no built-in timeout`
- Cached module-level dicts use `_CACHE` suffix: `_LEAGUE_RATES_CACHE`, `_PLAYER_INDEX_CACHE`, `_TEAM_GAME_INDEX_CACHE`

**Types:**

- Custom exceptions end with `Error` or `Timeout`: `AuditLockTimeout`, `EntityResolutionError`, `MarketBlendBlockedError`
- Enums use `StrEnum` from `enum` module, with `UPPER_SNAKE_CASE` members: `League.MLB`, `MarketType.MONEYLINE`, `ModelState.RESEARCH`
- Dataclass names use `PascalCase`: `BanEntry`, `ValidationRow`, `ChampionSnapshot`, `SettledBlendEvidence`

## Code Style

**Formatting:**

- Line length: 110 characters (configured in `pyproject.toml`)
- Use `ruff-format` for consistent formatting (enforced via pre-commit hook)
- No custom line-break rules beyond ruff defaults

**Linting:**

- Linter: `ruff` v0.6+
- Run via: `python -m pytest` (with PYTHONPATH set) and `.venv/bin/ruff check src/ tests/`
- Pre-commit hook: ruff linting + ruff-format (see `.pre-commit-config.yaml`)
- Target Python version: 3.11+ (configured as `target-version = "py311"` in pyproject.toml)
- Status: Pre-existing baseline was ~118 findings; cleaned to zero (2026-08-26, verified clean), do not reintroduce

**Type Checking:**

- MyPy enabled with strict settings:
  - `check_untyped_defs = true` — all function signatures must be annotated
  - `warn_unused_ignores = true` — no silent type-ignore overrides
  - `no_implicit_optional = true` — explicit `| None` required
  - `python_version = "3.14"` (set for future-proofing)
- Run: `mypy` (configured in `pyproject.toml`, third-party stubs allowed)
- Third-party type-ignore overrides: sklearn, scipy, openpyxl, pybaseball, pypdf, polars, duckdb, xgboost (all lack stubs)

## Import Organization

**Order:**

1. `from __future__ import annotations` (always first)
2. Standard library imports: `import json`, `import logging`, `from pathlib import Path`
3. Third-party imports: `import pytest`, `import pandas`, `from pydantic import BaseModel`
4. Local imports: `from .domain import ...`, `from .features.base import ...`

**Path Aliases:**

- No explicit path aliases; use relative imports within the package (`from .module import ...`)
- Absolute imports from external packages: `from model_prediction.validation import ...`
- In test files, both forms are used (`from model_prediction.X import ...` and relative imports)

**Grouping:**

- Separate groups with single blank line
- Within each group, sort alphabetically (ruff/import-sorting enforces this)
- Multi-line imports use parentheses (not backslash continuation)

Example from `learned_forward.py`:

```python
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .data_sources.espn import _probable
from .domain import EASTERN, parse_utc
from .features.base import FeatureStore
```

## Error Handling

**Patterns:**

- Raise standard exceptions for validation failures: `ValueError`, `TimeoutError`
- Define custom exceptions as `Exception` subclasses for domain-specific errors: `AuditLockTimeout(TimeoutError)`, `EntityResolutionError(Exception)`
- Raise with descriptive messages that include context: `"could not acquire lock on {path} within {timeout}s -- another process may be hung while holding it"`
- Use `... from None` to suppress exception chaining when it's not relevant to the user

Example from `audit.py`:

```python
class AuditLockTimeout(TimeoutError):
    """Raised when the audit log lock can't be acquired within the timeout."""

if time.monotonic() >= deadline:
    raise AuditLockTimeout(
        f"could not acquire lock on {path} within {timeout}s -- "
        "another process may be hung while holding it"
    ) from None
```

**No Exceptions:** Use `Optional` returns (e.g., returning `None` or an empty dict) for expected missing data, especially in features that fail gracefully. Feature modules return `None` when data is unavailable, stale, or ambiguous (fail-closed pattern).

## Logging

**Framework:** `logging` (standard library)

**Patterns:**

- Logger per module: `_logger = logging.getLogger(__name__)` at module level
- Use `_logger.warning()` for recoverable issues that the system can continue despite: `_logger.warning("production store unavailable %s; cycle's predictions will not recorded", path, exc_info=True)`
- Use `_logger.info()` for operational milestones (rarely used in this codebase)
- Never use `print()` for operational output (always use logger)
- Include `exc_info=True` when logging exception context: `_logger.warning("...", exc_info=True)`

Example from `cli_production.py`:

```python
_logger = logging.getLogger(__name__)

_logger.warning(
    "production store unavailable %s; cycle's predictions will not recorded",
    _paths().production_db,
    exc_info=True,
)
```

## Comments

**When to Comment:**

- Explain *why*, not *what* — avoid restating code that's already clear from reading it
- Document hidden constraints: "Point-in-time correctness: a decision made at time T may only use information with observed_at_utc <= T"
- Document workarounds for specific real bugs: "fcntl.flock(LOCK_EX) blocks indefinitely with no built-in timeout -- a hung holder (not crashed; POSIX auto-releases on process death) would otherwise wedge every other appender forever"
- Document non-obvious invariants: "Mutable operator state lives at the runtime root, not in the repo checkout"

Example from `domain.py`:

```python

# This US sports-market project: "today" forecasting, searching, backfill defaults

# always mean US-Eastern calendar day, never host machine's local timezone or UTC

# calendar day (which drifts a day off during US evening games). Hardcoded single

# source of truth — other modules import EASTERN/eastern_today rather than

# redefining ZoneInfo("America/New_York") themselves.

EASTERN = ZoneInfo("America/New_York")
```

## Function Design

**Size:** Functions stay focused on a single responsibility. Helper functions like `_parse_snapshot_time()` are tiny (3-4 lines). Main orchestrators like `build_learned_moneyline_slate()` can be 50+ lines but are clearly decomposed.

**Parameters:**

- Use explicit parameter names (no positional-only patterns)
- Type-hint all parameters: `def append(self, event_type: str, subject_id: str, payload: dict[str, Any]) -> dict[str, Any]:`
- Use default arguments sparingly; when needed, default to `None` or safe constants
- Avoid mutable default arguments (e.g., `dict()` or `[]`)

**Return Values:**

- Always type-hint return values: `-> dict[str, Any]`, `-> Optional[float]`
- Return structured data (dicts, dataclasses) from complex operations, not tuples
- Use dataclasses for multi-field returns (e.g., `ChampionSnapshot`, `ValidationRow`)
- Single `None` return indicates failure or no result; return empty containers only when semantically correct

## Module Design

**Exports:**

- Modules explicitly export public symbols in `__all__` (when needed for clarity); otherwise rely on naming convention (no leading underscore = public)
- Private functions/classes use leading underscore

**Barrel Files:**

- The `__init__.py` in `src/model_prediction/` is minimal (version marker only)
- Dashboard submodules use explicit re-exports in `__init__.py` (see conftest.py's patching strategy for `model_prediction.dashboard.status`)

Example of explicit re-export (from conftest.py):

```python
status = sys.modules["model_prediction.dashboard.status"]
matrix = sys.modules["model_prediction.dashboard.matrix"]
backtests = sys.modules["model_prediction.dashboard.backtests"]
```

## Dataclass & Type Patterns

**Frozen dataclasses:** Use `@dataclass(frozen=True)` for immutable value objects like `BanEntry`, `ChampionSnapshot`

**Union types:** Use `|` syntax (Python 3.10+) instead of `Union[]`: `path: str | Path`

**Optional types:** Use `| None` instead of `Optional[]`: `review_after: str | None = None`

**Mapping types:** Use `collections.abc.Mapping` for read-only dict-like interfaces instead of `dict`

Example from `audit.py`:

```python
@dataclass(frozen=True)
class BanEntry:
    league: League
    canonical_team_id: str
    canonical_name: str
    configured_input: str
    reason: str = "manual_governance"
    review_after: str | None = None
```

---

*Convention analysis: 2026-09-20*
