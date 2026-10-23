import json
from datetime import UTC, datetime

import pytest

from model_prediction.esports import NeutralElo, _predict, forecast_esports_slate
from model_prediction.esports_replay import build_snapshot, replay_inputs


def forecast(
    tmp_path,
    best_of=3,
    derivative=None,
    anchor="Alpha",
    line=-1.5,
    format_client=None,
    canonical_name="Alpha",
    market_name="Alpha",
    manual_aliases=None,
    duplicate_alias=False,
):
    data = tmp_path / "data/esports/lol"
    artifacts = tmp_path / "artifacts"
    data.mkdir(parents=True)
    artifacts.mkdir()
    catalog = {"bo3:3:12": {"name": canonical_name}, "bo3:3:34": {"name": "Beta"}}
    if duplicate_alias:
        catalog["bo3:3:99"] = {"name": canonical_name}
    (data / "teams.json").write_text(json.dumps(catalog))
    if manual_aliases:
        (data.parent / "team_aliases.json").write_text(json.dumps({"lol": manual_aliases}))
    (artifacts / "lol-tiered-elo-v6.json").write_text(
        json.dumps(
            {
                "model_version": "lol-tiered-elo-v6",
                "trained_through_utc": "2026-01-01T00:00:00Z",
                "artifact_hash": "fixture",
                "k": 20,
                "ratings": {"bo3:3:12": 1700, "bo3:3:34": 1500},
                "games_played": {"bo3:3:12": 100, "bo3:3:34": 100},
            }
        )
    )
    markets = [
        {
            "market_type": "moneyline",
            "market_slug": "ml",
            "sides": [{"description": market_name}, {"description": "Beta"}],
        }
    ]
    if derivative:
        markets.append(
            {
                "market_type": derivative,
                "market_slug": "derivative",
                "line": line,
                "sides": [{"description": "-1.5"}, {"description": "+1.5"}],
            }
        )

    class Client:
        def slate(self, *args):
            return [
                {
                    "event_id": "one",
                    "event_start_utc": "2099-01-01T00:00:00Z",
                    "best_of": best_of,
                    "markets": markets,
                }
            ]

        def snapshot(self, slug):
            return {
                "observed_at_utc": "2026-10-23T00:00:00Z",
                "team": anchor,
                "long": {"description": market_name if slug == "ml" else "-1.5", "ask": 0.5},
                "short": {"description": "Beta" if slug == "ml" else "+1.5", "ask": 0.5},
            }

    return forecast_esports_slate(
        tmp_path / "data", artifacts, "lol", "2099-01-01", client=Client(), format_client=format_client
    )


@pytest.mark.parametrize("best_of", [1, 3, 5])
def test_served_format_probabilities_replay_exactly(tmp_path, best_of):
    result = forecast(tmp_path, best_of)
    contract = result["priced_contracts"][0]
    evidence = build_snapshot(contract)
    assert evidence["probabilities"]["home"] == contract["sides"][0]["model_probability"]
    # Removing unfitted format transforms must keep the established v6 book math.
    assert contract["sides"][0]["model_probability"] == pytest.approx(
        1 / (1 + 10 ** (-200 / 400 / 1.18)), abs=1e-6
    )
    assert contract["gated_research_eligible"]


@pytest.mark.parametrize(
    ("best_of", "derivative", "anchor", "line", "reason"),
    [
        (None, "spread", "Alpha", -1.5, "NO_CALL_SERIES_FORMAT_UNRESOLVED"),
        (3, "spread", "Alpha", None, "NO_CALL_EXACT_LINE_UNAVAILABLE"),
        (3, "spread", None, -1.5, "NO_CALL_SPREAD_ANCHOR_UNRESOLVED"),
        (3, "spread", "Unknown", -1.5, "NO_CALL_SPREAD_ANCHOR_UNRESOLVED"),
        (3, "total", "Alpha", 3.5, "NO_CALL_EXACT_LINE_UNAVAILABLE"),
        (5, "total", "Alpha", 2.5, "NO_CALL_EXACT_LINE_UNAVAILABLE"),
    ],
)
def test_derivatives_decline_ambiguous_contracts(tmp_path, best_of, derivative, anchor, line, reason):
    result = forecast(tmp_path, best_of, derivative, anchor, line)
    assert result["priced_count"] == 1
    assert reason in {row["reason"] for row in result["no_calls"]}


def test_unknown_format_is_diagnostic_not_actionable(tmp_path):
    [contract] = forecast(tmp_path, "garbage")["priced_contracts"]
    assert not contract["gated_research_eligible"]
    assert contract["gated_research_ineligibility_reason"] == "NO_CALL_SERIES_FORMAT_UNRESOLVED"


def row(number, start, end):
    return {
        "match_id": str(number),
        "start_utc": f"2026-01-01T{start}:00:00Z",
        "end_utc": f"2026-01-01T{end}:00:00Z" if end else None,
        "team1_id": "a",
        "team2_id": "b",
        "winner_id": "a",
    }


def test_corrected_walk_forward_waits_for_result_across_split_boundary():
    book = NeutralElo(k=20, ratings={})
    _predict(book, [row(1, "10", "13")], result_availability=True)
    predictions = _predict(
        book, [row(2, "11", "15"), row(3, "13", None), row(4, "14", None)], result_availability=True
    )
    assert [item["probability"] for item in predictions[:2]] == [0.5, 0.5]
    assert predictions[2]["probability"] > 0.5
    assert book.games_played == {"a": 1, "b": 1}


