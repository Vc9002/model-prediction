"""Australian NBL lane: ESPN results, tempo model, Polymarket prices (flat ledger only until verified)."""

from __future__ import annotations

from .data_sources.nbl import refresh_nbl_history
from .domain import League
from .euroleague_forward import BasketballLaneSpec, forecast_basketball_lane
from .hockey_forward import _resolve_names
from .models.basketball_leagues import nbl_v2_model


def _resolver(name: str, data_root, known: set[str]) -> str | None:
    matches = _resolve_names(name, known)
    return matches[0] if len(matches) == 1 else None


SPEC = BasketballLaneSpec("nbl", "NBL", League.NBL, nbl_v2_model, refresh_nbl_history, _resolver)


def forecast_nbl_sport(*, data_root, args_date, config, history_client=None, **ledgers):
    return forecast_basketball_lane(
        SPEC,
        data_root=data_root,
        args_date=args_date,
        config=config,
        history_client=history_client,
        **ledgers,
    )
