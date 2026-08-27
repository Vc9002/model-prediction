"""Execution-ticket boundary (consolidation C, item 13).

Order execution is separated from research/shadow by an explicit ticket
contract: the dashboard (or any future execution service) issues a
short-lived HMAC-signed ticket naming the exact order it authorizes;
nothing else in the system can fabricate one. Research and shadow
pipelines have no import path to this module (enforced by
``tests/test_execution_boundary.py``) and no access to the signing
secret, which lives under the runtime root, not the repo.

Automated orders remain disabled unless separately authorized — the
ticket machinery is the boundary, not a license to trade.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from .runtime_paths import RuntimePaths

_TICKET_TTL_SECONDS = 300


def _secret_path() -> Path:
    """Per-machine signing secret under the runtime root.

    Created with mode 0600 on first use — deliberately NOT in the git
    checkout, so checking out a different branch can never change or
    expose it.
    """
    paths = RuntimePaths.resolve()
    return paths.runtime_root / "execution_secret.key"


def _load_or_create_secret() -> bytes:
    path = _secret_path()
    if path.is_file():
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_bytes(32)
    path.write_bytes(secret)
    os.chmod(path, 0o600)
    return secret


def create_ticket(order: dict[str, Any], *, ttl_seconds: int = _TICKET_TTL_SECONDS) -> str:
    """Sign an exact order ticket: ``payload.signature`` (both hex)."""
    payload = {
        "order": order,
        "issued_at": int(time.time()),
        "expires_at": int(time.time()) + ttl_seconds,
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    signature = hmac.new(_load_or_create_secret(), body, hashlib.sha256).hexdigest()
    return f"{body.decode()}.{signature}"


def verify_ticket(ticket: str) -> dict[str, Any]:
    """Validate a ticket and return its payload, or raise ValueError.

    Checks: structure, signature (constant-time compare), and expiry.
    """
    body, _, signature = ticket.rpartition(".")
    if not body or not signature:
        raise ValueError("malformed ticket")
    expected = hmac.new(_load_or_create_secret(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ValueError("invalid ticket signature")
    payload = json.loads(body)
    if int(payload.get("expires_at", 0)) < int(time.time()):
        raise ValueError("ticket expired")
    return payload


def is_ticket_valid(ticket: str) -> bool:
    """Non-raising predicate to verify if an HMAC ticket is active and unexpired."""
    try:
        verify_ticket(ticket)
        return True
    except (ValueError, TypeError, KeyError):
        return False


def extract_order(ticket: str) -> dict[str, Any] | None:
    """Safely extract order payload from a valid ticket; returns None if invalid or expired."""
    try:
        payload = verify_ticket(ticket)
        return payload.get("order")
    except (ValueError, TypeError, KeyError):
        return None
