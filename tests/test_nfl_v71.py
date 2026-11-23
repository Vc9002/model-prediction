"""nfl-spread-structural-v7.1 / nfl-total-structural-v7.1: the v7 engines plus probability_logit_slope.

The shrink is p' = sigmoid(s * logit(p)) with intercept 0 on the final cover/over probability
(quick-261123-2w2). These pin: no field => v7 bit-for-bit; the formula; the artifacts; and the
scalar replay, which re-checks every served probability at log, request and settlement time.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from model_prediction.cli import forecast as cli_forecast
from model_prediction.cli.forecast_nfl_moneyline_shadow import moneyline_shadow_input
from model_prediction.learned_replay import snapshot_hash
from model_prediction.models.nfl_spread_v7 import NFLSpreadModelV7
from model_prediction.models.nfl_spread_v7 import _logit_shrink as spread_logit_shrink
from model_prediction.models.nfl_total_v7 import NFLTotalModelV7
from model_prediction.models.nfl_total_v7 import _logit_shrink as total_logit_shrink
from model_prediction.production_registry import compute_artifact_hash
from model_prediction.scalar_artifact_replay import (
    HISTORICAL_ENGINE_HASHES,
    build_snapshot,
    replay_snapshot,
    validate_decision_snapshot,
)

REPO = Path(__file__).resolve().parents[1]
MODELS = REPO / "config" / "models"

SPREAD_CASES = [
    {
        "home_pass_epa": 0.15,
        "away_pass_epa": -0.05,
        "home_rush_epa": 0.02,
        "away_rush_epa": -0.01,
        "home_def_pass_epa": -0.03,
        "away_def_pass_epa": 0.04,
        "home_qb_tier_adj": 1.0,
        "spread_home_line": -3.5,
    },
    {
        "home_pass_epa": -0.10,
        "away_pass_epa": 0.20,
        "spread_home_line": 9.5,
        "home_first_down_rate": 0.27,
        "away_first_down_rate": 0.33,
    },
    {"spread_home_line": -7.0, "is_neutral_site": True, "home_turnover_luck": 1.5},
]
TOTAL_CASES = [
    {
        "total_pass_epa": 0.25,
        "total_rush_epa": 0.05,
        "total_line": 44.5,
        "wind_mph": 8.0,
        "temperature_f": 50.0,
    },
    {"total_pass_epa": -0.3, "total_line": 49.5, "is_dome": True, "home_explosive_rate": 0.11},
    {"total_line": 38.5, "wind_mph": 20.0, "temperature_f": 20.0},
]
# Captured from the engines BEFORE probability_logit_slope existed (HEAD dab60a87).
V7_SPREAD_GOLDEN = [
    (0.506738, 0.493262, 0.665, 13.8, 4.02),
    (0.528974, 0.471026, 0.190825, 17.25, -8.47),
    (0.426621, 0.573379, 0.695768, 13.8, 4.85),
]
V7_TOTAL_GOLDEN = [(0.534647, 0.465353, 45.7), (0.502891, 0.497109, 49.6), (0.416777, 0.583223, 35.6)]


def _artifact(name: str) -> dict:
    return json.loads((MODELS / f"{name}.json").read_text(encoding="utf-8"))


def _sigmoid_shrink(p: float, s: float) -> float:
    return 1.0 / (1.0 + math.exp(-s * math.log(p / (1.0 - p))))


def test_field_absent_is_bit_identical_to_pre_slope_v7() -> None:
    spread = NFLSpreadModelV7.from_artifact(_artifact("nfl-spread-structural-v7"))
    total = NFLTotalModelV7.from_artifact(_artifact("nfl-total-structural-v7"))
    assert spread.probability_logit_slope == 1.0 and total.probability_logit_slope == 1.0
    for case, golden in zip(SPREAD_CASES, V7_SPREAD_GOLDEN, strict=True):
        fc = spread.forecast_spread("H", "A", **case)
        got = (fc.prob_home_cover, fc.prob_away_cover, fc.prob_home_win, fc.effective_sd)
        assert got + (fc.projected_margin_home,) == golden
    for case, golden in zip(TOTAL_CASES, V7_TOTAL_GOLDEN, strict=True):
        fc = total.forecast_total("H", "A", **case)
        assert (fc.prob_over, fc.prob_under, fc.projected_total) == golden


def test_explicit_slope_one_equals_absent_field() -> None:
    for name, cls, cases, method in (
        ("nfl-spread-structural-v7", NFLSpreadModelV7, SPREAD_CASES, "forecast_spread"),
        ("nfl-total-structural-v7", NFLTotalModelV7, TOTAL_CASES, "forecast_total"),
    ):
        base = cls.from_artifact(_artifact(name))
        explicit = cls.from_artifact({**_artifact(name), "probability_logit_slope": 1.0})
        for case in cases:
            assert getattr(base, method)("H", "A", **case) == getattr(explicit, method)("H", "A", **case)


def test_slope_zero_serves_exactly_half() -> None:
    spread = NFLSpreadModelV7.from_artifact(
        {**_artifact("nfl-spread-structural-v7"), "probability_logit_slope": 0}
    )
    total = NFLTotalModelV7.from_artifact(
        {**_artifact("nfl-total-structural-v7"), "probability_logit_slope": 0}
    )
    for case in SPREAD_CASES:
        fc = spread.forecast_spread("H", "A", **case)
        assert fc.prob_home_cover == 0.5 and fc.prob_away_cover == 0.5
    for case in TOTAL_CASES:
        fc = total.forecast_total("H", "A", **case)
        assert fc.prob_over == 0.5 and fc.prob_under == 0.5


def test_slope_quarter_matches_formula() -> None:
    for p in (0.35, 0.4123, 0.5, 0.57, 0.65):
        assert spread_logit_shrink(p, 0.25) == pytest.approx(_sigmoid_shrink(p, 0.25), abs=1e-15)
        assert total_logit_shrink(p, 0.25) == pytest.approx(_sigmoid_shrink(p, 0.25), abs=1e-15)
    raw_sp = NFLSpreadModelV7.from_artifact(_artifact("nfl-spread-structural-v7"))
    raw_tot = NFLTotalModelV7.from_artifact(_artifact("nfl-total-structural-v7"))
    q_sp = NFLSpreadModelV7.from_artifact(
        {**_artifact("nfl-spread-structural-v7"), "probability_logit_slope": 0.25}
    )
    q_tot = NFLTotalModelV7.from_artifact(
        {**_artifact("nfl-total-structural-v7"), "probability_logit_slope": 0.25}
    )
    for case in SPREAD_CASES:
        raw, shrunk = raw_sp.forecast_spread("H", "A", **case), q_sp.forecast_spread("H", "A", **case)
        # raw is rounded to 6 dp, so the formula on it agrees to ~1e-7, not bit-for-bit
        assert shrunk.prob_home_cover == pytest.approx(_sigmoid_shrink(raw.prob_home_cover, 0.25), abs=1e-6)
        assert shrunk.prob_home_cover + shrunk.prob_away_cover == pytest.approx(1.0, abs=1e-12)
        # only the cover price moves: margin, win probability and the replay sd are v7's
        assert (shrunk.projected_margin_home, shrunk.prob_home_win, shrunk.effective_sd) == (
            raw.projected_margin_home,
            raw.prob_home_win,
            raw.effective_sd,
        )
    for case in TOTAL_CASES:
        raw, shrunk = raw_tot.forecast_total("H", "A", **case), q_tot.forecast_total("H", "A", **case)
        assert shrunk.prob_over == pytest.approx(_sigmoid_shrink(raw.prob_over, 0.25), abs=1e-6)
        assert (shrunk.projected_total, shrunk.effective_sd) == (raw.projected_total, raw.effective_sd)


@pytest.mark.parametrize("bad", [1.5, -0.1, float("nan"), float("inf")])
def test_from_artifact_reads_and_bounds_the_slope(bad: float) -> None:
    art = {**_artifact("nfl-spread-structural-v7"), "probability_logit_slope": 0.37}
    assert NFLSpreadModelV7.from_artifact(art).probability_logit_slope == 0.37
    art_tot = {**_artifact("nfl-total-structural-v7"), "probability_logit_slope": 0.37}
    assert NFLTotalModelV7.from_artifact(art_tot).probability_logit_slope == 0.37
    with pytest.raises(ValueError, match="probability_logit_slope"):
        NFLSpreadModelV7.from_artifact({**art, "probability_logit_slope": bad})
    with pytest.raises(ValueError, match="probability_logit_slope"):
        NFLTotalModelV7.from_artifact({**art_tot, "probability_logit_slope": bad})


@pytest.mark.parametrize(
    ("v71", "v7", "cls"),
    [
        ("nfl-spread-structural-v7.1", "nfl-spread-structural-v7", NFLSpreadModelV7),
        ("nfl-total-structural-v7.1", "nfl-total-structural-v7", NFLTotalModelV7),
    ],
)
def test_v71_artifacts_load_and_hash_verifies(v71: str, v7: str, cls: type) -> None:
    art, parent = _artifact(v71), _artifact(v7)
    assert art["artifact_hash"] == compute_artifact_hash(art)
    assert art["model_version"] == v71 and art["qualification"]["model_version"] == v71
    assert art["parent_model_version"] == v7 and art["parent_artifact_hash"] == parent["artifact_hash"]
    # Same engine and replay method; every engine parameter is v7's.
    added = {
        "probability_logit_slope",
        "parent_model_version",
        "parent_artifact_hash",
        "probability_shrink_provenance",
    }
    changed = {"artifact_hash", "model_version", "note", "qualification"}
    assert set(art) == set(parent) | added
    assert {k: art[k] for k in parent if k not in changed} == {
        k: parent[k] for k in parent if k not in changed
    }
    slope = art["probability_logit_slope"]
    assert 0.0 <= slope <= 1.0
    model = cls.from_artifact(art)
    assert model.version == v71 and model.probability_logit_slope == slope
    loaded, error = cli_forecast._load_exact_artifact_contract(v71)
    assert error is None and loaded == art


def _spread_row(art: dict, event_id: str, selection: str, line: float, probability: float) -> dict:
    return {
        "model_version": art["model_version"],
        "model_artifact_hash": art["artifact_hash"],
        "league": "NFL",
        "event_id": event_id,
        "event_start_utc": "2026-11-29T18:00:00Z",
        "market_type": "spread",
        "selection": selection,
        "line": line,
        "model_probability": probability,
    }


@pytest.mark.parametrize("spread_home_line", [-2.5, 9.5])
def test_v71_spread_scalar_replay_round_trip(spread_home_line: float) -> None:
    art = _artifact("nfl-spread-structural-v7.1")
    model = NFLSpreadModelV7.from_artifact(art)
    raw_model = NFLSpreadModelV7.from_artifact(_artifact("nfl-spread-structural-v7"))
    kwargs = {"home_pass_epa": 0.12, "away_pass_epa": -0.04, "spread_home_line": spread_home_line}
    fc = model.forecast_spread("Buffalo Bills", "Baltimore Ravens", **kwargs)
    raw = raw_model.forecast_spread("Buffalo Bills", "Baltimore Ravens", **kwargs)
    # the forecast.py inference_inputs shape, including the 1.25x heavy-line sd
    snap = build_snapshot(
        art,
        {"home_team": "Buffalo Bills", "away_team": "Baltimore Ravens"},
        {"mean": fc.projected_margin_home, "sd": fc.effective_sd, "line": -spread_home_line},
        event_id="nfl-v71-1",
        event_start_utc="2026-11-29T18:00:00Z",
        observed_at_utc="2026-11-29T12:00:00Z",
    )
    replayed = replay_snapshot(snap)
    assert (replayed["home"], replayed["away"]) == (fc.prob_home_cover, fc.prob_away_cover)
    row = _spread_row(art, "nfl-v71-1", "home", spread_home_line, fc.prob_home_cover)
    assert validate_decision_snapshot(snap, row) == fc.prob_home_cover
    away = _spread_row(art, "nfl-v71-1", "away", -spread_home_line, fc.prob_away_cover)
    assert validate_decision_snapshot(snap, away) == fc.prob_away_cover
    if abs(raw.prob_home_cover - fc.prob_home_cover) > 2e-6:
        # a v7.1 row carrying the unshrunk v7 price is not what the artifact serves
        with pytest.raises(ValueError, match="scalar_replay_probability_mismatch"):
            validate_decision_snapshot(snap, {**row, "model_probability": raw.prob_home_cover})


def test_v71_total_scalar_replay_round_trip() -> None:
    art = _artifact("nfl-total-structural-v7.1")
    model = NFLTotalModelV7.from_artifact(art)
    fc = model.forecast_total("Buffalo Bills", "Baltimore Ravens", total_pass_epa=0.4, total_line=47.5)
    snap = build_snapshot(
        art,
        {"home_team": "Buffalo Bills", "away_team": "Baltimore Ravens"},
        {"mean": fc.projected_total, "sd": fc.effective_sd, "line": 47.5},
        event_id="nfl-v71-2",
        event_start_utc="2026-11-29T18:00:00Z",
        observed_at_utc="2026-11-29T12:00:00Z",
    )
    replayed = replay_snapshot(snap)
    assert (replayed["over"], replayed["under"]) == (fc.prob_over, fc.prob_under)
    row = {
        "model_version": art["model_version"],
        "model_artifact_hash": art["artifact_hash"],
        "league": "NFL",
        "event_id": "nfl-v71-2",
        "event_start_utc": "2026-11-29T18:00:00Z",
        "market_type": "total",
        "selection": "under",
        "line": 47.5,
        "model_probability": fc.prob_under,
    }
    assert validate_decision_snapshot(snap, row) == fc.prob_under


def _shrunk_artifact(parent: str, slope: float) -> dict:
    """A re-hashed v7 artifact with a real shrink: the replay must apply it, whatever s v7.1 ships."""
    art = {**_artifact(parent), "model_version": f"{parent}-test-shrink", "probability_logit_slope": slope}
    art["artifact_hash"] = compute_artifact_hash(art)
    return art


@pytest.mark.parametrize("spread_home_line", [-2.5, -3.0, 9.5])
def test_shrunk_spread_artifact_replays_the_shrunk_probability(spread_home_line: float) -> None:
    art = _shrunk_artifact("nfl-spread-structural-v7", 0.25)
    model = NFLSpreadModelV7.from_artifact(art)
    raw = NFLSpreadModelV7.from_artifact(_artifact("nfl-spread-structural-v7"))
    kwargs = {"home_pass_epa": 0.30, "away_pass_epa": -0.10, "spread_home_line": spread_home_line}
    fc = model.forecast_spread("Buffalo Bills", "Baltimore Ravens", **kwargs)
    raw_fc = raw.forecast_spread("Buffalo Bills", "Baltimore Ravens", **kwargs)
    assert abs(fc.prob_home_cover - raw_fc.prob_home_cover) > 1e-3  # the shrink really moved it
    snap = build_snapshot(
        art,
        {"home_team": "Buffalo Bills", "away_team": "Baltimore Ravens"},
        {"mean": fc.projected_margin_home, "sd": fc.effective_sd, "line": -spread_home_line},
        event_id="nfl-shrink-1",
        event_start_utc="2026-11-29T18:00:00Z",
        observed_at_utc="2026-11-29T12:00:00Z",
    )
    assert replay_snapshot(snap) == {"away": fc.prob_away_cover, "home": fc.prob_home_cover}
    row = _spread_row(art, "nfl-shrink-1", "home", spread_home_line, fc.prob_home_cover)
    assert validate_decision_snapshot(snap, row) == fc.prob_home_cover
    with pytest.raises(ValueError, match="scalar_replay_probability_mismatch"):
        validate_decision_snapshot(snap, {**row, "model_probability": raw_fc.prob_home_cover})


def test_shrunk_total_artifact_replays_the_shrunk_probability() -> None:
    art = _shrunk_artifact("nfl-total-structural-v7", 0.25)
    model = NFLTotalModelV7.from_artifact(art)
    raw = NFLTotalModelV7.from_artifact(_artifact("nfl-total-structural-v7"))
    fc = model.forecast_total("Buffalo Bills", "Baltimore Ravens", total_pass_epa=1.5, total_line=44.5)
    raw_fc = raw.forecast_total("Buffalo Bills", "Baltimore Ravens", total_pass_epa=1.5, total_line=44.5)
    assert abs(fc.prob_over - raw_fc.prob_over) > 1e-3
    snap = build_snapshot(
        art,
        {"home_team": "Buffalo Bills", "away_team": "Baltimore Ravens"},
        {"mean": fc.projected_total, "sd": fc.effective_sd, "line": 44.5},
        event_id="nfl-shrink-2",
        event_start_utc="2026-11-29T18:00:00Z",
        observed_at_utc="2026-11-29T12:00:00Z",
    )
    assert replay_snapshot(snap) == {"under": fc.prob_under, "over": fc.prob_over}
    row = {
        "model_version": art["model_version"],
        "model_artifact_hash": art["artifact_hash"],
        "league": "NFL",
        "event_id": "nfl-shrink-2",
        "event_start_utc": "2026-11-29T18:00:00Z",
        "market_type": "total",
        "selection": "over",
        "line": 44.5,
        "model_probability": fc.prob_over,
    }
    assert validate_decision_snapshot(snap, row) == fc.prob_over
    with pytest.raises(ValueError, match="scalar_replay_probability_mismatch"):
        validate_decision_snapshot(snap, {**row, "model_probability": raw_fc.prob_over})


def test_v7_snapshot_from_the_pre_slope_engine_still_replays() -> None:
    """Open v7 picks were logged with the old engine-file hash; editing the file must not orphan them."""
    art = _artifact("nfl-spread-structural-v7")
    model = NFLSpreadModelV7.from_artifact(art)
    fc = model.forecast_spread("Buffalo Bills", "Baltimore Ravens", spread_home_line=-2.5)
    snap = build_snapshot(
        art,
        {"home_team": "Buffalo Bills", "away_team": "Baltimore Ravens"},
        {"mean": fc.projected_margin_home, "sd": fc.effective_sd, "line": 2.5},
        event_id="nfl-v7-old",
        event_start_utc="2026-11-29T18:00:00Z",
        observed_at_utc="2026-11-29T12:00:00Z",
    )
    (old_hash,) = HISTORICAL_ENGINE_HASHES["nfl_spread_structural_v7_engine"]
    old = {k: v for k, v in snap.items() if k != "snapshot_hash"} | {"engine_source_sha256": old_hash}
    old["snapshot_hash"] = snapshot_hash(old)
    assert replay_snapshot(old)["home"] == fc.prob_home_cover


def test_moneyline_challenger_accepts_v71_parent_with_unchanged_win_probability() -> None:
    v7 = NFLSpreadModelV7.from_artifact(_artifact("nfl-spread-structural-v7"))
    v71 = NFLSpreadModelV7.from_artifact(_artifact("nfl-spread-structural-v7.1"))
    for case in SPREAD_CASES:
        a, b = v7.forecast_spread("H", "A", **case), v71.forecast_spread("H", "A", **case)
        assert (a.prob_home_win, a.projected_margin_home) == (b.prob_home_win, b.projected_margin_home)
    fc = v71.forecast_spread("H", "A", **SPREAD_CASES[0])
    kwargs = {
        "event_id": "1",
        "event_start_utc": "2026-11-29T18:00:00Z",
        "home_team": "H",
        "away_team": "A",
        "forecast": fc,
        "spread_home_line": -3.5,
        "input_qualified": True,
    }
    entry = moneyline_shadow_input(parent_model_version="nfl-spread-structural-v7.1", **kwargs)
    assert entry is not None and entry["prob_home_win"] == fc.prob_home_win
    assert moneyline_shadow_input(parent_model_version="nfl-spread-structural-v6", **kwargs) is None


@pytest.mark.parametrize(
    ("champion", "market", "expected"),
    [
        (
            SimpleNamespace(model_id="nfl-spread-structural-v7.1", available=True),
            "spread",
            "nfl-spread-structural-v7.1",
        ),
        (
            SimpleNamespace(model_id="nfl-total-structural-v7.1", available=True),
            "total",
            "nfl-total-structural-v7.1",
        ),
        (
            SimpleNamespace(model_id="nfl-spread-structural-v7", available=True),
            "spread",
            "nfl-spread-structural-v7",
        ),
        (
            SimpleNamespace(model_id="nfl-spread-structural-v7.1", available=False),
            "spread",
            "nfl-spread-structural-v7",
        ),
        (
            SimpleNamespace(model_id="nfl-spread-structural-v8", available=True),
            "spread",
            "nfl-spread-structural-v7",
        ),
        (None, "total", "nfl-total-structural-v7"),
    ],
)
def test_nfl_slate_serves_the_champion_it_can_build(monkeypatch, champion, market, expected) -> None:
    calls: list[tuple[str, str]] = []

    class _Registry:
        def champion(self, sport: str, mkt: str):
            calls.append((sport, mkt))
            return champion

    monkeypatch.setattr(
        cli_forecast.ProductionModelRegistry, "load", classmethod(lambda cls, root: _Registry())
    )
    assert cli_forecast._nfl_serving_model_version(market) == expected
    assert calls == [("NFL", market)]


def test_nfl_slate_falls_back_when_the_registry_will_not_load(monkeypatch) -> None:
    def _boom(cls, root):
        raise ValueError("broken registry")

    monkeypatch.setattr(cli_forecast.ProductionModelRegistry, "load", classmethod(_boom))
    assert cli_forecast._nfl_serving_model_version("spread") == "nfl-spread-structural-v7"


def _total_slate_fixture(tmp_path: Path, monkeypatch) -> dict:
    """The tests/sports/nfl/total end-to-end fixture: one KC @ BUF total quote at 46.5."""
    from model_prediction.data_sources.espn import ESPNClient

    game_date, event_start = "2026-10-25", "2026-10-25T17:00:00Z"
    odds_dir = tmp_path / "odds" / "nfl" / game_date
    odds_dir.mkdir(parents=True, exist_ok=True)
    snap_row = {
        "event_id": "99901",
        "event_slug": "nfl-kc-buf-2026-10-25",
        "event_start_utc": event_start,
        "event_title": "KC Chiefs vs. BUF Bills",
        "league": "NFL",
        "line": 46.5,
        "long": {"ask": 0.46, "bid": 0.45, "price": 0.46},
        "short": {"ask": 0.54, "bid": 0.53, "price": 0.54},
        "market_id": "m999",
        "market_slug": "tsc-nfl-kc-buf-2026-10-25-total-46pt5",
        "market_state": "MARKET_STATE_OPEN",
        "market_type": "total",
        "observed_at_utc": "2026-10-25T11:59:00Z",
        "provider": "polymarket_us",
        "reconstructed": False,
        "team": "Kansas City Chiefs",
        "timestamp_valid": True,
        "usage": "prospective_executable_bbo",
    }
    (odds_dir / "polymarket_snapshots.jsonl").write_text(json.dumps(snap_row) + "\n", encoding="utf-8")
    competitors = [
        {"homeAway": "home", "team": {"displayName": "Buffalo Bills", "name": "Bills"}},
        {"homeAway": "away", "team": {"displayName": "Kansas City Chiefs", "name": "Chiefs"}},
    ]
    scoreboard = {
        "events": [{"id": "99901", "date": event_start, "competitions": [{"competitors": competitors}]}]
    }
    monkeypatch.setattr(ESPNClient, "scoreboard", lambda self, league, date: scoreboard)

    # The worktree has no nflverse frames; a fixed matchup with a real pass edge gives a priced row.
    from model_prediction.features.nfl_efficiency import NFLEfficiencyMatchup

    matchup = NFLEfficiencyMatchup(
        home_team="Buffalo Bills",
        away_team="Kansas City Chiefs",
        home_off_epa=0.1,
        home_def_epa=0.05,
        away_off_epa=0.1,
        away_def_epa=0.05,
        home_qb_tier_adj=0.0,
        away_qb_tier_adj=0.0,
        home_prior_games=6,
        away_prior_games=6,
        home_pass_epa=0.25,
        away_pass_epa=0.20,
        home_def_pass_epa=0.10,
        away_def_pass_epa=0.10,
    )
    store = SimpleNamespace(
        source_manifest={},
        get_matchup_efficiency=lambda **kwargs: matchup,
        covers_last_game=lambda team, last: True,
    )
    monkeypatch.setattr(cli_forecast.NFLEfficiencyStore, "get_default", lambda *a, **k: store)
    return {"game_date": game_date}


def test_total_slate_serves_and_validates_the_v71_shrink_end_to_end(tmp_path, monkeypatch) -> None:
    """A real slate run under v7.1: the row logs, request.validate() replays it, and it is the shrink of v7."""
    from datetime import UTC, datetime

    from model_prediction.config import load_config

    fixture = _total_slate_fixture(tmp_path, monkeypatch)

    class _Ledger:
        def __init__(self) -> None:
            self.appended: list = []

        def append_evaluated(self, request, eligibility, now=None):
            self.appended.append((request, eligibility))

        def exposure(self, *args, **kwargs):
            return SimpleNamespace(exposure=0.0)

    class _Entities:
        def resolve(self, league, team, date):
            return SimpleNamespace(canonical_team_id=team)

    monkeypatch.setattr(cli_forecast, "_is_registered_serving_model", lambda *a, **k: True)
    served: dict[str, tuple[dict, list]] = {}
    for model_id in ("nfl-total-structural-v7", "nfl-total-structural-v7.1"):
        monkeypatch.setattr(cli_forecast, "_nfl_serving_model_version", lambda market, _m=model_id: _m)
        flat = _Ledger()
        result = cli_forecast._forecast_nfl_total_sport(
            data_root=tmp_path,
            args_date=fixture["game_date"],
            config=load_config(),
            registry=_Entities(),
            bans=SimpleNamespace(check=lambda *a: (None, False)),
            main_ledger=_Ledger(),
            flat_ledger=flat,
            observed_at=datetime(2026, 10, 25, 12, 0, tzinfo=UTC),
        )
        assert result["model_version"] == model_id
        assert not [s for s in result["skipped"] if "scalar_replay" in str(s.get("reason"))]
        assert len(flat.appended) == 1, (
            result.get("skipped"),
            result.get("unmatched"),
            result.get("market_candidates"),
        )
        served[model_id] = (result["priced_contracts"][0], flat.appended)
    v7_row, _ = served["nfl-total-structural-v7"]
    v71_row, v71_flat = served["nfl-total-structural-v7.1"]
    request = v71_flat[0][0]
    assert request.model_version == "nfl-total-structural-v7.1"
    slope = _artifact("nfl-total-structural-v7.1")["probability_logit_slope"]
    p7_over = (
        v7_row["model_probability"] if v7_row["selection"] == "over" else 1 - v7_row["model_probability"]
    )
    p71_over = (
        v71_row["model_probability"] if v71_row["selection"] == "over" else 1 - v71_row["model_probability"]
    )
    assert abs(p71_over - p7_over) > 1e-3  # the fixture really exercises the shrink
    assert p71_over == pytest.approx(_sigmoid_shrink(p7_over, slope), abs=1e-6)
    assert request.model_probability == v71_row["model_probability"]
    # no live calibrator exists for the new id, so served == engine output (post-shrink)
    assert v71_row["model_probability_raw"] == v71_row["model_probability"]
