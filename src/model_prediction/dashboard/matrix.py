"""Dashboard matrix module (extracted from dashboard_server.py, DD-5).

Part of the dashboard_server.py -> dashboard/ package split. See MASTER.md
DD-5 and dashboard/__init__.py for the re-export shim that keeps the old
`dashboard_server` import path and test-suite symbol references working.
"""

from __future__ import annotations

from pathlib import Path

import yaml

try:
    from openpyxl import load_workbook
except ImportError:  # pragma: no cover
    load_workbook = None


from model_prediction.dashboard.common import (
    CONFIG_FILE,
    OUTPUTS,
    ROOT,
    _read_json,
)

# ── SECTION: Validation & Matrix ────────────────────────────────────


def _newest_validation() -> tuple[dict, str]:
    """Newest core validation merged with the newest artifact-backed soccer report."""
    candidates = sorted(
        OUTPUTS.glob("learned-model-validation*.json"),
        key=lambda path: path.stat().st_mtime,
    )
    merged: dict = {"sports": {}, "production_artifacts": {}}
    sources = []
    newest: dict = {}
    if candidates:
        newest = _read_json(candidates[-1]) or {}
        merged["sports"].update(newest.get("sports") or {})
        merged["production_artifacts"].update(newest.get("production_artifacts") or {})
        sources.append(candidates[-1].name)

    soccer_candidates: list[tuple[float, Path, dict]] = []
    for path in OUTPUTS.glob("soccer-*.json"):
        payload = _read_json(path) or {}
        if (payload.get("sports") or {}).get("soccer") and (payload.get("production_artifacts") or {}).get(
            "soccer"
        ):
            soccer_candidates.append((path.stat().st_mtime, path, payload))
    if soccer_candidates:
        _, path, soccer = max(soccer_candidates, key=lambda item: item[0])
        merged["sports"].update(soccer["sports"])
        merged["production_artifacts"].update(soccer.get("production_artifacts") or {})
        sources.append(path.name)

    # Preserve embedded grids, then fill them from their dedicated validation
    # reports. Core learned-model validation intentionally does not own these
    # research-only leagues and may omit both keys entirely.
    esports_grid = dict(newest.get("esports_grid") or {})
    esports_validation = _read_json(OUTPUTS / "esports-baseline-validation.json") or {}
    for sport, result in (esports_validation.get("titles") or {}).items():
        locked = (result.get("locked_test") or {}).get("selected_matches") or {}
        if not locked:
            continue
        esports_grid[str(sport)] = {
            "moneyline": {
                "state": "research_only",
                "hit_rate": locked.get("accuracy"),
                "calls": locked.get("calls"),
                "brier": locked.get("brier"),
                "units": 0.0,
                "diagnostic_units": locked.get("units_at_minus_110"),
                "threshold": (result.get("chosen") or {}).get("confidence_threshold"),
                "model_version": result.get("model_version"),
                "qualified_for_betting": False,
            }
        }
    if esports_validation.get("titles"):
        sources.append("esports-baseline-validation.json")

    baseball_grid = dict(newest.get("baseball_grid") or {})
    baseball_validation = _read_json(OUTPUTS / "international-baseball-baseline-validation.json") or {}
    for sport, result in (baseball_validation.get("leagues") or {}).items():
        locked = result.get("locked_test") or {}
        if not locked:
            continue
        baseball_grid[str(sport)] = {
            "moneyline": {
                "state": "research_only",
                "hit_rate": locked.get("accuracy_decisive"),
                "calls": locked.get("calls"),
                "brier": locked.get("brier_settlement"),
                "units": 0.0,
                "diagnostic_units": locked.get("units_at_minus_110"),
                "observations": locked.get("observations"),
                "ties": locked.get("ties"),
                "model_version": result.get("model_version"),
                "qualified_for_betting": False,
            }
        }
    if baseball_validation.get("leagues"):
        sources.append("international-baseball-baseline-validation.json")

    merged["esports_grid"] = esports_grid
    merged["baseball_grid"] = baseball_grid

    return merged, " + ".join(sources)


