"""cfb-structural-v6 research challenger: additive form, point-in-time, joint ratings, fail-closed, inertness."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

import model_prediction
from model_prediction.config import PROJECT_ROOT
from model_prediction.domain import parse_utc
from model_prediction.features import cfb_features_v6 as v6
from model_prediction.models.cfb_structural_v6 import (
    CFB_V6_ARTIFACT_PATH,
    CFB_V6_MODEL_VERSION,
    CFBStructuralV6Model,
    load_cfb_structural_v6_artifact,
)
from model_prediction.production_registry import compute_artifact_hash

LAM = 0.1
W_PREV = 0.2
CUTOFF = parse_utc("2024-10-19T20:00:00Z")
TARGET_START = "2024-10-19T20:00:00Z"
TEAMS = [f"Team {c}" for c in "ABCDEF"]


def _fake_resolve(name: str) -> SimpleNamespace:
    return SimpleNamespace(canonical_name=name, tier="P4")


def _game(
    i: int, start: str, home: str, away: str, hs: int, as_: int, neutral: bool | None = False
) -> v6.V6Game:
    st = parse_utc(start)
    return v6.V6Game(f"g{i}", st, v6.season_of(st), home, away, hs, as_, neutral, None, "existing")


def _extractor(games: list[v6.V6Game], **kw) -> v6.CFBV6FeatureExtractor:
    ordered = sorted(games, key=lambda g: (g.start, g.event_id))
    resolver = v6.TeamResolver.from_games(ordered, resolve=_fake_resolve)
    return v6.CFBV6FeatureExtractor(ordered, lam=LAM, w_prev=W_PREV, resolver=resolver, **kw)


def _league(level: float = 31.0, rounds: int = 5) -> list[v6.V6Game]:
    """Weekly round robin among six teams; every pair of scores sums to 2 * level, so the league mean is `level`."""
    pairs = [(-7, 7), (4, -4), (0, 0), (-3, 3), (7, -7), (-10, 10), (3, -3), (0, 0)]
    games: list[v6.V6Game] = []
    n = len(TEAMS)
    ring = list(range(n))
    k = 0
    for rnd in range(rounds):
        day = datetime(2024, 9, 7, 18, tzinfo=UTC) + timedelta(days=7 * rnd)
        for j in range(n // 2):
            a, b = ring[j], ring[n - 1 - j]
            home, away = (TEAMS[a], TEAMS[b]) if (rnd + j) % 2 == 0 else (TEAMS[b], TEAMS[a])
            dh, da = pairs[k % len(pairs)]
            games.append(
                _game(k, day.strftime("%Y-%m-%dT%H:%MZ"), home, away, int(level) + dh, int(level) + da)
            )
            k += 1
        ring = [ring[0], ring[-1], *ring[1:-1]]
    return games


def _features(
    games: list[v6.V6Game], home: str = "Team A", away: str = "Team B", **kw
) -> v6.CFBV6MatchupFeatures:
    return _extractor(games).extract_features(
        event_id="target", game_start_utc=TARGET_START, home_team=home, away_team=away, **kw
    )


# ----------------------------------------------------------------------------- item 1: additive form
def _solution_for_two_teams() -> tuple[v6.RatingSolution, v6.RatingBook]:
    games = [_game(0, "2024-09-07T18:00Z", "Team A", "Team B", 30, 20)]
    book = v6.RatingBook(games, v6.TeamResolver.from_games(games, resolve=_fake_resolve))
    assert book.team_index == {"Team A": 0, "Team B": 1}
    sol = v6.RatingSolution(
        mu=28.0,
        h_home=0.0,
        h_away=0.0,
        off=np.array([10.0, 0.0]),  # Team A offense +10 over the league average
        dfn=np.array([0.0, 8.0]),  # Team B concedes +8 over the league average
        cur_games=v6.Counter({"Team A": 1, "Team B": 1}),
        prev_games=v6.Counter(),
        eligible_games=1,
        cutoff=CUTOFF,
    )
    return sol, book


def test_additive_form_projects_46_not_the_half_sum_37() -> None:
    sol, book = _solution_for_two_teams()
    proj = v6.project(sol, book, "Team A", "Team B", True)
    assert proj.home_points == pytest.approx(46.0)  # 28 + 10 + 8
    assert proj.away_points == pytest.approx(28.0)
    assert proj.total == pytest.approx(74.0)
    half_sum = 28.0 + (10.0 + 8.0) / 2.0
    assert half_sum == 37.0 and proj.home_points != half_sum


def test_additive_form_test_fails_when_the_half_sum_is_substituted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative control: with the old averaging form substituted the additive assertion cannot hold."""
    monkeypatch.setattr(v6, "additive_points", lambda mu, off, dfn, home: mu + (off + dfn) / 2.0 + home)
    sol, book = _solution_for_two_teams()
    proj = v6.project(sol, book, "Team A", "Team B", True)
    assert proj.home_points == pytest.approx(37.0)
    assert proj.home_points != pytest.approx(46.0)


