"""W4 of quick-261115-183: the quarterback UPPER-BOUND test for the NCAAF closing total and spread.

Question: would knowing that a team started a different quarterback than usual have told us anything the
closing line did not already price? The answer here is an UPPER bound only. Starters are derived POST HOC
from the finished game's box score (the passer with the most pass attempts), so the flags are known only
after the game; the closing line knows the announced starter, and there are no historical timestamped
injury or depth-chart reports locally. A pass cannot be shown to be point-in-time, so the best possible
verdict is UPPER_BOUND_PASS and nothing here enters cfb-structural-v6.1.

Definitions (pinned in outputs/ncaaf/cfb_totals_sources_rule.json, source W4)
    starter        the team's passer with the most pass attempts in that game (ESPN site summary box score
                   ``C/ATT``; ties go to the first listed passer).
    qb_changed     starter differs from the team's previous game starter in the SAME season (the first game
                   of a season is 0; an offseason change is not an in-season availability signal).
    backup_start   starter differs from the team's season-to-date attempts leader over prior games of the
                   same season (the first game of a season is 0; counted in coverage).
    prior games    every FBS-involved game of the team that season, including FBS-vs-FCS ones, which is why
                   the fetch covers every FBS-involved game 2022-2026, not only the FBS-vs-FBS lined ones.
    cohort         FBS-vs-FBS lined games only (``L.run_primary`` filters on game_class == 'FBS-FBS').

Subcommands
    fetch          resumable cache of derived per-team passer rows (athlete id, name, attempts) in
                   data/research/ncaaf_v6/qb_starters.jsonl; read-only GET, 0.4 s pacing, backoff on 429/5xx,
                   generic User-Agent. Run it in the background with a log.
    run [--final]  build the flags from the cache, run the pinned primary statistic and write
                   outputs/ncaaf/cfb_qb_bound.json and .md. Without --final no holdout row is read.

Read-only GET against the ESPN site summary endpoint only; no order, account or execute endpoint exists here.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cfb_totals_lib as L

SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/summary"
USER_AGENT = "Mozilla/5.0"
PACING_S = 0.4
MAX_ATTEMPTS = 8
CACHE_PATH = L.REPO / "data" / "research" / "ncaaf_v6" / "qb_starters.jsonl"
LOG_PATH = L.REPO / "data" / "research" / "ncaaf_v6" / "qb_starters.log"
OUT_JSON = L.REPO / "outputs" / "ncaaf" / "cfb_qb_bound.json"
OUT_MD = L.REPO / "outputs" / "ncaaf" / "cfb_qb_bound.md"
FIRST_SEASON, LAST_SEASON = 2022, 2026

LEAKY_NOTE = (
    "LEAKY UPPER BOUND. Starters are derived post hoc from box-score pass attempts. "
    "The closing line knows the announced starter and there are no historical timestamped "
    "injury or depth-chart reports locally, so nothing here can be shown to be observable before kickoff. "
    "The best possible verdict is UPPER_BOUND_PASS, and nothing here enters cfb-structural-v6.1."
)

NOTES = [
    (
        "qb_changed and backup_start are season-local: the first game of a season is 0 for both (counted in "
        "coverage), so an offseason starter change is not flagged."
    ),
    (
        "A game whose starter did not resolve (no pass attempts or no box score) leaves the feature missing "
        "and is dropped from the cohort; it does not update the team's state."
    ),
    "Seasons 2022-2026 only; 2022 select rows end 2022-11-11 and so are few.",
]


def season_of(start_utc: str) -> int:
    year, month = int(start_utc[:4]), int(start_utc[5:7])
    return year if month >= 7 else year - 1


# --------------------------------------------------------------------------- the event list
def fbs_involved_events() -> list[dict[str, Any]]:
    """Non-forfeit FBS-involved games of seasons 2022-2026 from the frozen history snapshot, kickoff-sorted."""
    L.verify_frozen_inputs()
    out = []
    for g in L._jsonl(L.INPUT_DIR / "history_snapshot.jsonl"):
        if g.get("is_forfeit") or g.get("game_class") == "FCS-FCS":
            continue
        season = season_of(g["start_utc"])
        if FIRST_SEASON <= season <= LAST_SEASON:
            out.append({**g, "event_id": str(g["event_id"]), "season": season})
    return sorted(out, key=lambda g: (g["start_utc"], g["event_id"]))


# --------------------------------------------------------------------------- fetch
def parse_passers(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Derived passer rows per team id from one summary payload: [{id, name, att}] in listed order."""
    teams: dict[str, list[dict[str, Any]]] = {}
    for team_block in (payload.get("boxscore") or {}).get("players") or []:
        team_id = str((team_block.get("team") or {}).get("id") or "")
        if not team_id:
            continue
        rows: list[dict[str, Any]] = []
        for stat in team_block.get("statistics") or []:
            if stat.get("name") != "passing":
                continue
            labels = stat.get("labels") or []
            if "C/ATT" not in labels:
                continue
            idx = labels.index("C/ATT")
            for ath in stat.get("athletes") or []:
                vals = ath.get("stats") or []
                if idx >= len(vals) or "/" not in str(vals[idx]):
                    continue
                try:
                    att = int(str(vals[idx]).split("/")[1])
                except ValueError:
                    continue
                a = ath.get("athlete") or {}
                rows.append({"id": str(a.get("id") or ""), "name": a.get("displayName"), "att": att})
        teams[team_id] = rows
    return teams


