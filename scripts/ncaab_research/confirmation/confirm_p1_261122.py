"""Addendum-v5 CONFIRMATION read for P1_player_availability (quick 261121-hmc, lane P). Exactly one scored read.

P1 was DEV_ELIGIBLE under addendum v4 (829f4d3a). Addendum v5 (``protocol_261120_addendum_v5.json``, canonical
sha256 pinned below) fixes the confirmation path, and its clauses are binding here:

- ``f_confirmation_seasons``: season years 2018, 2019, 2020 and 2022, the only ones with espn_backfill_261120
  spread lines;
- ``c_season_usability``: a season counts only if >= 85% of its D1 team-games pass the box gate (audit,
  outcome-free); fewer than 3 usable seasons means CONFIRMATION_UNAVAILABLE;
- ``d_coefficients``: ONE OLS fit, no intercept, of the P1 form on ALL development cohort rows 2023-2026 (v4
  table, v4 baseline v7 mean). It is written to ``confirmation_coefficients.json`` and committed before any
  confirmation line or outcome is loaded, then applied unchanged (no refit);
- ``e_date_quirk``: the table's scoreboard date may be 0 or 1 day before the Eastern day of the tip; anything
  else is refused.

v7_mu_confirmation is the served-constant v7 mean of the confirmation replay, read from the pinned A_ml_w0
confirmation predictions file (arm mu_v7_scale_v7 of ``confirmation_pin_a_ml_w0.json``), with v7's scale and
dist. Candidate: mu = v7_mu + b1 * dA + b2 * dL, adjustment 0 when any of the four inputs is null.

Primary (v4 pass rule): spread_corr_delta_vs_v7 = corr(y, cand - L) - corr(y, v7 - L), y = margin - L, L the
closing home margin, pooled over the usable seasons' D1-vs-D1 games with a backfill spread line. Day-cluster
bootstrap (grader._boot: Eastern game day, 4000 resamples, seed 17). CONFIRMED iff the plain one-sided 95%
lower bound (5th percentile) is above 0. RMSE, cover log loss, moneyline and the non-zero-adjustment subset are
reported with no verdict.

grader_v4 refuses this working tree (the feature builder changed for v5); it is imported for its pure helpers
only, never for its development scorer or input check. Nothing here is promoted or registered.

    OPENBLAS_NUM_THREADS=1 .venv/Scripts/python scripts/ncaab_research/confirmation/confirm_p1_261122.py coef
    ... confirm_p1_261122.py predict
    ... confirm_p1_261122.py shuffle --seed 5    # plumbing: outcomes permuted within season, never logged
    ... confirm_p1_261122.py read                # THE read: logged before any statistic exists

Points and probabilities only, never American odds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
for _p in (
    REPO / "src",
    REPO / "scripts",
    REPO / "scripts/ncaab_research",
    REPO / "scripts/ncaab_research/candidates",
    HERE,
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import grader
import grader_v4
import p_player_261121 as pp

GRID = "P1_player_availability"
WINDOW = "confirmation_v5"
CANDIDATE = (
    "P1: v7 + b1*d(avail_top7_prev1) + b2*d(top7_minutes_share_out_3plus), pooled 2023-2026 OLS (v5 d)"
)
ADDENDUM_V5_PATH = REPO / "scripts/ncaab_research/protocol_261120_addendum_v5.json"
ADDENDUM_V5_SHA256 = "c11e1910cec9fbc1cc1c5ea7b9ef9422d4eb8b8eafae48e96f65c255b5df7b57"
CONF_SEASONS = (2018, 2019, 2020, 2022)  # clause f
USABLE_MIN_RATE = 0.85  # clause c (v4 rule)
MIN_USABLE_SEASONS = 3
ALPHA_ONE_SIDED = 0.05
MAX_DATE_GAP_DAYS = 1  # clause e

# clause b: scripts are compared after CRLF -> LF normalisation, data files raw
LF_PINS = {
    "scripts/backfill_ncaab_player_boxes.py": "3cf28c2ce45365f22f04cf8cebcae65e2467920bca2b242da873a1671d85e9d5",
    "scripts/ncaab_research/player_features_261121.py": "29ab4b838360ff28259039d5b1e368c07f798a398536927f4b7719d68f65999a",
    "scripts/ncaab_research/export_player_events_v5.py": "391fc36017fc428f87d3638cc28dd99c8c46bdf54750ceb0f08404f7f5b7508b",
}
V4_TABLE = "data/research/ncaab_external/player_features/pit_player_features.jsonl"
V5_TABLE = "data/research/ncaab_external/player_features_v5/pit_player_features.jsonl"
V5_AUDIT = REPO / "data/research/ncaab_external/player_features_v5/audit_261121.json"
EVENTS_V5 = "data/research/ncaab_external/player_events_v5/events_2014_2022.jsonl"
A_ML_PREDS = "outputs/ncaab/research_261120/confirmation/A_ml_w0_confirmation_preds.jsonl"
RAW_PINS = {
    "scripts/ncaab_research/confirmation/replay_261120.py": "69235d8decc7e1cc2cb4ca5654d6486529eaeff17a30a79fc6e28bab591b8e90",
    EVENTS_V5: "14fbf653f04f86742ccf1f0e5a54b8fdd210175c1aaa8380d7dde8992508557b",
    "data/research/ncaab_market/backfill_261120/scoreboard.jsonl": "5eeeb8fc64d02ba400dec5ed525376b0267fdbd7bb8a8fbb7d49ef64b9ddb43a",
    V4_TABLE: "a9a406d87af14fc6ac9a6023dda79ad8ce8b02c0459825bebdc605f69f93062a",
    # built after the v5 pin, before any line or outcome join (coordinator, recorded on the card)
    V5_TABLE: "e08ff54483ef1cc7723b28f4b679c594d2cb317dc3bfb718a83294ff0255f928",
    # the A_ml_w0 confirmation replay's v7 arm (confirmation_pin_a_ml_w0.json predictions_sha256)
    A_ML_PREDS: "d1c0f45a15ad6249300e0d28871a54d3c4814f5aaeb10433a385479e76c2d930",
}
OUT = grader.OUT_DIR / GRID
COEF_PATH = OUT / "confirmation_coefficients.json"
PRED_PATH = OUT / "confirmation_v5_preds.jsonl"
INFO_PATH = OUT / "confirmation_v5_preds.info.json"
CARD_STEM = "confirmation_v5"
ML_CLIP = (0.0001, 0.9999)


# ------------------------------------------------------------------------------------------ pins
def sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def lf_sha256(path: Path) -> str:
    return sha_bytes(Path(path).read_bytes().replace(b"\r\n", b"\n"))


def load_addendum_v5(path: Path = ADDENDUM_V5_PATH) -> dict[str, Any]:
    """v1-v4 pins (grader_v4.load_addendum_v4 checks the JSON pins only) and the v5 pin."""
    grader_v4.load_addendum_v4()
    add = json.loads(Path(path).read_text(encoding="utf-8"))
    got = grader.canonical_sha256(add)
    if got != ADDENDUM_V5_SHA256:
        raise grader.ProtocolMismatchError(f"{path} hashes to {got}, pinned {ADDENDUM_V5_SHA256}")
    if tuple(add["f_confirmation_seasons"]["season_years"]) != CONF_SEASONS:
        raise grader.ProtocolMismatchError("v5 confirmation seasons drifted from the module constant")
    return add


def verify_inputs(
    lf_pins: dict[str, str] = LF_PINS, raw_pins: dict[str, str] = RAW_PINS, root: Path = REPO
) -> dict[str, str]:
    """Refuse on any pinned script (LF-normalised) or data file (raw) that hashes differently."""
    out = {}
    checks = [(rel, want, lf_sha256) for rel, want in lf_pins.items()]
    checks += [(rel, want, nr_sha) for rel, want in raw_pins.items()]
    for rel, want, hasher in checks:
        p = Path(root) / rel
        if not p.exists():
            raise grader.ProtocolMismatchError(f"pinned input {rel} is missing")
        got = hasher(p)
        if got != want:
            raise grader.ProtocolMismatchError(f"{rel} hashes to {got}, pinned {want}")
        out[rel] = got
    return out


def nr_sha(p: Path) -> str:
    return grader.nr.sha256_file(p)


def usable_seasons() -> dict[str, Any]:
    """Clause c, outcome-free: the audit's D1 team-game box pass rate per confirmation season."""
    rates = json.loads(V5_AUDIT.read_text(encoding="utf-8"))["d1_team_games_box_pass_rate_by_season"]
    rates = {int(k): float(v) for k, v in rates.items()}
    usable = [s for s in CONF_SEASONS if rates.get(s, 0.0) >= USABLE_MIN_RATE]
    return {
        "rates": {s: rates.get(s) for s in CONF_SEASONS},
        "usable": usable,
        "dropped": [s for s in CONF_SEASONS if s not in usable],
    }


