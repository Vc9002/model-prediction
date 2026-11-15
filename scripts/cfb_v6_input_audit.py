"""Read-only audit of the NCAAF weather, QB and pace-data inputs behind cfb-structural-v4/v5.

Quick task 261114-srb, workstream D. Question: are the hand-set weather and QB point
adjustments live inputs or dead ones, and does the training history carry any real
possession / efficiency data for a pace term?

Everything here is a read. The ledger DB is opened with a ``mode=ro`` URI, the history and
weather files are opened for reading, and the code claims are located by searching the
source text (the line numbers in the report are looked up at run time, never typed in).
Probes call pure functions on synthetic inputs; nothing touches the network or a ledger.

Usage: OPENBLAS_NUM_THREADS=1 .venv/Scripts/python scripts/cfb_v6_input_audit.py
Writes outputs/ncaaf/cfb_v6_input_audit.json and outputs/ncaaf/cfb_v6_input_audit.md.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

# Pinned by the quick-task brief; recorded in every output.
AS_OF_UTC = "2026-11-15T02:10:00Z"

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

LEDGER_DB = Path("E:/model-prediction-runtime/ledgers/ledgers.db")
HISTORY_FILES = (
    REPO / "data" / "historical" / "ncaaf_games_all.jsonl",
    REPO / "data" / "processed" / "ncaaf" / "games.jsonl",
)
WEATHER_FILES = {
    "repo": REPO / "data" / "weather" / "ncaaf_historical.jsonl",
    "runtime": Path("E:/model-prediction-runtime/weather/ncaaf_historical.jsonl"),
}
INJURY_SNAPSHOT_DIR = REPO / "data" / "availability" / "ncaaf" / "espn_injury_snapshots"
OUT_JSON = REPO / "outputs" / "ncaaf" / "cfb_v6_input_audit.json"
OUT_MD = REPO / "outputs" / "ncaaf" / "cfb_v6_input_audit.md"

SRC = REPO / "src" / "model_prediction"
F_FORECAST = SRC / "models" / "college_football.py"
F_WEATHER = SRC / "features" / "multi_sport_weather.py"
F_FEATURES = SRC / "features" / "cfb_features.py"
F_QB = SRC / "features" / "cfb_qb_availability.py"

# Hand-set weather thresholds copied from the extractor are re-derived by probe, not typed
# here; this key regex only decides which payload keys count as a raw weather reading.
RAW_WEATHER_KEY = re.compile(r"(^|_)(wind|temp|temperature|precip|precipitation)(_|$)", re.IGNORECASE)

POSSESSION_FIELDS = ("possessions", "home_success_rate", "away_success_rate")
EPA_FIELDS = ("home_epa_per_play", "away_epa_per_play")
WEATHER_FIELDS = ("temperature_f", "wind_mph", "precipitation_in", "is_dome")
DRIVE_PLAY_KEY = re.compile(r"drive|play|yard|poss|epa|success|snap|down", re.IGNORECASE)
# Genuine drive/play-level data, as opposed to the per-play rate fields (``*_per_play``).
TRUE_DRIVE_PLAY_KEY = re.compile(r"drive|(?<!per_)play(?!er)|snap|down|yard|turnover|punt", re.IGNORECASE)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def pct(num: int, den: int) -> str:
    return f"{100.0 * num / den:.1f}%" if den else "n/a"


# --------------------------------------------------------------------------------------
# Code-line lookup: every code claim in the report carries a file:line found by search.
# --------------------------------------------------------------------------------------
def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def find_line(path: Path, pattern: str, *, after: int = 0, before: int | None = None) -> dict[str, Any]:
    regex = re.compile(pattern)
    lines = _lines(path)
    stop = len(lines) if before is None else min(before, len(lines))
    for idx in range(max(after, 0), stop):
        if regex.search(lines[idx]):
            return {
                "file": str(path.relative_to(REPO)).replace("\\", "/"),
                "line": idx + 1,
                "text": lines[idx].strip(),
            }
    return {
        "file": str(path.relative_to(REPO)).replace("\\", "/"),
        "line": None,
        "text": f"UNVERIFIED: pattern {pattern!r} not found",
    }


def func_span(path: Path, func_name: str) -> tuple[int, int]:
    """(0-based start, exclusive end) of a top-level function in ``path``."""
    lines = _lines(path)
    start = next((i for i, ln in enumerate(lines) if re.match(rf"def {func_name}\b", ln)), None)
    if start is None:
        return (0, 0)
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"(def |class )", lines[i])), len(lines))
    return (start, end)


def find_in_func(path: Path, func_name: str, pattern: str) -> dict[str, Any]:
    start, end = func_span(path, func_name)
    return find_line(path, pattern, after=start, before=end)


# --------------------------------------------------------------------------------------
# Part (a): weather
# --------------------------------------------------------------------------------------
def weather_code_trace() -> dict[str, Any]:
    f_res = "resolve_cfb_game_weather"
    f_file = "_resolve_weather_from_file"
    none_returns = [
        (
            "neutral site, no venue name: unknown weather",
            find_in_func(F_WEATHER, f_res, r"neutral_site_venue_unknown"),
        ),
        (
            "weather file missing: unknown weather",
            find_in_func(F_WEATHER, f_file, r"if not weather_file\.exists\(\)"),
        ),
        (
            "decision time at or after kickoff, or no event start: unknown weather",
            find_in_func(F_WEATHER, f_file, r"decision_time >= event_time"),
        ),
        (
            "record is not an Open-Meteo forecast: skipped",
            find_in_func(F_WEATHER, f_file, r'rec\.get\("source"\) != "open_meteo:forecast"'),
        ),
        (
            "capture hash mismatch: skipped",
            find_in_func(F_WEATHER, f_file, r'rec\.get\("capture_sha256"\) != _weather_capture_hash'),
        ),
        (
            "forecast captured after decision time or at/after kickoff: skipped",
            find_in_func(F_WEATHER, f_file, r"captured > decision_time or captured >= event_time"),
        ),
        (
            "no hourly slot for the kickoff hour: skipped",
            find_in_func(F_WEATHER, f_file, r"target_hour = times\.index\(target\)"),
        ),
        (
            "temp, wind and precip must all be non-null to count as a hit",
            find_in_func(F_WEATHER, f_file, r"if None not in \(temp_f, wind_mph, precip_in\)"),
        ),
        (
            "no surviving record: (None, None, None, False)",
            find_in_func(F_WEATHER, f_file, r"if latest else \(None, None, None, False\)"),
        ),
    ]
    return {
        "forecast_call": find_line(F_FORECAST, r"resolve_cfb_game_weather\(\s*$"),
        "verified_flag": find_line(F_FORECAST, r"weather_inputs_verified = is_dome or None not in"),
        "upcoming_wind": find_line(F_FORECAST, r"wind_mph=w_wind"),
        "upcoming_temp": find_line(F_FORECAST, r"temperature_f=w_temp"),
        "upcoming_precip": find_line(F_FORECAST, r"precipitation_in=w_precip"),
        "predict_matchup": find_line(F_FORECAST, r"model\.predict_matchup\(history, upcoming"),
        "dome_return": find_in_func(F_WEATHER, f_res, r"return \(70\.0, 0\.0, 0\.0, True\)"),
        "weather_file_path": find_in_func(F_WEATHER, f_res, r'"weather" / "ncaaf_historical\.jsonl"'),
        "none_returns": [{"claim": claim, **ref} for claim, ref in none_returns],
        "extractor_default_temp": find_line(F_FEATURES, r"effective_temp = temperature_f if"),
        "extractor_default_wind": find_line(F_FEATURES, r"effective_wind = wind_mph if"),
        "extractor_default_precip": find_line(F_FEATURES, r"effective_precip = precipitation_in if"),
        "extractor_dome_gate": find_line(F_FEATURES, r"if not is_dome:"),
        "extractor_wind_term": find_line(F_FEATURES, r"if effective_wind > 14\.0:"),
        "extractor_precip_term": find_line(F_FEATURES, r"if effective_precip > 0\.05:"),
        "extractor_temp_term": find_line(F_FEATURES, r"if effective_temp < 32\.0:"),
        "extractor_dome_source": find_line(F_FEATURES, r"is_dome = home_obj\.is_dome"),
        "extractor_neutral_not_dome": find_line(F_FEATURES, r"is_dome = False"),
    }


def weather_probes() -> dict[str, Any]:
    """Run the real extractor and resolver on synthetic inputs (pure, offline)."""
    from model_prediction.data_sources.cfb_data import resolve_team
    from model_prediction.features.cfb_features import CFBFeatureExtractor
    from model_prediction.features.multi_sport_weather import resolve_cfb_game_weather

    extractor = CFBFeatureExtractor()
    outdoor_home, dome_home = "Michigan Wolverines", "Syracuse Orange"
    outdoor = resolve_team(outdoor_home)
    dome = resolve_team(dome_home)
    cases = (
        ("weather None/None/None, outdoor home", outdoor_home, {}),
        (
            "weather 70F / 5 mph / 0 in, outdoor home",
            outdoor_home,
            {"wind_mph": 5.0, "temperature_f": 70.0, "precipitation_in": 0.0},
        ),
        ("weather None/None/None, dome home", dome_home, {}),
        (
            "wind 20 mph / 20 F / 0.2 in, outdoor home",
            outdoor_home,
            {"wind_mph": 20.0, "temperature_f": 20.0, "precipitation_in": 0.2},
        ),
        (
            "wind 20 mph / 20 F / 0.2 in, dome home",
            dome_home,
            {"wind_mph": 20.0, "temperature_f": 20.0, "precipitation_in": 0.2},
        ),
    )
    extractor_rows = []
    for label, home, kwargs in cases:
        feat = extractor.extract_features(
            history=[],
            away_team="Ohio State Buckeyes",
            home_team=home,
            event_id="audit-probe",
            game_start_utc="2026-11-14T17:00Z",
            season_year=2026,
            week=11,
            **kwargs,
        )
        extractor_rows.append(
            {
                "case": label,
                "weather_total_adjustment": feat.weather_total_adjustment,
                "is_dome": feat.is_dome,
                "effective_wind_mph": feat.wind_mph,
                "effective_temperature_f": feat.temperature_f,
                "effective_precipitation_in": feat.precipitation_in,
            }
        )

    # Resolver probes against the repo weather file with a decision time before kickoff.
    data_root = REPO / "data"
    probes = []
    for label, home, game_date, neutral, venue in (
        (
            "outdoor home, game date before file coverage (2026-09-05)",
            outdoor_home,
            "2026-09-05",
            False,
            None,
        ),
        ("neutral site with no venue name", outdoor_home, "2026-11-14", True, None),
        ("dome home team (catalog)", dome_home, "2026-11-14", False, None),
    ):
        evidence: dict[str, Any] = {}
        result = resolve_cfb_game_weather(
            data_root,
            home,
            game_date,
            event_start_utc=f"{game_date}T17:00Z",
            observed_at_utc=f"{game_date}T12:00:00+00:00",
            provenance_out=evidence,
            is_neutral_site=neutral,
            venue_name=venue,
        )
        probes.append({"case": label, "result": list(result), "provenance": evidence})
    return {
        "catalog": {
            outdoor_home: {
                "canonical": outdoor.canonical_name if outdoor else None,
                "is_dome": outdoor.is_dome if outdoor else None,
            },
            dome_home: {
                "canonical": dome.canonical_name if dome else None,
                "is_dome": dome.is_dome if dome else None,
            },
        },
        "extractor": extractor_rows,
        "resolver": probes,
    }


def weather_file_coverage() -> dict[str, Any]:
    from model_prediction.features.multi_sport_weather import _weather_capture_hash

    out: dict[str, Any] = {}
    for label, path in WEATHER_FILES.items():
        if not path.exists():
            out[label] = {"path": str(path), "exists": False}
            continue
        rows = read_jsonl(path)
        sources = Counter(str(r.get("source")) for r in rows)
        forecast_rows = [r for r in rows if r.get("source") == "open_meteo:forecast"]
        hash_ok = sum(1 for r in forecast_rows if r.get("capture_sha256") == _weather_capture_hash(r))
        dates = sorted({str(r.get("date")) for r in rows if r.get("date")})
        observed = sorted(str(r["observed_at_utc"]) for r in forecast_rows if r.get("observed_at_utc"))
        out[label] = {
            "path": str(path),
            "exists": True,
            "rows": len(rows),
            "sha256": sha256_file(path),
            "sources": dict(sources),
            "forecast_rows_hash_verified": hash_ok,
            "game_dates_min": dates[0] if dates else None,
            "game_dates_max": dates[-1] if dates else None,
            "distinct_game_dates": len(dates),
            "forecast_observed_at_min": observed[0] if observed else None,
            "forecast_observed_at_max": observed[-1] if observed else None,
        }
    return out


def history_audit() -> dict[str, Any]:
    """Weather and possession/efficiency field facts for both history files."""
    import numpy as np

    report: dict[str, Any] = {}
    row_sets = []
    for path in HISTORY_FILES:
        rows = read_jsonl(path)
        row_sets.append(rows)
        key_counts = Counter(k for r in rows for k in r)

        def field_profile(field: str, rows: list[dict[str, Any]] = rows) -> dict[str, Any]:
            vals = [r[field] for r in rows if field in r and r[field] is not None]
            counter = Counter(vals)
            numeric = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
            return {
                "rows_with_value": len(vals),
                "rows_absent": len(rows) - len(vals),
                "distinct_values": len(counter),
                "top_values": [[v, c] for v, c in counter.most_common(4)],
                "min": min(numeric) if numeric else None,
                "max": max(numeric) if numeric else None,
            }

        legacy = [r for r in rows if "parser_version" not in r]
        new = [r for r in rows if "parser_version" in r]
        # 72 F / 0 mph rows should be exactly the is_dome rows (a constant placeholder pair).
        dome_rows = [r for r in legacy if r.get("is_dome") is True]
        dome_pair = Counter((r.get("temperature_f"), r.get("wind_mph")) for r in dome_rows)
        outdoor_pair = Counter(
            (r.get("temperature_f"), r.get("wind_mph")) for r in legacy if r.get("is_dome") is False
        )

        epa = np.array(
            [
                (
                    r["home_epa_per_play"],
                    r["away_epa_per_play"],
                    r["home_score"] - r["away_score"],
                    r["home_score"] + r["away_score"],
                )
                for r in rows
                if r.get("home_epa_per_play") is not None and r.get("away_epa_per_play") is not None
            ],
            dtype=float,
        )
        epa_summary: dict[str, Any] = {"rows": len(epa)}
        if len(epa):
            epa_summary.update(
                {
                    "corr_epa_diff_vs_margin": float(np.corrcoef(epa[:, 0] - epa[:, 1], epa[:, 2])[0, 1]),
                    "corr_home_epa_vs_total": float(np.corrcoef(epa[:, 0], epa[:, 3])[0, 1]),
                    "home_epa_exactly_zero": int((epa[:, 0] == 0).sum()),
                    "away_epa_exactly_zero": int((epa[:, 1] == 0).sum()),
                    "both_epa_exactly_zero": int(((epa[:, 0] == 0) & (epa[:, 1] == 0)).sum()),
                    "decimals_max": 3,
                }
            )
            # Values are stored to three decimals; confirm that rather than assert it.
            epa_summary["values_rounded_to_3dp"] = bool(
                np.allclose(np.round(epa[:, :2], 3), epa[:, :2], atol=1e-12)
            )
        newest_keys = Counter(k for r in new for k in r)
        report[str(path.relative_to(REPO)).replace("\\", "/")] = {
            "sha256": sha256_file(path),
            "rows": len(rows),
            "legacy_rows_without_parser_version": len(legacy),
            "newest_rows_with_parser_version": len(new),
            "parser_versions": dict(Counter(r.get("parser_version") for r in new)),
            "raw_sources_newest": len({r.get("raw_source") for r in new}),
            "rows_by_season": {
                str(k): v for k, v in sorted(Counter(r.get("season_year") for r in rows).items())
            },
            "key_counts": dict(key_counts),
            "weather_fields": {f: field_profile(f) for f in WEATHER_FIELDS},
            "weather_pairs_legacy_dome_rows": {f"{k[0]}F/{k[1]}mph": v for k, v in dome_pair.items()},
            "weather_pairs_legacy_outdoor_rows": {f"{k[0]}F/{k[1]}mph": v for k, v in outdoor_pair.items()},
            "possession_fields": {f: field_profile(f) for f in POSSESSION_FIELDS},
            "epa_fields": {f: field_profile(f) for f in EPA_FIELDS},
            "epa_diagnostics": epa_summary,
            "neutral_site_and_week": {
                "is_neutral_site_present": key_counts.get("is_neutral_site", 0),
                "is_neutral_site_absent": len(rows) - key_counts.get("is_neutral_site", 0),
                "week_present": key_counts.get("week", 0),
                "week_absent": len(rows) - key_counts.get("week", 0),
                "absent_rows_are_exactly_the_parser_version_rows": (
                    sum(
                        1
                        for r in rows
                        if "is_neutral_site" not in r and "week" not in r and "parser_version" in r
                    )
                    == len(rows) - key_counts.get("is_neutral_site", 0)
                ),
            },
            "drive_or_play_like_keys": {k: v for k, v in key_counts.items() if DRIVE_PLAY_KEY.search(k)},
            "newest_rows_keys_with_epa_or_possession": {
                k: v for k, v in newest_keys.items() if re.search(r"epa|poss|success", k, re.IGNORECASE)
            },
        }
    # The two files should be the same data; report rather than assume.
    a, b = row_sets
    report["files_identical_rows"] = a == b
    report["files_identical_sha256"] = (
        report[next(iter(report))]["sha256"] == report[list(report)[1]]["sha256"]
    )
    return report


def weather_never_fitted_evidence() -> dict[str, Any]:
    """Code-level evidence that no fit reads history weather: coefficients are literals and
    the extractor never reads weather off the history records."""
    f5_research = REPO / "scripts" / "cfb_v5_research.py"
    art = REPO / "config" / "models" / "cfb-structural-v5.json"
    history_weather_reads = []
    for idx, line in enumerate(_lines(F_FEATURES), start=1):
        if re.search(r"\b(g|game|rec|r|row)\.(temperature_f|wind_mph)\b", line):
            history_weather_reads.append({"line": idx, "text": line.strip()})
    research_hits = [
        {"line": i, "text": ln.strip()}
        for i, ln in enumerate(_lines(f5_research), start=1)
        if re.search(r"weather|wind|temperature", ln, re.IGNORECASE)
    ]
    art_hits = [
        {"line": i, "text": ln.strip()}
        for i, ln in enumerate(_lines(art), start=1)
        if re.search(r"weather|wind|temperature", ln, re.IGNORECASE)
    ]
    return {
        "extractor_reads_of_history_record_weather": history_weather_reads,
        "cfb_v5_research_weather_references": research_hits,
        "v5_artifact_weather_references": art_hits,
        "literal_coefficients": [
            find_line(F_FEATURES, r"0\.28 \* \(effective_wind - 14\.0\)"),
            find_line(F_FEATURES, r"weather_total_adj -= 1\.8"),
            find_line(F_FEATURES, r"0\.05 \* \(32\.0 - effective_temp\)"),
        ],
    }


# --------------------------------------------------------------------------------------
# Ledger scan (read-only)
# --------------------------------------------------------------------------------------
def _parse(raw: Any) -> Any:
    if raw in (None, ""):
        return None
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None


def _coerce(value: Any) -> Any:
    """Feature-payload values are stored as strings: JSON for numbers, a Python repr for
    dicts and booleans (``"{'away': {...}}"``, ``"True"``). Decode both, else keep the text."""
    if not isinstance(value, str):
        return value
    for decoder in (json.loads, ast.literal_eval):
        try:
            return decoder(value)
        except (ValueError, SyntaxError, TypeError):
            continue
    return value


def _num(value: Any) -> float:
    try:
        return float(_coerce(value))
    except (TypeError, ValueError):
        return 0.0


def _walk_raw_weather(obj: Any, path: str, hits: dict[str, int]) -> None:
    """Count non-empty values under keys that look like a raw weather reading."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            sub = f"{path}.{key}" if path else str(key)
            if isinstance(value, str) and value[:1] in ("{", "["):
                parsed = _coerce(value)
                if isinstance(parsed, (dict, list)):
                    _walk_raw_weather(parsed, sub, hits)
                    continue
            if RAW_WEATHER_KEY.search(str(key)) and not isinstance(value, (dict, list)):
                if value not in (None, ""):
                    hits[re.sub(r"\[\d+\]", "[]", sub)] += 1
            else:
                _walk_raw_weather(value, sub, hits)
    elif isinstance(obj, list):
        for idx, item in enumerate(obj[:50]):
            _walk_raw_weather(item, f"{path}[{idx}]", hits)


