"""NCAAB v7 serving: parity with the research combiner, PIT, fallback, default-off (quick 261115-v7s)."""

from __future__ import annotations

import gzip
import json
import logging
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from model_prediction.features.base import GameRecord
from model_prediction.models import ncaab_v7_serving as serving
from model_prediction.models.basketball import UpcomingGame
from model_prediction.models.ncaab_spread_v6 import NCAABSpreadModelV6, ncaab_spread_v6_model
from model_prediction.models.ncaab_v7_serving import V7Margin, combine_f1

REPO = Path(__file__).resolve().parents[1]
FROZEN = REPO / "outputs" / "ncaab" / "ncaab_v7_multiseason_frozen.json"
RESEARCH_COMPONENTS = REPO / "data" / "research" / "ncaab_market" / "v7ms_components.jsonl"
needs_archive = pytest.mark.skipif(
    not serving.ARCHIVE_PATH.exists(), reason="v7 prior-seasons archive missing"
)


def _research_module() -> Any:
    # scripts/ is not a package; the serving code must not import it, but the parity tests do on purpose
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import ncaab_v7_multiseason as m
    finally:
        sys.path.remove(str(REPO / "scripts"))
    return m


def test_frozen_constants_match_frozen_json() -> None:
    cfg = json.loads(FROZEN.read_text(encoding="utf-8"))["config"]
    assert cfg["variant"] == serving.V7_VARIANT and cfg["form"] == "F1"
    assert list(serving.V7_COEF) == cfg["coef"] and list(serving.V7_SCALES) == cfg["scales"]


def test_combiner_equals_research_apply_config() -> None:
    import pandas as pd

    m = _research_module()
    cfg = json.loads(FROZEN.read_text(encoding="utf-8"))["config"]
    rng = np.random.default_rng(3)
    n = 400
    df = pd.DataFrame(
        {
            cfg["variant"]: rng.normal(0, 12, n),
            "v6_mean": rng.normal(0, 14, n),
            "sample": rng.integers(0, 20, n),
            "d1_home": True,
            "d1_away": True,
            "v6_total": np.nan,
        }
    )
    ref = m.apply_config(df, cfg)
    for i in range(n):
        mean, scale = combine_f1(float(df[cfg["variant"]][i]), float(df["v6_mean"][i]), int(df["sample"][i]))
        assert mean == pytest.approx(ref["v7_mean"][i], abs=1e-9)
        assert scale == pytest.approx(ref["v7_scale"][i], abs=1e-9)


@needs_archive
@pytest.mark.skipif(not RESEARCH_COMPONENTS.exists(), reason="untracked research component cache missing")
def test_serving_equals_research_output_on_2024_25_days() -> None:
    import pandas as pd

    m = _research_module()
    cfg = json.loads(FROZEN.read_text(encoding="utf-8"))["config"]
    df = pd.read_json(RESEARCH_COMPONENTS, lines=True, dtype={"event_id": str})
    df["event_id"] = df["event_id"].astype(str)
    srv = serving.NCAABV7Serving()
    games = {g.event_id: g for g in srv.archive.games}
    in_season = df[df["season_year"] == 2025]
    for day in ("2024-11-09", "2025-01-29"):
        sub = in_season[in_season["day"] == day]
        assert len(sub) > 10
        ups = [
            UpcomingGame(
                e,
                games[e].event_start_utc,
                games[e].away_team,
                games[e].home_team,
                league="NCAAB",
                neutral_site=bool(nt),
            )
            for e, nt in zip(sub["event_id"], sub["neutral"], strict=True)
        ]
        # live history is empty: the whole PIT history comes from the archive (same-day games excluded)
        out = srv.margins([], ups)
        ref = m.apply_config(sub, cfg)
        d1 = (m.is_d1(sub) & sub[cfg["variant"]].notna()).to_numpy()
        checked = 0
        for k, eid in enumerate(sub["event_id"]):
            if not d1[k]:
                assert eid not in out  # non-D1 falls back to v6, never the research non-D1 branch
                continue
            assert out[eid].mean == pytest.approx(float(ref["v7_mean"].iloc[k]), abs=1e-6)
            assert out[eid].scale == pytest.approx(float(ref["v7_scale"].iloc[k]), abs=1e-6)
            checked += 1
        assert checked > 5


