"""W2 line-movement tests for NCAAF totals and spreads (quick-261115-183, workstream W2).

Part A: ESPN open-to-close movement, exactly as pinned in ``outputs/ncaaf/cfb_totals_sources_rule.json``
        (``sources.W2``): ``move_total = close total - open total`` and ``move_spread = close home line -
        open home line``, merged on event_id with the lined cohort, primary statistic dMSE through
        ``cfb_totals_lib.run_primary``. ESPN open and close blocks carry no ``observed_at``, so the movement is
        only known at close and the best possible verdict is UPPER_BOUND_PASS.
Part B: 2026 intraday movement from the Polymarket-lineage ladder quotes in the runtime root
        (``odds/ncaaf/<date>/polymarket_snapshots.jsonl``; ``market_quotes.db`` is reconciled read-only). These
        are Polymarket ladder quotes, NOT ESPN lines. Verdict DESCRIPTIVE_ONLY: there is no select or
        validation data (every 2026 game is holdout or post-holdout), so nothing here can decide anything.

Part B definitions (fixed in this file before any outcome was read; ``--final`` is the only run that reads
outcomes of holdout-window games):
    * full-game markets only (market slugs ending ``-pos|neg-<n>pt<m>`` for spreads and ``-total-<n>pt<m>``
      for totals; the 1h/2h/1q-4q/team-total ladders are excluded), ``MARKET_STATE_OPEN`` only,
    * kickoff = the earlier of the ESPN base-row start and the Polymarket event start; only quotes with
      ``observed_at < kickoff`` (parsed UTC instants, strict) are used, so no post-kickoff quote enters,
    * snapshot = maximal run of one event+market quotes whose consecutive ``observed_at`` gaps are
      <= 30 s. One ladder sweep polls its rungs about 10 s apart, so a 5 s gap cut a sweep into pieces
      (checked on real data: 2,499 of 6,978 five-second groups held a single rung); the observed gap
      histogram has almost nothing between 30 s and 60 s, so 30 s is the natural break between sweeps.
      Within a snapshot the latest quote of each rung is kept,
    * main-line rung of a snapshot = the rung whose over (totals) or home-side (spreads) midpoint is nearest
      50 percent (ties: lower line); a snapshot with fewer than 2 rungs has no main rung,
    * spreads are converted to the ESPN convention (home-signed line, negative = home favoured); the side
      of the quoted team is resolved from names only (never from an ESPN line) and unresolved rows are dropped,
    * movement = last pre-kickoff main rung minus first observed main rung, defined only when the two
      snapshots are at least 1 hour apart,
    * comparison: movement vs ``actual - ESPN close`` (totals) and, for spreads, the movement of the expected
      home margin (``-(change in home line)``) vs ``home margin - ESPN expected home margin``.
Sensitivities (labelled, never primary): rungs bracketing 50 percent at both ends, books no wider than 10 points
of probability at both ends, last snapshot within 6 hours of kickoff.

Usage: python scripts/cfb_line_movement.py [--final]
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))

import cfb_totals_lib as L

OUT_JSON = L.REPO / "outputs" / "ncaaf" / "cfb_line_movement.json"
OUT_MD = L.REPO / "outputs" / "ncaaf" / "cfb_line_movement.md"

SNAPSHOT_GAP_S = 30.0
MIN_SPAN_S = 3600.0
TIGHT_WIDTH = 0.10
NEAR_KICKOFF_S = 6 * 3600.0
GATE_CORR = 0.95
GATE_MEDIAN_ABS_DIFF = 2.0
MATCH_WINDOW_S = 6 * 3600.0
MOVE_EDGES = (1.0, 3.0)
MIN_BUCKET_N = 30

SPREAD_SLUG = re.compile(r"-\d{4}-\d{2}-\d{2}-(pos|neg)-\d+pt\d+$")
TOTAL_SLUG = re.compile(r"-\d{4}-\d{2}-\d{2}-total-\d+pt\d+$")
EVENT_SLUG_FROM_MARKET = re.compile(r"^[a-z]+-(cfb-.+?-\d{4}-\d{2}-\d{2})(?:-|$)")

# Explicit alias table: raw Polymarket team name -> acceptable raw ESPN base-row names. Both sides are
# normalised by ``_norm`` below, so the keys here read as the feeds spell them.
_ALIAS_RAW: dict[str, tuple[str, ...]] = {
    "Miami (FL)": ("Miami Hurricanes",),
    "Miami (OH)": ("Miami (OH) RedHawks",),
    "LIU": ("Long Island University Sharks",),
    "Louisiana-Monroe": ("UL Monroe Warhawks",),
    "Southeastern Louisiana": ("SE Louisiana Lions",),
    "UMass": ("Massachusetts Minutemen",),
    "K-State": ("Kansas State Wildcats",),
    "LA Tech": ("Louisiana Tech Bulldogs",),
    "NM State": ("New Mexico State Aggies",),
    "St. Thomas (MN)": ("St. Thomas Tommies",),
    "Central Connecticut State": ("Central Connecticut Blue Devils",),
    "Penn": ("Pennsylvania Quakers",),
    "Hawai'i": ("Hawai'i Rainbow Warriors", "Hawaii Rainbow Warriors"),
    "Appalachian State": ("App State Mountaineers", "Appalachian State Mountaineers"),
    "San Jose State": ("San Jose State Spartans", "San Jos� State Spartans"),
    "Louisiana": ("Louisiana Ragin' Cajuns",),
    "Central State (OH)": ("Central State Marauders",),
    "Glenville State College": ("Glenville State Pioneers",),
    "Grambling State": ("Grambling Tigers",),
}


def _jd(obj: Any) -> Any:
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return None if np.isnan(obj) else float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    raise TypeError(f"not JSON serialisable: {type(obj)}")


# --------------------------------------------------------------------------- shared movement-vs-outcome
def move_vs_outcome(d: pd.DataFrame, move: str, y: str) -> dict[str, Any]:
    """Exploratory movement-vs-outcome statistics on rows with non-missing ``move`` and ``y`` columns.

    ``y`` is the realised outcome minus the close. corr and slope use every row (zero movement included);
    continuation (outcome beyond the close in the direction of the movement) uses non-zero movement only.
    Date-clustered 95% CIs. Nothing here is a verdict input.
    """
    d = d[d[move].notna() & d[y].notna()].reset_index(drop=True)
    res: dict[str, Any] = {"n": len(d), "n_nonzero_move": int((d[move] != 0).sum())}
    if len(d) < MIN_BUCKET_N:
        res["note"] = f"fewer than {MIN_BUCKET_N} rows; no statistics"
        return res
    mv = d[move].to_numpy(float)
    res["mean_move"] = float(mv.mean())
    res["mean_abs_move"] = float(np.abs(mv).mean())
    res["zero_move_share"] = float((mv == 0).mean())
    if np.ptp(mv) > 0:

        def stat(dd: pd.DataFrame, idx: np.ndarray) -> list[float]:
            m = dd[move].to_numpy(float)[idx]
            v = dd[y].to_numpy(float)[idx]
            var = m.var()
            slope = float(((m - m.mean()) * (v - v.mean())).mean() / var) if var > 0 else float("nan")
            return [L.corr(m, v), slope]

        point, lo, hi = L.cboot(d, stat)
        res["corr_move_outcome"] = {"point": float(point[0]), "lo": float(lo[0]), "hi": float(hi[0])}
        res["slope_outcome_per_point_move"] = {
            "point": float(point[1]),
            "lo": float(lo[1]),
            "hi": float(hi[1]),
        }
    nz = d[d[move] != 0].reset_index(drop=True)
    if len(nz) >= MIN_BUCKET_N:
        s = np.sign(nz[move].to_numpy(float))
        v = nz[y].to_numpy(float)
        res["mean_outcome_in_move_direction"] = L.mean_ci(v * s, nz["date"].to_numpy())
        dec = v != 0
        hit = (np.sign(v[dec]) == s[dec]).astype(float)
        res["continuation_share"] = {
            **L.mean_ci(hit, nz["date"].to_numpy()[dec]),
            "n_decided": int(dec.sum()),
        }
        a = np.abs(nz[move].to_numpy(float))
        edges = (0.0, *MOVE_EDGES, np.inf)
        buckets: dict[str, Any] = {}
        for lo_e, hi_e in pairwise(edges):
            sel = (a > lo_e) & (a <= hi_e)
            label = f"({lo_e:g},{hi_e:g}]" if np.isfinite(hi_e) else f">{lo_e:g}"
            if sel.sum() >= MIN_BUCKET_N:
                buckets[label] = {
                    "n": int(sel.sum()),
                    "mean_outcome_in_move_direction": L.mean_ci(v[sel] * s[sel], nz["date"].to_numpy()[sel]),
                    "continuation_share": float((np.sign(v[sel & dec]) == s[sel & dec]).mean()),
                }
            else:
                buckets[label] = {"n": int(sel.sum()), "note": f"fewer than {MIN_BUCKET_N} rows"}
        res["size_buckets_abs_move"] = buckets
        up = s > 0
        res["by_direction"] = {
            name: {
                "n": int(sel.sum()),
                "mean_outcome_in_move_direction": (
                    L.mean_ci(v[sel] * s[sel], nz["date"].to_numpy()[sel])
                    if sel.sum() >= MIN_BUCKET_N
                    else None
                ),
            }
            for name, sel in (("move_up", up), ("move_down", ~up))
        }
    return res


# --------------------------------------------------------------------------- Part A: ESPN open to close
def build_espn_frame(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    recs = [L.espn_open_close(r) for r in L.iter_espn_lines()]
    m = pd.DataFrame(recs)
    if not m["event_id"].is_unique:
        raise SystemExit("espn_lines holds duplicate event_id rows")
    for c in ("open_total", "close_total", "open_spread", "close_spread"):
        m[c] = pd.to_numeric(m[c], errors="coerce")
    m["move_total"] = m["close_total"] - m["open_total"]
    m["mv_close_total"] = m["close_total"]
    m["move_spread"] = m["close_spread"] - m["open_spread"]
    m["mv_close_margin"] = -m["close_spread"]
    m.loc[m["open_spread"].isna() | m["close_spread"].isna(), "mv_close_margin"] = np.nan
    m.loc[m["open_total"].isna() | m["close_total"].isna(), "mv_close_total"] = np.nan
    keep = m[["event_id", "move_total", "mv_close_total", "move_spread", "mv_close_margin"]]
    out = df.merge(keep, on="event_id", how="left", validate="one_to_one")
    # the movement cohort is a subset of the lined cohort: rows without the selected close are dropped
    out.loc[out["line"].isna(), ["move_total", "mv_close_total"]] = np.nan
    out.loc[out["spread_home"].isna(), ["move_spread", "mv_close_margin"]] = np.nan
    audit = {
        "espn_events": len(m),
        "initial_spread_sign_conflicts_total": int(m["initial_spread_sign_conflicts"].sum()),
        "initial_spread_sign_conflicts_note": (
            "provider rows whose initial_spread sign disagrees with fav_side, counted over every event; "
            "reported, never resolved (the rule pins the stored home-signed value)"
        ),
        "events_with_open_total": int(m["open_total"].notna().sum()),
        "events_with_open_spread": int(m["open_spread"].notna().sum()),
    }
    return out, audit


def season_coverage(out: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for season, g in out.groupby("season"):
        lt = g[g["line"].notna()]
        ls = g[g["spread_home"].notna()]
        rows.append(
            {
                "season": int(season),
                "games": len(g),
                "total_lined": len(lt),
                "total_with_open": int(lt["move_total"].notna().sum()),
                "total_nonzero_move": int((lt["move_total"].fillna(0) != 0).sum()),
                "spread_lined": len(ls),
                "spread_with_open": int(ls["move_spread"].notna().sum()),
                "spread_nonzero_move": int((ls["move_spread"].fillna(0) != 0).sum()),
                "first_date_with_open_total": (
                    str(lt.loc[lt["move_total"].notna(), "date"].min())
                    if lt["move_total"].notna().any()
                    else None
                ),
            }
        )
    return rows


def cohort_sizes(out: pd.DataFrame) -> dict[str, Any]:
    res: dict[str, Any] = {}
    for market, col, cl in (("total", "move_total", "line"), ("spread", "move_spread", "spread_home")):
        res[market] = {}
        for w in (*L.WINDOWS, "post_holdout"):
            g = out[out["window"] == w]
            lined_n = int(g[cl].notna().sum())
            has = g[col].notna()
            res[market][w] = {
                "lined": lined_n,
                "with_open": int(has.sum()),
                "nonzero_move": int((g.loc[has, col] != 0).sum()),
                "open_coverage_of_lined": (float(has.sum() / lined_n) if lined_n else None),
            }
    return res


def espn_exploratory(out: pd.DataFrame, windows: tuple[str, ...]) -> dict[str, Any]:
    """Exploratory, labelled: movement vs (actual - movement-cohort close) per window."""
    res: dict[str, Any] = {"label": "EXPLORATORY: not a verdict input"}
    d = out.copy()
    d["tot_resid"] = d["actual"] - d["mv_close_total"]
    d["exp_margin_move"] = -d["move_spread"]
    d["margin_resid"] = d["margin"] - d["mv_close_margin"]
    for w in windows:
        g = d[d["window"] == w]
        res[w] = {
            "total": move_vs_outcome(g, "move_total", "tot_resid"),
            "spread_expected_home_margin_move": move_vs_outcome(g, "exp_margin_move", "margin_resid"),
        }
    return res


def refit_dmse(fit: pd.DataFrame, ev: pd.DataFrame, market: str) -> dict[str, Any]:
    """EXPLORATORY, not pinned: dMSE of line+movement over line-only, coefficients fit on ``fit`` rows and
    evaluated on ``ev`` rows. Needed because the pinned select window carries no open values, so the pinned
    primary statistic is untestable; this never decides a verdict."""
    close, y, move = {
        "total": ("mv_close_total", "actual", "move_total"),
        "spread": ("mv_close_margin", "margin", "move_spread"),
    }[market]
    cols = [close, y, move]
    f = fit[fit[cols].notna().all(axis=1)]
    e = ev[ev[cols].notna().all(axis=1)]
    res: dict[str, Any] = {"n_fit": len(f), "n_eval": len(e)}
    if len(f) < L.MIN_VALIDATION_N or len(e) < MIN_BUCKET_N:
        res["note"] = "too few rows"
        return res
    base = L.ols_fit([f[close].to_numpy(float)], f[y].to_numpy(float))
    src = L.ols_fit([f[close].to_numpy(float), f[move].to_numpy(float)], f[y].to_numpy(float))
    ce, ye, me = (e[c].to_numpy(float) for c in (close, y, move))
    gain = (ye - L.ols_predict(base, [ce])) ** 2 - (ye - L.ols_predict(src, [ce, me])) ** 2
    samples = L.boot_mean_samples(gain, e["date"].to_numpy())
    lo, hi = L.ci95(samples)
    res.update(
        {
            "baseline_coef": [float(x) for x in base],
            "source_coef": [float(x) for x in src],
            "dmse": float(gain.mean()),
            "ci_lo": float(lo),
            "ci_hi": float(hi),
        }
    )
    return res


def espn_refit(out: pd.DataFrame, final: bool) -> dict[str, Any]:
    res: dict[str, Any] = {
        "label": (
            "EXPLORATORY and NOT the pinned primary statistic: the pinned select window (<= 2022-11-11) has "
            "no open values, so the pinned test is untestable; coefficients here are fit on validation rows "
            "that carry open values. Never a verdict input."
        )
    }
    v = out[out["window"] == "validation"]
    for market in ("total", "spread"):
        res[market] = {
            "fit_validation_through_2023_eval_validation_2024": refit_dmse(
                v[v["date"] <= "2023-12-31"], v[v["date"] >= "2024-01-01"], market
            )
        }
        if final:
            res[market]["fit_all_validation_eval_holdout"] = refit_dmse(
                v, out[out["window"] == "holdout"], market
            )
    return res


# --------------------------------------------------------------------------- Part B: Polymarket ladders
def _norm(s: str) -> str:
    s = s.casefold().replace("&", "and")
    s = re.sub(r"\bst\.", "st", s)
    s = re.sub(r"\bstate\b", "st", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return " ".join(s.split())


ALIAS: dict[str, tuple[str, ...]] = {_norm(k): tuple(_norm(x) for x in v) for k, v in _ALIAS_RAW.items()}


def _name_ok(pm: str, espn: str) -> bool:
    if pm in ALIAS:
        return espn in ALIAS[pm]
    return espn == pm or espn.startswith(pm + " ")


def _epoch(series: pd.Series) -> np.ndarray:
    ts = pd.to_datetime(series, utc=True, format="ISO8601")
    return ((ts - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds()).to_numpy(float)


def load_ladders() -> tuple[pd.DataFrame, dict[str, Any]]:
    from model_prediction.runtime_paths import RuntimePaths

    rp = RuntimePaths.resolve(require_external_runtime=True)
    odds_dir = rp.data_root / "odds" / "ncaaf"
    files = sorted(odds_dir.glob("*/polymarket_snapshots.jsonl"))
    seen: set[tuple[str, str]] = set()
    recs: list[dict[str, Any]] = []
    n_raw = n_dup = 0
    slug_keys: set[tuple[str, str, str]] = set()
    for f in files:
        with f.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                r = json.loads(line)
                n_raw += 1
                key = (str(r.get("market_id")), str(r.get("observed_at_utc")))
                if key in seen:
                    n_dup += 1
                    continue
                seen.add(key)
                slug = r.get("event_slug")
                if not slug:
                    mm = EVENT_SLUG_FROM_MARKET.match(str(r.get("market_slug") or ""))
                    slug = mm.group(1) if mm else None
                if slug:
                    slug_keys.add((slug, str(r["market_type"]), str(r["observed_at_utc"])))
                recs.append(
                    {
                        "pm_event": str(r["event_id"]),
                        "title": r.get("event_title"),
                        "pm_start": r.get("event_start_utc"),
                        "market_type": r["market_type"],
                        "market_slug": str(r.get("market_slug") or ""),
                        "state": r.get("market_state"),
                        "obs": r["observed_at_utc"],
                        "team": r.get("team"),
                        "line": r.get("line"),
                        "l_desc": (r.get("long") or {}).get("description"),
                        "l_mid": (r.get("long") or {}).get("midpoint"),
                        "s_mid": (r.get("short") or {}).get("midpoint"),
                        "l_bid": (r.get("long") or {}).get("bid"),
                        "l_ask": (r.get("long") or {}).get("ask"),
                    }
                )
    p = pd.DataFrame(recs)
    info: dict[str, Any] = {
        "source": "runtime root odds/ncaaf/<date>/polymarket_snapshots.jsonl (Polymarket-lineage ladder quotes)",
        "files": len(files),
        "rows_raw": n_raw,
        "rows_duplicate_dropped": n_dup,
        "rows": len(p),
    }
    # market_quotes.db: reconciliation only (read-only; explicit columns, no price fields)
    db_path = rp.market_quotes_db
    uri = f"file:{db_path.as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    try:
        cur = con.execute(
            "SELECT event_id, market_type, observed_at_utc FROM market_quotes WHERE sport = 'ncaaf'"
        )
        dbkeys = Counter((e, m, o) for e, m, o in cur)
    finally:
        con.close()
    missing = [k for k in dbkeys if k not in slug_keys]
    info["market_quotes_db_reconciliation"] = {
        "role": (
            "reconciliation only: the DB carries no market slug and no team, so it cannot separate full-game "
            "from quarter/half ladders or orient spreads; it is not used for any number below"
        ),
        "db_distinct_keys": len(dbkeys),
        "db_keys_present_in_jsonl": len(dbkeys) - len(missing),
        "db_keys_absent_from_jsonl": len(missing),
        "db_absent_by_date_market": dict(Counter(f"{k[2][:10]} {k[1]}" for k in missing)),
    }
    return p, info


def match_events(p: pd.DataFrame, df: pd.DataFrame) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Two-sided, 1:1 match of Polymarket events (title 'Away vs. Home') to 2026 base rows."""
    g = df[df["season"] == 2026].copy()
    g["away_n"] = g["away"].map(_norm)
    g["home_n"] = g["home"].map(_norm)
    last_base = g["t_s"].max()
    ev = p.dropna(subset=["title"]).drop_duplicates("pm_event")[["pm_event", "title", "pm_start"]]
    ev_start = _epoch(ev["pm_start"])
    matched: dict[str, dict[str, Any]] = {}
    cls: Counter[str] = Counter()
    by_base: dict[str, list[str]] = defaultdict(list)
    for (pm_event, title, _), start in zip(ev.itertuples(index=False, name=None), ev_start, strict=True):
        if " vs. " not in title:
            cls["title_unparsed"] += 1
            continue
        a, b = (_norm(x) for x in title.split(" vs. ", 1))
        near = g[(g["t_s"] - start).abs() <= MATCH_WINDOW_S]
        hits = [r for r in near.itertuples() if _name_ok(a, r.away_n) and _name_ok(b, r.home_n)]
        if len(hits) == 1:
            r = hits[0]
            matched[pm_event] = {"eid": r.event_id, "away_n": r.away_n, "home_n": r.home_n, "title": title}
            by_base[r.event_id].append(pm_event)
        elif len(hits) > 1:
            cls["ambiguous_multiple_base_rows"] += 1
        elif start > last_base:
            cls["after_last_base_row"] += 1
        elif any(_name_ok(a, r.away_n) or _name_ok(b, r.home_n) for r in near.itertuples()):
            cls["name_mismatch_one_side_found"] += 1
        else:
            cls["no_base_row"] += 1
    dup = {e for e, lst in by_base.items() if len(lst) > 1}
    for e in dup:
        for pm_event in by_base[e]:
            matched.pop(pm_event, None)
        cls["duplicate_polymarket_events_dropped"] += len(by_base[e])
    assert len({v["eid"] for v in matched.values()}) == len(matched), "match is not 1:1"
    info = {
        "polymarket_events_with_title": len(ev),
        "matched_1to1": len(matched),
        "unmatched_classification": dict(cls),
        "alias_table_entries": len(ALIAS),
        "match_rule": (
            "both team names must match (exact or word-boundary prefix of the ESPN name, or an explicit "
            "alias) and the kickoffs must be within 6 hours; exactly one base row must qualify"
        ),
    }
    return matched, info