def confirmation_already_read(lines: list[str], grid: str = GRID, window: str = WINDOW) -> bool:
    for line in lines:
        if line.strip():
            e = json.loads(line)
            if e.get("grid_name") == grid and e.get("window") == window:
                return True
    return False


def git_clean_and_tracked(path: Path) -> str:
    """The committed coefficients are the ones applied: refuse if the file is untracked or modified."""
    rel = str(path.relative_to(REPO)).replace("\\", "/")
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", rel], cwd=REPO, capture_output=True, check=False
    )
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", rel], cwd=REPO, capture_output=True, text=True, check=False
    )
    if tracked.returncode != 0 or dirty.stdout.strip():
        raise RuntimeError(f"{rel} must be committed and unmodified before predictions are built")
    head = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", rel], cwd=REPO, capture_output=True, text=True, check=False
    ).stdout.strip()
    return head


# ------------------------------------------------------------------------------------------ math
def p1_design(h_av, a_av, h_out, a_out) -> tuple[np.ndarray, np.ndarray]:
    h_av, a_av, h_out, a_out = (np.asarray(v, float) for v in (h_av, a_av, h_out, a_out))
    x = np.column_stack([h_av - a_av, h_out - a_out])
    valid = ~(np.isnan(h_av) | np.isnan(a_av) | np.isnan(h_out) | np.isnan(a_out))
    return x, valid


