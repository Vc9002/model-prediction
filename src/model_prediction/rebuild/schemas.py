"""Formal table/schema contracts (FOUNDATION_COMPLETION.md Phase 5).

Every normalized, market, and feature table gets a versioned schema with an
explicit primary key. Validation happens before persistence — a row that
violates its table's contract must fail loudly, not get silently written
next to rows that don't.

This does not replace NormalizedStore's own primary-key dedupe (storage.py)
-- that's about idempotency on rerun. This is about the row shape itself:
required columns present, primary key never null, declared types held.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import polars as pl
from pydantic import BaseModel, Field, ValidationError, field_validator

SCHEMA_VERSION = "1"


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    dtype: type  # python-level type: str, float, int, bool
    nullable: bool = True


@dataclass(frozen=True)
class TableContract:
    """A versioned schema and primary key for one real table."""

    name: str
    primary_key: list[str]
    columns: list[ColumnSpec]
    conflict_policy: str = "keep_latest"  # matches NormalizedStore.write()'s conflict_policy values
    schema_version: str = field(default=SCHEMA_VERSION)


_PL_TYPE_GROUPS: dict[type, tuple[type, ...]] = {
    str: (pl.Utf8, pl.String),
    float: (pl.Float32, pl.Float64, pl.Int8, pl.Int16, pl.Int32, pl.Int64),
    int: (pl.Int8, pl.Int16, pl.Int32, pl.Int64, pl.UInt8, pl.UInt16, pl.UInt32, pl.UInt64),
    bool: (pl.Boolean,),
}


def validate_against_contract(df: pl.DataFrame, contract: TableContract) -> list[str]:
    """Real, code-checked violations for `df` against `contract`. Empty list
    means the table is valid to persist. Never raises itself -- callers
    decide whether to fail closed (validate_or_raise) or just report."""
    errors: list[str] = []
    # A primary key column is required to be non-null UNLESS its own
    # ColumnSpec explicitly says nullable=True -- real composite keys can
    # legitimately include a nullable column (e.g. market_snapshot's
    # `line`, which is null for moneyline rows and a real signed line for
    # spread/total; matches the COALESCE(..., sentinel) pattern already
    # used for the same real reason in shadow_ledger.py's trade_decisions
    # unique index). Not declared in `columns` at all defaults to
    # required, since that's the stricter/safer assumption.
    column_specs = {c.name: c for c in contract.columns}

    for pk in contract.primary_key:
        if pk not in df.columns:
            errors.append(f"missing primary key column: {pk}")
            continue
        spec = column_specs.get(pk)
        pk_nullable = spec.nullable if spec is not None else False
        if not pk_nullable and df.height > 0 and df[pk].null_count() > 0:
            errors.append(f"primary key column '{pk}' has {df[pk].null_count()} null value(s)")

    for col in contract.columns:
        if col.name not in df.columns:
            if not col.nullable:
                errors.append(f"missing required column: {col.name}")
            continue
        if not col.nullable and df.height > 0:
            null_count = df[col.name].null_count()
            if null_count > 0:
                errors.append(f"'{col.name}': {null_count} null value(s) in non-nullable column")
        if df.height > 0:
            actual_dtype = df.schema[col.name]
            # A column that's entirely null in this batch infers as
            # polars' own Null dtype, not the eventual real dtype -- a
            # nullable column with no non-null values yet (e.g. a real
            # collector's optional canonical-id lookup that didn't
            # resolve for any row in a small batch) isn't a real type
            # violation. A non-nullable column that's still all-null was
            # already caught by the null-count check above.
            if actual_dtype == pl.Null and col.nullable:
                continue
            allowed = _PL_TYPE_GROUPS.get(col.dtype)
            if allowed is not None and actual_dtype not in allowed:
                errors.append(
                    f"'{col.name}': expected {col.dtype.__name__}-compatible dtype, got {actual_dtype}"
                )

    return errors


def validate_or_raise(df: pl.DataFrame, contract: TableContract) -> None:
    """Fail closed: raise ValueError if `df` violates `contract`. Schema
    drift must produce a visible failure, not a silently-written bad row."""
    errors = validate_against_contract(df, contract)
    if errors:
        raise ValueError(
            f"Schema contract violation for table '{contract.name}' "
            f"(schema_version={contract.schema_version}): {'; '.join(errors)}"
        )


# ── Real contracts for tables actually written by real collectors ──────────

SCOREBOARD_CONTRACT = TableContract(
    name="scoreboard",
    primary_key=["event_id"],
    conflict_policy="keep_latest",
    columns=[
        ColumnSpec("event_id", str, nullable=False),
        ColumnSpec("home_team", str, nullable=False),
        ColumnSpec("away_team", str, nullable=False),
        ColumnSpec("home_score", float, nullable=False),
        ColumnSpec("away_score", float, nullable=False),
        ColumnSpec("status", str, nullable=False),
        ColumnSpec("venue", str, nullable=True),
        ColumnSpec("home_team_canonical_id", str, nullable=True),
        ColumnSpec("away_team_canonical_id", str, nullable=True),
        ColumnSpec("event_canonical_id", str, nullable=True),
        ColumnSpec("venue_canonical_id", str, nullable=True),
        ColumnSpec("observed_at_utc", str, nullable=False),
        ColumnSpec("event_start_utc", str, nullable=False),
        ColumnSpec("source", str, nullable=False),
    ],
)

ROSTER_CONTRACT = TableContract(
    name="roster",
    primary_key=["team_id", "player_id", "observed_at_utc"],
    conflict_policy="keep_latest",
    columns=[
        ColumnSpec("team_id", str, nullable=False),  # source-native (ESPN) team id
        ColumnSpec("player_id", str, nullable=False),  # source-native (ESPN) athlete id
        ColumnSpec("player_name", str, nullable=False),
        ColumnSpec("player_canonical_id", str, nullable=True),
        ColumnSpec("team_canonical_id", str, nullable=True),
        ColumnSpec("position", str, nullable=True),
        ColumnSpec("jersey", str, nullable=True),
        ColumnSpec("season", int, nullable=False),
        ColumnSpec("observed_at_utc", str, nullable=False),
        ColumnSpec("source", str, nullable=False),
    ],
)

MARKET_SNAPSHOT_CONTRACT = TableContract(
    name="market_snapshot",
    primary_key=["market_id", "team_or_side", "line", "observed_at_utc"],
    conflict_policy="keep_latest",
    columns=[
        ColumnSpec("event_id", str, nullable=False),
        ColumnSpec("market_id", str, nullable=False),
        ColumnSpec("market_type", str, nullable=False),
        ColumnSpec("team_or_side", str, nullable=False),
        ColumnSpec("line", float, nullable=True),  # null for moneyline; real for spread/total
        ColumnSpec("executable_price", float, nullable=True),
        ColumnSpec("observed_at_utc", str, nullable=False),
        ColumnSpec("source", str, nullable=False),
        # Resolved via identity.resolve_polymarket_team_id() -- null for
        # total-market rows (no team) and for a real market-side team name
        # the identity registry can't confidently match to an already-
        # registered canonical team (fails closed, not fabricated).
        ColumnSpec("team_canonical_id", str, nullable=True),
    ],
)


# ── Declarative Pydantic Ingestion Schemas ─────────────────────────────────


class IngestionScoreboardRow(BaseModel):
    event_id: str = Field(min_length=1)
    home_team: str = Field(min_length=1)
    away_team: str = Field(min_length=1)
    home_score: float = Field(ge=0.0)
    away_score: float = Field(ge=0.0)
    status: str = Field(min_length=1)
    observed_at_utc: str = Field(min_length=1)
    event_start_utc: str = Field(min_length=1)
    source: str = Field(min_length=1)
    venue: str | None = None
    home_team_canonical_id: str | None = None
    away_team_canonical_id: str | None = None
    event_canonical_id: str | None = None
    venue_canonical_id: str | None = None


class IngestionMarketSnapshotRow(BaseModel):
    event_id: str = Field(min_length=1)
    market_id: str = Field(min_length=1)
    market_type: str
    team_or_side: str
    line: float | None = None
    executable_price: float | None = Field(default=None, ge=0.0, le=1.0)
    observed_at_utc: str = Field(min_length=1)
    source: str = Field(min_length=1)
    team_canonical_id: str | None = None

    @field_validator("market_type")
    @classmethod
    def validate_market_type(cls, v: str) -> str:
        valid = {"moneyline", "spread", "total", "double_chance", "btts", "runline", "puckline"}
        if v.lower() not in valid:
            raise ValueError(f"unknown market type: {v}")
        return v.lower()


def validate_ingestion_payload(
    payload: dict[str, Any],
    schema_cls: type[BaseModel],
) -> tuple[bool, str | None]:
    """Validate raw ingestion payload with declarative Pydantic schema."""
    try:
        schema_cls.model_validate(payload)
        return True, None
    except ValidationError as exc:
        return False, str(exc)