def test_home_term_applies_only_off_neutral_sites() -> None:
    sol, book = _solution_for_two_teams()
    sol = v6.replace(sol, h_home=2.0, h_away=-2.0)
    home_game = v6.project(sol, book, "Team A", "Team B", False)
    neutral = v6.project(sol, book, "Team A", "Team B", True)
    assert home_game.home_points == pytest.approx(48.0)
    assert home_game.away_points == pytest.approx(26.0)
    assert neutral.home_points == pytest.approx(46.0)


# ----------------------------------------------------------------------------- point-in-time
def test_later_game_and_a_game_inside_the_result_buffer_change_nothing() -> None:
    games = _league()
    base = _features(games).to_dict()
    after_kickoff = _game(900, "2024-10-26T18:00Z", "Team A", "Team B", 70, 0)
    # kicked off 2 h before the decision: start + 5 h buffer is after the cutoff, so its result is not yet known
    inside_buffer = _game(901, "2024-10-19T18:00Z", "Team C", "Team D", 70, 0)
    assert _features([*games, after_kickoff]).to_dict() == base
    assert _features([*games, inside_buffer]).to_dict() == base
    assert _features([*games, after_kickoff, inside_buffer]).to_dict() == base


def test_the_same_game_before_the_cutoff_does_change_the_projection() -> None:
    games = _league()
    base = _features(games)
    settled = _game(902, "2024-10-12T18:00Z", "Team A", "Team B", 70, 0)
    moved = _features([*games, settled])
    assert moved.eligible_games == base.eligible_games + 1
    assert moved.projected_margin_home != base.projected_margin_home


def test_buffer_boundary_is_inclusive() -> None:
    games = _league()
    exactly = _game(903, "2024-10-19T15:00Z", "Team C", "Team D", 70, 0)  # start + 5 h == cutoff
    assert _features([*games, exactly]).eligible_games == _features(games).eligible_games + 1


# ----------------------------------------------------------------------------- joint ratings
def test_beating_a_strong_team_moves_a_rating_more_than_beating_a_weak_one() -> None:
    mids = [f"Mid {i}" for i in range(4)]
    base: list[v6.V6Game] = []
    n = 0
    for i, m in enumerate(mids):
        for strong, weak in (("Strong", "Weak"),):
            base.append(_game(n, f"2024-09-{7 + i:02d}T18:00Z", strong, m, 42, 10))
            n += 1
            base.append(_game(n, f"2024-09-{14 + i:02d}T18:00Z", m, weak, 38, 10))
            n += 1
    base.append(_game(n, "2024-09-28T18:00Z", "Mid 0", "Mid 1", 24, 21))
    n += 1
    base.append(_game(n, "2024-09-28T20:00Z", "Mid 2", "Mid 3", 21, 24))
    n += 1
    base.append(_game(n, "2024-09-21T18:00Z", "X", "Mid 0", 30, 20))
    n += 1

    def strength(opponent: str) -> float:
        games = [*base, _game(99, "2024-10-05T18:00Z", "X", opponent, 30, 20)]
        f = _features(games, home="X", away="Mid 1")
        return f.home_offense_rating - f.home_defense_rating

    assert strength("Strong") - strength("Weak") > 3.0


