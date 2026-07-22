#!/usr/bin/env python3
"""Grid search CS2 Elo configs: K, recency, score, threshold. Test on settled picks."""
import json, math, pandas as pd
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path("/Users/vincentc9002/Documents/Poly & Kalshi/model prediction")
MATCHES = PROJECT / "data/esports/cs2/matches.jsonl"

NAME_OVERRIDES = {
    'nip': 'bo3:1:785', 'm80': 'bo3:1:7430',
    'cybershoke esports': 'bo3:1:7429', 'bet-m 33': 'bo3:1:22191',
    'astralis': 'bo3:1:794', 'team nemesis': 'bo3:1:21388',
    'misa esports': 'bo3:1:20952', 'honvéd': 'bo3:1:1279', 'honved': 'bo3:1:1279',
    'alka gaming': 'bo3:1:23043', 'procyon gaming': 'bo3:1:898',
    'entropy': 'bo3:1:20939', 'esport academy copenhagen': 'bo3:1:21816',
    'gentle mates': 'bo3:1:21556', '3dmax': 'bo3:1:9960',
    'heroic': 'bo3:1:10190', 'hotu': 'bo3:1:21953',
    'quazar': 'bo3:1:21583', 'enjoy': 'bo3:1:21547',
    'just players': 'bo3:1:21901', 'ex-rustec': 'bo3:1:21741',
    'wildcard': 'bo3:1:22219', 'alliance': 'bo3:1:21776',
    'bestia academy': 'bo3:1:22135', 'borracheiros': 'bo3:1:21983',
}

teams_data = json.load(open(PROJECT / 'data/esports/cs2/teams.json'))
name_to_id = {}
for tid, t in teams_data.items():
    n = t.get('name','').lower().strip()
    name_to_id[n] = tid
    name_to_id[n.replace(' ','')] = tid

def find_team(q):
    q = str(q).lower().strip()
    if q in NAME_OVERRIDES: return NAME_OVERRIDES[q]
    if q in name_to_id: return name_to_id[q]
    if q.replace(' ','') in name_to_id: return name_to_id[q.replace(' ','')]
    matches = [tid for n, tid in name_to_id.items() if q in n or n in q]
    return matches[0] if len(matches) == 1 else None

def elo_prob(ra, rb):
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))

def score_mult(s1, s2, bo):
    if bo == 1: return 1.0
    w, l = max(s1,s2), min(s1,s2)
    if w+l == 0: return 1.0
    return 0.7 + 0.6 * (w / (w+l))

# Load matches
matches = []
for line in open(MATCHES):
    matches.append(json.loads(line))
matches.sort(key=lambda m: m.get('start_utc', m.get('end_utc', '')))

# Train on 80%, test on 20%
split = int(len(matches) * 0.8)
train, test = matches[:split], matches[split:]

# Compute recency baseline
train_dates = []
for m in train:
    try: train_dates.append(datetime.fromisoformat(m.get('start_utc',m.get('end_utc','')).replace('Z','+00:00')))
    except: pass
latest = max(train_dates) if train_dates else datetime.now(timezone.utc)

# Load settled picks for backtest
def load_settled(filepath):
    df = pd.read_excel(filepath, sheet_name='Picks')
    cs2 = df[(df['league'].str.upper()=='CS2')&(df['settled_at_utc'].notna())]
    picks = []
    for _, row in cs2.iterrows():
        away = find_team(row.get('away_team', row.get('canonical_away_team_name','')))
        home = find_team(row.get('home_team', row.get('canonical_home_team_name','')))
        if not away or not home:
            continue
        sel = str(row.get('selection','')).lower()
        picks.append({
            'away': away, 'home': home,
            'side': 'HOME' if 'home' in sel else 'AWAY',
            'actual': str(row.get('result','?')),
            'pnl': row.get('pnl_units',0),
            'units': row.get('units',1.5),
            'implied': row.get('market_implied_probability',0.5),
        })
    return picks

flat_picks = load_settled('data/flat_picks.xlsx')
main_picks = load_settled('data/picks.xlsx')
print(f'Flat settled: {len(flat_picks)}, Main settled: {len(main_picks)}')

# Grid search
configs = []
for K in [32, 40, 48, 56, 64, 80, 96]:
    for use_recency in [False, True]:
        for use_score in [False, True]:
            configs.append((K, use_recency, use_score))

