"""LoL Sport Module."""

from __future__ import annotations

from . import moneyline, spread, total
from .adapter import LoLAdapter

__all__ = ["LoLAdapter", "moneyline", "spread", "total"]
