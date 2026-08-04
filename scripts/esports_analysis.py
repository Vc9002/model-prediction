#!/usr/bin/env python3
"""Esports model analysis: grid search + backtest for any esports title.

Usage:
  python scripts/esports_analysis.py cs2 grid     # Grid search K and threshold
  python scripts/esports_analysis.py cs2 backtest # Backtest on settled picks
  python scripts/esports_analysis.py lol both     # Both
"""
import json, math, sys, pandas as pd
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DATA = PROJECT / "data/esports"
MODELS = PROJECT / "config/models"
# Esports never reaches Main and (since 2026-08-03) no longer reaches Flat --
# Research/Gated Research are its only ledgers, one file per title.
def _research_ledger(title: str) -> Path:
    return PROJECT / f"data/research/{title.lower()}.xlsx"


def _gated_ledger(title: str) -> Path:
    return PROJECT / f"data/gated_research/{title.lower()}.xlsx"

# ── team name mapping ──────────────────────────────────────────────
TEAM_CACHE: dict[str, dict] = {}

def _load_teams(title: str) -> dict:
    if title not in TEAM_CACHE:
        teams = json.load(open(DATA / title / "teams.json"))
        name_to_id = {}
        for tid, t in teams.items():
            n = t.get("name", "").lower().strip()
            name_to_id[n] = tid
            name_to_id[n.replace(" ", "")] = tid
        TEAM_CACHE[title] = name_to_id
    return TEAM_CACHE[title]

def find_team(title: str, query: str) -> str | None:
    name_to_id = _load_teams(title)
    q = str(query).lower().strip()
    if q in name_to_id: return name_to_id[q]
    if q.replace(" ", "") in name_to_id: return name_to_id[q.replace(" ", "")]
    for n, tid in name_to_id.items():
        if q in n or n in q: return tid
    return None

# ── Elo ────────────────────────────────────────────────────────────
def elo_prob(ra: float, rb: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))

def score_mult(s1, s2, bo):
    if bo == 1: return 1.0
    w, l = max(s1, s2), min(s1, s2)
    if w + l == 0: return 1.0
    return 0.7 + 0.6 * (w / (w + l))

def load_matches(title: str) -> list[dict]:
    matches = []
    for line in open(DATA / title / "matches.jsonl"):
        matches.append(json.loads(line))
    matches.sort(key=lambda m: m.get("start_utc", m.get("end_utc", "")))
    return matches

# ── grid search ────────────────────────────────────────────────────
def grid_search(title: str) -> None:
    matches = load_matches(title)
    split = int(len(matches) * 0.8)
    train, test = matches[:split], matches[split:]

    dates = []
    for m in train:
        try: dates.append(datetime.fromisoformat(m.get("start_utc", m.get("end_utc", "")).replace("Z", "+00:00")))
        except: pass
    latest = max(dates) if dates else datetime.now(timezone.utc)

    print(f"\n{'='*70}")
    print(f"  GRID SEARCH — {title.upper()} ({len(matches)} matches)")
    print(f"{'='*70}")
    print(f"{'K':<6} {'recency':<8} {'score':<8} {'th':<6} {'Brier':<10} {'Called':>8}")
    print(f"{'-'*50}")

    best_brier, best_cfg = 1.0, None
    for K in [32, 48, 64, 96]:
        for use_rec in [False, True]:
            for use_score in [False, True]:
                ratings = defaultdict(lambda: 1500.0)
                for m in train:
                    t1, t2 = m["team1_id"], m["team2_id"]
                    p1 = elo_prob(ratings[t1], ratings[t2])
                    outcome = 1.0 if m["team1_score"] > m["team2_score"] else 0.0
                    k_eff = float(K)
                    if use_score: k_eff *= score_mult(m["team1_score"], m["team2_score"], m.get("best_of", 3))
                    if use_rec:
                        try:
                            md = datetime.fromisoformat(m.get("start_utc", m.get("end_utc", "")).replace("Z", "+00:00"))
                            k_eff *= 1.0 + max(0, 0.3 * (1.0 - (latest - md).days / 180.0))
                        except: pass
                    ratings[t1] += k_eff * (outcome - p1)
                    ratings[t2] += k_eff * ((1 - outcome) - (1 - p1))

                preds = [(elo_prob(ratings.get(m["team1_id"], 1500), ratings.get(m["team2_id"], 1500)),
                          1.0 if m["team1_score"] > m["team2_score"] else 0.0) for m in test]

                for th in [0.03, 0.05, 0.10, 0.15, 0.18, 0.20]:
                    sel = [(p, o) for p, o in preds if abs(p - 0.5) >= th]
                    if len(sel) < 50: continue
                    brier = sum((p - o) ** 2 for p, o in sel) / len(sel)
                    if brier < best_brier:
                        best_brier = brier
                        best_cfg = (K, use_rec, use_score, th, brier, len(sel))

    for K in [32, 48, 64, 96]:
        for use_rec in [False, True]:
            for use_score in [False, True]:
                for th in [0.03, 0.05, 0.10, 0.15, 0.18, 0.20]:
                    # Quick eval just for display
                    ratings = defaultdict(lambda: 1500.0)
                    for m in train:
                        t1, t2 = m["team1_id"], m["team2_id"]
                        p1 = elo_prob(ratings[t1], ratings[t2])
                        outcome = 1.0 if m["team1_score"] > m["team2_score"] else 0.0
                        k_eff = float(K)
                        if use_score: k_eff *= score_mult(m["team1_score"], m["team2_score"], m.get("best_of", 3))
                        ratings[t1] += k_eff * (outcome - p1)
                        ratings[t2] += k_eff * ((1 - outcome) - (1 - p1))
                    preds = [(elo_prob(ratings.get(m["team1_id"], 1500), ratings.get(m["team2_id"], 1500)),
                              1.0 if m["team1_score"] > m["team2_score"] else 0.0) for m in test]
                    sel = [(p, o) for p, o in preds if abs(p - 0.5) >= th]
                    if len(sel) < 50: continue
                    brier = sum((p - o) ** 2 for p, o in sel) / len(sel)
                    marker = " ← BEST" if (K, use_rec, use_score, th) == best_cfg[:4] else ""
                    if marker:
                        print(f"{K:<6} {str(use_rec):<8} {str(use_score):<8} {th:<6.2f} {brier:<10.6f} {len(sel):>8}{marker}")

    if best_cfg:
        K, rec, sc, th, brier, called = best_cfg
        print(f"\n  Best: K={K}  recency={rec}  score={sc}  th={th:.2f}  Brier={brier:.6f}  Called={called}/{len(test)}")

