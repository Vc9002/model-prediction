"""Survey the NCAAF Polymarket snapshot archives: slug shapes, market types, descriptions, states."""
import collections
import glob
import json
import re

root = "E:/model-prediction-runtime/odds/ncaaf"
types = collections.Counter()
states = collections.Counter()
prefix = collections.Counter()
tot_desc = collections.Counter()
sp_desc_eq_line = collections.Counter()
ml_desc = collections.Counter()
slug_tokens = collections.Counter()
usage = collections.Counter()
recon = collections.Counter()
keys = collections.Counter()
n = 0
for path in sorted(glob.glob(f"{root}/*/polymarket_snapshots.jsonl")):
    for line in open(path, encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        n += 1
        keys[tuple(sorted(r.keys()))] += 1
        mt = r.get("market_type")
        types[mt] += 1
        states[r.get("market_state")] += 1
        usage[(r.get("usage"), r.get("reconstructed"), r.get("timestamp_valid"))] += 1
        slug = str(r.get("market_slug") or "")
        prefix[(mt, slug.split("-")[0])] += 1
        m = re.search(r"-(1h|2h|1q|2q|3q|4q|tt|tt1h|tt2h|alt|team)-", slug)
        if m:
            slug_tokens[(mt, m.group(1))] += 1
        lo = r.get("long") or {}
        so = r.get("short") or {}
        if mt in ("total", "over_under"):
            tot_desc[(str(lo.get("description"))[:20], str(so.get("description"))[:20])] += 1
        if mt == "spread":
            try:
                eq = float(str(lo.get("description")).replace("+", "")) == float(r.get("line"))
            except Exception:
                eq = f"unparsable:{lo.get('description')!r}"
            sp_desc_eq_line[eq] += 1
print("rows", n)
print("types", types)
print("states", states)
print("usage", usage)
print("prefix", prefix.most_common(40))
print("partial tokens", slug_tokens)
print("total desc", tot_desc.most_common(20))
print("spread long desc == line", sp_desc_eq_line.most_common(10))
print("keysets", len(keys))
for k, v in keys.most_common(5):
    print(v, k)
