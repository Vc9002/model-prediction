"""nba-total-margin-v9.2: the v9.1 total engine served with a train-fitted probability shrink.

quick-261123-h40. Pins: v9/v7 serving unchanged (against a capture taken at the parent commit),
the shrink formula, the artifact's hash and fit provenance, the scalar replay round trip, champion
map serving, and player availability staying off for v9.2.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from nba_v92_golden_capture import SLATES, capture, run_slate, strip_volatile
from test_nba_serving_plumbing import (
    AVAILABILITY,
    CLIPPERS,
    HISTORY,
    NOW,
    SPURS,
    TIP_ESPN,
    _client,
    _event,
    _patch_slate,
    _total_row,
)

from model_prediction.cli import forecast as forecast_cli
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.nba_v91 import (
    NBA_TOTAL_V92_VERSION,
    NBAStructuralV91Engine,
    NBATotalModelV92,
    _logit_shrink,
    _validated_logit_slope,
)
from model_prediction.production_registry import compute_artifact_hash
from model_prediction.scalar_artifact_replay import (
    engine_hash,
    replay_snapshot,
    validate_decision_snapshot,
)

REPO = Path(__file__).resolve().parents[1]
GOLDEN = REPO / "tests" / "fixtures" / "nba_v92" / "serving_golden_pre_v92.json"
V92_PATH = REPO / "config" / "models" / "nba-total-margin-v9.2.json"
RESEARCH_PATH = REPO / "config" / "models" / "research" / "nba-total-margin-v9-1.json"
FIT_OUTPUT = REPO / "outputs" / "nba" / "v10_harness" / "v92_total_shrink.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _game(**overrides) -> UpcomingGame:
    fields = {
        "event_id": "401",
        "event_start_utc": TIP_ESPN,
        "away_team": CLIPPERS,
        "home_team": SPURS,
        "total_line": 197.5,
        "league": "NBA",
    }
    return UpcomingGame(**{**fields, **overrides})


# ------------------------------------------------------------------ v9 / v7 bit identity


@pytest.fixture(scope="module")
def live_capture() -> dict:
    return capture()


@pytest.mark.parametrize("model_id", [model_id for _, model_id in SLATES])
def test_v9_and_v7_serving_match_the_pre_v92_capture(live_capture, model_id):
    """Every v9/v7 NBA spread and total output, as captured at daeccd3e (the parent commit).

    v7 spread's slate already fails at snapshot build on this fixture (pre-existing, deferred);
    the failure and the engine output are both pinned.
    """
    golden = _load(GOLDEN)[model_id]
    assert strip_volatile(live_capture[model_id]) == strip_volatile(golden)


def test_v9_total_snapshot_still_replays_under_total_normal():
    """v9 keeps the basketball.py-hashed total_normal replay (built live: the stored fixture's
    raw-byte engine hash depends on the checkout's line endings)."""
    snapshot = run_slate("total", "nba-total-margin-v9")["priced_contracts"][0]["model_input_snapshot"]
    assert snapshot["artifact"]["method"] == "total_normal"
    assert snapshot["engine_source_sha256"] == engine_hash("total_normal")
    assert replay_snapshot(snapshot)["over"] == pytest.approx(0.747109, abs=1e-6)


# ------------------------------------------------------------------------- the shrink


def test_shrink_formula():
    s = 0.105
    for p in (0.05, 0.35, 0.5, 0.65, 0.749, 0.99):
        expected = 1.0 / (1.0 + math.exp(-s * math.log(p / (1.0 - p))))
        assert _logit_shrink(p, s) == pytest.approx(expected, abs=1e-15)
        # Symmetric around 50%: no side is favoured, and the side never flips.
        assert _logit_shrink(1.0 - p, s) == pytest.approx(1.0 - _logit_shrink(p, s), abs=1e-12)
        assert (_logit_shrink(p, s) - 0.5) * (p - 0.5) >= 0
    assert _logit_shrink(0.7312345678, 1.0) == 0.7312345678  # bit-exact pass-through
    assert _logit_shrink(0.8, 0.0) == 0.5
    for bad in (-0.01, 1.01, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            _validated_logit_slope(bad)
    with pytest.raises(ValueError):
        _logit_shrink(1.0, 0.5)


def test_v92_artifact_hash_and_fit_provenance():
    artifact = _load(V92_PATH)
    research = _load(RESEARCH_PATH)
    assert artifact["model_version"] == artifact["total_version"] == NBA_TOTAL_V92_VERSION
    assert artifact["artifact_hash"] == compute_artifact_hash(artifact)
    assert artifact["parent_artifact_hash"] == research["artifact_hash"] == compute_artifact_hash(research)
    assert artifact["method"] == "nba_total_structural_v92_engine"
    assert artifact["availability_adjustment"] is False
    assert artifact["pace_sd_scaling"] is False

    provenance = artifact["probability_shrink_provenance"]
    fit = _load(FIT_OUTPUT)
    assert (
        provenance["fit_output_sha256"]
        == hashlib.sha256(FIT_OUTPUT.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    )
    assert fit["reproduction"]["ok"] is True
    block = fit["slope_fit_train"]
    assert artifact["probability_logit_slope"] == block["stored_probability_logit_slope"] == 0.105
    assert round(provenance["train_mle_unclipped"], 3) == 0.105
    assert 0.0 < artifact["probability_logit_slope"] < 1.0  # the stop rule (clip at 1.0) did not fire
    assert provenance["train"][1] < "2025-11-03" <= provenance["validation"][0]
    assert provenance["validation"][1] < "2026-02-23" <= provenance["locked_holdout"][0]

    qualification = artifact["qualification"]
    assert qualification["status"] == "candidate"
    assert qualification["inherited_from"] == "nba-total-margin-v9"
    assert "NOT earned" in qualification["inherited_note"]

    # The engine is the research engine; only the slope and the served id differ.
    v92 = vars(NBATotalModelV92.from_artifact(artifact).engine)
    v91 = vars(NBAStructuralV91Engine.from_artifact(research))
    differing = {k for k in v91 if v91[k] != v92[k]}
    assert differing == {"probability_logit_slope", "total_version"}


def test_v92_wrapper_refuses_a_bad_artifact():
    artifact = _load(V92_PATH)
    with pytest.raises(ValueError, match="probability_logit_slope"):
        NBATotalModelV92.from_artifact({k: v for k, v in artifact.items() if k != "probability_logit_slope"})
    with pytest.raises(ValueError, match="pace_sd_scaling"):
        NBATotalModelV92.from_artifact({**artifact, "pace_sd_scaling": True})
    with pytest.raises(ValueError, match="not a"):
        NBATotalModelV92.from_artifact(_load(RESEARCH_PATH))


def test_v92_is_the_v91_mean_with_the_shrunk_probability():
    artifact = _load(V92_PATH)
    raw = NBAStructuralV91Engine.from_artifact(_load(RESEARCH_PATH))
    served = NBATotalModelV92.from_artifact(artifact)
    game = _game()
    raw_total = next(p for p in raw.predict_games(list(HISTORY), [game]) if p.market_type == "total")
    v92 = served.predict_totals(list(HISTORY), [game])[0]
    assert v92.model_version == NBA_TOTAL_V92_VERSION
    assert v92.inference_inputs == raw_total.inference_inputs
    inputs = raw_total.inference_inputs
    from model_prediction.models.nba_v9 import _normal_cdf

    unshrunk = 1.0 - _normal_cdf(inputs["line"], inputs["mean"], inputs["sd"])
    assert v92.probabilities["over"] == round(_logit_shrink(unshrunk, 0.105), 6)
    assert v92.probabilities["over"] + v92.probabilities["under"] == pytest.approx(1.0, abs=2e-6)
    assert v92.feature_basis["over_probability_unshrunk"] == round(unshrunk, 6)
    assert raw_total.probabilities["over"] == round(unshrunk, 6)  # the research engine is unshrunk


# --------------------------------------------------------------------- availability


def test_v92_wrapper_ignores_availability_inputs():
    served = NBATotalModelV92.from_artifact(_load(V92_PATH))
    with_avail = _game(
        availability_points_gap=AVAILABILITY["availability_points_gap"],
        availability_total_loss=AVAILABILITY["availability_total_loss"],
    )
    a = served.predict_totals(list(HISTORY), [with_avail])[0]
    b = served.predict_totals(list(HISTORY), [replace(with_avail, availability_total_loss=0.0)])[0]
    assert a.inference_inputs == b.inference_inputs
    assert a.probabilities == b.probabilities


def test_v92_slate_serves_without_availability_while_v9_keeps_it():
    v92 = run_slate("total", NBA_TOTAL_V92_VERSION)["priced_contracts"][0]
    assert v92["research_availability"]["applied_to_qualified_model"] is False
    features = v92["model_input_snapshot"]["features"]
    assert features["availability_total_loss_input"] == 0.0
    assert features["availability_points_gap_input"] == 0.0
    assert features["availability_total_component"] == 0.0

    v9 = run_slate("total", "nba-total-margin-v9")["priced_contracts"][0]
    assert v9["research_availability"]["applied_to_qualified_model"] is True
    assert v9["model_input_snapshot"]["features"]["availability_total_loss_input"] == 6.0
    # 0.15 * 6.0 = 0.9 points, plus the overtime bump the availability gap moves.
    assert v9["model_input_snapshot"]["features"]["availability_total_component"] < -0.8


# ------------------------------------------------------------------ champion map serving


def test_serving_follows_the_champion_map():
    """v9 -> promote v9.2 -> roll back to v9: the registry champion alone picks the served model."""
    for champion_id in ("nba-total-margin-v9", NBA_TOTAL_V92_VERSION, "nba-total-margin-v9"):
        result = run_slate("total", champion_id)
        artifact, _ = forecast_cli._load_exact_artifact_contract(champion_id)
        contract = result["priced_contracts"][0]
        assert result["model_version"] == contract["model_version"] == champion_id
        assert contract["model_artifact_hash"] == artifact["artifact_hash"]
        assert contract["model_input_snapshot"]["artifact_hash"] == artifact["artifact_hash"]
        assert result["model_qualified"] is True


def test_v92_contract_probability_is_shrunk_and_replays():
    contract = run_slate("total", NBA_TOTAL_V92_VERSION)["priced_contracts"][0]
    snapshot = contract["model_input_snapshot"]
    unshrunk = snapshot["features"]["over_probability_unshrunk"]
    assert unshrunk > 0.70  # the raw v9.1 price on this fixture
    side = contract["selection"]
    assert side == "over"
    # Edge, sizing, gates and the ledger all read model_probability: it is the shrunk price.
    assert contract["model_probability"] == contract["model_probability_raw"]
    assert 0.5 < contract["model_probability"] < 0.54
    assert snapshot["engine_source_sha256"] == engine_hash("nba_total_structural_v92_engine")

    replayed = replay_snapshot(snapshot)
    assert replayed["over"] == contract["model_probability"]
    row = {
        "model_version": NBA_TOTAL_V92_VERSION,
        "model_artifact_hash": contract["model_artifact_hash"],
        "league": "NBA",
        "market_type": "total",
        "selection": side,
        "event_id": contract["event_id"],
        "event_start_utc": contract["event_start_utc"],
        "line": contract["line"],
        "calibration_method": contract["calibration_method"],
        "model_probability": contract["model_probability"],
    }
    assert validate_decision_snapshot(snapshot, row) == contract["model_probability"]
    # The unshrunk price does not validate against this snapshot.
    with pytest.raises(ValueError, match="scalar_replay_probability_mismatch"):
        validate_decision_snapshot(snapshot, {**row, "model_probability": round(unshrunk, 6)})


def test_v92_replay_refuses_a_tampered_slope():
    snapshot = run_slate("total", NBA_TOTAL_V92_VERSION)["priced_contracts"][0]["model_input_snapshot"]
    tampered = json.loads(json.dumps(snapshot))
    tampered["artifact"]["probability_logit_slope"] = 1.0
    tampered["artifact"]["artifact_hash"] = compute_artifact_hash(tampered["artifact"])
    tampered["artifact_hash"] = tampered["artifact"]["artifact_hash"]
    from model_prediction.learned_replay import snapshot_hash

    tampered["snapshot_hash"] = snapshot_hash(tampered)
    # Self-consistent but a different artifact: it replays the unshrunk price, not the served one.
    assert replay_snapshot(tampered)["over"] == snapshot["features"]["over_probability_unshrunk"]
    assert replay_snapshot(snapshot)["over"] != replay_snapshot(tampered)["over"]


def test_v92_engine_hash_ignores_line_endings(tmp_path, monkeypatch):
    import model_prediction.scalar_artifact_replay as replay_module

    source = (Path(replay_module.__file__).parent / "models" / "nba_v91.py").read_bytes()
    lf = source.replace(b"\r\n", b"\n")
    crlf = lf.replace(b"\n", b"\r\n")
    hashes = []
    for body in (lf, crlf):
        (tmp_path / "models").mkdir(exist_ok=True)
        (tmp_path / "models" / "nba_v91.py").write_bytes(body)
        monkeypatch.setattr(replay_module, "__file__", str(tmp_path / "scalar_artifact_replay.py"))
        hashes.append(engine_hash("nba_total_structural_v92_engine"))
    assert hashes[0] == hashes[1] == hashlib.sha256(lf).hexdigest()


def test_v92_playoff_offset_reaches_the_slate(monkeypatch, tmp_path):
    """Season types come from the scoreboard, so B7's playoff offset applies in the postseason."""
    means = {}
    for slug in ("regular-season", "post-season"):
        event = {**_event("401", CLIPPERS, SPURS), "season": {"slug": slug}}
        _patch_slate(monkeypatch, market="total", rows=[_total_row()], events=[event])
        champion = SimpleNamespace(
            model_id=NBA_TOTAL_V92_VERSION, available=True, evidence_status="predictively_qualified"
        )
        monkeypatch.setattr(
            forecast_cli.ProductionModelRegistry,
            "load",
            lambda root, champion=champion: SimpleNamespace(champion=lambda *args: champion),
        )
        result = forecast_cli._forecast_nba_total_slate(tmp_path, "2026-11-19", _client([event]))
        means[slug] = result["priced_contracts"][0]["model_input_snapshot"]["inference_inputs"]["mean"]
    assert means["post-season"] - means["regular-season"] == pytest.approx(-12.25, abs=1e-9)


def test_v92_flat_and_main_logging_read_the_shrunk_probability(monkeypatch, tmp_path):
    """The logged request carries the shrunk price; at a 50% ask its ~2.8% edge stays off Main."""
    # Lineage present, as on a live quote, so the row is not downgraded before the edge band.
    contracts = [
        {**c, "market_lineage": {}} for c in run_slate("total", NBA_TOTAL_V92_VERSION)["priced_contracts"]
    ]
    requests: list = []
    monkeypatch.setattr(forecast_cli.PickRequest, "validate", lambda self, now: None)
    monkeypatch.setattr(
        forecast_cli, "_forecast_nba_total_slate", lambda *args: {"priced_contracts": contracts}
    )
    monkeypatch.setattr(forecast_cli, "unit_policy", lambda config: object())
    monkeypatch.setattr(forecast_cli, "_is_registered_serving_model", lambda *args: True)
    monkeypatch.setattr(
        forecast_cli,
        "evaluate_eligibility",
        lambda *args, **kwargs: SimpleNamespace(decision="CALL", qualified=True),
    )
    flat, main = SimpleNamespace(exposure=lambda *args, **kwargs: object()), object()
    monkeypatch.setattr(
        forecast_cli,
        "_append_secondary_ledger",
        lambda ledger, request, eligibility, observed_at, source: requests.append((ledger, request)),
    )
    forecast_cli._forecast_nba_total_sport(
        data_root=tmp_path,
        args_date="2026-11-19",
        config={
            "models": {"NBA": {"min_edge": 0.05}},
            "project": {"maximum_unreviewed_market_disagreement": 0.10},
        },
        registry=SimpleNamespace(resolve=lambda *args: SimpleNamespace(canonical_team_id="t")),
        bans=None,
        flat_ledger=flat,
        main_ledger=main,
    )
    assert [ledger for ledger, _ in requests] == [flat]
    assert requests[0][1].model_probability == contracts[0]["model_probability"]
    assert requests[0][1].model_version == NBA_TOTAL_V92_VERSION


def test_v92_row_through_the_real_validate_eligibility_and_ledgers(monkeypatch, tmp_path):
    """Unstubbed PickRequest.validate (replays the snapshot against the row), real eligibility and
    sizing, real Flat/Main ledgers: the stored row carries the shrunk price, and its ~2.8% edge at a
    50% ask is sized on Flat but stays off Main (5% minimum)."""
    from model_prediction.config import load_config
    from model_prediction.domain import League
    from model_prediction.entities import CanonicalTeam
    from model_prediction.main_ledgers import MultiSportPickLedger

    monkeypatch.setenv("MODEL_PREDICTION_LEDGER_MIRROR", "0")
    contracts = [
        {**c, "market_lineage": {}} for c in run_slate("total", NBA_TOTAL_V92_VERSION)["priced_contracts"]
    ]
    monkeypatch.setattr(
        forecast_cli, "_forecast_nba_total_slate", lambda *args: {"priced_contracts": contracts}
    )
    monkeypatch.setattr(forecast_cli, "_is_registered_serving_model", lambda *args: True)
    teams = {
        name: CanonicalTeam(f"nba-{i}", League.NBA, name, name, True, None, None, ())
        for i, name in enumerate((CLIPPERS, SPURS))
    }
    registry = SimpleNamespace(resolve=lambda league, team, start: teams[team])
    bans = SimpleNamespace(check=lambda league, team_id: (None, False))
    monkeypatch.setattr(forecast_cli, "utc_now", lambda: NOW)
    flat = MultiSportPickLedger(tmp_path / "flat", flat=True)
    main = MultiSportPickLedger(tmp_path / "main", flat=False)
    result = forecast_cli._forecast_nba_total_sport(
        data_root=tmp_path,
        args_date="2026-11-19",
        config=load_config(),
        registry=registry,
        bans=bans,
        flat_ledger=flat,
        main_ledger=main,
    )
    assert result["skipped"] == [], result["skipped"]
    flat_rows = flat.rows()
    assert len(flat_rows) == 1
    row = flat_rows[0]
    assert row["model_version"] == NBA_TOTAL_V92_VERSION
    assert float(row["model_probability"]) == contracts[0]["model_probability"]
    assert float(row["units"]) > 0.0
    assert main.rows() == []


def test_real_registry_serves_v92_as_the_nba_total_champion(monkeypatch, tmp_path):
    """The shipped production.yaml (v9.2 promoted 2026-11-23, quick-261123-h40): the real registry
    validates the artifact and the slate serves it. Its evidence_status is historical_only (G1 failed),
    so the slate reports it as not predictively qualified."""
    from model_prediction.production_registry import ProductionModelRegistry

    registry = ProductionModelRegistry.load(REPO)
    champion = registry.champion("NBA", "total")
    assert champion is not None and champion.available, champion and champion.load_error
    assert champion.model_id == NBA_TOTAL_V92_VERSION
    assert champion.rollback_model == "nba-total-margin-v9"
    assert champion.artifact_hash == _load(V92_PATH)["artifact_hash"]

    events = [_event("401", CLIPPERS, SPURS)]
    _patch_slate(monkeypatch, market="total", rows=[_total_row()], events=events)
    # _patch_slate stubs the registry; put the real, loaded one back.
    monkeypatch.setattr(forecast_cli.ProductionModelRegistry, "load", lambda root: registry)
    result = forecast_cli._forecast_nba_total_slate(tmp_path, "2026-11-19", _client(events))
    assert result["model_version"] == NBA_TOTAL_V92_VERSION
    assert result["model_qualified"] is False
    assert forecast_cli._is_registered_serving_model(NBA_TOTAL_V92_VERSION, "NBA", "total") is True


def test_v92_engine_hash_is_pinned() -> None:
    """Stored v9.2 snapshots validate against the LF-normalised hash of nba_v91.py. That file is still
    edited for v10 research: when it changes, move this value into HISTORICAL_ENGINE_HASHES
    ["nba_total_structural_v92_engine"] (as the NFL v7 entries were) and pin the new one here, or every
    stored v9.2 row stops replaying (review 2026-11-23)."""
    from model_prediction.scalar_artifact_replay import HISTORICAL_ENGINE_HASHES, engine_hash

    current = engine_hash("nba_total_structural_v92_engine")
    pinned = "eb6f2ee8aa8ebee3b4c11af5de0c4da7fcf20e4d9713a4ef55585a4dbfbe4f6e"
    assert current == pinned or pinned in HISTORICAL_ENGINE_HASHES.get(
        "nba_total_structural_v92_engine", set()
    )
