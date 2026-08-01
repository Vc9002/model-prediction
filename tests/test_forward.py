"""Tests for forward.py -- the legacy MLB Measured Edge paired-model slate
builder. Superseded by the learned-model path (learned_forward.py) for live
daily forecasting, but still reachable via ``forecast --model
legacy-measured-edge`` and had zero test coverage.

These focus on ``_paired_event_candidates`` (the selection-by-edge and
candidate-field logic) and ``_teams`` using real, hand-built dataclass
instances rather than mocking the ESPN/odds-feed network clients that
``build_mlb_slate`` orchestrates around them.
"""

from __future__ import annotations

from datetime import UTC, datetime

from model_prediction.data_sources.mlb_market_odds import MarketSideQuote, MLBGameOdds
from model_prediction.domain import MarketType
from model_prediction.forward import MLBForwardCandidate, _paired_event_candidates, _teams
from model_prediction.models.base import ScoreSimulation
from model_prediction.models.mlb import (
    MarginModelOutput,
    MarketDistribution,
    RunEstimate,
    TotalsModelOutput,
)


def _event() -> dict:
    return {
        "id": "999",
        "date": "2026-07-01T23:00:00Z",
        "competitions": [
            {
                "competitors": [
                    {"homeAway": "home", "team": {"displayName": "Home Team"}},
                    {"homeAway": "away", "team": {"displayName": "Away Team"}},
                ]
            }
        ],
    }


def _run_estimate() -> RunEstimate:
    return RunEstimate(
        away_expected_runs=3.5,
        home_expected_runs=4.5,
        factors={},
        uncertainty=0.4,
        uncertainty_components={},
        formula_version="v1",
        feature_schema_version="1",
        configuration_hash="hash",
    )


def _simulation() -> ScoreSimulation:
    return ScoreSimulation(away_scores=[3, 4, 2], home_scores=[5, 4, 6], uncertainty=0.4, model_version="v1")


class _FakeMeasuredEdgeModel:
    """Stands in for MeasuredEdgeMarginModel/MeasuredEdgeTotalsModel.

    Only implements the two methods and the ``raw`` mapping
    _paired_event_candidates actually reads.
    """

    def __init__(self, scale: float = 1.0, offset: float = 0.0) -> None:
        self.raw = {
            "model_name": "measured-edge-fake",
            "model_version": "measured-edge-fake-v1",
            "artifact_hash": "fakehash",
            "calibration_version": "identity-v1",
        }
        self._scale = scale
        self._offset = offset

    def calibrate_selected_side(self, raw_probability: float) -> float:
        return self._scale * raw_probability + self._offset


def _margin_output(home_win_probability: float, away_line: float = -1.5) -> MarginModelOutput:
    return MarginModelOutput(
        run_estimate=_run_estimate(),
        simulation=_simulation(),
        moneyline=MarketDistribution(
            first_selection="away",
            first_win_probability=1 - home_win_probability,
            second_selection="home",
            second_win_probability=home_win_probability,
            push_probability=0.0,
        ),
        spread=MarketDistribution(
            first_selection="away",
            first_win_probability=1 - home_win_probability,
            second_selection="home",
            second_win_probability=home_win_probability,
            push_probability=0.0,
        ),
        away_spread_line=away_line,
        model_version="measured-edge-fake-v1",
        model_artifact_hash="fakehash",
        calibration_version="identity-v1",
    )


def _totals_output(over_probability: float, total_line: float = 8.5) -> TotalsModelOutput:
    return TotalsModelOutput(
        run_estimate=_run_estimate(),
        simulation=_simulation(),
        total=MarketDistribution(
            first_selection="over",
            first_win_probability=over_probability,
            second_selection="under",
            second_win_probability=1 - over_probability,
            push_probability=0.0,
        ),
        total_line=total_line,
        model_version="measured-edge-fake-v1",
        model_artifact_hash="fakehash",
        calibration_version="identity-v1",
    )


def _odds_snapshot() -> MLBGameOdds:
    return MLBGameOdds(
        event_id="999",
        event_start_utc="2026-07-01T23:00:00Z",
        away_team="Away Team",
        home_team="Home Team",
        provider="polymarket_us",
        observed_at_utc="2026-07-01T20:00:00Z",
        markets={
            "moneyline": {
                "away": MarketSideQuote("away", None, 150, 0.40, None),
                "home": MarketSideQuote("home", None, -130, 0.565, None),
            },
            "spread": {
                "away": MarketSideQuote("away", 1.5, 110, 0.476, None),
                "home": MarketSideQuote("home", -1.5, -130, 0.565, None),
            },
            "total": {
                "over": MarketSideQuote("over", 8.5, -110, 0.524, None),
                "under": MarketSideQuote("under", 8.5, -110, 0.524, None),
            },
        },
        raw_response={},
        snapshot_hash="snaphash",
    )


def test_teams_reads_home_away_from_competitors() -> None:
    away, home = _teams(_event())
    assert away == "Away Team"
    assert home == "Home Team"


