"""NCAAF pre-kickoff close capture (quick 261121-pkc).

Every exchange call goes to an in-memory gateway (``httpx.MockTransport``) behind the real
``PolymarketUSClient`` and the real ``capture_slate_snapshots``, so the rows settlement reads are
written by the production writer, not by hand. No request leaves the process.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx

from model_prediction import ncaaf_prekickoff_capture as cap
from model_prediction.cli import settle as settle_module
from model_prediction.config import PROJECT_ROOT
from model_prediction.data_sources import polymarket_us as pm
from model_prediction.domain import League, MarketType, ModelOrigin, PickRequest, iso_utc
from model_prediction.eligibility import EligibilityResult
from model_prediction.entities import CanonicalTeam
from model_prediction.ledger import PickLedger
from model_prediction.pretip_close_capture import _row_slug
from model_prediction.run_supervisor import DEFAULT_WORKER_TIMEOUTS, WORKERS
from model_prediction.runtime_ledger_store import _SCHEMA


class _Clock:
    """A clock that only moves forward: ``step`` per read, plus explicit ``advance`` (request latency)."""

    def __init__(self, start: datetime, step: timedelta = timedelta(0)) -> None:
        self.now = start
        self.step = step

    def __call__(self) -> datetime:
        value = self.now
        self.now = self.now + self.step
        return value

    def advance(self, delta: timedelta) -> None:
        self.now = self.now + delta


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _pt(line: float) -> str:
    return f"{abs(line):g}".replace(".", "pt")


class _Game:
    """One NCAAF game in the gateway's own payload shapes; home is the anchor of every spread."""

    def __init__(self, key: str, away: str, home: str, start: datetime, *, slug_date: str = "2026-11-21"):
        self.key, self.away, self.home, self.start = key, away, home, start
        self.event_slug = f"cfb-{key}-{slug_date}"
        self.ml_slug = f"aec-cfb-{key}-{slug_date}"
        # (line, cached long-side price): -3.5 is the most balanced rung, -7.5 the least.
        self.spreads = [(-3.5, 0.52), (-7.5, 0.30), (-1.5, 0.62)]
        self.totals = [(47.5, 0.50), (51.5, 0.33)]

    def spread_slug(self, line: float) -> str:
        return f"asc-cfb-{self.key}-2026-11-21-{'neg' if line < 0 else 'pos'}-{_pt(line)}"

    def total_slug(self, line: float) -> str:
        return f"tsc-cfb-{self.key}-2026-11-21-total-{_pt(line)}"

    def _sides(self, long_desc, short_desc, price, long_team=None, short_team=None):
        return [
            {
                "id": "L",
                "description": long_desc,
                "long": True,
                "quote": {"value": f"{price}"},
                "team": long_team,
            },
            {
                "id": "S",
                "description": short_desc,
                "long": False,
                "quote": {"value": f"{1 - price:.3f}"},
                "team": short_team,
            },
        ]

    def markets(self) -> list[dict]:
        home, away = {"name": self.home}, {"name": self.away}
        out = [
            {
                "id": f"{self.key}-ml",
                "slug": self.ml_slug,
                "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_MONEYLINE",
                "marketSides": self._sides(self.home, self.away, 0.60, home, away),
            }
        ]
        for line, price in self.spreads:
            out.append(
                {
                    "id": f"{self.key}-sp{line}",
                    "slug": self.spread_slug(line),
                    "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_SPREAD",
                    "sportsMarketType": "football_team_full_game_spread",
                    "line": line,
                    "marketSides": self._sides(f"{line:+.2f}", f"{-line:+.2f}", price, home, away),
                }
            )
        for line, price in self.totals:
            out.append(
                {
                    "id": f"{self.key}-to{line}",
                    "slug": self.total_slug(line),
                    "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_TOTAL",
                    "sportsMarketType": "football_team_full_game_total",
                    "line": line,
                    "marketSides": self._sides("Over", "Under", price),
                }
            )
        return out

    def detail(self) -> dict:
        return {
            "id": self.key,
            "slug": self.event_slug,
            "title": f"{self.away} vs. {self.home}",
            "startTime": _iso(self.start),
            "markets": self.markets(),
        }


def _ask(slug: str) -> float:
    """A distinct, deterministic book ask per contract, so a test can tell whose quote was read."""
    return round(0.40 + (sum(map(ord, slug)) % 40) / 100, 2)


