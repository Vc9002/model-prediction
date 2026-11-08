"""Dota 2 Sport Module."""

from __future__ import annotations

from . import moneyline, spread, total
from .adapter import Dota2Adapter

__all__ = ["Dota2Adapter", "moneyline", "spread", "total"]
