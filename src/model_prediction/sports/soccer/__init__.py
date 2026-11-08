"""Soccer Sport Module."""

from __future__ import annotations

from . import moneyline, spread, total
from .adapter import SoccerAdapter

__all__ = ["SoccerAdapter", "moneyline", "spread", "total"]