results = []
for K, use_recency, use_score in configs:
    # Train model
    ratings = defaultdict(lambda: 1500.0)
    for m in train:
        t1, t2 = m['team1_id'], m['team2_id']
        r1, r2 = ratings[t1], ratings[t2]
        p1 = elo_prob(r1, r2)
        outcome = 1.0 if m['team1_score'] > m['team2_score'] else 0.0
        k_eff = float(K)
        if use_score:
            k_eff *= score_mult(m['team1_score'], m['team2_score'], m.get('best_of',3))
        if use_recency:
            try:
                md = datetime.fromisoformat(m.get('start_utc',m.get('end_utc','')).replace('Z','+00:00'))
                days = (latest - md).days
                k_eff *= 1.0 + max(0, 0.3 * (1.0 - days/180.0))
            except: pass
        ratings[t1] += k_eff * (outcome - p1)
        ratings[t2] += k_eff * ((1-outcome) - (1-p1))
    
    # Evaluate on holdout
    preds = []
    for m in test:
        t1, t2 = m['team1_id'], m['team2_id']
        r1 = ratings.get(t1, 1500.0)
        r2 = ratings.get(t2, 1500.0)
        p1 = elo_prob(r1, r2)
        outcome = 1.0 if m['team1_score'] > m['team2_score'] else 0.0
        preds.append((p1, outcome))
    
    # Find best threshold on holdout
    best_brier, best_thresh = 1.0, 0.0
    holdout_metrics = {}
    for th in [0.0, 0.03, 0.05, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20]:
        sel = [(p,o) for p,o in preds if abs(p-0.5) >= th]
        if len(sel) < 100: continue
        brier = sum((p-o)**2 for p,o in sel)/len(sel)
        holdout_metrics[th] = {'brier': brier, 'called': len(sel), 'rate': len(sel)/len(preds)}
        if brier < best_brier:
            best_brier, best_thresh = brier, th
    
    # Backtest on settled flat picks (call ALL for research)
    flat_all = {'pnl': 0, 'wins': 0, 'losses': 0, 'n': 0}
    for pick in flat_picks:
        ra = ratings.get(pick['away'], 1500.0)
        rh = ratings.get(pick['home'], 1500.0)
        p_home = elo_prob(rh, ra)
        prob = p_home if pick['side'] == 'HOME' else (1-p_home)
        flat_all['n'] += 1
        flat_all['pnl'] += pick['pnl']
        if pick['pnl'] > 0: flat_all['wins'] += 1
        elif pick['pnl'] < 0: flat_all['losses'] += 1
    
    # Backtest on settled main picks (only CALL if passes threshold)
    main_metrics = {}
    for th in [0.0, 0.03, 0.05, 0.08, 0.10, 0.12, 0.15, 0.18, 0.20]:
        called_pnl, wins, losses, n = 0, 0, 0, 0
        for pick in main_picks:
            ra = ratings.get(pick['away'], 1500.0)
            rh = ratings.get(pick['home'], 1500.0)
            p_home = elo_prob(rh, ra)
            prob = p_home if pick['side'] == 'HOME' else (1-p_home)
            if abs(prob - 0.5) >= th:
                n += 1
                called_pnl += pick['pnl']
                if pick['pnl'] > 0: wins += 1
                elif pick['pnl'] < 0: losses += 1
        if n > 0:
            main_metrics[th] = {'pnl': called_pnl, 'wins': wins, 'losses': losses, 'n': n, 'wr': wins/(wins+losses) if wins+losses>0 else 0}
    
    results.append({
        'K': K, 'recency': use_recency, 'score': use_score,
        'best_thresh': best_thresh, 'best_brier': round(best_brier, 6),
        'flat_all': flat_all,
        'main_by_thresh': main_metrics,
    })

# Show top configs by holdout Brier
print(f'\n{"="*90}')
print(f'  TOP CONFIGS BY HOLDOUT BRIER')
print(f'{"="*90}')
results.sort(key=lambda r: r['best_brier'])
for r in results[:10]:
    print(f'  K={r["K"]:<4} recency={str(r["recency"]):<6} score={str(r["score"]):<6} '
          f'best_th={r["best_thresh"]:.2f} brier={r["best_brier"]:.6f} '
          f'flat_pnl={r["flat_all"]["pnl"]:+.2f} flat_wr={r["flat_all"]["wins"]}/{r["flat_all"]["losses"]}')
    # Show main picks at best threshold
    bt = r['best_thresh']
    if bt in r['main_by_thresh']:
        m = r['main_by_thresh'][bt]
        print(f'    MAIN@{bt:.2f}: pnl={m["pnl"]:+.2f} wr={m["wins"]}/{m["losses"]} called={m["n"]}/{len(main_picks)}')

# Now find best config for MAIN profitability specifically
print(f'\n{"="*90}')
print(f'  BEST CONFIGS FOR MAIN PICKS PROFITABILITY')
print(f'{"="*90}')
main_best = []
for r in results:
    for th, m in r['main_by_thresh'].items():
        if m['n'] >= 2:
            main_best.append((m['pnl'], th, r['K'], r['recency'], r['score'], m['n'], m['wins'], m['losses']))
main_best.sort(reverse=True)
for pnl, th, K, rec, sc, n, w, l in main_best[:10]:
    print(f'  P&L={pnl:+.2f} th={th:.2f} K={K} recency={rec} score={sc} called={n}/{len(main_picks)} W-L={w}-{l}')

# Best config for FLAT (call all, highest flat P&L)
print(f'\n{"="*90}')
print(f'  BEST CONFIGS FOR FLAT PICKS (CALL ALL)')
print(f'{"="*90}')
results.sort(key=lambda r: r['flat_all']['pnl'], reverse=True)
for r in results[:5]:
    f = r['flat_all']
    print(f'  P&L={f["pnl"]:+.2f} W-L={f["wins"]}-{f["losses"]} ({f["wins"]/(f["wins"]+f["losses"]):.1%}) '
          f'K={r["K"]} recency={r["recency"]} score={r["score"]} brier={r["best_brier"]}')
