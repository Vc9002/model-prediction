from __future__ import annotations

import pytest

from model_prediction.rebuild.data_cli import _parser


def test_rebuild_data_backfill_contract():
    args = _parser().parse_args(["backfill", "--sport", "wnba", "--season", "2024", "--resume"])
    assert args.sport == "wnba"
    assert args.season == [2024]
    assert args.resume is True


def test_rebuild_data_rejects_unimplemented_sports():
    with pytest.raises(SystemExit):
        _parser().parse_args(["backfill", "--sport", "nba", "--season", "2024"])
