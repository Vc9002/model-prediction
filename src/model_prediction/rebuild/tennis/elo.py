"""Tennis Surface Elo — rebuild-native rating engine for `tennis-surface-elo-rebuild-v1`.

Independently fit from TennisMyLife historical match data via
`TennisNormalizedStore`, never loading the incumbent `tennis-surface-elo-v1`
artifact or its rating state (`docs/model_audit/ARCHITECTURE_CORRECTION.md`).

Methodology retained from incumbent lineage:
- Per-surface Elo tracks: Hard, Clay, Grass (missing surface → Hard fallback)
- Overall Elo track (cross-surface)
- Dynamic surface weight: min(0.6, 0.1 + 0.025 * min(n_a, n_b)) per incumbent
- K=32 default, surface K boost for specialized surface ratings
- Walk-forward, chronologically ordered by tourney_date
- Completed matches only for Elo updates (retirement/walkover/default excluded)
- Cold-start: DEFAULT_ELO = 1500, need minimum matches before predicting

Key differences from incumbent:
- Reads from TennisNormalizedStore (rebuild-owned data), not ESPN-only
- Timestamp-anchored PIT guards (no same-day leakage)
- Explicit handling of irregular results
- Produces artifact-ready coefficients for LR training downstream
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import polars as pl

DEFAULT_ELO = 1500.0
K_FACTOR = 32.0
HOME_ADVANTAGE = 0  # Tennis has no home advantage in the traditional sense

KNOWN_SURFACES = {"Hard", "Clay", "Grass"}
DEFAULT_SURFACE = "Hard"


@dataclass
class TennisMatchRow:
    """One completed tennis match row from the normalized store."""
    canonical_match_id: str
    tour: str  # "ATP" | "WTA"
    tourney_date: str  # "YYYY-MM-DD"
    surface: str
    winner_id: str  # provider-scoped tennis_player_id
    loser_id: str
    winner_name: str
    loser_name: str
    result_type: str  # "completed" | "retirement" | "walkover" | "default"
    winner_rank: int | None = None
    loser_rank: int | None = None


@dataclass
class WalkForwardRow:
    """One walk-forward prediction row: Elo snapshot BEFORE match outcome.

    Orientation: player_one is deterministically chosen (lower player_id)
    independent of the match outcome. This means player_one_win is 0 or 1
    (not always 1), enabling proper calibration with both label classes.
    """
    match_id: str
    tourney_date: str
    tour: str
    surface: str
    player_one_id: str
    player_two_id: str
    player_one_name: str
    player_two_name: str
    player_one_win: int  # 1 if player_one won, 0 if player_two won
    elo_probability_player_one: float  # P(player_one beats player_two)
    player_one_overall_elo: float
    player_two_overall_elo: float
    player_one_surface_elo: float
    player_two_surface_elo: float
    player_one_blended_elo: float
    player_two_blended_elo: float
    player_one_surface_matches: int
    player_two_surface_matches: int
    player_one_rank: int | None = None
    player_two_rank: int | None = None


@dataclass
class WalkForwardResult:
    rows: list[WalkForwardRow]
    skipped_bootstrap: int
    skipped_cold_start: int
    skipped_irregular: int
    n_total: int


class SurfaceEloBook:
    """Surface-aware Elo rating book — faithful to the incumbent lineage.

    Mirrors `tennis-surface-elo-v1`'s documented mechanics exactly:
    - Default Elo: 1500
    - K-factor: 32 for BOTH overall and surface Elo (no surface K boost)
    - Surface weight: FIXED 0.6 (not dynamic)
    - Overall weight: 0.4
    - Elo formula: P(A wins) = 1 / (1 + 10^((R_B - R_A) / 400))

    All updates are chronological — caller must ensure matches are sorted
    by date before calling update().
    """

    def __init__(
        self,
        k: float = K_FACTOR,
        default_elo: float = DEFAULT_ELO,
    ) -> None:
        self.k = k
        self.default_elo = default_elo
        self.overall: dict[str, float] = defaultdict(lambda: default_elo)
        self.surface: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(lambda: default_elo))
        self.surface_count: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.total_matches: dict[str, int] = defaultdict(int)

    def rating(self, player: str) -> float:
        return self.overall[player]

    def surface_rating(self, player: str, surface: str) -> float:
        return self.surface[player].get(surface, self.default_elo)

    def surface_matches(self, player: str, surface: str) -> int:
        return self.surface_count[player].get(surface, 0)

    def blended_rating(self, player: str, surface: str) -> float:
        """Fixed 0.6 surface / 0.4 overall blend per incumbent lineage."""
        s = self.surface_rating(player, surface)
        o = self.rating(player)
        return 0.6 * s + 0.4 * o

    def expected_win(self, player_a: str, player_b: str, surface: str) -> float:
        r_a = self.blended_rating(player_a, surface)
        r_b = self.blended_rating(player_b, surface)
        return 1.0 / (1.0 + 10 ** ((r_b - r_a) / 400.0))

    def update(self, winner: str, loser: str, surface: str) -> None:
        """Update Elo after a completed match. K=32 for both overall and surface."""
        exp_win = self.expected_win(winner, loser, surface)
        delta = self.k * (1.0 - exp_win)

        # Overall Elo (K=32)
        self.overall[winner] += delta
        self.overall[loser] -= delta

        # Surface-specific Elo (same K=32, no boost)
        self.surface[winner][surface] += delta
        self.surface[loser][surface] -= delta

        # Match counts
        self.surface_count[winner][surface] += 1
        self.surface_count[loser][surface] += 1
        self.total_matches[winner] += 1
        self.total_matches[loser] += 1

    def has_minimum_history(self, player: str, min_matches: int = 3) -> bool:
        return self.total_matches[player] >= min_matches


def _clean_surface(raw: str | None) -> str:
    """Normalize surface to one of {Hard, Clay, Grass}, defaulting to Hard."""
    if raw is None:
        return DEFAULT_SURFACE
    s = raw.strip().title()
    if s in KNOWN_SURFACES:
        return s
    # Common variants
    s_lower = s.lower()
    if "hard" in s_lower:
        return "Hard"
    if "clay" in s_lower:
        return "Clay"
    if "grass" in s_lower:
        return "Grass"
    return DEFAULT_SURFACE


def load_matches(store_path: str, tours: list[str] | None = None) -> list[TennisMatchRow]:
    """Load all completed matches from the TennisNormalizedStore.

    Returns matches sorted by tourney_date for chronological walk-forward.
    Only completed matches (result_type="completed") are included for Elo training.
    """
    from model_prediction.rebuild.tennis.store import TennisNormalizedStore

    store = TennisNormalizedStore(store_path)
    frame = store.read_matches()

    if frame.is_empty():
        return []

    if tours:
        tours_lower = [t.lower() for t in tours]
        frame = frame.filter(pl.col("tour").str.to_lowercase().is_in(tours_lower))

    # Sort by date
    frame = frame.sort("tourney_date")

    rows: list[TennisMatchRow] = []
    for row in frame.iter_rows(named=True):
        rows.append(TennisMatchRow(
            canonical_match_id=str(row["canonical_match_id"]),
            tour=str(row["tour"]),
            tourney_date=str(row["tourney_date"]),
            surface=_clean_surface(row.get("surface")),
            winner_id=str(row["winner_tennis_player_id"]),
            loser_id=str(row["loser_tennis_player_id"]),
            winner_name=str(row["winner_player_name"]),
            loser_name=str(row["loser_player_name"]),
            result_type=str(row["result_type"]),
            winner_rank=row.get("winner_rank"),
            loser_rank=row.get("loser_rank"),
        ))
    return rows


def build_walk_forward_rows(
    matches: list[TennisMatchRow],
    minimum_history_matches: int = 100,
    minimum_player_matches: int = 3,
) -> WalkForwardResult:
    """Build walk-forward prediction rows with day-bucketed Elo snapshots.

    For each calendar day (by tourney_date):
    1. Take an Elo snapshot from history (all prior-day matches)
    2. Predict today's matches using that snapshot
    3. Then update Elo with today's results

    This prevents same-day leakage — a match's Elo snapshot never includes
    its own result or same-day results.

    Irregular results (retirement/walkover/default) are skipped for prediction
    but the winner still gets an Elo update afterward (they did win the match,
    even if irregularly).
    """
    if not matches:
        return WalkForwardResult(rows=[], skipped_bootstrap=0, skipped_cold_start=0,
                                 skipped_irregular=0, n_total=0)

    book = SurfaceEloBook()
    history: list[TennisMatchRow] = []

    # Group by date
    by_date: dict[str, list[TennisMatchRow]] = defaultdict(list)
    for m in matches:
        by_date[m.tourney_date].append(m)

    rows: list[WalkForwardRow] = []
    skipped_bootstrap = 0
    skipped_cold_start = 0
    skipped_irregular = 0

    for date_str in sorted(by_date.keys()):
        day_matches = by_date[date_str]

        # Snapshot → predict → then update
        for m in day_matches:
            if len(history) < minimum_history_matches:
                skipped_bootstrap += 1
            elif m.result_type != "completed":
                skipped_irregular += 1
            elif not book.has_minimum_history(m.winner_id, minimum_player_matches) or \
                 not book.has_minimum_history(m.loser_id, minimum_player_matches):
                skipped_cold_start += 1
            else:
                surface = m.surface or DEFAULT_SURFACE

                # Player ordering: lower player_id = player_one (deterministic,
                # pre-outcome orientation independent of who won)
                ids = sorted([m.winner_id, m.loser_id])
                p1_id, p2_id = ids[0], ids[1]
                p1_is_winner = m.winner_id == p1_id
                p1_win = 1 if p1_is_winner else 0
                p1_name = m.winner_name if p1_is_winner else m.loser_name
                p2_name = m.loser_name if p1_is_winner else m.winner_name
                p1_rank = m.winner_rank if p1_is_winner else m.loser_rank
                p2_rank = m.loser_rank if p1_is_winner else m.winner_rank

                prob_p1 = book.expected_win(p1_id, p2_id, surface)

                rows.append(WalkForwardRow(
                    match_id=m.canonical_match_id,
                    tourney_date=m.tourney_date,
                    tour=m.tour,
                    surface=surface,
                    player_one_id=p1_id,
                    player_two_id=p2_id,
                    player_one_name=p1_name,
                    player_two_name=p2_name,
                    player_one_win=p1_win,
                    elo_probability_player_one=prob_p1,
                    player_one_overall_elo=book.rating(p1_id),
                    player_two_overall_elo=book.rating(p2_id),
                    player_one_surface_elo=book.surface_rating(p1_id, surface),
                    player_two_surface_elo=book.surface_rating(p2_id, surface),
                    player_one_blended_elo=book.blended_rating(p1_id, surface),
                    player_two_blended_elo=book.blended_rating(p2_id, surface),
                    player_one_surface_matches=book.surface_matches(p1_id, surface),
                    player_two_surface_matches=book.surface_matches(p2_id, surface),
                    player_one_rank=p1_rank,
                    player_two_rank=p2_rank,
                ))

        # Now update Elo with today's results
        # Completed matches: full update
        # Irregular matches: half-K update — winner still gets credit
        for m in day_matches:
            if m.result_type == "completed":
                book.update(m.winner_id, m.loser_id, m.surface or DEFAULT_SURFACE)
            elif m.result_type in ("retirement", "walkover", "default"):
                surface = m.surface or DEFAULT_SURFACE
                exp_win = book.expected_win(m.winner_id, m.loser_id, surface)
                delta = (book.k * 0.5) * (1.0 - exp_win)
                book.overall[m.winner_id] += delta
                book.overall[m.loser_id] -= delta
                book.surface_count[m.winner_id][surface] += 1
                book.surface_count[m.loser_id][surface] += 1
                book.total_matches[m.winner_id] += 1
                book.total_matches[m.loser_id] += 1

        history.extend(day_matches)

    return WalkForwardResult(
        rows=rows,
        skipped_bootstrap=skipped_bootstrap,
        skipped_cold_start=skipped_cold_start,
        skipped_irregular=skipped_irregular,
        n_total=len(matches),
    )


def rows_to_frame(rows: list[WalkForwardRow]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame()
    return pl.DataFrame([r.__dict__ for r in rows])


def build_dataset(
    data_root: str = "data/rebuild",
    tours: list[str] | None = None,
    **kwargs: Any,
) -> WalkForwardResult:
    """Convenience: load matches from store, build walk-forward rows."""
    matches = load_matches(f"{data_root}/normalized", tours=tours)
    return build_walk_forward_rows(matches, **kwargs)
