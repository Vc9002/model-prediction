"""NBA Sport Module."""

from __future__ import annotations

from . import moneyline, spread, total
from .adapter import NBAAdapter

__all__ = ["NBAAdapter", "moneyline", "spread", "total"]
