"""NFL Sport Module."""

from __future__ import annotations

from . import moneyline, spread, total
from .adapter import NFLAdapter

__all__ = ["NFLAdapter", "moneyline", "spread", "total"]
