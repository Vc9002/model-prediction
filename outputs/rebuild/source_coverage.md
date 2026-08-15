# Source Coverage Report — Rebuild Platform

**Generated**: 2026-08-05

## Registered Sources (14 total)

| Source | Tier | Key Required | Sports Covered | Status |
|---|---|---|---|---|
| polymarket_us | official_public | No | 13 sports | Active — market prices + order books |
| espn_public | official_public | No | MLB, NBA, WNBA, NFL, soccer, tennis | Active — scoreboards + schedules |
| pybaseball | keyless_api | No | MLB | Active — Statcast, Savant, FanGraphs |
| open_meteo | keyless_api | No | MLB, NFL, KBO, NPB | Active — archived weather forecasts |
| sportsdataverse | versioned_release | No | NBA, WNBA | Pending — release data not yet downloaded |
| nflverse | versioned_release | No | NFL | Pending — nflreadpy integration |
| statsbomb | versioned_release | No | Soccer | Pending — open data not yet integrated |
| sackmann | cached_repository | No | Tennis | Pending — CSV archive |
| valve_vrs | official_public | No | CS2 | Pending — Regional Standings API |
| opendota | keyless_api | No | Dota2 | Pending — keyless tier |
| bo3 | keyless_api | No | Esports (5 titles) | Active — BO3.gg API |
| odds_api | paid_or_keyed | Yes | MLB, soccer | Pending — requires API key |
| kbo_scraper | throttled_scraper | No | KBO | Active — koreabaseball.com |
| npb_scraper | throttled_scraper | No | NPB | Active — npb.jp |

## Source-Sport Coverage Matrix (51 mappings)

| Sport | Market Data | Scoreboard | Deep Stats | Weather | Historical |
|---|---|---|---|---|---|
| MLB | polymarket_us | espn_public | pybaseball | open_meteo | sackmann(n/a) |
| NBA | polymarket_us | espn_public | sportsdataverse† | — | sportsdataverse† |
| WNBA | polymarket_us | espn_public | sportsdataverse† | — | — |
| NFL | polymarket_us | espn_public | nflverse† | open_meteo | nflverse† |
| Soccer | polymarket_us | espn_public | statsbomb† | — | — |
| Tennis | polymarket_us | espn_public | sackmann† | — | sackmann† |
| Esports | polymarket_us | bo3 | valve_vrs†/opendota† | — | bo3 |
| KBO | polymarket_us | kbo_scraper | — | open_meteo | kbo_scraper |
| NPB | polymarket_us | npb_scraper | — | open_meteo | npb_scraper |

† = source registered but data not yet downloaded/integrated

## Coverage Gap Summary

- **MLB**: Full coverage — all sources actively collecting or ready to collect
- **NBA/WNBA**: SportsDataverse release data needs download
- **NFL**: nflverse data needs download (offseason — no urgency)
- **Soccer**: StatsBomb open data needs integration
- **Tennis**: Sackmann CSV needs download + parsing
- **Esports**: Valve VRS and OpenDota need integration for roster-level data
- **KBO/NPB**: Reliable player-level data sources still being investigated

## Medallion Storage Status

| Layer | Contents |
|---|---|
| `data/rebuild/raw/` | 1 snapshot (ESPN MLB 2026-08-05) |
| `data/rebuild/normalized/` | 15 MLB scoreboard rows |
| `data/rebuild/features/` | Empty — pending feature computation |
| `data/rebuild/markets/` | Empty — pending Polymarket BBO capture |
| `data/rebuild/metadata.db` | 14 sources, 51 mappings, 1 benchmark model |
