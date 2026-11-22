"""Shared helpers for the A/B grid candidates of protocol 261120 (A_ml_w0, B1, B2, B3).

Research only. Everything starts from the served v7 baseline as the grader builds it
(``grader.baseline_predictions(grader.load_archive())["v7_served"]``), so the switched-off pipeline is the
baseline by construction, and every outcome comes through ``grader.load_archive`` (which applies the lock
first). The closing line is dropped from the working frame: no candidate here ever sees a line.

Fold discipline: ``loso_apply`` hands the fit function only the training seasons' rows and the predict
function the held-out rows WITHOUT the margin column, so a held-out outcome cannot reach a prediction.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parent
if str(RESEARCH) not in sys.path:
    sys.path.insert(0, str(RESEARCH))

import grader

REPO = grader.REPO
DF_T = grader.DF_T
DEV_SEASONS = grader.DEV_SEASONS
OUT_ROOT = grader.OUT_DIR
V7_VARIANT = "rg_c010_l02_k14"  # the served ridge variant (models/ncaab_v7_serving.py)
BUCKET_CUTS = (1, 3, 8)  # current-season sample buckets 0 | 1-2 | 3-7 | 8+
N_BUCKETS = 4
TAIL_KNOT = 12.0
HISTORY_CUTOFF = "strictly before Eastern midnight of the game day"
# served v7's own fit seasons: frozen constants fit on 2023+2024, LOSO rows for 2023/2024 (grader export meta)
V7_FIT_SEASONS = {2023: [2024], 2024: [2023], 2025: [2023, 2024], 2026: [2023, 2024]}
ID_COLS = ["event_id", "season", "game_date", "home_team", "away_team", "neutral"]


# ------------------------------------------------------------------------------------------ pure math
def bucket(sample: Iterable[float] | np.ndarray) -> np.ndarray:
    """v7 games-played bucket index (0 | 1-2 | 3-7 | 8+)."""
    return np.searchsorted(np.array(BUCKET_CUTS), np.asarray(sample), side="right")


def tail_term(v6_mean: np.ndarray) -> np.ndarray:
    v = np.asarray(v6_mean, float)
    return np.sign(v) * np.maximum(np.abs(v) - TAIL_KNOT, 0.0)


def t8_nll(resid: np.ndarray, scale: np.ndarray | float) -> float:
    """Negative log-likelihood of residuals under Student-t(8) with the given (per-row) scale."""
    from scipy import stats

    r = np.asarray(resid, float)
    s = np.broadcast_to(np.asarray(scale, float), r.shape)
    return -float(np.sum(stats.t.logpdf(r / s, DF_T) - np.log(s)))


def t8_scale_mle(resid: np.ndarray) -> float:
    """Location-0 t(8) scale MLE: the exact golden-section routine v7's fit used."""
    from model_prediction.models.ncaab_v7_shadow import student_t_scale_mle

    return float(student_t_scale_mle(np.asarray(resid, float), DF_T))


def bucket_scales(resid: np.ndarray, sample: np.ndarray) -> list[float]:
    b = bucket(sample)
    return [t8_scale_mle(np.asarray(resid, float)[b == k]) for k in range(N_BUCKETS)]


