#!/usr/bin/env python3
"""Backtest CS2 v3 model on settled picks from both ledgers."""
import json, math, pandas as pd
from pathlib import Path

v3 = json.load(open('config/models/cs2-tiered-elo-v3.json'))
ratings = v3['ratings']
threshold = v3['confidence_threshold']

teams_data = json.load(open('data/esports/cs2/teams.json'))
name_to_id = {}
for tid, tdata in teams_data.items():
    name = tdata.get('name', '').lower().strip()
    name_to_id[name] = tid
    name_to_id[name.replace(' ', '')] = tid

def elo_prob(ra, rb):
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))

# Manual overrides for team name mismatches
NAME_OVERRIDES = {
    'nip': 'bo3:1:785', 'm80': 'bo3:1:7430',
    'cybershoke esports': 'bo3:1:7429', 'cybershoke': 'bo3:1:7429',
    'bet-m 33': 'bo3:1:22191', 'bet-m': 'bo3:1:22191',
    'astralis': 'bo3:1:794',
    'team nemesis': 'bo3:1:21388', 'nemesis': 'bo3:1:21388',
    'misa esports': 'bo3:1:20952', 'misa': 'bo3:1:20952',
    'honved': 'bo3:1:1279', 'honvéd': 'bo3:1:1279',
    'alka gaming': 'bo3:1:23043', 'alka': 'bo3:1:23043',
    'procyon gaming': 'bo3:1:898', 'procyon': 'bo3:1:898',
    'entropy': 'bo3:1:20939',
    'esport academy copenhagen': 'bo3:1:21816',
    'gentle mates': 'bo3:1:21556', '3dmax': 'bo3:1:9960',
    'heroic': 'bo3:1:10190', 'hotu': 'bo3:1:21953',
    'quazar': 'bo3:1:21583', 'enjoy': 'bo3:1:21547',
    'just players': 'bo3:1:21901', 'ex-rustec': 'bo3:1:21741',
    'wildcard': 'bo3:1:22219', 'alliance': 'bo3:1:21776',
    'bestia academy': 'bo3:1:22135', 'borracheiros': 'bo3:1:21983',
}

def find_team(query):
    q = str(query).lower().strip()
    if q in NAME_OVERRIDES: return NAME_OVERRIDES[q]
    if q in name_to_id: return name_to_id[q]
    if q.replace(' ', '') in name_to_id: return name_to_id[q.replace(' ', '')]
    matches = [tid for name, tid in name_to_id.items() if q in name or name in q]
    return matches[0] if len(matches) == 1 else None

def backtest(filepath, label):
    df = pd.read_excel(filepath, sheet_name='Picks')
    if 'league' not in df.columns:
        print(f'{label}: no league column')
        return
    
    cs2 = df[(df['league'].str.upper() == 'CS2') & (df['settled_at_utc'].notna())].copy()
    if len(cs2) == 0:
        print(f'{label}: no settled CS2 picks')
        return
    
    results = []
    for _, row in cs2.iterrows():
        away = find_team(row.get('away_team', row.get('canonical_away_team_name', '')))
        home = find_team(row.get('home_team', row.get('canonical_home_team_name', '')))
        
        if not away or not home:
            results.append({
                'match': f'{row["away_team"]} @ {row["home_team"]}',
                'side': '?', 'prob': None, 'edge': None,
                'actual': str(row.get('result', '?')),
                'called': 'unmatched', 'old_pnl': row.get('pnl_units', 0),
                'units': row.get('units', 1.5),
            })
            continue
        
        ra = ratings.get(away, 1500.0)
        rh = ratings.get(home, 1500.0)
        p_home = elo_prob(rh, ra)
        p_away = 1.0 - p_home
        
        selection = str(row.get('selection', '')).lower()
        side = 'HOME' if 'home' in selection else 'AWAY'
        prob = p_home if side == 'HOME' else p_away
        
        implied = row.get('market_implied_probability', 0.5)
        edge = prob - implied if implied else 0
        called = abs(prob - 0.5) >= threshold
        
        results.append({
            'match': f'{row["away_team"]} @ {row["home_team"]}',
            'side': side, 'prob': round(prob, 4),
            'implied': implied, 'edge': round(edge, 4),
            'actual': str(row.get('result', '?')),
            'called': called, 'old_pnl': row.get('pnl_units', 0),
            'units': row.get('units', 1.5),
        })
    
    total_old = sum(r['old_pnl'] for r in results)
    called = [r for r in results if r['called'] == True]
    skipped = [r for r in results if r['called'] == False]
    unmatched = [r for r in results if r['called'] == 'unmatched']
    
    skipped_losses = sum(r['old_pnl'] for r in skipped if r['old_pnl'] < 0)
    skipped_gains = sum(r['old_pnl'] for r in skipped if r['old_pnl'] > 0)
    called_pnl = sum(r['old_pnl'] for r in called)
    
    n_bad_skipped = len([r for r in skipped if r['old_pnl'] < 0])
    n_good_skipped = len([r for r in skipped if r['old_pnl'] > 0])
    
    print(f'\n{"="*70}')
    print(f'  CS2 v3 BACKTEST — {label}')
    print(f'{"="*70}')
    print(f'  Settled picks:     {len(results)}')
    print(f'  v3 Called:         {len(called)} (threshold={threshold})')
    print(f'  v3 Skipped:        {len(skipped)}')
    print(f'  Unmatched teams:   {len(unmatched)}')
    print()
    
    print(f'  {"Match":<45} {"Sd":>3} {"v3 Prob":>8} {"Edge":>8} {"Call":>6} {"Result":>8} {"Old P&L":>8}')
    print(f'  {"-"*92}')
    
    for r in results:
        tag = 'YES' if r['called'] == True else ('no' if r['called'] == False else '--')
        prob_str = f'{r["prob"]:.4f}' if r['prob'] is not None else 'N/A'
        edge_str = f'{r["edge"]:+.4f}' if r['edge'] is not None else 'N/A'
        print(f'  {r["match"]:<45} {r["side"]:>3} {prob_str:>8} {edge_str:>8} {tag:>6} {r["actual"]:>8} {r["old_pnl"]:>+8.2f}')
    
    print(f'\n  {"-"*70}')
    print(f'  OLD (v1/v2):       {total_old:+.2f} units on {len(results)} picks (all called)')
    print(f'  v3 CALLED ONLY:    {called_pnl:+.2f} units on {len(called)} picks')
    print(f'  v3 avoided losses: {skipped_losses:+.2f} units ({n_bad_skipped} bad picks skipped)')
    print(f'  v3 missed gains:   {skipped_gains:+.2f} units ({n_good_skipped} good picks skipped)')
    
    if len(called) > 0:
        v3_wins = sum(1 for r in called if 'win' in str(r['actual']).lower() or r['old_pnl'] > 0)
        print(f'  v3 win rate:       {v3_wins}/{len(called)} ({v3_wins/len(called):.1%})')
    
    if unmatched:
        print(f'\n  Unmatched teams:')
        for r in unmatched:
            print(f'    {r["match"]}')

backtest('data/flat_picks.xlsx', 'FLAT PICKS')
backtest('data/picks.xlsx', 'MAIN PICKS')