def test_no_conference_table_or_fixed_baseline_exists_in_the_feature_module() -> None:
    source = (PROJECT_ROOT / "src/model_prediction/features/cfb_features_v6.py").read_text(encoding="utf-8")
    for needle in ("conference", "49.5", "54.0", "CFB_BASELINE_TOTAL", "tier_offset", "fast_tempo"):
        assert needle not in source.replace("conference-tier", "").replace("conference or tier", ""), needle


# ----------------------------------------------------------------------------- rolling baseline
def test_mu_is_the_rolling_league_average_not_a_fixed_baseline() -> None:
    mus = {}
    for level in (24.0, 31.0, 40.0):
        mus[level] = _features(_league(level)).mu
        assert mus[level] == pytest.approx(level, abs=0.5)
    assert mus[40.0] - mus[24.0] > 15.0


# ----------------------------------------------------------------------------- raw weather and QB pass-through
def test_raw_weather_and_qb_inputs_are_recorded_verbatim_and_move_nothing() -> None:
    games = _league()
    plain = _features(games)
    blank = plain.to_dict()
    assert blank["raw_wind_mph"] is None and blank["raw_temperature_f"] is None
    assert blank["raw_precipitation_in"] is None
    assert blank["qb_starter_prob_home"] is None and blank["qb_starter_prob_away"] is None
    stormy = _features(
        games,
        wind_mph=31.5,
        temperature_f=12.0,
        precipitation_in=0.4,
        qb_starter_prob_home=0.25,
        qb_starter_prob_away=0.0,
    )
    got = stormy.to_dict()
    assert (got["raw_wind_mph"], got["raw_temperature_f"], got["raw_precipitation_in"]) == (31.5, 12.0, 0.4)
    assert (got["qb_starter_prob_home"], got["qb_starter_prob_away"]) == (0.25, 0.0)
    for name in (
        "projected_margin_home",
        "projected_total",
        "projected_home_points",
        "projected_away_points",
    ):
        assert getattr(stormy, name) == getattr(plain, name)


# ----------------------------------------------------------------------------- fail closed
def test_unrated_team_declines_with_the_reason_code() -> None:
    games = _league()
    with pytest.raises(v6.V6NoCall) as exc:
        _features(games, home="Team A", away="Ghost University")
    assert exc.value.reason == "NO_CALL_CFB_V6_UNRATED_TEAM"
    assert v6.NO_CALL_UNRATED == "NO_CALL_CFB_V6_UNRATED_TEAM"


def test_team_with_no_game_in_the_window_declines() -> None:
    games = _league()
    # Team Z's only game kicks off after the decision, another's is older than the previous season.
    games.append(_game(950, "2024-11-02T18:00Z", "Team Z", "Team A", 21, 20))
    games.append(_game(951, "2021-10-02T18:00Z", "Team Old", "Team B", 21, 20))
    for ghost in ("Team Z", "Team Old"):
        with pytest.raises(v6.V6NoCall) as exc:
            _features(games, home="Team A", away=ghost)
        assert exc.value.reason == "NO_CALL_CFB_V6_UNRATED_TEAM"


# ----------------------------------------------------------------------------- known-team gate
def _strict_resolve(name: str) -> SimpleNamespace | None:
    """Knows the league teams and one alias only; every other name is unresolvable, as in production."""
    if name == "Alias Team A":
        return SimpleNamespace(canonical_name="Team A", tier="P4")
    return _fake_resolve(name) if name in TEAMS else None


def _pooled_league() -> list[v6.V6Game]:
    """The league plus a lower-division singleton that plays one in-season game, so the season has a pool."""
    games = _league()
    games.append(_game(900, "2024-09-14T18:00Z", "Team A", "Tiny College", 38, 10))
    return games


