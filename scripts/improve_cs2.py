#!/usr/bin/env python3
"""CS2 model improvement: tier-weighted, score-aware, recency-weighted Elo."""

import json, math, sys
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timezone

PROJECT = Path("/Users/vincentc9002/Documents/Poly & Kalshi/model prediction")
MATCHES = PROJECT / "data/esports/cs2/matches.jsonl"

# Tier K-factors (higher tier = more informative)
TIER_K = {"s": 48, "a": 40, "b": 32, "c": 24}

# Score multiplier: sweep (2-0) = 1.3x, close (2-1) = 0.7x
def score_multiplier(s1, s2, best_of):
    if best_of == 1:
        return 1.0
    winner_score = max(s1, s2)
    loser_score = min(s1, s2)
    total = winner_score + loser_score
    if total == 0:
        return 1.0
    # Sweep bonus: winner_score / (winner_score + loser_score) scaled
    return 0.7 + 0.6 * (winner_score / max(total, 1))

def elo_prob(rating_a, rating_b):
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))

def load_matches():
    matches = []
    for line in open(MATCHES):
        d = json.loads(line)
        matches.append(d)
    matches.sort(key=lambda m: m.get("start_utc", m.get("end_utc", "")))
    return matches

def train_baseline(matches, k=96):
    """Current v2 model: flat K=96, no tier/score/recency adjustments."""
    ratings = defaultdict(lambda: 1500.0)
    predictions = []
    for m in matches:
        t1, t2 = m["team1_id"], m["team2_id"]
        r1, r2 = ratings[t1], ratings[t2]
        p1 = elo_prob(r1, r2)
        outcome = 1.0 if m["team1_score"] > m["team2_score"] else 0.0
        predictions.append((p1, outcome, m))
        # Update
        k_eff = k * score_multiplier(m["team1_score"], m["team2_score"], m.get("best_of", 3))
        ratings[t1] += k_eff * (outcome - p1)
        ratings[t2] += k_eff * ((1.0 - outcome) - (1.0 - p1))
    return predictions, ratings

def train_improved(matches):
    """Tier-weighted, recency-weighted, score-aware Elo."""
    ratings = defaultdict(lambda: 1500.0)
    match_counts = defaultdict(int)
    predictions = []
    
    all_dates = [m.get("start_utc", m.get("end_utc", "")) for m in matches]
    parsed_dates = []
    for d in all_dates:
        try:
            parsed_dates.append(datetime.fromisoformat(d.replace("Z", "+00:00")))
        except:
            parsed_dates.append(None)
    
    latest = max((d for d in parsed_dates if d), default=datetime.now(timezone.utc))
    
    for i, m in enumerate(matches):
        t1, t2 = m["team1_id"], m["team2_id"]
        tier = m.get("tier", "c").lower()
        best_of = m.get("best_of", 3)
        
        r1, r2 = ratings[t1], ratings[t2]
        p1 = elo_prob(r1, r2)
        outcome = 1.0 if m["team1_score"] > m["team2_score"] else 0.0
        predictions.append((p1, outcome, m))
        
        # Base K from tier
        base_k = TIER_K.get(tier, 24)
        
        # Score multiplier
        score_mult = score_multiplier(m["team1_score"], m["team2_score"], best_of)
        
        # Recency weight: matches in last 6 months get boost
        match_date = parsed_dates[i] if i < len(parsed_dates) else None
        if match_date:
            days_ago = (latest - match_date).days
            recency_mult = 1.0 + max(0, 0.5 * (1.0 - days_ago / 180.0))  # up to 1.5x for recent
        else:
            recency_mult = 1.0
        
        k_eff = base_k * score_mult * recency_mult
        
        ratings[t1] += k_eff * (outcome - p1)
        ratings[t2] += k_eff * ((1.0 - outcome) - (1.0 - p1))
        match_counts[t1] += 1
        match_counts[t2] += 1
    
    return predictions, ratings

def evaluate(predictions, name, confidence_threshold=0.03):
    """Evaluate predictions with Brier score and calibration."""
    # Chronological 80/20 split
    split = int(len(predictions) * 0.8)
    test = predictions[split:]
    
    selected = [(p, o) for p, o, _ in test if abs(p - 0.5) >= confidence_threshold]
    all_preds = [(p, o) for p, o, _ in test]
    
    def calc_metrics(data):
        if not data:
            return {"brier": None, "accuracy": None, "n": 0}
        n = len(data)
        brier = sum((p - o)**2 for p, o in data) / n
        acc = sum(1 for p, o in data if (p > 0.5) == (o > 0.5)) / n
        
        # ECE 10-bin
        bins = [[] for _ in range(10)]
        for p, o in data:
            bin_idx = min(9, int(p * 10))
            bins[bin_idx].append((p, o))
        ece = 0.0
        for b in bins:
            if b:
                avg_p = sum(p for p,_ in b) / len(b)
                avg_o = sum(o for _,o in b) / len(b)
                ece += (len(b)/n) * abs(avg_p - avg_o)
        return {"brier": round(brier, 6), "accuracy": round(acc, 4), "n": n, "ece": round(ece, 6)}
    
    all_metrics = calc_metrics(all_preds)
    sel_metrics = calc_metrics(selected)
    
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"{'='*60}")
    print(f"  Test set (last 20%):  {all_metrics['n']} matches")
    print(f"  Brier (all):          {all_metrics['brier']}")
    print(f"  Accuracy (all):       {all_metrics['accuracy']}")
    print(f"  ECE (all):            {all_metrics['ece']}")
    print(f"  --- confidence ≥ {confidence_threshold} ---")
    print(f"  Selected:             {sel_metrics['n']}")
    print(f"  Brier (selected):     {sel_metrics['brier']}")
    print(f"  Accuracy (selected):  {sel_metrics['accuracy']}")
    return all_metrics, sel_metrics

if __name__ == "__main__":
    print("Loading 37,887 CS2 matches...")
    matches = load_matches()
    print(f"Loaded {len(matches)} matches")
    
    # Baseline (current v2)
    print("\nTraining baseline (K=96, current v2)...")
    base_preds, base_ratings = train_baseline(matches)
    base_all, base_sel = evaluate(base_preds, "BASELINE (v2, K=96)")
    
    # Improved (tier-weighted, score-aware, recency)
    print("\nTraining improved (tier-K + score + recency)...")
    imp_preds, imp_ratings = train_improved(matches)
    imp_all, imp_sel = evaluate(imp_preds, "IMPROVED (tier-K + score-aware + recency)")
    
    # Comparison
    print(f"\n{'='*60}")
    print(f"  COMPARISON")
    print(f"{'='*60}")
    if base_all['brier'] and imp_all['brier']:
        delta = base_all['brier'] - imp_all['brier']
        direction = "better" if delta > 0 else "worse"
        print(f"  Brier delta: {delta:+.6f} ({direction})")
    if base_all['ece'] and imp_all['ece']:
        delta_ece = base_all['ece'] - imp_all['ece']
        dir_ece = "better" if delta_ece > 0 else "worse"
        print(f"  ECE delta:   {delta_ece:+.6f} ({dir_ece})")
    if base_all['accuracy'] and imp_all['accuracy']:
        delta_acc = imp_all['accuracy'] - base_all['accuracy']
        dir_acc = "better" if delta_acc > 0 else "worse"
        print(f"  Acc delta:   {delta_acc:+.4f} ({dir_acc})")
    
    print("\nDone.")
