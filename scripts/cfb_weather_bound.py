"""W3 of quick-261115-183: observed-weather UPPER BOUND test for the NCAAF closing total and spread.

Question: does perfect (observed) game-window weather improve a closing-line-only OLS of the actual total
(and, for spreads, wind x expected margin improve the expected-margin OLS)? Observed weather is the outcome
the line already priced from a forecast, so a pass is an UPPER BOUND; a fail means weather is not worth
building at all. Only if the observed test passes on a market does the near point-in-time retest run: the
select-window observed-weather coefficients are applied to Open-Meteo historical-forecast values (2022+).

Rule: outputs/ncaaf/cfb_totals_sources_rule.json (hash pinned in cfb_totals_lib.py). The statistics, windows,
bootstrap and verdict function all come from the shared library; this script only builds the weather columns.

Subcommands
    plan                       count venues / venue-seasons and cache coverage (no network)
    fetch --kind archive       read-only GET Open-Meteo archive, one request per venue per season, ~2 s apart,
                               resumable cache, stops on HTTP 429 (exit 3) so it can be resumed later
    fetch --kind forecast      same for the historical-forecast archive, validation/holdout dates only
    analyze [--final]          build the columns, run L.run_primary, write the JSON and MD outputs;
                               without --final no holdout row is read (the library drops them)

Network is read-only GET with a generic User-Agent. No order or execute endpoint exists in this script.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "src"))

import cfb_totals_lib as L

REPO = L.REPO
CACHE_DIR = REPO / "data" / "research" / "ncaaf_v6" / "weather_open_meteo"
OUT_JSON = REPO / "outputs" / "ncaaf" / "cfb_weather_bound.json"
OUT_MD = REPO / "outputs" / "ncaaf" / "cfb_weather_bound.md"

ENDPOINTS = {
    "archive": "https://archive-api.open-meteo.com/v1/archive",
    "forecast": "https://historical-forecast-api.open-meteo.com/v1/forecast",
}
HOURLY = "temperature_2m,wind_speed_10m,precipitation"
USER_AGENT = "Mozilla/5.0"  # generic on purpose: no project, person or repo identifier
PACE_S = 2.0
FORECAST_FIRST_DATE = L.VALID_START  # forecast retest evaluates validation/holdout rows only
HOUR_S = 3600
WINDOW_HOURS = 3
COLD_BASE_F = 40.0
EXIT_RATE_LIMITED = 3

OBS_COLS = ("wind_mph", "precip_in", "cold_f")
FC_COLS = ("wind_mph_fc", "precip_in_fc", "cold_f_fc")


# --------------------------------------------------------------------------- venues and requirements
def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")


def attach_venues(df: pd.DataFrame) -> pd.DataFrame:
    """Add venue columns from resolve_team on the HOME team. ``wx_eligible`` marks games whose weather the
    rule can test: FBS home team with coordinates, not a dome, not neutral, not an FCS-vs-FCS game."""
    from model_prediction.data_sources.cfb_data import resolve_team

    cache: dict[str, tuple[Any, ...] | None] = {}

    def info(name: str) -> tuple[Any, ...] | None:
        if name not in cache:
            t = resolve_team(name)
            cache[name] = (
                None
                if t is None
                else (t.canonical_name, t.tier, bool(t.is_dome), t.latitude, t.longitude, t.stadium_name)
            )
        return cache[name]

    infos = df["home"].map(info)
    out = df.copy()
    out["venue"] = infos.map(lambda x: None if x is None else x[0])
    out["home_tier"] = infos.map(lambda x: None if x is None else x[1])
    out["is_dome"] = infos.map(lambda x: None if x is None else x[2])
    out["lat"] = infos.map(lambda x: np.nan if x is None or x[3] is None else float(x[3]))
    out["lon"] = infos.map(lambda x: np.nan if x is None or x[4] is None else float(x[4]))
    fbs_home = (out["game_class"] == "FBS-FBS") | (out["home_tier"].notna() & (out["home_tier"] != "FCS"))
    out["wx_eligible"] = (
        out["venue"].notna()
        & out["lat"].notna()
        & out["lon"].notna()
        & (out["is_dome"] == False)  # None must stay not-eligible
        & ~out["neutral"].astype(bool)
        & (out["game_class"] != "FCS-FCS")
        & fbs_home
    )
    out["hour0_s"] = out["t_s"] - out["t_s"] % HOUR_S
    return out


def _utc_date(epoch_s: int) -> str:
    return datetime.fromtimestamp(int(epoch_s), UTC).strftime("%Y-%m-%d")


def requirements(df: pd.DataFrame, kind: str) -> list[dict[str, Any]]:
    """One entry per (venue, season): the UTC date span that covers the first to last home game's 3-hour
    window. The forecast kind keeps only validation/holdout games (2022-11-12 onward)."""
    d = df[df["wx_eligible"]]
    if kind == "forecast":
        d = d[d["window"].isin(("validation", "holdout"))]
    cap = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
    reqs = []
    for (venue, season), g in d.groupby(["venue", "season"]):
        start = _utc_date(int(g["hour0_s"].min()))
        end = _utc_date(int(g["hour0_s"].max()) + (WINDOW_HOURS - 1) * HOUR_S)
        end = min(end, cap)
        if end < start:
            continue
        row = g.iloc[0]
        reqs.append(
            {
                "venue": venue,
                "season": int(season),
                "lat": round(float(row["lat"]), 4),
                "lon": round(float(row["lon"]), 4),
                "start": start,
                "end": end,
                "n_games": len(g),
            }
        )
    return reqs


def cache_path(kind: str, req: dict[str, Any]) -> Path:
    return CACHE_DIR / kind / f"{_slug(req['venue'])}__{req['season']}.json"


def cache_ok(kind: str, req: dict[str, Any]) -> bool:
    path = cache_path(kind, req)
    if not path.exists():
        return False
    try:
        c = json.loads(path.read_text(encoding="utf-8"))
        return bool(c["start"] <= req["start"] and c["end"] >= req["end"] and c["hourly"]["time"])
    except (ValueError, KeyError, OSError):
        return False


# --------------------------------------------------------------------------- fetching
class RateLimited(Exception):
    pass


def _get(url: str) -> tuple[int, dict[str, Any]]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=90) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        try:
            body = json.loads(err.read().decode("utf-8"))
        except ValueError:
            body = {}
        return err.code, body


def fetch_one(kind: str, req: dict[str, Any]) -> dict[str, Any]:
    params = {
        "latitude": f"{req['lat']:.4f}",
        "longitude": f"{req['lon']:.4f}",
        "start_date": req["start"],
        "end_date": req["end"],
        "hourly": HOURLY,
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "GMT",
    }
    url = f"{ENDPOINTS[kind]}?{urllib.parse.urlencode(params, safe=',')}"
    last: str = ""
    for attempt in range(4):
        try:
            status, body = _get(url)
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            status, body = -1, {"reason": f"transport error: {err}"}
        if status == 200 and body.get("hourly", {}).get("time"):
            return {
                "kind": kind,
                "venue": req["venue"],
                "season": req["season"],
                "start": req["start"],
                "end": req["end"],
                "request_lat": req["lat"],
                "request_lon": req["lon"],
                "grid_lat": body.get("latitude"),
                "grid_lon": body.get("longitude"),
                "elevation": body.get("elevation"),
                "fetched_at_utc": L.utc_now_iso(),
                "hourly_units": body.get("hourly_units"),
                "hourly": {
                    k: body["hourly"][k]
                    for k in ("time", "temperature_2m", "wind_speed_10m", "precipitation")
                },
            }
        reason = str(body.get("reason") or body)
        if status == 429:
            raise RateLimited(reason)
        last = f"HTTP {status}: {reason}"
        if status == 400:
            break  # a malformed or out-of-range request will not improve on retry
        time.sleep(10 * (attempt + 1))
    raise RuntimeError(last)


def cmd_fetch(args: argparse.Namespace) -> int:
    L.require_rule()
    df = attach_venues(L.load_base())
    reqs = requirements(df, args.kind)
    todo = [r for r in reqs if not cache_ok(args.kind, r)]
    print(
        f"[{args.kind}] venue-seasons required {len(reqs)}, cached {len(reqs) - len(todo)}, to fetch {len(todo)}",
        flush=True,
    )
    if args.limit:
        todo = todo[: args.limit]
    (CACHE_DIR / args.kind).mkdir(parents=True, exist_ok=True)
    done = failed = 0
    for i, req in enumerate(todo, 1):
        try:
            payload = fetch_one(args.kind, req)
        except RateLimited as err:
            print(
                f"HTTP 429 after {done} fetched this run ({i - 1} attempted): {err}. Stopping; resume later.",
                flush=True,
            )
            return EXIT_RATE_LIMITED
        except RuntimeError as err:
            failed += 1
            print(f"FAILED {req['venue']} {req['season']}: {err}", flush=True)
        else:
            cache_path(args.kind, req).write_text(
                json.dumps(payload, separators=(",", ":")), encoding="utf-8"
            )
            done += 1
            if done % 25 == 0 or i == len(todo):
                print(f"  {done} fetched ({i}/{len(todo)}) last {req['venue']} {req['season']}", flush=True)
        time.sleep(PACE_S)
    print(
        f"done: fetched {done}, failed {failed}, remaining uncached {len(todo) - done - failed}", flush=True
    )
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    df = attach_venues(L.load_base())
    out: dict[str, Any] = {
        "eligible_games": int(df["wx_eligible"].sum()),
        "eligible_lined_total_games": int((df["wx_eligible"] & df["line"].notna()).sum()),
    }
    for kind in ENDPOINTS:
        reqs = requirements(df, kind)
        out[kind] = {
            "venue_seasons": len(reqs),
            "venues": len({r["venue"] for r in reqs}),
            "cached": sum(cache_ok(kind, r) for r in reqs),
        }
    print(json.dumps(out, indent=2))
    return 0


# --------------------------------------------------------------------------- weather columns
def load_hourly(kind: str, reqs: list[dict[str, Any]]) -> pd.DataFrame:
    """Long table (venue, hour_s, temp_f, wind_mph, precip_in) from every cached venue-season file."""
    frames = []
    for req in reqs:
        path = cache_path(kind, req)
        if not path.exists():
            continue
        c = json.loads(path.read_text(encoding="utf-8"))
        h = c["hourly"]
        hour_s = pd.to_datetime(pd.Series(h["time"])).to_numpy().astype("datetime64[s]").astype("int64")
        frames.append(
            pd.DataFrame(
                {
                    "venue": req["venue"],
                    "hour_s": hour_s,
                    "temp_f": pd.to_numeric(pd.Series(h["temperature_2m"]), errors="coerce"),
                    "wind_mph": pd.to_numeric(pd.Series(h["wind_speed_10m"]), errors="coerce"),
                    "precip_in": pd.to_numeric(pd.Series(h["precipitation"]), errors="coerce"),
                }
            )
        )
    if not frames:
        return pd.DataFrame(columns=["venue", "hour_s", "temp_f", "wind_mph", "precip_in"])
    return pd.concat(frames, ignore_index=True).drop_duplicates(["venue", "hour_s"], keep="last")


def game_window_means(df: pd.DataFrame, hourly: pd.DataFrame, suffix: str) -> pd.DataFrame:
    """Window means over the three half-open hourly rows [floor_hour(kickoff), +3 h). The cold transform
    max(0, 40 - temp F) is applied to each hourly row BEFORE averaging. A game missing any of the three
    rows (or any of the three variables) gets NaN in every column. Ineligible games are NaN."""
    idx = hourly.set_index(["venue", "hour_s"])
    elig = df["wx_eligible"].to_numpy()
    venues = df["venue"].to_numpy()
    h0 = df["hour0_s"].to_numpy()
    acc = {k: np.zeros(len(df)) for k in ("wind", "precip", "cold")}
    ok = elig.copy()
    for k in range(WINDOW_HOURS):
        keys = pd.MultiIndex.from_arrays([venues, h0 + k * HOUR_S])
        got = idx.reindex(keys)
        t = got["temp_f"].to_numpy(float)
        w = got["wind_mph"].to_numpy(float)
        p = got["precip_in"].to_numpy(float)
        ok &= ~(np.isnan(t) | np.isnan(w) | np.isnan(p))
        acc["wind"] += np.nan_to_num(w)
        acc["precip"] += np.nan_to_num(p)
        acc["cold"] += np.nan_to_num(np.maximum(0.0, COLD_BASE_F - t))
    out = pd.DataFrame(index=df.index)
    for src, name in (("wind", "wind_mph"), ("precip", "precip_in"), ("cold", "cold_f")):
        out[name + suffix] = np.where(ok, acc[src] / WINDOW_HOURS, np.nan)
    out["wind_x_margin" + suffix] = out["wind_mph" + suffix] * df["close_margin"].to_numpy(float) / 10.0
    return out


# --------------------------------------------------------------------------- analysis
def _num(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: _num(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_num(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating,)):
        return None if np.isnan(x) else float(x)
    if isinstance(x, float) and np.isnan(x):
        return None
    return x


def coverage_block(df: pd.DataFrame) -> dict[str, Any]:
    """Counts only (no outcome statistics): games, eligible outdoor home games, games carrying weather."""
    by_season: dict[str, Any] = {}
    for season, g in df.groupby("season"):
        fbs = g[g["game_class"] != "FCS-FCS"]
        by_season[str(season)] = {
            "games_fbs_involved": len(fbs),
            "excluded_neutral": int(fbs["neutral"].astype(bool).sum()),
            "excluded_dome_home": int(((fbs["is_dome"] == True) & ~fbs["neutral"].astype(bool)).sum()),
            "excluded_unresolved_or_no_coords": int((fbs["lat"].isna() & ~fbs["neutral"].astype(bool)).sum()),
            "wx_eligible": int(g["wx_eligible"].sum()),
            "with_observed_weather": int(g["wind_mph"].notna().sum()),
            "lined_total_with_observed_weather": int((g["wind_mph"].notna() & g["line"].notna()).sum()),
            "lined_spread_with_observed_weather": int(
                (g["wind_mph"].notna() & g["spread_home"].notna()).sum()
            ),
            "with_forecast_weather": int(g["wind_mph_fc"].notna().sum()) if "wind_mph_fc" in g else 0,
        }
    return by_season


def fmt(x: Any, nd: int = 4) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def run_analysis(final: bool) -> dict[str, Any]:
    header = L.startup(__file__, OUT_JSON, final=final)
    df = attach_venues(L.load_base())

    obs_reqs = requirements(df, "archive")
    fc_reqs = requirements(df, "forecast")
    obs = game_window_means(df, load_hourly("archive", obs_reqs), "")
    df = pd.concat([df, obs], axis=1)
    fc_cached = sum(cache_ok("forecast", r) for r in fc_reqs)
    fc = game_window_means(df, load_hourly("forecast", fc_reqs), "_fc")
    df = pd.concat([df, fc], axis=1)
    # Forecast columns exist only for validation/holdout rows by construction of the forecast requirements;
    # rows outside carry NaN because no forecast hours are cached for them.

    coverage = {
        "venues_required": len({r["venue"] for r in obs_reqs}),
        "venue_seasons_required": len(obs_reqs),
        "venue_seasons_cached": sum(cache_ok("archive", r) for r in obs_reqs),
        "forecast_venue_seasons_required": len(fc_reqs),
        "forecast_venue_seasons_cached": fc_cached,
        "by_season": coverage_block(df),
        "cohort_counts": {m: L.cohort_counts(df, m) for m in ("total", "spread")},
        "note": "counts only; the cohort for each test is the lined rows carrying every pinned feature",
    }

    results: dict[str, Any] = {}
    verdicts: dict[str, Any] = {}
    forecast: dict[str, Any] = {}
    for market in ("total", "spread"):
        res = L.run_primary(df, "W3", market, final=final)
        results[market] = res
        v = dict(res["verdict"])
        v["stage"] = "observed"
        obs_pass = (
            v["verdict"] == "UPPER_BOUND_PASS" if final else bool(v.get("validation_pass"))
        )  # non-final: validation half only
        fc_any = fc_cached > 0
        if obs_pass and fc_any:
            fres = L.run_primary(df, "W3", market, stage="forecast", final=final)
            forecast[market] = {"ran": True, "forecast_cache": f"{fc_cached}/{len(fc_reqs)}", "result": fres}
            if final:
                fv = fres["verdict"]
                if fv["verdict"] == "PIT_PASS":
                    v = {
                        **fv,
                        "stage": "forecast",
                        "observed_verdict": res["verdict"]["verdict"],
                        "caveat": "Open-Meteo historical-forecast values are short-lead (hours before the "
                        "game); PIT_PASS carries that lead-time caveat.",
                    }
                else:
                    # An observed pass stays an upper bound; a failed or under-powered PIT retest is
                    # recorded next to it and never upgrades or erases the observed verdict.
                    v["forecast_retest_verdict"] = fv["verdict"]
                    v["forecast_retest_reason"] = fv.get("reason")
        elif obs_pass:
            forecast[market] = {
                "ran": False,
                "reason": f"observed test passed but no forecast hours are cached ({fc_cached}/{len(fc_reqs)} venue-seasons)",
            }
        else:
            forecast[market] = {
                "ran": False,
                "reason": "observed test did not pass; the rule only runs the retest on a pass",
            }
        verdicts[market] = v

    out = {
        **header,
        "workstream": "W3",
        "label": "OBSERVED WEATHER UPPER BOUND (the line already knows the forecast)",
        "source": "Open-Meteo archive (observed/reanalysis) hourly temperature_2m, wind_speed_10m, precipitation",
        "coverage": coverage,
        "results": results,
        "forecast_stage": forecast,
        "verdicts": verdicts,
    }
    return _num(out)


def write_md(out: dict[str, Any]) -> str:
    cov = out["coverage"]
    lines = [
        "# NCAAF W3: observed-weather upper bound (quick-261115-183)",
        "",
        (
            f"Rule sha256 `{out['rule_sha256']}`; script sha256 `{out['script_sha256']}`; "
            f"generated {out['generated_at_utc']}; final run: {out['final']}; "
            f"holdout_reads: {out['holdout_reads']}."
        ),
        "",
        (
            f"**{out['label']}.** Observed weather can at most reach UPPER_BOUND_PASS and never enters "
            "v6.1; if even perfect weather fails, weather is not worth building."
        ),
        "",
        "## Coverage",
        "",
        (
            f"Venues {cov['venues_required']}; venue-seasons cached {cov['venue_seasons_cached']} of "
            f"{cov['venue_seasons_required']}. Forecast venue-seasons cached "
            f"{cov['forecast_venue_seasons_cached']} of {cov['forecast_venue_seasons_required']}."
        ),
        "",
        "| season | FBS-involved | neutral | dome home | unresolved or no coords | eligible | with weather | lined total | lined spread | forecast |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s, c in cov["by_season"].items():
        lines.append(
            f"| {s} | {c['games_fbs_involved']} | {c['excluded_neutral']} | {c['excluded_dome_home']} | "
            f"{c['excluded_unresolved_or_no_coords']} | {c['wx_eligible']} | {c['with_observed_weather']} | "
            f"{c['lined_total_with_observed_weather']} | {c['lined_spread_with_observed_weather']} | {c['with_forecast_weather']} |"
        )
    lines += [
        "",
        "## Primary statistic: dMSE = MSE(line-only) - MSE(line + weather), validation, select-fit coefficients",
        "",
    ]
    lines += [
        "| market | stage | n select | n validation | n holdout | dMSE validation | 95% CI | dMSE holdout (point) | verdict |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for market in ("total", "spread"):
        r = out["results"][market]["result"]
        v = r.get("validation") or {}
        h = r.get("holdout") or {}
        lines.append(
            f"| {market} | observed | {r['n_select']} | {r['n_validation']} | {r['n_holdout']} | {fmt(v.get('dmse'))} | "
            f"[{fmt(v.get('ci_lo'))}, {fmt(v.get('ci_hi'))}] | {fmt(h.get('dmse'))} | "
            f"{out['results'][market]['verdict']['verdict']} |"
        )
        fs = out["forecast_stage"][market]
        if fs.get("ran"):
            fr = fs["result"]["result"]
            fv = fr.get("validation") or {}
            fh = fr.get("holdout") or {}
            lines.append(
                f"| {market} | forecast | {fr['n_select']} | {fr['n_validation']} | {fr['n_holdout']} | {fmt(fv.get('dmse'))} | "
                f"[{fmt(fv.get('ci_lo'))}, {fmt(fv.get('ci_hi'))}] | {fmt(fh.get('dmse'))} | "
                f"{fs['result']['verdict']['verdict']} |"
            )
    lines += [
        "",
        "Positive dMSE means weather helps. PASS needs the validation CI lower bound above 0 AND a positive holdout point estimate.",
        "",
    ]
    lines += ["## Verdicts", ""]
    for market, v in out["verdicts"].items():
        lines.append(f"- **{market}**: {v['verdict']} (stage {v['stage']}): {v.get('reason', '')}")
    lines += ["", "## Forecast stage", ""]
    for market, fs in out["forecast_stage"].items():
        lines.append(
            f"- {market}: {'ran' if fs.get('ran') else 'not run'}"
            + ("" if fs.get("ran") else f" ({fs['reason']})")
        )
    lines += ["", "## Exploratory (not a verdict input)", ""]
    for market in ("total", "spread"):
        ex = out["results"][market]["result"].get("exploratory") or {}
        if ex:
            lines.append(
                f"- {market}: select in-sample dMSE {fmt(ex.get('select_in_sample_dmse'))}, validation dMAE {fmt(ex.get('validation_dmae'))}"
            )
    lines.append("")
    return "\n".join(lines)


def cmd_analyze(args: argparse.Namespace) -> int:
    out = run_analysis(args.final)
    OUT_JSON.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    OUT_MD.write_text(write_md(out), encoding="utf-8")
    print(f"wrote {OUT_JSON.name} and {OUT_MD.name}; holdout_reads={out['holdout_reads']}")
    for market, v in out["verdicts"].items():
        print(f"  {market}: {v['verdict']} ({v['stage']})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    p_fetch = sub.add_parser("fetch")
    p_fetch.add_argument("--kind", choices=tuple(ENDPOINTS), required=True)
    p_fetch.add_argument("--limit", type=int, default=0, help="stop after this many requests (0 = all)")
    p_an = sub.add_parser("analyze")
    p_an.add_argument("--final", action="store_true", help="read the holdout once (D-183-HOLDOUT)")
    args = parser.parse_args(argv)
    return {"plan": cmd_plan, "fetch": cmd_fetch, "analyze": cmd_analyze}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