def _spread_side(team: str, m: dict[str, Any]) -> str | None:
    t = _norm(team)
    title_a, title_h = (_norm(x) for x in m["title"].split(" vs. ", 1))

    def is_side(title_name: str, espn_name: str) -> bool:
        return (
            _name_ok(t, espn_name)
            or _name_ok(title_name, t)
            or t == title_name
            or espn_name.endswith(" " + t)
        )

    away = is_side(title_a, m["away_n"])
    home = is_side(title_h, m["home_n"])
    if away == home:
        return None
    return "away" if away else "home"


def build_snapshots(p: pd.DataFrame, matched: dict[str, dict[str, Any]], df: pd.DataFrame) -> pd.DataFrame:
    """One row per (matched event, market) with first/last main-rung data. Pre-kickoff quotes only."""
    start_by_eid = df.set_index("event_id")["t_s"].to_dict()
    q = p[p["pm_event"].isin(matched) & (p["state"] == "MARKET_STATE_OPEN")].copy()
    q["t"] = _epoch(q["obs"])
    pm_start_by_event = {
        e: s
        for e, s in zip(
            p.dropna(subset=["pm_start"]).drop_duplicates("pm_event")["pm_event"],
            _epoch(p.dropna(subset=["pm_start"]).drop_duplicates("pm_event")["pm_start"]),
            strict=True,
        )
    }
    kick = {
        e: min(float(start_by_eid[m["eid"]]), pm_start_by_event.get(e, np.inf)) for e, m in matched.items()
    }
    q["kick"] = q["pm_event"].map(kick)
    stats: dict[str, Any] = {"quotes_open_matched": len(q)}
    q = q[q["t"] < q["kick"]]  # strict: nothing at or after kickoff
    stats["quotes_pre_kickoff"] = len(q)
    is_sp = (q["market_type"] == "spread") & q["market_slug"].map(lambda s: bool(SPREAD_SLUG.search(s)))
    is_tot = (q["market_type"] == "total") & q["market_slug"].map(lambda s: bool(TOTAL_SLUG.search(s)))
    stats["quotes_full_game_spread"] = int(is_sp.sum())
    stats["quotes_full_game_total"] = int(is_tot.sum())
    side_cache: dict[tuple[str, str], str | None] = {}
    rungs: dict[tuple[str, str], list[tuple[float, float, float, float]]] = defaultdict(list)
    unresolved = 0
    resolved_side: Counter[str] = Counter()
    for r in q[is_tot | is_sp].itertuples(index=False):
        if r.l_mid is None or r.line is None or pd.isna(r.l_mid) or pd.isna(r.line):
            continue
        width = (
            float(r.l_ask) - float(r.l_bid)
            if r.l_ask is not None and r.l_bid is not None and not pd.isna(r.l_ask) and not pd.isna(r.l_bid)
            else np.nan
        )
        if r.market_type == "total":
            if r.l_desc != "Over":
                continue
            rungs[(r.pm_event, "total")].append((r.t, float(r.line), float(r.l_mid), width))
            continue
        team = str(r.team or "")
        key = (r.pm_event, team)
        if key not in side_cache:
            side_cache[key] = _spread_side(team, matched[r.pm_event])
        side = side_cache[key]
        if side is None or r.s_mid is None or pd.isna(r.s_mid):
            unresolved += 1
            continue
        resolved_side[side] += 1
        if side == "away":
            # team is away with signed line x (team gets x points): home line = -x, home-side midpoint = other side
            rungs[(r.pm_event, "spread")].append((r.t, -float(r.line), float(r.s_mid), width))
        else:
            rungs[(r.pm_event, "spread")].append((r.t, float(r.line), float(r.l_mid), width))
    stats["spread_rows_side_unresolved_dropped"] = unresolved
    stats["spread_rows_resolved_side"] = dict(resolved_side)

    out: list[dict[str, Any]] = []
    snap_counts: Counter[str] = Counter()
    for (pm_event, market), lst in rungs.items():
        lst.sort(key=lambda x: x[0])
        snaps: list[list[tuple[float, float, float, float]]] = []
        cur: list[tuple[float, float, float, float]] = []
        last_t = None
        for item in lst:
            if last_t is not None and item[0] - last_t > SNAPSHOT_GAP_S:
                snaps.append(cur)
                cur = []
            cur.append(item)
            last_t = item[0]
        if cur:
            snaps.append(cur)
        valid: list[dict[str, Any]] = []
        for s in snaps:
            latest: dict[float, tuple[float, float, float, float]] = {}
            for item in s:
                latest[item[1]] = item
            if len(latest) < 2:
                snap_counts["single_rung_snapshots_excluded"] += 1
                continue
            items = sorted(latest.values(), key=lambda x: (abs(x[2] - 0.5), x[1]))
            best = items[0]
            mids = [x[2] for x in latest.values()]
            lines = [x[1] for x in latest.values()]
            valid.append(
                {
                    "t": max(x[0] for x in s),
                    "line": best[1],
                    "mid": best[2],
                    "width": best[3],
                    "n_rungs": len(latest),
                    "bracketed": min(mids) <= 0.5 <= max(mids),
                    "at_edge": best[1] in (min(lines), max(lines)),
                }
            )
            snap_counts["multi_rung_snapshots"] += 1
        row: dict[str, Any] = {
            "pm_event": pm_event,
            "eid": matched[pm_event]["eid"],
            "market": market,
            "kick": kick[pm_event],
            "n_snapshots": len(valid),
            "move_defined": False,
        }
        if len(valid) >= 2:
            a, b = valid[0], valid[-1]
            row.update(
                {
                    "first_line": a["line"],
                    "last_line": b["line"],
                    "move_line": b["line"] - a["line"],
                    "first_lead_h": (row["kick"] - a["t"]) / 3600,
                    "last_lead_h": (row["kick"] - b["t"]) / 3600,
                    "span_h": (b["t"] - a["t"]) / 3600,
                    "first_bracketed": a["bracketed"],
                    "last_bracketed": b["bracketed"],
                    "first_width": a["width"],
                    "last_width": b["width"],
                    "first_at_edge": a["at_edge"],
                    "last_at_edge": b["at_edge"],
                    "move_defined": (b["t"] - a["t"]) >= MIN_SPAN_S,
                }
            )
        out.append(row)
    res = pd.DataFrame(out)
    res.attrs["stats"] = {**stats, **dict(snap_counts)}
    return res