def apply_p1(mu_v7: np.ndarray, x: np.ndarray, valid: np.ndarray, coef: list[float]) -> np.ndarray:
    """mu = v7 + b1*dA + b2*dL on valid rows; exactly v7 (same float) where any input is null."""
    mu_v7 = np.asarray(mu_v7, float)
    xz = np.where(valid[:, None], x, 0.0)
    return np.where(valid, mu_v7 + xz @ np.asarray(coef, float), mu_v7)


def corr_delta(xc: np.ndarray, xv: np.ndarray, y: np.ndarray, days: list[str]) -> dict[str, Any]:
    """corr(y, xc) - corr(y, xv), day-cluster bootstrap; plain one-sided 95% lower bound."""

    def sums() -> dict[str, np.ndarray]:
        s = grader._sums(xc, y, "c")
        s.update({k: v for k, v in grader._sums(xv, y, "v").items() if k not in ("n", "y", "yy")})
        return s

    def dcorr(s: dict[str, np.ndarray]) -> np.ndarray:
        return grader._corr_from_sums(s, "c") - grader._corr_from_sums(s, "v")

    pt, smp = grader._boot(days, sums(), dcorr)
    return {
        "n": len(y),
        "days": len(set(days)),
        "delta": round(pt, 6),
        "lower_one_sided_95": round(float(grader.nr._percentiles(smp, ALPHA_ONE_SIDED)[0]), 6),
        "ci95_two_sided": [round(v, 6) for v in grader.nr._percentiles(smp, 0.025)],
        "corr_cand": round(float(np.corrcoef(xc, y)[0, 1]), 6),
        "corr_v7": round(float(np.corrcoef(xv, y)[0, 1]), 6),
    }


def verdict(primary: dict[str, Any], n_usable: int) -> str:
    if n_usable < MIN_USABLE_SEASONS:
        return "CONFIRMATION_UNAVAILABLE"
    return "CONFIRMED" if primary["lower_one_sided_95"] > 0 else "NOT_CONFIRMED"


