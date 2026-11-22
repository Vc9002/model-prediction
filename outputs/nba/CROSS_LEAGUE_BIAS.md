# Main-line side biases: develop on WNBA/NCAAB, score NBA once

```
{
 "wnba": {
  "mapping": {
   "rationale_slug": 134,
   "unmapped": 7,
   "date_team_match": 1
  },
  "sanity": {
   "spread_home_cover": [
    0.617,
    128
   ],
   "ml_fav_60_80": [
    0.679,
    56
   ]
  },
  "sanity_pass": false
 },
 "ncaab": {
  "mapping": {
   "date_team_match": 504,
   "unmapped": 321,
   "ambiguous": 10
  },
  "sanity": {
   "spread_home_cover": [
    0.478,
    446
   ],
   "ml_fav_60_80": [
    0.611,
    157
   ]
  },
  "sanity_pass": true
 }
}
```

## Development (leagues passing sanity)

| rule | n | W-L | gross | net | ROI net | 95% CI |
|---|---|---|---|---|---|---|
| spread_home | 446 | 213-233 | -25.39 | -37.49 | -8.4% | -17.5% .. +0.5% |
| spread_away | 446 | 233-213 | +11.75 | -1.35 | -0.3% | -9.2% .. +8.8% |
| spread_fav | 446 | 207-239 | -36.13 | -47.95 | -10.8% | -19.4% .. -1.9% |
| spread_dog | 446 | 239-207 | +22.49 | +9.12 | +2.0% | -6.6% .. +10.7% |
| total_over | 429 | 204-225 | -26.79 | -38.34 | -8.9% | -17.7% .. +0.1% |
| total_under | 429 | 225-204 | +15.31 | +2.53 | +0.6% | -8.5% .. +9.4% |
| ml_home | 347 | 215-132 | +2.21 | -5.60 | -1.6% | -12.0% .. +9.3% |
| ml_away | 347 | 132-215 | +17.32 | +3.94 | +1.1% | -16.2% .. +19.4% |
| ml_fav | 347 | 228-119 | -27.65 | -33.02 | -9.5% | -16.7% .. -2.5% |
| ml_dog | 347 | 119-228 | +47.18 | +31.35 | +9.0% | -10.6% .. +29.1% |

Frozen rules: none

