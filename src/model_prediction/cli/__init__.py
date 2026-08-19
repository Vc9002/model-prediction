"""model_prediction CLI package.

The former cli.py monolith (DD-6) is decomposed: parser.py owns the
argparse tree, main.py the dispatch, and one module per command group
(forecast, settle, daily, commands). This package's __init__ is a pure
re-export shim so `from model_prediction.cli import _X` and the
model-prediction console script keep resolving exactly as before.
"""

from __future__ import annotations

from ..config import PROJECT_ROOT as PROJECT_ROOT
from ..runtime_paths import rolling_models_root as rolling_models_root
from .commands import (  # noqa: F401 -- re-export for compat
    _clear_today_open,
    _handle_ban,
    _polymarket_slate,
    _research_models_dir,
    _row_artifact_qualified,
    _summary,
    _verify_chain,
)
from .daily import run_daily
from .forecast import (  # noqa: F401 -- re-export for compat
    _append_secondary_ledger,
    _forecast_international_sport,
    _forecast_learned_sport,
    _forecast_mlb,
    _forecast_mlb_totals_flat,
    _forecast_research_sport,
    _forecast_soccer_sport,
    _forecast_tennis_sport,
    _forecast_wnba_spread_slate,
    _forecast_wnba_spread_sport,
    _load_market_residual_model,
    _log_esports_forecast,
    _refresh_esports_ratings,
    _refresh_international_baseball_ratings,
    _select_wnba_spread_market,
)
from .main import _fail as _fail
from .main import main
from .parser import parser
from .settle import (  # noqa: F401 -- re-export for compat
    _closing_probability_for_moneyline_pick,
    _extract_market_slug,
    _find_espn_result,
    _find_soccer_result,
    _find_tennis_result,
    _identity_key,
    _load_soccer_scores,
    _settle_all_unsettled,
    _settle_esports_pick,
    _settle_international_baseball_pick,
    _settle_tennis_pick,
)
from .state import (  # noqa: F401 -- re-export for compat
    _LEDGER_LEAGUE_TO_ESPN,
    _LEDGER_LOCK,
    _TERMINAL_MARKET_STATES,
    DAILY_INTERNATIONAL_BASEBALL_SPORTS,
    DAILY_LEARNED_SPORTS,
    DUAL_LEDGER_SPORTS,
    ESPN_SPORTS,
    ESPORTS_TITLES,
    FLAT_LEDGER_SPORTS,
    RESEARCH_ONLY_DAILY_SPORTS,
    SPORTS,
)

__all__ = [
    "main",
    "parser",
    "run_daily",
]
