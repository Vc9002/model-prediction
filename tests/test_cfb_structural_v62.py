"""cfb-structural-v6.2 challenger: fit parity, coherent pmfs, point-in-time anchor, fallback marking, side selection."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from model_prediction.config import PROJECT_ROOT
from model_prediction.models.cfb_structural_v6 import load_cfb_structural_v6_artifact
from model_prediction.models.cfb_structural_v62 import (
    ANCHOR_MISSING,
    ANCHOR_TIMESTAMPED,
    CFB_V62_MODEL_VERSION,
    Anchor,
    CFBStructuralV62Model,
    EventQuotes,
    build_contracts,
    load_anchor_index,
    load_cfb_structural_v62_artifact,
    select_max_edge,
)
from model_prediction.production_registry import compute_artifact_hash

FIXTURE = PROJECT_ROOT / "outputs" / "ncaaf" / "cfb_v62_parity_fixture.json"
SPEC = PROJECT_ROOT / "outputs" / "ncaaf" / "cfb_v62_spec.json"
DECISION = datetime(2026, 11, 21, 15, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def model() -> CFBStructuralV62Model:
    return CFBStructuralV62Model(load_cfb_structural_v62_artifact(), load_cfb_structural_v6_artifact())


def _anchor(margin: float, total: float, observed: str = "2026-11-21T12:00:00+00:00") -> Anchor:
    return Anchor("e1", margin, total, -margin, observed, "DraftKings", "core_odds", "h")


# ----------------------------------------------------------------------------- artifact and fit parity
def test_artifact_is_hash_verified_and_built_on_frozen_v6() -> None:
    art = load_cfb_structural_v62_artifact()
    assert art["model_version"] == CFB_V62_MODEL_VERSION
    assert art["artifact_hash"] == compute_artifact_hash(art)
    assert art["base_artifact_hash"] == load_cfb_structural_v6_artifact()["artifact_hash"]
    assert (
        art["v6_frozen_params_sha256"] == "6e38b14ca486acfd836c1259e5c4fd4fb8e9b5894a06f367322b1eb0c7b13e70"
    )
    spec_sha = __import__("hashlib").sha256(SPEC.read_bytes()).hexdigest()
    assert art["training"]["spec_sha256"] == spec_sha
    spec = json.loads(SPEC.read_text(encoding="utf-8"))
    assert art["blend"]["w_margin"] == spec["blend"]["margin"]["w"]
    assert art["margin_distribution"]["key_log_multipliers"] == spec["margin"]["theta"]
    assert art["promotion_protocol_hash"] == spec["rule_sha256"]
    assert datetime.fromisoformat(art["frozen_at_utc"]) <= datetime.now(UTC)


def test_tampered_artifact_fails_closed(tmp_path: Path) -> None:
    art = load_cfb_structural_v62_artifact()
    art["blend"]["w_margin"] = 0.5
    bad = tmp_path / "a.json"
    bad.write_text(json.dumps(art), encoding="utf-8")
    with pytest.raises(ValueError, match="artifact_hash mismatch"):
        load_cfb_structural_v62_artifact(bad)
    art["artifact_hash"] = compute_artifact_hash(art)
    art["base_artifact_hash"] = "0" * 64
    art["artifact_hash"] = compute_artifact_hash(art)
    with pytest.raises(ValueError, match="different frozen v6"):
        CFBStructuralV62Model(art, load_cfb_structural_v6_artifact())


def test_module_reproduces_the_fit_script_parity_fixture(model: CFBStructuralV62Model) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload["spec_sha256"] == load_cfb_structural_v62_artifact()["training"]["spec_sha256"]
    assert len(payload["cases"]) >= 10
    for case in payload["cases"]:
        i, e = case["inputs"], case["expected"]
        anchor = None if i["anchor_margin"] is None else _anchor(i["anchor_margin"], i["anchor_total"])
        priced = model.price(
            v6_margin=i["v6_margin"],
            v6_total=i["v6_total"],
            v6_slice=i["slice"],
            anchor=anchor,
            spread_home_line=i["spread_home_line"],
            total_line=i["total_line"],
        )
        assert priced.anchor_status == (ANCHOR_MISSING if anchor is None else ANCHOR_TIMESTAMPED)
        assert priced.mu_margin == pytest.approx(e["mu_margin"], abs=1e-12)
        assert priced.mu_total == pytest.approx(e["mu_total"], abs=1e-12)
        assert priced.margin_sd == pytest.approx(e["margin_sd"], abs=1e-10)
        assert priced.total_sd == pytest.approx(e["total_sd"], abs=1e-12)
        for key in (
            "home_win",
            "away_win",
            "home_cover",
            "away_cover",
            "push_spread",
            "over",
            "under",
            "push_total",
        ):
            assert priced.probabilities[key] == pytest.approx(e[key], abs=1e-9), (case, key)
        pm = model.margin_pmf(priced.mu_margin, priced.margin_sd)
        assert pm[model.margin_grid == 3][0] == pytest.approx(e["pmf_margin_at_3"], abs=1e-10)
        assert pm[model.margin_grid == -7][0] == pytest.approx(e["pmf_margin_at_minus_7"], abs=1e-10)


# ----------------------------------------------------------------------------- coherence
def test_pmf_has_no_ties_keeps_its_mean_and_markets_are_coherent(model: CFBStructuralV62Model) -> None:
    for mu, line in ((3.0, -3.0), (-7.0, 7.0), (13.5, -14.0), (0.4, -0.5)):
        sd = model.margin_sd(mu, 52.0)
        pm = model.margin_pmf(mu, sd)
        assert pm[model.margin_grid == 0][0] == 0.0
        assert float(pm @ model.margin_grid) == pytest.approx(mu, abs=1e-9)
        pr = model.market_probabilities(pm, model.total_pmf(52.0, 15.0), line, 52.0)
        assert pr["home_win"] + pr["away_win"] == pytest.approx(1.0, abs=1e-12)
        assert pr["home_cover"] + pr["away_cover"] + pr["push_spread"] == pytest.approx(1.0, abs=1e-12)
        assert pr["over"] + pr["under"] + pr["push_total"] == pytest.approx(1.0, abs=1e-12)
        if line == -mu and float(line).is_integer():
            assert pr["push_spread"] > 0.0
        # moneyline and cover from one pmf: at a pick'em line of -0.5 the home cover IS the home win
        if line == -0.5:
            assert pr["home_cover"] == pytest.approx(pr["home_win"], abs=1e-12)


def test_pickem_is_an_exact_coin_flip_and_sides_are_symmetric(model: CFBStructuralV62Model) -> None:
    """Neutral-site OT ties used to go 52% home (cfb_distribution.py); v6.2 has no home term anywhere."""
    pm = model.margin_pmf(0.0, model.margin_sd(0.0, 50.0))
    pr = model.market_probabilities(pm, model.total_pmf(50.0, 15.0), None, None)
    assert pr["home_win"] == pytest.approx(0.5, abs=1e-12)
    for mu in (2.5, 6.0, 17.0):
        a = model.market_probabilities(model.margin_pmf(mu, 15.0), model.total_pmf(50.0, 15.0), None, None)
        b = model.market_probabilities(model.margin_pmf(-mu, 15.0), model.total_pmf(50.0, 15.0), None, None)
        assert a["home_win"] == pytest.approx(b["away_win"], abs=1e-12)


def test_fitted_key_mass_sits_on_three_and_seven(model: CFBStructuralV62Model) -> None:
    g = {int(abs(m)): float(v) for m, v in zip(model.margin_grid, model.key_multipliers, strict=True)}
    assert g[3] > 1.5 and g[7] > 1.3
    assert g[3] > g[2] and g[3] > g[4] and g[7] > g[6] and g[7] > g[8]
    assert g[0] == 0.0


# ----------------------------------------------------------------------------- point-in-time anchor
def _record(
    eid: str,
    observed: str,
    spread: float,
    total: float,
    *,
    provider: str = "DraftKings",
    live: bool = False,
    start: str = "2026-11-21T20:00Z",
) -> str:
    return json.dumps(
        {
            "event_id": eid,
            "event_start_utc": start,
            "record_hash": f"rh-{observed}",
            "core_odds": {
                "observed_at_utc": observed,
                "providers": [
                    {
                        "provider_name": provider,
                        "is_live": live,
                        "home_spread_current": spread,
                        "home_spread_line": spread,
                        "total_current": total,
                        "over_under": total,
                    }
                ],
            },
            "scoreboard": {"observed_at_utc": observed, "line": {}},
        }
    )


def test_anchor_index_is_point_in_time_and_max_scans(tmp_path: Path) -> None:
    path = tmp_path / "espn_pregame_snapshots.jsonl"
    lines = [
        _record(
            "e1", "2026-11-21T14:00:00+00:00", -7.0, 51.0
        ),  # latest visible: written FIRST (ingest order)
        _record("e1", "2026-11-21T09:00:00+00:00", -6.5, 50.0),
        _record("e1", "2026-11-21T15:30:00+00:00", -9.0, 53.0),  # after the decision: invisible
        _record("e1", "2026-11-21T14:30:00+00:00", -3.0, 40.0, provider="Caesars"),  # not DraftKings
        _record("e1", "2026-11-21T14:45:00+00:00", -1.0, 41.0, live=True),  # live: never an anchor
        _record("e2", "2026-11-21T14:00:00+00:00", 3.0, 44.0, start="2026-11-21T13:00Z"),  # after kickoff
        "{not json",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    idx = load_anchor_index(path, DECISION)
    assert set(idx) == {"e1"}
    a = idx["e1"]
    assert (a.spread_home_line, a.margin_home, a.total) == (-7.0, 7.0, 51.0)
    assert a.observed_at_utc == "2026-11-21T14:00:00+00:00"
    assert load_anchor_index(tmp_path / "missing.jsonl", DECISION) == {}
    with pytest.raises(ValueError):
        load_anchor_index(path, DECISION.replace(tzinfo=None))  # naive decision time


# ----------------------------------------------------------------------------- contracts and side selection
def _quotes(sportsbook: str = "polymarket_us") -> EventQuotes:
    lineage = {"market_quote_observed_at_utc": "2026-11-21T14:55:00+00:00"}
    return EventQuotes(
        event_id="e1",
        event_start_utc="2026-11-21T20:00Z",
        away_team="Away U",
        home_team="Home U",
        is_neutral_site=False,
        moneyline={
            "home_ask": 0.60,
            "away_ask": 0.30,
            "slug_base": "ml-e1",
            "sportsbook": sportsbook,
            "lineage": lineage,
        },
        spread={
            "home_line": -3.5,
            "home_ask": 0.52,
            "away_ask": 0.40,
            "slug": "sp-e1",
            "sportsbook": sportsbook,
            "lineage": lineage,
        },
        total={
            "line": 51.5,
            "over_ask": 0.50,
            "under_ask": 0.42,
            "slug": "tot-e1",
            "sportsbook": sportsbook,
            "lineage": lineage,
        },
    )


def test_select_max_edge_takes_the_larger_edge_not_the_favourite() -> None:
    assert select_max_edge([("home", 0.70, 0.69), ("away", 0.30, 0.20)]) == pytest.approx(
        ("away", 0.30, 0.20, 0.10)
    )
    assert select_max_edge([]) is None


def test_contracts_are_max_edge_polymarket_only_and_carry_their_own_hash(
    model: CFBStructuralV62Model,
) -> None:
    v6m = (7.0 - model.v6_margin_calibration["intercept"]) / model.v6_margin_calibration["slope"]
    v6t = (51.0 - model.v6_total_calibration["intercept"]) / model.v6_total_calibration["slope"]
    kwargs = {
        "v6_projected_margin": v6m,
        "v6_projected_total": v6t,
        "is_fbs_vs_fcs": False,
        "sample_games": 5,
        "decision_time": DECISION,
        "markets": ("moneyline", "spread", "total"),
    }
    rows = build_contracts(model, _quotes(), anchor=_anchor(7.0, 51.0), **kwargs)
    assert rows, "a 7-point anchor against a 3.5-point Polymarket rung must find an edge somewhere"
    for r in rows:
        assert r["model_version"] == CFB_V62_MODEL_VERSION
        assert r["model_artifact_hash"] == model.artifact_hash
        assert r["edge_vs_executable_ask"] > 0
        assert r["feature_basis"]["anchor_status"] == ANCHOR_TIMESTAMPED
        assert r["feature_basis"]["anchor_age_hours"] == pytest.approx(3.0)
        assert r["observed_at_utc"] == "2026-11-21T14:55:00+00:00"
    spread = next(r for r in rows if r["market_type"] == "spread")
    pr = model.price(
        v6_margin=7.0,
        v6_total=51.0,
        v6_slice="established",
        anchor=_anchor(7.0, 51.0),
        spread_home_line=-3.5,
        total_line=51.5,
    ).probabilities
    best = max((pr["home_cover"] - 0.52, "home"), (pr["away_cover"] - 0.40, "away"))
    assert spread["selection"] == best[1]
    assert spread["line"] == (-3.5 if best[1] == "home" else 3.5)
    # ESPN consensus is never priced by the challenger.
    assert build_contracts(model, _quotes("espn_consensus"), anchor=_anchor(7.0, 51.0), **kwargs) == []
    # Only routed markets are emitted.
    only_total = build_contracts(
        model, _quotes(), anchor=_anchor(7.0, 51.0), **{**kwargs, "markets": ("total",)}
    )
    assert {r["market_type"] for r in only_total} <= {"total"}


def test_missing_anchor_falls_back_to_model_only_and_says_so(model: CFBStructuralV62Model) -> None:
    rows = build_contracts(
        model,
        _quotes(),
        v6_projected_margin=20.0,
        v6_projected_total=50.0,
        is_fbs_vs_fcs=False,
        sample_games=0,
        anchor=None,
        decision_time=DECISION,
        markets=("moneyline", "spread", "total"),
    )
    assert rows
    for r in rows:
        fb = r["feature_basis"]
        assert fb["anchor_status"] == ANCHOR_MISSING
        assert fb["anchor_observed_at_utc"] is None
        assert fb["v6_slice"] == "thin_sample"
        assert fb["effective_margin_sd"] == pytest.approx(model.v6_margin_sd_table["thin_sample"], abs=1e-4)
        assert "model-only" in r["rationale"]


# ----------------------------------------------------------------------------- slate pricing and wiring
def _history_rows() -> list[str]:
    teams = ["Home U", "Away U", "Third U", "Fourth U"]
    rows = []
    n = 0
    for week in range(1, 9):
        for i, home in enumerate(teams):
            away = teams[(i + week) % 4] if teams[(i + week) % 4] != home else teams[(i + week + 1) % 4]
            n += 1
            rows.append(
                json.dumps(
                    {
                        "event_id": f"h{n}",
                        "event_start_utc": f"2026-09-{week + 1:02d}T18:00:00Z",
                        "home_team": home,
                        "away_team": away,
                        "home_score": 20 + (n * 7) % 17,
                        "away_score": 14 + (n * 5) % 13,
                        "is_neutral_site": False,
                    }
                )
            )
    return rows


def test_price_challenger_slate_end_to_end(tmp_path: Path, model: CFBStructuralV62Model) -> None:
    from model_prediction.models.cfb_structural_v62 import price_challenger_slate

    hist = tmp_path / "processed" / "ncaaf" / "games.jsonl"
    hist.parent.mkdir(parents=True)
    hist.write_text("\n".join(_history_rows()) + "\n", encoding="utf-8")
    capture = tmp_path / "capture.jsonl"
    capture.write_text(_record("e1", "2026-11-21T14:00:00+00:00", -7.0, 51.0) + "\n", encoding="utf-8")
    q = _quotes()
    unrated = EventQuotes(**{**q.__dict__, "event_id": "e9", "home_team": "Nobody U"})
    contracts, summary = price_challenger_slate(
        tmp_path,
        DECISION,
        [q, unrated],
        markets=("moneyline", "spread", "total"),
        model=model,
        capture_file=capture,
    )
    assert summary["status"] == "ok"
    assert summary["v6_no_call"] == 1 and summary["priced_anchored"] == 1
    assert contracts and all(c["event_id"] == "e1" for c in contracts)
    assert all(c["feature_basis"]["anchor_status"] == ANCHOR_TIMESTAMPED for c in contracts)
    assert price_challenger_slate(tmp_path, DECISION, [q], markets=())[1]["status"] == "not_routed"


def test_build_cfb_slate_appends_challenger_after_an_unchanged_champion(monkeypatch, tmp_path: Path) -> None:
    from types import SimpleNamespace

    from model_prediction.models import cfb_structural_v62, college_football

    event = {
        "id": "cfb-wire-1",
        "date": "2026-11-21T20:00:00Z",
        "season": {"year": 2026, "type": 2},
        "week": {"number": 13},
        "competitions": [
            {
                "neutralSite": False,
                "competitors": [
                    {"homeAway": "home", "team": {"id": "1", "displayName": "Miami Hurricanes"}},
                    {"homeAway": "away", "team": {"id": "2", "displayName": "Duke Blue Devils"}},
                ],
                "odds": [
                    {
                        "moneyline": {
                            "home": {"close": {"odds": "-150"}},
                            "away": {"close": {"odds": "+130"}},
                        },
                        "pointSpread": {
                            "home": {"close": {"line": "-3.5", "odds": "-110"}},
                            "away": {"close": {"line": "3.5", "odds": "-110"}},
                        },
                        "total": {
                            "over": {"close": {"line": "50.5", "odds": "-110"}},
                            "under": {"close": {"line": "50.5", "odds": "-110"}},
                        },
                    }
                ],
            }
        ],
    }
    client = SimpleNamespace(scoreboard=lambda *_a, **_k: {"events": [event]})
    seen: list = []
    sentinel = {"model_version": CFB_V62_MODEL_VERSION, "event_id": "cfb-wire-1", "market_type": "total"}

    def fake(root, obs, events):
        seen.append(events)
        return [sentinel], {"status": "ok"}

    monkeypatch.setattr(cfb_structural_v62, "price_challenger_slate", fake)
    off = college_football.build_cfb_slate(tmp_path, "2026-11-21", client=client, observed_at=DECISION)
    on = college_football.build_cfb_slate(
        tmp_path, "2026-11-21", client=client, observed_at=DECISION, price_challenger=True
    )
    assert off["challenger_pricing"] == {"status": "not_requested"} and seen and len(seen) == 1
    champion_on = [c for c in on["priced_contracts"] if c.get("model_version") != CFB_V62_MODEL_VERSION]
    assert json.dumps(champion_on, sort_keys=True, default=str) == json.dumps(
        off["priced_contracts"], sort_keys=True, default=str
    )
    assert on["priced_contracts"][-1] is sentinel
    # Only ESPN consensus was available: the challenger receives no quote to price against.
    ev = seen[0][0]
    assert (ev["moneyline"], ev["spread"], ev["total"]) == (None, None, None)

    def boom(*_a, **_k):
        raise RuntimeError("challenger down")

    monkeypatch.setattr(cfb_structural_v62, "price_challenger_slate", boom)
    failed = college_football.build_cfb_slate(
        tmp_path, "2026-11-21", client=client, observed_at=DECISION, price_challenger=True
    )
    assert failed["challenger_pricing"]["status"] == "error"
    assert json.dumps(failed["priced_contracts"], sort_keys=True, default=str) == json.dumps(
        off["priced_contracts"], sort_keys=True, default=str
    )