def _production_artifact(validation: dict, sport: str) -> dict:
    raw_path = str((validation.get("production_artifacts") or {}).get(sport) or "")
    if not raw_path:
        # The validation report can contain sport metrics without repeating
        # the active artifact path. model.yaml remains authoritative for which
        # version the dashboard labels as production/shadow.
        raw_path = _config_production_artifact_path(sport)
    if not raw_path:
        return {}
    path = Path(raw_path)
    if not path.is_absolute():
        path = ROOT / path
    return _read_json(path) or {}


def _config_production_artifact_path(sport: str) -> str:
    """Read model.yaml and return the production_artifact path for a sport."""
    try:
        import yaml

        if CONFIG_FILE.exists():
            config = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
            models = config.get("models") or {}
            sport_config = models.get(sport.upper()) or {}
            return str(sport_config.get("production_artifact") or "")
    except Exception:  # noqa: BLE001, S110 - missing/malformed config just falls back to ""
        pass
    return ""


def _ml_cell(sport_meta: dict, artifact: dict | None = None) -> dict:
    """Moneyline cell pinned to the active artifact's exact validated variant."""
    variants = sport_meta.get("variants") or {}
    artifact = artifact or {}

    # Esports: flat Elo artifact without market_models wrapper
    if "k" in artifact and "ratings" in artifact:
        qual = artifact.get("qualified_for_betting", False)
        return {
            "state": "qualified" if qual else "research_only",
            "hit_rate": None,
            "calls": len(artifact.get("ratings", {})),
            "variant": ["elo_neutral"],
            "variant_name": "elo_neutral_series",
            "model_version": artifact.get("model_version"),
        }

    market_model = (artifact.get("market_models") or {}).get("moneyline") or {}
    artifact_features = tuple(market_model.get("feature_names") or ())
    artifact_qualification = artifact.get("qualification") or {}
    variant_name = None
    variant = None
    for name, candidate in variants.items():
        if not isinstance(candidate, dict) or tuple(candidate.get("features") or ()) != artifact_features:
            continue
        holdout = (candidate.get("primary_65") or {}).get("locked_holdout") or {}
        calls_match = artifact_qualification.get("calls") in (None, holdout.get("calls"))
        artifact_rate = artifact_qualification.get("hit_rate")
        holdout_rate = holdout.get("hit_rate")
        rate_match = artifact_rate is None or (
            holdout_rate is not None and abs(float(artifact_rate) - float(holdout_rate)) < 1e-9
        )
        if calls_match and rate_match:
            variant_name, variant = name, candidate
            break

    if variant is None and not artifact:
        for name in ("elo_trend", "elo_trend_defense", "elo_trend_park", "elo_only"):
            candidate = variants.get(name) or {}
            if ((candidate.get("primary_65") or {}).get("locked_holdout") or {}).get("qualified"):
                variant_name, variant = name, candidate
                break
        if variant is None:
            match = next(
                (
                    (name, candidate)
                    for name, candidate in variants.items()
                    if isinstance(candidate, dict) and candidate.get("primary_65")
                ),
                (None, None),
            )
            variant_name, variant = match

    primary = (variant or {}).get("primary_65") or {}
    holdout = primary.get("locked_holdout") or {}
    if not holdout and artifact_qualification:
        holdout = artifact_qualification
        primary = {"learned_threshold": market_model.get("confidence_threshold")}
        variant_name = "artifact_pinned"
    if not holdout:
        # A boolean without calls/hit-rate evidence is not enough to render a
        # qualified cell. Show as tested (model exists, qualified flag set)
        # rather than untested, but make the missing metrics explicit.
        if artifact.get("qualified"):
            return {
                "state": "tested_not_qualified",
                "hit_rate": None,
                "calls": None,
                "threshold": market_model.get("confidence_threshold"),
                "variant": list(artifact_features),
                "variant_name": "artifact_pinned",
                "model_version": artifact.get("model_version"),
                "readiness": "ARTIFACT_QUALIFIED_FLAG_WITHOUT_LOCKED_HOLDOUT_METRICS",
            }
        return {"state": "no_data"}

    cell = {
        "state": "qualified" if holdout.get("qualified") else "tested_not_qualified",
        "hit_rate": holdout.get("hit_rate"),
        "calls": holdout.get("calls"),
        "units": holdout.get("units_at_minus_110"),
        "brier": holdout.get("brier_score"),
        "threshold": primary.get("learned_threshold"),
        "roi": holdout.get("roi"),
        "variant": list(artifact_features or tuple((variant or {}).get("features") or ())),
        "variant_name": variant_name,
        "model_version": artifact.get("model_version"),
    }

    # Soccer: also show 3-way variant if available
    three = variants.get("soccer_3way")
    if three and isinstance(three, dict):
        tp = three.get("primary_65", {})
        th = tp.get("locked_holdout", {})
        if th.get("qualified"):
            cell["three_way"] = {
                "hit_rate": th.get("hit_rate"),
                "calls": th.get("calls"),
                "units": th.get("units_at_minus_110"),
            }

    return cell


