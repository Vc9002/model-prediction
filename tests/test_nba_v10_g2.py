"""Forward gate G2 scorer for nba-anchor-v10 (read-only evaluation).

The ESPN record mirrors the real 2026-11-21 DraftKings capture shape (home_line negative when home
is favoured; devigged pairs in ``consensus``); the ledger row mirrors a live v10 Flat row.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from model_prediction import nba_v10_g2 as g2

TIP = datetime(2026, 11, 22, 0, 30, tzinfo=UTC)
DECISION = datetime(2026, 11, 21, 19, 21, 29, tzinfo=UTC)
AFTER_TIP = TIP + timedelta(hours=4)
SIGMA_SPREAD = 13.792


def _capture(observed: datetime, *, start: datetime = TIP, line: float = 3.5, p_low: float = 0.5):
    return g2.ConsensusCapture("401910024", "spread", observed, start, line, p_low, 1)


def _espn_record(observed: datetime, *, home_line: float = -3.5, away: float = 0.515184, total=True) -> dict:
    return {
        "event_id": "401910024",
        "event_start_utc": "2026-11-22T00:30Z",
        "observed_at_utc": observed.isoformat(),
        "minutes_to_tip": round((TIP - observed).total_seconds() / 60, 1),
        "consensus": {
            "spread": {"home_line": home_line, "away": away, "home": round(1 - away, 6), "n_books": 1},
            "total": (
                {"line": 215.5, "over": 0.50433, "under": 0.49567, "n_books": 1}
                if total
                else {"line": None, "over": None, "under": None, "n_books": 0}
            ),
        },
        "providers": [{"provider_name": "DraftKings", "is_live": False}],
    }


def _pick(event_id: str = "401910024", **overrides) -> g2.Pick:
    base = {
        "pick_id": f"p-{event_id}",
        "model": g2.V10_MODEL,
        "event_id": event_id,
        "market": "spread",
        "side": "away",
        "contract_line": 3.5,
        "ask": 0.51,
        "probability": 0.505,
        "decision_time": DECISION,
        "tip": TIP,
        "sigma": SIGMA_SPREAD,
    }
    return g2.Pick(**{**base, **overrides})


# --------------------------------------------------------------------------- T-10 benchmark
def test_benchmark_is_latest_capture_inside_the_decision_to_t10_window():
    captures = [
        _capture(DECISION - timedelta(minutes=5), p_low=0.40),  # before the decision
        _capture(TIP - timedelta(minutes=30), p_low=0.45),
        _capture(TIP - timedelta(minutes=15), p_low=0.55),  # the T-10 benchmark
        _capture(TIP - timedelta(minutes=5), p_low=0.60),  # after tip - 10 min
    ]
    best, reason = g2.select_t10_benchmark(
        list(reversed(captures)), decision_time=DECISION, tip=TIP, as_of=AFTER_TIP
    )
    assert reason is None and best is not None
    assert best.observed_at_utc == TIP - timedelta(minutes=15)


def test_benchmark_rejects_captures_after_tip_minus_10():
    captures = [_capture(TIP - timedelta(minutes=9)), _capture(TIP + timedelta(minutes=30))]
    best, reason = g2.select_t10_benchmark(captures, decision_time=DECISION, tip=TIP, as_of=AFTER_TIP)
    assert best is None and reason == "missing_benchmark_all_after_t10"
    # Exactly tip - 10 min is allowed ("at or before").
    edge = _capture(TIP - timedelta(minutes=10))
    assert g2.select_t10_benchmark([edge], decision_time=DECISION, tip=TIP, as_of=AFTER_TIP)[0] == edge


def test_benchmark_rejects_captures_at_or_before_the_decision_time():
    captures = [_capture(DECISION), _capture(DECISION - timedelta(hours=1))]
    best, reason = g2.select_t10_benchmark(
        captures, decision_time=DECISION, tip=DECISION + timedelta(minutes=30), as_of=AFTER_TIP
    )
    assert best is None and reason == "missing_benchmark_all_before_decision"


def test_benchmark_uses_the_earlier_of_row_and_capture_tip():
    # ESPN moved the tip 20 minutes earlier: a capture at row-tip - 15 is after the real T-10.
    early = TIP - timedelta(minutes=20)
    capture = _capture(TIP - timedelta(minutes=15), start=early)
    best, reason = g2.select_t10_benchmark([capture], decision_time=DECISION, tip=TIP, as_of=AFTER_TIP)
    assert best is None and reason == "missing_benchmark_all_after_t10"


def test_benchmark_is_not_final_before_tip_and_stale_captures_are_flagged():
    capture = _capture(TIP - timedelta(minutes=15))
    assert g2.select_t10_benchmark(
        [capture], decision_time=DECISION, tip=TIP, as_of=TIP - timedelta(minutes=1)
    ) == (None, "pre_tip_not_final")
    stale = _capture(TIP - timedelta(hours=5))
    assert g2.select_t10_benchmark([stale], decision_time=DECISION, tip=TIP, as_of=AFTER_TIP) == (
        None,
        "stale_benchmark",
    )


def test_consensus_is_moved_to_the_contract_line_with_the_v10_sign_convention():
    [spread, total] = g2.parse_espn_record(_espn_record(TIP - timedelta(minutes=12)))
    assert (spread.market, spread.line, spread.p_low) == ("spread", 3.5, 0.515184)  # anchor line = -home_line
    assert (total.market, total.line, total.p_low) == ("total", 215.5, 0.49567)
    same = g2.consensus_side_probability(spread, "away", 3.5, SIGMA_SPREAD)
    assert same == pytest.approx(0.515184, abs=1e-9)
    assert g2.consensus_side_probability(spread, "home", 3.5, SIGMA_SPREAD) == pytest.approx(1 - 0.515184)
    # Away covers iff the home margin < line: a higher contract line is better for the away side.
    assert g2.consensus_side_probability(spread, "away", 4.5, SIGMA_SPREAD) > same


def test_capture_loader_fails_closed_per_market(tmp_path):
    path = tmp_path / "espn_odds.jsonl"
    rows = [
        json.dumps(_espn_record(TIP - timedelta(minutes=12), total=False)),
        "{not json",
        json.dumps({**_espn_record(TIP - timedelta(minutes=2)), "observed_at_utc": "garbage"}),
    ]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    captures, diag = g2.load_consensus_captures([path, tmp_path / "missing.jsonl"])
    assert list(captures) == [("401910024", "spread")]
    assert diag["unparseable_lines"] == 1 and diag["bad_timestamps"] == 1
    assert diag["total_no_devigged_pair"] == 1


# --------------------------------------------------------------------------- statistics
def test_cluster_bootstrap_resamples_games_not_picks():
    # Game A: nine picks at 0c; game B: one pick at +10c. Resampling games, both draws land on B
    # a quarter of the time, so the upper bound is the full +10c; pick resampling would put it near 3c.
    values = [0.0] * 9 + [0.10]
    clusters = ["A"] * 9 + ["B"]
    ci = g2.cluster_mean_ci(values, clusters, draws=4000, seed=7)
    assert ci.mean == pytest.approx(0.01)
    assert ci.high == pytest.approx(0.10)
    assert ci.low == pytest.approx(0.0)
    assert (ci.n, ci.n_games) == (10, 2)


def test_cluster_bootstrap_needs_two_games_for_a_ci():
    assert g2.cluster_mean_ci([0.02, 0.03], ["A", "A"]) == g2.MeanCI(2, 1, 0.025, None, None)
    assert g2.cluster_mean_ci([], []) == g2.MeanCI(0, 0, None, None, None)


# --------------------------------------------------------------------------- verdict
ALL_PASS = {"c2": g2.PASS, "c3": g2.PASS, "c4": g2.PASS}


@pytest.mark.parametrize(
    ("n", "criteria", "verdict"),
    [
        (0, ALL_PASS, "PENDING"),
        (149, ALL_PASS, "PENDING"),
        (150, ALL_PASS, "PASS"),
        (150, {**ALL_PASS, "c2": g2.FAIL}, "FAIL"),
        (299, {**ALL_PASS, "c3": g2.FAIL}, "FAIL"),
        (300, {**ALL_PASS, "c3": g2.FAIL}, "KILL"),
        (450, {**ALL_PASS, "c4": g2.FAIL, "c2": g2.NOT_EVALUABLE}, "KILL"),
        (200, {**ALL_PASS, "c4": g2.NOT_EVALUABLE}, "PENDING"),
    ],
)
def test_verdict(n, criteria, verdict):
    assert g2.g2_verdict(n, criteria)[0] == verdict


def test_missing_benchmark_is_excluded_with_a_reason_never_scored():
    captures = {("401910024", "spread"): [_capture(TIP - timedelta(minutes=12), p_low=0.53)]}
    scored = [
        g2.score_pick(_pick(), captures, as_of=AFTER_TIP),
        g2.score_pick(_pick("401910099", tip=TIP), captures, as_of=AFTER_TIP),
    ]
    assert scored[0].clv == pytest.approx(0.53 - 0.51, abs=1e-6)
    assert scored[1].reason == "missing_benchmark_no_capture" and scored[1].clv is None
    card = g2.evaluate_population(scored, {}, draws=200)
    assert card["rows"] == 2 and card["scored"] == 1
    assert card["excluded"] == {"missing_benchmark_no_capture": 1}
    assert card["consensus_clv"]["mean"] == pytest.approx(0.02, abs=1e-6)
    assert card["verdict"] == "PENDING"


# --------------------------------------------------------------------------- loader, end to end
def _ledger(path, rows):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE ledger_records (pick_id TEXT, ledger_tier TEXT, sport TEXT, model_id TEXT, event_id TEXT,"
        " event_start_utc TEXT, market_type TEXT, selection TEXT, line REAL, model_probability REAL,"
        " status TEXT, result TEXT, created_at_utc TEXT, decision_payload_json TEXT)"
    )
    conn.executemany("INSERT INTO ledger_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def _write_g2_fixture(
    tmp_path, *, anchor_source: str = "polymarket_mid", net_edge: float = -0.019994
) -> None:
    """One settled v10 spread row (away, won) with its v9 pair, decision quote, close and ESPN captures."""
    quote_seen = "2026-11-21T19:02:01.359634Z"
    slug = "asc-nba-por-ny-2026-11-21-pos-3pt5"
    snapshot = {
        "market": "spread",
        "side": "away",
        "contract_line": 3.5,
        "sigma": SIGMA_SPREAD,
        "ask": 0.51,
        "probability": 0.505,
        "decision_time_utc": DECISION.isoformat(),
        "anchor_source": anchor_source,
        "net_edge": net_edge,
        "market_slug": slug,
        "quote_observed_at_utc": quote_seen,
        # T-B fired on the side opposite the row's: consensus home 54c vs the 50c home ask.
        "tb": {
            "evaluated": True,
            "flag": True,
            "threshold": 0.03,
            "side": "home",
            "gap": 0.04,
            "consensus_prob_at_contract": {"away": 0.46, "home": 0.54},
            "gap_by_side": {"away": -0.05, "home": 0.04},
        },
    }
    teams = {"home_team": "New York Knicks", "away_team": "Portland Trail Blazers"}
    v10_payload = {**teams, "model_input_snapshot_json": json.dumps(snapshot)}
    v9_payload = {
        **teams,
        "market_slug": slug,
        "market_quote_observed_at_utc": quote_seen,
        "rationale": f"NBA v9 spread ({slug}).",
    }
    common = ("nba", "401910024", "2026-11-22T00:30Z", "spread")
    _ledger(
        tmp_path / "ledgers.db",
        [
            ("v10", "flat", "nba", g2.V10_MODEL, *common[1:], "away", 3.5, 0.505, "settled", "win",
             "2026-11-21T19:21:29.156063Z", json.dumps(v10_payload)),
            ("v9", "flat", "nba", "nba-spread-margin-v9", *common[1:], "home", -3.5, 0.63, "settled", "loss",
             "2026-11-21T19:17:36.246548Z", json.dumps(v9_payload)),
            ("v9old", "flat", "nba", "nba-spread-margin-v9", *common[1:], "home", -3.5, 0.63, "superseded", None,
             "2026-11-21T17:17:36.246548Z", json.dumps(v9_payload)),
        ],
    )  # fmt: skip
    day = tmp_path / "odds" / "nba" / "2026-11-21"
    day.mkdir(parents=True)
    side = {"ask_size": None, "bid_size": None}
    quote = {
        "market_slug": slug,
        "market_type": "spread",
        "line": 3.5,
        "team": "Portland Trail Blazers",
        "event_start_utc": "2026-11-22T00:30:00Z",
        "market_state": "MARKET_STATE_OPEN",
        "long": {**side, "bid": 0.50, "ask": 0.51, "midpoint": 0.505, "description": "+3.50"},
        "short": {**side, "bid": 0.49, "ask": 0.50, "midpoint": 0.495, "description": "-3.50"},
    }
    close = {**quote, "long": {**quote["long"], "ask": 0.54}, "short": {**quote["short"], "ask": 0.47}}
    (day / "polymarket_snapshots.jsonl").write_text(
        json.dumps({**quote, "observed_at_utc": quote_seen})
        + "\n"
        + json.dumps({**close, "observed_at_utc": "2026-11-22T00:20:00Z"})
        + "\n",
        encoding="utf-8",
    )
    (day / "espn_odds.jsonl").write_text(
        "\n".join(
            json.dumps(_espn_record(at, home_line=-3.5, away=p))
            for at, p in (
                (TIP - timedelta(minutes=12), 0.53),  # the T-10 benchmark
                (TIP - timedelta(minutes=2), 0.60),  # after tip - 10: never the benchmark
                (DECISION - timedelta(minutes=1), 0.40),  # before the decision
            )
        )
        + "\n",
        encoding="utf-8",
    )


def test_run_g2_scores_a_settled_v10_row_and_its_v9_pair(tmp_path):
    _write_g2_fixture(tmp_path)
    report = g2.run_g2(
        tmp_path / "ledgers.db", tmp_path, {"markets": {"spread": {}}}, as_of=AFTER_TIP, draws=200
    )

    flat = report["populations"]["all_flat"]
    assert flat["scored"] == 1
    assert flat["consensus_clv"]["mean"] == pytest.approx(0.53 - 0.51, abs=1e-6)
    # v9 took home at the same contract and quote: consensus home 47c minus its 50c ask.
    assert flat["v9_paired_clv_diff"]["mean"] == pytest.approx(0.02 - (0.47 - 0.50), abs=1e-6)
    assert flat["brier"]["n"] == 1 and flat["brier"]["model"] == pytest.approx((1 - 0.505) ** 2)
    assert flat["brier"]["polymarket_mid"] == pytest.approx((1 - 0.505) ** 2)
    assert flat["polymarket_clv_not_g2"]["mean"] == pytest.approx(0.54 - 0.51, abs=1e-6)
    assert report["row_exclusions"]["v9"] == {"status_superseded": 1}
    assert report["load_diagnostics"]["rows"] == {}
    # main_min_edge unset, so the T-B rule applies, and a row anchored on the Polymarket mid is never
    # a G2 pick even when its stored T-B block is flagged.
    assert report["g2_rule"] == {"spread": "tb", "total": "tb"}
    assert report["populations"]["g2_picks"]["rows"] == 0
    assert report["populations"]["g2_picks"]["verdict"] == "PENDING"

    # T-B is graded on its own side (home), never on the row's selection (away).
    tb = report["populations"]["tb"]
    [tb_row] = report["rows"]["tb"]
    assert (tb_row["side"], tb_row["ask"], tb_row["probability"]) == ("home", 0.50, 0.54)
    assert tb_row["outcome"] == 0  # the away row won, so the home side lost
    assert tb["consensus_clv"]["mean"] == pytest.approx((1 - 0.53) - 0.50, abs=1e-6)
    assert tb["polymarket_clv_not_g2"]["mean"] == pytest.approx(0.47 - 0.50, abs=1e-6)
    assert report["load_diagnostics"]["tb"] == {"evaluated": 1, "flagged": 1, "flagged_off_row_side": 1}


def test_v9_pair_from_another_run_is_not_compared():
    captures = {("401910024", "spread"): [_capture(TIP - timedelta(minutes=12), p_low=0.53)]}
    v10 = g2.score_pick(_pick(), captures, as_of=AFTER_TIP)
    same_run = g2.score_pick(
        _pick(model="v9", side="home", decision_time=DECISION - timedelta(minutes=4)),
        captures,
        as_of=AFTER_TIP,
    )
    other_run = g2.score_pick(
        _pick(model="v9", side="home", decision_time=DECISION - timedelta(hours=2)), captures, as_of=AFTER_TIP
    )
    paired = g2.evaluate_population([v10], {("401910024", "spread"): same_run}, draws=100)
    assert paired["v9_paired_clv_diff"]["n"] == 1
    unpaired = g2.evaluate_population([v10], {("401910024", "spread"): other_run}, draws=100)
    assert unpaired["v9_paired_clv_diff"]["unpaired"] == {"v9_decision_not_same_run": 1}


ART_NULL = {"markets": {"spread": {"main_min_edge": None}, "total": {"main_min_edge": None}}}


def test_null_threshold_makes_consensus_tb_rows_the_g2_picks_graded_on_the_tb_side(tmp_path):
    _write_g2_fixture(tmp_path, anchor_source="espn_consensus_devig")
    report = g2.run_g2(tmp_path / "ledgers.db", tmp_path, ART_NULL, as_of=AFTER_TIP, draws=200)
    assert report["g2_rule"]["spread"] == "tb"
    picks = report["populations"]["g2_picks"]
    [row] = report["rows"]["g2_picks"]
    assert (row["side"], row["ask"], row["outcome"]) == ("home", 0.50, 0)
    assert picks["scored"] == 1
    assert picks["consensus_clv"]["mean"] == pytest.approx((1 - 0.53) - 0.50, abs=1e-6)


@pytest.mark.parametrize(("threshold", "picked"), [(0.01, True), (0.05, False)])
def test_set_threshold_switches_g2_to_the_net_edge_rule(tmp_path, threshold, picked):
    # The row's stored T-B block is flagged, but with a threshold set only net_edge decides.
    _write_g2_fixture(tmp_path, anchor_source="espn_consensus_devig", net_edge=0.02)
    artifact = {"markets": {"spread": {"main_min_edge": threshold}, "total": {"main_min_edge": None}}}
    report = g2.run_g2(tmp_path / "ledgers.db", tmp_path, artifact, as_of=AFTER_TIP, draws=200)
    assert report["g2_rule"] == {"spread": "net_edge", "total": "tb"}
    rows = report["rows"]["g2_picks"]
    assert [r["side"] for r in rows] == (["away"] if picked else [])  # graded on the row's own side


@pytest.mark.parametrize(
    ("anchor", "gap", "picked"),
    [
        ("espn_consensus_devig", 0.030, True),
        ("espn_consensus_devig", 0.0299, False),
        ("polymarket_mid", 0.08, False),
        (None, 0.08, False),
    ],
)
def test_tb_g2_rule_boundary_and_anchor(anchor, gap, picked):
    snap = {"anchor_source": anchor, "tb": {"gap_by_side": {"home": gap}}}
    assert g2._is_tb_g2_pick(snap, "home") is picked