WIDE_WEATHER_KEY = re.compile(r"weather|wind|temp|precip", re.IGNORECASE)


def _census(obj: Any, path: str, census: dict[str, list[int]], unparsed: list[str]) -> None:
    """Every key matching weather/wind/temp/precip, with (occurrences, non-empty) counts.

    This is the positive control for the narrow raw-reading walker: it shows the traversal
    reaches those keys and whether any row stored weather under a different name. String
    values that look like JSON/Python containers are decoded; one that cannot be is recorded
    in ``unparsed`` so a silent decode failure cannot masquerade as "key absent".
    """
    if isinstance(obj, str) and obj[:1] in ("{", "["):
        decoded = _coerce(obj)
        if isinstance(decoded, (dict, list)):
            _census(decoded, path, census, unparsed)
        else:
            unparsed.append(path)
        return
    if isinstance(obj, dict):
        for key, value in obj.items():
            sub = f"{path}.{key}" if path else str(key)
            if WIDE_WEATHER_KEY.search(str(key)):
                cell = census.setdefault(re.sub(r"\[\d+\]", "[]", sub), [0, 0])
                cell[0] += 1
                if value not in (None, "", {}, [], "None"):
                    cell[1] += 1
            _census(value, sub, census, unparsed)
    elif isinstance(obj, list):
        for idx, item in enumerate(obj[:50]):
            _census(item, f"{path}[{idx}]", census, unparsed)


