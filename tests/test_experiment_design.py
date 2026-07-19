from model_prediction.experiment_design import ABLATION_COLUMNS, ExperimentLog, ablation_table, bootstrap_by_date


def test_ablation_table_has_the_standard_columns_in_order() -> None:
    rows = ablation_table(
        {
            "incumbent": {"brier": 0.24, "net_roi": 0.01},
            "+ feature A": {"brier": 0.22, "net_roi": 0.03, "clv": 0.01},
        }
    )
    assert len(rows) == 2
    assert list(rows[0].keys()) == list(ABLATION_COLUMNS)
    assert rows[0]["variant"] == "incumbent"
    assert rows[0]["brier"] == 0.24
    assert rows[0]["clv"] is None  # not provided, stays None rather than KeyError
    assert rows[1]["clv"] == 0.01


def test_ablation_table_preserves_insertion_order_not_sorted_by_pnl() -> None:
    rows = ablation_table({"b_worse": {"net_roi": -0.1}, "a_better": {"net_roi": 0.5}})
    assert [row["variant"] for row in rows] == ["b_worse", "a_better"]


def test_bootstrap_by_date_aggregates_same_day_rows_before_resampling() -> None:
    rows = [
        {"date": "2026-07-17", "pnl": 1.0},
        {"date": "2026-07-17", "pnl": 2.0},  # same day -> summed to 3.0
        {"date": "2026-07-18", "pnl": -1.0},
    ]
    result = bootstrap_by_date(rows, date_key="date", value_key="pnl")
    assert result["status"] == "ok"
    assert result["dates"] == 2
    assert result["point_estimate"] == (3.0 + -1.0) / 2


def test_bootstrap_by_date_empty_rows() -> None:
    result = bootstrap_by_date([], date_key="date", value_key="pnl")
    assert result["status"] == "insufficient_sample"


def test_experiment_log_records_and_reloads(tmp_path) -> None:
    path = tmp_path / "mlb_starter_quality.jsonl"
    log = ExperimentLog(path)
    assert log.trial_count() == 0

    log.record("elo_only", "mlb", {"brier": 0.25}, notes="baseline")
    log.record("elo_trend", "mlb", {"brier": 0.23})
    log.record("elo_only", "nba", {"brier": 0.20})

    assert log.trial_count() == 3
    assert log.trial_count("mlb") == 2
    assert log.trial_count("nba") == 1

    # A fresh log reading the same file sees the persisted trials.
    reloaded = ExperimentLog(path)
    assert reloaded.trial_count() == 3
    assert reloaded.trials("mlb")[0].variant_name == "elo_only"
    assert reloaded.trials("mlb")[0].notes == "baseline"


def test_experiment_log_trial_count_flags_repeated_holdout_use(tmp_path) -> None:
    log = ExperimentLog(tmp_path / "wnba.jsonl")
    log.record("v1", "wnba", {})
    assert log.trial_count("wnba") == 1  # single trial: holdout still clean
    log.record("v2", "wnba", {})
    assert log.trial_count("wnba") == 2  # second trial: holdout has been viewed again
