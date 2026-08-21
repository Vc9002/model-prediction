"""PIT-safe WNBA team form; the same function serves research and live rows.

Recovered from the archived `origin/rebuild/wnba-v1` branch
(`archive/model-source-rebuild-wnba-v1-95c7dcc2`) per
`docs/model_audit/models/WNBA_ARCHIVED_BASELINES.md`'s RECOVER verdict --
the file was excluded from the original WNBA data-foundation merge as a
scope decision (feature-engineering is a different concern from
backfill/normalize/store), not a quality rejection. Its only non-stdlib
dependency (`.pit.eligible_prior_team_games`, `providers.base.canonical_json`)
is byte-identical to what this file was originally written against, so it
is ported unmodified rather than rewritten against a drifted schema.

Computes rolling Four-Factors-family team form (`ortg`/`drtg`/`netrtg`/
`pace`/`efg`/`tov_pct`/`orb_pct`/`ft_rate`) over 5/10/20-game and season
windows, strictly from games/team_box rows eligible as of a given
`decision_time_utc` -- see `.pit.eligible_prior_team_games` for the PIT
gate itself. This is plain rolling means, not yet opponent-adjusted or
reliability-shrunk (`docs/MODEL_IMPROVEMENTS.md` section 7's rank-3
roadmap item) -- that remains real future work, not a gap in this port.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

import polars as pl

from model_prediction.rebuild.providers.base import canonical_json

from .pit import eligible_prior_team_games

WINDOWS = (5, 10, 20)
METRICS = ("ortg", "drtg", "netrtg", "pace", "efg", "tov_pct", "orb_pct", "ft_rate")


def _number(value: Any) -> float | None:
    return float(value) if value is not None else None


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return float(numerator) / float(denominator)


def _latest_eligible_boxes(team_box: pl.DataFrame, decision_time_utc: datetime) -> pl.DataFrame:
    return (
        team_box.with_columns(
            pl.col("observed_at_utc").str.to_datetime(time_zone="UTC", strict=True).alias("_observed")
        )
        .filter(pl.col("_observed") <= decision_time_utc)
        .sort("_observed")
        .group_by(["event_id", "team_id"], maintain_order=True)
        .last()
        .filter(pl.col("pit_eligible"))
        .drop("_observed")
    )


def _game_metrics(row: dict[str, Any], opponent: dict[str, Any]) -> dict[str, float | None]:
    fga = _number(row.get("field_goals_attempted"))
    fgm = _number(row.get("field_goals_made"))
    three_made = _number(row.get("three_points_made"))
    fta = _number(row.get("free_throws_attempted"))
    oreb = _number(row.get("offensive_rebounds"))
    turnovers = _number(row.get("turnovers"))
    opp_fga = _number(opponent.get("field_goals_attempted"))
    opp_fta = _number(opponent.get("free_throws_attempted"))
    opp_oreb = _number(opponent.get("offensive_rebounds"))
    opp_turnovers = _number(opponent.get("turnovers"))
    possessions = (
        fga + 0.44 * fta - oreb + turnovers
        if fga is not None and fta is not None and oreb is not None and turnovers is not None
        else None
    )
    opp_possessions = (
        opp_fga + 0.44 * opp_fta - opp_oreb + opp_turnovers
        if opp_fga is not None and opp_fta is not None and opp_oreb is not None and opp_turnovers is not None
        else None
    )
    points = _number(row.get("points"))
    opponent_points = _number(opponent.get("points"))
    ortg = (100.0 * points / possessions) if possessions and points is not None else None
    drtg = (
        100.0 * opponent_points / opp_possessions if opp_possessions and opponent_points is not None else None
    )
    efg_numerator = fgm + 0.5 * three_made if fgm is not None and three_made is not None else None
    opponent_dreb = _number(opponent.get("defensive_rebounds"))
    rebound_chances = oreb + opponent_dreb if oreb is not None and opponent_dreb is not None else None
    return {
        "ortg": ortg,
        "drtg": drtg,
        "netrtg": ortg - drtg if ortg is not None and drtg is not None else None,
        "pace": (possessions + opp_possessions) / 2.0 if possessions and opp_possessions else None,
        "efg": _ratio(efg_numerator, fga),
        "tov_pct": _ratio(turnovers, possessions),
        "orb_pct": _ratio(oreb, rebound_chances),
        "ft_rate": _ratio(fta, fga),
    }


def build_team_form_snapshot(
    games: pl.DataFrame,
    team_box: pl.DataFrame,
    *,
    team_id: str,
    decision_time_utc: datetime,
) -> dict[str, Any]:
    """Build prior-only rolling form for either a historical or live target."""
    if decision_time_utc.tzinfo is None:
        raise ValueError("decision_time_utc must be timezone-aware")
    knowledge_time = decision_time_utc.astimezone(UTC)
    # A season whose team_box (or games) table was never backfilled reads
    # back as a genuinely empty, zero-column frame (WNBANormalizedStore
    # .read_observations()'s own contract when no parts exist on disk) --
    # not just zero matching rows. eligible_prior_team_games()'s column
    # -presence check would raise on that, which is correct for a caller
    # that expected a populated table but wrong here: "this team has no
    # backfilled history yet" is exactly the same UNAVAILABLE/zero-sample
    # case this function already returns when there are simply no eligible
    # game_rows, so it must degrade the same way rather than crash the
    # whole build for one never-backfilled season.
    if games.is_empty() or team_box.is_empty():
        return {
            "team_id": team_id,
            "as_of_utc": knowledge_time.isoformat(),
            "status": "UNAVAILABLE",
            "sample_size": 0,
            "source_watermark_utc": None,
            "source_raw_snapshot_hashes": [],
            "source_manifest_hash": None,
            **{
                f"{label}_{key}": (0 if key == "sample" else None)
                for label in [*(f"last_{window}" for window in WINDOWS), "season"]
                for key in ("sample", *METRICS)
            },
        }
    eligible = eligible_prior_team_games(
        games,
        team_box,
        team_id=team_id,
        decision_time_utc=knowledge_time,
    )
    all_boxes = _latest_eligible_boxes(team_box, knowledge_time)
    by_event_team = {
        (str(row["event_id"]), str(row["team_id"])): row for row in all_boxes.iter_rows(named=True)
    }
    game_rows: list[dict[str, Any]] = []
    for row in eligible.iter_rows(named=True):
        opponent_id = row.get("opponent_team_id")
        opponent = by_event_team.get((str(row["event_id"]), str(opponent_id)))
        if opponent is None:
            continue
        game_rows.append(
            {
                "event_id": row["event_id"],
                "event_start_utc": row["event_start_utc"],
                "observed_at_utc": max(str(row["observed_at_utc"]), str(opponent["observed_at_utc"])),
                "raw_snapshot_hashes": sorted(
                    {
                        str(row["raw_snapshot_hash"]),
                        str(opponent["raw_snapshot_hash"]),
                        str(row["game_raw_snapshot_hash"]),
                    }
                ),
                **_game_metrics(row, opponent),
            }
        )

    raw_snapshot_hashes = sorted({digest for row in game_rows for digest in row["raw_snapshot_hashes"]})

    result: dict[str, Any] = {
        "team_id": team_id,
        "as_of_utc": knowledge_time.isoformat(),
        "status": "AVAILABLE" if game_rows else "UNAVAILABLE",
        "sample_size": len(game_rows),
        "source_watermark_utc": max((row["observed_at_utc"] for row in game_rows), default=None),
        "source_raw_snapshot_hashes": raw_snapshot_hashes,
        "source_manifest_hash": (
            hashlib.sha256(
                canonical_json(
                    {
                        "event_ids": [str(row["event_id"]) for row in game_rows],
                        "raw_snapshot_hashes": raw_snapshot_hashes,
                    }
                )
            ).hexdigest()
            if game_rows
            else None
        ),
    }
    rolling_groups = [(f"last_{window}", game_rows[-window:]) for window in WINDOWS]
    for label, rows in [*rolling_groups, ("season", game_rows)]:
        result[f"{label}_sample"] = len(rows)
        for metric in METRICS:
            values = [float(row[metric]) for row in rows if row[metric] is not None]
            result[f"{label}_{metric}"] = sum(values) / len(values) if values else None
    return result
