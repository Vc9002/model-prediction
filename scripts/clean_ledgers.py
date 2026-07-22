#!/usr/bin/env python3
"""Clean up both ledgers: flat = research only, main = qualified only."""
import pandas as pd
from pathlib import Path

PROJECT = Path("/Users/vincentc9002/Documents/Poly & Kalshi/model prediction")

def clean_flat():
    """Flat ledger: ALL picks become research_observation. Call everything."""
    f = PROJECT / "data/flat_picks.xlsx"
    xls = pd.ExcelFile(f)
    sheets = {s: pd.read_excel(f, sheet_name=s) for s in xls.sheet_names}
    picks = sheets['Picks']
    
    before = len(picks)
    changed = (picks['call_type'] != 'research_observation').sum()
    picks['call_type'] = 'research_observation'
    picks['model_origin'] = 'research_observation'
    
    sheets['Picks'] = picks
    with pd.ExcelWriter(f, engine='openpyxl') as w:
        for sn, sdf in sheets.items():
            sdf.to_excel(w, sheet_name=sn, index=False)
    
    print(f'FLAT: {before} picks, {changed} changed to research_observation')

def clean_main():
    """Main ledger: ONLY model_qualified with edge >= 0.02. Remove everything else."""
    f = PROJECT / "data/picks.xlsx"
    xls = pd.ExcelFile(f)
    sheets = {s: pd.read_excel(f, sheet_name=s) for s in xls.sheet_names}
    picks = sheets['Picks']
    
    before = len(picks)
    
    # Determine which to keep
    keep = pd.Series(False, index=picks.index)
    for idx in picks.index:
        ct = str(picks.at[idx, 'call_type']) if pd.notna(picks.at[idx, 'call_type']) else ''
        edge = picks.at[idx, 'edge'] if pd.notna(picks.at[idx, 'edge']) else 0
        # Keep if model_qualified or QUALIFIED_SHADOW_CALL, AND edge >= 0.02
        if ct in ('model_qualified', 'QUALIFIED_SHADOW_CALL') and edge >= 0.02:
            keep.at[idx] = True
    
    removed = picks[~keep]
    kept = picks[keep]
    
    print(f'MAIN: {before} → {len(kept)} picks (removed {len(removed)})')
    
    # Show what was removed
    for _, r in removed.iterrows():
        print(f'  REMOVED: {r["league"]:8s} {str(r.get("away_team",""))[:20]:20s} @ {str(r.get("home_team",""))[:20]:20s}  call={r.get("call_type","?")} edge={r.get("edge",0):+.3f}')
    
    # Show what was kept
    print(f'\n  KEPT:')
    for _, r in kept.iterrows():
        print(f'  {r["league"]:8s} {str(r.get("away_team",""))[:20]:20s} @ {str(r.get("home_team",""))[:20]:20s}  edge={r.get("edge",0):+.3f} pnl={r.get("pnl_units",0):+.2f}')
    
    sheets['Picks'] = kept.reset_index(drop=True)
    with pd.ExcelWriter(f, engine='openpyxl') as w:
        for sn, sdf in sheets.items():
            sdf.to_excel(w, sheet_name=sn, index=False)
    
    # Update Summary sheet counts
    if 'Summary' in sheets:
        summary = sheets['Summary']
        # Update total/settled counts if possible
        print(f'\n  Summary sheet preserved (manual update recommended)')

clean_flat()
print()
clean_main()
print('\nDone.')
