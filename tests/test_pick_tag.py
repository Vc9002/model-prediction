"""The three pick tags are hard-coded destinations; every pick carries units and edge."""

import pytest

from model_prediction.domain import PickTag, pick_tag_for


def test_exactly_three_tags():
    assert {t.value for t in PickTag} == {"research", "shadow", "qualified"}


@pytest.mark.parametrize("tier", ["main", "flat", "research", "gated_research", "MAIN"])
def test_ledger_tiers_are_research(tier):
    assert pick_tag_for(ledger_tier=tier) is PickTag.RESEARCH


def test_paper_buyer_is_shadow_and_live_buyer_is_qualified():
    assert pick_tag_for(buyer_mode="paper") is PickTag.SHADOW
    assert pick_tag_for(buyer_mode="live") is PickTag.QUALIFIED


def test_buyer_mode_wins_over_tier():
    assert pick_tag_for(ledger_tier="main", buyer_mode="live") is PickTag.QUALIFIED


@pytest.mark.parametrize("kwargs", [{}, {"ledger_tier": "bogus"}, {"buyer_mode": "dry_run"}])
def test_unknown_destination_fails_closed(kwargs):
    with pytest.raises(ValueError):
        pick_tag_for(**kwargs)
