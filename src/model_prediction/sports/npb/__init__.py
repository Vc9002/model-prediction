"""NPB Sport Module."""

from __future__ import annotations

from . import moneyline, spread, total
from .adapter import NPBAdapter

__all__ = ["NPBAdapter", "moneyline", "spread", "total"]
