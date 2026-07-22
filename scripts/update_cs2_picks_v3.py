#!/usr/bin/env python3
"""Rerun all CS2 picks (flat + main) with v3 model. Update model predictions in-place."""
import json, math, pandas as pd
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

PROJECT = Path("/Users/vincentc9002/Documents/Poly & Kalshi/model prediction")
V3 = json.load(open(PROJECT / "config/models/cs2-tiered-elo-v3.json"))
RATINGS = V3["ratings"]
MAIN_THRESHOLD = V3["confidence_threshold"]  # 0.20
FLAT_THRESHOLD = V3.get("flat_confidence_threshold", 0.0)  # 0.0

# Team name mapping
teams_data = json.load(open(PROJECT / "data/esports/cs2/teams.json"))
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
    return None

def elo_prob(ra, rb):
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))

def update_picks(filepath, threshold, is_flat=False):
    """Update all CS2 picks in an Excel ledger with v3 model predictions."""
    xls = pd.ExcelFile(filepath)
    sheets = {}
    for sheet in xls.sheet_names:
        sheets[sheet] = pd.read_excel(filepath, sheet_name=sheet)
    
    picks_df = sheets['Picks'].copy()
    cs2_mask = picks_df['league'].str.upper() == 'CS2'
    cs2 = picks_df[cs2_mask]
    
    if len(cs2) == 0:
        print(f'  No CS2 picks in {filepath}')
        return
    
    updated = 0
    for idx in cs2.index:
        row = picks_df.loc[idx]
        away = find_team(row.get('away_team', row.get('canonical_away_team_name', '')))
        home = find_team(row.get('home_team', row.get('canonical_home_team_name', '')))
        if not away or not home:
            continue
        
        ra = RATINGS.get(away, 1500.0)
        rh = RATINGS.get(home, 1500.0)
        p_home = elo_prob(rh, ra)
        
        sel = str(row.get('selection', '')).lower()
        prob = p_home if 'home' in sel else (1 - p_home)
        implied = row.get('market_implied_probability', 0.5) or 0.5
        edge = prob - implied
        called = abs(prob - 0.5) >= threshold
        
        # Update model fields
        picks_df.at[idx, 'model_probability'] = round(prob, 6)
        picks_df.at[idx, 'edge'] = round(edge, 6)
        picks_df.at[idx, 'model_version'] = 'cs2-tiered-elo-v3'
        picks_df.at[idx, 'model_artifact_hash'] = V3['artifact_hash']
        
        # Update call type based on threshold
        if called:
            # For main picks: only pass if edge also sufficient
            if not is_flat and edge < 0.02:
                picks_df.at[idx, 'call_type'] = 'NO_CALL_LOW_EDGE'
            else:
                picks_df.at[idx, 'call_type'] = 'model_qualified' if not is_flat else 'research_observation'
        else:
            picks_df.at[idx, 'call_type'] = 'NO_CALL_LOW_EDGE' if not is_flat else 'research_observation'
        
        # Always update model_origin
        picks_df.at[idx, 'model_origin'] = 'statistical_model' if not is_flat else 'research_observation'
        
        updated += 1
    
    sheets['Picks'] = picks_df
    
    # Write back
    with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
        for sheet_name, df in sheets.items():
            df.to_excel(writer, sheet_name=sheet_name, index=False)
    
    print(f'  Updated {updated}/{len(cs2)} CS2 picks in {filepath}')

# Update FLAT picks (threshold=0, call all for research)
print("=== FLAT PICKS (research, call all) ===")
update_picks(PROJECT / "data/flat_picks.xlsx", FLAT_THRESHOLD, is_flat=True)

# Update MAIN picks (threshold=0.20, only high-conviction)
print("\n=== MAIN PICKS (qualified, th=0.20) ===")
update_picks(PROJECT / "data/picks.xlsx", MAIN_THRESHOLD, is_flat=False)

print("\nDone. Both ledgers updated with cs2-tiered-elo-v3 predictions.")