@needs_archive
def test_pit_no_game_on_or_after_target_day_is_used() -> None:
    srv = serving.NCAABV7Serving()
    day_games = [g for g in srv.archive.games if g.event_start_utc.startswith("2025-01-29")]
    ups = [
        UpcomingGame(g.event_id, g.event_start_utc, g.away_team, g.home_team, league="NCAAB")
        for g in day_games[:8]
    ]
    cutoff = serving.et_midnight(ups[0].event_start_utc)
    assert all(g.start < cutoff for g in srv.merged_history([], cutoff))
    base = srv.margins([], ups)
    assert base
    # a same-day live game with an absurd score, and a later one, must not move anything
    t0 = ups[0]
    poison = [
        GameRecord(
            "p1", t0.event_start_utc, "NCAAB", t0.away_team, t0.home_team, 0, 150, "regular-season", 2025
        ),
        GameRecord(
            "p2", "2025-02-05T00:00Z", "NCAAB", t0.home_team, t0.away_team, 0, 150, "regular-season", 2025
        ),
    ]
    again = srv.margins(poison, ups)
    assert {k: (v.mean, v.scale) for k, v in base.items()} == {k: (v.mean, v.scale) for k, v in again.items()}
    # while a genuinely earlier ET day (this slate is 2025-01-28 ET) is used
    early = GameRecord(
        "p0", "2025-01-27T20:00Z", "NCAAB", t0.away_team, t0.home_team, 0, 150, "regular-season", 2025
    )
    assert srv.margins([early], ups)[t0.event_id].mean != base[t0.event_id].mean