def ols(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """v7's least squares (1e-6 ridge, no intercept)."""
    return np.linalg.solve(x.T @ x + 1e-6 * np.eye(x.shape[1]), x.T @ y)


def p_margin_ge1(mu: np.ndarray, scale: np.ndarray) -> np.ndarray:
    return grader.p_home_from_margin(mu, scale, np.full(len(np.atleast_1d(mu)), "t8"))


def ml_blend(elo: np.ndarray, mu: np.ndarray, scale: np.ndarray, w: float) -> np.ndarray:
    """p_home = w * Elo + (1 - w) * P(margin >= 1), clipped as the served rule clips (w = 0.5 is served)."""
    return np.clip(w * np.asarray(elo, float) + (1.0 - w) * p_margin_ge1(mu, scale), 0.0001, 0.9999)


# ------------------------------------------------------------------------------------------ data
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _components_fields(ids: set[str], fields: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for rec in grader._iter_jsonl(grader.COMPONENTS):
        if grader.is_locked(rec["season_year"], rec["day"]):  # lock before anything else is read
            continue
        eid = str(rec["event_id"])
        if eid in ids:
            out[eid] = {f: rec.get(f) for f in fields}
    return out


def load_frame() -> pd.DataFrame:
    """Served v7 rows (D1-vs-D1) with PIT components and the outcome margin (for training rows only).

    Columns: identity, games_played_min (archive PIT sample), mu_v7, scale_v7, p_ml_v7, elo (reconstructed
    v6 Elo win probability), v6_mean, ridge, margin. No line column.
    """
    a = grader.load_archive(DEV_SEASONS)
    v7 = grader.baseline_predictions(a)["v7_served"]
    v7 = v7[v7["event_id"].isin(a.loc[a["d1"], "event_id"])]  # exactly what `grader.py export` writes
    a_ix = a.set_index("event_id")
    f = v7[ID_COLS + ["games_played_min"]].copy()
    f["mu_v7"] = v7["mu_margin"].to_numpy(float)
    f["scale_v7"] = v7["scale_margin"].to_numpy(float)
    f["p_ml_v7"] = v7["p_home_ml"].to_numpy(float)
    sub = a_ix.loc[f["event_id"]]
    f["v6_mean"] = sub["v6_mean"].to_numpy(float)
    f["elo"] = grader.reconstruct_elo_win(sub["v6_p_home"], sub["v6_mean"], sub["v6_scale"])
    comp = _components_fields(set(f["event_id"]), (V7_VARIANT,))
    f["ridge"] = [
        float(comp[e][V7_VARIANT]) if comp.get(e, {}).get(V7_VARIANT) is not None else np.nan
        for e in f["event_id"]
    ]
    f["margin"] = sub["margin"].to_numpy(float)
    assert "line" not in f.columns and "total_line" not in f.columns
    return f.reset_index(drop=True)


def fit_seasons_loso(seasons: Iterable[int] = DEV_SEASONS) -> dict[int, list[int]]:
    s = sorted(int(x) for x in seasons)
    return {h: [x for x in s if x != h] for h in s}


def loso_apply(
    frame: pd.DataFrame,
    fit_fn: Callable[[pd.DataFrame], dict[str, Any]],
    predict_fn: Callable[[pd.DataFrame, dict[str, Any]], pd.DataFrame],
    seasons: Iterable[int] = DEV_SEASONS,
) -> tuple[pd.DataFrame, dict[int, dict[str, Any]]]:
    """Leave-one-season-out: fit on the other seasons, predict the held-out season with its margin removed."""
    parts, params = [], {}
    for held, fits in fit_seasons_loso(seasons).items():
        train = frame[frame["season"].isin(fits)].copy()
        if held in set(train["season"]):
            raise AssertionError("held-out season leaked into the training rows")
        test = frame[frame["season"] == held].drop(columns=["margin"]).copy()
        p = fit_fn(train)
        params[held] = p
        parts.append(predict_fn(test, p))
    return pd.concat(parts, ignore_index=True), params


# ------------------------------------------------------------------------------------------ submission
def submission_rows(pred: pd.DataFrame, with_ml: bool) -> list[dict[str, Any]]:
    rows = []
    for r in pred.itertuples(index=False):
        d: dict[str, Any] = {
            "event_id": str(r.event_id),
            "season": int(r.season),
            "game_date": str(r.game_date),
            "home_team": str(r.home_team),
            "away_team": str(r.away_team),
            "neutral": bool(r.neutral),
            "mu_margin": float(r.mu_margin),
            "scale_margin": float(r.scale_margin),
            "dist": "t8",
            "games_played_min": int(r.games_played_min),
        }
        if with_ml:
            d["p_home_ml"] = float(r.p_home_ml)
        for v in (d["mu_margin"], d["scale_margin"], d.get("p_home_ml", 0.5)):
            if not math.isfinite(v):
                raise ValueError(f"non-finite prediction for {d['event_id']}")
        rows.append(d)
    return rows


def already_read(grid_name: str) -> bool:
    if not grader.READ_LOG.exists():
        return False
    for line in grader.READ_LOG.read_text(encoding="utf-8").splitlines():
        if line.strip() and json.loads(line).get("grid_name") == grid_name:
            return True
    return False


def write_submission(
    pred: pd.DataFrame,
    *,
    grid_name: str,
    candidate: str,
    code_files: list[Path],
    fit_seasons: dict[int, list[int]],
    with_ml: bool,
    extra_meta: dict[str, Any],
) -> Path:
    """Write <OUT_ROOT>/<grid>/<grid>.jsonl and .meta.json. Refuses once the grid name has a logged read,
    unless the bytes would be identical (a regenerated file with float drift would be a re-read)."""
    out_dir = OUT_ROOT / grid_name
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{grid_name}.jsonl"
    body = "".join(json.dumps(r, sort_keys=True) + "\n" for r in submission_rows(pred, with_ml))
    if already_read(grid_name) and (not path.exists() or path.read_text(encoding="utf-8") != body):
        raise RuntimeError(f"{grid_name} already has a logged read; refusing to write a different file")
    path.write_text(body, encoding="utf-8")
    code_sha = {str(p.relative_to(REPO)).replace("\\", "/"): sha256_file(p) for p in code_files}
    combined = hashlib.sha256("".join(code_sha[k] for k in sorted(code_sha)).encode()).hexdigest()
    sources = (
        grader.COMPONENTS,
        grader.LOSO_V7,
        grader.nr.SCOREBOARD,
        REPO / "outputs/ncaab/ncaab_v7_multiseason_frozen.json",
    )
    meta = {
        "candidate": candidate,
        "grid_name": grid_name,
        "protocol_sha256": grader.PROTOCOL_SHA256,
        "addendum_sha256": grader.ADDENDUM_SHA256,
        "code_sha256": combined,
        "code_files_sha256": code_sha,
        "fit_seasons_by_predicted_season": {str(k): v for k, v in fit_seasons.items()},
        "history_cutoff": HISTORY_CUTOFF,
        "data_sources": {str(p.relative_to(REPO)).replace("\\", "/"): sha256_file(p) for p in sources},
        "predictions_file_sha256": hashlib.sha256(body.encode()).hexdigest(),
        **extra_meta,
    }
    path.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8")
    return path


def compare_to_export(pred_path: Path, export_path: Path, check_scale: bool = True) -> dict[str, Any]:
    """Max abs mu (and scale) difference against ``grader.py export --model v7_served`` on the same ids."""
    p = pd.read_json(pred_path, lines=True, dtype={"event_id": str})
    e = pd.read_json(export_path, lines=True, dtype={"event_id": str})
    p["event_id"], e["event_id"] = p["event_id"].astype(str), e["event_id"].astype(str)
    j = p.merge(e, on="event_id", suffixes=("", "_v7"))
    out = {
        "n_pred": len(p),
        "n_export": len(e),
        "n_shared": len(j),
        "ids_equal": set(p["event_id"]) == set(e["event_id"]),
        "max_abs_mu_diff": float(np.max(np.abs(j["mu_margin"] - j["mu_margin_v7"]))),
    }
    if check_scale:
        out["max_abs_scale_diff"] = float(np.max(np.abs(j["scale_margin"] - j["scale_margin_v7"])))
    return out
