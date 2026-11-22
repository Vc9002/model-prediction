"""Settlement from Polymarket contract resolution when no results API covers the league."""

from __future__ import annotations

import json

import pytest

from model_prediction.cli import settle as settle_module
from model_prediction.cli.settle import _scores_for_outcome, _settle_by_resolution
from model_prediction.domain import MarketType
from model_prediction.pricing import PickResult, grade_pick

# Captured before the autouse stub below replaces it, for the closing-bound tests.
_REAL_CLOSING_LOOKUP = settle_module._closing_probability_for_moneyline_pick


@pytest.mark.parametrize(
    "market,selection,line",
    [
        ("total", "over", 3.5),
        ("total", "under", 3.5),
        ("total", "over", 6.5),
        ("total", "under", 2.5),
        ("spread", "home", -1.5),
        ("spread", "away", 1.5),
        ("spread", "home", 2.5),
        ("spread", "away", -3.5),
        ("moneyline", "home", None),
        ("moneyline", "away", None),
    ],
)
def test_synthetic_scores_grade_to_the_resolved_outcome(market, selection, line):
    for won in (True, False):
        away, home = _scores_for_outcome(market, selection, line, won)
        result = grade_pick(MarketType(market), selection, line, away, home)
        assert result is (PickResult.WIN if won else PickResult.LOSS), (
            market,
            selection,
            line,
            won,
            away,
            home,
        )


class _Ledger:
    def settle(self, pick_id, away, home, line, odds, **kwargs):
        self.args = (away, home)
        return {"result": "win"}

    def void(self, pick_id, reason):
        self.voided = reason
        return {"result": "void"}


class _Client:
    def __init__(self, state, prices, sides):
        self.state, self.prices, self.sides = state, prices, sides

    def market(self, slug):
        return {
            "marketSides": [
                {"description": d, "price": {"value": str(p)}, "team": {"name": t} if t else None}
                for (d, t), p in zip(self.sides, self.prices)
            ]
        }

    def book(self, slug):
        return {"state": self.state}


@pytest.fixture(autouse=True)
def _no_closing_line_lookup(monkeypatch):
    # The settle path also looks up a closing probability on disk; keep these tests hermetic.
    monkeypatch.setattr(
        "model_prediction.cli.settle._closing_probability_for_moneyline_pick",
        lambda *args, **kwargs: (None, None),
    )


ROW = {
    "pick_id": "p",
    "event_start_utc": "2026-10-22T18:00:00Z",
    "market_type": "total",
    "selection": "under",
    "line": 3.5,
    "home_team": "A",
    "away_team": "B",
    "rationale": "x (tsc-khl-a-b-2026-10-22-3pt5).",
}


def test_total_resolution_settles_and_unresolved_or_unclean_stay_pending():
    sides = [("Over", None), ("Under", None)]
    ledger = _Ledger()
    done = _settle_by_resolution(ROW, ledger, _Client("MARKET_STATE_EXPIRED", [0.0, 1.0], sides))
    assert done["settled"] and ledger.args == (0, 0)  # under won -> total below the line
    assert _settle_by_resolution(ROW, _Ledger(), _Client("MARKET_STATE_OPEN", [0.0, 1.0], sides)) is None
    # A terminal market with non-binary prices is never guessed. It used to be voided here, but
    # Polymarket US reports pre-close trading prices on decided markets, so the void silently
    # booked 0 pnl (207 settled-push rows on esports/darts). The pick now stays open (2026-11-21).
    ledger = _Ledger()
    out = _settle_by_resolution(ROW, ledger, _Client("MARKET_STATE_EXPIRED", [0.4, 0.6], sides))
    assert "non-binary" in out["reason"] and not out.get("voided") and not out.get("settled")
    assert not hasattr(ledger, "voided") and not hasattr(ledger, "args")


def test_spread_resolution_matches_team_and_line():
    row = {
        **ROW,
        "market_type": "spread",
        "selection": "away",
        "line": 1.5,
        "home_team": "Home Club",
        "away_team": "Away Club",
    }
    sides = [("+1.50", "Away Club"), ("-1.50", "Home Club")]
    ledger = _Ledger()
    assert _settle_by_resolution(row, ledger, _Client("MARKET_STATE_EXPIRED", [1.0, 0.0], sides))["settled"]
    away, home = ledger.args
    assert grade_pick(MarketType.SPREAD, "away", 1.5, away, home) is PickResult.WIN