def _history() -> list[GameRecord]:
    teams = ["Alpha", "Beta", "Gamma", "Delta"]
    games = []
    for i in range(40):
        a, h = teams[i % 4], teams[(i + 1 + i // 4) % 4]
        if a == h:
            continue
        start = f"2026-11-{1 + i // 4:02d}T20:00Z"
        games.append(
            GameRecord(f"g{i}", start, "NCAAB", a, h, 60 + i % 7, 70 + i % 5, "regular-season", 2027)
        )
    return games


def _upcoming() -> list[UpcomingGame]:
    return [
        UpcomingGame("u1", "2026-11-20T20:00Z", "Alpha", "Beta", spread_away_line=3.5, league="NCAAB"),
        UpcomingGame("u2", "2026-11-20T22:00Z", "Gamma", "Delta", spread_away_line=-2.5, league="NCAAB"),
    ]


def _summ(preds: list[Any]) -> list[tuple]:
    return [(p.event_id, p.market_type, p.inference_inputs, p.probabilities) for p in preds]


def _run(model: NCAABSpreadModelV6) -> list[tuple]:
    return _summ(model.predict_games(_history(), _upcoming()))


def test_default_off_is_bit_for_bit_v6() -> None:
    off = _run(ncaab_spread_v6_model())
    explicit = ncaab_spread_v6_model()
    explicit.margin_engine = "v6"
    assert _run(explicit) == off
    assert ncaab_spread_v6_model().margin_engine == "v6"


class _FakeServing:
    def margins(self, history: Any, upcoming: Any) -> dict[str, V7Margin]:
        return {"u1": V7Margin(mean=4.25, scale=10.5, ridge=4.0, v6_asof_mean=3.0, sample=5)}


def test_v7_overrides_only_the_returned_games() -> None:
    base = _run(ncaab_spread_v6_model())
    m = ncaab_spread_v6_model()
    m.margin_engine = "v7"
    m.v7_serving = _FakeServing()
    new = m.predict_games(_history(), _upcoming())
    got = {(p.event_id, p.market_type): p for p in new}
    assert got[("u1", "spread")].inference_inputs["mean"] == 4.25
    assert got[("u1", "spread")].inference_inputs["scale"] == 10.5
    assert got[("u1", "moneyline")].feature_basis["margin_engine"] == "v7"
    assert got[("u1", "moneyline")].feature_basis["rest_diff"] == 0.0
    # u2 (not served by v7) is untouched
    assert [r for r in _summ(new) if r[0] == "u2"] == [r for r in base if r[0] == "u2"]


def test_missing_archive_falls_back_to_v6_with_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    base = _run(ncaab_spread_v6_model())
    monkeypatch.setattr(serving, "ARCHIVE_PATH", tmp_path / "missing.jsonl.gz")
    monkeypatch.setattr(serving, "_SERVING", {})
    m = ncaab_spread_v6_model()
    m.margin_engine = "v7"
    with caplog.at_level(logging.WARNING):
        assert _run(m) == base
    assert "v7 margin engine failed" in caplog.text


def test_unknown_engine_value_serves_v6(caplog: pytest.LogCaptureFixture) -> None:
    base = _run(ncaab_spread_v6_model())
    m = ncaab_spread_v6_model()
    m.margin_engine = "v8"
    with caplog.at_level(logging.WARNING):
        assert _run(m) == base
    assert "not recognised" in caplog.text


def test_corrupt_archive_is_refused(tmp_path: Path) -> None:
    bad = tmp_path / "a.jsonl.gz"
    meta = {
        "games_sha256": "0",
        "n_games": 0,
        "max_archived_season_year": 2025,
        "conferences_by_season_year": {},
    }
    with gzip.open(bad, "wb") as fh:
        fh.write((json.dumps({"_meta": meta}) + '\n{"x":1}\n').encode())
    with pytest.raises(ValueError, match="sha256"):
        serving.load_archive(bad)


def test_v7_id_is_the_v6_artifact_with_the_v7_engine() -> None:
    from model_prediction.models.ncaab_spread_v6 import NCAAB_SPREAD_V7_VERSION, ncaab_spread_v7_model

    v6, v7 = ncaab_spread_v6_model(), ncaab_spread_v7_model()
    assert (v7.version, v7.margin_engine, v6.margin_engine) == (NCAAB_SPREAD_V7_VERSION, "v7", "v6")
    skip = {"version", "margin_engine", "model_artifact_hash", "_stats", "v7_serving"}
    assert {k: v for k, v in vars(v7).items() if k not in skip} == {
        k: v for k, v in vars(v6).items() if k not in skip
    }
    v6.margin_engine = "v7"  # exactly what served under the v6 id since 2026-11-16
    v6.v7_serving = v7.v7_serving = _FakeServing()
    a, b = v6.predict_games(_history(), _upcoming()), v7.predict_games(_history(), _upcoming())
    assert _summ(a) == _summ(b)  # served probabilities bit-identical
    assert {getattr(p, "model_version", None) for p in b} == {NCAAB_SPREAD_V7_VERSION}


def test_model_artifact_hashes() -> None:
    from model_prediction.models.ncaab_spread_v6 import ncaab_spread_v7_artifact_hash, ncaab_spread_v7_model
    from model_prediction.models.ncaab_total_v6 import ncaab_total_v6_model

    art = json.loads((REPO / "config/models/ncaab-spread-v6.json").read_text(encoding="utf-8"))
    tot = json.loads((REPO / "config/models/ncaab-total-v6.json").read_text(encoding="utf-8"))
    assert ncaab_spread_v6_model().model_artifact_hash == art["artifact_hash"]
    assert ncaab_total_v6_model().model_artifact_hash == tot["artifact_hash"]
    h7 = ncaab_spread_v7_model().model_artifact_hash
    assert len(h7) == 64 and h7 != art["artifact_hash"]
    assert h7 == ncaab_spread_v7_artifact_hash(art["artifact_hash"])


def test_v7_config_hash_tracks_the_archive(tmp_path: Path) -> None:
    assert serving.v7_config_hash() == serving.v7_config_hash()
    assert serving.v7_config_hash(tmp_path / "missing.gz") != serving.v7_config_hash()
    truncated = tmp_path / "truncated.gz"
    truncated.write_bytes(gzip.compress(b'{"meta": 1}\n')[:12])
    assert serving.v7_config_hash(truncated) == serving.v7_config_hash(tmp_path / "missing.gz")
