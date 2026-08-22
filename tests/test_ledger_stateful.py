"""Stateful property-based testing of Ledger APIs using Hypothesis RuleBasedStateMachine."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hypothesis import strategies as st
from hypothesis.stateful import Bundle, RuleBasedStateMachine, invariant, rule

from model_prediction.domain import League, MarketType, PickRequest
from model_prediction.ledger import PickLedger


class LedgerStateMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.ledger = PickLedger(self.data_dir / "picks.xlsx")
        self.created_picks: dict[str, dict] = {}
        self.settled_picks: set[str] = set()
        self.pick_counter = 0

    def teardown(self) -> None:
        self.temp_dir.cleanup()

    picks = Bundle("picks")

    @rule(
        target=picks,
        price=st.integers(min_value=-200, max_value=200).filter(lambda x: abs(x) >= 100),
        units=st.floats(min_value=0.25, max_value=2.0),
        confidence=st.integers(min_value=50, max_value=90),
    )
    def create_pick(self, price: int, units: float, confidence: int):
        self.pick_counter += 1
        pick_req = PickRequest(
            event_start_utc=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
            event_id=f"event_{self.pick_counter}",
            league=League.MLB,
            away_team="NYY",
            home_team="BOS",
            market_type=MarketType.MONEYLINE,
            selection="home",
            line=None,
            sportsbook="ExampleBook",
            american_odds=price,
            model_probability=0.55,
            model_uncertainty=0.01,
            model_version="test-v1",
            rationale="Test rationale",
            risks="Test risks",
        )

        logged = self.ledger.append_call(pick_req, round(units, 2), confidence)
        pick_id = logged["pick_id"]
        self.created_picks[pick_id] = {
            "pick_id": pick_id,
            "units": round(units, 2),
            "odds": price,
        }
        return pick_id

    @rule(
        pick_id=picks,
        home_score=st.integers(min_value=0, max_value=10),
        away_score=st.integers(min_value=0, max_value=10),
    )
    def settle_pick(self, pick_id: str, home_score: int, away_score: int):
        if pick_id in self.settled_picks:
            return  # Already settled

        self.ledger.settle(
            pick_id,
            away_score=away_score,
            home_score=home_score,
            closing_line=0.0,
            closing_american_odds=-110,
        )
        self.settled_picks.add(pick_id)

    @invariant()
    def check_row_count_and_integrity(self):
        report = self.ledger.report()
        assert report["records"] == len(self.created_picks)
        assert report["open"] == len(self.created_picks) - len(self.settled_picks)


from hypothesis import settings

TestLedgerStateful = LedgerStateMachine.TestCase
TestLedgerStateful.settings = settings(max_examples=5, stateful_step_count=10, deadline=None)
