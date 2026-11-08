"""Rainbow Six Sport Module."""

from __future__ import annotations

from . import moneyline, spread, total
from .adapter import RainbowSixAdapter

__all__ = ["RainbowSixAdapter", "moneyline", "spread", "total"]
