"""Shared, domain-agnostic helpers for research-only backfill modules.

Extracted from esports.py and international_baseball.py, which had each
defined byte-identical copies of these functions. Any future title/league
research module should import from here instead of forking a third copy.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unicodedata
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o644)
        os.replace(temporary_name, path)
    except BaseException:
        with suppress(FileNotFoundError):
            os.unlink(temporary_name)
        raise


def backup_before_overwrite(path: Path) -> Path | None:
    """Copy an artifact's current content to a `.previous` sibling before it
    gets overwritten in place, giving a one-step rollback path.

    For esports/KBO/NPB ratings artifacts, staying current is the whole
    point (see refresh_recent_matches/_refresh_esports_ratings): they're
    intentionally overwritten under a stable filename every day by `daily`,
    unlike MLB's versioned production artifacts (mlb-elo-trend-lr-vN.json),
    so a FileExistsError-style immutability guard would just break the
    intended refresh. The real gap this closes instead: nothing preserved
    the artifact a bad refresh replaced, so a corrupted day's ratings had no
    recovery path. Keeps exactly one prior version -- not a full history --
    matching how much rollback protection was actually asked for.
    """
    if not path.exists():
        return None
    backup_path = path.with_suffix(".previous" + path.suffix)
    backup_path.write_bytes(path.read_bytes())
    return backup_path


def identity_key(value: str) -> str:
    return "".join(
        character for character in unicodedata.normalize("NFKD", value).casefold() if character.isalnum()
    )
