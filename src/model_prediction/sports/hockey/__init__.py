"""Hockey Sport Module."""

from __future__ import annotations

from . import moneyline, spread, total
from .adapter import HockeyAdapter

__all__ = ["HockeyAdapter", "moneyline", "spread", "total"]
