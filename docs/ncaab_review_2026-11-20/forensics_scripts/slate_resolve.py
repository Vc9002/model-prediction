import json
import sys
from datetime import date

sys.path.insert(0, "E:/MP/src")
from model_prediction.college_basketball_forward import _normalize_name, _resolve_ncaab_team
from model_prediction.data_sources.polymarket_us import PolymarketUSClient
from model_prediction.features.base import FeatureStore

day = sys.argv[1]
h = FeatureStore("E:/MP/data").games_before("ncaab", day)
names = {n for g in h for n in (g.home_team, g.away_team)}
norm = {_normalize_name(n): n for n in names}
d1 = json.load(open("E:/MP/data/entities/ncaab_conferences.json", encoding="utf-8"))
events = PolymarketUSClient().slate("NCAAB", date.fromisoformat(day))
print("events", len(events))
nd = 0
for event in events:
    away_school = home_school = None
    am = hm = ""
    src = None
    for m in event.get("markets", []):
        sides = m.get("sides", [])
        a = next((s for s in sides if s.get("selection") == "away"), None)
        hh = next((s for s in sides if s.get("selection") == "home"), None)
        if a and hh:
            if a.get("team") and hh.get("team"):
                away_school = str(a["team"])
                home_school = str(hh["team"])
                da = str(a.get("description", ""))
                dh = str(hh.get("description", ""))
                if da and not any(c in da for c in "+-0123456789"):
                    am = da
                if dh and not any(c in dh for c in "+-0123456789"):
                    hm = dh
                src = (m.get("market_type"), da, dh)
                break
            if a.get("description") and hh.get("description"):
                da = str(a["description"])
                dh = str(hh["description"])
                if not any(c in da for c in "+-0123456789") and not any(c in dh for c in "+-0123456789"):
                    away_school = da
                    home_school = dh
                    src = (m.get("market_type"), "desc-as-school")
                    break
    if not away_school:
        src = "title"
    if not away_school:
        continue
    ra = _resolve_ncaab_team(away_school, am, names, norm)
    rh = _resolve_ncaab_team(home_school, hm, names, norm)
    if ra not in d1 or rh not in d1:
        nd += 1
        print(
            event.get("title"),
            "|",
            away_school,
            repr(am),
            "->",
            ra,
            ra in d1,
            "|",
            home_school,
            repr(hm),
            "->",
            rh,
            rh in d1,
            "| src",
            src,
        )
print("non-D1 declines", nd)