def read_cache() -> dict[str, dict[str, Any]]:
    cache: dict[str, dict[str, Any]] = {}
    if CACHE_PATH.exists():
        with CACHE_PATH.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:  # a crash mid-append leaves a truncated line; that event is refetched
                    continue
                cache[str(rec["event_id"])] = rec
    return cache


def _ensure_trailing_newline() -> None:
    """Make the next append start on a fresh line even after a crash mid-write."""
    if CACHE_PATH.exists() and CACHE_PATH.stat().st_size:
        with CACHE_PATH.open("rb+") as handle:
            handle.seek(-1, 2)
            if handle.read(1) != b"\n":
                handle.write(b"\n")


def _log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"{L.utc_now_iso()} {msg}\n")


def get_summary(client: httpx.Client, event_id: str) -> tuple[str, dict[str, Any] | None]:
    """('ok', payload), ('http_<code>', None) for a definitive client error, or ('failed', None) after backoff."""
    for attempt in range(MAX_ATTEMPTS):
        wait = min(120.0, 2.0 * (2**attempt))
        try:
            resp = client.get(SUMMARY_URL, params={"event": event_id})
        except (httpx.HTTPError, MemoryError) as exc:  # MemoryError: host low on virtual memory, transient
            _log(f"event {event_id} attempt {attempt + 1}: {type(exc).__name__}; sleep {wait:.0f}s")
            time.sleep(wait)
            continue
        if resp.status_code == 200:
            try:
                return "ok", resp.json()
            except (ValueError, MemoryError):
                _log(f"event {event_id} attempt {attempt + 1}: invalid JSON or low memory; sleep {wait:.0f}s")
                time.sleep(wait)
                continue
        if resp.status_code == 429 or resp.status_code >= 500:
            retry_after = resp.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                wait = max(wait, float(retry_after))
            _log(f"event {event_id} attempt {attempt + 1}: HTTP {resp.status_code}; sleep {wait:.0f}s")
            time.sleep(wait)
            continue
        return f"http_{resp.status_code}", None
    return "failed", None