class _Gateway:
    def __init__(self, games: list[_Game], *, clock: _Clock | None = None, list_latency=timedelta(0)):
        self.games = {game.event_slug: game for game in games}
        self.markets = {m["slug"]: (game, m) for game in games for m in game.markets()}
        self.clock, self.list_latency = clock, list_latency
        self.paths: list[str] = []
        self.events_status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.paths.append(path)
        if path == "/v2/leagues/cfb/events":
            if self.clock is not None:
                self.clock.advance(self.list_latency)
            if self.events_status != 200:
                return httpx.Response(self.events_status, json={})
            summary = [
                {"id": g.key, "slug": g.event_slug, "startTime": _iso(g.start)} for g in self.games.values()
            ]
            return httpx.Response(200, json={"events": summary})
        if path.startswith("/v1/events/slug/"):
            return httpx.Response(200, json={"event": self.games[path.rsplit("/", 1)[-1]].detail()})
        if path.startswith("/v1/market/slug/"):
            game, market = self.markets[path.rsplit("/", 1)[-1]]
            body = {"id": market["id"], "gameStartTime": _iso(game.start), "line": market.get("line")}
            return httpx.Response(200, json={"market": {**body, "marketSides": market["marketSides"]}})
        if path.startswith("/v1/markets/") and path.endswith("/book"):
            slug = path.split("/")[3]
            ask = _ask(slug)
            book = {
                "marketSlug": slug,
                "state": "MARKET_STATE_OPEN",
                "bids": [{"px": {"value": f"{ask - 0.02:.2f}"}, "qty": "100"}],
                "offers": [{"px": {"value": f"{ask:.2f}"}, "qty": "100"}],
            }
            return httpx.Response(200, json={"marketData": book})
        return httpx.Response(404, json={})

    def client(self) -> pm.PolymarketUSClient:
        http = httpx.Client(transport=httpx.MockTransport(self.handler))
        return pm.PolymarketUSClient(client=http, max_attempts=1)

    def books(self) -> set[str]:
        return {p.split("/")[3] for p in self.paths if p.endswith("/book")}

    def hydrated(self) -> set[str]:
        return {p.rsplit("/", 1)[-1] for p in self.paths if p.startswith("/v1/events/slug/")}


def _ledger(path: Path, rows: list[tuple[str, datetime, str]]) -> Path:
    """Open NCAAF rows (market type, kickoff, rationale) in the runtime ledger's real schema."""
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    for i, (market_type, start, rationale) in enumerate(rows):
        conn.execute(
            "INSERT INTO ledger_records (pick_id, operation_id, ledger_tier, sport, event_start_utc, "
            "market_type, status, created_at_utc, decision_payload_json) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                f"p{i}",
                f"op{i}",
                "flat",
                "ncaaf",
                iso_utc(start),
                market_type,
                "open",
                "2026-11-21T12:00:00Z",
                json.dumps({"rationale": rationale}),
            ),
        )
    conn.commit()
    conn.close()
    return path


def _fast_capture(monkeypatch, clock: _Clock) -> None:
    # The capture stamps observed_at_utc with utc_now(); drive it from the same advancing clock.
    monkeypatch.setattr(pm, "utc_now", clock)
    monkeypatch.setattr(pm, "CAPTURE_MIN_REQUEST_INTERVAL_SECONDS", 0.0)


def _written(root: Path, game_date: str) -> list[dict]:
    path = root / "odds" / "ncaaf" / game_date / "polymarket_snapshots.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


V62_ML = "CFB v6.2 challenger (anchor-blended): fair margin -5.50. Executable ask 0.6000 ({slug}-home)."