def test_unknown_result_time_never_updates_corrected_book():
    book = NeutralElo(k=20, ratings={})
    predictions = _predict(book, [row(1, "10", None), row(2, "14", None)], result_availability=True)
    assert [item["probability"] for item in predictions] == [0.5, 0.5]
    assert book.ratings == {}


def test_replay_rejects_unknown_serving_transformation():
    from model_prediction.esports_replay import capture_inputs

    inputs = capture_inputs(NeutralElo(k=20, ratings={}), ["a", "b"], datetime(2026, 1, 1, tzinfo=UTC))
    inputs["serving_math"] = "unfitted-format-scaling"
    with pytest.raises(ValueError, match="serving_math"):
        replay_inputs(inputs)


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"title": "Alpha vs Beta (Best of 3)"}, 3),
        ({"markets": [{"question": "Winner - BO5"}]}, 5),
        ({"title": "Alpha vs Beta", "markets": [{"line": 2.5}]}, None),
        ({"best_of": 3, "title": "Best of 5"}, None),
        ({"title": "Best of 7"}, None),
    ],
)
def test_format_uses_explicit_metadata_only(event, expected):
    from model_prediction.esports import _explicit_series_format

    assert _explicit_series_format(event)[0] == expected


def test_missing_provider_format_is_counted_as_acquisition_gap(tmp_path):
    result = forecast(tmp_path, None)
    assert result["format_coverage"]["unresolved_format_events"] == 1
    assert result["format_coverage"]["known_format_events"] == 0
    assert any(item.get("detail") == "provider_format_not_available" for item in result["no_calls"])
    assert result["priced_contracts"][0]["series_format_source"] == "provider_format_not_available"


@pytest.mark.parametrize(("explicit", "source_bo", "expected"), [(None, 3, 3), (5, 3, None), (3, 7, None)])
def test_forecast_binds_prospective_source_and_preserves_replay(
    tmp_path, monkeypatch, explicit, source_bo, expected
):
    clock = [datetime(2026, 10, 23, 12, tzinfo=UTC)]
    monkeypatch.setattr("model_prediction.domain.utc_now", lambda: clock[0])
    monkeypatch.setattr("model_prediction.esports_context.utc_now", lambda: clock[0])

    class FormatSource:
        def _get(self, path, query):
            from datetime import timedelta

            clock[0] += timedelta(seconds=2)
            assert query["filter[matches.discipline_id][eq]"] == 3
            return {
                "results": [
                    {
                        "id": 100,
                        "status": "upcoming",
                        "start_date": "2099-01-01T00:00:00Z",
                        "end_date": None,
                        "team1_id": 12,
                        "team2_id": 34,
                        "bo_type": source_bo,
                    }
                ]
            }

    result = forecast(tmp_path, explicit, format_client=FormatSource())
    [contract] = result["priced_contracts"]
    assert contract["best_of"] == expected
    assert contract["gated_research_eligible"] is (expected is not None)
    context = contract["series_context"]
    assert context["provenance"]["observed_at_utc"] == contract["inference_inputs"]["reference_date"]
    assert result["format_coverage"]["source_pages_captured"] == 1
    evidence = build_snapshot(contract)
    assert evidence["inference_inputs"]["series_context"] == context
    assert evidence["probabilities"]["home"] == contract["sides"][0]["model_probability"]
    if context["status"] == "matched":
        tampered = json.loads(json.dumps(contract["inference_inputs"]))
        tampered["team_resolution_methods"][0] = "fuzzy"
        with pytest.raises(ValueError, match="context_mismatch"):
            replay_inputs(tampered)
        tampered = json.loads(json.dumps(contract["inference_inputs"]))
        tampered["series_context"]["provenance"]["observed_at_utc"] = "2099-01-01T00:00:00Z"
        with pytest.raises(ValueError, match="context_mismatch"):
            replay_inputs(tampered)


@pytest.mark.parametrize("manual", [False, True])
def test_fuzzy_academy_cannot_borrow_senior_source_context(tmp_path, manual):
    class FormatSource:
        def _get(self, path, query):
            return {
                "results": [
                    {
                        "id": 100,
                        "status": "upcoming",
                        "start_date": "2099-01-01T00:00:00Z",
                        "end_date": None,
                        "team1_id": 12,
                        "team2_id": 34,
                        "bo_type": 3,
                    }
                ]
            }

    market_name = "Liquid alias" if manual else "Team Liquid Academy"
    result = forecast(
        tmp_path,
        None,
        format_client=FormatSource(),
        canonical_name="Team Liquid",
        market_name=market_name,
        manual_aliases={market_name: "Team Liquid"} if manual else None,
    )
    [contract] = result["priced_contracts"]
    if manual:
        assert contract["team_resolution_methods"] == ["manual", "exact"]
        assert contract["series_context"]["status"] == "matched"
        assert contract["gated_research_eligible"]
    else:
        assert contract["team_resolution_methods"] == ["fuzzy", "exact"]
        assert contract["series_context"]["status"] == "source_identity_unresolved"
        assert not contract["source_teams_resolved"]
        assert not contract["gated_research_eligible"]
        assert contract["best_of"] is None
    assert contract["inference_inputs"]["team_resolution_methods"] == contract["team_resolution_methods"]


def test_manual_alias_requires_unique_catalog_identity(tmp_path):
    result = forecast(
        tmp_path,
        None,
        canonical_name="Team Liquid",
        market_name="Liquid alias",
        manual_aliases={"Liquid alias": "Team Liquid"},
        duplicate_alias=True,
    )
    assert result["priced_count"] == 0
    assert result["no_calls"][0]["reason"] == "NO_CALL_ENTITY_UNRESOLVED"