# ── Platt helper ────────────────────────────────────────────────────
def _apply_platt_anal(prob, intercept, slope):
    if intercept is None or slope is None:
        return prob
    clipped = min(1 - 1e-12, max(1e-12, prob))
    logit = math.log(clipped / (1 - clipped))
    return 1 / (1 + math.exp(-(intercept + slope * logit)))


# ── backtest ───────────────────────────────────────────────────────
def backtest(title: str) -> None:
    model_file = MODELS / f"{title}-tiered-elo-v4.json"
    if not model_file.exists():
        model_file = MODELS / f"{title}-tiered-elo-v3.json"
    if not model_file.exists():
        print(f"No model found for {title}")
        return

    cfg = json.load(open(model_file))
    ratings = cfg.get("ratings", {})
    threshold = cfg.get("confidence_threshold", 0.10)
    platt_intercept = cfg.get("platt_intercept")
    platt_slope = cfg.get("platt_slope")

    for fname, label in [(_research_ledger(title), "RESEARCH"), (_gated_ledger(title), "GATED")]:
        if not fname.exists(): continue
        df = pd.read_excel(fname, sheet_name="Picks")
        picks = df[(df["league"].str.upper() == title.upper()) & (df["pnl_units"].notna())]
        if len(picks) == 0: continue

        print(f"\n{'='*70}")
        print(f"  BACKTEST — {title.upper()} on {label} ({len(picks)} settled)")
        print(f"  Model: {cfg.get('model_version', '?')}  K={cfg.get('k','?')}  th={threshold}")
        print(f"{'='*70}")
        print(f"  {'Match':<45} {'Sd':>3} {'Raw':>7} {'Platt':>7} {'Edge':>8} {'Call':>6} {'P&L':>7}")
        print(f"  {'-'*90}")

        total_pnl, called_n, called_pnl = 0, 0, 0
        for _, r in picks.iterrows():
            away = find_team(title, r.get("away_team", ""))
            home = find_team(title, r.get("home_team", ""))
            prob, prob_platt, edge, called = 0.5, 0.5, 0.0, "?"
            if away and home:
                ra = ratings.get(away, 1500.0)
                rh = ratings.get(home, 1500.0)
                p_home_raw = elo_prob(rh, ra)
                p_home_platt = _apply_platt_anal(p_home_raw, platt_intercept, platt_slope)
                sel = str(r.get("selection", "")).lower()
                prob = p_home_raw if "home" in sel else (1 - p_home_raw)
                prob_platt = p_home_platt if "home" in sel else (1 - p_home_platt)
                impl = r.get("market_implied_probability", 0.5) or 0.5
                edge = prob_platt - impl
                called = "YES" if abs(prob_platt - 0.5) >= threshold else "no"

            matchup = f"{str(r['away_team'])[:22]} @ {str(r['home_team'])[:22]}"
            pnl = r["pnl_units"]
            total_pnl += pnl
            if called == "YES":
                called_n += 1
                called_pnl += pnl
            print(f"  {matchup:<45} {'H' if 'home' in str(r.get('selection','')).lower() else 'A':>3} {prob:>7.3f} {prob_platt:>7.3f} {edge:>+8.3f} {called:>6} {pnl:>+7.2f}")

        print(f"  {'-'*90}")
        print(f"  Total: {total_pnl:+.2f}U  |  Called only: {called_pnl:+.2f}U ({called_n} picks)")

# ── main ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python esports_analysis.py <title> <grid|backtest|both>")
        print("  title: cs2, lol, dota2, valorant")
        sys.exit(1)

    title = sys.argv[1].lower()
    action = sys.argv[2].lower()

    if action in ("grid", "both"):
        grid_search(title)
    if action in ("backtest", "both"):
        backtest(title)