# --------------------------------------------------------------------------- end to end
def _settle_row(tmp_path, monkeypatch, *, start, market_type, selection, line, rationale):
    ledger = PickLedger(tmp_path / f"picks_{market_type}.xlsx", tmp_path / f"events_{market_type}.jsonl")
    request = PickRequest(
        event_start_utc=start.isoformat(),
        event_id="g1",
        league=League.NCAAF,
        away_team="Aaaa",
        home_team="Bbbb",
        market_type=market_type,
        selection=selection,
        line=line,
        sportsbook="polymarket_us",
        american_odds=-110,
        model_probability=0.6,
        model_uncertainty=0.05,
        model_version="cfb-structural-v6.2",
        rationale=rationale,
        risks="",
        model_origin=ModelOrigin.STATISTICAL_MODEL,
        model_enabled=True,
    )
    away = CanonicalTeam("Aaaa", League.NCAAF, "Aaaa", "AAA", True, None, None, ())
    home = CanonicalTeam("Bbbb", League.NCAAF, "Bbbb", "BBB", True, None, None, ())
    row = ledger.append_evaluated(
        request,
        EligibilityResult(False, "CALL", "PAPER_CALL_MODEL_UNVALIDATED", 1.0, 60, 0.05, 0.05, away, home),
        now=start - timedelta(hours=5),
    )
    monkeypatch.setattr(
        settle_module,
        "_find_espn_result",
        lambda *a, **k: {
            "status_name": "STATUS_FINAL",
            "completed": True,
            "away_score": 10,
            "home_score": 20,
        },
    )
    config = {
        "project": {
            "ledger_path": str(PROJECT_ROOT / "data" / "picks.xlsx"),
            "market_odds_snapshot_path": str(tmp_path / "no_market_odds.jsonl"),
        }
    }
    settle_module._settle_all_unsettled(
        SimpleNamespace(), config, ledger, espn=SimpleNamespace(), scoreboard_cache={}
    )
    return next(item for item in ledger.rows() if item["pick_id"] == row["pick_id"])


def test_capture_is_the_close_settlement_records(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(runtime))
    # Kickoff three hours ago (settlement grades only completed games); the capture ran at T-20 min.
    start = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(hours=3)
    game = _Game("aaaa-bbbb", "Aaaa", "Bbbb", start)
    clock = _Clock(start - timedelta(minutes=20), step=timedelta(milliseconds=50))
    _fast_capture(monkeypatch, clock)
    gateway = _Gateway([game])
    ledgers_db = _ledger(
        tmp_path / "ledgers.db",
        [
            ("moneyline", start, V62_ML.format(slug=game.ml_slug)),
            # The least balanced rung, pruned by ladder_keep=1 unless an open row holds it.
            (
                "spread",
                start,
                f"CFB v6.2 challenger (anchor-blended): x. Executable ask 0.3 ({game.spread_slug(-7.5)}).",
            ),
        ],
    )

    status = cap.capture_ncaaf_prekickoff(
        now=clock, ledgers_db=ledgers_db, runtime_root=runtime, client=gateway.client()
    )

    assert status["status"] == "ok", status
    assert status["held_in_window_slugs"] == 2 and status["held_in_window_discovered"] == 2
    game_date = start.astimezone(cap.EASTERN).date().isoformat()
    written = {row["market_slug"]: row for row in _written(runtime, game_date)}
    # Held rungs, the moneyline, and the most balanced spread and total; nothing else.
    assert set(written) == {
        game.ml_slug,
        game.spread_slug(-7.5),
        game.spread_slug(-3.5),
        game.total_slug(47.5),
    }
    assert gateway.books() == set(written)
    for row in written.values():
        assert row["market_state"] == "MARKET_STATE_OPEN"
        assert pm.parse_utc(row["observed_at_utc"]) < start

    ml = _settle_row(
        tmp_path,
        monkeypatch,
        start=start,
        market_type=MarketType.MONEYLINE,
        selection="home",
        line=None,
        rationale=V62_ML.format(slug=game.ml_slug),
    )
    assert ml["status"] == "settled"
    assert float(ml["closing_implied_probability"]) == _ask(game.ml_slug)

    spread = _settle_row(
        tmp_path,
        monkeypatch,
        start=start,
        market_type=MarketType.SPREAD,
        selection="home",
        line=-7.5,
        rationale=f"CFB v6.2 challenger (anchor-blended): x. Executable ask 0.3000 ({game.spread_slug(-7.5)}).",
    )
    assert spread["status"] == "settled"
    assert float(spread["closing_implied_probability"]) == _ask(game.spread_slug(-7.5))


# --------------------------------------------------------------------------- window selection
NOW = datetime(2026, 11, 21, 19, 0, tzinfo=UTC)
LIST_LATENCY = timedelta(seconds=120)
OBSERVED = NOW + LIST_LATENCY  # the clock read after the event list returned


