import yaml

from model_prediction.audit import AuditLog
from model_prediction.bans import TeamBanList
from model_prediction.domain import League


def test_add_check_list_remove_are_idempotent_and_audited(ban_list) -> None:
    entry, changed = ban_list.add(League.MLB, "nyy", "data_quality", "2026-08-01")
    assert changed and entry.canonical_team_id == "mlb-nyy"
    assert ban_list.add(League.MLB, "New York Yankees", "data_quality")[1] is False
    team, banned = ban_list.check(League.MLB, "N.Y.Y.")
    assert banned and team.canonical_name == "New York Yankees"
    listed = ban_list.list()
    assert len(listed) == 1 and listed[0].review_after == "2026-08-01"
    assert ban_list.remove(League.MLB, "NY Yankees")[1] is True
    assert ban_list.remove(League.MLB, "NYY")[1] is False
    events = ban_list.audit_log.events()
    assert [event["event_type"] for event in events] == ["ban_team_added", "ban_team_removed"]
    assert events[1]["previous_hash"] == events[0]["event_hash"]


def test_atomic_mutation_preserves_unrelated_configuration(ban_list) -> None:
    ban_list.add(League.MLB, "NYY")
    config = yaml.safe_load(ban_list.config_path.read_text(encoding="utf-8"))
    assert config["unrelated"] == {"preserve": True}
    assert not list(ban_list.config_path.parent.glob("model-*.yaml"))


def test_add_check_remove_work_for_a_registry_free_league(tmp_path, registry) -> None:
    """Real bug found 2026-08-04: LOL/soccer/tennis/KBO/NPB use name-based
    placeholder teams that EntityRegistry.resolve() can never resolve (it
    raises EntityResolutionError for them, by design -- these sports were
    deliberately never added to the registry). A prior fix added a
    registry-free fallback to check()/_entries() but referenced a class
    (`entities.PlaceholderTeam`) that has never existed in this codebase,
    so check() would have raised ImportError the instant it was actually
    called for one of these leagues. Separately, add()/remove() (_mutate)
    had the identical unguarded registry.resolve() calls at three different
    points and were never touched by that fix at all -- banning a
    registry-free team through the real API would have raised
    EntityResolutionError directly. This test exercises the full real
    add -> check -> list -> remove -> check cycle for LOL end to end."""
    config = {
        "schema_version": "2",
        "team_ban_list": {
            "enabled": True,
            "teams": {"LOL": []},
            "allowed_reasons": ["manual_governance", "data_quality"],
        },
    }
    path = tmp_path / "model.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    ban_list = TeamBanList(path, registry, AuditLog(tmp_path / "events.jsonl"))

    entry, changed = ban_list.add(League.LOL, "T1", "data_quality")
    assert changed is True
    assert entry.canonical_team_id == "T1"

    team, banned = ban_list.check(League.LOL, "T1")
    assert banned is True
    assert team.canonical_team_id == "T1"

    # A different, never-banned registry-free team must not be flagged.
    _, other_banned = ban_list.check(League.LOL, "Cloud9")
    assert other_banned is False

    assert len(ban_list.list()) == 1

    _, removed = ban_list.remove(League.LOL, "T1")
    assert removed is True
    _, still_banned = ban_list.check(League.LOL, "T1")
    assert still_banned is False
