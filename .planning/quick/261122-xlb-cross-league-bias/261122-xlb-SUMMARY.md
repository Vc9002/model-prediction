---
quick_id: 261122-xlb
status: complete
date: 2026-11-22
---

# Summary: main-line side biases developed on WNBA/NCAAB (ralph round 9)

`scripts/nba_v10/cross_league_bias.py`, report `outputs/nba/CROSS_LEAGUE_BIAS.md`; `settled_common`
generalised to any sport (`load_sport`, sport-aware harness). WNBA failed the orientation sanity
check (main spread home cover 61.7%, n=128) and was excluded; NCAAB passed (home cover 47.8%, ML
60-80c favourites 61.1%). No rule's development CI cleared 0, so nothing was frozen and NBA was not
scored. NCAAB favourites lost significantly (spread -10.8%, CI -19.4..-1.9; ML -9.5%, CI
-16.7..-2.5); underdogs positive but not significant (spread +2.0%, ML +9.0%).