def test_paired_event_candidates_selects_the_model_favorite() -> None:
    # Home team is the model favorite (65%) -- home should be the
    # moneyline/spread selection regardless of what the market price is.
    candidates = _paired_event_candidates(
        _event(),
        _odds_snapshot(),
        _margin_output(home_win_probability=0.65),
        _totals_output(over_probability=0.55),
        _FakeMeasuredEdgeModel(),
        _FakeMeasuredEdgeModel(),
        datetime(2026, 7, 1, 20, tzinfo=UTC),
    )
    by_market = {c.market_type: c for c in candidates}
    assert set(by_market) == {MarketType.MONEYLINE, MarketType.SPREAD, MarketType.TOTAL}

    moneyline = by_market[MarketType.MONEYLINE]
    assert moneyline.selection == "home"
    assert moneyline.american_odds == -130
    assert moneyline.raw_probability == 0.65
    assert moneyline.line is None  # moneyline never carries a line

    spread = by_market[MarketType.SPREAD]
    assert spread.selection == "home"
    assert spread.line == -1.5  # the selected side's own line, not away's

    total = by_market[MarketType.TOTAL]
    assert total.selection == "over"
    assert total.line == 8.5


def test_paired_event_candidates_never_switches_to_the_underdog_for_a_better_price() -> None:
    # Home is only a 30% model favorite, but priced as the big plus-money
    # underdog (+400) while away (70% modeled) is priced as the expensive
    # favorite (-900). Expected-value math would favor home here (0.30*5.0-1
    # = +0.5 vs away's 0.70*1.111-1 = -0.22) -- that used to flip the
    # selection to the side the model does NOT think will win. Operator
    # directive (2026-07-30/31): models pick the side they think wins,
    # period; edge/price is informational only, never a selection factor.
    odds = MLBGameOdds(
        event_id="999",
        event_start_utc="2026-07-01T23:00:00Z",
        away_team="Away Team",
        home_team="Home Team",
        provider="polymarket_us",
        observed_at_utc="2026-07-01T20:00:00Z",
        markets={
            "moneyline": {
                "away": MarketSideQuote("away", None, -900, 0.9, None),
                "home": MarketSideQuote("home", None, 400, 0.2, None),
            },
            "spread": {
                "away": MarketSideQuote("away", 1.5, -900, 0.9, None),
                "home": MarketSideQuote("home", -1.5, 400, 0.2, None),
            },
            "total": {
                "over": MarketSideQuote("over", 8.5, -110, 0.524, None),
                "under": MarketSideQuote("under", 8.5, -110, 0.524, None),
            },
        },
        raw_response={},
        snapshot_hash="snaphash",
    )
    candidates = _paired_event_candidates(
        _event(),
        odds,
        _margin_output(home_win_probability=0.30),
        _totals_output(over_probability=0.45),
        _FakeMeasuredEdgeModel(),
        _FakeMeasuredEdgeModel(),
        datetime(2026, 7, 1, 20, tzinfo=UTC),
    )
    by_market = {c.market_type: c for c in candidates}
    assert by_market[MarketType.MONEYLINE].selection == "away"
    assert by_market[MarketType.SPREAD].selection == "away"
    assert by_market[MarketType.TOTAL].selection == "under"


def test_paired_event_candidates_applies_calibration_not_just_raw_probability() -> None:
    candidates = _paired_event_candidates(
        _event(),
        _odds_snapshot(),
        _margin_output(home_win_probability=0.65),
        _totals_output(over_probability=0.55),
        _FakeMeasuredEdgeModel(scale=0.9, offset=0.02),
        _FakeMeasuredEdgeModel(scale=0.9, offset=0.02),
        datetime(2026, 7, 1, 20, tzinfo=UTC),
    )
    moneyline = next(c for c in candidates if c.market_type is MarketType.MONEYLINE)
    assert moneyline.raw_probability == 0.65
    assert moneyline.shrunk_probability != moneyline.raw_probability
    assert moneyline.shrunk_probability == round(0.9 * 0.65 + 0.02, 6)


def test_paired_event_candidates_no_vig_probability_sums_to_one_across_sides() -> None:
    candidates = _paired_event_candidates(
        _event(),
        _odds_snapshot(),
        _margin_output(home_win_probability=0.65),
        _totals_output(over_probability=0.55),
        _FakeMeasuredEdgeModel(),
        _FakeMeasuredEdgeModel(),
        datetime(2026, 7, 1, 20, tzinfo=UTC),
    )
    moneyline = next(c for c in candidates if c.market_type is MarketType.MONEYLINE)
    # away(0.40) + home(0.565) = 0.965 raw implied; de-vigged home share:
    assert moneyline.no_vig_probability == round(0.565 / (0.40 + 0.565), 6)


def test_mlb_forward_candidate_is_frozen_and_carries_full_provenance() -> None:
    candidates = _paired_event_candidates(
        _event(),
        _odds_snapshot(),
        _margin_output(home_win_probability=0.65),
        _totals_output(over_probability=0.55),
        _FakeMeasuredEdgeModel(),
        _FakeMeasuredEdgeModel(),
        datetime(2026, 7, 1, 20, tzinfo=UTC),
    )
    moneyline = next(c for c in candidates if c.market_type is MarketType.MONEYLINE)
    assert isinstance(moneyline, MLBForwardCandidate)
    assert moneyline.model_artifact_hash == "fakehash"
    assert moneyline.calibration_version == "identity-v1"
    assert moneyline.sportsbook == "polymarket_us"
    assert moneyline.observed_at_utc == "2026-07-01T20:00:00Z"
