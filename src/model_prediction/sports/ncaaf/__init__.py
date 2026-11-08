"""NCAAF Sport Module."""

from __future__ import annotations

from . import moneyline, spread, total
from .adapter import NCAAFAdapter

__all__ = ["NCAAFAdapter", "moneyline", "spread", "total"]