def _strict_features(games: list[v6.V6Game], home: str, away: str) -> v6.CFBV6MatchupFeatures:
    ordered = sorted(games, key=lambda g: (g.start, g.event_id))
    resolver = v6.TeamResolver.from_games(ordered, resolve=_strict_resolve)
    extractor = v6.CFBV6FeatureExtractor(ordered, lam=LAM, w_prev=W_PREV, resolver=resolver)
    return extractor.extract_features(
        event_id="target", game_start_utc=TARGET_START, home_team=home, away_team=away
    )


def test_invented_team_declines_instead_of_pooling() -> None:
    games = _pooled_league()
    for home, away in (("Team A", "Invented Tech"), ("Invented Tech", "Team B")):
        with pytest.raises(v6.V6NoCall) as exc:
            _strict_features(games, home, away)
        assert exc.value.reason == "NO_CALL_CFB_V6_UNRATED_TEAM"
        assert "Invented Tech" in str(exc.value)


def test_real_pooled_team_from_history_still_projects() -> None:
    games = _pooled_league()
    ordered = sorted(games, key=lambda g: (g.start, g.event_id))
    book = v6.RatingBook(ordered, v6.TeamResolver.from_games(ordered, resolve=_strict_resolve))
    ident = book.identity("Tiny College", 2024)
    assert ident.pooled and not ident.resolved  # a pooled history team, not a rated one
    features = _strict_features(games, "Team A", "Tiny College")
    assert features.sample_games >= 1
    assert np.isfinite(features.projected_margin_home) and np.isfinite(features.projected_total)


def test_unseen_name_that_resolve_team_aliases_still_projects() -> None:
    features = _strict_features(_pooled_league(), "Alias Team A", "Team B")
    reference = _strict_features(_pooled_league(), "Team A", "Team B")
    assert features.projected_margin_home == reference.projected_margin_home


# ----------------------------------------------------------------------------- loader
def _jline(**kw) -> str:
    return json.dumps(kw)