def _features_block(decision: Any, feature: Any) -> dict[str, Any] | None:
    for candidate in (
        (feature or {}).get("features") if isinstance(feature, dict) else None,
        ((feature or {}).get("model_input_snapshot") or {}).get("features")
        if isinstance(feature, dict)
        else None,
    ):
        if isinstance(candidate, dict):
            return {k: _coerce(v) for k, v in candidate.items()}
    if isinstance(decision, dict):
        snap = _parse(decision.get("model_input_snapshot_json"))
        if isinstance(snap, dict) and isinstance(snap.get("features"), dict):
            return {k: _coerce(v) for k, v in snap["features"].items()}
    return None


def load_ledger_rows() -> list[dict[str, Any]]:
    uri = f"file:{LEDGER_DB.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        records = conn.execute(
            "SELECT pick_id, ledger_tier, status, model_id, event_id, canonical_event_id, "
            "event_start_utc, market_type, created_at_utc, decision_payload_json, "
            "feature_payload_json FROM ledger_records WHERE sport = 'ncaaf' AND created_at_utc <= ?",
            (AS_OF_UTC,),
        ).fetchall()
    finally:
        conn.close()
    rows = []
    for rec in records:
        decision = _parse(rec["decision_payload_json"])
        feature = _parse(rec["feature_payload_json"])
        feats = _features_block(decision, feature)
        raw_hits: dict[str, int] = defaultdict(int)
        _walk_raw_weather(decision, "decision", raw_hits)
        _walk_raw_weather(feature, "feature", raw_hits)
        census: dict[str, list[int]] = {}
        unparsed: list[str] = []
        _census(decision, "decision", census, unparsed)
        _census(feature, "feature", census, unparsed)
        rows.append(
            {
                "pick_id": rec["pick_id"],
                "tier": rec["ledger_tier"],
                "status": rec["status"],
                "model_id": rec["model_id"],
                "event": rec["event_id"] or rec["canonical_event_id"] or f"pick:{rec['pick_id']}",
                "start": rec["event_start_utc"],
                "market": rec["market_type"],
                "created": rec["created_at_utc"],
                "has_decision_payload": decision is not None,
                "has_feature_payload": feature is not None,
                "feats": feats,
                "raw_hits": dict(raw_hits),
                "census": census,
                "unparsed": unparsed,
            }
        )
    return rows


def _wx_summary(items: list[dict[str, Any]], weather_start: str | None) -> dict[str, Any]:
    n = len(items)
    with_feats = [r for r in items if r["feats"] is not None]
    adj_present = [r for r in with_feats if "weather_total_adjustment" in r["feats"]]
    adj_nonzero = [r for r in adj_present if abs(_num(r["feats"]["weather_total_adjustment"])) > 0.0]
    verified = Counter(str(r["feats"].get("weather_inputs_verified", "<absent>")) for r in with_feats)
    sources: Counter = Counter()
    for r in with_feats:
        ev = r["feats"].get("weather_evidence")
        if not isinstance(ev, dict):
            sources["<key absent>"] += 1
        elif not ev:
            sources["<empty evidence {}>"] += 1
        else:
            sources[str(ev.get("source"))] += 1
    is_dome = Counter(str(r["feats"].get("is_dome", "<absent>")) for r in with_feats)
    raw_rows = [r for r in items if r["raw_hits"]]
    raw_keys: Counter = Counter()
    for r in raw_rows:
        raw_keys.update(r["raw_hits"].keys())
    # Non-dome rows whose evidence is a real forecast: the only rows where the adjustment can
    # be non-zero. Rows before the weather file's first game date cannot have a forecast.
    live_forecast = [
        r
        for r in with_feats
        if isinstance(r["feats"].get("weather_evidence"), dict)
        and str(r["feats"]["weather_evidence"].get("source", "")).startswith("open_meteo")
    ]
    before_cov = [
        r for r in with_feats if weather_start and r["start"] and str(r["start"])[:10] < weather_start
    ]
    return {
        "n": n,
        "with_feature_features_block": len(with_feats),
        "without_feature_block": n - len(with_feats),
        "has_weather_total_adjustment_field": len(adj_present),
        "weather_total_adjustment_nonzero": len(adj_nonzero),
        "weather_total_adjustment_nonzero_values": sorted(
            {round(_num(r["feats"]["weather_total_adjustment"]), 2) for r in adj_nonzero}
        ),
        "weather_inputs_verified": dict(verified),
        "weather_evidence_source": dict(sources),
        "is_dome": dict(is_dome),
        "rows_with_nonnull_raw_wind_temp_precip_key": len(raw_rows),
        "raw_weather_key_paths": dict(raw_keys),
        "rows_with_open_meteo_forecast_evidence": len(live_forecast),
        "rows_with_event_before_weather_file_coverage": len(before_cov),
        "nonzero_adjustment_events": sorted({r["event"] for r in adj_nonzero}),
    }


def _qb_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    sides = []
    for r in items:
        feats = r["feats"] or {}
        qb = feats.get("qb_availability")
        for side in ("away", "home"):
            cell = qb.get(side) if isinstance(qb, dict) else None
            sides.append(
                {
                    "event": r["event"],
                    "side": side,
                    "start": r["start"],
                    "team": feats.get(f"{side}_team"),
                    "cell": cell if isinstance(cell, dict) else None,
                    "adjustment": _num(feats.get(f"{side}_qb_value_adjustment")),
                }
            )
    present = [s for s in sides if s["cell"] is not None]
    status = Counter(str(s["cell"].get("status")) for s in present)
    reason = Counter(str(s["cell"].get("reason")) for s in present)
    prob = Counter(round(float(s["cell"].get("starter_prob", 1.0)), 4) for s in present)
    discount_fired = [s for s in present if float(s["cell"].get("starter_prob", 1.0)) < 0.99]
    adj_nonzero = [s for s in sides if s["adjustment"] not in (None, 0, 0.0)]
    no_name = [
        s for s in present if not s["cell"].get("starter_name") and not s["cell"].get("starter_athlete_id")
    ]
    return {
        "side_cells_total": len(sides),
        "side_cells_with_qb_availability": len(present),
        "side_cells_without_qb_availability": len(sides) - len(present),
        "status": dict(status),
        "reason": dict(reason),
        "starter_prob": {str(k): v for k, v in sorted(prob.items())},
        "side_cells_starter_prob_below_0_99": len(discount_fired),
        "side_cells_nonzero_qb_value_adjustment": len(adj_nonzero),
        "side_cells_starter_unresolved": len(no_name),
    }


