"""Tests for the real settlement-outcome determination logic
(mlb_settle_and_capture_closing.py, CLAUDE.md's next-phase Task 19):
determine_outcome() must correctly resolve moneyline/spread/total
WIN/LOSS/PUSH from a real final score against one evaluated side/line,
using each side's own signed line, not an assumed mirrored pair.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from mlb_settle_and_capture_closing import determine_outcome


class TestMoneylineOutcome:
    def test_home_wins_home_side_is_a_win(self):
        assert determine_outcome("moneyline", "home", None, home_score=5, away_score=3) == "WIN"

    def test_home_wins_away_side_is_a_loss(self):
        assert determine_outcome("moneyline", "away", None, home_score=5, away_score=3) == "LOSS"

    def test_away_wins_away_side_is_a_win(self):
        assert determine_outcome("moneyline", "away", None, home_score=2, away_score=7) == "WIN"

    def test_tie_is_a_push(self):
        # Doesn't happen in real MLB (no ties), but the formula must not
        # crash or silently mislabel a hypothetical tie.
        assert determine_outcome("moneyline", "home", None, home_score=4, away_score=4) == "PUSH"


class TestSpreadOutcome:
    def test_home_favored_and_covers(self):
        # home -1.5, wins by 3 -> covers.
        assert determine_outcome("spread", "home", -1.5, home_score=5, away_score=2) == "WIN"

    def test_home_favored_and_does_not_cover(self):
        # home -1.5, wins by only 1 -> does not cover.
        assert determine_outcome("spread", "home", -1.5, home_score=3, away_score=2) == "LOSS"

    def test_away_underdog_covers_on_a_loss(self):
        # away +2.5, loses by 1 -> still covers.
        assert determine_outcome("spread", "away", 2.5, home_score=3, away_score=2) == "WIN"

    def test_away_underdog_fails_to_cover(self):
        # away +1.5, loses by 3 -> does not cover.
        assert determine_outcome("spread", "away", 1.5, home_score=5, away_score=2) == "LOSS"

    def test_whole_integer_line_can_push(self):
        # home -2, wins by exactly 2 -> push.
        assert determine_outcome("spread", "home", -2.0, home_score=6, away_score=4) == "PUSH"

    def test_independent_lines_not_assumed_mirrored(self):
        # A real captured away line need not be the exact mirror of the
        # home line at the same market -- each side is evaluated on its
        # own real signed line, not a derived complement.
        assert determine_outcome("spread", "away", -2.5, home_score=1, away_score=6) == "WIN"


class TestTotalOutcome:
    def test_over_covers(self):
        assert determine_outcome("total", "over", 7.5, home_score=5, away_score=4) == "WIN"

    def test_over_fails(self):
        assert determine_outcome("total", "over", 9.5, home_score=2, away_score=1) == "LOSS"

    def test_under_covers(self):
        assert determine_outcome("total", "under", 9.5, home_score=2, away_score=1) == "WIN"

    def test_whole_integer_total_can_push(self):
        assert determine_outcome("total", "over", 8.0, home_score=5, away_score=3) == "PUSH"
        assert determine_outcome("total", "under", 8.0, home_score=5, away_score=3) == "PUSH"