def test_window_is_measured_from_after_the_event_list(tmp_path, monkeypatch):
    clock = _Clock(NOW)
    _fast_capture(monkeypatch, clock)
    window = timedelta(minutes=cap.DEFAULT_WINDOW_MINUTES)
    games = {
        # In the window when the run started, but kicked off (inside a minute) by the time the list returned.
        "started-during-list": NOW + timedelta(seconds=90),
        "lead-60s": OBSERVED + timedelta(seconds=60),
        "lead-61s": OBSERVED + timedelta(seconds=61),
        "window-end": OBSERVED + window,
        "past-window": OBSERVED + window + timedelta(seconds=1),
    }
    gateway = _Gateway(
        [_Game(key, "Aaaa", "Bbbb", start) for key, start in games.items()],
        clock=clock,
        list_latency=LIST_LATENCY,
    )
    # The idle gate needs local evidence of a game in the window: an archived Daily record.
    archive = tmp_path / "odds" / "ncaaf" / "2026-11-21" / "polymarket_snapshots.jsonl"
    archive.parent.mkdir(parents=True)
    archive.write_text(json.dumps({"market_slug": "x", "event_start_utc": _iso(games["lead-61s"])}) + "\n")

    status = cap.capture_ncaaf_prekickoff(
        now=clock, ledgers_db=tmp_path / "missing.db", runtime_root=tmp_path, client=gateway.client()
    )

    assert gateway.hydrated() == {"cfb-lead-61s-2026-11-21", "cfb-window-end-2026-11-21"}
    assert status["games_discovered"] == 2
    captured_events = {row.get("event_slug") for row in _written(tmp_path, "2026-11-21")} - {None}
    assert captured_events == {"cfb-lead-61s-2026-11-21", "cfb-window-end-2026-11-21"}


def test_late_kickoff_is_filed_under_its_eastern_date(tmp_path, monkeypatch):
    # 00:30Z on the 22nd is 19:30 ET on the 21st: settlement looks in the ET-date folder first.
    start = datetime(2026, 11, 22, 0, 30, tzinfo=UTC)
    clock = _Clock(start - timedelta(minutes=15), step=timedelta(milliseconds=10))
    _fast_capture(monkeypatch, clock)
    game = _Game("cccc-dddd", "Cccc", "Dddd", start)
    gateway = _Gateway([game])
    ledgers_db = _ledger(tmp_path / "ledgers.db", [("total", start, f"x ({game.total_slug(51.5)})")])

    cap.capture_ncaaf_prekickoff(
        now=clock, ledgers_db=ledgers_db, runtime_root=tmp_path, client=gateway.client()
    )

    assert {row["market_slug"] for row in _written(tmp_path, "2026-11-21")} >= {game.total_slug(51.5)}
    assert _written(tmp_path, "2026-11-22") == []


def test_held_contract_is_captured_when_the_event_list_fails(tmp_path, monkeypatch):
    start = NOW + timedelta(minutes=12)
    clock = _Clock(NOW, step=timedelta(milliseconds=10))
    _fast_capture(monkeypatch, clock)
    game = _Game("eeee-ffff", "Eeee", "Ffff", start)
    gateway = _Gateway([game])
    gateway.events_status = 503
    slug = game.spread_slug(-7.5)
    ledgers_db = _ledger(tmp_path / "ledgers.db", [("spread", start, f"x ({slug})")])
    archive = tmp_path / "odds" / "ncaaf" / "2026-11-21" / "polymarket_snapshots.jsonl"
    archive.parent.mkdir(parents=True)
    archive.write_text(
        json.dumps(
            {
                "market_slug": slug,
                "market_type": "spread",
                "line": -7.5,
                "team": "Ffff",
                "league": "NCAAF",
                "event_id": "eeee-ffff",
                "event_slug": game.event_slug,
                "event_start_utc": _iso(start),
                "observed_at_utc": _iso(NOW - timedelta(hours=4)),
            }
        )
        + "\n"
    )

    status = cap.capture_ncaaf_prekickoff(
        now=clock, ledgers_db=ledgers_db, runtime_root=tmp_path, client=gateway.client()
    )

    assert status["status"] == "partial" and status["fallback_contracts"] == 1
    fresh = [row for row in _written(tmp_path, "2026-11-21") if pm.parse_utc(row["observed_at_utc"]) > NOW]
    assert [(row["market_slug"], row["line"], row["team"]) for row in fresh] == [(slug, -7.5, "Ffff")]


