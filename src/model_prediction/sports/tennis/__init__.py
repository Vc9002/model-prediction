"""Tennis Sport Module."""

from __future__ import annotations

from . import derivatives, moneyline, spread, total
from .adapter import TennisAdapter

__all__ = ["TennisAdapter", "derivatives", "moneyline", "spread", "total"]
