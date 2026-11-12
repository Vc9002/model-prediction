"""NFL forecast-path admission and freshness guards (2026-11-11 fix batch)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from model_prediction.cli.forecast import _downgrade_unserved
from model_prediction.cli.forecast_nfl_support import (
    _apply_serving_calibrator,
    _main_call_eligible,
    _nfl_inputs_fresh,
    _nfl_serving_calibrator,
    _select_nfl_total_market,
)
from model_prediction.domain import RecordType


@dataclass(frozen=True)
class _Eligibility:
    decision: str
    record_type: RecordType
    reason_code: str


def test_downgraded_research_row_is_never_main_eligible() -> None:
    qualified = _Eligibility("CALL", RecordType.QUALIFIED_SHADOW_CALL, "QUALIFIED")
    assert _main_call_eligible(qualified, edge=0.06, min_edge=0.05, max_edge=0.10)
    assert not _main_call_eligible(qualified, edge=0.04, min_edge=0.05, max_edge=0.10)
    assert not _main_call_eligible(qualified, edge=0.11, min_edge=0.05, max_edge=0.10)

    downgraded = _downgrade_unserved(qualified)
    # Still a CALL so Flat keeps the diagnostic row with units ...
    assert downgraded.decision == "CALL"
    assert downgraded.record_type is RecordType.RESEARCH_OBSERVATION
    # ... but Main must refuse it even inside the edge band.
    assert not _main_call_eligible(downgraded, edge=0.06, min_edge=0.05, max_edge=0.10)


def _total_row(slug: str, long_ask: float, observed: str, *, short_ask: float | None = None) -> dict:
    row = {"market_slug": slug, "line": 44.5, "long": {"ask": long_ask}, "observed_at_utc": observed}
    if short_ask is not None:
        row["short"] = {"ask": short_ask}
    return row


def test_total_selector_uses_latest_quote_per_contract_not_the_most_balanced_stale_one() -> None:
    # Real shape from odds/nfl/2026-11-09: the 05:52Z capture read 49/51, the 18:10Z one 53/47.
    rows = [
        _total_row("tsc-nfl-buf-min-total-44pt5", 0.49, "2026-11-09T05:52:00Z", short_ask=0.51),
        _total_row("tsc-nfl-buf-min-total-44pt5", 0.53, "2026-11-09T18:10:00Z", short_ask=0.47),
    ]
    picked = _select_nfl_total_market(rows)
    assert picked is not None
    assert picked["observed_at_utc"] == "2026-11-09T18:10:00Z"
    assert picked["long"]["ask"] == 0.53


def test_total_selector_requires_a_two_sided_quote() -> None:
    # The under branch reads market["short"]["ask"]; a one-sided row used to be selected
    # and then dropped silently when the model leaned under.
    assert (
        _select_nfl_total_market([_total_row("tsc-nfl-x-total-44pt5", 0.50, "2026-11-09T18:10:00Z")]) is None
    )


def test_inputs_fresh_is_an_ordering_and_age_test_not_a_calendar_date_match() -> None:
    # nflverse published the depth chart Tue 15:32Z; the 00:30 ET (05:30Z Wed) run must still qualify.
    run = datetime(2026, 11, 11, 5, 30, tzinfo=UTC)
    common = {
        "qb_update_utc": "2026-11-10T15:32:14Z",
        "last_game_date": date(2026, 11, 8),
        "observed_at": run,
    }
    assert _nfl_inputs_fresh(**common, injury_observed_utc="2026-11-11T05:20:00Z", max_age_hours=12)
    # Injury capture 13h old -> stale.
    assert not _nfl_inputs_fresh(**common, injury_observed_utc="2026-11-10T16:00:00Z", max_age_hours=12)
    # Depth chart published BEFORE the team's last game cannot reflect post-game changes.
    assert not _nfl_inputs_fresh(
        qb_update_utc="2026-11-07T15:00:00Z",
        last_game_date=date(2026, 11, 8),
        injury_observed_utc="2026-11-11T05:20:00Z",
        observed_at=run,
        max_age_hours=12,
    )
    # Anything dated after the decision time is a PIT violation, not freshness.
    assert not _nfl_inputs_fresh(**common, injury_observed_utc="2026-11-11T06:00:00Z", max_age_hours=12)
    assert not _nfl_inputs_fresh(
        qb_update_utc=None,
        last_game_date=None,
        injury_observed_utc="2026-11-11T05:20:00Z",
        observed_at=run,
        max_age_hours=12,
    )


def _write_calibrator(root, *, method: str, sample: int, baseline: float, oof: float, params: dict) -> None:
    import json

    folder = root / "calibration" / "models"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "nfl-spread-structural-v7.spread_calibrator.json").write_text(
        json.dumps(
            {
                "sport": "nfl",
                "market": "spread",
                "model_version": "nfl-spread-structural-v7",
                "champion_method": method,
                "parameters": params,
                "sample_size": sample,
                "baseline_brier": baseline,
                "calibrated_oof_brier": oof,
                "calibrated_at_utc": "2026-11-11T00:00:00+00:00",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def test_serving_calibrator_guard(tmp_path) -> None:
    mv = "nfl-spread-structural-v7"
    assert _nfl_serving_calibrator(mv, "spread", tmp_path) is None  # no artifact
    _write_calibrator(tmp_path, method="identity", sample=500, baseline=0.25, oof=0.24, params={})
    assert _nfl_serving_calibrator(mv, "spread", tmp_path) is None  # identity champion
    platt = {"intercept": 0.0, "slope": 1.3}
    _write_calibrator(tmp_path, method="platt", sample=80, baseline=0.25, oof=0.24, params=platt)
    assert _nfl_serving_calibrator(mv, "spread", tmp_path) is None  # too few outcomes
    _write_calibrator(tmp_path, method="platt", sample=500, baseline=0.25, oof=0.2495, params=platt)
    assert _nfl_serving_calibrator(mv, "spread", tmp_path) is None  # no real OOF improvement
    _write_calibrator(tmp_path, method="platt", sample=500, baseline=0.25, oof=0.24, params=platt)
    cal = _nfl_serving_calibrator(mv, "spread", tmp_path)
    assert cal is not None and cal.transform(0.6) != 0.6

    served, method, version, art_hash = _apply_serving_calibrator(
        0.6, cal, default_method="margin_normal", model_version=mv, model_artifact_hash="abc"
    )
    assert (method, version) == ("platt", "platt-artifact-v1")
    assert art_hash == cal.metadata.artifact_hash
    assert served == cal.transform(0.6)
    raw = _apply_serving_calibrator(
        0.6, None, default_method="margin_normal", model_version=mv, model_artifact_hash="abc"
    )
    assert raw == (0.6, "margin_normal", mv, "abc")


def test_calibrated_pick_round_trips_through_scalar_replay(tmp_path, monkeypatch) -> None:
    """A served (calibrated) probability must validate: the replay recomputes the raw engine
    value, applies the calibrator it finds by file hash, and compares to 2e-6."""
    import json
    from pathlib import Path

    from model_prediction import scalar_artifact_replay as replay
    from model_prediction.models.nfl_spread_v7 import NFLSpreadModelV7

    repo = Path(__file__).resolve().parents[1]
    artifact = json.loads(
        (repo / "config" / "models" / "nfl-spread-structural-v7.json").read_text(encoding="utf-8")
    )
    data_root = tmp_path / "data"
    _write_calibrator(
        data_root,
        method="platt",
        sample=500,
        baseline=0.25,
        oof=0.24,
        params={"intercept": 0.05, "slope": 1.2},
    )
    monkeypatch.setattr(replay, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(tmp_path / "runtime"))
    cal = _nfl_serving_calibrator(artifact["model_version"], "spread", data_root)
    assert cal is not None

    model = NFLSpreadModelV7.from_artifact(artifact)
    fc = model.forecast_spread(home_team="A", away_team="B", spread_home_line=-9.5)
    served, method, _version, art_hash = _apply_serving_calibrator(
        fc.prob_home_cover,
        cal,
        default_method="margin_normal",
        model_version=model.version,
        model_artifact_hash=artifact["artifact_hash"],
    )
    payload = replay.build_snapshot(
        artifact,
        {},
        {"mean": fc.projected_margin_home, "sd": fc.effective_sd, "line": 9.5},
        event_id="e1",
        event_start_utc="2026-11-16T18:00:00Z",
        observed_at_utc="2026-11-16T12:00:00Z",
    )
    row = {
        "model_version": model.version,
        "model_artifact_hash": artifact["artifact_hash"],
        "league": "NFL",
        "market_type": "spread",
        "selection": "home",
        "event_id": "e1",
        "event_start_utc": "2026-11-16T18:00:00Z",
        "line": -9.5,
        "model_probability": round(served, 6),
        "calibration_method": method,
        "calibration_artifact_hash": art_hash,
    }
    assert abs(replay.validate_decision_snapshot(payload, row) - round(served, 6)) <= 2e-6