def test_no_game_in_the_window_makes_no_request(tmp_path, monkeypatch):
    clock = _Clock(NOW, step=timedelta(milliseconds=10))
    _fast_capture(monkeypatch, clock)
    later = NOW + timedelta(hours=3)
    gateway = _Gateway([_Game("gggg-hhhh", "Gggg", "Hhhh", later)])
    ledgers_db = _ledger(
        tmp_path / "ledgers.db", [("total", later, "x (tsc-cfb-gggg-hhhh-2026-11-21-total-47pt5)")]
    )
    archive = tmp_path / "odds" / "ncaaf" / "2026-11-21" / "polymarket_snapshots.jsonl"
    archive.parent.mkdir(parents=True)
    archive.write_text(json.dumps({"market_slug": "x", "event_start_utc": _iso(later)}) + "\n")

    status = cap.capture_ncaaf_prekickoff(
        now=clock, ledgers_db=ledgers_db, runtime_root=tmp_path, client=gateway.client()
    )

    assert status["status"] == "idle"
    assert gateway.paths == []


# --------------------------------------------------------------------------- slugs
def test_ledger_slugs_resolve_to_the_contract():
    rationale = V62_ML.format(slug="aec-cfb-byu-kan-2026-11-21")
    # The shared extractor is unchanged (it also feeds the real-money ticket gate); NCAAF
    # settlement corrects its "anchor-blended" pick locally.
    assert settle_module._extract_market_slug(rationale, {}) == "anchor-blended"
    assert cap.ncaaf_settlement_slug("anchor-blended", rationale) == "aec-cfb-byu-kan-2026-11-21"
    assert cap.ncaaf_settlement_slug("ncaaf-ml-401752-home", "ESPN consensus (no contract)") == (
        "ncaaf-ml-401752-home"
    )
    assert _row_slug(None, None, rationale) == "aec-cfb-byu-kan-2026-11-21-home"
    assert cap.ncaaf_contract_slug("aec-cfb-byu-kan-2026-11-21-home") == "aec-cfb-byu-kan-2026-11-21"
    assert cap.ncaaf_contract_slug("aec-cfb-byu-kan-2026-11-21-away") == "aec-cfb-byu-kan-2026-11-21"
    # Spread/total rungs and ESPN-consensus pseudo-slugs are left alone.
    assert (
        cap.ncaaf_contract_slug("asc-cfb-byu-kan-2026-11-21-neg-5pt5")
        == "asc-cfb-byu-kan-2026-11-21-neg-5pt5"
    )
    assert cap.ncaaf_contract_slug("ncaaf-ml-401752-home") == "ncaaf-ml-401752-home"
    # A plain hyphenated token is still a slug when no contract-prefixed one exists.
    assert (
        settle_module._extract_market_slug("Model (nba-lal-bos-2026-11-21).", {}) == "nba-lal-bos-2026-11-21"
    )


# --------------------------------------------------------------------------- worker
def test_worker_is_registered_with_the_supervisor():
    command = WORKERS["ncaaf-prekickoff-capture"]
    assert command[-2:] == ["-m", "model_prediction.ncaaf_prekickoff_capture_worker"]
    assert importlib.util.find_spec("model_prediction.ncaaf_prekickoff_capture_worker") is not None
    # The capture writes only at the end of its budget, which must end well inside the timeout.
    assert cap.DEFAULT_MAX_CAPTURE_SECONDS + 60 <= DEFAULT_WORKER_TIMEOUTS["ncaaf-prekickoff-capture"] <= 600
    script = (PROJECT_ROOT / "scripts" / "register_windows_tasks.ps1").read_text(encoding="utf-8")
    assert "'ModelPrediction-NcaafPrekickoffCapture' 'ncaaf-prekickoff-capture'" in script


def test_worker_exit_codes(monkeypatch, tmp_path, capsys):
    from model_prediction import ncaaf_prekickoff_capture_worker as worker

    monkeypatch.setenv("MODEL_PREDICTION_RUNTIME_ROOT", str(tmp_path))
    for result, code in (
        ({"status": "ok"}, 0),
        ({"status": "idle"}, 0),
        ({"status": "partial"}, 0),
        ({"status": "error"}, 1),
    ):
        monkeypatch.setattr(worker, "capture_ncaaf_prekickoff", lambda result=result, **_kw: result)
        assert worker.main([]) == code
    assert json.loads(capsys.readouterr().out.strip().splitlines()[-1]) == {"status": "error"}