def matrix() -> dict:
    """Wiring status per sport/market: is it live in `daily`, what does it
    actually run on, and which ledger does it write to.

    Deliberately NOT hit rates, Brier scores, MAE, or promotion-gate status
    -- those questions live on the System/Evidence tabs. This mirrors the
    operator's standing instruction to discuss models in terms of wiring and
    features, not validation metrics (see docs/PROJECT_STATUS.md's operating
    note). Rows are maintained by hand alongside cli.py's actual dispatch
    logic -- when a sport's wiring changes, update this list in the same
    change. Active version labels are read from config/model.yaml so this
    operational surface cannot silently lag a model promotion.
    """
    try:
        config = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        config = {}
    configured_models = config.get("models") or {}

    def active_version(sport: str) -> str:
        sport_config = configured_models.get(sport.upper()) or {}
        return str(
            sport_config.get("active_production_version")
            or sport_config.get("active_research_version")
            or "version unavailable"
        )

    esports_versions = ", ".join(
        f"{sport.upper()} {active_version(sport)}"
        for sport in ("lol", "cs2", "dota2", "valorant", "rainbow_six")
    )
    rows = [
        {
            "sport": "MLB",
            "market": "Moneyline",
            "wired": True,
            "model": (
                "learned_forward.py -- Elo + trend logistic regression "
                f"({active_version('mlb')}); features: elo_probability, trend_gap, "
                "park_factor, weather_factor, pitcher_era_gap, bullpen_weakness_gap "
                "-- no probable_starter_era_gap (v6's contaminated ESPN-live-probables "
                "feature, retired 2026-07-30). No confidence-threshold or "
                "min-edge-vs-ask gate as of 2026-07-30 -- every real forecasted game "
                "becomes a real, sized Main-ledger call; both numbers are still "
                "recorded for manual review, not used to hide the row."
            ),
            "ledger": "Main + Flat",
        },
        {
            "sport": "MLB",
            "market": "Totals & Spread",
            "wired": True,
            "model": "models/mlb.py MeasuredEdgeTotalsModel/margin -- Gamma-Poisson mixture "
            "Monte-Carlo (20000 sims), priced against real Polymarket lines closest to "
            "50/50. Rebuilt 2026-07-30: real elasticities fit per factor (offense 0.035, "
            "starter weakness 0.211, park 0.222, weather 0.021) replace the prior "
            "assumed-1.0 multiplicative weight on each; bullpen elasticity fit "
            "consistently negative/implausible and is zeroed rather than trusted.",
            "ledger": "Flat only",
        },
        {
            "sport": "MLB",
            "market": "Moneyline (legacy)",
            "wired": False,
            "model": "MeasuredEdgeMarginModel via --model legacy-measured-edge -- intentionally "
            "retained as an explicit manual rollback, not part of daily",
            "ledger": "Main, only if manually invoked",
        },
        {
            "sport": "NBA",
            "market": "Moneyline",
            "wired": True,
            "model": (
                "learned_forward.py -- Elo + trend logistic regression "
                f"({active_version('nba')}); features: elo_probability, trend_gap, "
                "defensive_trend_gap. Not in PRODUCTION_SPORTS (domain.py) -- never "
                "reaches Main regardless of real-world strength."
            ),
            "ledger": "Flat only",
        },
        {
            "sport": "WNBA",
            "market": "Moneyline",
            "wired": True,
            "model": (
                "learned_forward.py -- Elo + trend logistic regression "
                f"({active_version('wnba')}); features: elo_probability, trend_gap, "
                "defensive_trend_gap"
            ),
            "ledger": "Main + Flat",
        },
        {
            "sport": "NFL",
            "market": "Moneyline",
            "wired": True,
            "model": (
                "learned_forward.py -- Elo + trend logistic regression "
                f"({active_version('nfl')}); features: elo_probability, trend_gap. "
                "Not in PRODUCTION_SPORTS (domain.py) -- never reaches Main regardless "
                "of real-world strength."
            ),
            "ledger": "Flat only",
        },
        {
            "sport": "Soccer",
            "market": "Totals (2.5)",
            "wired": True,
            "model": "models/soccer.py -- Poisson/Dixon-Coles score matrix",
            "ledger": "Flat Research + Gated Research",
        },
        {
            "sport": "Soccer",
            "market": "Moneyline",
            "wired": True,
            "model": "Same score matrix, matched against Polymarket's per-team team_win "
            "Yes/No markets (not a single combined moneyline market)",
            "ledger": "Flat Research + Gated Research",
        },
        {
            "sport": "Soccer",
            "market": "BTTS",
            "wired": True,
            "model": "Same score matrix, Platt-recalibrated (2026-07-31: raw joint-matrix "
            "probability was overconfident, 55.0% real accuracy; calibrated to 56.7%). "
            "Matching/pricing fully wired (soccer_forward.py), but no BTTS market has "
            "ever been observed live on Polymarket US (checked across all 19 configured "
            "leagues and 4 real captured days) -- activates automatically, no further "
            "code changes, once one appears and its raw market type is confirmed.",
            "ledger": "Flat Research + Gated Research (currently prices 0 -- no real market exists yet)",
        },
        {
            "sport": "Tennis",
            "market": "Moneyline",
            "wired": True,
            "model": "models/tennis.py -- surface-blended Elo, singles only, WTA only "
            f"({active_version('tennis')}; Polymarket US has no ATP market; "
            "ESPN has no ITF scoreboard)",
            "ledger": "Flat Research + Gated Research",
        },
        {
            "sport": "LOL / CS2 / DOTA2 / VALORANT / Rainbow Six",
            "market": "Moneyline",
            "wired": True,
            "model": "esports.py -- result-based neutral Elo, Platt-scaled, refreshed from "
            f"bo3.gg before every forecast; active config: {esports_versions}. Gated "
            "Research's research_confidence_gate raised 2026-07-31 (was 0.0 for every "
            "title, barely filtering anything -- real settled Gated picks were "
            "performing worse than unfiltered Research) to each title's own already-"
            "validated confidence_threshold: LOL/DOTA2/VALORANT 0.05, CS2/Rainbow Six 0.03.",
            "ledger": "Flat Research + Gated Research",
        },
        {
            "sport": "CoD / Rocket League / Overwatch",
            "market": "Moneyline",
            "wired": False,
            "model": "Polymarket lists these leagues and real BBO is captured daily, but "
            "bo3.gg (the only esports data source) has no discipline for any of them -- not buildable",
            "ledger": "--",
        },
        {
            "sport": "KBO / NPB",
            "market": "Moneyline",
            "wired": True,
            "model": "international_baseball.py -- tie-aware home-field Elo "
            f"(KBO {active_version('kbo')}; NPB {active_version('npb')}; "
            "result/margin only, no starters/park/weather)",
            "ledger": "Flat Research + Gated Research",
        },
    ]
    return {
        "rows": rows,
        "note": ("Wiring and features, not validation stats -- see docs/PROJECT_STATUS.md's operating note."),
    }
