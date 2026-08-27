"""Statcast point-in-time game metrics aggregation (pitcher/batter).

Consumes the boxscore snapshots the daily pipeline already captures
(``data/mlb_statsapi/game_snapshots.jsonl``) and rebuilds two parquet
tables: ``pitcher_game_metrics.parquet`` / ``batter_game_metrics.parquet``
under ``data/statcast/``.

The rebuild is a full scan -- deliberately incremental-free: the source
snapshots are append-only point-in-time records, and a full rebuild is the
only way to guarantee the aggregates never depend on run order. Wired into
the daily pipeline 2026-08-26 (was a manual-only script, 3+ days behind);
it runs after the capture pool so the same run's new snapshots are
included. Fail-soft: aggregate freshness is a research input, never a
blocker for the daily run.

Migrated from ``scripts/ingest_statcast_aggregates.py``, which is now a
thin wrapper so the manual invocation keeps working.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl

STATCAST_SUBDIR = "statcast"
PITCHER_METRICS_FILE = "pitcher_game_metrics.parquet"
BATTER_METRICS_FILE = "batter_game_metrics.parquet"


def build_statcast_game_aggregates(
    data_root: str | Path,
    *,
    snapshots_path: str | Path | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Build pitcher and batter PIT aggregates from available boxscores.

    ``data_root`` is the caller's data tree (the scheduler-resolved root in
    the daily pipeline -- never silently defaulted away from it, the same
    split-brain bug class as other capture steps). ``snapshots_path``
    defaults to ``<data_root>/mlb_statsapi/game_snapshots.jsonl`` for the
    manual path; the daily passes ``data_root`` explicitly.
    """
    root = Path(data_root)
    statcast_dir = root / STATCAST_SUBDIR
    statcast_dir.mkdir(parents=True, exist_ok=True)
    snapshots = (
        Path(snapshots_path) if snapshots_path is not None else root / "mlb_statsapi" / "game_snapshots.jsonl"
    )

    pitcher_rows: list[dict[str, Any]] = []
    batter_rows: list[dict[str, Any]] = []

    if snapshots.exists():
        with snapshots.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    snap = json.loads(line)
                except json.JSONDecodeError:
                    continue

                game_pk = str(snap.get("game_pk") or snap.get("id") or "")
                game_start = str(snap.get("game_start_utc") or "")
                game_date = game_start[:10] if len(game_start) >= 10 else ""
                if not game_pk or not game_date:
                    continue

                for side in ("home", "away"):
                    side_obj = snap.get(side, {})
                    if not isinstance(side_obj, dict):
                        continue
                    team_name = side_obj.get("team_name", "")
                    team_id = str(side_obj.get("team_id", ""))
                    players = side_obj.get("players", [])
                    if not isinstance(players, list):
                        continue

                    for player in players:
                        player_id = str(player.get("player_id") or "")
                        player_name = str(player.get("name") or "")
                        bat_side = str(player.get("bat_side") or "R")
                        pitch_hand = str(player.get("pitch_hand") or "R")

                        pitching = player.get("pitching", {})
                        if (
                            pitching
                            and isinstance(pitching, dict)
                            and (pitching.get("battersFaced") or pitching.get("numberOfPitches"))
                        ):
                            ip_str = str(pitching.get("inningsPitched") or "0.0")
                            try:
                                ip = float(ip_str)
                            except ValueError:
                                ip = 0.0
                            bf = int(pitching.get("battersFaced") or int(ip * 4))
                            pitches = int(pitching.get("numberOfPitches") or int(ip * 16))
                            strikes = int(pitching.get("strikes") or int(pitches * 0.65))
                            k = int(pitching.get("strikeOuts") or 0)
                            bb = int(pitching.get("baseOnBalls") or 0)
                            er = int(pitching.get("earnedRuns") or 0)
                            order = int(player.get("pitching_order") or 0)

                            k_pct = k / max(1, bf)
                            bb_pct = bb / max(1, bf)
                            csw_est = (strikes / max(1, pitches)) * 0.45 + (k / max(1, bf)) * 0.15

                            pitcher_rows.append(
                                {
                                    "game_pk": game_pk,
                                    "game_date": game_date,
                                    "team": team_name,
                                    "team_id": team_id,
                                    "pitcher_id": player_id,
                                    "pitcher_name": player_name,
                                    "pitch_hand": pitch_hand,
                                    "pitching_order": order,
                                    "is_starter": order == 1,
                                    "innings_pitched": ip,
                                    "batters_faced": bf,
                                    "pitches": pitches,
                                    "csw_rate": round(csw_est, 4),
                                    "k_rate": round(k_pct, 4),
                                    "bb_rate": round(bb_pct, 4),
                                    "k_minus_bb_rate": round(k_pct - bb_pct, 4),
                                    "earned_runs": er,
                                }
                            )

                        batting = player.get("batting", {})
                        if (
                            batting
                            and isinstance(batting, dict)
                            and (batting.get("plateAppearances") or batting.get("atBats"))
                        ):
                            pa = int(batting.get("plateAppearances") or 0)
                            ab = int(batting.get("atBats") or pa)
                            hits = int(batting.get("hits") or 0)
                            doubles = int(batting.get("doubles") or 0)
                            triples = int(batting.get("triples") or 0)
                            hr = int(batting.get("homeRuns") or 0)
                            bb_b = int(batting.get("baseOnBalls") or 0)
                            k_b = int(batting.get("strikeOuts") or 0)
                            bip = max(0, ab - k_b)
                            order = player.get("batting_order")

                            batter_rows.append(
                                {
                                    "game_pk": game_pk,
                                    "game_date": game_date,
                                    "team": team_name,
                                    "team_id": team_id,
                                    "batter_id": player_id,
                                    "batter_name": player_name,
                                    "bat_side": bat_side,
                                    "batting_order": order,
                                    "pa": pa,
                                    "ab": ab,
                                    "hits": hits,
                                    "doubles": doubles,
                                    "triples": triples,
                                    "home_runs": hr,
                                    "walks": bb_b,
                                    "strikeouts": k_b,
                                    "bip_count": bip,
                                }
                            )

    df_pitchers = pl.DataFrame(pitcher_rows) if pitcher_rows else pl.DataFrame()
    df_batters = pl.DataFrame(batter_rows) if batter_rows else pl.DataFrame()

    pitcher_out = statcast_dir / PITCHER_METRICS_FILE
    batter_out = statcast_dir / BATTER_METRICS_FILE

    if not df_pitchers.is_empty():
        df_pitchers.write_parquet(pitcher_out)
    if not df_batters.is_empty():
        df_batters.write_parquet(batter_out)

    return df_pitchers, df_batters
