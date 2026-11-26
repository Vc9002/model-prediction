"""Daily auto-buyer report: close-time CLV, line moves, per-cohort summary, twin comparison.

Everything reads a synthetic runtime root under tmp_path; nothing reaches the exchange.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from model_prediction.portfolio import auto_buyer_report as report_mod

DATE = "2026-11-25"
SPREAD_V7 = "ncaab-spread-v7"
TOTAL_V6 = "ncaab-total-v6"
SLUG_A = "asc-cbb-aaa-bbb-2026-11-25-neg-3pt5"  # tips 16:00 ET (21:00Z)
SLUG_B = "tsc-cbb-ccc-ddd-2026-11-25-140pt5"  # tips 19:00 ET (00:00Z next day)
SLUG_C = "tsc-cbb-eee-fff-2026-11-25-150pt5"  # tips 18:00 ET (23:00Z); no quote after the buy

COHORTS = [
    {
        "model_id": SPREAD_V7,
        "sport": "ncaab",
        "market": "spread",
        "min_saved_edge": 0.05,
        "max_saved_edge_exclusive": 0.12,
        "mode": "live",
        "enabled": True,
    },
    {
        "model_id": SPREAD_V7,
        "sport": "ncaab",
        "market": "spread",
        "min_saved_edge": 0.05,
        "max_saved_edge_exclusive": 0.12,
        "mode": "paper",
        "enabled": True,
        "paper_twin": True,
    },
    {
        "model_id": TOTAL_V6,
        "sport": "ncaab",
        "market": "total",
        "min_saved_edge": 0.05,
        "max_saved_edge_exclusive": 0.12,
        "mode": "live",
        "enabled": True,
    },
]


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")


def _buy(pick_id: str, slug: str, *, model: str, market: str, **extra: Any) -> dict[str, Any]:
    return {
        "pick_id": pick_id,
        "order_id": f"O-{pick_id}",
        "model_id": model,
        "sport": "NCAAB",
        "market_type": market,
        "selection": "home" if market == "spread" else "over",
        "line": -3.5 if market == "spread" else 140.5,
        "market_slug": slug,
        "token_side": "long",
        "entry_price": 0.50,
        "shares": 20.0,
        "cost_usd": 10.0,
        "edge": 0.08,
        "status": "settled",
        "result": "win",
        "pnl_usd": 10.0,
        **extra,
    }


def _snap(
    slug: str, observed: str, ask: float, *, start: str, state: str = "MARKET_STATE_OPEN"
) -> dict[str, Any]:
    return {
        "market_slug": slug,
        "observed_at_utc": observed,
        "event_start_utc": start,
        "market_state": state,
        "line": -3.5 if slug == SLUG_A else 140.5,
        "team": "AAA" if slug == SLUG_A else None,
        "long": {"ask": ask, "bid": round(ask - 0.02, 2), "midpoint": round(ask - 0.01, 3)},
        "short": {"ask": round(1.02 - ask, 2), "bid": round(1 - ask, 2), "midpoint": round(1.01 - ask, 3)},
    }


def _ledgers_db(path: Path, picks: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE ledger_records (pick_id TEXT, ledger_tier TEXT, line REAL, edge REAL, "
        "market_probability REAL, created_at_utc TEXT, event_start_utc TEXT, decision_payload_json TEXT)"
    )
    for pick in picks:
        conn.execute(
            "INSERT INTO ledger_records VALUES (?,?,?,?,?,?,?,?)",
            (
                pick["pick_id"],
                pick.get("tier", "flat"),
                pick["line"],
                pick["edge"],
                pick["ask"],
                pick["created"],
                pick["start"],
                json.dumps(
                    {
                        "market_quote_observed_at_utc": pick["quote_at"],
                        "model_input_snapshot_json": json.dumps(
                            {"features": {"research_quote_join.market_slug": pick["slug"]}}
                        ),
                    }
                ),
            ),
        )
    conn.commit()
    conn.close()


@pytest.fixture
def runtime(tmp_path: Path) -> Path:
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "auto_buyer_state.json").write_text(json.dumps({"cohorts": COHORTS}), encoding="utf-8")
    a_start, b_start = "2026-11-25T21:00:00Z", "2026-11-26T00:00:00Z"
    _jsonl(
        root / "auto_buyer_ledger.jsonl",
        [
            _buy(
                "pa",
                SLUG_A,
                model=SPREAD_V7,
                market="spread",
                executed_at_utc="2026-11-25T20:40:00Z",
                event_start_utc=a_start,
                fee_usd=0.10,
            ),
            _buy(
                "pb",
                SLUG_B,
                model=TOTAL_V6,
                market="total",
                executed_at_utc="2026-11-25T23:40:00Z",
                event_start_utc=b_start,
                result="loss",
                pnl_usd=-10.0,
            ),
            _buy(
                "pd",
                SLUG_C,
                model=TOTAL_V6,
                market="total",
                executed_at_utc="2026-11-25T22:00:00Z",
                event_start_utc="2026-11-25T23:00:00Z",
                status="open",
                result="open",
                pnl_usd=0.0,
            ),
            # A bad timestamp fails closed for that row only.
            _buy(
                "pc",
                SLUG_A,
                model=SPREAD_V7,
                market="spread",
                executed_at_utc="",
                event_start_utc=a_start,
                status="open",
                result="open",
                pnl_usd=0.0,
            ),
        ],
    )
    _jsonl(
        root / "paper_auto_buyer" / "auto_buyer_ledger.jsonl",
        [
            _buy(
                "pa",
                SLUG_A,
                model=SPREAD_V7,
                market="spread",
                executed_at_utc="2026-11-25T20:40:30Z",
                event_start_utc=a_start,
                entry_price=0.49,
            )
        ],
    )
    _jsonl(
        root / "odds" / "ncaab" / DATE / "polymarket_snapshots.jsonl",
        [
            _snap(SLUG_A, "2026-11-25T20:30:00Z", 0.49, start=a_start),  # before the buy: never a close
            _snap(SLUG_A, "2026-11-25T20:40:04Z", 0.50, start=a_start),  # same-cycle capture
            _snap(SLUG_A, "2026-11-25T20:57:00Z", 0.55, start=a_start),  # the real close
            _snap(SLUG_A, "2026-11-25T20:58:00Z", 0.99, start=a_start, state="MARKET_STATE_HALTED"),
            _snap(SLUG_A, "2026-11-25T21:00:00Z", 0.90, start=a_start),  # at tip: in play
            _snap(SLUG_B, "2026-11-25T23:40:03Z", 0.47, start=b_start),
            _snap(SLUG_C, "2026-11-25T21:50:00Z", 0.40, start="2026-11-25T23:00:00Z"),
            # Observed exactly at the buy: not after it, so never a close.
            _snap(SLUG_C, "2026-11-25T22:00:00Z", 0.41, start="2026-11-25T23:00:00Z"),
        ],
    )
    _ledgers_db(
        root / "ledgers" / "ledgers.db",
        [
            {
                "pick_id": "pa",
                "line": -3.5,
                "edge": 0.071,
                "ask": 0.48,
                "created": "2026-11-25T20:31:00Z",
                "start": a_start,
                "quote_at": "2026-11-25T20:20:00Z",
                "slug": SLUG_A,
            },
            {
                "pick_id": "pb",
                "line": 141.5,
                "edge": 0.09,
                "ask": 0.46,
                "created": "2026-11-25T23:31:00Z",
                "start": b_start,
                "quote_at": "2026-11-25T23:20:00Z",
                "slug": "tsc-cbb-ccc-ddd-2026-11-25-141pt5",
            },
        ],
    )
    _jsonl(
        root / "logs" / "auto_buyer_skip_reasons.jsonl",
        [
            # Two cycles skip pk; the terminal (last) reason counts once.
            {
                "book": "live",
                "pick_id": "pk",
                "cohort": "c",
                "reason": "rejected_future_slate",
                "cycle_at_utc": "2026-11-25T20:10:00Z",
                "tip_date_et": DATE,
                "tip_window": "5pm_or_later_et",
            },
            {
                "book": "live",
                "pick_id": "pk",
                "cohort": "c",
                "reason": "rejected_low_edge",
                "cycle_at_utc": "2026-11-25T20:25:00Z",
                "tip_date_et": DATE,
                "tip_window": "5pm_or_later_et",
            },
            # pa was bought by live in a later cycle: an earlier skip is not a terminal skip.
            {
                "book": "live",
                "pick_id": "pa",
                "cohort": "c",
                "reason": "rejected_future_slate",
                "cycle_at_utc": "2026-11-25T20:10:00Z",
                "tip_date_et": DATE,
                "tip_window": "before_5pm_et",
            },
            {
                "book": "live",
                "pick_id": "other-day",
                "cohort": "c",
                "reason": "rejected_low_edge",
                "cycle_at_utc": "2026-11-24T20:25:00Z",
                "tip_date_et": "2026-11-24",
                "tip_window": "before_5pm_et",
            },
        ],
    )
    _jsonl(
        root / "logs" / "auto_buyer_live_fills.jsonl",
        [
            {
                "pick_id": "pa",
                "outcome": "submitted",
                "fill_path": "ioc_full",
                "requested_shares": 20.0,
                "filled_shares": 20.0,
                "tip_date_et": DATE,
                "cohort": "ncaab-spread-v7:spread:[0.05,0.12):live",
            },
            {
                "pick_id": "px",
                "outcome": "refused",
                "fill_path": None,
                "requested_shares": 10.0,
                "filled_shares": None,
                "tip_date_et": DATE,
                "cohort": "ncaab-spread-v7:spread:[0.05,0.12):live",
            },
        ],
    )
    _jsonl(
        root / "logs" / "auto_buyer_cycle_history.jsonl",
        [
            {
                "pass": "cohort_live",
                "executed_at_utc": "2026-11-25T22:00:00Z",
                "rejected_daily_loss_breaker": 3,
            },
            {
                "pass": "cohort_live",
                "executed_at_utc": "2026-11-25T22:15:00Z",
                "rejected_daily_loss_breaker": 0,
            },
        ],
    )
    return root


def _rows(report: dict[str, Any], book: str, pick_id: str) -> dict[str, Any]:
    return next(r for r in report["rows"] if r["book"] == book and r["pick_id"] == pick_id)


def test_close_is_the_last_open_quote_after_the_buy_and_before_tip(runtime):
    report = report_mod.build_auto_buyer_report(runtime, DATE)
    row = _rows(report, "live", "pa")
    assert row["close_status"] == "ok"
    assert row["close_observed_at_utc"] == "2026-11-25T20:57:00Z"
    assert row["close_price_tip"] == pytest.approx(0.55)
    assert row["close_mid_tip"] == pytest.approx(0.54)
    assert row["clv_cents"] == pytest.approx(5.0)
    assert row["clv_mid_cents"] == pytest.approx(4.0)
    assert row["close_seconds_before_tip"] == pytest.approx(180.0)
    assert row["close_seconds_after_buy"] == pytest.approx(1020.0)
    assert row["close_independent_of_buy"] is True
    assert row["tip_window"] == "before_5pm_et"
    assert row["edge_bucket"] == "5-8%"
    assert row["cohort"] == "ncaab-spread-v7:spread:[0.05,0.12):live"


def test_same_cycle_close_is_flagged_not_independent(runtime):
    row = _rows(build := report_mod.build_auto_buyer_report(runtime, DATE), "live", "pb")
    assert build["report_date"] == DATE
    assert row["close_observed_at_utc"] == "2026-11-25T23:40:03Z"
    assert row["close_independent_of_buy"] is False
    assert row["clv_cents"] == pytest.approx(-3.0)
    assert row["tip_window"] == "5pm_or_later_et"


def test_bad_buy_time_fails_closed_for_that_row_only(runtime):
    report = report_mod.build_auto_buyer_report(runtime, DATE)
    row = _rows(report, "live", "pc")
    assert row["close_status"] == "missing_buy_time"
    assert row["clv_cents"] is None


def test_paper_twin_close_uses_its_own_entry(runtime):
    report = report_mod.build_auto_buyer_report(runtime, DATE)
    row = _rows(report, "paper", "pa")
    assert row["cohort"] == "ncaab-spread-v7:spread:[0.05,0.12):paper"
    assert row["paper_twin"] is True
    assert row["clv_cents"] == pytest.approx(6.0)


def test_line_move_record(runtime):
    report = report_mod.build_auto_buyer_report(runtime, DATE)
    row = _rows(report, "live", "pb")
    assert row["pick_line"] == pytest.approx(141.5)
    assert row["pick_price"] == pytest.approx(0.46)
    assert row["pick_slug"] == "tsc-cbb-ccc-ddd-2026-11-25-141pt5"
    assert row["pick_slug_matches_bought"] is False
    assert row["buy_line"] == pytest.approx(140.5)
    assert row["buy_contract_line"] == pytest.approx(140.5)
    assert row["buy_price"] == pytest.approx(0.50)
    assert row["pick_to_buy_cents"] == pytest.approx(4.0)
    assert row["buy_to_close_cents"] == pytest.approx(-3.0)
    assert row["closing_main_line"] is None
    assert row["closing_main_line_reason"]
    assert row["saved_edge"] == pytest.approx(0.09)


def test_daily_cohort_summary(runtime):
    report = report_mod.build_auto_buyer_report(runtime, DATE)
    spread = next(c for c in report["cohorts"] if c["cohort"] == "ncaab-spread-v7:spread:[0.05,0.12):live")
    day = spread["day"]
    assert day["orders"] == 2
    assert day["wins"] == 1 and day["losses"] == 0 and day["open"] == 1
    assert day["pnl_usd"] == pytest.approx(10.0)
    assert day["fees_usd"] == pytest.approx(0.10)
    assert day["pnl_net_usd"] == pytest.approx(9.90)
    assert day["roi"] == pytest.approx(0.99)
    assert day["clv_cents_mean"] == pytest.approx(5.0)
    assert day["clv_independent_n"] == 1
    assert day["avg_saved_edge"] == pytest.approx(0.071)
    assert spread["tip_split"]["before_5pm_et"]["wins"] == 1
    assert spread["tip_split"]["5pm_or_later_et"]["orders"] == 0
    assert spread["fills"]["attempts"] == 2
    assert spread["fills"]["outcomes"] == {"submitted": 1, "refused": 1}
    total = next(c for c in report["cohorts"] if c["cohort"].startswith(TOTAL_V6))
    assert total["day"]["losses"] == 1
    assert total["cumulative"]["max_drawdown_usd"] == pytest.approx(10.0)
    assert report["loss_stop_cycles"]["live"] == 1


def test_skip_reasons_count_the_terminal_reason_of_unbought_picks(runtime):
    report = report_mod.build_auto_buyer_report(runtime, DATE)
    assert report["skip_reasons"] == {"live": {"c": {"rejected_low_edge": 1}}}


def test_twin_comparison_joins_on_pick_id(runtime):
    report = report_mod.build_auto_buyer_report(runtime, DATE)
    (twin,) = report["twin_comparison"]
    assert twin["model_id"] == SPREAD_V7
    assert twin["both"] == ["pa"]
    assert twin["live_only"] == ["pc"]
    assert twin["paper_only"] == []
    assert twin["entry_diff_cents_mean"] == pytest.approx(1.0)


def test_write_report_files_and_markdown_has_no_american_odds(runtime, tmp_path):
    report = report_mod.build_auto_buyer_report(runtime, DATE)
    out = tmp_path / "out"
    paths = report_mod.write_auto_buyer_report(report, out)
    for key in ("json", "markdown", "close_clv", "line_moves"):
        assert Path(paths[key]).exists()
    markdown = Path(paths["markdown"]).read_text(encoding="utf-8")
    assert "ncaab-spread-v7" in markdown
    assert "american" not in markdown.lower()
    assert "¢" in markdown or "c " in markdown
    clv_lines = Path(paths["close_clv"]).read_text(encoding="utf-8").splitlines()
    assert len(clv_lines) == len([r for r in report["rows"] if r["tip_date_et"] == DATE])
    assert json.loads(Path(paths["json"]).read_text(encoding="utf-8"))["report_date"] == DATE


def test_report_reads_and_never_writes_the_runtime_root(runtime):
    before = {p: p.stat().st_mtime_ns for p in runtime.rglob("*") if p.is_file()}
    report_mod.build_auto_buyer_report(runtime, DATE)
    after = {p: p.stat().st_mtime_ns for p in runtime.rglob("*") if p.is_file()}
    assert before == after


def test_empty_runtime_root_gives_an_empty_report(tmp_path):
    report = report_mod.build_auto_buyer_report(tmp_path, DATE)
    assert report["rows"] == [] and report["cohorts"] == []


def test_cli_parses_and_writes_the_report(runtime, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from model_prediction.cli.parser import parser
    from model_prediction.runtime_paths import RuntimePaths

    args = parser().parse_args(["auto-buyer-report", "--date", DATE, "--out-dir", str(tmp_path / "out")])
    monkeypatch.setattr(
        RuntimePaths,
        "resolve",
        classmethod(
            lambda cls, **_k: SimpleNamespace(
                runtime_root=runtime, ledgers_db=runtime / "ledgers" / "ledgers.db"
            )
        ),
    )
    output = report_mod.cmd_auto_buyer_report(args)
    assert output["report_date"] == DATE
    assert Path(output["files"]["markdown"]).parent == tmp_path / "out" / DATE
    assert {c["cohort"] for c in output["cohorts"]} >= {"ncaab-spread-v7:spread:[0.05,0.12):live"}


def test_dashboard_daily_report_route_is_read_only(runtime, monkeypatch):
    from io import BytesIO
    from unittest.mock import Mock

    from model_prediction.dashboard import common, routes
    from model_prediction.portfolio import auto_executor

    monkeypatch.setattr(auto_executor, "DATA", runtime)
    monkeypatch.setattr(common, "_CACHE", {})

    def get(path: str) -> tuple[int, dict[str, Any]]:
        handler = routes.Handler.__new__(routes.Handler)
        handler.path = path
        handler.headers = {}
        handler.wfile = BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.do_GET()
        return handler.send_response.call_args[0][0], json.loads(handler.wfile.getvalue().decode("utf-8"))

    before = {p: p.stat().st_mtime_ns for p in runtime.rglob("*") if p.is_file()}
    code, body = get(f"/api/auto-buyer/daily-report?date={DATE}")
    assert code == 200
    assert body["report_date"] == DATE
    assert any(c["cohort"].startswith(SPREAD_V7) for c in body["cohorts"])
    code, body = get("/api/auto-buyer/daily-report?date=not-a-date")
    assert code == 400
    assert before == {p: p.stat().st_mtime_ns for p in runtime.rglob("*") if p.is_file()}


def test_quotes_at_or_before_the_buy_are_never_a_close(runtime):
    row = _rows(report_mod.build_auto_buyer_report(runtime, DATE), "live", "pd")
    assert row["close_status"] == "no_close_after_buy_before_tip"
    assert row["clv_cents"] is None
    assert row["buy_contract_line"] == pytest.approx(140.5)
