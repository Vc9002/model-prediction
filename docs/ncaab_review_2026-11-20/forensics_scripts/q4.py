import sqlite3

con = sqlite3.connect("file:E:/model-prediction-runtime/ledgers/ledgers.db?mode=ro", uri=True)
q = """select created_at_utc, json_extract(feature_payload_json,'$.features.margin_engine') eng, count(*) from ledger_records
where sport='ncaab' and model_id='ncaab-spread-v6' and created_at_utc between '2026-11-15' and '2026-11-17' group by 1,2 order by 1"""
for r in con.execute(q):
    print(r)
q = """select substr(created_at_utc,1,13), json_extract(feature_payload_json,'$.features.effective_hca') h, json_extract(feature_payload_json,'$.features.base_margin_scale') b, json_extract(feature_payload_json,'$.features.elo_weight_effective') w, count(*) from ledger_records
where sport='ncaab' and model_id='ncaab-spread-v6' and created_at_utc between '2026-11-11T12' and '2026-11-12T23' and json_extract(feature_payload_json,'$.features.neutral_site') in ('False',0,'false') group by 1,2,3,4 order by 1"""
for r in con.execute(q):
    print(r)
q = """select substr(created_at_utc,1,13), json_extract(feature_payload_json,'$.features.effective_total_sd') s, json_extract(feature_payload_json,'$.features.credibility_factor') c, count(*) from ledger_records
where sport='ncaab' and model_id='ncaab-total-v6' and created_at_utc between '2026-11-11T12' and '2026-11-12T23' group by 1 order by 1"""
for r in con.execute(q):
    print(r)
