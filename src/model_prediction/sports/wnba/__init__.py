"""WNBA Sport Module."""

from __future__ import annotations

from . import moneyline, spread, total
from .adapter import WNBAAdapter

__all__ = ["WNBAAdapter", "moneyline", "spread", "total"]
