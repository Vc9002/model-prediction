from __future__ import annotations

import pytest

from model_prediction.rebuild.data_cli import _parser, main


def test_rebuild_data_backfill_contract():
    args = _parser().parse_args(["backfill", "--sport", "wnba", "--season", "2024", "--resume"])
    assert args.sport == "wnba"
    assert args.season == [2024]
    assert args.resume is True


def test_rebuild_data_rejects_unimplemented_sports():
    with pytest.raises(SystemExit):
        _parser().parse_args(["backfill", "--sport", "nba", "--season", "2024"])


def test_rebuild_data_exposes_only_explicit_mlb_v3_lane():
    args = _parser().parse_args(
        [
            "backfill",
            "--sport",
            "mlb",
            "--version",
            "v3",
            "--provider",
            "statcast",
            "--start",
            "2026-08-01",
            "--end",
            "2026-08-07",
            "--resume",
        ]
    )
    assert args.sport == "mlb"
    assert args.version == "v3"
    assert args.provider == "statcast"


def test_mlb_v3_audit_without_data_reports_no_data(capsys):
    main(["audit", "--sport", "mlb", "--version", "v3", "--season", "2099"])
    assert '"status": "NO_DATA"' in capsys.readouterr().out
