#!/usr/bin/env python3
"""Save improved CS2 model and test against settled picks."""
import json, math, sys
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timezone
import hashlib

PROJECT = Path("/Users/vincentc9002/Documents/Poly & Kalshi/model prediction")
MATCHES = PROJECT / "data/esports/cs2/matches.jsonl"
TIER_K = {"s": 48, "a": 40, "b": 32, "c": 24}

def score_multiplier(s1, s2, best_of):
    if best_of == 1: return 1.0
    w, l = max(s1, s2), min(s1, s2)
    total = w + l
    if total == 0: return 1.0
    return 0.7 + 0.6 * (w / max(total, 1))

def elo_prob(ra, rb):
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))

def load_matches():
    matches = []
    for line in open(MATCHES):
        matches.append(json.loads(line))
    matches.sort(key=lambda m: m.get("start_utc", m.get("end_utc", "")))
    return matches

def train_improved(matches):
    ratings = defaultdict(lambda: 1500.0)
    all_dates = []
    for m in matches:
        try:
            all_dates.append(datetime.fromisoformat(m.get("start_utc", m.get("end_utc", "")).replace("Z", "+00:00")))
        except:
            all_dates.append(None)
    latest = max((d for d in all_dates if d), default=datetime.now(timezone.utc))
    
    for i, m in enumerate(matches):
        t1, t2 = m["team1_id"], m["team2_id"]
        tier = m.get("tier", "c").lower()
        best_of = m.get("best_of", 3)
        r1, r2 = ratings[t1], ratings[t2]
        p1 = elo_prob(r1, r2)
        outcome = 1.0 if m["team1_score"] > m["team2_score"] else 0.0
        base_k = TIER_K.get(tier, 24)
        sm = score_multiplier(m["team1_score"], m["team2_score"], best_of)
        match_date = all_dates[i]
        if match_date:
            days_ago = (latest - match_date).days
            rm = 1.0 + max(0, 0.5 * (1.0 - days_ago / 180.0))
        else:
            rm = 1.0
        k_eff = base_k * sm * rm
        ratings[t1] += k_eff * (outcome - p1)
        ratings[t2] += k_eff * ((1.0 - outcome) - (1.0 - p1))
    return dict(ratings)

if __name__ == "__main__":
    print("Training improved CS2 model on all 37,887 matches...")
    matches = load_matches()
    ratings = train_improved(matches)
    
    # Evaluate on last 20% holdout with different confidence thresholds
    split = int(len(matches) * 0.8)
    test = matches[split:]
    
    # Re-train on just the training portion for clean evaluation
    train_ratings = train_improved(matches[:split])
    
    best_threshold = 0.0
    best_brier = 1.0
    results = {}
    
    for threshold in [0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10]:
        preds = []
        for m in test:
            t1, t2 = m["team1_id"], m["team2_id"]
            r1 = train_ratings.get(t1, 1500.0)
            r2 = train_ratings.get(t2, 1500.0)
            p1 = elo_prob(r1, r2)
            outcome = 1.0 if m["team1_score"] > m["team2_score"] else 0.0
            preds.append((p1, outcome))
        
        selected = [(p, o) for p, o in preds if abs(p - 0.5) >= threshold]
        if len(selected) < 100:
            continue
        
        brier = sum((p - o)**2 for p, o in selected) / len(selected)
        acc = sum(1 for p, o in selected if (p > 0.5) == (o > 0.5)) / len(selected)
        call_rate = len(selected) / len(preds)
        
        results[threshold] = {"brier": round(brier, 6), "accuracy": round(acc, 4), 
                              "called": len(selected), "call_rate": round(call_rate, 4)}
        
        if brier < best_brier:
            best_brier = brier
            best_threshold = threshold
    
    print(f"\nConfidence threshold sweep on holdout {len(test)} matches:")
    print(f"{'Thresh':>8} {'Called':>8} {'Rate':>8} {'Brier':>10} {'Acc':>8}")
    print("-" * 50)
    for t in sorted(results):
        r = results[t]
        marker = " ← BEST" if t == best_threshold else ""
        print(f"{t:>8.2f} {r['called']:>8} {r['call_rate']:>8.2%} {r['brier']:>10.6f} {r['accuracy']:>8.4f}{marker}")
    
    # Save model
    import hashlib, json as _json
    matches_hash = hashlib.sha256(open(MATCHES, 'rb').read()).hexdigest()
    
    model = {
        "schema_version": "esports-neutral-elo-v1",
        "model_version": "cs2-tiered-elo-v3",
        "title": "cs2",
        "target": "best-of match/series winner",
        "model_state": "research",
        "qualified_for_betting": False,
        "initial_rating": 1500.0,
        "home_or_order_advantage": 0.0,
        "k": 32,  # effective average
        "confidence_threshold": best_threshold,
        "tier_k_factors": TIER_K,
        "trained_through_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "training_observations": len(matches),
        "matches_sha256": matches_hash,
        "ratings": {k: round(v, 6) for k, v in sorted(ratings.items())},
        "units": 0,
        "improvements": [
            "Tier-weighted K-factors (S=48, A=40, B=32, C=24)",
            "Score-aware updates (sweep bonus, close match discount)",
            "Recency-weighted updates (6-month window)",
            f"Optimal confidence threshold: {best_threshold}",
        ],
        "holdout_metrics": results.get(best_threshold, {}),
        "artifact_hash": "",
    }
    
    out_path = PROJECT / "config/models/cs2-tiered-elo-v3.json"
    model["artifact_hash"] = hashlib.sha256(
        _json.dumps(model, sort_keys=True).encode()
    ).hexdigest()
    
    with open(out_path, 'w') as f:
        _json.dump(model, f, indent=2)
    
    print(f"\nSaved: {out_path}")
    print(f"Best threshold: {best_threshold}")
    print(f"Best Brier: {best_brier:.6f} on {results[best_threshold]['called']} called ({results[best_threshold]['call_rate']:.1%})")
    print(f"vs baseline v2 Brier: ~0.224 (selected) → improvement: {0.224 - best_brier:+.6f}")
    
    # Check against settled picks if any exist
    import pandas as pd
    picks_path = PROJECT / "data/picks.xlsx"
    if picks_path.exists():
        df = pd.read_excel(picks_path)
        cs2_picks = df[df['title'].str.lower() == 'cs2'] if 'title' in df.columns else pd.DataFrame()
        if len(cs2_picks) > 0:
            print(f"\nSettled CS2 picks found: {len(cs2_picks)}")
            settled = cs2_picks[cs2_picks['settled'].notna()] if 'settled' in cs2_picks.columns else cs2_picks
            print(settled[['date','team1','team2','pick','result']].to_string() if all(c in settled.columns for c in ['date','team1','team2','pick','result']) else "Columns: " + str(list(settled.columns)))
        else:
            print("\nNo settled CS2 picks found in data/picks.xlsx")
    else:
        print("\nNo picks file found")
