"""Canonical repo-root/runtime-root path resolution.

Not rebuild-specific: the incumbent system may eventually route its own
mutable state through this too, so it lives at the package top level rather
than under `rebuild/`. Only the rebuild dashboard reader is actually wired
to it yet (see `dashboard/rebuild_status.py`) -- this module just stops that
one real, identified coupling (mutable rebuild DB state living inside
whichever Git checkout happens to launch the dashboard) rather than
attempting a wider migration in the same change.

Two roots, two different lifecycles:

- `repo_root`: the Git checkout. Versioned research evidence
  (`outputs/rebuild/`) belongs here -- it's reviewed, committed, meant to
  survive a `git log`.
- `runtime_root`: mutable, disposable, machine-local state (raw provider
  cache, normalized Parquet, SQLite databases, resume state, logs). Belongs
  outside Git entirely so that switching worktrees/branches, or even
  deleting and re-cloning the repo, can never silently orphan or corrupt it.

Resolution order for `runtime_root`:
1. `MODEL_PREDICTION_RUNTIME_ROOT` env var, if set -- the real external
   location once the operational cutover has happened.
2. `repo_root / "data"` otherwise -- the historical, repo-local behavior,
   preserved as a safe default so an unconfigured dev checkout (or a test
   that forgets to override) keeps working exactly as it always has,
   rather than silently writing somewhere unexpected.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


class RuntimePathError(ValueError):
    """A resolved or explicitly-constructed path configuration is unsafe."""


def migrate_legacy_state(paths: RuntimePaths) -> list[str]:
    """One-time carry-over of pre-runtime-root mutable state into runtime_root.

    The 2026-08-13 split-brain taught this repo the hard way that a HALF
    migration (some writers on the runtime root, some readers on repo
    data/) corrupts silently. The rule now is the inverse: every reader
    and writer resolves through RuntimePaths, and the historical
    repo-local files are carried over exactly once — only when the
    runtime-root file does not exist yet — via copy-to-tmp + rename so a
    concurrent reader never sees a partial file. Legacy files are never
    deleted (some are git-tracked evidence). Idempotent and safe to call
    on every open.
    """
    moved: list[str] = []
    legacy_pairs = [
        (paths.repo_root / "data" / "runs.db", paths.runs_db),
        (
            paths.repo_root / "data" / "production" / "predictions.db",
            paths.production_db,
        ),
        (
            paths.repo_root / "data" / "production_state.json",
            paths.production_state_file,
        ),
    ]
    for legacy, target in legacy_pairs:
        if legacy.is_file() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".migrating")
            shutil.copy2(legacy, tmp)
            os.replace(tmp, target)
            moved.append(f"{legacy} -> {target}")
    return moved


def rolling_models_root(repo_root: Path | str | None = None) -> Path:
    """Where scheduled retraining writes its rolling rating artifacts.

    The daily cycle retrains esports/KBO/NPB Elo artifacts every run;
    those full-file rewrites belong under the runtime root so the
    checked-in config/models/ copies stay frozen promoted artifacts.
    """
    return RuntimePaths.resolve(repo_root=repo_root).models_root


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class RuntimePaths:
    repo_root: Path
    runtime_root: Path

    def __post_init__(self) -> None:
        repo_root = self.repo_root.resolve()
        runtime_root = self.runtime_root.resolve()
        object.__setattr__(self, "repo_root", repo_root)
        object.__setattr__(self, "runtime_root", runtime_root)
        if runtime_root == repo_root / "src" / "model_prediction":
            raise RuntimePathError("runtime root must not be the production source package")
        if ".git" in runtime_root.parts:
            raise RuntimePathError("runtime root must not live inside a .git directory")

    # ── versioned evidence: stays with the repo ──────────────────────────

    @property
    def rebuild_output_root(self) -> Path:
        return self.repo_root / "outputs" / "rebuild"

    # ── mutable rebuild state: lives under runtime_root ──────────────────

    @property
    def rebuild_root(self) -> Path:
        return self.runtime_root / "rebuild"

    @property
    def rebuild_raw_root(self) -> Path:
        return self.rebuild_root / "raw"

    @property
    def rebuild_normalized_root(self) -> Path:
        return self.rebuild_root / "normalized"

    @property
    def rebuild_feature_root(self) -> Path:
        return self.rebuild_root / "features"

    @property
    def rebuild_market_root(self) -> Path:
        return self.rebuild_root / "markets"

    @property
    def rebuild_resume_root(self) -> Path:
        return self.rebuild_root / "resume_state"

    @property
    def rebuild_shadow_db(self) -> Path:
        # Directly under rebuild_root, not a "databases/" subfolder: matches
        # where these two files actually live today
        # (data/rebuild/shadow.db). A future migration is free to introduce
        # a subfolder, but that's a real data-relocation decision for the
        # live cutover to make deliberately, not something this path
        # resolver should invent as a side effect -- doing so here already
        # broke an existing test that (correctly) expects today's real
        # layout.
        return self.rebuild_root / "shadow.db"

    @property
    def rebuild_metadata_db(self) -> Path:
        return self.rebuild_root / "metadata.db"

    # ── mutable production/control-plane state ────────────────────────────
    # Everything the production canary and the run supervisor write lives
    # under runtime_root, matching the consolidation target layout:
    #
    #   runtime/
    #   ├── runs.db                    (control plane: runs + promotions)
    #   ├── production/
    #   │   ├── production.db          (predictions, decisions, snapshots, runs)
    #   │   └── production_state.json
    #   ├── research/research.db       (future shadow-ledger cutover target)
    #   ├── rebuild/shadow.db
    #   └── logs/supervisor/           (per-run worker output)

    @property
    def runs_db(self) -> Path:
        return self.runtime_root / "runs.db"

    @property
    def production_root(self) -> Path:
        return self.runtime_root / "production"

    @property
    def production_db(self) -> Path:
        return self.production_root / "production.db"

    @property
    def production_state_file(self) -> Path:
        # Directly under runtime_root, matching where the canary's state
        # file has always lived (repo data/production_state.json) so every
        # existing reader (health checks, system_health) keeps resolving
        # to the same file during the cutover.
        return self.runtime_root / "production_state.json"

    @property
    def ledgers_root(self) -> Path:
        return self.runtime_root / "ledgers"

    @property
    def models_root(self) -> Path:
        # Rolling retraining artifacts (esports/KBO/NPB ratings): the
        # scheduled cycle rewrites these every run, so they live under the
        # runtime root. config/models/ keeps only the frozen promoted
        # artifacts (git). See cli._research_models_dir() for the
        # rolling-first-with-frozen-fallback read contract.
        return self.runtime_root / "models"

    @property
    def ledgers_db(self) -> Path:
        return self.ledgers_root / "ledgers.db"

    @property
    def research_root(self) -> Path:
        return self.runtime_root / "research"

    @property
    def research_db(self) -> Path:
        return self.research_root / "research.db"

    @property
    def supervisor_log_root(self) -> Path:
        return self.log_root / "supervisor"

    @property
    def lock_root(self) -> Path:
        # All mutable runtime state is external — leases included.
        return self.runtime_root / "locks"

    @property
    def log_root(self) -> Path:
        return self.runtime_root / "logs"

    @classmethod
    def resolve(
        cls,
        *,
        repo_root: Path | str | None = None,
        require_external_runtime: bool = False,
    ) -> RuntimePaths:
        """Resolve from environment.

        ``require_external_runtime=True`` is the operational contract:
        the caller may only touch the canonical external runtime root.
        Without ``MODEL_PREDICTION_RUNTIME_ROOT`` the call raises instead
        of silently creating a second (split-brain) runtime under the
        repository — one env-less invocation used to spawn an entirely
        separate database universe next to the canonical one. Local
        development keeps the default repo ``data/`` fallback, and tests
        use :meth:`for_test`.
        """
        resolved_repo_root = Path(repo_root) if repo_root is not None else _default_repo_root()
        runtime_root_env = os.environ.get("MODEL_PREDICTION_RUNTIME_ROOT")
        if runtime_root_env:
            runtime_root = Path(runtime_root_env)
        elif require_external_runtime:
            raise RuntimeError(
                "MODEL_PREDICTION_RUNTIME_ROOT is required for operational "
                "invocations; refusing the repo-local data/ fallback because "
                "it silently creates a second runtime root (split-brain) next "
                "to the canonical one. Set the env var, or use the default "
                "resolve() for explicit local development."
            )
        else:
            runtime_root = resolved_repo_root / "data"
        return cls(repo_root=resolved_repo_root, runtime_root=runtime_root)

    @classmethod
    def for_test(cls, tmp_path: Path) -> RuntimePaths:
        """Isolated roots for tests -- never the real repo or runtime directory."""
        repo_root = tmp_path / "repo"
        runtime_root = tmp_path / "runtime"
        repo_root.mkdir(parents=True, exist_ok=True)
        runtime_root.mkdir(parents=True, exist_ok=True)
        return cls(repo_root=repo_root, runtime_root=runtime_root)