def intraday_frame(ev: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    """Join the event/market rows to the base frame (lines, windows, outcomes are carried, not computed)."""
    cols = ["event_id", "date", "window", "line", "spread_home", "close_margin", "actual", "margin"]
    m = ev.merge(df[cols], left_on="eid", right_on="event_id", how="left", validate="many_to_one")
    espn_close = np.where(m["market"] == "total", m["line"], m["spread_home"])
    m["espn_close_line"] = espn_close
    m["move_expected"] = np.where(m["market"] == "total", m["move_line"], -m["move_line"])
    m["pm_minus_espn"] = m["last_line"] - m["espn_close_line"]
    return m


def _gate_stats(g: pd.DataFrame) -> dict[str, Any]:
    """Line-only agreement of the last pre-kickoff main rung with the ESPN selected close (no outcomes)."""
    g = g[g["espn_close_line"].notna() & g["last_line"].notna()]
    if len(g) < MIN_BUCKET_N:
        return {"n": len(g), "passed": False, "note": "too few rows"}
    c = L.corr(g["last_line"].to_numpy(float), g["espn_close_line"].to_numpy(float))
    diff = (g["last_line"] - g["espn_close_line"]).to_numpy(float)
    return {
        "n": len(g),
        "corr_last_rung_vs_espn_close": c,
        "median_abs_diff_points": float(np.median(np.abs(diff))),
        "mean_diff_points": float(diff.mean()),
        "share_within_1_point": float((np.abs(diff) <= 1.0).mean()),
        "passed": bool(c >= GATE_CORR and np.median(np.abs(diff)) <= GATE_MEDIAN_ABS_DIFF),
    }


def agreement_gate(m: pd.DataFrame) -> dict[str, Any]:
    """Per market and per definition (primary and each labelled sensitivity), over every event with a
    defined movement. The gate is fixed before any outcome is read; an outcome comparison is only produced
    for a definition whose own gate passes."""
    res: dict[str, Any] = {
        "thresholds": {"corr_min": GATE_CORR, "median_abs_diff_max": GATE_MEDIAN_ABS_DIFF},
        "scope": "events with a defined movement; the last snapshot's main rung vs the ESPN selected close",
    }
    for market in ("total", "spread"):
        d = m[(m["market"] == market) & m["move_defined"]]
        res[market] = {name: _gate_stats(d[mask]) for name, mask in sensitivity_masks(d).items()}
    return res


def movement_distribution(m: pd.DataFrame) -> dict[str, Any]:
    res: dict[str, Any] = {}
    for market in ("total", "spread"):
        g = m[m["market"] == market]
        d = g[g["move_defined"]]
        entry: dict[str, Any] = {
            "event_market_rows": len(g),
            "with_ge2_snapshots": int((g["n_snapshots"] >= 2).sum()),
            "move_defined": len(d),
        }
        if len(d):
            mv = d["move_line"].to_numpy(float)
            entry.update(
                {
                    "mean_move_points": float(mv.mean()),
                    "mean_abs_move_points": float(np.abs(mv).mean()),
                    "median_abs_move_points": float(np.median(np.abs(mv))),
                    "zero_move_share": float((mv == 0).mean()),
                    "abs_move_quantiles_p50_p90_p99": [
                        float(x) for x in np.quantile(np.abs(mv), [0.5, 0.9, 0.99])
                    ],
                    "median_span_hours": float(d["span_h"].median()),
                    "median_first_lead_hours": float(d["first_lead_h"].median()),
                    "last_lead_hours_p10_p50_p90": [
                        float(x) for x in np.quantile(d["last_lead_h"].to_numpy(float), [0.1, 0.5, 0.9])
                    ],
                    "share_last_snapshot_within_6h": float(
                        (d["last_lead_h"] * 3600 <= NEAR_KICKOFF_S).mean()
                    ),
                    "width_first_median": float(np.nanmedian(d["first_width"].to_numpy(float))),
                    "width_last_median": float(np.nanmedian(d["last_width"].to_numpy(float))),
                    "share_first_bracketed": float(d["first_bracketed"].mean()),
                    "share_last_bracketed": float(d["last_bracketed"].mean()),
                    "share_first_at_ladder_edge": float(d["first_at_edge"].mean()),
                    "share_last_at_ladder_edge": float(d["last_at_edge"].mean()),
                }
            )
        res[market] = entry
    return res


def sensitivity_masks(d: pd.DataFrame) -> dict[str, pd.Series]:
    return {
        "primary_all_defined": pd.Series(True, index=d.index),
        "both_ends_bracket_50pct": d["first_bracketed"].astype(bool) & d["last_bracketed"].astype(bool),
        "both_ends_width_le_10pts": (d["first_width"] <= TIGHT_WIDTH) & (d["last_width"] <= TIGHT_WIDTH),
        "last_snapshot_within_6h": d["last_lead_h"] * 3600 <= NEAR_KICKOFF_S,
    }


def intraday_outcomes(m: pd.DataFrame, windows: tuple[str, ...]) -> dict[str, Any]:
    """Movement vs outcome minus ESPN close for events in ``windows`` (--final only for real data).

    Every definition is reported; one whose line-only agreement gate fails on the evaluated rows is flagged
    ``gate_failed`` (read it as unreliable, never as suppressed).
    """
    res: dict[str, Any] = {"label": "DESCRIPTIVE ONLY: Polymarket-lineage ladder quotes, not ESPN lines"}
    for market in ("total", "spread"):
        d = m[(m["market"] == market) & m["move_defined"] & m["window"].isin(windows)].copy()
        if market == "total":
            d["y"] = d["actual"] - d["line"]
        else:
            d["y"] = d["margin"] - d["close_margin"]
        entry: dict[str, Any] = {}
        for name, mask in sensitivity_masks(d).items():
            sub = d[mask]
            gate = _gate_stats(sub)
            label = "primary definition" if name.startswith("primary") else "SENSITIVITY (labelled)"
            entry[name] = {
                "label": label,
                "gate": gate,
                "gate_failed": not gate["passed"],
                "gate_note": (
                    None
                    if gate["passed"]
                    else "GATE FAILED: the last main rung does not track the ESPN close here (the ladder "
                    "often does not bracket 50%), so read this comparison as unreliable"
                ),
                "pm_last_minus_first_vs_outcome_minus_espn_close": move_vs_outcome(sub, "move_expected", "y"),
            }
        res[market] = entry
    return res


def _selftest_intraday() -> None:
    """Exercise intraday_outcomes on synthetic rows so --final cannot fail on a code path never run."""
    rng = np.random.default_rng(7)
    n = 240
    rows = []
    for i in range(n):
        market = "total" if i % 2 == 0 else "spread"
        move = float(rng.choice([-3.0, -1.0, 0.0, 1.0, 3.0]))
        last = 50.0 + float(rng.normal(0, 5))
        rows.append(
            {
                "market": market,
                "move_defined": True,
                "window": "holdout",
                "date": f"2026-10-{1 + i % 25:02d}",
                "move_line": move,
                "move_expected": move if market == "total" else -move,
                "last_line": last,
                "espn_close_line": last + rng.normal(0, 0.3),
                "line": 50.0,
                "close_margin": 3.0,
                "actual": 50.0 + rng.normal(0, 14),
                "margin": 3.0 + rng.normal(0, 14),
                "first_bracketed": True,
                "last_bracketed": True,
                "first_width": 0.05,
                "last_width": 0.05,
                "last_lead_h": 1.0,
            }
        )
    m = pd.DataFrame(rows)
    bad = m.copy()
    bad["espn_close_line"] = bad["espn_close_line"].sample(frac=1.0, random_state=1).to_numpy()
    assert intraday_outcomes(bad, ("holdout",))["total"]["primary_all_defined"]["gate_failed"]
    out = intraday_outcomes(m, ("holdout",))
    assert out["total"]["primary_all_defined"]["pm_last_minus_first_vs_outcome_minus_espn_close"]["n"] > 0
    assert (
        "corr_move_outcome"
        in out["spread"]["primary_all_defined"]["pm_last_minus_first_vs_outcome_minus_espn_close"]
    )


def intraday_part(df: pd.DataFrame, final: bool) -> dict[str, Any]:
    p, load_info = load_ladders()
    matched, match_info = match_events(p, df)
    ev = build_snapshots(p, matched, df)
    snap_stats = ev.attrs.get("stats", {})
    m = intraday_frame(ev, df)
    gate = agreement_gate(m)
    base26 = df[df["season"] == 2026]
    defined = m[m["move_defined"]]
    coverage = {
        "base_rows_2026": len(base26),
        "base_rows_2026_total_lined": int(base26["line"].notna().sum()),
        "base_rows_2026_spread_lined": int(base26["spread_home"].notna().sum()),
        "base_rows_by_window": base26["window"].value_counts().to_dict(),
        "matched_events": len(matched),
        "movement_defined_by_market_and_window": {
            f"{mk}|{w}": int(((defined["market"] == mk) & (defined["window"] == w)).sum())
            for mk in ("total", "spread")
            for w in (*L.WINDOWS, "post_holdout")
        },
        "movement_defined_and_espn_lined_holdout": {
            mk: int(
                (
                    (defined["market"] == mk)
                    & (defined["window"] == "holdout")
                    & defined["espn_close_line"].notna()
                ).sum()
            )
            for mk in ("total", "spread")
        },
        "post_holdout_games_excluded_from_outcome_comparison": int(
            (defined["window"] == "post_holdout").sum()
        ),
    }
    out: dict[str, Any] = {
        "tier": "descriptive",
        "definition": (
            "main-line rung = ladder rung whose over (totals) or home-side (spreads) midpoint is nearest 50 "
            "percent at each snapshot. These are Polymarket-lineage ladder quotes, NOT ESPN lines. Movement = "
            "last pre-kickoff main rung minus first observed main rung."
        ),
        "pinned_parameters": {
            "snapshot_gap_seconds": SNAPSHOT_GAP_S,
            "min_span_hours": MIN_SPAN_S / 3600,
            "tight_width": TIGHT_WIDTH,
            "near_kickoff_hours": NEAR_KICKOFF_S / 3600,
            "agreement_gate": {"corr_min": GATE_CORR, "median_abs_diff_max": GATE_MEDIAN_ABS_DIFF},
            "market_state": "MARKET_STATE_OPEN",
            "kickoff": "earlier of the ESPN base-row start and the Polymarket event start; observed_at < kickoff",
        },
        "load": load_info,
        "event_match": match_info,
        "quote_filtering": snap_stats,
        "coverage": coverage,
        "movement_distribution": movement_distribution(m),
        "agreement_with_espn_close": gate,
        "outcome_comparison": None,
    }
    if final:
        out["outcome_comparison"] = intraday_outcomes(m, ("holdout",))
    else:
        out["outcome_comparison_note"] = (
            "not computed without --final: every matched 2026 game is holdout or post-holdout"
        )
    return out


# --------------------------------------------------------------------------- report
def _pct(x: float | None, nd: int = 1) -> str:
    return "n/a" if x is None else f"{100 * x:.{nd}f}%"


def _num(x: Any, nd: int = 3) -> str:
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{nd}f}"


