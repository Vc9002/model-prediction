"""Basketball Sport Module (multi-league)."""

from __future__ import annotations

from . import moneyline, spread, total
from .adapter import BasketballAdapter

__all__ = ["BasketballAdapter", "moneyline", "spread", "total"]
