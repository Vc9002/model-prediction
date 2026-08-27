"""Hard isolation checks shared by rebuild entrypoints and artifact readers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..runtime_paths import RuntimePaths
from .config import EXECUTION_ENABLED, PRODUCTION_PROMOTION, SHADOW_ONLY, RebuildConfig


class RebuildSafetyError(RuntimeError):
    """Raised before a rebuild operation can cross a production boundary."""


PROTECTED_DATA_ROOTS = (
    "data/main",
    "data/flat",
    "data/flat_v9",
    "data/gated_research",
    "data/research",
)
PRODUCTION_ADAPTER_TOKENS = ("order", "execute", "execution", "production_ledger", "live_adapter")


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _within_lexical(path: Path, root: Path) -> bool:
    """Containment WITHOUT symlink resolution — used to judge the path the
    caller supplied, before resolve() can follow a symlinked data/ tree
    out of the repository."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class RebuildPathPolicy:
    repo_root: Path
    data_root: Path
    output_root: Path
    challenger_root: Path

    @classmethod
    def from_config(cls, config: RebuildConfig) -> RebuildPathPolicy:
        policy = cls(
            config.repo_root, config.paths.data_root, config.paths.output_root, config.paths.challenger_root
        )
        policy.assert_isolated_roots()
        return policy

    def assert_isolated_roots(self) -> None:
        # expected_data mirrors config.py's own resolution (RuntimePaths,
        # honoring MODEL_PREDICTION_RUNTIME_ROOT) rather than hardcoding
        # repo_root/data/rebuild -- config.py is the only other caller that
        # resolves this, and both must agree on where "isolated" means.
        expected_data = RuntimePaths.resolve(repo_root=self.repo_root).rebuild_root
        expected_output = self.repo_root / "outputs" / "rebuild"
        expected_challengers = self.repo_root / "config" / "models" / "challengers"
        if not _within(self.data_root, expected_data):
            raise RebuildSafetyError(
                "rebuild data_root must be inside the resolved runtime root's rebuild/ directory"
            )
        if not _within(self.output_root, expected_output):
            raise RebuildSafetyError("rebuild output_root must be inside outputs/rebuild")
        if not _within(self.challenger_root, expected_challengers):
            raise RebuildSafetyError("rebuild artifacts must be inside config/models/challengers")

    def assert_runtime_write(self, path: str | Path) -> Path:
        resolved = Path(path).resolve()
        if not (_within(resolved, self.data_root) or _within(resolved, self.output_root)):
            raise RebuildSafetyError(f"rebuild runtime write outside isolated roots: {resolved}")
        for protected in PROTECTED_DATA_ROOTS:
            if _within(resolved, self.repo_root / protected):
                raise RebuildSafetyError(f"rebuild may not write protected production path: {resolved}")
        return resolved

    def assert_challenger_artifact(self, path: str | Path) -> Path:
        resolved = Path(path).resolve()
        if not _within(resolved, self.challenger_root):
            raise RebuildSafetyError(f"rebuild may only load challenger artifacts: {resolved}")
        return resolved


def assert_shadow_only(config: RebuildConfig | None = None) -> None:
    if not SHADOW_ONLY or EXECUTION_ENABLED or PRODUCTION_PROMOTION:
        raise RebuildSafetyError("rebuild hard safety constants were modified")
    if config is not None and (
        not config.rebuild.shadow_only
        or config.rebuild.execution_enabled
        or config.rebuild.production_promotion
        or config.execution.allow_live_orders
        or config.execution.allow_production_ledger_write
    ):
        raise RebuildSafetyError("rebuild live execution is disabled by design")


def reject_production_adapter(adapter: Any) -> None:
    identity = f"{type(adapter).__module__}.{type(adapter).__name__}".lower()
    if any(token in identity for token in PRODUCTION_ADAPTER_TOKENS):
        raise RebuildSafetyError(f"production order adapter is forbidden in rebuild: {identity}")


def assert_challenger_artifact_path(path: str | Path, challenger_root: str | Path) -> Path:
    resolved = Path(path).resolve()
    if not _within(resolved, Path(challenger_root).resolve()):
        raise RebuildSafetyError(f"rebuild may only load challenger artifacts: {resolved}")
    return resolved


def assert_runtime_data_root(path: str | Path, repo_root: str | Path) -> Path:
    """Allow external temporary roots, but constrain every repo-local run.

    Containment is checked on the symlink-UNRESOLVED path first: a
    repo-local path that resolves through a symlinked directory (a
    research worktree sharing the canonical checkout's ``data/`` tree,
    exactly this checkout's layout) escapes the resolved-path check and
    would otherwise let a rebuild run open a ledger in the live
    production tree. The lexical check rejects by the path the caller
    actually supplied; the resolved check then guards the write-side
    layer against symlink targets inside the repo from outside.
    """
    lexical = Path(os.path.abspath(str(path)))
    repository_lexical = Path(os.path.abspath(str(repo_root)))
    if _within_lexical(lexical, repository_lexical) and not _within_lexical(
        lexical, repository_lexical / "data" / "rebuild"
    ):
        raise RebuildSafetyError(f"repo-local rebuild data must stay inside data/rebuild: {path}")
    resolved = Path(path).resolve()
    repository = Path(repo_root).resolve()
    if _within(resolved, repository) and not _within(resolved, repository / "data" / "rebuild"):
        raise RebuildSafetyError(f"repo-local rebuild data must stay inside data/rebuild: {resolved}")
    return resolved
