"""Settlement CLV reads a close only from a pre-kickoff, open-book quote of the row's own line."""

from model_prediction.cli.settle import _closing_probability_for_moneyline_pick
from model_prediction.data_sources.polymarket_us import PolymarketSnapshotStore

_SLUG = "tsc-cfb-nhc-cencon-2026-10-24-total-56pt5"


def _write_contract_history(tmp_path, *, line: float = 56.5) -> None:
    # The real 2026-10-24 shape: a 16:00Z game's total contract, quoted at 11:45Z (56c
    # Under) and again in play at 17:03Z (99c Under).
    store = PolymarketSnapshotStore.for_sport_date(tmp_path, "ncaaf", "2026-10-24")
    for observed, under_ask in (("2026-10-24T11:45:30.958270Z", 0.56), ("2026-10-24T17:03:06.590540Z", 0.99)):
        store.append(
            {
                "provider": "polymarket_us",
                "market_slug": _SLUG,
                "event_title": "New Haven vs. Central Connecticut State",
                "event_start_utc": "2026-10-24T16:00:00Z",
                "observed_at_utc": observed,
                "market_state": "MARKET_STATE_OPEN",
                "market_type": "total",
                "line": line,
                "long": {"description": "Over", "ask": 0.5},
                "short": {"description": "Under", "ask": under_ask},
            }
        )


def test_settlement_close_for_a_later_kickoff_row_is_never_the_in_play_99c_quote(tmp_path) -> None:
    _write_contract_history(tmp_path)

    probability, _odds = _closing_probability_for_moneyline_pick(
        tmp_path,
        "ncaaf",
        _SLUG,
        "2026-10-24T19:30Z",
        "Ball State Cardinals",
        "Sacramento State Hornets",
        "under",
        line=56.5,
    )

    assert probability == 0.56


def test_settlement_close_is_blank_when_only_post_kickoff_quotes_exist(tmp_path) -> None:
    store = PolymarketSnapshotStore.for_sport_date(tmp_path, "ncaaf", "2026-10-24")
    store.append(
        {
            "market_slug": _SLUG,
            "event_start_utc": "2026-10-24T16:00:00Z",
            "observed_at_utc": "2026-10-24T17:03:06Z",
            "market_state": "MARKET_STATE_OPEN",
            "market_type": "total",
            "line": 56.5,
            "long": {"description": "Over", "ask": 0.69},
            "short": {"description": "Under", "ask": 0.99},
        }
    )

    assert _closing_probability_for_moneyline_pick(
        tmp_path, "ncaaf", _SLUG, "2026-10-24T19:30Z", "Home", "Away", "under", line=56.5
    ) == (None, None)


def test_settlement_close_of_an_away_moneyline_is_not_read_off_a_team_named_thunder(tmp_path) -> None:
    """2026-10-20 South Alabama at Marshall: "under" in "Thundering Herd" made the moneyline
    look like a total, so the away (South Alabama) pick closed at Marshall's ask."""
    store = PolymarketSnapshotStore.for_sport_date(tmp_path, "ncaaf", "2026-10-20")
    store.append(
        {
            "market_slug": "aec-cfb-sala-marsh-2026-10-20",
            "event_start_utc": "2026-10-20T23:30:00Z",
            "observed_at_utc": "2026-10-20T22:08:06Z",
            "market_state": "MARKET_STATE_OPEN",
            "market_type": "moneyline",
            "long": {"description": "Jaguars", "ask": 0.55},
            "short": {"description": "Thundering Herd", "ask": 0.455},
        }
    )

    probability, _odds = _closing_probability_for_moneyline_pick(
        tmp_path,
        "ncaaf",
        "aec-cfb-sala-marsh-2026-10-20",
        "2026-10-20T23:30Z",
        "Marshall Thundering Herd",
        "South Alabama Jaguars",
        "away",
    )

    assert probability == 0.55


def test_settlement_close_is_blank_when_the_contract_line_is_not_the_rows_line(tmp_path) -> None:
    _write_contract_history(tmp_path, line=57.5)

    assert _closing_probability_for_moneyline_pick(
        tmp_path, "ncaaf", _SLUG, "2026-10-24T16:00Z", "Home", "Away", "under", line=56.5
    ) == (None, None)


def test_settlement_close_of_double_chance_is_blank_not_the_yes_side_of_the_away_win_market(
    tmp_path, caplog
) -> None:
    """2026-10-20 Pereira v Boyaca Chico: a 1X pick priced on the away-win market's No (76c)
    closed at that market's Yes ask (25c), the old default for an unmapped selection."""
    store = PolymarketSnapshotStore.for_sport_date(tmp_path, "soccer", "2026-10-20")
    store.append(
        {
            "market_slug": "atc-lco-per-byc-2026-10-20-byc",
            "event_start_utc": "2026-10-20T21:30:00Z",
            "observed_at_utc": "2026-10-20T20:00:00Z",
            "market_state": "MARKET_STATE_OPEN",
            "market_type": "team_win",
            "long": {"description": "Yes", "ask": 0.25},
            "short": {"description": "No", "ask": 0.76},
        }
    )

    with caplog.at_level("INFO", logger="model_prediction.cli"):
        result = _closing_probability_for_moneyline_pick(
            tmp_path,
            "soccer",
            "atc-lco-per-byc-2026-10-20-byc",
            "2026-10-20T21:30Z",
            "Deportivo Pereira",
            "Boyaca Chico FC",
            "1x",
        )

    assert result == (None, None)
    assert "close_side_unresolved" in caplog.text


def test_settlement_close_blank_reasons_are_logged(tmp_path, caplog) -> None:
    _write_contract_history(tmp_path, line=57.5)

    with caplog.at_level("INFO", logger="model_prediction.cli"):
        _closing_probability_for_moneyline_pick(
            tmp_path, "ncaaf", _SLUG, "2026-10-24T16:00Z", "Home", "Away", "under", line=56.5
        )
        _closing_probability_for_moneyline_pick(
            tmp_path, "ncaaf", _SLUG, "2026-10-24T11:00Z", "Home", "Away", "under", line=56.5
        )

    assert "close_line_mismatch" in caplog.text
    assert "no_pre_kickoff_open_quote" in caplog.text