def cmd_fetch(_args: argparse.Namespace) -> int:
    L.require_rule()
    events = fbs_involved_events()
    _ensure_trailing_newline()
    cache = read_cache()
    todo = [g for g in events if g["event_id"] not in cache]
    _log(f"fetch start: {len(events)} FBS-involved events, {len(cache)} cached, {len(todo)} to fetch")
    consecutive_failed = 0
    started = time.time()
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0) as client:
        for i, g in enumerate(todo, 1):
            status, payload = get_summary(client, g["event_id"])
            if status == "failed":
                consecutive_failed += 1
                _log(
                    f"event {g['event_id']}: gave up after {MAX_ATTEMPTS} attempts (not cached; resume retries)"
                )
                if consecutive_failed >= 5:
                    _log("5 consecutive failures: stopping; rerun fetch to resume")
                    return 3
                continue
            consecutive_failed = 0
            rec = {
                "event_id": g["event_id"],
                "season": g["season"],
                "start_utc": g["start_utc"],
                "status": status,
                "fetched_at_utc": L.utc_now_iso(),
                "teams": parse_passers(payload) if payload is not None else {},
            }
            with CACHE_PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(rec, separators=(",", ":")) + "\n")
                handle.flush()
            if i % 100 == 0:
                rate = i / max(1e-9, time.time() - started)
                _log(
                    f"progress {i}/{len(todo)} ({rate:.2f} events/s, eta {(len(todo) - i) / rate / 60:.0f} min)"
                )
            time.sleep(PACING_S)
    _log("fetch complete")
    return 0


# --------------------------------------------------------------------------- flags
def starter_of(rows: list[dict[str, Any]]) -> str | None:
    """Passer with the most attempts; ties go to the first listed. None when nobody attempted a pass."""
    best, best_att = None, 0
    for r in rows:
        if r["att"] > best_att and r["id"]:
            best, best_att = r["id"], r["att"]
    return best


def derive_flags(
    events: list[dict[str, Any]], cache: dict[str, dict[str, Any]]
) -> dict[str, dict[str, tuple[int, int] | None]]:
    """event_id -> {'home': (backup_start, qb_changed) | None, 'away': ...}; None = starter not resolved.

    Walks each season in kickoff order. A team's state (previous starter, cumulative attempts by passer) is
    updated only from games whose starter resolved, and a game's flags use only the team's prior games.
    """
    out: dict[str, dict[str, tuple[int, int] | None]] = {}
    last_starter: dict[tuple[int, str], str] = {}
    cum: dict[tuple[int, str], dict[str, int]] = defaultdict(dict)
    for g in events:
        rec = cache.get(g["event_id"])
        flags: dict[str, tuple[int, int] | None] = {"home": None, "away": None}
        pending: list[tuple[tuple[int, str], list[dict[str, Any]], str]] = []
        for side, tid in (("home", str(g.get("home_id"))), ("away", str(g.get("away_id")))):
            if rec is None or rec.get("status") != "ok":
                continue
            rows = (rec.get("teams") or {}).get(tid) or []
            sid = starter_of(rows)
            if sid is None:
                continue
            key = (g["season"], tid)
            prev = last_starter.get(key)
            if prev is None:
                flags[side] = (0, 0)
            else:
                totals = cum[key]
                order = list(totals)
                leader = max(order, key=lambda a, t=totals, o=order: (t[a], -o.index(a)))
                flags[side] = (int(sid != leader), int(sid != prev))
            pending.append((key, rows, sid))
        for key, rows, sid in pending:
            last_starter[key] = sid
            for r in rows:
                if r["id"]:
                    cum[key][r["id"]] = cum[key].get(r["id"], 0) + r["att"]
        out[g["event_id"]] = flags
    return out


def attach_features(df: pd.DataFrame, flags: dict[str, dict[str, tuple[int, int] | None]]) -> pd.DataFrame:
    df = df.copy()
    cols: dict[str, list[float]] = {k: [] for k in ("bh", "ch", "ba", "ca")}
    for eid in df["event_id"]:
        f = flags.get(str(eid), {})
        for side, (b, c) in (("home", ("bh", "ch")), ("away", ("ba", "ca"))):
            v = f.get(side)
            cols[b].append(np.nan if v is None else v[0])
            cols[c].append(np.nan if v is None else v[1])
    df["backup_start_home"], df["qb_changed_home"] = cols["bh"], cols["ch"]
    df["backup_start_away"], df["qb_changed_away"] = cols["ba"], cols["ca"]
    df["backup_start_sum"] = df["backup_start_home"] + df["backup_start_away"]
    df["qb_changed_sum"] = df["qb_changed_home"] + df["qb_changed_away"]
    df["backup_start_diff"] = df["backup_start_home"] - df["backup_start_away"]
    df["qb_changed_diff"] = df["qb_changed_home"] - df["qb_changed_away"]
    return df