def _ci(c: dict[str, Any] | None, nd: int = 3) -> str:
    if not c:
        return "n/a"
    return f"{_num(c.get('point'), nd)} [{_num(c.get('lo'), nd)}, {_num(c.get('hi'), nd)}]"


def render_md(o: dict[str, Any]) -> str:
    lines: list[str] = []
    w = lines.append
    w("# NCAAF line movement (W2): ESPN open-to-close and 2026 intraday ladders")
    w("")
    w(
        f"- generated_at_utc: `{o['generated_at_utc']}`; final run: `{o['final']}`; holdout_reads: `{o['holdout_reads']}`"
    )
    if o.get("holdout_note"):
        w(f"- {o['holdout_note']}")
    w(f"- rule_sha256: `{o['rule_sha256']}`; script sha256 (LF-normalised): `{o['script_sha256']}`")
    vtxt = ", ".join(f"{k}={v['verdict']}" for k, v in o["verdicts"].items())
    w(f"- verdicts: {vtxt}")
    w("")
    a = o["espn_open_close"]
    w("## Part A: ESPN open-to-close movement (untimestamped, close-time values)")
    w("")
    w(
        "ESPN open and close blocks carry no `observed_at`: the movement is only known at close, so the best "
        "possible verdict is UPPER_BOUND_PASS and nothing here can enter v6.1. Pinned definitions: "
        "`move_total = close total - open total`; `move_spread = close home line - open home line`; "
        "open = median over eligible non-live providers of the open value (Opening provider 23 contributes its "
        "own current line in 2016-2017); Caesars-family providers dropped from open and close in 2017-2018."
    )
    w("")
    w(
        f"Initial-spread sign conflicts vs fav_side (reported, not resolved): {a['audit']['initial_spread_sign_conflicts_total']}."
    )
    w("")
    w("### Coverage of open values by season (lined cohort)")
    w("")
    w(
        "| season | games | total lined | total with open | total non-zero move | spread lined | spread with open | spread non-zero move |"
    )
    w("|---|---|---|---|---|---|---|---|")
    for r in a["coverage_by_season"]:
        w(
            f"| {r['season']} | {r['games']} | {r['total_lined']} | {r['total_with_open']} | "
            f"{r['total_nonzero_move']} | {r['spread_lined']} | {r['spread_with_open']} | {r['spread_nonzero_move']} |"
        )
    w("")
    w("### Movement cohort by window")
    w("")
    w("| market | window | lined | with open | non-zero move | open coverage |")
    w("|---|---|---|---|---|---|")
    for mk in ("total", "spread"):
        for win, c in a["cohort_by_window"][mk].items():
            w(
                f"| {mk} | {win} | {c['lined']} | {c['with_open']} | {c['nonzero_move']} | {_pct(c['open_coverage_of_lined'])} |"
            )
    w("")
    w("### Primary statistic (dMSE = MSE(line only) - MSE(line + movement), positive = movement helps)")
    w("")
    w("| market | n select | n validation | validation dMSE [95% CI] | holdout dMSE (point) | verdict |")
    w("|---|---|---|---|---|---|")
    for mk in ("total", "spread"):
        r = a["primary"][mk]
        res = r["result"]
        v = res["validation"]
        vtxt = "n/a" if not v else f"{_num(v['dmse'], 4)} [{_num(v['ci_lo'], 4)}, {_num(v['ci_hi'], 4)}]"
        h = res["holdout"]
        htxt = "unread (no --final)" if not o["final"] else ("n/a" if not h else _num(h["dmse"], 4))
        if not r["interpretable"]:
            vtxt = f"n/a (select n = {res['n_select']}; not interpretable)"
            htxt = "n/a (not interpretable)"
        w(
            f"| {mk} | {res['n_select']} | {res['n_validation']} | {vtxt} | {htxt} | {r['verdict']['verdict']} |"
        )
    w("")
    for mk in ("total", "spread"):
        res = a["primary"][mk]["result"]
        w(
            f"- {mk}: baseline coef {res['baseline_coef']}, with-source coef {res['source_coef']}, "
            f"validation MSE baseline {_num((res['validation'] or {}).get('mse_baseline'), 3)} vs "
            f"with movement {_num((res['validation'] or {}).get('mse_with_source'), 3)}; "
            f"n holdout {res['n_holdout']}; reason: {a['primary'][mk]['verdict'].get('reason')}"
        )
    if any(a["primary"][mk]["result"]["n_select"] < L.MIN_SELECT_N for mk in ("total", "spread")):
        w("")
        w(
            "NOT INTERPRETABLE: the pinned select window (<= 2022-11-11) carries almost no ESPN open values, so "
            "the baseline and with-source coefficients above were fit on a handful of rows and the dMSE is "
            "meaningless. The verdict is INSUFFICIENT_DATA as the pinned rule requires; the rule was not altered. "
            "The labelled exploratory refit below is the only usable out-of-sample read."
        )
    w("")
    w(
        "### Exploratory refit (labelled, NOT the pinned primary; coefficients fit on validation rows with open values)"
    )
    w("")
    rf = a["exploratory_refit"]
    w(rf["label"])
    w("")
    for mk in ("total", "spread"):
        for name, r in rf[mk].items():
            if "dmse" in r:
                w(
                    f"- {mk} / {name}: n fit {r['n_fit']}, n eval {r['n_eval']}, dMSE {_num(r['dmse'], 3)} "
                    f"[{_num(r['ci_lo'], 3)}, {_num(r['ci_hi'], 3)}] (positive = movement helps)"
                )
            else:
                w(f"- {mk} / {name}: n fit {r['n_fit']}, n eval {r['n_eval']}, {r.get('note')}")
    w("")
    w("### Exploratory (labelled, not verdict inputs): movement vs outcome minus close")
    w("")
    ex = a["exploratory"]
    for win in (k for k in ex if k != "label"):
        for name, st in ex[win].items():
            w(
                f"- {win} / {name}: n {st['n']}, non-zero movement {st['n_nonzero_move']}, "
                f"corr {_ci(st.get('corr_move_outcome'))}, slope {_ci(st.get('slope_outcome_per_point_move'))}, "
                f"mean outcome in movement direction {_ci(st.get('mean_outcome_in_move_direction'))}, "
                f"continuation share {_pct((st.get('continuation_share') or {}).get('point'))}"
            )
            for b, bs in (st.get("size_buckets_abs_move") or {}).items():
                w(
                    f"    - |move| {b}: n {bs['n']}, mean outcome in movement direction {_ci(bs.get('mean_outcome_in_move_direction'))}, "
                    f"continuation {_pct(bs.get('continuation_share'))}"
                )
    w("")
    w(
        "Continuation = the realised outcome landed beyond the close in the direction the line moved; reversal = the opposite. Both are exploratory labels."
    )
    w("")
    b = o["intraday_2026"]
    w("## Part B: 2026 intraday movement (DESCRIPTIVE_ONLY)")
    w("")
    w(b["definition"])
    w("")
    w(
        "Verdict: **DESCRIPTIVE_ONLY**. No select or validation data exist (every 2026 game is holdout or post-holdout), so no number here decides anything."
    )
    w("")
    ld = b["load"]
    w(
        f"- Source: {ld['source']}; files {ld['files']}, rows {ld['rows']} (raw {ld['rows_raw']}, duplicates dropped {ld['rows_duplicate_dropped']})."
    )
    rec = ld["market_quotes_db_reconciliation"]
    w(
        f"- market_quotes.db (read-only, reconciliation only): {rec['db_distinct_keys']} distinct keys, {rec['db_keys_present_in_jsonl']} present in the JSONL, {rec['db_keys_absent_from_jsonl']} absent ({rec['db_absent_by_date_market']})."
    )
    em = b["event_match"]
    w(
        f"- Event match: {em['matched_1to1']} of {em['polymarket_events_with_title']} Polymarket events matched 1:1 to base rows; unmatched: {em['unmatched_classification']}."
    )
    w(f"- Quote filtering: {b['quote_filtering']}.")
    cv = b["coverage"]
    w(
        f"- Coverage: 2026 base rows {cv['base_rows_2026']} (total lined {cv['base_rows_2026_total_lined']}, spread lined {cv['base_rows_2026_spread_lined']}), by window {cv['base_rows_by_window']}; movement defined by market and window {cv['movement_defined_by_market_and_window']}; with an ESPN close in the holdout window {cv['movement_defined_and_espn_lined_holdout']}; post-holdout (2026-11-14) games excluded from outcome comparison: {cv['post_holdout_games_excluded_from_outcome_comparison']}."
    )
    w("")
    w("### Movement distribution (points)")
    w("")
    for mk, d in b["movement_distribution"].items():
        w(f"- {mk}: {d}")
    w("")
    w("### Agreement of the last pre-kickoff main rung with the ESPN close (line-only gate, no outcomes)")
    w("")
    w(f"Gate thresholds (fixed before any outcome was read): {b['agreement_with_espn_close']['thresholds']}.")
    w("")
    for mk in ("total", "spread"):
        for name, gs in b["agreement_with_espn_close"][mk].items():
            w(f"- {mk} / {name}: {gs}")
    w("")
    w("### Movement vs actual minus ESPN close (holdout-window games only; read in the --final run)")
    w("")
    oc = b.get("outcome_comparison")
    if not oc:
        w("Not computed in this run (no --final).")
    else:
        for mk in ("total", "spread"):
            for name, e in oc[mk].items():
                st = e["pm_last_minus_first_vs_outcome_minus_espn_close"]
                w(
                    f"- {mk} / {name} ({e['label']}; {'GATE FAILED' if e['gate_failed'] else 'gate passed'}: "
                    f"corr {_num(e['gate'].get('corr_last_rung_vs_espn_close'), 3)}, "
                    f"median abs diff {_num(e['gate'].get('median_abs_diff_points'), 2)} points): n {st['n']}, "
                    f"non-zero {st['n_nonzero_move']}, corr {_ci(st.get('corr_move_outcome'))}, "
                    f"slope {_ci(st.get('slope_outcome_per_point_move'))}, mean outcome in movement direction "
                    f"{_ci(st.get('mean_outcome_in_move_direction'))}, continuation share "
                    f"{_pct((st.get('continuation_share') or {}).get('point'))}"
                )
    w("")
    w("## Notes")
    w("")
    for n in o["notes"]:
        w(f"- {n}")
    w("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- main
def run(final: bool) -> dict[str, Any]:
    header = L.startup(__file__, OUT_JSON, final=final)
    _selftest_intraday()
    df = L.load_base()
    espn_df, audit = build_espn_frame(df)
    primary = {mk: L.run_primary(espn_df, "W2", mk, final=final) for mk in ("total", "spread")}
    for r in primary.values():
        ok = r["result"]["n_select"] >= L.MIN_SELECT_N
        r["interpretable"] = bool(ok)
        r["interpretable_note"] = (
            None
            if ok
            else f"NOT INTERPRETABLE: coefficients fit on select n = {r['result']['n_select']}; "
            "the pinned test is untestable (no open values in the select window)"
        )
    windows = ("select", "validation", "holdout") if final else ("select", "validation")
    espn_part = {
        "tier": "leaky",
        "audit": audit,
        "coverage_by_season": season_coverage(espn_df),
        "cohort_by_window": cohort_sizes(espn_df),
        "primary": primary,
        "exploratory": espn_exploratory(espn_df, windows),
        "exploratory_refit": espn_refit(espn_df, final),
    }
    intraday = intraday_part(df, final)
    verdicts = {
        "espn_total": primary["total"]["verdict"],
        "espn_spread": primary["spread"]["verdict"],
        "intraday_polymarket": L.verdict({}, "descriptive", final=final),
    }
    out = {
        **header,
        "quick_task": "261115-183",
        "workstream": "W2",
        "verdicts": verdicts,
        "espn_open_close": espn_part,
        "intraday_2026": intraday,
        "notes": [
            "ESPN movement is a close-time, untimestamped value: at most UPPER_BOUND_PASS, never enters v6.1.",
            "Intraday ladders are Polymarket-lineage quotes, not ESPN lines; DESCRIPTIVE_ONLY.",
            "2026-11-14 games are post-holdout and are never scored in any outcome statistic here.",
            "A non-final run reads no holdout row: verdicts that depend on the holdout are PROVISIONAL_PENDING_FINAL.",
        ],
    }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--final", action="store_true", help="read the holdout (one-time look) and write the final output"
    )
    args = ap.parse_args()
    out: dict[str, Any] | None = None
    for attempt in range(1, 4):
        try:
            out = run(args.final)
            break
        except MemoryError:
            print(f"MemoryError on attempt {attempt}; retrying in 30 s", file=sys.stderr)
            time.sleep(30)
    if out is None:
        raise SystemExit("MemoryError on every attempt")
    OUT_JSON.write_text(json.dumps(out, indent=2, default=_jd) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(out), encoding="utf-8")
    print(
        f"wrote {OUT_JSON.name} and {OUT_MD.name}; final={out['final']} holdout_reads={out['holdout_reads']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
