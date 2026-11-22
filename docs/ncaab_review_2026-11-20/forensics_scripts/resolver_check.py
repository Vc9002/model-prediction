import glob
import json
import sys

sys.path.insert(0, "E:/MP/src")
from model_prediction.college_basketball_forward import _normalize_name, _resolve_ncaab_team
from model_prediction.features.base import FeatureStore

hist = FeatureStore("E:/MP/data").games_before("ncaab", "2026-11-21")
names = {n for g in hist for n in (g.home_team, g.away_team)}
norm = {_normalize_name(n): n for n in names}
teams = {}
for p in glob.glob("E:/MP/data/raw/ncaab/2026-11-*/scores_ncaab.json"):
    for e in json.load(open(p, encoding="utf-8")).get("events", []):
        for c in e["competitions"][0]["competitors"]:
            t = c["team"]
            teams[t["displayName"]] = (
                t.get("location", ""),
                t.get("name", ""),
                t.get("shortDisplayName", ""),
                t.get("abbreviation", ""),
            )
bad_school = []
bad_both = []
bad_short = []
d1 = json.load(open("E:/MP/data/entities/ncaab_conferences.json", encoding="utf-8"))
for disp, (loc, mascot, short, abbr) in sorted(teams.items()):
    if disp not in d1:
        continue
    r = _resolve_ncaab_team(loc, "", names, norm)
    if r != disp:
        bad_school.append((loc, r, disp))
    r2 = _resolve_ncaab_team(loc, mascot, names, norm)
    if r2 != disp:
        bad_both.append((loc, mascot, r2, disp))
    r3 = _resolve_ncaab_team(short, "", names, norm)
    if r3 != disp:
        bad_short.append((short, r3, disp))
print("D1 teams seen", sum(1 for d in teams if d in d1))
print("school-only mismatches", len(bad_school))
for b in bad_school:
    print("  ", b)
print("school+mascot mismatches", len(bad_both))
for b in bad_both:
    print("  ", b)
print("shortDisplayName mismatches", len(bad_short))
for b in bad_short[:40]:
    print("  ", b)