@pytest.mark.parametrize(
    ("ledger_team", "market_sides", "winner"),
    [
        # Real ledger-vs-Polymarket spellings that returned "resolution side unresolved" and
        # would have voided winning picks after 7 days (2026-11-12/13).
        ("Oerebro Hockey", ["Oerebro HK", "Bjoerkloeven IF"], "Oerebro HK"),
        ("Anadolu Efes Istanbul", ["Anadolu Efes SK", "Virtus Bologna"], "Anadolu Efes SK"),
        ("Besiktas Istanbul", ["Besiktas JK", "LDLC ASVEL"], "Besiktas JK"),
    ],
)
def test_moneyline_resolution_matches_spelling_variants_by_name_tokens(ledger_team, market_sides, winner):
    row = {**ROW, "market_type": "moneyline", "selection": "away", "away_team": ledger_team, "home_team": "Z"}
    sides = [(name, name) for name in market_sides]
    prices = [1.0 if name == winner else 0.0 for name in market_sides]
    ledger = _Ledger()
    done = _settle_by_resolution(row, ledger, _Client("MARKET_STATE_EXPIRED", prices, sides))
    assert done["settled"]
    assert ledger.args == (1, 0)  # the away pick won


def test_name_token_fallback_fails_closed_on_ties_and_no_overlap():
    # Both sides share only "Istanbul" with the ledger team: ambiguous, so stay pending.
    row = {
        **ROW,
        "market_type": "moneyline",
        "selection": "away",
        "away_team": "Istanbul Basket",
        "home_team": "Z",
    }
    tie = [("Istanbul Spor", "Istanbul Spor"), ("Istanbul United", "Istanbul United")]
    out = _settle_by_resolution(row, _Ledger(), _Client("MARKET_STATE_EXPIRED", [1.0, 0.0], tie))
    assert out["reason"].startswith("resolution side unresolved")
    # No distinctive token in common at all.
    none = [("Alpha Club", "Alpha Club"), ("Omega Club", "Omega Club")]
    out = _settle_by_resolution(row, _Ledger(), _Client("MARKET_STATE_EXPIRED", [1.0, 0.0], none))
    assert out["reason"].startswith("resolution side unresolved")


def test_spread_resolution_matches_spelling_variant_and_keeps_line_check():
    row = {
        **ROW,
        "market_type": "spread",
        "selection": "away",
        "line": 1.5,
        "away_team": "Besiktas Istanbul",
        "home_team": "LDLC ASVEL Villeurbanne",
    }
    sides = [("+1.50", "Besiktas JK"), ("-1.50", "LDLC ASVEL")]
    ledger = _Ledger()
    assert _settle_by_resolution(row, ledger, _Client("MARKET_STATE_EXPIRED", [1.0, 0.0], sides))["settled"]
    away, home = ledger.args
    assert grade_pick(MarketType.SPREAD, "away", 1.5, away, home) is PickResult.WIN


class _ClosingLedger:
    def settle(self, pick_id, away, home, line, odds, **kwargs):
        self.closing = kwargs.get("closing_raw_probability")
        return {"result": "win"}


def _resolution_close(tmp_path, monkeypatch, league, quotes):
    """Settle a home-moneyline row by resolution against real snapshot files; return its close."""
    monkeypatch.setattr(settle_module, "_closing_probability_for_moneyline_pick", _REAL_CLOSING_LOOKUP)
    slug = f"aec-{league}-aaaa-bbbb-2026-11-21"
    start = "2026-11-21T19:00:00Z"
    folder = tmp_path / "odds" / league / "2026-11-21"
    folder.mkdir(parents=True)
    folder.joinpath("polymarket_snapshots.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "market_slug": slug,
                    "market_type": "moneyline",
                    "observed_at_utc": observed,
                    "market_state": "MARKET_STATE_OPEN",
                    "event_start_utc": start,
                    "long": {"description": "Bbbb", "ask": ask},
                    "short": {"description": "Aaaa", "ask": round(1.02 - ask, 2)},
                }
            )
            + "\n"
            for observed, ask in quotes
        ),
        encoding="utf-8",
    )
    row = {
        "pick_id": "p",
        "league": league.upper(),
        "event_start_utc": start,
        "market_type": "moneyline",
        "selection": "home",
        "line": None,
        "home_team": "Bbbb",
        "away_team": "Aaaa",
        # The decision quote's own time wins over the row's observed_at_utc, as on the ESPN path.
        "observed_at_utc": "2026-11-21T14:00:00Z",
        "market_quote_observed_at_utc": "2026-11-21T15:00:00Z",
        "rationale": f"x ({slug}).",
    }
    ledger = _ClosingLedger()
    sides = [("Bbbb", "Bbbb"), ("Aaaa", "Aaaa")]
    client = _Client("MARKET_STATE_EXPIRED", [1.0, 0.0], sides)
    assert _settle_by_resolution(row, ledger, client, data_root=tmp_path)["settled"]
    return ledger.closing


