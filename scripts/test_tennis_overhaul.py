"""Experimentation harness to test genuine model improvements for Tennis."""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import KFold

from model_prediction.config import PROJECT_ROOT
from model_prediction.runtime_paths import RuntimePaths


def run():
    # 1. Load games
    games = []
    with open(PROJECT_ROOT / "data/processed/tennis/games.jsonl", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                games.append(json.loads(line))
    games.sort(key=lambda m: str(m.get("event_start_utc", "")))
    print(f"Loaded {len(games)} historical matches.")

    # Build chronological match histories
    player_history = defaultdict(list)
    for m in games:
        d = str(m.get("event_start_utc", ""))
        w = m.get("winner")
        l = m.get("loser")
        s = m.get("surface", "Hard")
        if w and l:
            player_history[w].append((d, "W", s, l))
            player_history[l].append((d, "L", s, w))

    # 2. Load settled rows
    paths = RuntimePaths.resolve()
    conn = sqlite3.connect(paths.ledgers_db)
    rows = conn.execute("""
        SELECT event_id, selection, result, model_probability, market_probability, decision_payload_json
        FROM ledger_records
        WHERE sport = 'tennis' AND market_type = 'moneyline' AND status = 'settled'
          AND result IN ('win', 'loss')
        ORDER BY event_start_utc ASC
    """).fetchall()
    conn.close()
    print(f"Loaded {len(rows)} settled ledger rows.")

    X_list = []
    y_list = []
    p_mkt_list = []
    p_v1_list = []

    for r in rows:
        p_dec = json.loads(r[5])
        snap_raw = p_dec.get("model_input_snapshot_json", {})
        if isinstance(snap_raw, str):
            try:
                snap = json.loads(snap_raw) if snap_raw.strip() else {}
            except (ValueError, TypeError):
                snap = {}
        else:
            snap = snap_raw or {}

        inf = snap.get("inference_inputs", {})

        sel = str(r[1]).lower()
        res = str(r[2]).lower()
        y = 1 if res == "win" else 0

        p_v1 = float(r[3])
        p_mkt = float(r[4]) if r[4] is not None else p_v1

        away = p_dec.get("away_team")
        home = p_dec.get("home_team")
        target_player = away if sel == "away" else home
        opp_player = home if sel == "away" else away

        date_str = str(p_dec.get("event_start_utc") or p_dec.get("created_at_utc", ""))
        surface = inf.get("surface", "Hard")

        # Extract ratings
        if "overall_ratings" in inf and len(inf["overall_ratings"]) == 2:
            idx = 0 if sel == "away" else 1
            opp_idx = 1 if idx == 0 else 0
            r_target = inf["overall_ratings"][idx]
            r_opp = inf["overall_ratings"][opp_idx]
            sr_target = inf["surface_ratings"][idx]
            sr_opp = inf["surface_ratings"][opp_idx]
            sc_target = inf["surface_counts"][idx]
            sc_opp = inf["surface_counts"][opp_idx]
        else:
            r_target = 1500.0
            r_opp = 1500.0
            sr_target = 1500.0
            sr_opp = 1500.0
            sc_target = 0
            sc_opp = 0

        # Form prior to date_str
        h_target = [x for x in player_history[target_player] if x[0] < date_str]
        h_opp = [x for x in player_history[opp_player] if x[0] < date_str]

        # Recent 10 win rate
        rec_target = h_target[-10:] if h_target else []
        rec_opp = h_opp[-10:] if h_opp else []
        wr_target = sum(1 for x in rec_target if x[1] == "W") / len(rec_target) if rec_target else 0.5
        wr_opp = sum(1 for x in rec_opp if x[1] == "W") / len(rec_opp) if rec_opp else 0.5

        # Rest days
        def parse_days(h, cur_date_str):
            if not h:
                return 14
            try:
                d_last = datetime.fromisoformat(str(h[-1][0])).date()
                d_cur = datetime.fromisoformat(str(cur_date_str)).date()
                return min(30, max(0, (d_cur - d_last).days))
            except (ValueError, TypeError):
                return 14

        rest_t = parse_days(h_target, date_str)
        rest_o = parse_days(h_opp, date_str)

        # H2H
        h2h_target = sum(1 for x in h_target if x[3] == opp_player and x[1] == "W")
        h2h_opp = sum(1 for x in h_opp if x[3] == target_player and x[1] == "W")

        # Surface win rate
        s_target = [x for x in h_target if x[2] == surface]
        s_opp = [x for x in h_opp if x[2] == surface]
        swr_t = sum(1 for x in s_target if x[1] == "W") / len(s_target) if len(s_target) >= 5 else wr_target
        swr_o = sum(1 for x in s_opp if x[1] == "W") / len(s_opp) if len(s_opp) >= 5 else wr_opp

        tour = 1.0 if "atp" in str(p_dec.get("league", "")).lower() else 0.0

        feats = [
            (r_target - r_opp) / 400.0,
            (sr_target - sr_opp) / 400.0,
            math.log(1 + sc_target) - math.log(1 + sc_opp),
            wr_target - wr_opp,
            swr_t - swr_o,
            (rest_t - rest_o) / 7.0,
            h2h_target - h2h_opp,
            tour,
        ]

        X_list.append(feats)
        y_list.append(y)
        p_mkt_list.append(p_mkt)
        p_v1_list.append(p_v1)

    X = np.array(X_list)
    y = np.array(y_list)
    p_mkt = np.array(p_mkt_list)
    p_v1 = np.array(p_v1_list)

    print(f"Feature matrix shape: {X.shape}")
    kf = KFold(n_splits=5, shuffle=True, random_state=42)

    # 1. Independent Feature Model (without market)
    oof_indep = np.zeros(len(y))
    for tr, te in kf.split(X):
        lr = LogisticRegression(C=0.5)
        lr.fit(X[tr], y[tr])
        oof_indep[te] = lr.predict_proba(X[te])[:, 1]

    ll_indep = -np.mean(y * np.log(oof_indep) + (1 - y) * np.log(1 - oof_indep))
    br_indep = np.mean((oof_indep - y) ** 2)
    acc_indep = np.mean((oof_indep >= 0.5) == y)
    print("\n--- 1. Independent Feature Model (Out-of-Fold) ---")
    print(f"LogLoss: {ll_indep:.5f} | Brier: {br_indep:.5f} | Accuracy: {acc_indep:.4f}")

    # 2. Market-Residual Ensembled Model
    eps = 1e-6
    logit_mkt = np.log(np.clip(p_mkt, eps, 1 - eps) / (1 - np.clip(p_mkt, eps, 1 - eps))).reshape(-1, 1)
    logit_indep = np.log(np.clip(oof_indep, eps, 1 - eps) / (1 - np.clip(oof_indep, eps, 1 - eps))).reshape(
        -1, 1
    )
    X_combined = np.column_stack([logit_indep, logit_mkt])
    oof_comb = np.zeros(len(y))
    for tr, te in kf.split(X_combined):
        lr2 = LogisticRegression()
        lr2.fit(X_combined[tr], y[tr])
        oof_comb[te] = lr2.predict_proba(X_combined[te])[:, 1]

    ll_comb = -np.mean(y * np.log(oof_comb) + (1 - y) * np.log(1 - oof_comb))
    br_comb = np.mean((oof_comb - y) ** 2)
    acc_comb = np.mean((oof_comb >= 0.5) == y)
    print("\n--- 2. Market-Residual Ensembled Model (Out-of-Fold) ---")
    print(f"LogLoss: {ll_comb:.5f} | Brier: {br_comb:.5f} | Accuracy: {acc_comb:.4f}")

    # Baselines
    ll_v1 = -np.mean(y * np.log(p_v1) + (1 - y) * np.log(1 - p_v1))
    br_v1 = np.mean((p_v1 - y) ** 2)
    acc_v1 = np.mean((p_v1 >= 0.5) == y)
    print("\n--- Baselines ---")
    print(f"Incumbent v1 LogLoss: {ll_v1:.5f} | Brier: {br_v1:.5f} | Accuracy: {acc_v1:.4f}")
    ll_mkt = -np.mean(y * np.log(p_mkt) + (1 - y) * np.log(1 - p_mkt))
    br_mkt = np.mean((p_mkt - y) ** 2)
    acc_mkt = np.mean((p_mkt >= 0.5) == y)
    print(f"Market Baseline LogLoss: {ll_mkt:.5f} | Brier: {br_mkt:.5f} | Accuracy: {acc_mkt:.4f}")


if __name__ == "__main__":
    run()
