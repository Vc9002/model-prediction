"""Targeted tests for cli.py's highest-risk functions, which previously had
zero direct test coverage despite being the entire CLI's largest module
(2,500+ lines, ~40 subcommands).

Scope is intentionally narrow: _verify_chain (audit tamper-detection, the
one thing that would silently stop working if it broke) and
_clear_today_open (the re-forecast replacement logic fixed earlier this
session for both its date-matching and started-game guards).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from model_prediction import cli
from model_prediction.cli import _clear_today_open, _find_tennis_result, _verify_chain
from model_prediction.domain import (
    League,
    MarketType,
    ModelOrigin,
    ModelState,
    PickRequest,
)
from model_prediction.eligibility import EligibilityResult, RecordType
from model_prediction.entities import CanonicalTeam
from model_prediction.forward import MLBForwardCandidate
from model_prediction.learned_forward import LearnedForwardCandidate
from model_prediction.ledger import PickLedger
from model_prediction.units import Exposure

AWAY = CanonicalTeam("mlb-bos", League.MLB, "Boston Red Sox", "BOS", True, None, None, ())
HOME = CanonicalTeam("mlb-nyy", League.MLB, "New York Yankees", "NYY", True, None, None, ())


def _ledger(tmp_path) -> PickLedger:
    return PickLedger(tmp_path / "picks.xlsx", tmp_path / "events.jsonl")


class _CaptureLedger:
    def __init__(self) -> None:
        self.appended = []

    def exposure(self, request, now=None, **kwargs):
        return Exposure()

    def append_evaluated(self, request, eligibility, now=None):
        self.appended.append((request, eligibility))
        return {"pick_id": f"pick-{len(self.appended)}"}


def _international_config(*, min_edge: float) -> dict:
    return {
        "models": {
            "KBO": {
                "min_edge": min_edge,
                "research_confidence_gate": 0.0,
                "status": "shadow_qualified",
            }
        },
        "project": {
            "maximum_data_age_hours": 12,
            "maximum_unreviewed_market_disagreement": 0.10,
        },
        "bankroll": {},
    }


def _international_forecast() -> dict:
    return {
        "model_version": "kbo-tie-aware-elo-v2",
        "priced_contracts": [
            {
                "event_start_utc": "2026-07-27T10:00:00Z",
                "event_id": "kbo-game-1",
                "market_slug": "kbo-game-1",
                "teams": ["Hanwha", "Doosan"],
                "observed_at_utc": "2026-07-26T10:00:00Z",
                "artifact_hash": "artifact-hash",
                "sides": [
                    {
                        "team": "Hanwha",
                        "model_fair_settlement_value": 0.40,
                        "executable_ask": 0.45,
                        "tie_probability": 0.04,
                    },
                    {
                        "team": "Doosan",
                        "model_fair_settlement_value": 0.60,
                        "executable_ask": 0.55,
                        "tie_probability": 0.04,
                    },
                ],
                "min_team_games": 20,
            }
        ],
    }


def _esports_forecast() -> dict:
    base = {
        "event_start_utc": "2026-07-27T10:00:00Z",
        "teams": ["Team A", "Team B"],
        "observed_at_utc": "2026-07-26T10:00:00Z",
        "artifact_hash": "artifact-hash",
    }
    return {
        "title": "lol",
        "model_version": "lol-tiered-elo-v4",
        "priced_contracts": [
            {
                **base,
                "event_id": "valid",
                "market_slug": "valid",
                "gated_research_eligible": True,
                "sides": [
                    {"team": "Team A", "model_probability": 0.60, "executable_ask": 0.55},
                    {"team": "Team B", "model_probability": 0.40, "executable_ask": 0.45},
                ],
            },
            {
                **base,
                "event_id": "negative-edge",
                "market_slug": "negative-edge",
                "gated_research_eligible": True,
                "sides": [
                    {"team": "Team A", "model_probability": 0.60, "executable_ask": 0.62},
                    {"team": "Team B", "model_probability": 0.40, "executable_ask": 0.38},
                ],
            },
            {
                **base,
                "event_id": "untrained-team",
                "market_slug": "untrained-team",
                "gated_research_eligible": False,
                "sides": [
                    {"team": "Team A", "model_probability": 0.65, "executable_ask": 0.55},
                    {"team": "Team B", "model_probability": 0.35, "executable_ask": 0.45},
                ],
            },
        ],
    }


def test_esports_research_keeps_unvalidated_teams_and_gated_requires_positive_edge(
    monkeypatch,
) -> None:
    monkeypatch.setattr(cli, "utc_now", lambda: datetime(2026, 7, 26, 12, tzinfo=UTC))
    research = _CaptureLedger()
    gated = _CaptureLedger()
    config = {
        "models": {
            "LOL": {
                "min_edge": 0.02,
                "research_confidence_gate": 0.0,
                "status": "shadow_qualified",
            }
        },
        "project": {
            "maximum_data_age_hours": 12,
            "maximum_unreviewed_market_disagreement": 0.10,
        },
        "bankroll": {},
    }

    logged = cli._log_esports_forecast(
        _esports_forecast(), config, research, gated_ledger=gated
    )

    # Every safely priced candidate reaches the research ledger, including
    # the unvalidated/new-team one (as a downgraded NO_CALL row) -- only the
    # gated ledger is a curated subset.
    assert logged == 3
    assert len(research.appended) == 3
    assert len(gated.appended) == 1
    assert gated.appended[0][0].event_id == "valid"
    by_event = {request.event_id: eligibility for request, eligibility in research.appended}
    assert by_event["negative-edge"].reason_code == "NO_CALL_LOW_EDGE"
    assert by_event["untrained-team"].reason_code == "NO_CALL_MODEL_UNVALIDATED"


def test_esports_exposure_check_happens_while_ledger_lock_is_held(monkeypatch) -> None:
    """Exposure must be computed and the row appended inside one held
    _LEDGER_LOCK critical section, not as two separately-lockable steps --
    otherwise two concurrent forecast threads could both read the same stale
    exposure before either writes (in-process TOCTOU)."""
    monkeypatch.setattr(cli, "utc_now", lambda: datetime(2026, 7, 26, 12, tzinfo=UTC))

    lock_held_when_exposure_checked: list[bool] = []

    class _LockCheckingLedger(_CaptureLedger):
        def exposure(self, request, now=None, **kwargs):
            lock_held_when_exposure_checked.append(cli._LEDGER_LOCK.locked())
            return super().exposure(request, now=now, **kwargs)

    research = _LockCheckingLedger()
    gated = _CaptureLedger()
    config = {
        "models": {
            "LOL": {
                "min_edge": 0.02,
                "research_confidence_gate": 0.0,
                "status": "shadow_qualified",
            }
        },
        "project": {
            "maximum_data_age_hours": 12,
            "maximum_unreviewed_market_disagreement": 0.10,
        },
        "bankroll": {},
    }

    cli._log_esports_forecast(_esports_forecast(), config, research, gated_ledger=gated)

    assert lock_held_when_exposure_checked, "expected exposure() to be called at least once"
    assert all(lock_held_when_exposure_checked), (
        "exposure() must be checked while _LEDGER_LOCK is held"
    )


def test_esports_flat_mode_never_writes_research_or_gated(monkeypatch) -> None:
    monkeypatch.setattr(cli, "utc_now", lambda: datetime(2026, 7, 26, 12, tzinfo=UTC))
    research = _CaptureLedger()
    gated = _CaptureLedger()
    config = {
        "models": {"LOL": {"status": "shadow_qualified"}},
        "project": {},
        "bankroll": {},
    }

    logged = cli._log_esports_forecast(
        _esports_forecast(),
        config,
        research,
        flat_mode=True,
        gated_ledger=gated,
    )

    assert logged == 0
    assert research.appended == []
    assert gated.appended == []


def test_esports_logging_failure_is_recorded_not_silently_discarded(monkeypatch) -> None:
    """A per-contract ValueError/KeyError during logging (e.g. request.
    validate() rejecting a malformed event_start_utc) must be recorded in
    the forecast's errors list and logged via logger.warning -- not
    silently swallowed by a bare `except ...: continue` -- while every
    other contract in the same batch still gets processed normally."""
    monkeypatch.setattr(cli, "utc_now", lambda: datetime(2026, 7, 26, 12, tzinfo=UTC))
    research = _CaptureLedger()
    gated = _CaptureLedger()
    config = {
        "models": {
            "LOL": {
                "min_edge": 0.02,
                "research_confidence_gate": 0.0,
                "status": "shadow_qualified",
            }
        },
        "project": {
            "maximum_data_age_hours": 12,
            "maximum_unreviewed_market_disagreement": 0.10,
        },
        "bankroll": {},
    }
    forecast = _esports_forecast()
    forecast["priced_contracts"].append(
        {
            "event_id": "bad-event",
            "market_slug": "bad-event",
            "teams": ["Team A", "Team B"],
            "observed_at_utc": "2026-07-26T10:00:00Z",
            "artifact_hash": "artifact-hash",
            # Already started relative to the mocked utc_now() above ->
            # request.validate() raises ValueError.
            "event_start_utc": "2020-01-01T00:00:00Z",
            "gated_research_eligible": True,
            "sides": [
                {"team": "Team A", "model_probability": 0.60, "executable_ask": 0.55},
                {"team": "Team B", "model_probability": 0.40, "executable_ask": 0.45},
            ],
        }
    )

    logged = cli._log_esports_forecast(forecast, config, research, gated_ledger=gated)

    # The three originally-valid contracts are unaffected by the bad one.
    assert logged == 3
    assert len(research.appended) == 3
    assert forecast["errors"] == [
        {
            "event_id": "bad-event",
            "reason": "ValueError: cannot create a call after the event has started",
        }
    ]


def _mlb_totals_candidate(market_type: MarketType, selection: str, line: float | None) -> MLBForwardCandidate:
    return MLBForwardCandidate(
        event_id="mlb-totals-1",
        event_start_utc="2026-07-27T23:00:00Z",
        away_team="Boston Red Sox",
        home_team="New York Yankees",
        market_type=market_type,
        selection=selection,
        line=line,
        sportsbook="polymarket_us",
        american_odds=-110,
        raw_probability=0.55,
        shrunk_probability=0.53,
        no_vig_probability=0.52,
        uncertainty=0.02,
        rationale="fixture",
        risks="fixture",
        observed_at_utc="2026-07-27T19:00:00Z",
        model_name="Measured Edge Totals",
        model_version="measured-edge-totals-v1",
        model_artifact_hash="hash",
        calibration_version="measured-edge-totals-v1",
        feature_schema_version="mlb-analyst-poisson-trend-v0.2",
    )


def test_mlb_totals_flat_keeps_total_and_spread_but_not_moneyline_and_never_touches_main_ledger(
    monkeypatch, registry, ban_list
) -> None:
    monkeypatch.setattr(cli, "utc_now", lambda: datetime(2026, 7, 27, 20, tzinfo=UTC))
    monkeypatch.setattr(cli, "load_formula_spec", lambda path: object())
    monkeypatch.setattr(cli, "MLBMarketOddsFeed", lambda *args, **kwargs: object())

    candidates = [
        _mlb_totals_candidate(MarketType.MONEYLINE, "home", None),
        _mlb_totals_candidate(MarketType.SPREAD, "away", -1.5),
        _mlb_totals_candidate(MarketType.TOTAL, "over", 8.5),
    ]
    monkeypatch.setattr(
        cli, "build_mlb_slate", lambda *args, **kwargs: (candidates, [], 1)
    )

    flat_ledger = _CaptureLedger()
    config = {"project": {}, "bankroll": {}}

    result = cli._forecast_mlb_totals_flat(
        "2026-07-27", True, config, registry, ban_list, flat_ledger, None
    )

    assert result["market_candidates"] == 2
    assert result["total_candidates"] == 1
    assert result["spread_candidates"] == 1
    assert len(flat_ledger.appended) == 2
    logged_types = {request.market_type for request, _eligibility in flat_ledger.appended}
    assert logged_types == {MarketType.TOTAL, MarketType.SPREAD}
    spread_request = next(
        request for request, _e in flat_ledger.appended if request.market_type is MarketType.SPREAD
    )
    assert spread_request.line == -1.5
    assert spread_request.selection == "away"


def _soccer_forecast() -> dict:
    return {
        "model_code_hash": "soccer-code-hash",
        "priced_contracts": [
            {
                "event_id": "soccer-1",
                "event_start_utc": "2026-07-27T19:00:00Z",
                "away_team": "Away FC",
                "home_team": "Home FC",
                "market_type": "total",
                "selection": "over",
                "line": 2.5,
                "executable_ask": 0.40,
                "model_probability": 0.60,
                "model_uncertainty": 0.03,
                "model_version": "soccer-poisson-dc-v1",
                "rationale": "fixture",
                "market_slug": "soccer-1-total",
                "observed_at_utc": "2026-07-27T10:00:00Z",
                "feature_basis": {"min_team_games": 20},
            }
        ],
    }


def _soccer_config(*, status: str, min_edge: float = 0.05) -> dict:
    return {
        "models": {"SOCCER": {"status": status, "min_edge": min_edge}},
        "project": {
            "maximum_data_age_hours": 12,
            "maximum_unreviewed_market_disagreement": 0.10,
        },
        "bankroll": {},
    }


def test_soccer_flat_ledger_logs_every_contract_regardless_of_eligibility(
    monkeypatch,
) -> None:
    """Flat mode logs every priced contract regardless of the eligibility
    outcome -- same "show everything" semantics every other sport's flat
    forecast uses. (Operator directive, 2026-08-02, "remove all promotion
    qualification": status="research" no longer blocks a call the way it
    used to -- can_create_qualified_call no longer gates on promotion tier
    -- so this fixture's contract, well clear of min_edge, is QUALIFIED even
    at status="research" now; flat still logs it either way.)"""
    monkeypatch.setattr(cli, "utc_now", lambda: datetime(2026, 7, 27, 12, tzinfo=UTC))
    monkeypatch.setattr(cli, "build_soccer_total_slate", lambda **kwargs: _soccer_forecast())
    flat_ledger = _CaptureLedger()

    result = cli._forecast_soccer_sport(
        data_root="unused",
        args_date="2026-07-27",
        config=_soccer_config(status="research"),
        research_ledger=None,
        gated_ledger=None,
        flat_ledger=flat_ledger,
        main_ledger=None,
    )

    assert result["flat_logged"] == 1
    assert len(flat_ledger.appended) == 1
    request, eligibility = flat_ledger.appended[0]
    assert request.event_id == "soccer-1"
    assert eligibility.reason_code == "QUALIFIED"


def test_soccer_main_ledger_mirrors_gated_ledger_exactly(monkeypatch) -> None:
    """main_ledger must receive a pick if and only if gated_ledger does --
    same eligibility result, same genuinely_eligible gate. Uses
    status=shadow_qualified here (not soccer's current real config) purely
    to exercise the CALL path this test targets."""
    monkeypatch.setattr(cli, "utc_now", lambda: datetime(2026, 7, 27, 12, tzinfo=UTC))
    monkeypatch.setattr(cli, "build_soccer_total_slate", lambda **kwargs: _soccer_forecast())
    research = _CaptureLedger()
    gated = _CaptureLedger()
    main = _CaptureLedger()

    result = cli._forecast_soccer_sport(
        data_root="unused",
        args_date="2026-07-27",
        config=_soccer_config(status="shadow_qualified", min_edge=0.02),
        research_ledger=research,
        gated_ledger=gated,
        flat_ledger=None,
        main_ledger=main,
    )

    assert result["gated_logged"] == 1
    assert result["main_logged"] == 1
    assert len(gated.appended) == 1
    assert len(main.appended) == 1
    assert gated.appended[0][0].event_id == main.appended[0][0].event_id == "soccer-1"


def test_soccer_main_ledger_stays_empty_when_gated_ledger_does(monkeypatch) -> None:
    """A high min_edge that blocks Gated Research must also block Main --
    they share one eligibility computation."""
    monkeypatch.setattr(cli, "utc_now", lambda: datetime(2026, 7, 27, 12, tzinfo=UTC))
    monkeypatch.setattr(cli, "build_soccer_total_slate", lambda **kwargs: _soccer_forecast())
    research = _CaptureLedger()
    gated = _CaptureLedger()
    main = _CaptureLedger()

    result = cli._forecast_soccer_sport(
        data_root="unused",
        args_date="2026-07-27",
        config=_soccer_config(status="shadow_qualified", min_edge=0.90),
        research_ledger=research,
        gated_ledger=gated,
        flat_ledger=None,
        main_ledger=main,
    )

    assert result["gated_logged"] == 0
    assert result["main_logged"] == 0
    assert gated.appended == []
    assert main.appended == []
    assert len(research.appended) == 1


def test_soccer_gated_and_main_blocked_when_either_team_lacks_real_history(monkeypatch) -> None:
    """A contract whose feature_basis shows a team resting on the neutral
    cold-start default (min_team_games below MINIMUM_TEAM_GAMES) must not
    reach Gated Research or Main even with a comfortably clearing edge --
    "genuinely eligible" requires a real model opinion, not just a
    synthetic-prior matchup that happens to price with high edge."""
    monkeypatch.setattr(cli, "utc_now", lambda: datetime(2026, 7, 27, 12, tzinfo=UTC))
    forecast = _soccer_forecast()
    forecast["priced_contracts"][0]["feature_basis"] = {"min_team_games": 3}
    monkeypatch.setattr(cli, "build_soccer_total_slate", lambda **kwargs: forecast)
    research = _CaptureLedger()
    gated = _CaptureLedger()
    main = _CaptureLedger()

    result = cli._forecast_soccer_sport(
        data_root="unused",
        args_date="2026-07-27",
        config=_soccer_config(status="shadow_qualified", min_edge=0.02),
        research_ledger=research,
        gated_ledger=gated,
        flat_ledger=None,
        main_ledger=main,
    )

    assert result["gated_logged"] == 0
    assert result["main_logged"] == 0
    assert gated.appended == []
    assert main.appended == []
    assert len(research.appended) == 1


def test_flat_forecast_replace_today_clears_soccer_research_gated_main_symmetrically(
    monkeypatch, tmp_path
) -> None:
    """Real gap fixed 2026-08-02: flat-forecast's replace_today only ever
    cleared flat_ledger before this fix. Soccer now writes research/gated/
    main together with flat on every command variant (main+flat and
    research+gated are each a pair -- see _forecast_soccer_sport's
    docstring), so a second same-day flat-forecast run duplicated every
    soccer row in the three ledgers that never got cleared -- only flat
    stayed deduped.

    Note: this replicates the same clear-then-forecast sequence cli.py's
    dispatch runs, proving the pattern itself is idempotent -- it does not
    invoke main()/argparse (no test in this suite does), so it won't catch a
    future regression in the dispatch's own branch condition. That condition
    (`elif replace_today and log and is_flat and "soccer" in {...}:` in
    cli.py) was verified by direct reading, not by this test alone."""
    monkeypatch.setattr(cli, "utc_now", lambda: datetime(2026, 7, 27, 12, tzinfo=UTC))
    monkeypatch.setattr(cli, "build_soccer_total_slate", lambda **kwargs: _soccer_forecast())
    config = _soccer_config(status="shadow_qualified", min_edge=0.02)
    research = PickLedger(tmp_path / "research" / "soccer.xlsx")
    gated = PickLedger(tmp_path / "gated_research" / "soccer.xlsx")
    flat = PickLedger(tmp_path / "flat_picks.xlsx")
    main = PickLedger(tmp_path / "picks.xlsx")

    def run_one_flat_forecast_cycle() -> None:
        # Mirrors cli.py's `elif replace_today and log and is_flat and
        # "soccer" in {...}:` branch exactly: clear research/gated/main,
        # clear flat (the pre-existing is_flat branch), then forecast.
        _clear_today_open(research, "2026-07-27", by_event_date=True)
        _clear_today_open(gated, "2026-07-27", by_event_date=True)
        _clear_today_open(main, "2026-07-27", by_event_date=True)
        _clear_today_open(flat, "2026-07-27", by_event_date=True)
        cli._forecast_soccer_sport(
            data_root="unused",
            args_date="2026-07-27",
            config=config,
            research_ledger=research,
            gated_ledger=gated,
            flat_ledger=flat,
            main_ledger=main,
        )

    run_one_flat_forecast_cycle()
    run_one_flat_forecast_cycle()

    assert len(research.rows()) == 1
    assert len(gated.rows()) == 1
    assert len(flat.rows()) == 1
    assert len(main.rows()) == 1


def test_soccer_logging_failure_is_recorded_not_silently_discarded(monkeypatch) -> None:
    """A per-contract ValueError (malformed event_start_utc) must be
    recorded in the forecast's errors list, not silently swallowed by the
    bare `except (DuplicatePickError, KeyError, ValueError): continue` this
    used to be -- and the other, valid contract in the same batch must
    still get logged."""
    monkeypatch.setattr(cli, "utc_now", lambda: datetime(2026, 7, 27, 12, tzinfo=UTC))
    forecast = _soccer_forecast()
    forecast["priced_contracts"].append(
        {
            "event_id": "soccer-bad",
            # Already started relative to the mocked utc_now() above ->
            # request.validate() raises ValueError.
            "event_start_utc": "2020-01-01T00:00:00Z",
            "away_team": "Away FC",
            "home_team": "Home FC",
            "market_type": "total",
            "selection": "over",
            "line": 2.5,
            "executable_ask": 0.40,
            "model_probability": 0.60,
            "model_uncertainty": 0.03,
            "model_version": "soccer-poisson-dc-v1",
            "rationale": "fixture-bad",
            "market_slug": "soccer-bad-total",
            "observed_at_utc": "2026-07-27T10:00:00Z",
        }
    )
    monkeypatch.setattr(cli, "build_soccer_total_slate", lambda **kwargs: forecast)
    research = _CaptureLedger()

    result = cli._forecast_soccer_sport(
        data_root="unused",
        args_date="2026-07-27",
        config=_soccer_config(status="research"),
        research_ledger=research,
        gated_ledger=None,
        flat_ledger=None,
        main_ledger=None,
    )

    assert result["logged"] == 1
    assert len(research.appended) == 1
    assert research.appended[0][0].event_id == "soccer-1"
    assert result["errors"] == [
        {
            "event_id": "soccer-bad",
            "reason": "ValueError: cannot create a call after the event has started",
        }
    ]


def test_daily_forecast_roster_includes_soccer_and_both_international_baseball_leagues() -> None:
    assert "soccer" not in cli.DAILY_LEARNED_SPORTS
    assert "soccer" in cli.SPORTS
    assert set(cli.DAILY_INTERNATIONAL_BASEBALL_SPORTS) == {"kbo", "npb"}
    assert set(cli.FLAT_LEDGER_SPORTS) == {"mlb", "nba", "wnba", "nfl"}
    assert set(cli.RESEARCH_ONLY_DAILY_SPORTS) == {
        "soccer",
        "tennis",
        "lol",
        "cs2",
        "dota2",
        "valorant",
        "rainbow_six",
        "kbo",
        "npb",
    }
    assert not set(cli.FLAT_LEDGER_SPORTS) & set(cli.RESEARCH_ONLY_DAILY_SPORTS)


class _FakeTennisESPN:
    def __init__(self, scoreboard):
        self._scoreboard = scoreboard

    def scoreboard(self, league, game_date):
        assert league == "WTA"
        return self._scoreboard


def _tennis_row(**overrides):
    row = {
        "away_team": "Alpha Player",
        "original_away_team": "Alpha Player",
        "home_team": "Beta Player",
        "original_home_team": "Beta Player",
    }
    row.update(overrides)
    return row


def _tennis_scoreboard(*, completed=True, away_wins=True, slug="womens-singles"):
    return {
        "events": [
            {
                "id": "e1",
                "date": "2026-07-27T20:00:00Z",
                "groupings": [
                    {
                        "competitions": [
                            {
                                "id": "c1",
                                "type": {"slug": slug},
                                "status": {"type": {"completed": completed, "name": "STATUS_FINAL"}},
                                "competitors": [
                                    {
                                        "homeAway": "away",
                                        "winner": away_wins,
                                        "athlete": {"displayName": "Alpha Player"},
                                    },
                                    {
                                        "homeAway": "home",
                                        "winner": not away_wins,
                                        "athlete": {"displayName": "Beta Player"},
                                    },
                                ],
                            }
                        ]
                    }
                ],
            }
        ]
    }


def test_find_tennis_result_matches_singles_by_player_name_and_encodes_winner() -> None:
    espn = _FakeTennisESPN(_tennis_scoreboard(away_wins=True))
    result = _find_tennis_result(espn, "2026-07-27", _tennis_row())
    assert result == {
        "completed": True,
        "status_name": "STATUS_FINAL",
        "away_score": 1,
        "home_score": 0,
    }


def test_find_tennis_result_excludes_doubles_draws() -> None:
    espn = _FakeTennisESPN(_tennis_scoreboard(slug="womens-doubles"))
    assert _find_tennis_result(espn, "2026-07-27", _tennis_row()) is None


def test_find_tennis_result_returns_none_for_unmatched_players() -> None:
    espn = _FakeTennisESPN(_tennis_scoreboard())
    row = _tennis_row(away_team="Nobody", original_away_team="Nobody")
    assert _find_tennis_result(espn, "2026-07-27", row) is None


def test_find_tennis_result_pending_while_match_not_yet_completed() -> None:
    espn = _FakeTennisESPN(_tennis_scoreboard(completed=False))
    result = _find_tennis_result(espn, "2026-07-27", _tennis_row())
    assert result["completed"] is False


def _tennis_forecast() -> dict:
    return {
        "model_code_hash": "tennis-code-hash",
        "priced_contracts": [
            {
                "event_id": "tennis-1",
                "event_start_utc": "2026-07-27T19:00:00Z",
                "away_team": "Alpha Player",
                "home_team": "Beta Player",
                "market_type": "moneyline",
                "selection": "away",
                "line": None,
                "executable_ask": 0.45,
                "model_probability": 0.55,
                "model_uncertainty": 0.05,
                "model_version": "tennis-surface-elo-v1",
                "rationale": "fixture",
                "market_slug": "wta-alpha-beta-2026",
                "observed_at_utc": "2026-07-27T10:00:00Z",
                "feature_basis": {"min_player_matches": 20},
            }
        ],
    }


def _tennis_config(*, status: str, min_edge: float = 0.05) -> dict:
    return {
        "models": {"TENNIS": {"status": status, "min_edge": min_edge}},
        "project": {
            "maximum_data_age_hours": 12,
            "maximum_unreviewed_market_disagreement": 0.10,
        },
        "bankroll": {},
    }


def test_tennis_logging_failure_is_recorded_not_silently_discarded(monkeypatch) -> None:
    """A per-contract ValueError (malformed event_start_utc) must be
    recorded in the forecast's errors list, not silently swallowed by the
    bare `except (DuplicatePickError, KeyError, ValueError): continue` this
    used to be -- and the other, valid contract in the same batch must
    still get logged."""
    monkeypatch.setattr(cli, "utc_now", lambda: datetime(2026, 7, 27, 12, tzinfo=UTC))
    forecast = _tennis_forecast()
    forecast["priced_contracts"].append(
        {
            "event_id": "tennis-bad",
            # Already started relative to the mocked utc_now() above ->
            # request.validate() raises ValueError.
            "event_start_utc": "2020-01-01T00:00:00Z",
            "away_team": "Gamma Player",
            "home_team": "Delta Player",
            "market_type": "moneyline",
            "selection": "away",
            "line": None,
            "executable_ask": 0.45,
            "model_probability": 0.55,
            "model_uncertainty": 0.05,
            "model_version": "tennis-surface-elo-v1",
            "rationale": "fixture-bad",
            "market_slug": "wta-gamma-delta-2026",
            "observed_at_utc": "2026-07-27T10:00:00Z",
        }
    )
    monkeypatch.setattr(cli, "build_tennis_slate", lambda **kwargs: forecast)
    research = _CaptureLedger()

    result = cli._forecast_tennis_sport(
        data_root="unused",
        args_date="2026-07-27",
        config=_tennis_config(status="shadow_qualified", min_edge=0.02),
        research_ledger=research,
        gated_ledger=None,
    )

    assert result["logged"] == 1
    assert len(research.appended) == 1
    assert research.appended[0][0].event_id == "tennis-1"
    assert result["errors"] == [
        {
            "event_id": "tennis-bad",
            "reason": "ValueError: cannot create a call after the event has started",
        }
    ]


def test_tennis_main_ledger_mirrors_gated_ledger_exactly(monkeypatch) -> None:
    """Same relationship as soccer's equivalent test: main_ledger receives a
    pick if and only if gated_ledger does -- one shared eligibility result."""
    monkeypatch.setattr(cli, "utc_now", lambda: datetime(2026, 7, 27, 12, tzinfo=UTC))
    monkeypatch.setattr(cli, "build_tennis_slate", lambda **kwargs: _tennis_forecast())
    research = _CaptureLedger()
    gated = _CaptureLedger()
    main = _CaptureLedger()

    result = cli._forecast_tennis_sport(
        data_root="unused",
        args_date="2026-07-27",
        config=_tennis_config(status="shadow_qualified", min_edge=0.02),
        research_ledger=research,
        gated_ledger=gated,
        main_ledger=main,
    )

    assert result["gated_logged"] == 1
    assert result["main_logged"] == 1
    assert gated.appended[0][0].event_id == main.appended[0][0].event_id == "tennis-1"


def test_tennis_gated_and_main_blocked_when_either_player_lacks_real_history(monkeypatch) -> None:
    """A contract whose feature_basis shows a player resting on thin history
    (min_player_matches below MINIMUM_PLAYER_MATCHES) must not reach Gated
    Research or Main even with a comfortably clearing edge."""
    monkeypatch.setattr(cli, "utc_now", lambda: datetime(2026, 7, 27, 12, tzinfo=UTC))
    forecast = _tennis_forecast()
    forecast["priced_contracts"][0]["feature_basis"] = {"min_player_matches": 3}
    monkeypatch.setattr(cli, "build_tennis_slate", lambda **kwargs: forecast)
    research = _CaptureLedger()
    gated = _CaptureLedger()
    main = _CaptureLedger()

    result = cli._forecast_tennis_sport(
        data_root="unused",
        args_date="2026-07-27",
        config=_tennis_config(status="shadow_qualified", min_edge=0.02),
        research_ledger=research,
        gated_ledger=gated,
        main_ledger=main,
    )

    assert result["gated_logged"] == 0
    assert result["main_logged"] == 0
    assert gated.appended == []
    assert main.appended == []
    assert len(research.appended) == 1


def test_tennis_settlement_populates_clv_from_captured_closing_snapshot(tmp_path) -> None:
    """Operator directive, 2026-07-31: CLV wired for every model, not just
    MLB -- tennis should read the same per-sport-date snapshot history the
    daily slate capture already writes (data/odds/tennis/{date}/...)."""
    from model_prediction.cli import _settle_tennis_pick
    from model_prediction.data_sources.polymarket_us import PolymarketSnapshotStore

    ledger = _ledger(tmp_path)
    event_start_utc = "2026-07-27T20:00:00Z"
    request = PickRequest(
        event_start_utc=event_start_utc,
        event_id="event-tennis-1",
        league=League.TENNIS,
        away_team="Alpha Player",
        home_team="Beta Player",
        market_type=MarketType.MONEYLINE,
        selection="away",
        line=None,
        sportsbook="polymarket_us",
        american_odds=110,
        model_probability=0.55,
        model_uncertainty=0.05,
        model_version="tennis-surface-elo-v1",
        rationale="Surface-blended Elo; executable ask 0.4500 (market_slug=wta-alpha-beta-2026).",
        risks="",
        model_origin=ModelOrigin.STATISTICAL_MODEL,
        model_state=ModelState.SHADOW_QUALIFIED,
    )
    away_player = CanonicalTeam(
        "Alpha Player", League.TENNIS, "Alpha Player", "Alpha Player", True, None, None, ()
    )
    home_player = CanonicalTeam(
        "Beta Player", League.TENNIS, "Beta Player", "Beta Player", True, None, None, ()
    )
    eligibility = EligibilityResult(
        RecordType.QUALIFIED_SHADOW_CALL,
        "CALL",
        "QUALIFIED",
        0.5,
        60,
        0.05,
        0.05,
        away_player,
        home_player,
    )
    row = ledger.append_evaluated(request, eligibility, now=datetime(2026, 7, 27, 10, tzinfo=UTC))

    store = PolymarketSnapshotStore.for_sport_date(tmp_path, "tennis", "2026-07-27")
    store.append(
        {
            "provider": "polymarket_us",
            "market_slug": "wta-alpha-beta-2026",
            "observed_at_utc": "2026-07-27T15:00:00Z",
            "long": {"description": "Alpha Player", "ask": 0.58},
            "short": {"description": "Beta Player", "ask": 0.44},
        }
    )
    espn = _FakeTennisESPN(_tennis_scoreboard(away_wins=True))
    result = _settle_tennis_pick(row, ledger, espn, data_root=tmp_path)
    assert result is not None and result.get("settled") is True
    settled_row = next(r for r in ledger.rows() if r["pick_id"] == row["pick_id"])
    assert settled_row["probability_clv"] != ""
    assert float(settled_row["closing_raw_implied_probability"]) == pytest.approx(0.58)


def test_invalid_quote_timestamp_keeps_mlb_model_opinion_visible_but_non_executable(
    monkeypatch,
    tmp_path,
) -> None:
    observed = datetime(2026, 7, 26, 12, tzinfo=UTC)
    candidate = LearnedForwardCandidate(
        event_id="mlb-1",
        event_start_utc="2026-07-27T00:00:00Z",
        away_team="Boston Red Sox",
        home_team="New York Yankees",
        market_type="moneyline",
        selection="home",
        model_probability=0.62,
        home_probability=0.62,
        confidence_threshold=0.55,
        call=True,
        action="QUALIFIED_SHADOW_CALL",
        reason="CALL_LEARNED_CONFIDENCE",
        model_version="mlb-test",
        model_artifact_hash="artifact-hash",
        model_qualified=True,
        feature_basis={"elo_probability": 0.60, "trend_gap": 0.1},
        feature_snapshot_hash="feature-hash",
    )
    monkeypatch.setattr(cli, "utc_now", lambda: observed)
    monkeypatch.setattr(
        cli,
        "build_learned_moneyline_slate",
        lambda **kwargs: ([candidate], [], 1),
    )
    monkeypatch.setattr(
        cli,
        "match_executable_quote",
        lambda *args, **kwargs: {
            "executable_ask": 0.55,
            "market_slug": "mlb-1",
            "observed_at_utc": "2026-07-26T11:00:00Z",
            "timestamp_valid": False,
        },
    )

    class Registry:
        version = "1"

        @staticmethod
        def resolve(league, team, event_start):
            return AWAY if team == "Boston Red Sox" else HOME

    monkeypatch.setattr(
        cli,
        "evaluate_eligibility",
        lambda request, registry, bans, exposure, policy, **kwargs: EligibilityResult(
            RecordType.QUALIFIED_SHADOW_CALL,
            "CALL",
            "QUALIFIED",
            1.0,
            60,
            0.07,
            0.02,
            AWAY,
            HOME,
        ),
    )
    ledger = _CaptureLedger()
    config = {
        "models": {
            "MLB": {
                "production_artifact": str(tmp_path / "artifact.json"),
                "status": "shadow_qualified",
                "min_edge": 0.05,
            }
        },
        "project": {
            "maximum_data_age_hours": 12,
            "maximum_unreviewed_market_disagreement": 0.10,
            "ledger_path": str(tmp_path / "picks.xlsx"),
        },
        "bankroll": {},
    }

    result = cli._forecast_learned_sport(
        "mlb",
        "2026-07-26",
        True,
        config,
        Registry(),
        object(),
        ledger,
    )

    assert result["logged"] == 1
    request, eligibility = ledger.appended[0]
    assert request.sportsbook == "model_opinion_no_executable_quote"
    assert "executable_quote_timestamp_invalid" in request.unavailable_features
    assert eligibility.record_type is RecordType.RESEARCH_OBSERVATION
    assert eligibility.reason_code == "NO_CALL_MARKET_UNAVAILABLE"
    assert eligibility.units == 0


def test_below_min_edge_vs_market_still_gets_logged_not_skipped(monkeypatch, tmp_path) -> None:
    """Operator directive (2026-07-30): a candidate whose model probability
    is below the executable ask by more than the configured min_edge used
    to be silently skipped (`continue`) before ever reaching the ledger.
    It must now still be logged -- edge_scaled_units sizing is driven by
    the model's own confidence distance from 50/50, not this vs-market
    number, so nothing about the position size changes; only the row's
    visibility does. The gap is still recorded in edge_blocked as an
    informational note, not an exclusion."""
    observed = datetime(2026, 7, 26, 12, tzinfo=UTC)
    candidate = LearnedForwardCandidate(
        event_id="mlb-2",
        event_start_utc="2026-07-27T00:00:00Z",
        away_team="Boston Red Sox",
        home_team="New York Yankees",
        market_type="moneyline",
        selection="home",
        model_probability=0.55,
        home_probability=0.55,
        confidence_threshold=0.50,
        call=True,
        action="QUALIFIED_SHADOW_CALL",
        reason="CALL_LEARNED_CONFIDENCE",
        model_version="mlb-test",
        model_artifact_hash="artifact-hash",
        model_qualified=True,
        feature_basis={"elo_probability": 0.55, "trend_gap": 0.0},
        feature_snapshot_hash="feature-hash-2",
    )
    monkeypatch.setattr(cli, "utc_now", lambda: observed)
    monkeypatch.setattr(
        cli,
        "build_learned_moneyline_slate",
        lambda **kwargs: ([candidate], [], 1),
    )
    monkeypatch.setattr(
        cli,
        "match_executable_quote",
        lambda *args, **kwargs: {
            # model_probability (0.55) - executable_ask (0.65) = -0.10, well
            # below the configured min_edge of 0.05.
            "executable_ask": 0.65,
            "market_slug": "mlb-2",
            "observed_at_utc": "2026-07-26T11:00:00Z",
            "timestamp_valid": True,
        },
    )

    class Registry:
        version = "1"

        @staticmethod
        def resolve(league, team, event_start):
            return AWAY if team == "Boston Red Sox" else HOME

    monkeypatch.setattr(
        cli,
        "evaluate_eligibility",
        lambda request, registry, bans, exposure, policy, **kwargs: EligibilityResult(
            RecordType.QUALIFIED_SHADOW_CALL,
            "CALL",
            "QUALIFIED",
            1.0,
            60,
            0.07,
            0.02,
            AWAY,
            HOME,
        ),
    )
    ledger = _CaptureLedger()
    config = {
        "models": {
            "MLB": {
                "production_artifact": str(tmp_path / "artifact.json"),
                "status": "shadow_qualified",
                "min_edge": 0.05,
            }
        },
        "project": {
            "maximum_data_age_hours": 12,
            "maximum_unreviewed_market_disagreement": 0.10,
            "ledger_path": str(tmp_path / "picks.xlsx"),
        },
        "bankroll": {},
    }

    result = cli._forecast_learned_sport(
        "mlb", "2026-07-26", True, config, Registry(), object(), ledger,
    )

    assert result["logged"] == 1  # not skipped, despite failing min_edge
    assert len(result["edge_blocked"]) == 1  # still noted, just not exclusionary
    assert "logged anyway" in result["edge_blocked"][0]["reason"]
    request, _eligibility = ledger.appended[0]
    assert request.event_id == "mlb-2"


def test_below_learned_confidence_threshold_downgraded_and_kept_off_main(monkeypatch, tmp_path) -> None:
    """Operator directive reversing F-34/F-35: unlike the min-edge-vs-market
    number above (still just a visible note, never exclusionary), the
    model's own learned confidence_threshold IS restored as a real gate. A
    candidate whose model_probability falls short of it must be downgraded
    from CALL to a NO_CALL research observation, and -- same as any other
    hard NO_CALL reason for a production sport in non-flat mode -- kept off
    Main entirely rather than muddying it."""
    observed = datetime(2026, 7, 26, 12, tzinfo=UTC)
    candidate = LearnedForwardCandidate(
        event_id="mlb-conf",
        event_start_utc="2026-07-27T00:00:00Z",
        away_team="Boston Red Sox",
        home_team="New York Yankees",
        market_type="moneyline",
        selection="home",
        model_probability=0.55,
        home_probability=0.55,
        confidence_threshold=0.65,  # candidate falls short of this
        call=True,
        action="QUALIFIED_SHADOW_CALL",
        reason="CALL_BELOW_LEARNED_CONFIDENCE_OPERATOR_REVIEW",
        model_version="mlb-test",
        model_artifact_hash="artifact-hash",
        model_qualified=True,
        feature_basis={"elo_probability": 0.55, "trend_gap": 0.0},
        feature_snapshot_hash="feature-hash-conf",
    )
    monkeypatch.setattr(cli, "utc_now", lambda: observed)
    monkeypatch.setattr(
        cli, "build_learned_moneyline_slate", lambda **kwargs: ([candidate], [], 1),
    )
    monkeypatch.setattr(
        cli,
        "match_executable_quote",
        lambda *args, **kwargs: {
            "executable_ask": 0.56,
            "market_slug": "mlb-conf",
            "observed_at_utc": "2026-07-26T11:00:00Z",
            "timestamp_valid": True,
        },
    )

    class Registry:
        version = "1"

        @staticmethod
        def resolve(league, team, event_start):
            return AWAY if team == "Boston Red Sox" else HOME

    monkeypatch.setattr(
        cli,
        "evaluate_eligibility",
        lambda request, registry, bans, exposure, policy, **kwargs: EligibilityResult(
            RecordType.QUALIFIED_SHADOW_CALL,
            "CALL",
            "QUALIFIED",
            1.0,
            60,
            0.07,
            0.02,
            AWAY,
            HOME,
        ),
    )
    ledger = _CaptureLedger()
    config = {
        "models": {
            "MLB": {
                "production_artifact": str(tmp_path / "artifact.json"),
                "status": "shadow_qualified",
                "min_edge": 0.05,
            }
        },
        "project": {
            "maximum_data_age_hours": 12,
            "maximum_unreviewed_market_disagreement": 0.10,
            "ledger_path": str(tmp_path / "picks.xlsx"),
        },
        "bankroll": {},
    }

    result = cli._forecast_learned_sport(
        "mlb", "2026-07-26", True, config, Registry(), object(), ledger,
    )

    # Kept off Main entirely (mirrors any other hard NO_CALL reason for a
    # production sport in non-flat mode).
    assert result["logged"] == 0
    assert ledger.appended == []


def test_market_residual_probability_recorded_when_artifact_configured(monkeypatch, tmp_path) -> None:
    """P0-4: config.models.market_residual.artifact used to point at a file
    that didn't exist and was never read by any code. Now a real artifact
    (trained via `train-residual`, or the class's own identity fallback when
    the rolling settled window is too small) gets loaded once per forecast
    call and its calibrated_probability recorded as a diagnostic-only field
    on the row -- it must never feed model_probability or sizing itself."""
    from model_prediction.models.market_residual import MarketResidualModel

    observed = datetime(2026, 7, 26, 12, tzinfo=UTC)
    residual_path = tmp_path / "market-residual-v1.json"
    MarketResidualModel(coefficients=(0.1, 0.9, 0.2), sample_size=150).save(residual_path)
    expected = MarketResidualModel.load(residual_path).calibrated_probability(0.62, 0.55)

    candidate = LearnedForwardCandidate(
        event_id="mlb-3",
        event_start_utc="2026-07-27T00:00:00Z",
        away_team="Boston Red Sox",
        home_team="New York Yankees",
        market_type="moneyline",
        selection="home",
        model_probability=0.62,
        home_probability=0.62,
        confidence_threshold=0.55,
        call=True,
        action="QUALIFIED_SHADOW_CALL",
        reason="CALL_LEARNED_CONFIDENCE",
        model_version="mlb-test",
        model_artifact_hash="artifact-hash",
        model_qualified=True,
        feature_basis={"elo_probability": 0.60, "trend_gap": 0.1},
        feature_snapshot_hash="feature-hash-3",
    )
    monkeypatch.setattr(cli, "utc_now", lambda: observed)
    monkeypatch.setattr(cli, "build_learned_moneyline_slate", lambda **kwargs: ([candidate], [], 1))
    monkeypatch.setattr(
        cli,
        "match_executable_quote",
        lambda *args, **kwargs: {
            "executable_ask": 0.55,
            "market_slug": "mlb-3",
            "observed_at_utc": "2026-07-26T11:00:00Z",
            "timestamp_valid": True,
            "no_vig_probability": 0.55,
        },
    )

    class Registry:
        version = "1"

        @staticmethod
        def resolve(league, team, event_start):
            return AWAY if team == "Boston Red Sox" else HOME

    monkeypatch.setattr(
        cli,
        "evaluate_eligibility",
        lambda request, registry, bans, exposure, policy, **kwargs: EligibilityResult(
            RecordType.QUALIFIED_SHADOW_CALL, "CALL", "QUALIFIED", 1.0, 60, 0.07, 0.02, AWAY, HOME,
        ),
    )
    ledger = _CaptureLedger()
    config = {
        "models": {
            "MLB": {
                "production_artifact": str(tmp_path / "artifact.json"),
                "status": "shadow_qualified",
                "min_edge": 0.02,
            },
            "market_residual": {"artifact": str(residual_path)},
        },
        "project": {
            "maximum_data_age_hours": 12,
            "maximum_unreviewed_market_disagreement": 0.10,
            "ledger_path": str(tmp_path / "picks.xlsx"),
        },
        "bankroll": {},
    }

    result = cli._forecast_learned_sport(
        "mlb", "2026-07-26", True, config, Registry(), object(), ledger,
    )

    assert result["logged"] == 1
    request, _eligibility = ledger.appended[0]
    assert request.market_residual_probability == pytest.approx(expected)
    assert request.model_probability == 0.62  # never overwritten by the residual layer


def test_market_residual_probability_none_without_configured_artifact(monkeypatch, tmp_path) -> None:
    """No market_residual config block (or a missing/corrupt artifact) must
    fail soft to None rather than raising into the primary forecast path."""
    observed = datetime(2026, 7, 26, 12, tzinfo=UTC)
    candidate = LearnedForwardCandidate(
        event_id="mlb-4",
        event_start_utc="2026-07-27T00:00:00Z",
        away_team="Boston Red Sox",
        home_team="New York Yankees",
        market_type="moneyline",
        selection="home",
        model_probability=0.60,
        home_probability=0.60,
        confidence_threshold=0.55,
        call=True,
        action="QUALIFIED_SHADOW_CALL",
        reason="CALL_LEARNED_CONFIDENCE",
        model_version="mlb-test",
        model_artifact_hash="artifact-hash",
        model_qualified=True,
        feature_basis={"elo_probability": 0.60, "trend_gap": 0.1},
        feature_snapshot_hash="feature-hash-4",
    )
    monkeypatch.setattr(cli, "utc_now", lambda: observed)
    monkeypatch.setattr(cli, "build_learned_moneyline_slate", lambda **kwargs: ([candidate], [], 1))
    monkeypatch.setattr(
        cli,
        "match_executable_quote",
        lambda *args, **kwargs: {
            "executable_ask": 0.55,
            "market_slug": "mlb-4",
            "observed_at_utc": "2026-07-26T11:00:00Z",
            "timestamp_valid": True,
            "no_vig_probability": 0.55,
        },
    )

    class Registry:
        version = "1"

        @staticmethod
        def resolve(league, team, event_start):
            return AWAY if team == "Boston Red Sox" else HOME

    monkeypatch.setattr(
        cli,
        "evaluate_eligibility",
        lambda request, registry, bans, exposure, policy, **kwargs: EligibilityResult(
            RecordType.QUALIFIED_SHADOW_CALL, "CALL", "QUALIFIED", 1.0, 60, 0.07, 0.02, AWAY, HOME,
        ),
    )
    ledger = _CaptureLedger()
    config = {
        "models": {
            "MLB": {
                "production_artifact": str(tmp_path / "artifact.json"),
                "status": "shadow_qualified",
                "min_edge": 0.02,
            },
            "market_residual": {"artifact": str(tmp_path / "does-not-exist.json")},
        },
        "project": {
            "maximum_data_age_hours": 12,
            "maximum_unreviewed_market_disagreement": 0.10,
            "ledger_path": str(tmp_path / "picks.xlsx"),
        },
        "bankroll": {},
    }

    result = cli._forecast_learned_sport(
        "mlb", "2026-07-26", True, config, Registry(), object(), ledger,
    )

    assert result["logged"] == 1
    request, _eligibility = ledger.appended[0]
    assert request.market_residual_probability is None


def test_international_forecast_preview_never_requires_or_writes_a_ledger(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cli, "utc_now", lambda: datetime(2026, 7, 26, 12, tzinfo=UTC))
    monkeypatch.setattr(
        "model_prediction.international_baseball.forecast_international_baseball_slate",
        lambda *args, **kwargs: _international_forecast(),
    )

    result = cli._forecast_international_sport(
        tmp_path,
        tmp_path,
        "kbo",
        "2026-07-27",
        _international_config(min_edge=0.10),
        None,
    )

    assert result["logged"] == 0
    assert result["priced_contracts"]
    assert "Preview only" in result["logging_note"]


def test_international_forecast_observed_now_is_captured_after_slate_building_not_before(
    monkeypatch, tmp_path
) -> None:
    """Regression: forecast_international_baseball_slate stamps each
    contract's own observed_at_utc using ITS OWN internal utc_now() call. If
    the caller captured its own observed_now BEFORE calling the (real, slow)
    slate builder, that captured value was always earlier than the
    contract's own stamped time once any real work happened in between --
    request.validate(now=...) then always saw an observation "in the
    future" and rejected every single contract. Real-world impact before
    this fix: KBO/NPB logged zero picks, every single day, despite real
    scheduled games (5-6 events/day), because the two timestamps were
    captured on opposite sides of the slow forecast-building call.
    """
    clock = {"now": datetime(2026, 7, 26, 12, 0, 0, tzinfo=UTC)}

    def fake_utc_now():
        return clock["now"]

    def fake_slate(*args, **kwargs):
        # Simulate real wall-clock time passing while building the forecast
        # (real network calls, in production) -- by the time this returns,
        # "now" has moved past whatever the caller captured before calling it.
        clock["now"] = clock["now"] + timedelta(seconds=5)
        forecast = _international_forecast()
        stamp = clock["now"].isoformat().replace("+00:00", "Z")
        start = (clock["now"] + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        for contract in forecast["priced_contracts"]:
            contract["observed_at_utc"] = stamp
            contract["event_start_utc"] = start
        return forecast

    monkeypatch.setattr(cli, "utc_now", fake_utc_now)
    monkeypatch.setattr(
        "model_prediction.international_baseball.forecast_international_baseball_slate",
        fake_slate,
    )
    research = _CaptureLedger()
    gated = _CaptureLedger()

    result = cli._forecast_international_sport(
        tmp_path,
        tmp_path,
        "kbo",
        "2026-07-27",
        _international_config(min_edge=0.10),
        research,
        gated,
    )

    assert result["logged"] == 1
    assert len(research.appended) == 1


def test_international_forecast_logs_low_edge_to_research_but_not_gated(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(cli, "utc_now", lambda: datetime(2026, 7, 26, 12, tzinfo=UTC))
    monkeypatch.setattr(
        "model_prediction.international_baseball.forecast_international_baseball_slate",
        lambda *args, **kwargs: _international_forecast(),
    )
    research = _CaptureLedger()
    gated = _CaptureLedger()

    result = cli._forecast_international_sport(
        tmp_path,
        tmp_path,
        "kbo",
        "2026-07-27",
        _international_config(min_edge=0.10),
        research,
        gated,
    )

    assert result["logged"] == 1
    assert len(research.appended) == 1
    assert research.appended[0][1].record_type is RecordType.RESEARCH_OBSERVATION
    assert research.appended[0][1].reason_code == "NO_CALL_LOW_EDGE"
    assert gated.appended == []


def test_international_forecast_logging_failure_is_recorded_not_silently_discarded(
    monkeypatch, tmp_path
) -> None:
    """A per-contract ValueError (malformed event_start_utc) must be
    recorded in the forecast's errors list, not silently swallowed by the
    bare `except (ValueError, KeyError): continue` this used to be -- and
    the other, valid contract in the same batch must still get logged."""
    monkeypatch.setattr(cli, "utc_now", lambda: datetime(2026, 7, 26, 12, tzinfo=UTC))
    forecast = _international_forecast()
    forecast["priced_contracts"].append(
        {
            # Already started relative to the mocked utc_now() above ->
            # request.validate() raises ValueError.
            "event_start_utc": "2020-01-01T00:00:00Z",
            "event_id": "kbo-bad",
            "market_slug": "kbo-bad",
            "teams": ["Lotte", "Kia"],
            "observed_at_utc": "2026-07-26T10:00:00Z",
            "artifact_hash": "artifact-hash",
            "sides": [
                {
                    "team": "Lotte",
                    "model_fair_settlement_value": 0.40,
                    "executable_ask": 0.45,
                    "tie_probability": 0.04,
                },
                {
                    "team": "Kia",
                    "model_fair_settlement_value": 0.60,
                    "executable_ask": 0.55,
                    "tie_probability": 0.04,
                },
            ],
        }
    )
    monkeypatch.setattr(
        "model_prediction.international_baseball.forecast_international_baseball_slate",
        lambda *args, **kwargs: forecast,
    )
    research = _CaptureLedger()
    gated = _CaptureLedger()

    result = cli._forecast_international_sport(
        tmp_path,
        tmp_path,
        "kbo",
        "2026-07-27",
        _international_config(min_edge=0.10),
        research,
        gated,
    )

    assert result["logged"] == 1
    assert len(research.appended) == 1
    assert research.appended[0][0].event_id == "kbo-game-1"
    assert result["errors"] == [
        {
            "event_id": "kbo-bad",
            "reason": "ValueError: cannot create a call after the event has started",
        }
    ]


def test_international_forecast_mirrors_only_strategy_qualified_calls(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(cli, "utc_now", lambda: datetime(2026, 7, 26, 12, tzinfo=UTC))
    monkeypatch.setattr(
        "model_prediction.international_baseball.forecast_international_baseball_slate",
        lambda *args, **kwargs: _international_forecast(),
    )
    research = _CaptureLedger()
    gated = _CaptureLedger()

    result = cli._forecast_international_sport(
        tmp_path,
        tmp_path,
        "kbo",
        "2026-07-27",
        _international_config(min_edge=0.02),
        research,
        gated,
    )

    assert result["logged"] == 1
    assert research.appended[0][1].decision == "CALL"
    assert gated.appended[0][1].decision == "CALL"


def test_international_forecast_gated_blocked_when_team_lacks_real_history(
    monkeypatch, tmp_path
) -> None:
    """A contract whose min_team_games is below MINIMUM_TEAM_GAMES must not
    reach Gated Research even with a comfortably clearing edge -- same
    reasoning as soccer/tennis's equivalent test."""
    monkeypatch.setattr(cli, "utc_now", lambda: datetime(2026, 7, 26, 12, tzinfo=UTC))
    forecast = _international_forecast()
    forecast["priced_contracts"][0]["min_team_games"] = 3
    monkeypatch.setattr(
        "model_prediction.international_baseball.forecast_international_baseball_slate",
        lambda *args, **kwargs: forecast,
    )
    research = _CaptureLedger()
    gated = _CaptureLedger()

    result = cli._forecast_international_sport(
        tmp_path,
        tmp_path,
        "kbo",
        "2026-07-27",
        _international_config(min_edge=0.02),
        research,
        gated,
    )

    assert result["logged"] == 1
    assert research.appended[0][1].decision == "NO_CALL"
    assert gated.appended == []


def _log_pick(ledger: PickLedger, *, event_start_utc: str, created_at, units: float = 1.0) -> dict:
    request = PickRequest(
        event_start_utc=event_start_utc,
        event_id="event-1",
        league=League.MLB,
        away_team="Boston Red Sox",
        home_team="New York Yankees",
        market_type=MarketType.MONEYLINE,
        selection="home",
        line=None,
        sportsbook="Book",
        american_odds=-110,
        model_probability=0.6,
        model_uncertainty=0.05,
        model_version="v1",
        rationale="test",
        risks="",
        model_origin=ModelOrigin.STATISTICAL_MODEL,
        model_state=ModelState.SHADOW_QUALIFIED,
    )
    eligibility = EligibilityResult(
        RecordType.QUALIFIED_SHADOW_CALL, "CALL", "QUALIFIED", units, 60, 0.05, 0.05, AWAY, HOME
    )
    return ledger.append_evaluated(request, eligibility, now=created_at)


# ----------------------------------------------------------------- _verify_chain


def test_verify_chain_reports_intact_for_a_freshly_logged_pick(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    _log_pick(ledger, event_start_utc="2026-07-14T00:00:00Z", created_at=datetime(2026, 7, 13, tzinfo=UTC))
    result = _verify_chain(ledger.audit.path, ledger)
    assert result["chain_intact"] is True
    assert result["break_count"] == 0
    assert result["reconciled"] is True
    assert result["rows_missing_creation_event"] == []


def test_verify_chain_detects_a_tampered_event(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    _log_pick(ledger, event_start_utc="2026-07-14T00:00:00Z", created_at=datetime(2026, 7, 13, tzinfo=UTC))
    import json

    lines = ledger.audit.path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[0])
    tampered["payload"]["units"] = "999"
    ledger.audit.path.write_text(json.dumps(tampered) + "\n", encoding="utf-8")

    result = _verify_chain(ledger.audit.path, ledger)
    assert result["chain_intact"] is False
    assert result["break_count"] == 1
    assert result["breaks"][0]["kind"] == "hash_mismatch"


def test_verify_chain_flags_a_ledger_row_with_no_creation_event(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    row = _log_pick(ledger, event_start_utc="2026-07-14T00:00:00Z", created_at=datetime(2026, 7, 13, tzinfo=UTC))
    # Simulate a row that entered the ledger through a path that bypassed
    # the audited API entirely (the exact symptom that identified the
    # retroactively-rescored batch found and removed earlier this session).
    ledger.audit.path.write_text("", encoding="utf-8")

    result = _verify_chain(ledger.audit.path, ledger)
    assert result["rows_missing_creation_event"] == [row["pick_id"]]
    assert result["reconciled"] is False


def test_verify_chain_on_a_missing_audit_file_reports_zero_lines(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    result = _verify_chain(tmp_path / "does_not_exist.jsonl", ledger)
    assert result["audit_lines"] == 0
    assert result["chain_intact"] is True


# ------------------------------------------------------------- _clear_today_open


def test_clear_today_open_removes_a_not_yet_started_pick_by_creation_date(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    row = _log_pick(
        ledger,
        event_start_utc="2099-01-01T00:00:00Z",  # far future -- never "started"
        created_at=datetime(2026, 7, 25, 10, tzinfo=UTC),
    )
    removed = _clear_today_open(ledger, "2026-07-25")
    assert removed == [row["pick_id"]]
    assert ledger.rows() == []


def test_clear_today_open_by_event_date_matches_on_event_start_not_creation(tmp_path) -> None:
    """The fix from this session: a pick logged the day BEFORE its game
    (created 7/24 for a 7/25 game) must still be cleared by a 7/25 run when
    by_event_date=True, not silently frozen forever. Uses a far-future date
    so the event is guaranteed to still be "not yet started" regardless of
    when this test actually runs."""
    ledger = _ledger(tmp_path)
    row = _log_pick(
        ledger,
        event_start_utc="2030-07-25T00:00:00Z",
        created_at=datetime(2030, 7, 24, 20, tzinfo=UTC),
    )
    # created_at-only matching (main ledger's old behavior) would miss this.
    assert _clear_today_open(ledger, "2030-07-25", by_event_date=False) == []
    assert len(ledger.rows()) == 1
    # by_event_date=True correctly catches it.
    removed = _clear_today_open(ledger, "2030-07-25", by_event_date=True)
    assert removed == [row["pick_id"]]


def test_clear_today_open_never_removes_an_already_started_game(tmp_path) -> None:
    """Uses safely past-dated timestamps (both well before any real
    wall-clock run of this test) so the event is unambiguously "started"."""
    ledger = _ledger(tmp_path)
    _log_pick(
        ledger,
        event_start_utc="2020-01-02T00:00:00Z",  # after created_at, still long past
        created_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    removed = _clear_today_open(ledger, "2020-01-01")
    assert removed == []
    assert len(ledger.rows()) == 1


def test_clear_today_open_ignores_a_different_date(tmp_path) -> None:
    ledger = _ledger(tmp_path)
    _log_pick(
        ledger,
        event_start_utc="2099-01-01T00:00:00Z",
        created_at=datetime(2026, 7, 20, 10, tzinfo=UTC),
    )
    removed = _clear_today_open(ledger, "2026-07-25")
    assert removed == []
    assert len(ledger.rows()) == 1
