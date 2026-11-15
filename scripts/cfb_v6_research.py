"""Fit, select, freeze and (later, once released) score the cfb-structural-v6 shadow challenger.

  selfcheck       Synthetic point-in-time / additive-form / baseline / opponent-strength checks. `cache` refuses
                  to run until this has passed for the current scripts/cfb_v6_lib.py.
  snapshot        Write the loaded history union (existing sources + the v6-only backfill) once to
                  data/research/ncaaf_v6/history_snapshot.jsonl with its sha256 and row count.
  cache           v6 raw projected margin / total at kickoff for every game and every grid point.
  fit             Split (train <= 2022-11-11 < validation <= 2024-11-14 < locked holdout), fit calibration and
                  slice SDs on TRAIN only, choose (lambda, w_prev, k) on VALIDATION. Never reads holdout rows.
  freeze          Write outputs/ncaaf/cfb_v6_frozen_fit.json with a sha256 of the canonical parameters.
  verify-frozen   Recompute that sha256 (and the snapshot / code hashes); exit non-zero on mismatch.

Usage (repo root, OPENBLAS_NUM_THREADS=1):
  PYTHONPATH=src .venv/Scripts/python scripts/cfb_v6_research.py selfcheck
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

import cfb_v5_research as v5r
import cfb_v6_lib as lib

from model_prediction.domain import parse_utc

AS_OF_UTC = "2026-11-15T02:10:00Z"
OUT = REPO / "outputs" / "ncaaf"
WORK = REPO / "data" / "research" / "ncaaf_v6"
BACKFILL = WORK / "ncaaf_games_backfill.jsonl"
HISTORY_AUDIT = OUT / "cfb_v6_history_audit.json"
SNAPSHOT = WORK / "history_snapshot.jsonl"
SNAPSHOT_META = WORK / "history_snapshot.meta.json"
SELFCHECK_MARK = WORK / "selfcheck_ok.json"
CACHE = OUT / "cfb_v6_base_rows.jsonl"
FIT_SELECTION = WORK / "fit_selection.json"
FROZEN = OUT / "cfb_v6_frozen_fit.json"
LIB_PATH = REPO / "scripts" / "cfb_v6_lib.py"
LIVE = (REPO / "data" / "processed" / "ncaaf" / "games.jsonl", REPO / "data" / "historical" / "ncaaf_games_all.jsonl")

# Grids. First pass (lam 2-30, w_prev 0.3-1.0, k 3-6) was fixed before any metric was looked at; a labelled
# no-backfill validation run put the optimum on the lower edge of lam and w_prev (monotone in both), so the
# grid was extended downward before the real run. Train and validation only; the holdout and the 575 lined
# games were not touched. A second downward extension of lambda (0.25, 0.1) followed when the validation score was
# still improving at lambda 0.5 on the final backfill. Both extensions are recorded in the frozen fit.
GRID_FIRST_PASS = {"lambda": (2.0, 5.0, 12.0, 30.0), "w_prev": (0.3, 0.6, 1.0), "k": (3, 4, 5, 6)}
LAMBDAS = (0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 12.0, 30.0)  # ridge strength per rating, in weighted team-game rows
W_PREVS = (0.1, 0.2, 0.3, 0.45, 0.6, 1.0)  # weight of the previous season's rows (never 0: week 1 needs a prior)
K_GRID = (2, 3, 4, 5, 6)  # current-season games (of the thinner side) needed for the "established" SD slice
TRAIN_END = "2022-11-11"
VALIDATION_END = "2024-11-14"
BURN_IN_SEASON = 2016  # no previous season in the data: rating input only


def _abs(value: str) -> Path:
    return Path(value).resolve()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def gkey(lam: float, w: float) -> str:
    return f"{lam:g}|{w:g}"


# ------------------------------------------------------------------ selfcheck
def _g(i: int, start: str, home: str, away: str, hs: int, as_: int, neutral: bool | None = False) -> lib.V6Game:
    st = parse_utc(start)
    return lib.V6Game(f"s{i}", st, lib.season_of(st), home, away, hs, as_, neutral, None, "existing")


def _fake_resolve(name: str) -> Any:
    return SimpleNamespace(canonical_name=name, tier="P4")


def _books(games: list[lib.V6Game]) -> lib.RatingBook:
    ordered = sorted(games, key=lambda g: (g.start, g.event_id))
    return lib.RatingBook(ordered, lib.TeamResolver.from_games(ordered, resolve=_fake_resolve))


def selfcheck() -> int:
    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"  {'ok  ' if ok else 'FAIL'} {label} {detail}")
        if not ok:
            failures.append(label)

    teams = [f"Team{c}" for c in "ABCDEFGH"]
    base: list[lib.V6Game] = []
    i = 0
    rng = np.random.default_rng(5)
    for week in range(5):
        for a in range(0, 8, 2):
            h_, a_ = teams[(a + week) % 8], teams[(a + 1 + 2 * week) % 8]
            if h_ == a_:
                continue
            base.append(_g(i, f"2024-09-{7 + 7 * week:02d}T18:00Z" if week < 4 else "2024-10-05T18:00Z", h_, a_, int(20 + rng.integers(0, 20)), int(14 + rng.integers(0, 20))))
            i += 1
    cutoff = parse_utc("2024-10-12T20:00:00Z")

    def solve(games: list[lib.V6Game]) -> tuple[lib.RatingSolution, lib.RatingBook]:
        book = _books(games)
        book.advance(cutoff)
        return book.solve(cutoff, lam=5.0, w_prev=0.6), book

    ref, book = solve(base)

    def same(a: lib.RatingSolution, b: lib.RatingSolution) -> bool:
        return (
            a.mu == b.mu and a.h_home == b.h_home and np.array_equal(a.off, b.off) and np.array_equal(a.dfn, b.dfn)
        )

    # 1. point in time
    in_progress = _g(900, "2024-10-12T18:00Z", "TeamA", "TeamB", 70, 0)  # kickoff + buffer > cutoff
    after = _g(901, "2024-10-12T23:00Z", "TeamA", "TeamB", 70, 0)  # kicks off after the cutoff
    s1, _ = solve([*base, in_progress])
    s2, _ = solve([*base, after])
    check("in-progress game (kickoff + buffer after cutoff) changes no rating", same(ref, s1))
    check("game kicking off after the cutoff changes no rating", same(ref, s2))
    p_ref = lib.project(ref, book, "TeamA", "TeamB", False)
    b1 = _books([*base, in_progress])
    b1.advance(cutoff)
    p1 = lib.project(b1.solve(cutoff, lam=5.0, w_prev=0.6), b1, "TeamA", "TeamB", False)
    check("projection byte-identical with the later games present", p_ref == p1)
    forfeit = lib.replace(_g(903, "2024-10-05T10:00Z", "TeamA", "TeamB", 1, 0), is_forfeit=True)
    s_f, _ = solve([*base, forfeit])
    check("a forfeit row (1-0 placeholder) is not a rating input", same(ref, s_f))
    moved = _g(902, "2024-10-12T10:00Z", "TeamA", "TeamB", 70, 0)  # same game, early enough to count
    s3, _ = solve([*base, moved])
    check("the same game moved before the cutoff changes the ratings", not same(ref, s3))

    # 2. additive form (item 1)
    t = book.n_teams
    off = np.zeros(t)
    dfn = np.zeros(t)
    ia, ib = book.team_index["TeamA"], book.team_index["TeamB"]
    off[ia] = 10.0
    dfn[ib] = 8.0
    sol = lib.RatingSolution(28.0, 0.0, 0.0, off, dfn, ref.cur_games, ref.prev_games, ref.eligible_games, cutoff)
    pr = lib.project(sol, book, "TeamA", "TeamB", True)
    half_sum = 28.0 + (10.0 + 8.0) / 2.0
    check("offense +10 vs defence +8 over a 28 average projects 46 (not the half-sum 37)", pr.home_points == 46.0 and half_sum == 37.0, f"got {pr.home_points}")
    check("additive_points is the plain sum", lib.additive_points(28.0, 10.0, 8.0, 0.0) == 46.0)

    # 3. rolling baseline (no fixed 49.5 / 54 total)
    bal = [-12, -9, -6, -3, 0, 3, 6, 9, 12]
    hist: list[lib.V6Game] = []
    k = 0
    for h_ in teams:  # double round robin: every ordered pair once, so the design is balanced
        for a_ in teams:
            if h_ == a_:
                continue
            e1 = bal[k % 9] if k < 54 else 0
            day = 1 + (k // 2)
            month = 9 if day <= 30 else 10
            hist.append(_g(1000 + k, f"2024-{month:02d}-{day if day <= 30 else day - 30:02d}T{18 + k % 2:02d}:00Z", h_, a_, 31 + e1, 31 - e1))
            k += 1
    sc = sum(g.home_score + g.away_score for g in hist) / (2 * len(hist))
    bk = _books(hist)
    c2 = parse_utc("2024-11-05T00:00:00Z")
    bk.advance(c2)
    sol31 = bk.solve(c2, lam=5.0, w_prev=0.6)
    check("history averaging 31 points per team-game gives mu within 0.5 of 31", abs(sol31.mu - sc) < 0.5, f"sample mean {sc:.2f}, mu {sol31.mu:.3f}")

    # 4. opponent strength: same scoreline against a team the solve rates strong vs weak
    games: list[lib.V6Game] = []
    for day, (hm, aw, hs, as_) in enumerate(
        [
            ("Strong", "O1", 20, 10), ("O2", "Strong", 10, 14), ("Strong", "O3", 17, 7),
            ("Weak", "O1", 38, 17), ("O2", "Weak", 28, 45), ("Weak", "O3", 35, 14),
            ("O1", "O2", 24, 20), ("O3", "O1", 21, 24), ("O2", "O3", 17, 20),
        ]
    ):
        games.append(_g(2000 + day, f"2024-09-{1 + day:02d}T18:00Z", hm, aw, hs, as_))
    vs_strong = [*games, _g(2100, "2024-09-20T18:00Z", "X", "Strong", 28, 14)]
    vs_weak = [*games, _g(2100, "2024-09-20T18:00Z", "X", "Weak", 28, 14)]
    cut = parse_utc("2024-09-28T00:00:00Z")
    out = []
    for gl in (vs_strong, vs_weak):
        bk = _books(gl)
        bk.advance(cut)
        s = bk.solve(cut, lam=5.0, w_prev=0.6)
        out.append((s.off[bk.team_index["X"]], bk.team_index))
        if gl is vs_strong:
            strong_def = s.dfn[bk.team_index["Strong"]]
            weak_def = s.dfn[bk.team_index["Weak"]]
    check("the solve rates Strong's defence better (lower allowed) than Weak's", strong_def < weak_def, f"def Strong {strong_def:.2f}, Weak {weak_def:.2f}")
    check("scoring 28 on the strong defence raises X's offence rating more than 28 on the weak one", out[0][0] > out[1][0], f"{out[0][0]:.3f} vs {out[1][0]:.3f}")

    # 5. fail closed on an unrated team, and the loader's neutral handling
    try:
        lib.project(ref, book, "TeamA", "NeverPlayed", False)
        check("unrated team raises", False)
    except lib.V6NoCall as exc:
        check("unrated team raises NO_CALL_CFB_V6_UNRATED_TEAM", exc.reason == lib.NO_CALL_UNRATED)

    print("selfcheck", "FAILED: " + ", ".join(failures) if failures else "passed")
    if failures:
        return 1
    WORK.mkdir(parents=True, exist_ok=True)
    SELFCHECK_MARK.write_text(
        json.dumps({"lib_sha256": sha256_file(LIB_PATH), "passed_at_utc": datetime.now(UTC).isoformat()}),
        encoding="utf-8",
    )
    return 0


def require_selfcheck() -> None:
    try:
        mark = json.loads(SELFCHECK_MARK.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise SystemExit("selfcheck has not passed; run `cfb_v6_research.py selfcheck` first") from None
    if mark.get("lib_sha256") != sha256_file(LIB_PATH):
        raise SystemExit("cfb_v6_lib.py changed since selfcheck passed; re-run selfcheck")


# ------------------------------------------------------------------ snapshot
def cmd_snapshot(args: argparse.Namespace) -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    live = {str(p.relative_to(REPO)).replace("\\", "/"): {"sha256": sha256_file(p), "rows": sum(1 for ln in p.open("rb") if ln.strip())} for p in LIVE}
    backfill = None
    expected = None
    if not args.no_backfill:
        if not BACKFILL.exists():
            raise SystemExit("backfill file missing; use --no-backfill only for a labelled tracer")
        if HISTORY_AUDIT.exists():
            audit = json.loads(HISTORY_AUDIT.read_text(encoding="utf-8"))
            expected = _find_sha(audit)
        backfill = BACKFILL
    games, stats = lib.load_history(LIVE[0], LIVE[1], backfill, expected_backfill_sha256=expected)
    rows = lib.snapshot_rows(games)
    path = args.out or SNAPSHOT
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    meta = {
        "as_of_utc": AS_OF_UTC,
        "snapshot_path": str(path.relative_to(REPO)).replace("\\", "/"),
        "snapshot_sha256": sha256_file(path),
        "snapshot_rows": len(rows),
        "live_sources_at_load": live,
        "backfill": None
        if backfill is None
        else {"path": "data/research/ncaaf_v6/ncaaf_games_backfill.jsonl", "sha256": stats.get("backfill_sha256"), "rows": stats["backfill_rows_read"], "audit_declared_sha256": expected},
        "loader_stats": stats,
        "label": "no backfill" if backfill is None else "with backfill",
    }
    (args.meta or SNAPSHOT_META).write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({k: meta[k] for k in ("snapshot_sha256", "snapshot_rows", "label")}), stats)
    return 0


def _find_sha(audit: dict[str, Any]) -> str | None:
    """B records the backfill sha256 somewhere in its audit json; find a 64-hex value under a backfill key."""
    found: list[str] = []

    def walk(node: Any, key: str = "") -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{key}.{k}".lower())
        elif isinstance(node, str) and len(node) == 64 and "sha" in key and "backfill" in key:
            found.append(node)

    walk(audit)
    return found[0] if found else None


# ------------------------------------------------------------------ cache
def _solve_with_retry(book: lib.RatingBook, when: datetime, lam: float, w: float) -> lib.RatingSolution:
    """The host is short of virtual memory at times (peer load); a failed small allocation is retried."""
    for attempt in range(8):
        try:
            return book.solve(when, lam=lam, w_prev=w)
        except MemoryError:
            time.sleep(5 * (attempt + 1))
    return book.solve(when, lam=lam, w_prev=w)


def cmd_cache(args: argparse.Namespace) -> int:
    require_selfcheck()
    snap = args.snapshot or SNAPSHOT
    meta_path = args.meta or SNAPSHOT_META
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if sha256_file(snap) != meta["snapshot_sha256"]:
        raise SystemExit("history snapshot does not match its recorded sha256")
    games = lib.load_snapshot(snap)
    resolver = lib.TeamResolver.from_games(games)
    book = lib.RatingBook(games, resolver)
    seasons = set(args.seasons) if args.seasons else None
    out_path = args.out or CACHE
    t0 = time.time()
    last_key: tuple[int, int] | None = None
    sols: dict[str, lib.RatingSolution | None] = {}
    n_rows = skipped = 0
    reasons: dict[str, int] = {}
    with out_path.open("w", encoding="utf-8") as out:
        for g in games:
            if g.season <= BURN_IN_SEASON or g.is_forfeit:
                continue
            if seasons is not None and g.season not in seasons:
                continue
            ptr = book.advance(g.start)
            key = (ptr, g.season)
            if key != last_key:
                sols = {}
                for lam in LAMBDAS:
                    for w in W_PREVS:
                        try:
                            sols[gkey(lam, w)] = _solve_with_retry(book, g.start, lam, w)
                        except lib.V6NoCall:
                            sols[gkey(lam, w)] = None
                last_key = key
            row: dict[str, Any] = {
                "event_id": g.event_id,
                "date": g.start.strftime("%Y-%m-%d"),
                "season": g.season,
                "start_utc": g.start.astimezone(UTC).strftime("%Y-%m-%dT%H:%MZ"),
                "home": g.home,
                "away": g.away,
                "neutral": g.neutral,
                "margin": float(g.home_score - g.away_score),
                "total": float(g.home_score + g.away_score),
                "source": g.source,
                "eligible_games": ptr,
            }
            hi, ai = book.identity(g.home, g.season), book.identity(g.away, g.season)
            row["fbs_involved"] = not (hi.fcs and ai.fcs)
            row["game_class"] = g.game_class
            grid: dict[str, list[float]] = {}
            meta_set = False
            reason = None
            for k, sol in sols.items():
                if sol is None:
                    reason = lib.NO_CALL_NO_HISTORY
                    break
                try:
                    p = lib.project(sol, book, g.home, g.away, g.neutral)
                except lib.V6NoCall as exc:
                    reason = exc.reason
                    break
                grid[k] = [p.margin, p.total, p.mu, p.h_home]
                if not meta_set:
                    row.update(
                        sample_games=p.sample_games,
                        home_games_window=p.home_games_window,
                        away_games_window=p.away_games_window,
                        home_games_current=p.home_games_current,
                        away_games_current=p.away_games_current,
                        is_fcs=p.is_fbs_vs_fcs,
                    )
                    meta_set = True
            if reason is not None:
                row["no_call"] = reason
                reasons[reason] = reasons.get(reason, 0) + 1
                skipped += 1
            else:
                row["grid"] = grid
            out.write(json.dumps(row) + "\n")
            n_rows += 1
            if n_rows % 1000 == 0:
                print(f"  cache {n_rows} rows ({time.time() - t0:.0f}s)", flush=True)
    print(f"wrote {out_path} rows {n_rows} (no-call {skipped}: {reasons}) in {time.time() - t0:.0f}s")
    return 0


# ------------------------------------------------------------------ fit (train + validation only)
def _load_cache(path: Path) -> list[dict[str, Any]]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


_ols = v5r.ols
_pstdev = v5r.pstdev
_p_home = v5r.p_home_normal
_gls = v5r.gauss_log_score


def slice_of(r: dict[str, Any], k: int) -> str:
    if r["is_fcs"]:
        return "fbs_vs_fcs"
    return "established" if r["sample_games"] >= k else "thin_sample"


def fit_variant(train: list[dict[str, Any]], gk: str, k: int) -> dict[str, Any]:
    xm = [r["grid"][gk][0] for r in train]
    xt = [r["grid"][gk][1] for r in train]
    am, bm = _ols(xm, [r["margin"] for r in train])
    at, bt = _ols(xt, [r["total"] for r in train])
    sd_m: dict[str, list[float]] = {"established": [], "thin_sample": [], "fbs_vs_fcs": []}
    sd_t: dict[str, list[float]] = {"established": [], "thin_sample": [], "fbs_vs_fcs": []}
    for r, pm_, pt_ in zip(train, xm, xt, strict=True):
        s = slice_of(r, k)
        sd_m[s].append(r["margin"] - (am + bm * pm_))
        sd_t[s].append(r["total"] - (at + bt * pt_))
    all_m = [v for vs in sd_m.values() for v in vs]
    all_t = [v for vs in sd_t.values() for v in vs]
    return {
        "margin_calibration": {"intercept": am, "slope": bm},
        "total_calibration": {"intercept": at, "slope": bt},
        "margin_sd_table": {s: (_pstdev(v) if len(v) > 1 else _pstdev(all_m)) for s, v in sd_m.items()},
        "total_sd_table": {s: (_pstdev(v) if len(v) > 1 else _pstdev(all_t)) for s, v in sd_t.items()},
        "train_slice_counts": {s: len(v) for s, v in sd_m.items()},
        "established_min_sample": k,
    }


def score_variant(rows: list[dict[str, Any]], gk: str, params: dict[str, Any]) -> dict[str, Any]:
    mc, tc = params["margin_calibration"], params["total_calibration"]
    k = params["established_min_sample"]
    briers, mls, tls, mae_m, mae_t, in_m, in_t = [], [], [], [], [], [], []
    for r in rows:
        pm_, pt_ = r["grid"][gk][:2]
        mu = mc["intercept"] + mc["slope"] * pm_
        tmu = tc["intercept"] + tc["slope"] * pt_
        s = slice_of(r, k)
        sd = params["margin_sd_table"][s]
        tsd = params["total_sd_table"][s]
        p = _p_home(mu, sd)
        briers.append((p - int(r["margin"] > 0)) ** 2)
        mls.append(_gls(r["margin"], mu, sd))
        tls.append(_gls(r["total"], tmu, tsd))
        mae_m.append(abs(r["margin"] - mu))
        mae_t.append(abs(r["total"] - tmu))
        in_m.append(mu)
        in_t.append(tmu)
    n = len(rows)
    return {
        "n": n,
        "brier": sum(briers) / n,
        "margin_log_score": sum(mls) / n,
        "total_log_score": sum(tls) / n,
        "mean_log_score": (sum(mls) + sum(tls)) / (2 * n),
        "margin_mae": sum(mae_m) / n,
        "total_mae": sum(mae_t) / n,
        "model_margin_sd": _pstdev(in_m),
        "actual_margin_sd": _pstdev([r["margin"] for r in rows]),
        "model_total_sd": _pstdev(in_t),
        "actual_total_sd": _pstdev([r["total"] for r in rows]),
    }


def split_rows(rows: list[dict[str, Any]], train_end: str, val_end: str) -> tuple[list, list]:
    """Train and validation only; the holdout (date > val_end) is dropped here and never scored by `fit`."""
    usable = [
        r for r in rows
        if r["source"] == "existing" and r["season"] > BURN_IN_SEASON and "grid" in r
    ]
    train = [r for r in usable if r["date"] <= train_end]
    val = [r for r in usable if train_end < r["date"] <= val_end]
    return train, val


def cmd_fit(args: argparse.Namespace) -> int:
    path = args.cache or CACHE
    rows = _load_cache(path)
    train_end, val_end = (args.train_end or TRAIN_END), (args.val_end or VALIDATION_END)
    train, val = split_rows(rows, train_end, val_end)
    snap_games = lib.load_snapshot(SNAPSHOT)
    burn_in_existing = sum(1 for g in snap_games if g.season <= BURN_IN_SEASON and g.source == "existing" and not g.is_forfeit)
    excluded = {
        "burn_in_2016_games_excluded_existing": burn_in_existing,
        "no_call_unrated_or_no_history": sum(1 for r in rows if "no_call" in r and r["source"] == "existing"),
        "backfill_only_rows": sum(1 for r in rows if r["source"] == "backfill"),
        "holdout_rows_not_read_by_fit": sum(1 for r in rows if r["date"] > val_end),
    }
    print("train", len(train), "validation", len(val), excluded)
    table = []
    cache_fit: dict[tuple[str, int], dict[str, Any]] = {}
    for lam in LAMBDAS:
        for w in W_PREVS:
            gk = gkey(lam, w)
            for k in K_GRID:
                params = fit_variant(train, gk, k)
                cache_fit[(gk, k)] = params
                sc = score_variant(val, gk, params)
                table.append({"lambda": lam, "w_prev": w, "k": k, "validation": sc, "train_margin_cal": params["margin_calibration"]})
    best = min(table, key=lambda t: (-t["validation"]["mean_log_score"], t["validation"]["brier"]))
    gk = gkey(best["lambda"], best["w_prev"])
    params = cache_fit[(gk, best["k"])]
    train_score = score_variant(train, gk, params)
    # league average points per team: mean of total / 2 on train and validation actuals
    league_avg_train = statistics.fmean(r["total"] for r in train) / 2
    league_avg_val = statistics.fmean(r["total"] for r in val) / 2
    # solved league average (mu) and home term at the selected configuration, from the cache (grid[2], grid[3])
    mus = [r["grid"][gk][2] for r in train + val if len(r["grid"][gk]) > 3]
    hfas = [r["grid"][gk][3] for r in train + val if len(r["grid"][gk]) > 3]
    solved = {
        "mu_mean_train": statistics.fmean(r["grid"][gk][2] for r in train),
        "mu_mean_validation": statistics.fmean(r["grid"][gk][2] for r in val),
        "mu_min_max": [min(mus), max(mus)],
        "h_home_mean_train": statistics.fmean(r["grid"][gk][3] for r in train),
        "h_home_mean_validation": statistics.fmean(r["grid"][gk][3] for r in val),
        "h_home_min_max": [min(hfas), max(hfas)],
    }
    result = {
        "as_of_utc": AS_OF_UTC,
        "solved_baseline_at_selected_config": solved,
        "split": {"train_end": train_end, "validation_end": val_end},
        "counts": {"train": len(train), "validation": len(val), **excluded},
        "grid": {"lambda": list(LAMBDAS), "w_prev": list(W_PREVS), "k": list(K_GRID)},
        "selection_rule": "max mean(margin, total gaussian log score) on validation; moneyline Brier tiebreak (D-SELECTION)",
        "selected": {"lambda": best["lambda"], "w_prev": best["w_prev"], "k": best["k"]},
        "params": params,
        "train_score_selected": train_score,
        "validation_score_selected": best["validation"],
        "league_avg_points_per_team": {"train": league_avg_train, "validation": league_avg_val},
        "selection_table": table,
    }
    out = args.fit_out or FIT_SELECTION
    out.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    sel = result["selected"]
    v = best["validation"]
    print("selected", sel, "validation:", {k2: round(x, 4) for k2, x in v.items() if k2 != "n"})
    print("train score:", {k2: round(x, 4) for k2, x in train_score.items() if k2 != "n"})
    print("calibration", params["margin_calibration"], params["total_calibration"])
    print("margin SD", params["margin_sd_table"], "total SD", params["total_sd_table"])
    print("solved baseline", solved)
    print("league avg points/team train", round(league_avg_train, 2), "val", round(league_avg_val, 2))
    print("wrote", out)
    return 0


# ------------------------------------------------------------------ freeze / verify
def canonical_params(frozen: dict[str, Any]) -> str:
    blob = {k: frozen[k] for k in sorted(frozen) if k not in ("params_sha256", "generated_at_utc")}
    return json.dumps(blob, sort_keys=True, separators=(",", ":"))


def cmd_freeze(args: argparse.Namespace) -> int:
    require_selfcheck()
    sel = json.loads(FIT_SELECTION.read_text(encoding="utf-8"))
    meta = json.loads(SNAPSHOT_META.read_text(encoding="utf-8"))
    if meta["label"] != "with backfill" and not args.allow_no_backfill:
        raise SystemExit("refusing to freeze a no-backfill fit")
    if sha256_file(SNAPSHOT) != meta["snapshot_sha256"]:
        raise SystemExit("snapshot does not match its recorded sha256")
    if FROZEN.exists():
        raise SystemExit(f"{FROZEN} already exists; a re-freeze must be a deliberate, labelled second look")
    frozen = {
        "model": "cfb-structural-v6",
        "as_of_utc": AS_OF_UTC,
        "result_buffer_hours": lib.RESULT_BUFFER_HOURS,
        "pool_key": lib.POOL_KEY,
        "burn_in_season": BURN_IN_SEASON,
        "split": sel["split"],
        "grid": sel["grid"],
        "grid_first_pass_before_extension": {k: list(v) for k, v in GRID_FIRST_PASS.items()},
        "grid_extensions": [
            "first: lambda 0.5,1.0 / w_prev 0.1,0.2,0.45 / k 2 added after a no-backfill validation run hit the lower edge",
            "second: lambda 0.1,0.25 added after the final-backfill validation score still improved at lambda 0.5",
        ],
        "selected": sel["selected"],
        "params": sel["params"],
        "counts": sel["counts"],
        "train_score_selected": sel["train_score_selected"],
        "validation_score_selected": sel["validation_score_selected"],
        "league_avg_points_per_team": sel["league_avg_points_per_team"],
        "league_avg_points_per_team_note": "actual scoring of the scored FBS-side games (not the solved mu); solved mu is in solved_baseline_at_selected_config",
        "solved_baseline_at_selected_config": sel["solved_baseline_at_selected_config"],
        "selection_table": sel["selection_table"],
        "selection_rule": sel["selection_rule"],
        "history_snapshot": {"sha256": meta["snapshot_sha256"], "rows": meta["snapshot_rows"]},
        "backfill": meta["backfill"],
        "live_sources_at_load": meta["live_sources_at_load"],
        "code": {"cfb_v6_lib_sha256": sha256_file(LIB_PATH)},
        "label": meta["label"],
    }
    frozen["params_sha256"] = hashlib.sha256(canonical_params(frozen).encode("utf-8")).hexdigest()
    frozen["generated_at_utc"] = datetime.now(UTC).isoformat()
    FROZEN.write_text(json.dumps(frozen, indent=2, sort_keys=True), encoding="utf-8")
    print("frozen", FROZEN, frozen["params_sha256"])
    return 0


def verify_frozen(quiet: bool = False) -> str:
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    want = hashlib.sha256(canonical_params(frozen).encode("utf-8")).hexdigest()
    problems = []
    if want != frozen["params_sha256"]:
        problems.append("params sha256 mismatch")
    if SNAPSHOT.exists() and sha256_file(SNAPSHOT) != frozen["history_snapshot"]["sha256"]:
        problems.append("history snapshot sha256 mismatch")
    if sha256_file(LIB_PATH) != frozen["code"]["cfb_v6_lib_sha256"]:
        problems.append("cfb_v6_lib.py changed since freeze")
    if problems:
        raise SystemExit("verify-frozen FAILED: " + "; ".join(problems))
    if not quiet:
        print("verify-frozen ok", frozen["params_sha256"])
    return frozen["params_sha256"]


# ------------------------------------------------------------------ holdout (single scoring, refuses to overwrite)
RESEARCH_JSON = OUT / "cfb_v6_research.json"
RESEARCH_MD = OUT / "cfb_v6_research.md"
V5_CACHE = OUT / "cfb_v5_base_rows.jsonl"
V5_REPORT = OUT / "cfb_v5_research.json"
V5_ARTIFACT = REPO / "config" / "models" / "cfb-structural-v5.json"
V5_WINDOW = ("2024-11-15", "2026-11-12")
_SCORE_KEYS = ("brier", "log_loss", "ece", "calibration_slope", "margin_log_score", "total_log_score")


def _v6_values(r: dict[str, Any], gk: str, params: dict[str, Any]) -> dict[str, float]:
    pm_, pt_ = r["grid"][gk][:2]
    mc, tc = params["margin_calibration"], params["total_calibration"]
    s = slice_of(r, params["established_min_sample"])
    mu = mc["intercept"] + mc["slope"] * pm_
    tmu = tc["intercept"] + tc["slope"] * pt_
    sd = params["margin_sd_table"][s]
    return {"v6_mu": mu, "v6_sd": sd, "v6_tmu": tmu, "v6_tsd": params["total_sd_table"][s], "v6_p": _p_home(mu, sd)}


def _score(rows: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    if prefix == "v6":
        return v5r.score_rows(rows, p_key="v6_p", mu_key="v6_mu", sd_key="v6_sd", tmu_key="v6_tmu", tsd_key="v6_tsd")
    return v5r.score_rows(rows, p_key="an_p", mu_key="an_mu", sd_key="an_sd", tmu_key="an_tmu", tsd_key="an_tsd")


def _mae(rows: list[dict[str, Any]], mu: str) -> float:
    return sum(abs(r["margin"] - r[mu]) for r in rows) / len(rows)


def _v6_only_block(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    if len(rows) < 30:
        return {"n": len(rows)}
    return {"n": len(rows), "v6": _score(rows, "v6"), "margin_mae": _mae(rows, "v6_mu")}


def cmd_holdout(args: argparse.Namespace) -> int:
    require_selfcheck()
    frozen_hash = verify_frozen(quiet=True)
    if RESEARCH_JSON.exists():
        raise SystemExit(f"{RESEARCH_JSON} exists: the locked holdout is scored once; a second look needs a new freeze")
    frozen = json.loads(FROZEN.read_text(encoding="utf-8"))
    params = frozen["params"]
    sel = frozen["selected"]
    gk = gkey(sel["lambda"], sel["w_prev"])
    val_end = frozen["split"]["validation_end"]

    # v5 on its own cache with the artifact calibration and SD table (analytic scorer)
    v5_art = json.loads(V5_ARTIFACT.read_text(encoding="utf-8"))
    v5_rows = _load_cache(V5_CACHE)
    v5_hold = [r for r in v5_rows if V5_WINDOW[0] <= r["date"] <= V5_WINDOW[1]]
    v5r.apply_variant(v5_hold, v5_art, mean_cal=True, sd_table=True)
    recorded = json.loads(V5_REPORT.read_text(encoding="utf-8"))["holdout"]["v5_engine"]["brier"]
    got = sum((r["an_p"] - int(r["margin"] > 0)) ** 2 for r in v5_hold) / len(v5_hold)
    incumbent = {"n": len(v5_hold), "analytic_brier": got, "recorded_engine_brier": recorded, "diff": got - recorded}
    print("incumbent check:", incumbent)
    if len(v5_hold) != 1527 or abs(got - recorded) > 0.002:
        raise SystemExit("v5 incumbent reproduction failed; stopping before scoring v6")
    v5_by_id = {r["event_id"]: r for r in v5_hold}

    cache = _load_cache(CACHE)
    hold = [r for r in cache if r["date"] > val_end and r["season"] > BURN_IN_SEASON]
    scoreable = [r for r in hold if "grid" in r]
    existing = [r for r in scoreable if r["source"] == "existing"]
    for r in existing:
        r.update(_v6_values(r, gk, params))
    common = []
    for r in existing:
        v5row = v5_by_id.get(r["event_id"])
        if v5row is None:
            continue
        m = dict(r)
        m.update({k: v5row[k] for k in ("an_p", "an_mu", "an_sd", "an_tmu", "an_tsd")})
        y = int(m["margin"] > 0)
        m["d_brier"] = (m["v6_p"] - y) ** 2 - (m["an_p"] - y) ** 2
        m["d_margin_ls"] = _gls(m["margin"], m["v6_mu"], m["v6_sd"]) - _gls(m["margin"], m["an_mu"], m["an_sd"])
        m["d_total_ls"] = _gls(m["total"], m["v6_tmu"], m["v6_tsd"]) - _gls(m["total"], m["an_tmu"], m["an_tsd"])
        m["d_margin_abs"] = abs(m["margin"] - m["v6_mu"]) - abs(m["margin"] - m["an_mu"])
        common.append(m)
    scored_ids = {r["event_id"] for r in existing}
    nocall_by_id = {r["event_id"]: r.get("no_call") for r in hold if "no_call" in r}
    reasons: dict[str, int] = {}
    missing_v6 = [e for e in v5_by_id if e not in scored_ids]
    for e in missing_v6:
        why = nocall_by_id.get(e, "not_in_v6_cache")
        reasons[why] = reasons.get(why, 0) + 1

    common_report: dict[str, Any] = {
        "n": len(common),
        "v6": _score(common, "v6"),
        "v5": _score(common, "v5"),
        "margin_mae": {"v6": _mae(common, "v6_mu"), "v5": _mae(common, "an_mu")},
        "delta_v6_minus_v5": {
            "brier": v5r.clustered_bootstrap_mean_delta(common, "d_brier", seed=11),
            "margin_log_score": v5r.clustered_bootstrap_mean_delta(common, "d_margin_ls", seed=12),
            "total_log_score": v5r.clustered_bootstrap_mean_delta(common, "d_total_ls", seed=13),
            "margin_abs_error": v5r.clustered_bootstrap_mean_delta(common, "d_margin_abs", seed=14),
        },
        "by_season": {},
        "by_slice": {},
    }
    for season in sorted({r["season"] for r in common}):
        sub = [r for r in common if r["season"] == season]
        a6, a5 = _score(sub, "v6"), _score(sub, "v5")
        common_report["by_season"][str(season)] = {
            "n": len(sub),
            "v6_brier": a6["brier"],
            "v5_brier": a5["brier"],
            "v6_margin_log_score": a6["margin_log_score"],
            "v5_margin_log_score": a5["margin_log_score"],
            "v6_margin_bias": a6["margin_bias"],
            "v5_margin_bias": a5["margin_bias"],
        }
    k = params["established_min_sample"]
    for label in ("established", "thin_sample", "fbs_vs_fcs"):
        sub = [r for r in common if slice_of(r, k) == label]
        if len(sub) >= 30:
            common_report["by_slice"][label] = {"n": len(sub), "v6": _score(sub, "v6"), "v5": _score(sub, "v5")}

    v6_only = [r for r in existing if r["event_id"] not in v5_by_id]
    backfill_rows = [r for r in scoreable if r["source"] == "backfill" and r.get("fbs_involved")]
    for r in backfill_rows:
        r.update(_v6_values(r, gk, params))
    report = {
        "as_of_utc": AS_OF_UTC,
        "frozen_params_sha256": frozen_hash,
        "frozen_label": frozen["label"],
        "selected": sel,
        "split": frozen["split"],
        "incumbent_check": incumbent,
        "holdout_window": {"v6_after": val_end, "v5_window": list(V5_WINDOW)},
        "counts": {
            "holdout_rows_in_cache": len(hold),
            "v6_scoreable_existing": len(existing),
            "common_cohort": len(common),
            "v5_events_without_v6": len(missing_v6),
            "v5_events_without_v6_reasons": reasons,
            "v6_only_existing_events": len(v6_only),
            "backfill_only_fbs_involved": len(backfill_rows),
        },
        "common_cohort": common_report,
        "v6_only_existing_events_block": _v6_only_block(v6_only),
        "backfill_only_fbs_involved_block": _v6_only_block(backfill_rows),
        "backfill_only_by_class": {
            c: _v6_only_block([r for r in backfill_rows if r.get("game_class") == c]) for c in ("FBS-FBS", "FBS-FCS")
        },
    }
    RESEARCH_JSON.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    RESEARCH_MD.write_text(render_holdout_md(report), encoding="utf-8")
    print("wrote", RESEARCH_JSON, RESEARCH_MD)
    print("common n", len(common), "v6", {x: common_report["v6"][x] for x in _SCORE_KEYS})
    print("            v5", {x: common_report["v5"][x] for x in _SCORE_KEYS})
    return 0


def _f(x: Any, nd: int = 4) -> str:
    return "n/a" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def render_holdout_md(rep: dict[str, Any]) -> str:
    c = rep["common_cohort"]
    inc = rep["incumbent_check"]
    lines = [
        f"# cfb-structural-v6 locked-holdout result, as of {rep['as_of_utc']}",
        "",
        (
            f"Frozen fit sha256 `{rep['frozen_params_sha256']}` ({rep['frozen_label']}); selected "
            f"{json.dumps(rep['selected'])}. Holdout scored once. v5 incumbent check: analytic Brier "
            f"{_f(inc['analytic_brier'], 5)} vs recorded engine {_f(inc['recorded_engine_brier'], 5)} (n {inc['n']})."
        ),
        "",
        (
            f"Common event cohort (in both v5 and v6 holdout sets): n {c['n']}. v5 events without a v6 projection: "
            f"{rep['counts']['v5_events_without_v6']} ({json.dumps(rep['counts']['v5_events_without_v6_reasons'])})."
        ),
        "",
        (
            "| model | n | Brier | log loss | ECE | calibration slope | margin log score | total log score |"
            " margin resid SD | total resid SD |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name in ("v6", "v5"):
        v = c[name]
        lines.append(
            f"| {name} | {v['n']} | {_f(v['brier'], 5)} | {_f(v['log_loss'])} | {_f(v['ece'])} | "
            f"{_f(v['calibration_slope'], 3)} | {_f(v['margin_log_score'])} | {_f(v['total_log_score'])} | "
            f"{_f(v['margin_resid_sd'], 2)} | {_f(v['total_resid_sd'], 2)} |"
        )
    d = c["delta_v6_minus_v5"]
    lines += [
        "",
        f"Margin MAE: v6 {_f(c['margin_mae']['v6'], 3)}, v5 {_f(c['margin_mae']['v5'], 3)}.",
        "",
        "Date-clustered bootstrap, v6 minus v5 (95% CI):",
        "",
    ]
    for key, label, better in (
        ("brier", "ML Brier", "negative = v6 better"),
        ("margin_log_score", "margin log score", "positive = v6 better"),
        ("total_log_score", "total log score", "positive = v6 better"),
        ("margin_abs_error", "margin absolute error", "negative = v6 better"),
    ):
        x = d[key]
        lines.append(
            f"- {label}: {x['point_estimate']:+.5f} [{x['ci_95_low']:+.5f}, {x['ci_95_high']:+.5f}] ({better})"
        )
    lines += [
        "",
        "| season | n | v6 Brier | v5 Brier | v6 margin LS | v5 margin LS | v6 bias | v5 bias |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s, v in c["by_season"].items():
        lines.append(
            f"| {s} | {v['n']} | {_f(v['v6_brier'], 5)} | {_f(v['v5_brier'], 5)} | {_f(v['v6_margin_log_score'])} | "
            f"{_f(v['v5_margin_log_score'])} | {_f(v['v6_margin_bias'], 2)} | {_f(v['v5_margin_bias'], 2)} |"
        )
    lines += ["", "| slice | n | v6 Brier | v5 Brier | v6 margin LS | v5 margin LS |", "|---|---|---|---|---|---|"]
    for s, v in c["by_slice"].items():
        lines.append(
            f"| {s} | {v['n']} | {_f(v['v6']['brier'], 5)} | {_f(v['v5']['brier'], 5)} | "
            f"{_f(v['v6']['margin_log_score'])} | {_f(v['v5']['margin_log_score'])} |"
        )
    for title, key in (
        ("v6-only existing events (after the v5 cache ends, or v5 has no row)", "v6_only_existing_events_block"),
        ("Backfill-only FBS-involved games (rating inputs in the fit, scored separately)", "backfill_only_fbs_involved_block"),
    ):
        b = rep[key]
        lines += ["", f"## {title}", ""]
        if b is None:
            lines.append("none")
        elif "v6" not in b:
            lines.append(f"n {b['n']} (too few to score)")
        else:
            v = b["v6"]
            lines.append(
                f"n {b['n']}: Brier {_f(v['brier'], 5)}, log loss {_f(v['log_loss'])}, ECE {_f(v['ece'])}, "
                f"slope {_f(v['calibration_slope'], 3)}, margin log score {_f(v['margin_log_score'])}, "
                f"total log score {_f(v['total_log_score'])}, margin MAE {_f(b['margin_mae'], 3)}."
            )
    lines += ["", "Counts: " + json.dumps(rep["counts"])]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("selfcheck")
    ps = sub.add_parser("snapshot")
    ps.add_argument("--no-backfill", action="store_true")
    ps.add_argument("--out", type=_abs)
    ps.add_argument("--meta", type=_abs)
    pc = sub.add_parser("cache")
    pc.add_argument("--seasons", type=int, nargs="*")
    pc.add_argument("--snapshot", type=_abs)
    pc.add_argument("--meta", type=_abs)
    pc.add_argument("--out", type=_abs)
    pf = sub.add_parser("fit")
    pf.add_argument("--cache", type=_abs)
    pf.add_argument("--train-end")
    pf.add_argument("--val-end")
    pf.add_argument("--fit-out", type=_abs)
    sub.add_parser("holdout")
    pz = sub.add_parser("freeze")
    pz.add_argument("--allow-no-backfill", action="store_true")
    sub.add_parser("verify-frozen")
    args = ap.parse_args()
    if args.cmd == "selfcheck":
        return selfcheck()
    if args.cmd == "snapshot":
        return cmd_snapshot(args)
    if args.cmd == "cache":
        return cmd_cache(args)
    if args.cmd == "fit":
        return cmd_fit(args)
    if args.cmd == "freeze":
        return cmd_freeze(args)
    if args.cmd == "holdout":
        return cmd_holdout(args)
    if args.cmd == "verify-frozen":
        verify_frozen()
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
