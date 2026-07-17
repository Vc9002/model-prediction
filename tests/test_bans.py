import yaml

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
