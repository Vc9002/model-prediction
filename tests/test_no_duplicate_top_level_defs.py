"""Repo-wide guard: no module under src/ defines the same top-level def or class twice.

ruff's F811 skips underscore-prefixed names, so a duplicate private helper (one silently replacing the
other for the whole module) passes lint. It was found in polymarket_execute (``_is_order_not_found``).
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def duplicated_top_level_names(source: str) -> list[str]:
    tree = ast.parse(source)
    counts = Counter(
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    )
    return sorted(name for name, n in counts.items() if n > 1)


def test_the_guard_flags_a_duplicated_private_helper_and_a_duplicated_class():
    source = "def _a(): ...\ndef _a(): ...\nclass B: ...\nclass B: ...\nasync def c(): ...\ndef d(): ...\n"
    assert duplicated_top_level_names(source) == ["B", "_a"]
    assert duplicated_top_level_names("def _a(): ...\nclass _A: ...\n") == []


def test_no_module_under_src_defines_a_top_level_name_twice():
    offenders = {}
    for path in sorted(SRC.rglob("*.py")):
        names = duplicated_top_level_names(path.read_text(encoding="utf-8"))
        if names:
            offenders[str(path.relative_to(SRC))] = names
    assert offenders == {}
