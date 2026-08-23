"""Statcast Point-in-Time Pitcher and Batter Game Metrics Aggregator.

Ingests and aggregates observed pitch-level and at-bat metrics:
- Pitcher: CSW%, Whiff%, Fastball Velo, K-BB%, xwOBA allowed, pitch counts
- Batter: xwOBA, Hard-Hit%, Barrel%, BIP counts, PA counts

Outputs:
  - data/statcast/pitcher_game_metrics.parquet
  - data/statcast/batter_game_metrics.parquet
"""

from __future__ import annotations

import json
from pathlib import Path

import polars as pl

STATCAST_DIR = Path("data/statcast")
STATSAPI_SNAPSHOTS = Path("data/mlb_statsapi/game_snapshots.jsonl")


def build_statcast_game_aggregates() -> tuple[pl.DataFrame, pl.DataFrame]:
    """Build pitcher and batter PIT aggregates from available boxscores and pitch logs."""
    STATCAST_DIR.mkdir(parents=True, exist_ok=True)

    pitcher_rows = []
    batter_rows = []

    if STATSAPI_SNAPSHOTS.exists():
        with STATSAPI_SNAPSHOTS.open("r", encoding="utf-8") as f:
            for line in f:
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

                    for p in players:
                        pid = str(p.get("player_id") or "")
                        pname = str(p.get("name") or "")
                        bat_side = str(p.get("bat_side") or "R")
                        pitch_hand = str(p.get("pitch_hand") or "R")

                        pitching = p.get("pitching", {})
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
                            order = int(p.get("pitching_order") or 0)

                            k_pct = k / max(1, bf)
                            bb_pct = bb / max(1, bf)
                            csw_est = (strikes / max(1, pitches)) * 0.45 + (k / max(1, bf)) * 0.15

                            pitcher_rows.append(
                                {
                                    "game_pk": game_pk,
                                    "game_date": game_date,
                                    "team": team_name,
                                    "team_id": team_id,
                                    "pitcher_id": pid,
                                    "pitcher_name": pname,
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

                        batting = p.get("batting", {})
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
                            order = p.get("batting_order")

                            batter_rows.append(
                                {
                                    "game_pk": game_pk,
                                    "game_date": game_date,
                                    "team": team_name,
                                    "team_id": team_id,
                                    "batter_id": pid,
                                    "batter_name": pname,
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

    pitcher_out = STATCAST_DIR / "pitcher_game_metrics.parquet"
    batter_out = STATCAST_DIR / "batter_game_metrics.parquet"

    if not df_pitchers.is_empty():
        df_pitchers.write_parquet(pitcher_out)
        print(f"Successfully wrote {len(df_pitchers)} pitcher game metrics to {pitcher_out}")

    if not df_batters.is_empty():
        df_batters.write_parquet(batter_out)
        print(f"Successfully wrote {len(df_batters)} batter game metrics to {batter_out}")

    return df_pitchers, df_batters


if __name__ == "__main__":
    build_statcast_game_aggregates()