def _starter_stability(latest_rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_team: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for r in latest_rows:
        feats = r["feats"] or {}
        qb = feats.get("qb_availability")
        if not isinstance(qb, dict):
            continue
        for side in ("away", "home"):
            cell = qb.get(side)
            team = feats.get(f"{side}_team")
            if isinstance(cell, dict) and team and cell.get("starter_name") and r["start"]:
                by_team[str(team)].append((str(r["start"]), str(cell["starter_name"])))
    multi = {}
    for team, seq in by_team.items():
        names = {name for _, name in seq}
        if len(names) > 1:
            multi[team] = sorted(names)
    games_per_team = Counter(len(v) for v in by_team.values())
    return {
        "teams_with_named_starter": len(by_team),
        "teams_with_two_or_more_distinct_starter_names": len(multi),
        "examples": dict(list(multi.items())[:8]),
        "games_per_team_distribution": {str(k): v for k, v in sorted(games_per_team.items())},
    }


def ledger_extras(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Presence by model, key census, decode check, non-zero cross-tab, live-file match."""
    by_model: dict[str, dict[str, int]] = {}
    for r in rows:
        feats = r["feats"] or {}
        cell = by_model.setdefault(
            str(r["model_id"]),
            {
                "rows": 0,
                "with_features_block": 0,
                "has_weather_total_adjustment": 0,
                "weather_total_adjustment_nonzero": 0,
                "has_weather_evidence_key": 0,
                "has_qb_availability_key": 0,
                "has_away_qb_value_adjustment": 0,
                "has_home_qb_value_adjustment": 0,
                "qb_value_adjustment_nonzero": 0,
            },
        )
        cell["rows"] += 1
        cell["with_features_block"] += 1 if r["feats"] is not None else 0
        cell["has_weather_total_adjustment"] += 1 if "weather_total_adjustment" in feats else 0
        cell["weather_total_adjustment_nonzero"] += (
            1 if abs(_num(feats.get("weather_total_adjustment"))) > 0.0 else 0
        )
        cell["has_weather_evidence_key"] += 1 if "weather_evidence" in feats else 0
        cell["has_qb_availability_key"] += 1 if "qb_availability" in feats else 0
        cell["has_away_qb_value_adjustment"] += 1 if "away_qb_value_adjustment" in feats else 0
        cell["has_home_qb_value_adjustment"] += 1 if "home_qb_value_adjustment" in feats else 0
        cell["qb_value_adjustment_nonzero"] += (
            1
            if abs(_num(feats.get("away_qb_value_adjustment"))) > 0.0
            or abs(_num(feats.get("home_qb_value_adjustment"))) > 0.0
            else 0
        )

    census_total: dict[str, list[int]] = {}
    for r in rows:
        for key, (occ, nonempty) in r["census"].items():
            cell = census_total.setdefault(key, [0, 0, 0])
            cell[0] += 1  # rows carrying the key
            cell[1] += occ
            cell[2] += nonempty
    unparsed_rows = [r for r in rows if r["unparsed"]]

    nonzero = [r for r in rows if r["feats"] and abs(_num(r["feats"].get("weather_total_adjustment"))) > 0.0]
    cross: Counter = Counter()
    nonzero_detail = []
    for r in nonzero:
        ev = r["feats"].get("weather_evidence")
        if not isinstance(ev, dict):
            src = "<key absent>"
        elif not ev:
            src = "<empty {}>"
        else:
            src = str(ev.get("source"))
        cross[(str(r["model_id"]), src, str(r["created"])[:10])] += 1
        nonzero_detail.append(
            {
                "pick_id": r["pick_id"],
                "tier": r["tier"],
                "model_id": r["model_id"],
                "event": r["event"],
                "event_start": r["start"],
                "created": r["created"],
                "adjustment": round(_num(r["feats"].get("weather_total_adjustment")), 2),
                "evidence_source": src,
                "evidence_observed_at_utc": ev.get("observed_at_utc") if isinstance(ev, dict) else None,
                "is_dome": r["feats"].get("is_dome"),
                "weather_inputs_verified": r["feats"].get("weather_inputs_verified"),
                "created_before_event_start": bool(
                    r["start"] and str(r["created"]).replace("Z", "") < str(r["start"]).replace("Z", "")
                ),
            }
        )
    # Which weather file does the live process read? Match recorded capture hashes to each file.
    recorded: set[str] = set()
    for r in rows:
        ev = (r["feats"] or {}).get("weather_evidence")
        if isinstance(ev, dict) and ev.get("capture_sha256"):
            recorded.add(str(ev["capture_sha256"]))
    file_hashes: dict[str, set[str]] = {}
    for label, path in WEATHER_FILES.items():
        if path.exists():
            file_hashes[label] = {
                str(rec["capture_sha256"]) for rec in read_jsonl(path) if rec.get("capture_sha256")
            }
    match = {label: len(recorded & hashes) for label, hashes in file_hashes.items()}
    return {
        "presence_by_model": by_model,
        "weather_key_census": {
            k: {"rows_with_key": v[0], "occurrences": v[1], "non_empty": v[2]}
            for k, v in sorted(census_total.items())
        },
        "payload_strings_that_failed_to_decode": {
            "rows": len(unparsed_rows),
            "examples": [r["unparsed"][:3] for r in unparsed_rows[:3]],
        },
        "weather_total_adjustment_nonzero_cross_tab": [
            {"model_id": m, "evidence_source": src, "created_date": d, "rows": n}
            for (m, src, d), n in sorted(cross.items())
        ],
        "weather_total_adjustment_nonzero_rows": nonzero_detail,
        "recorded_capture_sha256_distinct": len(recorded),
        "recorded_capture_sha256_found_in_file": match,
        "recorded_capture_sha256_total_in_file": {label: len(h) for label, h in file_hashes.items()},
    }


def ledger_audit() -> dict[str, Any]:
    rows = load_ledger_rows()
    weather_cov = weather_file_coverage()
    weather_start = weather_cov.get("repo", {}).get("game_dates_min")

    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_event[r["event"]].append(r)
    latest_per_event = [max(group, key=lambda x: str(x["created"])) for group in by_event.values()]

    any_wx = _wx_summary(rows, weather_start)
    # Event-level "any row" view for the headline counts.
    event_flags: dict[str, dict[str, bool]] = {}
    for event, group in by_event.items():
        group_feats = [g for g in group if g["feats"] is not None]
        event_flags[event] = {
            "has_feature_block": bool(group_feats),
            "nonzero_adj": any(
                abs(_num(g["feats"].get("weather_total_adjustment"))) > 0.0 for g in group_feats
            ),
            "open_meteo_evidence": any(
                isinstance(g["feats"].get("weather_evidence"), dict)
                and str(g["feats"]["weather_evidence"].get("source", "")).startswith("open_meteo")
                for g in group_feats
            ),
            "raw_weather_key": any(g["raw_hits"] for g in group),
            "qb_cell": any(isinstance(g["feats"].get("qb_availability"), dict) for g in group_feats),
            "qb_prob_below_0_99": any(
                float(c.get("starter_prob", 1.0)) < 0.99
                for g in group_feats
                if isinstance(g["feats"].get("qb_availability"), dict)
                for c in g["feats"]["qb_availability"].values()
                if isinstance(c, dict)
            ),
        }
    event_totals = {
        k: sum(1 for f in event_flags.values() if f[k]) for k in next(iter(event_flags.values()), {})
    }

    starts = sorted(str(r["start"])[:10] for r in rows if r["start"])
    return {
        "ledger_db": str(LEDGER_DB),
        "opened_mode": "sqlite uri mode=ro",
        "filter": f"sport = 'ncaaf', all tiers, all statuses, created_at_utc <= {AS_OF_UTC}",
        "rows": len(rows),
        "events": len(by_event),
        "rows_by_tier_status": {
            f"{t}/{s}": c for (t, s), c in sorted(Counter((r["tier"], r["status"]) for r in rows).items())
        },
        "rows_by_model": dict(Counter(str(r["model_id"]) for r in rows)),
        "rows_by_market": dict(Counter(str(r["market"]) for r in rows)),
        "event_start_min": starts[0] if starts else None,
        "event_start_max": starts[-1] if starts else None,
        "payload_presence": {
            "decision_payload": sum(1 for r in rows if r["has_decision_payload"]),
            "feature_payload": sum(1 for r in rows if r["has_feature_payload"]),
        },
        "as_of_filter": f"created_at_utc <= {AS_OF_UTC}",
        "extras": ledger_extras(rows),
        "weather_row_level": any_wx,
        "weather_event_level_any_row": {"events": len(by_event), **event_totals},
        "weather_latest_row_per_event": _wx_summary(latest_per_event, weather_start),
        "qb_cell_row_created_at_range": [
            min(
                (
                    str(r["created"])
                    for r in rows
                    if isinstance((r["feats"] or {}).get("qb_availability"), dict)
                ),
                default=None,
            ),
            max(
                (
                    str(r["created"])
                    for r in rows
                    if isinstance((r["feats"] or {}).get("qb_availability"), dict)
                ),
                default=None,
            ),
        ],
        "qb_row_level": _qb_summary(rows),
        "qb_latest_row_per_event": _qb_summary(latest_per_event),
        "starter_name_stability_latest_row_per_event": _starter_stability(latest_per_event),
        "rows_with_feature_block_by_event_start_month": {
            k: v
            for k, v in sorted(
                Counter(str(r["start"])[:7] for r in rows if r["feats"] is not None and r["start"]).items()
            )
        },
    }


# --------------------------------------------------------------------------------------
# Part (b): QB
# --------------------------------------------------------------------------------------
def qb_code_trace() -> dict[str, Any]:
    return {
        "status_map": find_line(F_QB, r"^STATUS_STARTER_PROBABILITY"),
        "no_evidence_prob": find_in_func(F_QB, "_no_evidence", r"starter_prob=1\.0"),
        "not_listed_status": find_line(F_QB, r'status="not_listed"'),
        "not_listed_prob": find_line(
            F_QB,
            r"starter_prob=1\.0",
            after=find_line(F_QB, r'status="not_listed"')["line"] - 4
            if find_line(F_QB, r'status="not_listed"')["line"]
            else 0,
        ),
        "not_listed_reason": find_line(F_QB, r'reason="starter_absent_from_report"'),
        "unmapped_status_prob": find_line(F_QB, r"probability = 1\.0"),
        "passing_leader_def": find_line(F_QB, r"^def passing_leader_from_competitor"),
        "passing_leader_docstring": find_line(F_QB, r"The season passing leader is the de facto starter"),
        "passing_leader_groups": find_line(F_QB, r'"passingLeader", "passingYards"'),
        "forecast_leader_call": find_line(F_FORECAST, r"passing_leader_from_competitor\(away\)"),
        "forecast_resolve_call": find_line(F_FORECAST, r"away_qb = resolve_cfb_qb_availability\("),
        "forecast_prob_to_game": find_line(F_FORECAST, r"qb_starter_prob_away=away_qb\.starter_prob"),
        "extractor_discount_gate": find_line(F_FEATURES, r"away_qb_adj = \(1\.0 - qb_starter_prob_away\)"),
        "extractor_adjustment_field": find_line(F_FEATURES, r"away_qb_value_adjustment=away_qb_adj"),
        "history_qb_experience_field": find_line(F_FEATURES, r"home_qb_experience_starts:"),
        "history_qb_experience_reads": [
            {"line": i, "text": ln.strip()}
            for i, ln in enumerate(_lines(F_FEATURES), start=1)
            if "qb_experience_starts" in ln and not re.match(r"\s+\w+_qb_experience_starts\s*:", ln)
        ],
    }


def qb_probes() -> dict[str, Any]:
    from datetime import UTC, datetime

    from model_prediction.features.cfb_qb_availability import (
        passing_leader_from_competitor,
        resolve_cfb_qb_availability,
    )

    obs = datetime(2026, 11, 14, 18, 0, tzinfo=UTC)
    snap = {
        "observed_at_utc": "2026-11-14T12:00:00+00:00",
        "source": "espn_cfb_injuries",
        "entries": [
            {
                "team": "Ohio State Buckeyes",
                "position": "QB",
                "player_name": "Hurt Starter",
                "athlete_id": "1",
                "status": "Out",
            },
            {
                "team": "Ohio State Buckeyes",
                "position": "WR",
                "player_name": "Other Guy",
                "athlete_id": "9",
                "status": "Out",
            },
        ],
    }
    cases = {
        "starter absent from report": {
            "snapshot": snap,
            "team": "Ohio State Buckeyes",
            "starter_athlete_id": "2",
            "starter_name": "Healthy Starter",
        },
        "starter listed Out": {
            "snapshot": snap,
            "team": "Ohio State Buckeyes",
            "starter_athlete_id": "1",
            "starter_name": "Hurt Starter",
        },
        "no snapshot": {
            "snapshot": None,
            "team": "Ohio State Buckeyes",
            "starter_athlete_id": "2",
            "starter_name": "Healthy Starter",
        },
        "team with no entries at all": {
            "snapshot": snap,
            "team": "Michigan Wolverines",
            "starter_athlete_id": "3",
            "starter_name": "Other QB",
        },
    }
    resolved = {}
    for label, kwargs in cases.items():
        out = resolve_cfb_qb_availability(observed_at=obs, **kwargs)
        resolved[label] = {"starter_prob": out.starter_prob, "status": out.status, "reason": out.reason}

    competitor = {
        "leaders": [
            {
                "name": "passingYards",
                "leaders": [
                    {
                        "athlete": {
                            "id": "42",
                            "displayName": "Season Yards Leader",
                            "position": {"abbreviation": "QB"},
                        },
                        "displayValue": "2,500 YDS (season to date)",
                    }
                ],
            }
        ]
    }
    leader = passing_leader_from_competitor(competitor)
    return {
        "resolver": resolved,
        "passing_leader_synthetic_competitor": list(leader),
        "passing_leader_note": (
            "The function returns whichever athlete the scoreboard lists first under passingLeader or "
            "passingYards; it carries no game-day depth-chart or snap information."
        ),
    }


def injury_snapshot_audit() -> dict[str, Any]:
    paths = sorted(INJURY_SNAPSHOT_DIR.glob("*/*.json"))
    qb_status: Counter = Counter()
    all_status: Counter = Counter()
    qb_teams: set[str] = set()
    observed: list[str] = []
    snapshots_without_qb = 0
    qb_entries_total = 0
    qb_penalising: list[dict[str, str]] = []
    per_snapshot: list[int] = []
    distinct_entries: set[tuple[str, str, str, str]] = set()
    all_teams: set[str] = set()
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        observed.append(str(payload.get("observed_at_utc")))
        entries = payload.get("entries") or []
        per_snapshot.append(len(entries))
        for e in entries:
            all_teams.add(str(e.get("team")))
            distinct_entries.add(
                (str(e.get("team")), str(e.get("player_name")), str(e.get("position")), str(e.get("status")))
            )
        qbs = [e for e in entries if str(e.get("position") or "").upper() == "QB"]
        if not qbs:
            snapshots_without_qb += 1
        for e in entries:
            all_status[str(e.get("status"))] += 1
        for e in qbs:
            qb_entries_total += 1
            qb_status[str(e.get("status"))] += 1
            qb_teams.add(str(e.get("team")))
            if str(e.get("status")) in ("Out", "IR", "Doubtful", "Questionable"):
                qb_penalising.append(
                    {
                        "observed": str(payload.get("observed_at_utc")),
                        "team": str(e.get("team")),
                        "status": str(e.get("status")),
                        "player": str(e.get("player_name")),
                    }
                )
    observed.sort()
    distinct_penalised = {(q["team"], q["player"], q["status"]) for q in qb_penalising}
    return {
        "dir": str(INJURY_SNAPSHOT_DIR.relative_to(REPO)).replace("\\", "/"),
        "snapshots": len(paths),
        "observed_at_min": observed[0] if observed else None,
        "observed_at_max": observed[-1] if observed else None,
        "entries_total_all_positions": sum(per_snapshot),
        "entries_per_snapshot_min": min(per_snapshot) if per_snapshot else None,
        "entries_per_snapshot_max": max(per_snapshot) if per_snapshot else None,
        "distinct_team_player_position_status_entries": len(distinct_entries),
        "distinct_teams_any_position": len(all_teams),
        "snapshots_with_zero_qb_entries": snapshots_without_qb,
        "qb_entries_total_across_snapshots": qb_entries_total,
        "distinct_teams_with_any_qb_entry": len(qb_teams),
        "qb_entry_status": dict(qb_status),
        "all_position_entry_status": dict(all_status),
        "qb_entries_with_penalising_status_snapshot_level": len(qb_penalising),
        "qb_penalising_distinct_team_player_status": sorted(distinct_penalised),
    }


# --------------------------------------------------------------------------------------
# Verdicts and rendering
# --------------------------------------------------------------------------------------
def build_verdicts(report: dict[str, Any]) -> dict[str, Any]:
    led = report["ledger"]
    wx = led["weather_row_level"]
    ev = led["weather_event_level_any_row"]
    qb = led["qb_row_level"]
    hist = next(v for k, v in report["history"].items() if k.endswith("ncaaf_games_all.jsonl"))
    wf = hist["weather_fields"]
    pf = hist["possession_fields"]
    constant_weather = (
        wf["temperature_f"]["distinct_values"] <= 2
        and wf["wind_mph"]["distinct_values"] <= 2
        and wf["precipitation_in"]["rows_with_value"] == 0
    )
    constant_poss = (
        pf["possessions"]["distinct_values"] == 1 and pf["home_success_rate"]["distinct_values"] == 1
    )
    return {
        "weather_training_history_placeholder": bool(constant_weather),
        "weather_term_never_fitted": bool(constant_weather),
        "weather_adjustment_nonzero_rows": wx["weather_total_adjustment_nonzero"],
        "weather_adjustment_nonzero_events": ev["nonzero_adj"],
        "weather_rows_total": wx["n"],
        "weather_open_meteo_evidence_rows": wx["rows_with_open_meteo_forecast_evidence"],
        "weather_raw_reading_rows": wx["rows_with_nonnull_raw_wind_temp_precip_key"],
        "qb_discount_fired_side_cells": qb["side_cells_starter_prob_below_0_99"],
        "qb_nonzero_adjustment_side_cells": qb["side_cells_nonzero_qb_value_adjustment"],
        "qb_side_cells_with_qb_availability": qb["side_cells_with_qb_availability"],
        "possession_fields_constant": bool(constant_poss),
        "drive_or_play_fields_present": any(
            TRUE_DRIVE_PLAY_KEY.search(k) for k in hist["drive_or_play_like_keys"]
        ),
        "pace_conclusion": (
            "No drive or play data exists in the history, possessions is a single constant, and EPA has no "
            "recorded provenance, so v6 pace cannot be measured and is estimated from scoring through the "
            "additive points ratings."
        ),
    }


def render_md(report: dict[str, Any]) -> str:
    led = report["ledger"]
    wx = led["weather_row_level"]
    wxl = led["weather_latest_row_per_event"]
    ev = led["weather_event_level_any_row"]
    qb = led["qb_row_level"]
    qbl = led["qb_latest_row_per_event"]
    wt = report["weather_trace"]
    wp = report["weather_probes"]
    wfc = report["weather_file_coverage"]
    qt = report["qb_trace"]
    qp = report["qb_probes"]
    snap = report["injury_snapshots"]
    verd = report["verdicts"]
    snap_v = report["injury_snapshots"]
    hist_all = next(v for k, v in report["history"].items() if k.endswith("ncaaf_games_all.jsonl"))
    hist_proc = next(
        v for k, v in report["history"].items() if k.endswith("games.jsonl") and "processed" in k
    )

    def ref(d: dict[str, Any]) -> str:
        return f"`{d['file']}:{d['line']}`" if d.get("line") else f"`{d['file']}` (UNVERIFIED)"

    out: list[str] = []
    out.append("# NCAAF v6 input audit: weather, QB and pace data")
    out.append("")
    out.append(f"AS_OF_UTC: {report['as_of_utc']}  (generated {report['generated_at_utc']})")
    out.append("")
    out.append(
        "Read-only. Ledger opened with a `mode=ro` URI; no network, no order endpoint, no write to history,"
    )
    out.append(
        "ledger or src. Quote format: percentages and points only. Every code line below was located by"
    )
    out.append("searching the source at run time.")
    out.append("")
    out.append("## Verdict")
    out.append("")
    out.append(
        f"- **Weather adjustment: not fitted and almost never non-zero, so effectively dead in the numbers; "
        f"live only in plumbing since the weather capture began ({report['weather_file_coverage']['repo']['game_dates_min']}).** "
        f"Across {wx['n']} NCAAF ledger rows ({led['events']} events) the adjustment is non-zero on "
        f"{wx['weather_total_adjustment_nonzero']} rows ({pct(wx['weather_total_adjustment_nonzero'], wx['n'])}; "
        f"{pct(wx['weather_total_adjustment_nonzero'], wx['rows_with_open_meteo_forecast_evidence'])} of the "
        f"{wx['rows_with_open_meteo_forecast_evidence']} rows that carried a real forecast), on {ev['nonzero_adj']} "
        f"events, with a largest size of {max((abs(v) for v in wx['weather_total_adjustment_nonzero_values']), default=0.0)} "
        "total points. The training history carries only constant placeholder weather, so the coefficients "
        "(wind, precipitation, cold) are hand-set literals that were never fitted or validated."
    )
    out.append(
        f"- **QB adjustment: dead input.** The -4.5 point backup discount fired on "
        f"{qb['side_cells_starter_prob_below_0_99']} of {qb['side_cells_with_qb_availability']} recorded side "
        f"cells and {qb['side_cells_nonzero_qb_value_adjustment']} side cells carry a non-zero adjustment; the "
        "starter is the season passing leader, not a point-in-time starter, a starter missing from the "
        f"report is scored as available (1.0), and the captured injury feed holds {snap_v['entries_total_all_positions']} "
        f"entries over {snap_v['snapshots']} snapshots with {snap_v['qb_entries_total_across_snapshots']} QB entries."
    )
    out.append(
        "- **Pace data: none.** No drive or play fields exist; possessions is the constant 12.5, success rate the "
        "constant 0.42, EPA has no provenance. v6 estimates pace from scoring via additive points ratings."
    )
    out.append("")
    out.append("## (a) Weather")
    out.append("")
    out.append("### Code path (forecast to extractor)")
    out.append("")
    out.append(
        f"1. `build_cfb_slate` calls `resolve_cfb_game_weather` at {ref(wt['forecast_call'])} with the home team,"
    )
    out.append(
        f"   date, event start and the decision time `obs`; the weather file is `data_root/weather/ncaaf_historical.jsonl` ({ref(wt['weather_file_path'])})."
    )
    out.append(
        f"2. `weather_inputs_verified = is_dome or None not in (temp, wind, precip)` at {ref(wt['verified_flag'])}."
    )
    out.append(
        f"3. The three values go into `UpcomingCFBGame` ({ref(wt['upcoming_wind'])}, {ref(wt['upcoming_temp'])}, "
        f"{ref(wt['upcoming_precip'])}) and then `predict_matchup` ({ref(wt['predict_matchup'])}), which hands them to "
        "`extract_features`."
    )
    out.append("")
    out.append("`resolve_cfb_game_weather` returns `(None, None, None, False)` when:")
    out.append("")
    for item in wt["none_returns"]:
        out.append(f"- {item['claim']} ({ref(item)})")
    out.append("")
    out.append(f"A dome home team returns `(70.0, 0.0, 0.0, True)` ({ref(wt['dome_return'])}).")
    out.append("")
    out.append("In `extract_features` a `None` becomes the default:")
    out.append("")
    out.append(
        f"- temperature 70 F ({ref(wt['extractor_default_temp'])}), wind 5 mph ({ref(wt['extractor_default_wind'])}), precipitation 0 in ({ref(wt['extractor_default_precip'])})."
    )
    out.append(
        f"- The adjustment only runs `if not is_dome` ({ref(wt['extractor_dome_gate'])}) and only below/above the "
        f"hand-set thresholds: wind over 14 mph ({ref(wt['extractor_wind_term'])}), precipitation over 0.05 in "
        f"({ref(wt['extractor_precip_term'])}), temperature under 32 F ({ref(wt['extractor_temp_term'])}). "
        "70 F / 5 mph / 0 in trips none of them, so the adjustment is exactly 0.0 outside domes, and it is 0.0 in domes by the gate."
    )
    out.append(
        f"- `is_dome` inside the extractor comes from the home team's catalog entry ({ref(wt['extractor_dome_source'])}) "
        f"and is forced False on neutral sites ({ref(wt['extractor_neutral_not_dome'])}); an indoor neutral venue "
        "still resolves to 70 F / 0 mph / 0 in, so it also scores 0.0."
    )
    out.append("")
    out.append("Probe of the real extractor (`history=[]`, synthetic matchup, offline):")
    out.append("")
    out.append("| Case | weather_total_adjustment | extractor is_dome | wind mph | temp F | precip in |")
    out.append("|---|---|---|---|---|---|")
    for r in wp["extractor"]:
        out.append(
            f"| {r['case']} | {r['weather_total_adjustment']} | {r['is_dome']} | {r['effective_wind_mph']} | "
            f"{r['effective_temperature_f']} | {r['effective_precipitation_in']} |"
        )
    out.append("")
    out.append("Probe of the real resolver against the repo weather file (decision 5 h before kickoff):")
    out.append("")
    out.append("| Case | Result (temp, wind, precip, is_dome) | Provenance |")
    out.append("|---|---|---|")
    for r in wp["resolver"]:
        out.append(f"| {r['case']} | {tuple(r['result'])} | {json.dumps(r['provenance'], sort_keys=True)} |")
    out.append("")
    out.append("### Weather files on disk")
    out.append("")
    out.append("| File | Rows | Game dates | Forecast rows hash-verified | Sources |")
    out.append("|---|---|---|---|---|")
    for label, info in wfc.items():
        if not info.get("exists"):
            out.append(f"| {label}: `{info['path']}` | missing | | | |")
            continue
        out.append(
            f"| {label}: `{info['path']}` | {info['rows']} | {info['game_dates_min']} to {info['game_dates_max']} "
            f"({info['distinct_game_dates']} dates) | {info['forecast_rows_hash_verified']} | {info['sources']} |"
        )
    out.append("")
    out.append(
        "The forecast reads `data_root/weather/...`; `data_root` is the parent of `project.ledger_path` "
        f"(`config/model.yaml`: `data/picks.xlsx`), i.e. the repo `data/` folder unless `MODEL_PREDICTION_LEDGER` is set "
        f"(value at audit time: {report['env']['MODEL_PREDICTION_LEDGER']!r}). The ledger's recorded capture "
        "hashes (matched below) identify which file actually served the forecasts."
    )
    out.append("")
    out.append("### Ledger counts (sport = ncaaf, every tier and status)")
    out.append("")
    out.append(f"Rows {led['rows']}, events {led['events']}; tier/status {led['rows_by_tier_status']}.")
    out.append(
        f"Event start range {led['event_start_min']} to {led['event_start_max']}; models {led['rows_by_model']}."
    )
    out.append("")
    out.append("| Measure | Row level (all rows) | Event level (any row) | Latest row per event |")
    out.append("|---|---|---|---|")
    out.append(f"| Units | {wx['n']} | {ev['events']} | {wxl['n']} |")
    out.append(
        f"| With a feature `features` block | {wx['with_feature_features_block']} | {ev['has_feature_block']} | {wxl['with_feature_features_block']} |"
    )
    out.append(
        f"| `weather_total_adjustment` non-zero | {wx['weather_total_adjustment_nonzero']} | {ev['nonzero_adj']} | {wxl['weather_total_adjustment_nonzero']} |"
    )
    out.append(
        f"| Evidence source is an Open-Meteo forecast | {wx['rows_with_open_meteo_forecast_evidence']} | {ev['open_meteo_evidence']} | {wxl['rows_with_open_meteo_forecast_evidence']} |"
    )
    out.append(
        f"| Non-null raw wind/temp/precip value under any matching key | {wx['rows_with_nonnull_raw_wind_temp_precip_key']} | {ev['raw_weather_key']} | {wxl['rows_with_nonnull_raw_wind_temp_precip_key']} |"
    )
    out.append("")
    out.append(f"Row-level `weather_inputs_verified`: {wx['weather_inputs_verified']}.")
    out.append(f"Row-level `weather_evidence.source`: {wx['weather_evidence_source']}.")
    out.append(f"Row-level extractor-side `is_dome`: {wx['is_dome']}.")
    out.append(
        f"Latest-row-per-event `weather_inputs_verified`: {wxl['weather_inputs_verified']}; `weather_evidence.source`: {wxl['weather_evidence_source']}."
    )
    out.append(
        f"Rows whose event starts before the repo weather file's first game date: {wx['rows_with_event_before_weather_file_coverage']}."
    )
    out.append(
        f"Non-zero adjustment values seen: {wx['weather_total_adjustment_nonzero_values']}; events: {wx['nonzero_adjustment_events']}."
    )
    out.append(f"Raw weather key paths with a non-null value: {wx['raw_weather_key_paths'] or 'none'}.")
    out.append("")
    out.append(
        "The raw wind, temperature and precipitation readings are not persisted in the ledger payloads at all "
        "(the feature dict stores only `weather_total_adjustment`, `is_dome`, `weather_inputs_verified` and the "
        "evidence block), so the raw-reading count above is the count of payloads that carry one under any key "
        "matching wind/temp/temperature/precip. The effect is visible only through the adjustment itself."
    )
    out.append("")
    x = led["extras"]
    out.append(f"Ledger read is pinned: {led['as_of_filter']}.")
    out.append("")
    out.append("#### Field presence by model (absent is not zero)")
    out.append("")
    out.append(
        "| model_id | rows | features block | has weather_total_adjustment | non-zero | has weather_evidence | "
        "has qb_availability | has away/home qb_value_adjustment | qb adj non-zero |"
    )
    out.append("|---|---|---|---|---|---|---|---|---|")
    for m, c in sorted(x["presence_by_model"].items()):
        out.append(
            f"| {m} | {c['rows']} | {c['with_features_block']} | {c['has_weather_total_adjustment']} | "
            f"{c['weather_total_adjustment_nonzero']} | {c['has_weather_evidence_key']} | "
            f"{c['has_qb_availability_key']} | "
            f"{c['has_away_qb_value_adjustment']}/{c['has_home_qb_value_adjustment']} | "
            f"{c['qb_value_adjustment_nonzero']} |"
        )
    out.append("")
    failed = x["payload_strings_that_failed_to_decode"]
    out.append(
        f"Payload strings that looked like containers but failed to decode: {failed['rows']} rows "
        f"({failed['examples'] or 'none'}). A non-zero value here would mean an absent key was a decode failure."
    )
    out.append("")
    out.append("#### Positive control: every payload key containing weather, wind, temp or precip")
    out.append("")
    out.append("| Key path | Rows with key | Occurrences | Non-empty |")
    out.append("|---|---|---|---|")
    for k, c in x["weather_key_census"].items():
        out.append(f"| `{k}` | {c['rows_with_key']} | {c['occurrences']} | {c['non_empty']} |")
    out.append("")
    out.append(
        "A key here that holds a raw reading (not a flag, evidence or adjustment) with a non-empty count above 0 "
        "would have been counted in the raw-reading row above."
    )
    out.append("")
    out.append("#### The non-zero weather adjustments, cross-tabbed")
    out.append("")
    out.append("| model_id | evidence source | created date | rows |")
    out.append("|---|---|---|---|")
    for c in x["weather_total_adjustment_nonzero_cross_tab"]:
        out.append(f"| {c['model_id']} | {c['evidence_source']} | {c['created_date']} | {c['rows']} |")
    out.append("")
    nz_rows = x["weather_total_adjustment_nonzero_rows"]
    unbacked = [d for d in nz_rows if not d["evidence_source"].startswith("open_meteo")]
    after = [d for d in nz_rows if not d["created_before_event_start"]]
    out.append(
        f"Non-zero rows without Open-Meteo forecast evidence: {len(unbacked)} of {len(nz_rows)}; non-zero rows "
        f"created at or after the event start: {len(after)}. A non-zero row with no forecast evidence would be a "
        "point-in-time finding (the resolver's no-decision-time branch returns the first matching record with no "
        "source, hash or capture-time check)."
    )
    out.append("")
    found = x["recorded_capture_sha256_found_in_file"]
    totals = x["recorded_capture_sha256_total_in_file"]
    out.append(
        f"Which weather file the live process reads: {x['recorded_capture_sha256_distinct']} distinct capture "
        f"hashes are recorded in ledger evidence; found in the repo file {found.get('repo', 0)} of "
        f"{totals.get('repo', 0)} hashes, in the runtime file {found.get('runtime', 0)} of {totals.get('runtime', 0)}."
    )
    out.append("")
    out.append("### Training-history weather is placeholder only")
    out.append("")
    for name, h in (
        ("data/historical/ncaaf_games_all.jsonl", hist_all),
        ("data/processed/ncaaf/games.jsonl", hist_proc),
    ):
        wf = h["weather_fields"]
        out.append(
            f"- `{name}` ({h['rows']} rows, sha256 `{h['sha256'][:16]}...`): temperature_f distinct values "
            f"{wf['temperature_f']['distinct_values']} {wf['temperature_f']['top_values']}, wind_mph "
            f"{wf['wind_mph']['distinct_values']} {wf['wind_mph']['top_values']}, precipitation_in present on "
            f"{wf['precipitation_in']['rows_with_value']} rows, is_dome {wf['is_dome']['top_values']}. "
            f"Legacy dome rows carry {h['weather_pairs_legacy_dome_rows']}; legacy outdoor rows carry "
            f"{h['weather_pairs_legacy_outdoor_rows']}. The newest {h['newest_rows_with_parser_version']} "
            "rows have no weather fields."
        )
    out.append("")
    nf = report["weather_never_fitted"]
    out.append(
        "The extractor never reads weather off the history records "
        f"({len(nf['extractor_reads_of_history_record_weather'])} reads found), and the coefficients are literals "
        f"({ref(nf['literal_coefficients'][0])}, {ref(nf['literal_coefficients'][1])}, {ref(nf['literal_coefficients'][2])}); "
        f"`scripts/cfb_v5_research.py` has {len(nf['cfb_v5_research_weather_references'])} weather reference(s) "
        f"({[r['text'][:90] for r in nf['cfb_v5_research_weather_references']]}); the v5 artifact lists "
        f"`weather_total_adjustment` only as a feature name ({[r['line'] for r in nf['v5_artifact_weather_references']]}). "
        "No fit consumes weather, so the weather term was never fitted."
    )
    out.append("")
    out.append("## (b) QB availability")
    out.append("")
    out.append("### Code")
    out.append("")
    out.append(
        f"- Status to starter probability map: {ref(qt['status_map'])} (Out/IR 0.0, Doubtful, Questionable, Probable 1.0)."
    )
    out.append(
        f'- A starter **not on the injury report** gets `starter_prob=1.0`, `status="not_listed"`, '
        f'`reason="starter_absent_from_report"` ({ref(qt["not_listed_status"])}, {ref(qt["not_listed_prob"])}, {ref(qt["not_listed_reason"])}). '
        f"No snapshot, a stale one, or an unresolved starter returns `no_report` with 1.0 ({ref(qt['no_evidence_prob'])}); "
        f"an unmapped status such as Active also scores 1.0 ({ref(qt['unmapped_status_prob'])})."
    )
    out.append(
        f"- The discount `(1 - prob) * -4.5` only applies when `prob < 0.99` ({ref(qt['extractor_discount_gate'])})."
    )
    out.append("")
    out.append("Probe of the real resolver on a synthetic snapshot:")
    out.append("")
    out.append("| Case | starter_prob | status | reason |")
    out.append("|---|---|---|---|")
    for label, r in qp["resolver"].items():
        out.append(f"| {label} | {r['starter_prob']} | {r['status']} | {r['reason']} |")
    out.append("")
    out.append("### Ledger counts")
    out.append("")
    out.append("| Measure | Row level (2 sides per row) | Latest row per event |")
    out.append("|---|---|---|")
    out.append(f"| Side cells | {qb['side_cells_total']} | {qbl['side_cells_total']} |")
    out.append(
        f"| With a `qb_availability` cell | {qb['side_cells_with_qb_availability']} | {qbl['side_cells_with_qb_availability']} |"
    )
    out.append(
        f"| Without one (older rows) | {qb['side_cells_without_qb_availability']} | {qbl['side_cells_without_qb_availability']} |"
    )
    out.append(
        f"| Starter prob below 0.99 (discount fires) | {qb['side_cells_starter_prob_below_0_99']} | {qbl['side_cells_starter_prob_below_0_99']} |"
    )
    out.append(
        f"| Non-zero `*_qb_value_adjustment` | {qb['side_cells_nonzero_qb_value_adjustment']} | {qbl['side_cells_nonzero_qb_value_adjustment']} |"
    )
    out.append(
        f"| Starter unresolved (no name, no id) | {qb['side_cells_starter_unresolved']} | {qbl['side_cells_starter_unresolved']} |"
    )
    out.append("")
    out.append(
        f"Row-level status: {qb['status']}; reason: {qb['reason']}; starter_prob: {qb['starter_prob']}."
    )
    out.append(
        f"Latest-row-per-event status: {qbl['status']}; reason: {qbl['reason']}; starter_prob: {qbl['starter_prob']}."
    )
    out.append(
        f"Events with any row carrying a starter probability below 0.99: {ev['qb_prob_below_0_99']} of {ev['qb_cell']} events with a QB cell."
    )
    out.append("")
    out.append("### Injury snapshots on disk")
    out.append("")
    out.append(
        f"`{snap['dir']}`: {snap['snapshots']} snapshots, observed {snap['observed_at_min']} to {snap['observed_at_max']}. "
        f"QB entries across all snapshots: {snap['qb_entries_total_across_snapshots']} covering "
        f"{snap['distinct_teams_with_any_qb_entry']} distinct teams, statuses {snap['qb_entry_status']}; "
        f"{snap['snapshots_with_zero_qb_entries']} snapshots carry no QB entry. The whole feed holds "
        f"{snap['entries_total_all_positions']} entries over {snap['snapshots']} snapshots "
        f"({snap['entries_per_snapshot_min']} to {snap['entries_per_snapshot_max']} per snapshot, "
        f"{snap['distinct_team_player_position_status_entries']} distinct team/player/position/status entries, "
        f"{snap['distinct_teams_any_position']} teams), so it cannot describe QB availability for the slate. "
        f"Distinct (team, player, status) QB rows with a penalising status: {len(snap['qb_penalising_distinct_team_player_status'])}: "
        f"{snap['qb_penalising_distinct_team_player_status']}. All-position entry statuses: {snap['all_position_entry_status']}."
    )
    out.append("")
    out.append("### What `passing_leader_from_competitor` returns pregame")
    out.append("")
    stab = led["starter_name_stability_latest_row_per_event"]
    out.append(
        f"- It scans the scoreboard competitor's `leaders` for the `passingLeader` or `passingYards` group and returns "
        f'the first QB entry ({ref(qt["passing_leader_groups"])}); its own docstring says "The season passing leader is '
        f'the de facto starter; ESPN offers no college depth chart" ({ref(qt["passing_leader_docstring"])}).'
    )
    out.append(
        "- That is a cumulative-statistics leader, not a point-in-time starter: it does not know who starts "
        "Saturday and moves only when a backup out-throws the incumbent. Whether ESPN's pregame scoreboard "
        "`leaders` are exactly season-to-date totals was **not verified** (no network call; no stored raw scoreboard "
        "payload was inspected)."
    )
    out.append(
        f"- Observed in the ledger: {stab['teams_with_named_starter']} teams have a named starter in the latest row "
        f"per event; {stab['teams_with_two_or_more_distinct_starter_names']} of them show two or more distinct "
        f"starter names (examples: {stab['examples']}). Games per team with a QB cell: "
        f"{stab['games_per_team_distribution']}. A team needs at least two such games to show a change, so starter "
        f"stability over a season is **not testable** from the ledger (QB cells exist only on rows created "
        f"{led['qb_cell_row_created_at_range'][0]} to {led['qb_cell_row_created_at_range'][1]})."
    )
    out.append(
        f"- The history files carry `home_qb_experience_starts` / `away_qb_experience_starts` "
        f"({hist_all['key_counts'].get('home_qb_experience_starts', 0)} rows) but the extractor reads them "
        f"{len(qt['history_qb_experience_reads'])} time(s) outside the record definition, so no historical QB signal feeds any fit."
    )
    out.append("")
    out.append("## Item 3 data finding: possessions, success rate, EPA, drive and play data")
    out.append("")
    out.append("| Field | Present rows | Distinct values | Top values | Absent rows |")
    out.append("|---|---|---|---|---|")
    for fld, p in {**hist_all["possession_fields"], **hist_all["epa_fields"]}.items():
        out.append(
            f"| `{fld}` | {p['rows_with_value']} | {p['distinct_values']} | {p['top_values']} | {p['rows_absent']} |"
        )
    out.append("")
    epa = hist_all["epa_diagnostics"]
    nsw = hist_all["neutral_site_and_week"]
    out.append(
        f"- `ncaaf_games_all.jsonl`: {hist_all['rows']} rows; {hist_all['legacy_rows_without_parser_version']} legacy rows "
        f"and {hist_all['newest_rows_with_parser_version']} newest rows (parser {hist_all['parser_versions']}). "
        f"`data/processed/ncaaf/games.jsonl` has {hist_proc['rows']} rows; the two files are "
        f"{'identical' if report['history']['files_identical_rows'] else 'NOT identical'} row for row."
    )
    out.append(
        "- `possessions` is a single constant on every row that has it; `home_success_rate` and `away_success_rate` "
        "are a single constant on every row that has them. They carry no information."
    )
    out.append(
        f"- EPA varies ({hist_all['epa_fields']['home_epa_per_play']['distinct_values']} / "
        f"{hist_all['epa_fields']['away_epa_per_play']['distinct_values']} distinct home/away values, stored to 3 decimals: "
        f"{epa.get('values_rounded_to_3dp')}), but neither file records where it came from "
        f"(no source or parser field on the {hist_all['legacy_rows_without_parser_version']} legacy rows). "
        f"Both sides are exactly 0.0 on {epa.get('both_epa_exactly_zero')} rows (home 0.0 on {epa.get('home_epa_exactly_zero')}, "
        f"away on {epa.get('away_epa_exactly_zero')}). Diagnostic only: correlation of EPA difference with final margin "
        f"{epa.get('corr_epa_diff_vs_margin'):.3f}, of home EPA with the game total {epa.get('corr_home_epa_vs_total'):.3f}. "
        "Provenance is **unverified**; v6 does not use it."
    )
    out.append(
        f"- The newest {hist_all['newest_rows_with_parser_version']} rows lack `possessions`, EPA, success rate, weather, "
        f"`is_neutral_site` and `week` (is_neutral_site absent on {nsw['is_neutral_site_absent']}, week absent on "
        f"{nsw['week_absent']}; absent rows are exactly the parser-version rows: {nsw['absent_rows_are_exactly_the_parser_version_rows']})."
    )
    out.append(
        f"- Keys whose name suggests drive, play, yardage, possession, EPA, success, snap or down data: "
        f"{hist_all['drive_or_play_like_keys']}. Only possessions, the two success rates and the two EPA fields qualify; "
        "there is no drive, play-count, yardage, or turnover field."
    )
    out.append("")
    out.append(f"**Conclusion:** {verd['pace_conclusion']}")
    out.append("")
    out.append("## Reproducibility")
    out.append("")
    out.append(f"- Script: `scripts/cfb_v6_input_audit.py`; AS_OF_UTC {report['as_of_utc']}.")
    out.append(f"- Ledger DB: `{led['ledger_db']}` ({led['opened_mode']}).")
    for name, h in report["history"].items():
        if isinstance(h, dict) and "sha256" in h:
            out.append(f"- `{name}`: {h['rows']} rows, sha256 `{h['sha256']}`.")
    for label, info in wfc.items():
        if info.get("exists"):
            out.append(
                f"- weather ({label}) `{info['path']}`: {info['rows']} rows, sha256 `{info['sha256']}`."
            )
    out.append("")
    return "\n".join(out) + "\n"


def main() -> int:
    from datetime import UTC, datetime

    report: dict[str, Any] = {
        "as_of_utc": AS_OF_UTC,
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "env": {"MODEL_PREDICTION_LEDGER": os.environ.get("MODEL_PREDICTION_LEDGER")},
        "weather_trace": weather_code_trace(),
        "weather_probes": weather_probes(),
        "weather_file_coverage": weather_file_coverage(),
        "weather_never_fitted": weather_never_fitted_evidence(),
        "history": history_audit(),
        "ledger": ledger_audit(),
        "qb_trace": qb_code_trace(),
        "qb_probes": qb_probes(),
        "injury_snapshots": injury_snapshot_audit(),
    }
    report["verdicts"] = build_verdicts(report)
    # Any code claim the search could not locate is reported, never silently dropped.
    unverified = []

    def scan(obj: Any, path: str) -> None:
        if isinstance(obj, dict):
            if obj.get("line", 0) is None and "text" in obj:
                unverified.append(path)
            for k, v in obj.items():
                scan(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                scan(v, f"{path}[{i}]")

    scan(report["weather_trace"], "weather_trace")
    scan(report["qb_trace"], "qb_trace")
    scan(report["weather_never_fitted"], "weather_never_fitted")
    report["unverified_code_claims"] = unverified

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    OUT_MD.write_text(render_md(report), encoding="utf-8")
    print(json.dumps(report["verdicts"], indent=2))
    print("unverified code claims:", unverified or "none")
    print(f"wrote {OUT_JSON.relative_to(REPO)} and {OUT_MD.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