# ------------------------------------------------------------------------------------------ commands
def cmd_coef(_args: argparse.Namespace) -> int:
    """Clause d: one pooled OLS on the 2023-2026 development cohort rows. Reads no confirmation data."""
    load_addendum_v5()
    shas = verify_inputs()
    if COEF_PATH.exists():
        raise RuntimeError(f"{COEF_PATH} exists; the coefficients are written once")
    frame = pp.load_frame()  # v4 table, v4 baseline v7 mean, dev outcomes only (2023-2026)
    if set(frame["season"]) != set(grader.DEV_SEASONS):
        raise AssertionError(f"development frame seasons {sorted(set(frame['season']))}")
    fit = pp.fit(frame, GRID)
    rec = {
        "grid_name": GRID,
        "clause": "addendum v5 d_coefficients: one OLS fit, no intercept, on ALL development cohort rows 2023-2026",
        "addendum_v5_sha256": ADDENDUM_V5_SHA256,
        "addendum_v4_sha256": grader_v4.ADDENDUM_V4_SHA256,
        "b1_dA_avail_top7_prev1": fit["coef"][0],
        "b2_dL_top7_minutes_share_out_3plus": fit["coef"][1],
        "coef": fit["coef"],
        "n": fit["n_train"],
        "n_by_season": {
            int(s): int(n)
            for s, n in frame[pp.design(frame, GRID)[1] & frame["cohort_eligible"].to_numpy(bool)]
            .groupby("season")
            .size()
            .items()
        },
        "fit_seasons": list(grader.DEV_SEASONS),
        "train_target_rmse_before": fit["train_target_rmse_before"],
        "train_target_rmse_after": fit["train_target_rmse_after"],
        "regressor_sd": fit["regressor_sd"],
        "target": "margin - v7_mu, v7_mu = grader.baseline_predictions(load_archive(2023-2026))['v7_served']",
        "inputs_table": {V4_TABLE: shas[V4_TABLE]},
        "written_before": "any confirmation-window line or outcome is loaded",
        "written_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "code_lf_sha256": {
            "scripts/ncaab_research/confirmation/confirm_p1_261122.py": lf_sha256(Path(__file__)),
            "scripts/ncaab_research/candidates/p_player_261121.py": lf_sha256(Path(pp.__file__)),
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    COEF_PATH.write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(rec, indent=2, sort_keys=True))
    return 0


def _load_v7_rows(seasons: list[int]) -> list[dict[str, Any]]:
    rows = [json.loads(x) for x in (REPO / A_ML_PREDS).read_text(encoding="utf-8").splitlines() if x.strip()]
    return [r for r in rows if int(r["season"]) in seasons]


def _home_ids(event_ids: set[str]) -> dict[str, str]:
    """Outcome-free: home team id per event from the pinned v5 event export (scores are never read)."""
    out = {}
    for rec in grader._iter_jsonl(REPO / EVENTS_V5):
        for e in rec["events"]:
            eid = str(e["event_id"])
            if eid in event_ids and eid not in out:
                out[eid] = str(e["home_team_id"])
    return out