# --------------------------------------------------------------------------- coverage and report
def coverage(
    events: list[dict[str, Any]],
    cache: dict[str, dict[str, Any]],
    flags: dict[str, dict[str, tuple[int, int] | None]],
    df: pd.DataFrame,
) -> dict[str, dict[str, Any]]:
    """Per season: events expected/fetched/with a box score, team sides resolved, flag counts (scored
    cohort = FBS-vs-FBS lined games with both sides resolved, windows select/validation/holdout only)."""
    cov: dict[str, dict[str, Any]] = {}
    for season in range(FIRST_SEASON, LAST_SEASON + 1):
        ev = [g for g in events if g["season"] == season]
        fetched = [g for g in ev if g["event_id"] in cache]
        ok = [g for g in fetched if any(cache[g["event_id"]].get("teams", {}).values())]
        sides = [flags.get(g["event_id"], {}).get(s) for g in ev for s in ("home", "away")]
        d = df[(df["season"] == season) & (df["game_class"] == "FBS-FBS") & df["window"].isin(L.WINDOWS)]
        tot = d[d["line"].notna() & d["backup_start_sum"].notna()]
        spr = d[d["spread_home"].notna() & d["backup_start_diff"].notna()]
        cov[str(season)] = {
            "events_expected": len(ev),
            "events_fetched": len(fetched),
            "events_with_box_score": len(ok),
            "team_sides_expected": 2 * len(ev),
            "team_sides_resolved": sum(1 for v in sides if v is not None),
            "sides_with_both_flags_zero": sum(1 for v in sides if v == (0, 0)),
            "fbs_fbs_total_lined_scored": len(tot),
            "fbs_fbs_spread_lined_scored": len(spr),
            "backup_start_sum_gt0": int((tot["backup_start_sum"] > 0).sum()),
            "qb_changed_sum_gt0": int((tot["qb_changed_sum"] > 0).sum()),
            "backup_start_diff_ne0": int((spr["backup_start_diff"] != 0).sum()),
            "qb_changed_diff_ne0": int((spr["qb_changed_diff"] != 0).sum()),
        }
    return cov


def exploratory_validation(df: pd.DataFrame) -> dict[str, Any]:
    """Validation-window descriptives, labelled exploratory: mean (actual - close) by flag value (totals)."""
    d = df[
        (df["game_class"] == "FBS-FBS")
        & (df["window"] == "validation")
        & df["line"].notna()
        & df["qb_changed_sum"].notna()
    ]
    out: dict[str, Any] = {"label": "exploratory, validation window only, not a verdict input"}
    resid = d["actual"] - d["line"]
    for col in ("backup_start_sum", "qb_changed_sum"):
        out[col] = {
            str(int(v)): {
                "n": int((d[col] == v).sum()),
                "mean_actual_minus_close": float(resid[d[col] == v].mean()),
            }
            for v in sorted(d[col].unique())
        }
    return out