@pytest.mark.parametrize("league", ["ncaab", "ncaaf"])
def test_resolution_close_observed_before_the_decision_quote_is_never_the_close(
    tmp_path, monkeypatch, league
):
    # Quotes at and before the decision quote (15:00Z) only: no close, never the decision's own price.
    quotes = [("2026-11-21T14:30:00Z", 0.55), ("2026-11-21T15:00:00Z", 0.56)]
    assert _resolution_close(tmp_path, monkeypatch, league, quotes) is None


@pytest.mark.parametrize("league", ["ncaab", "ncaaf"])
def test_resolution_close_is_the_last_pre_kickoff_quote_after_the_decision(tmp_path, monkeypatch, league):
    quotes = [("2026-11-21T14:30:00Z", 0.55), ("2026-11-21T18:50:00Z", 0.62)]
    assert _resolution_close(tmp_path, monkeypatch, league, quotes) == 0.62


def test_resolution_close_for_unlisted_sport_is_unchanged(tmp_path, monkeypatch):
    quotes = [("2026-11-21T14:30:00Z", 0.55)]
    assert _resolution_close(tmp_path, monkeypatch, "nba", quotes) == 0.55


class _RecordingLedger:
    """Fails loudly on any write, so a stays-open assertion cannot pass by accident."""

    def settle(self, *args, **kwargs):
        raise AssertionError("must not settle")

    def void(self, *args, **kwargs):
        raise AssertionError("must not void")


@pytest.mark.parametrize("state", ["MARKET_STATE_HALTED", "MARKET_STATE_CLOSED"])
def test_terminal_market_with_trading_prices_leaves_the_pick_open(state):
    sides = [("Over", None), ("Under", None)]
    out = _settle_by_resolution(ROW, _RecordingLedger(), _Client(state, [0.41, 0.59], sides))
    assert out["pick_id"] == "p"
    assert "non-binary" in out["reason"] and "tsc-khl-a-b-2026-10-22-3pt5" in out["reason"]
    assert "settled" not in out and "voided" not in out


@pytest.mark.parametrize("state", ["MARKET_STATE_HALTED", "MARKET_STATE_CLOSED"])
def test_clean_terminal_prices_still_settle_win_and_loss(state):
    sides = [("Over", None), ("Under", None)]
    ledger = _Ledger()
    assert _settle_by_resolution(ROW, ledger, _Client(state, [0.0, 1.0], sides))["settled"]
    assert ledger.args == (0, 0)  # under won
    ledger = _Ledger()
    assert _settle_by_resolution(ROW, ledger, _Client(state, [1.0, 0.0], sides))["settled"]
    assert ledger.args != (0, 0)  # over won


def test_non_terminal_market_returns_none_even_with_trading_prices():
    sides = [("Over", None), ("Under", None)]
    assert (
        _settle_by_resolution(ROW, _RecordingLedger(), _Client("MARKET_STATE_OPEN", [0.41, 0.59], sides))
        is None
    )


def test_espn_fallback_reports_non_binary_terminal_and_does_not_age_out_to_void(tmp_path, monkeypatch):
    """A row with no ESPN record older than 7 days must not fall through to the unrecorded-event void."""
    from datetime import timedelta
    from types import SimpleNamespace

    from model_prediction.domain import utc_now

    start = utc_now() - timedelta(days=9)
    row = {
        **ROW,
        "status": "open",
        "league": "NBA",
        "event_start_utc": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    class _OneRowLedger(_RecordingLedger):
        def rows(self):
            return [row]

    monkeypatch.setattr(settle_module, "_find_espn_result", lambda *a, **k: None)
    monkeypatch.setattr(
        settle_module,
        "_settle_by_resolution",
        lambda r, ledger: {
            "pick_id": r["pick_id"],
            "reason": f"{settle_module._NON_BINARY_TERMINAL_REASON} [0.4, 0.6]",
        },
    )
    config = {
        "project": {
            "ledger_path": str(tmp_path / "picks.xlsx"),
            "market_odds_snapshot_path": str(tmp_path / "no_market_odds.jsonl"),
        }
    }
    out = settle_module._settle_all_unsettled(
        SimpleNamespace(), config, _OneRowLedger(), espn=SimpleNamespace(), scoreboard_cache={}
    )
    assert out["voided"] == [] and out["settled"] == []
    assert [f["pick_id"] for f in out["failures"]] == ["p"]