def cmd_predict(_args: argparse.Namespace) -> int:
    """Outcome- and line-free predictions for every replayed D1-vs-D1 game of the usable seasons."""
    load_addendum_v5()
    verify_inputs()
    commit = git_clean_and_tracked(COEF_PATH)
    coef_rec = json.loads(COEF_PATH.read_text(encoding="utf-8"))
    coef = [float(coef_rec["b1_dA_avail_top7_prev1"]), float(coef_rec["b2_dL_top7_minutes_share_out_3plus"])]
    us = usable_seasons()
    rows = _load_v7_rows(us["usable"])
    ids = {str(r["event_id"]) for r in rows}
    cols = ["event_id", "season", "side", "team_id", "game_date", *pp.P1_INPUTS]
    f = pd.read_json(REPO / V5_TABLE, lines=True, dtype={"event_id": str, "team_id": str})[cols]
    f = f[f["season"].isin(us["usable"]) & f["event_id"].isin(ids)]
    if f.duplicated(["event_id", "side"]).any():
        raise ValueError("duplicate (event_id, side) feature rows")
    wide = f.pivot(index="event_id", columns="side")
    wide.columns = [f"{side}_{c}" for c, side in wide.columns]
    v7 = pd.DataFrame(rows)
    v7["event_id"] = v7["event_id"].astype(str)
    d = v7.merge(wide.reset_index(), on="event_id", how="left")
    both = d["home_team_id"].notna() & d["away_team_id"].notna()
    home = _home_ids(ids)
    flipped = int((d.loc[both, "home_team_id"].astype(str) != d.loc[both, "event_id"].map(home)).sum())
    fd = pd.to_datetime(d.loc[both, "home_game_date"].astype(str).str[:10])
    gap = (pd.to_datetime(d.loc[both, "game_date"]) - fd).dt.days
    if flipped or (gap < 0).any() or (gap > MAX_DATE_GAP_DAYS).any():
        raise AssertionError(
            f"orientation/date check failed: {flipped} flipped, gaps {gap.value_counts().to_dict()}"
        )
    x, valid = p1_design(
        d["home_avail_top7_prev1"],
        d["away_avail_top7_prev1"],
        d["home_top7_minutes_share_out_3plus"],
        d["away_top7_minutes_share_out_3plus"],
    )
    mu_v7 = d["mu_margin"].to_numpy(float)
    mu = apply_p1(mu_v7, x, valid, coef)
    sc = d["scale_margin"].to_numpy(float)
    pm = grader.p_home_from_margin(mu, sc, np.full(len(d), "t8"))
    w = grader.ELO_WEIGHT_SERVED
    p_ml = np.clip(w * d["elo_p_home"].to_numpy(float) + (1.0 - w) * pm, *ML_CLIP)
    out = []
    for i, r in enumerate(d.itertuples(index=False)):
        out.append(
            {
                "event_id": str(r.event_id),
                "season": int(r.season),
                "game_date": str(r.game_date),
                "neutral": bool(r.neutral),
                "games_played_min": int(r.games_played_min),
                "mu_v7": float(mu_v7[i]),
                "mu_margin": float(mu[i]),
                "scale_margin": float(sc[i]),
                "dist": "t8",
                "adj_valid": bool(valid[i]),
                "p_home_ml_v7_served": float(r.p_home_ml_v7_served),
                "p_home_ml": float(p_ml[i]),
            }
        )
    body = "".join(json.dumps(r, sort_keys=True) + "\n" for r in out).encode("utf-8")
    if confirmation_already_read(_log_lines()) and PRED_PATH.exists() and PRED_PATH.read_bytes() != body:
        raise RuntimeError("the confirmation read is logged; refusing to rewrite its predictions")
    PRED_PATH.write_bytes(body)
    nonzero = np.abs(mu - mu_v7) > grader_v4.NONZERO_TOL
    by_s = d.assign(_both=both, _valid=valid, _nz=nonzero).groupby("season")
    info = {
        "predictions_file": str(PRED_PATH.relative_to(REPO)).replace("\\", "/"),
        "predictions_sha256": sha_bytes(body),
        "coefficients_file_commit": commit,
        "coefficients": coef,
        "usable_seasons": us,
        "n_rows": len(out),
        "games_by_season": {
            int(s): {
                "v7_rows": len(g),
                "feature_rows_found_both_sides": int(g["_both"].sum()),
                "not_found": int((~g["_both"]).sum()),
                "found_but_null_input": int((g["_both"] & ~g["_valid"]).sum()),
                "all_inputs_non_null": int(g["_valid"].sum()),
                "adjusted_nonzero": int(g["_nz"].sum()),
            }
            for s, g in by_s
        },
        "date_gap_days_counts": {int(k): int(v) for k, v in gap.value_counts().items()},
        "orientation_flipped": flipped,
        "adjustment": {
            "share_nonzero": round(float(nonzero.mean()), 5),
            "mean_abs_nonzero": round(float(np.abs(mu - mu_v7)[nonzero].mean()), 4)
            if nonzero.any()
            else None,
        },
        "written_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    INFO_PATH.write_text(json.dumps(info, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(info, indent=2, sort_keys=True))
    return 0


def _log_lines() -> list[str]:
    return grader_v4.READ_LOG.read_text(encoding="utf-8").splitlines() if grader_v4.READ_LOG.exists() else []


def _backfill_lines(ids: set[str]) -> dict[str, float]:
    """Closing home margin L per event with a non-null selected spread line (ncaab-espn-lines-rule-1)."""
    out = {}
    for rec in grader._iter_jsonl(grader.LINE_SOURCES["espn_backfill_261120"]["path"]):
        eid = str(rec["event_id"])
        hs = (rec.get("selected") or {}).get("home_spread_line")
        if eid in ids and hs is not None and eid not in out:
            out[eid] = grader.implied_home_margin(hs)
    return out


def score(shuffle_seed: int | None) -> dict[str, Any]:
    load_addendum_v5()
    shas = verify_inputs()
    info = json.loads(INFO_PATH.read_text(encoding="utf-8"))
    if sha_bytes(PRED_PATH.read_bytes()) != info["predictions_sha256"]:
        raise RuntimeError("predictions file changed since predict")
    commit = git_clean_and_tracked(COEF_PATH)
    us = usable_seasons()
    preds = pd.read_json(PRED_PATH, lines=True, dtype={"event_id": str}, precise_float=True)
    preds["event_id"] = preds["event_id"].astype(str)
    lines = _backfill_lines(set(preds["event_id"]))
    # outcomes: the confirmation replay's history (as the A_ml_w0 read), joined only now
    import replay_261120 as rp

    events, _ = rp.load_confirmation_history()
    margin = {str(e["event_id"]): float(int(e["home_score"]) - int(e["away_score"])) for e in events}
    df = preds[preds["event_id"].isin(lines)].copy()
    df["line"] = df["event_id"].map(lines)
    df["margin"] = df["event_id"].map(margin)
    if df["margin"].isna().any():
        raise RuntimeError(f"{int(df['margin'].isna().sum())} cohort games have no outcome")
    df = df.sort_values(["game_date", "event_id"]).reset_index(drop=True)
    if shuffle_seed is not None:
        rng = np.random.default_rng(shuffle_seed)
        for idx in df.groupby("season").groups.values():
            df.loc[idx, "margin"] = df.loc[idx, "margin"].to_numpy()[rng.permutation(len(idx))]
    y = (df["margin"] - df["line"]).to_numpy(float)
    xc = (df["mu_margin"] - df["line"]).to_numpy(float)
    xv = (df["mu_v7"] - df["line"]).to_numpy(float)
    days = df["game_date"].tolist()
    primary = corr_delta(xc, xv, y, days)
    by_season = {}
    for s, g in df.groupby("season"):
        k = (df["season"] == s).to_numpy()
        by_season[int(s)] = {
            "n": int(k.sum()),
            "delta": round(float(np.corrcoef(xc[k], y[k])[0, 1] - np.corrcoef(xv[k], y[k])[0, 1]), 6),
            "corr_cand": round(float(np.corrcoef(xc[k], y[k])[0, 1]), 6),
            "corr_v7": round(float(np.corrcoef(xv[k], y[k])[0, 1]), 6),
            "share_adjusted_nonzero": round(float(np.mean(np.abs(g["mu_margin"] - g["mu_v7"]) > 1e-9)), 5),
        }
    # secondaries (no verdict) with grader's paired machinery on the same games
    t8 = np.full(len(df), "t8")
    sec = pd.DataFrame(
        {
            "season": df["season"],
            "game_date": df["game_date"],
            "margin": df["margin"],
            "line": df["line"],
            "cand_mu_margin": df["mu_margin"],
            "v7_served_mu_margin": df["mu_v7"],
            "cand_p_cover": grader.p_above_line(df["mu_margin"], df["scale_margin"], df["line"], t8),
            "v7_served_p_cover": grader.p_above_line(df["mu_v7"], df["scale_margin"], df["line"], t8),
            "cand_p_ml": df["p_home_ml"],
            "v7_served_p_ml": df["p_home_ml_v7_served"],
        }
    )
    paired = grader.paired_vs_v7(sec, grader_v4.K_V4)
    # the primary must agree with grader's own paired statistic on the same games
    if abs(paired["spread_corr_delta_vs_v7"]["delta"] - primary["delta"]) > 1e-5:
        raise AssertionError(f"primary {primary['delta']} != grader {paired['spread_corr_delta_vs_v7']}")
    nonzero = grader_v4.nonzero_subset_report(sec)
    lined_d1 = len(lines)  # lined events among the replayed D1-vs-D1 games
    n_usable = len(us["usable"])
    return {
        "grid_name": GRID,
        "candidate": CANDIDATE,
        "window": WINDOW,
        "addendum_v5_sha256": ADDENDUM_V5_SHA256,
        "addendum_v4_sha256": grader_v4.ADDENDUM_V4_SHA256,
        "protocol_sha256": grader.PROTOCOL_SHA256,
        "shuffled_outcomes": shuffle_seed is not None,
        "pinned_inputs": shas,
        "coefficients": info["coefficients"],
        "coefficients_file_commit": commit,
        "predictions_sha256": info["predictions_sha256"],
        "usable_seasons": us,
        "line_source": {
            "name": "espn_backfill_261120",
            "rule": "ncaab-espn-lines-rule-1",
            "sha256": nr_sha(Path(grader.LINE_SOURCES["espn_backfill_261120"]["path"])),
        },
        "cohort": {
            "definition": "replayed D1-vs-D1 games of the usable seasons (A_ml_w0 confirmation rules) with a "
            "non-null espn_backfill_261120 selected spread line",
            "n": len(df),
            "n_by_season": {int(s): int(n) for s, n in df.groupby("season").size().items()},
            "replayed_d1_games_by_season": {
                int(s): int(n) for s, n in preds.groupby("season").size().items()
            },
            "lined_events_matched": lined_d1,
        },
        "games_by_season": info["games_by_season"],
        "primary_spread_corr_delta_vs_v7": primary,
        "by_season": by_season,
        "pass_rule": "v4: CONFIRMED iff the plain one-sided 95% lower bound of the pooled corr delta > 0 "
        "(day-cluster bootstrap, 4000, seed 17); fewer than 3 usable seasons => CONFIRMATION_UNAVAILABLE",
        "verdict": verdict(primary, n_usable) if shuffle_seed is None else "PLUMBING_ONLY",
        "would_be_verdict": verdict(primary, n_usable),
        "secondary_no_verdict": {
            "margin_rmse_delta_vs_v7": paired["margin_rmse_delta_vs_v7"],
            "cover_logloss_delta_vs_v7": paired["cover_logloss_delta_vs_v7"],
            "moneyline_logloss_delta_vs_v7": paired["moneyline_logloss_delta_vs_v7"],
            "moneyline_brier_delta_vs_v7": paired["moneyline_brier_delta_vs_v7"],
            "mu_equals_v7": paired["mu_equals_v7"],
            "rmse": {
                "cand": grader._err(df["margin"].to_numpy(float), df["mu_margin"].to_numpy(float)),
                "v7": grader._err(df["margin"].to_numpy(float), df["mu_v7"].to_numpy(float)),
                "line": grader._err(df["margin"].to_numpy(float), df["line"].to_numpy(float)),
            },
            "nonzero_adjustment_subset": nonzero,
        },
        "disclosures": [
            (
                "Coefficients: one pooled 2023-2026 fit (clause d), committed before any confirmation line or "
                "outcome was loaded; no refit."
            ),
            "Backcast: v7 constants and P1 coefficients come from later seasons and are applied to 2018-2022.",
            "Date quirk (clause e): table date 0 or 1 day before the Eastern tip day; quirk rows kept.",
            (
                "Transfers carrying a new ESPN id count as newcomers (v4 feature_table.transfers); P1's inputs "
                "are same-season only."
            ),
        ],
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def render_md(card: dict[str, Any]) -> str:
    p, s = card["primary_spread_corr_delta_vs_v7"], card["secondary_no_verdict"]
    shuf = " - SHUFFLED PLUMBING (no number means anything)" if card["shuffled_outcomes"] else ""
    lines = [
        f"# P1_player_availability confirmation read (addendum v5){shuf}",
        "",
        (
            f"Addendum v5 sha256 `{card['addendum_v5_sha256']}`. Predictions sha256 "
            f"`{card['predictions_sha256']}`. Coefficients (pooled 2023-2026, commit "
            f"{card['coefficients_file_commit'][:8]}): b1 {card['coefficients'][0]:+.4f}, "
            f"b2 {card['coefficients'][1]:+.4f}. Line source espn_backfill_261120. Points and probabilities only."
        ),
        "",
        (
            f"Usable seasons (>= 85% D1 box pass): {card['usable_seasons']['usable']} rates "
            f"{card['usable_seasons']['rates']}; dropped {card['usable_seasons']['dropped'] or 'none'}."
        ),
        "",
        "| statistic | n | days | delta | one-sided 95% lower | two-sided 95% | corr cand | corr v7 |",
        "|---|---|---|---|---|---|---|---|",
        (
            f"| spread_corr_delta_vs_v7 (pooled) | {p['n']} | {p['days']} | {p['delta']:+.6f} | "
            f"{p['lower_one_sided_95']:+.6f} | [{p['ci95_two_sided'][0]:+.6f}, {p['ci95_two_sided'][1]:+.6f}] | "
            f"{p['corr_cand']:+.6f} | {p['corr_v7']:+.6f} |"
        ),
        "",
        f"**Verdict: {card['verdict']}** ({card['pass_rule']})",
        "",
        "| season | n | delta | corr cand | corr v7 | share adjusted |",
        "|---|---|---|---|---|---|",
    ]
    for k, b in card["by_season"].items():
        lines.append(
            f"| {k} | {b['n']} | {b['delta']:+.6f} | {b['corr_cand']:+.6f} | {b['corr_v7']:+.6f} | "
            f"{b['share_adjusted_nonzero']:.3f} |"
        )
    lines += ["", "## Secondary (no verdict)", ""]
    for key in ("margin_rmse_delta_vs_v7", "cover_logloss_delta_vs_v7", "moneyline_logloss_delta_vs_v7"):
        v = s[key]
        lines.append(f"- {key}: {v['delta']:+.6f} 95% {grader._ci_txt(v['ci95'], 5)}")
    lines.append(f"- RMSE cand / v7 / line: {json.dumps(s['rmse'])}")
    nz = s["nonzero_adjustment_subset"]
    lines.append(f"- non-zero-adjustment subset: n {nz['n_nonzero']} ({nz['share_nonzero_all']:.1%})")
    if "paired_vs_v7" in nz:
        for key in ("spread_corr_delta_vs_v7", "margin_rmse_delta_vs_v7", "cover_logloss_delta_vs_v7"):
            v = nz["paired_vs_v7"][key]
            lines.append(f"  - {key}: {v['delta']:+.5f} 95% {grader._ci_txt(v['ci95'], 5)}")
        lines.append(f"  - adjustment: {json.dumps(nz['adjustment'])}")
    lines += ["", "## Games by season (feature join)", "", f"{json.dumps(card['games_by_season'])}", ""]
    lines += ["## Disclosures", "", *[f"- {d}" for d in card["disclosures"]], ""]
    return "\n".join(lines)


def _write(card: dict[str, Any], stem: str) -> None:
    (OUT / f"{stem}.json").write_text(
        json.dumps(grader_v4._jsonable(card), indent=1, sort_keys=True), encoding="utf-8"
    )
    (OUT / f"{stem}.md").write_text(render_md(card), encoding="utf-8")


def cmd_shuffle(args: argparse.Namespace) -> int:
    card = score(args.seed)
    _write(card, f"{CARD_STEM}_shuffled_{args.seed}")
    print(render_md(card))
    return 0


def cmd_read(_args: argparse.Namespace) -> int:
    load_addendum_v5()
    verify_inputs()
    if confirmation_already_read(_log_lines()):
        raise RuntimeError(f"{GRID} already has a {WINDOW} read; exactly one is allowed")
    if (OUT / f"{CARD_STEM}.json").exists():
        raise RuntimeError(f"{CARD_STEM}.json exists; exactly one read is allowed")
    info = json.loads(INFO_PATH.read_text(encoding="utf-8"))
    entry = {
        "at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "grid_name": GRID,
        "candidate": CANDIDATE,
        "window": WINDOW,
        "line_source": "espn_backfill_261120",
        "protocol_sha256": grader.PROTOCOL_SHA256,
        "addendum_v5_sha256": ADDENDUM_V5_SHA256,
        "predictions_sha256": info["predictions_sha256"],
        "coefficients_file_commit": info["coefficients_file_commit"],
        "feature_table_v5_sha256": RAW_PINS[V5_TABLE],
    }
    grader_v4.READ_LOG.parent.mkdir(parents=True, exist_ok=True)
    with grader_v4.READ_LOG.open("a", encoding="utf-8") as fh:  # logged BEFORE any statistic exists
        fh.write(json.dumps(entry, sort_keys=True) + "\n")
    card = score(None)
    _write(card, CARD_STEM)
    print(render_md(card))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("coef")
    sub.add_parser("predict")
    sh = sub.add_parser("shuffle")
    sh.add_argument("--seed", type=int, default=5)
    sub.add_parser("read")
    args = ap.parse_args(argv)
    fn = {"coef": cmd_coef, "predict": cmd_predict, "shuffle": cmd_shuffle, "read": cmd_read}[args.cmd]
    return fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