def fmt(x: float | None, nd: int = 3) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def render_md(report: dict[str, Any]) -> str:
    head = "| season | events expected | fetched | with box score | sides resolved / expected | "
    head += "total-lined scored | backup_start_sum>0 | qb_changed_sum>0 | backup_start_diff!=0 | qb_changed_diff!=0 |"
    lines = [
        "# NCAAF QB upper-bound test (quick-261115-183, W4)",
        "",
        f"**{LEAKY_NOTE}**",
        "",
        f"- rule_sha256: `{report['rule_sha256']}`",
        f"- script_sha256: `{report['script_sha256']}`",
        f"- generated_at_utc: {report['generated_at_utc']}; final run: {report['final']}",
        f"- holdout_reads: {report['holdout_reads']}",
        "",
        "## Coverage by season",
        "",
        head,
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s, c in report["coverage_by_season"].items():
        lines.append(
            f"| {s} | {c['events_expected']} | {c['events_fetched']} | {c['events_with_box_score']} | "
            f"{c['team_sides_resolved']} / {c['team_sides_expected']} | {c['fbs_fbs_total_lined_scored']} | "
            f"{c['backup_start_sum_gt0']} | {c['qb_changed_sum_gt0']} | {c['backup_start_diff_ne0']} | "
            f"{c['qb_changed_diff_ne0']} |"
        )
    lines += [
        "",
        (
            "Flag counts cover FBS-vs-FBS lined games with both sides resolved (select, validation and holdout "
            "windows; counts only, no outcome). Seasons start at 2022, so the select window of the pinned OLS is "
            "only the 2022 games up to 2022-11-11."
        ),
        "",
        "## Primary statistic: dMSE = MSE(line-only OLS) - MSE(line + QB flags OLS), validation (positive = helps)",
        "",
        "| market | n select | n validation | n holdout | validation dMSE | 95% CI | holdout dMSE (point only) | verdict |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for market, v in report["verdicts"].items():
        r = report["results"][market]["result"]
        val, hold = r["validation"], r["holdout"]
        n_hold = r["n_holdout"] if r["n_holdout"] is not None else "unread"
        ci = f"[{fmt(val['ci_lo'])}, {fmt(val['ci_hi'])}]" if val else "n/a"
        lines.append(
            f"| {market} | {r['n_select']} | {r['n_validation']} | {n_hold} | "
            f"{fmt(val['dmse']) if val else 'n/a'} | {ci} | {fmt(hold['dmse']) if hold else 'unread'} | "
            f"{v['verdict']} |"
        )
    lines += ["", "Verdict reasons:", ""]
    for market, v in report["verdicts"].items():
        lines.append(f"- {market}: {v['verdict']} ({v.get('reason')})")
    lines += ["", "Flagged rows per market (select / validation):", ""]
    for market, res in report["results"].items():
        lines.append(f"- {market}: {json.dumps(res['result']['flag_counts'])}")
    lines += [
        "",
        "## Exploratory (not a verdict input)",
        "",
        "```json",
        json.dumps(report["exploratory"], indent=1),
        "```",
        "",
        "## Reading this",
        "",
        (
            "A pass here would mean only that knowing the actual starter after the game is worth something the "
            "closing line lacked. The line already knows the announced starter, so a pass is an upper bound, "
            "and the follow-up is the forward capture of pregame injuries and depth plus a point-in-time retest. "
            "A failure is informative: even with hindsight the QB flag does not help."
        ),
    ]
    return "\n".join(lines) + "\n"


def cmd_run(args: argparse.Namespace) -> int:
    header = L.startup(__file__, OUT_JSON, final=args.final)
    events = fbs_involved_events()
    cache = read_cache()
    flags = derive_flags(events, cache)
    df = attach_features(L.load_base(), flags)
    results: dict[str, Any] = {}
    verdicts: dict[str, Any] = {}
    for market in ("total", "spread"):
        res = L.run_primary(df, "W4", market, final=args.final)
        results[market] = res
        verdicts[market] = res["verdict"]
    report = {
        **header,
        "workstream": "W4",
        "source": "W4 quarterback starter bound",
        "tier": "leaky",
        "caveat": LEAKY_NOTE,
        "definitions": L.rule()["sources"]["W4"]["starter_definition"],
        "interpretation_notes": NOTES,
        "cache_file": str(CACHE_PATH.relative_to(L.REPO)),
        "cache_events": len(cache),
        "coverage_by_season": coverage(events, cache, flags, df),
        "results": results,
        "verdicts": verdicts,
        "exploratory": exploratory_validation(df),
    }
    OUT_JSON.write_text(json.dumps(report, indent=1, default=float) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(report), encoding="utf-8")
    print(json.dumps({"verdicts": verdicts, "holdout_reads": report["holdout_reads"]}, indent=1))
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fetch", help="resumable ESPN summary fetch of derived passer rows")
    run = sub.add_parser("run", help="build flags, run the pinned primary test, write outputs")
    run.add_argument("--final", action="store_true", help="read the holdout once (D-183-HOLDOUT)")
    args = parser.parse_args(argv)
    return cmd_fetch(args) if args.cmd == "fetch" else cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
