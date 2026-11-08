"""Darts Sport Module."""

from __future__ import annotations

from . import moneyline, spread, total
from .adapter import DartsAdapter

__all__ = ["DartsAdapter", "moneyline", "spread", "total"]