def test_loader_existing_sources_win_on_score_and_backfill_fills_gaps(tmp_path: Path) -> None:
    processed = tmp_path / "games.jsonl"
    historical = tmp_path / "all.jsonl"
    backfill = tmp_path / "backfill.jsonl"
    processed.write_text(
        "\n".join(
            [
                _jline(
                    event_id="e1",
                    event_start_utc="2024-09-07T18:00:00Z",
                    home_team="Team A",
                    away_team="Team B",
                    home_score=30,
                    away_score=20,
                ),
                _jline(
                    event_id="e1b",
                    event_start_utc="2024-09-14T18:00:00Z",
                    home_team="Team A",
                    away_team="Team C",
                    home_score=10,
                    away_score=13,
                    is_neutral_site=True,
                    week=2,
                    home_team_id="11",
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    historical.write_text(
        "\n".join(
            [
                _jline(
                    event_id="e1",
                    event_start_utc="2024-09-07T18:00:00Z",
                    home_team="Team A",
                    away_team="Team B",
                    home_score=99,
                    away_score=0,
                ),
                _jline(
                    event_id="e2",
                    event_start_utc="2024-09-21T18:00:00Z",
                    home_team="Team C",
                    away_team="Team B",
                    home_score=17,
                    away_score=14,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    backfill.write_text(
        "\n".join(
            [
                _jline(
                    event_id="e1",
                    event_start_utc="2024-09-07T18:00Z",
                    home_team="Team A",
                    away_team="Team B",
                    home_score=1,
                    away_score=2,
                    neutral_site=True,
                    week=1,
                    game_class="FBS-FBS",
                ),
                _jline(
                    event_id="e3",
                    event_start_utc="2024-09-28T18:00Z",
                    home_team="Team D",
                    away_team="Team A",
                    home_score=40,
                    away_score=3,
                    neutral_site=False,
                    week=5,
                    game_class="FBS-FBS",
                ),
                "not json at all",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    games, stats = v6.load_history(processed, historical, backfill)
    by_id = {g.event_id: g for g in games}
    assert [g.event_id for g in games] == ["e1", "e1b", "e2", "e3"]
    assert (by_id["e1"].home_score, by_id["e1"].away_score) == (
        30,
        20,
    )  # processed beats historical and backfill
    assert by_id["e1"].neutral is True  # existing rows carried no flag: the backfill fills it
    assert by_id["e1b"].neutral is True and by_id["e1b"].week == 2 and by_id["e1b"].home_id == "11"
    assert by_id["e3"].source == "backfill" and by_id["e3"].neutral is False
    assert by_id["e2"].neutral is None and stats["unknown_neutral"] == 1
    assert stats["backfill_games_added"] == 1
    assert stats["backfill_score_disagreements_existing_wins"] == 1
    assert stats["backfill_neutral_filled"] == 1
    assert stats["backfill_malformed"] == 1
    assert stats["backfill_sha256"]

    with pytest.raises(ValueError, match="backfill sha256 mismatch"):
        v6.load_history(processed, historical, backfill, expected_backfill_sha256="0" * 64)
    games2, _ = v6.load_history(
        processed, historical, backfill, expected_backfill_sha256=stats["backfill_sha256"]
    )
    assert [g.event_id for g in games2] == [g.event_id for g in games]


# ----------------------------------------------------------------------------- artifact
def _artifact() -> dict:
    return json.loads(CFB_V6_ARTIFACT_PATH.read_text(encoding="utf-8"))


def _resign(payload: dict) -> dict:
    payload["artifact_hash"] = compute_artifact_hash(payload)
    return payload


def test_artifact_loads_verifies_its_hash_and_is_research_only() -> None:
    artifact = load_cfb_structural_v6_artifact()
    assert artifact["model_version"] == CFB_V6_MODEL_VERSION == "cfb-structural-v6"
    assert artifact["artifact_hash"] == compute_artifact_hash(artifact)
    assert (
        artifact["qualification"]["status"] == "research" and artifact["qualification"]["qualified"] is False
    )
    assert "gates" not in artifact
    frozen = json.loads((PROJECT_ROOT / "outputs/ncaaf/cfb_v6_frozen_fit.json").read_text(encoding="utf-8"))
    assert artifact["training"]["frozen_fit_sha256"] == frozen["params_sha256"]
    for key in ("margin_calibration", "total_calibration", "margin_sd_table", "total_sd_table"):
        assert artifact[key] == frozen["params"][key]
    assert artifact["solver"]["lambda"] == frozen["selected"]["lambda"]
    assert artifact["solver"]["w_prev"] == frozen["selected"]["w_prev"]


def test_artifact_loader_fails_closed(tmp_path: Path) -> None:
    good = _artifact()
    wrong_version = tmp_path / "wrong_version.json"
    wrong_version.write_text(
        json.dumps(_resign({**good, "model_version": "cfb-structural-v5"})), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="model_version mismatch"):
        load_cfb_structural_v6_artifact(wrong_version)

    tampered = tmp_path / "tampered.json"
    tampered.write_text(
        json.dumps({**good, "established_min_sample": 99}), encoding="utf-8"
    )  # hash not re-signed
    with pytest.raises(ValueError, match="artifact_hash mismatch"):
        load_cfb_structural_v6_artifact(tampered)

    with pytest.raises(ValueError, match="never reads"):
        CFBStructuralV6Model.from_artifact({**good, "gates": {"min_edge": 0.02}})
    with pytest.raises(ValueError, match="never produces"):
        CFBStructuralV6Model.from_artifact({**good, "features": [*good["features"], "home_elo"]})
    with pytest.raises(ValueError, match="missing slices"):
        CFBStructuralV6Model.from_artifact({**good, "margin_sd_table": {"established": 16.0}})


def test_artifact_builds_a_model_whose_probabilities_sum_to_one() -> None:
    model = CFBStructuralV6Model.from_artifact(load_cfb_structural_v6_artifact(), n_simulations=20_000)
    game = SimpleNamespace(
        event_id="v6-test",
        event_start_utc=TARGET_START,
        home_team="Team A",
        away_team="Team B",
        spread_home_line=-3.5,
        total_line=50.5,
        is_neutral_site=False,
    )
    # The artifact's solver config (ridge 0.1, previous-season weight 0.2, 5 h buffer) drives the extractor.
    history = _league()
    ordered = sorted(history, key=lambda g: (g.start, g.event_id))
    extractor = v6.CFBV6FeatureExtractor(
        ordered,
        lam=model.solver["lambda"],
        w_prev=model.solver["w_prev"],
        buffer_hours=model.solver["result_buffer_hours"],
        resolver=v6.TeamResolver.from_games(ordered, resolve=_fake_resolve),
    )
    preds = {p.market_type: p for p in model.predict_matchup(extractor, game)}
    assert set(preds) == {"moneyline", "spread", "total"}
    for market in preds.values():
        assert sum(market.probabilities.values()) == pytest.approx(1.0, abs=2e-6)
        assert market.model_version == CFB_V6_MODEL_VERSION
    basis = preds["moneyline"].feature_basis
    assert basis["raw_wind_mph"] is None and basis["effective_margin_sd"] > 0
    assert preds["moneyline"].inference_inputs is not None


# ----------------------------------------------------------------------------- inertness
_PROBE = """
import importlib, json, sys
for name in sys.argv[1].split(","):
    importlib.import_module(name)
watch = ["model_prediction.models.cfb_structural_v6", "model_prediction.features.cfb_features_v6",
         "model_prediction.models.cfb_structural_v5"]
print(json.dumps({w: (w in sys.modules) for w in watch}))
"""


def _probe(modules: str) -> dict[str, bool]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("MODEL_PREDICTION_")}
    env["MODEL_PREDICTION_SKIP_DOTENV"] = "1"
    env["PYTHONPATH"] = str(Path(model_prediction.__file__).resolve().parent.parent)
    out = subprocess.run(
        [sys.executable, "-c", _PROBE, modules],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_serving_modules_never_load_v6_in_a_clean_subprocess() -> None:
    serving = (
        "model_prediction.learned_forward,model_prediction.models.college_football,model_prediction.cli.daily"
    )
    loaded = _probe(serving)
    assert loaded["model_prediction.models.cfb_structural_v6"] is False
    assert loaded["model_prediction.features.cfb_features_v6"] is False


def test_the_inertness_probe_detects_a_loaded_module() -> None:
    """Negative control: the same probe reports v6 (and, separately, v5) when something does import it."""
    loaded = _probe("model_prediction.models.cfb_structural_v6")
    assert loaded["model_prediction.models.cfb_structural_v6"] is True
    assert loaded["model_prediction.features.cfb_features_v6"] is True
    assert (
        _probe("model_prediction.models.cfb_structural_v5")["model_prediction.models.cfb_structural_v5"]
        is True
    )


def test_no_module_under_src_imports_or_names_v6_and_no_config_routes_to_it() -> None:
    src = PROJECT_ROOT / "src" / "model_prediction"
    own = {src / "models" / "cfb_structural_v6.py", src / "features" / "cfb_features_v6.py"}
    offenders = [
        str(p.relative_to(PROJECT_ROOT))
        for p in src.rglob("*.py")
        if p not in own
        and any(name in p.read_text(encoding="utf-8") for name in ("cfb_structural_v6", "cfb_features_v6"))
    ]
    assert offenders == []
    learned_forward = (src / "learned_forward.py").read_text(encoding="utf-8")
    for name in ("cfb_structural_v6", "cfb_features_v6", "cfb-structural-v6"):
        assert name not in learned_forward
    for rel in ("config/production.yaml", "config/model.yaml"):
        text = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
        assert "cfb-structural-v6" not in text and "cfb_structural_v6" not in text, rel
        assert "cfb-structural-v6" not in json.dumps(yaml.safe_load(text)), rel


def test_v6_feature_module_imports_nothing_from_the_v5_feature_module() -> None:
    path = PROJECT_ROOT / "src/model_prediction/features/cfb_features_v6.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.append(("." * node.level) + (node.module or ""))
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
    assert not [name for name in imported if name.endswith("cfb_features") or name == "CFBGameRecord"]
    assert not [name for name in imported if "cfb_structural" in name]
