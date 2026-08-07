"""Tests for formal table/schema contracts (schemas.py).

FOUNDATION_COMPLETION.md Phase 5: every normalized/market/feature table
gets a versioned schema and explicit primary key; validation happens
before persistence; schema drift must fail visibly.
"""

from __future__ import annotations

import polars as pl
import pytest

from model_prediction.rebuild.schemas import (
    MARKET_SNAPSHOT_CONTRACT,
    SCOREBOARD_CONTRACT,
    ColumnSpec,
    TableContract,
    validate_against_contract,
    validate_or_raise,
)

_CONTRACT = TableContract(
    name="test_table",
    primary_key=["id"],
    columns=[
        ColumnSpec("id", str, nullable=False),
        ColumnSpec("value", float, nullable=False),
        ColumnSpec("note", str, nullable=True),
    ],
)


class TestValidateAgainstContract:
    def test_valid_table_has_no_errors(self):
        df = pl.DataFrame({"id": ["a"], "value": [1.0], "note": ["hi"]})
        assert validate_against_contract(df, _CONTRACT) == []

    def test_missing_primary_key_column_is_an_error(self):
        df = pl.DataFrame({"value": [1.0]})
        errors = validate_against_contract(df, _CONTRACT)
        assert any("missing primary key column: id" in e for e in errors)

    def test_null_primary_key_value_is_an_error(self):
        df = pl.DataFrame({"id": [None], "value": [1.0]}, schema={"id": pl.Utf8, "value": pl.Float64})
        errors = validate_against_contract(df, _CONTRACT)
        assert any("primary key column 'id'" in e and "null" in e for e in errors)

    def test_missing_required_non_nullable_column_is_an_error(self):
        df = pl.DataFrame({"id": ["a"]})
        errors = validate_against_contract(df, _CONTRACT)
        assert any("missing required column: value" in e for e in errors)

    def test_null_in_required_non_nullable_column_is_an_error(self):
        df = pl.DataFrame({"id": ["a"], "value": [None]}, schema={"id": pl.Utf8, "value": pl.Float64})
        errors = validate_against_contract(df, _CONTRACT)
        assert any("'value'" in e and "null" in e for e in errors)

    def test_missing_optional_nullable_column_is_not_an_error(self):
        df = pl.DataFrame({"id": ["a"], "value": [1.0]})  # no "note" column at all
        assert validate_against_contract(df, _CONTRACT) == []

    def test_wrong_dtype_is_an_error(self):
        df = pl.DataFrame({"id": ["a"], "value": ["not-a-number"]})
        errors = validate_against_contract(df, _CONTRACT)
        assert any("'value'" in e and "dtype" in e for e in errors)

    def test_all_null_nullable_column_is_not_a_dtype_violation(self):
        # Real bug found live wiring this into collectors.py: a batch where
        # every row's optional value is None (e.g. canonical-id lookup that
        # didn't resolve for anyone in a small real slate) infers as
        # polars' own Null dtype, not the eventual real dtype -- must not
        # be flagged as a type mismatch.
        df = pl.DataFrame({"id": ["a"], "value": [1.0], "note": [None]})
        assert validate_against_contract(df, _CONTRACT) == []

    def test_all_null_non_nullable_column_is_still_caught_by_null_check(self):
        df = pl.DataFrame({"id": ["a"], "value": [None]})
        errors = validate_against_contract(df, _CONTRACT)
        assert any("'value'" in e and "null" in e for e in errors)

    def test_empty_dataframe_with_right_columns_is_valid(self):
        df = pl.DataFrame(schema={"id": pl.Utf8, "value": pl.Float64, "note": pl.Utf8})
        assert validate_against_contract(df, _CONTRACT) == []

    def test_primary_key_column_explicitly_marked_nullable_may_be_null(self):
        # Real case: MARKET_SNAPSHOT_CONTRACT's composite key includes
        # `line`, which is legitimately null for moneyline rows -- a
        # primary key column must only be required non-null when its own
        # ColumnSpec doesn't say otherwise.
        contract = TableContract(
            name="composite_key_table",
            primary_key=["id", "line"],
            columns=[
                ColumnSpec("id", str, nullable=False),
                ColumnSpec("line", float, nullable=True),
            ],
        )
        df = pl.DataFrame({"id": ["a"], "line": [None]}, schema={"id": pl.Utf8, "line": pl.Float64})
        assert validate_against_contract(df, contract) == []


class TestValidateOrRaise:
    def test_valid_table_does_not_raise(self):
        df = pl.DataFrame({"id": ["a"], "value": [1.0]})
        validate_or_raise(df, _CONTRACT)  # must not raise

    def test_invalid_table_fails_closed_with_a_visible_error(self):
        df = pl.DataFrame({"value": [1.0]})
        with pytest.raises(ValueError, match="Schema contract violation"):
            validate_or_raise(df, _CONTRACT)


class TestRealContracts:
    """The real contracts real collectors actually write against."""

    def test_scoreboard_contract_accepts_a_real_shaped_row(self):
        df = pl.DataFrame({
            "event_id": ["401816384"], "home_team": ["Baltimore Orioles"],
            "away_team": ["Los Angeles Angels"], "home_score": [3.0], "away_score": [1.0],
            "status": ["STATUS_FINAL"], "venue": ["Camden Yards"],
            "home_team_canonical_id": ["mlb:team:abc"], "away_team_canonical_id": ["mlb:team:def"],
            "observed_at_utc": ["2026-08-06T10:00:00"], "event_start_utc": ["2026-08-06T22:35:00"],
            "source": ["espn_public"],
        })
        assert validate_against_contract(df, SCOREBOARD_CONTRACT) == []

    def test_scoreboard_contract_rejects_missing_event_id(self):
        df = pl.DataFrame({
            "home_team": ["Baltimore Orioles"], "away_team": ["Los Angeles Angels"],
            "home_score": [3.0], "away_score": [1.0], "status": ["STATUS_FINAL"],
            "observed_at_utc": ["2026-08-06T10:00:00"], "event_start_utc": ["2026-08-06T22:35:00"],
            "source": ["espn_public"],
        })
        errors = validate_against_contract(df, SCOREBOARD_CONTRACT)
        assert any("event_id" in e for e in errors)

    def test_market_snapshot_contract_accepts_a_real_shaped_row(self):
        df = pl.DataFrame({
            "event_id": ["70543"], "market_id": ["m1"], "market_type": ["moneyline"],
            "team_or_side": ["home"], "line": [None], "executable_price": [0.55],
            "observed_at_utc": ["2026-08-06T10:00:00"], "source": ["polymarket_us"],
        }, schema={"event_id": pl.Utf8, "market_id": pl.Utf8, "market_type": pl.Utf8,
                   "team_or_side": pl.Utf8, "line": pl.Float64, "executable_price": pl.Float64,
                   "observed_at_utc": pl.Utf8, "source": pl.Utf8})
        assert validate_against_contract(df, MARKET_SNAPSHOT_CONTRACT) == []
