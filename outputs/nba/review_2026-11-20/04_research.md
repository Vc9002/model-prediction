# NBA model overhaul: external research (2026-11-20)

Confidence tags: **[R]** read the source text myself; **[S]** abstract, metadata or search-snippet only;
**[C]** my own computation/derivation from cited numbers; **[U]** unverified (memory, or source unreachable).
All prices are implied probabilities. PIT = point-in-time recoverable (an as-of snapshot exists).

---

## 1. State of the art: player-level projection

### 1a. Player rating systems

| Claim | Source | Conf. |
|---|---|---|
| EPM = RAPM with a statistical-plus-minus Bayesian prior; box-score SPM fit on 2003-04..2022-23 (4.9M possessions), tracking-based defense model 2017-18..2022-23. Inputs are decay-weighted per-stat "Estimated Skills" | https://dunksandthrees.com/about/epm | [R] |
| EPM game model, live 2024-25..2025-26 (2,643 games): mean abs miss on margin 11.0 vs 11.4 for an opponent-adjusted net-rating baseline; winners 68.4% vs 67.5%. Backtest 2005-06..2023-24 (24,425 games): 9.6 vs 9.9, winners 67.8% vs 66.3%. **No comparison vs betting lines published.** | same | [R] |
| EPM minutes projection beats season-to-date average by 14.5% (abs miss); game predictions refresh within 30 min of tip as minutes update; daily history back to 2001-02 via **paid API subscription** | same | [R] |
| DARKO = "Daily Adjusted and Regularized..." Bayesian + ML game-by-game player projection; a Wharton paper calls it "the most predictive all-in-one skill metric" and uses preseason DARKO values | https://wsb.wharton.upenn.edu/wp-content/uploads/2023/12/Brill_2023_Q.pdf | [S] |
| DARKO shiny app retired June 2026; now at darko.app with DPM (offense/defense, box vs on/off split), CSV download, "Rewind" time machine to 1996-97. Terms/licence not stated on the page. Whether Rewind gives true daily as-of snapshots: not verified | https://apanalytics.shinyapps.io/DARKO/ , https://www.darko.app/ | [R] |
| DARKO-style method (padding, Bayes/Kalman filters per stat, "now-cast" of current true talent) is described for Stats Perform DRIP, which credits Medvedovsky | https://theanalyst.com/articles/nba-drip-daily-updated-rating-of-individual-performance/ , https://medium.com/draftkings-engineering/kalman-filters-for-nba-player-ratings-d3bb9365221b | [S] |
| LEBRON = box-score prior (boxPIPM weights) feeding a luck-adjusted RAPM fit; most public metrics (DARKO, LEBRON, EPM, BPM) are fits that predict RAPM | https://www.bball-index.com/lebron-introduction/ , https://development.craftednba.com/glossary | [S] |
| BPM 2.0: replacement level = **-2.0 per 100 poss.**; VORP = [BPM - (-2.0)] x (% of possessions played) x (team games/82); minute-weighted player BPM is forced to sum to team adjusted efficiency via a constant "team adjustment" | https://www.basketball-reference.com/about/bpm2.html | [R] |
| 538 RAPTOR model: team rating = minute-weighted average of player ratings; minutes from a depth-chart model; for 2022-23 blended in a "history-based" rolling minutes projection for games in the next two weeks because backtests showed it improved accuracy. 538 is offline (pages redirect to ABC News; nba_elo.csv URL returns a redirect); exact constants (home court, travel, altitude, rest) **could not be verified** | https://fivethirtyeight.com/features/how-were-improving-our-nba-forecast-for-2022-23 (snippet), https://github.com/fivethirtyeight/data/tree/master/nba-forecasts | [S]/[U] |

### 1b. Lineup-based team rating (the standard recipe) [C, consistent with the BPM/538/EPM sources above]
- Team net rating for a game = sum over expected-available players of (player rating per 100) x (projected minutes / 48) x 5-man normalization, plus home court, minus a rest/travel term.
- Margin projection = net-rating difference x (projected possessions / 100). Total = possessions x (offensive efficiency of both sides) / 100.
- Injury handling = re-project minutes: remove the absent player, redistribute minutes down the depth chart, and replacement minutes enter at roughly replacement level (BPM -2.0) unless a named backup has a rating. This replaces a flat-tier injury adjustment and naturally gives "star value depends on depth".

### 1c. How the market prices injuries
| Claim | Source | Conf. |
|---|---|---|
| Oddsmaker-derived spread values (spring 2026): Jokic 5.5 points; SGA, Wembanyama 4; Doncic 3.5; tier 2 (Cunningham, Edwards) 3, then 2.5; many all-stars 1.5-2; role players about 1 or less. Depth/backup quality explicitly drives the value | https://www.thescore.com/nba/news/3531319/nba-playoff-injury-woes-how-much-are-stars-worth-to-their-teams | [R] |
| Rule of thumb: top-5 player up to 4-6 points; key rotation player 0.5-2 | https://www.actionnetwork.com/education/how-injuries-affect-betting-lines-a-guide-to-market-movement | [S] |
| Sauer event study: spread reaction to key-player injuries is consistent with efficient pricing (relation of spreads to scores during injury events) | https://www.taylorfrancis.com/chapters/edit/10.4324/9780203986936-19/player-injuries-price-responses-point-spread-wagering-market-raymond-sauer | [S] |

### 1d. Public models vs closing lines: evidence is thin
- **No public, verifiable ATS or Brier-vs-close record found** for RAPTOR, DARKO, EPM, LEBRON or inpredictable. EPM publishes accuracy only against a net-rating baseline [R]. 538's CSVs that would allow computing it are no longer served [R: HTTP redirect, tested 2026-11-20].
- inpredictable seeds pregame win probability from the market spread; Brier 0.162 vs ESPN 0.166 for in-game WP (not pregame skill) — https://www.inpredictable.com/2018/01/judging-win-probability-models.html [S].
- EvanMiya (college basketball, not NBA): edge vs Vegas lines concentrated early season; "65.2% of the best bets are picked in the first 30 days", attributed to player-level preseason priors that Vegas catches up to — https://blog.evanmiya.com/i/138347906/backtesting [R, NCAAB only].

---

## 2. Market efficiency

| Claim | Source | Conf. |
|---|---|---|
| Manner (2015), NBA 2006-2014 second halves of seasons: Vegas **opening** spread MSE 137.49 / MAE 9.17 vs best model 142.21 / 9.36 (models significantly worse, outside 95% model confidence set). Equal-weight blend model+spread 137.93: essentially equal to the market, slightly better winner %. Model and spread correlation > 0.9. Playoffs: blends slightly better than spread (147.69 vs 148.96). | https://static.uni-graz.at/fileadmin/_Persoenliche_Webseite/manner_hans/Publikationen/NBA_revision2.pdf | [R] |
| Implication: model alone loses to even the opening line; a blend roughly matches it. Use the model as a **deviation from the market**, not a standalone price | [C] from above | — |
| Moskowitz (J. Finance 2021): open-to-close momentum in NBA/NFL/MLB/NHL spreads and totals, fully reversed by outcomes (closing line overreacts slightly); returns too small to beat transaction costs; author calls significance "slight" | https://ideas.repec.org/a/bla/jfinan/v76y2021i6p3153-3209.html | [S] |
| TeamRankings, 22,880 NBA games since 2008 with open and close: opening favorite covers 50.0% overall; cover rates by size of line move scatter around 50% with no clean exploitable bucket | https://betiq.teamrankings.com/nba/betting-trends/ats-results-by-line-movement/ , https://betiq.teamrankings.com/nba/betting-trends/ats-results-by-opening-line-and-line-movement/ | [R] page figures only |
| Compton and Sigler (2012), 10,325 games 2000-08: spreads efficient (underdogs 49.86%); only home dogs of 10+ points beat the break-even (59.52%), and that faded in the later sub-period; totals not profitable | https://thesportjournal.org/article/nba-gambling-inefficiencies-a-second-look/ | [R] |
| **Early-season totals bias**: Baryla et al. (Finance Research Letters 2007): totals lines significantly biased early each season, sides are not; betting against the closing total early won 56.72% | https://dc.etsu.edu/etsu-works/17780 | [S] |
| Girdner et al. (J. Prediction Markets 2013), 2009-12: totals market "generally very efficient"; unders in week 1 returned 11.1%/game (small sample) | https://ideas.repec.org/a/buc/jpredm/v7y2013i2p1-9.html (search summary) | [S] |
| Over bettors dominate ticket share and it rises with the total (Paul & Weinbach 2008) | https://kylewoodward.com/blog-data/pdfs/references/paul+weinbach-international-journal-of-sport-finance-2008A.pdf | [S] |
| **Rest/back-to-back**: Entine and Small (2007, 2004-06 data): team on 0 days' rest about 1.77 points worse than with 3+ days; road-rest disparity explains only ~0.3 of ~3.2-point home edge | https://faculty.wharton.upenn.edu/wp-content/uploads/2012/04/Nba.pdf | [R] |
| Ashman, Bowman, Lambrinos (J. Sports Econ 2010), 19 seasons: home team on 2nd night of back-to-back vs rested visitor performs poorly ATS; worse after travelling 1-2 time zones east. Old sample; practitioners now say a generic B2B fade has no value | https://econpapers.repec.org/RePEc:sae:jospec:v:11:y:2010:i:6:p:602-613 , https://www.actionnetwork.com/nba/moore-dont-be-fooled-by-blindly-fading-teams-on-back-to-backs | [S] |
| Referees: over records by crew near 50% since 2005 (Bet Labs); no rigorous evidence of a totals edge | https://www.actionnetwork.com/nba/nba-finals-referees-betting-trends-warriors-raptors-officials-2019 | [S] |
| Altitude/travel, pace mismatches: **no rigorous market-efficiency evidence found** | — | [U] |
| Injury-news latency: the NBA added a mandatory game-day report (Dec 2025) explicitly to reduce the value of private participation info; implies the edge window is between report publication and price update | https://www.thescore.com/nba/news/3432280 | [R] |

**Polymarket specifics**
| Claim | Source | Conf. |
|---|---|---|
| Polymarket NBA 2025-26 (1,019 games, student write-up): well calibrated at all horizons; Brier 0.216 five days out to 0.199 one hour before; accuracy ~66% to ~68%; logistic recalibration slope about 1 | https://laidlawscholars.network/posts/polymarket-calibration-analysis-across-4-domains | [R], non-peer-reviewed |
| Polymarket NBA books are shallow: median pre-game bid-ask spread **392 bps** (~3.9 cents of probability), in-game 1,031 bps; single-market arbitrage essentially absent (7 episodes / 3,042 markets) | https://arxiv.org/html/2605.00864v1 | [R] |
| Implication: the half-spread (~2 cents) plus fees is the cost hurdle; a model needs more than about 2-3 points of probability edge vs mid just to cross the spread | [C] | — |

---

## 3. Totals and distributions

| Claim | Source | Conf. |
|---|---|---|
| SD of margin around the spread about 12 points (binomial shooting argument) | https://www.goodreads.com/author_blog_posts/16483561-why-is-standard-deviation-of-nba-scores-about-point-spread-12-points (Wayne Winston, 2018) | [R] |
| Opening-spread MSE 137.49 implies **sigma about 11.7** (2006-14); MAE 9.17 / sqrt(2/pi) gives 11.5, so residuals are close to normal | Manner 2015, above | [R]/[C] |
| EPM live 2024-26 mean abs miss 11.0 implies sigma about 13.8 for a *model*; market sigma will be lower. Recent seasons score more; refit sigma per season on own residuals rather than hard-coding 12 | EPM page; [C] | [C] |
| Total SD about 17.4 (estimated) / 18.3 (empirical) at totals near 200; per-team points SD 11.1 (2009-10) | https://www.sportsbookreview.com/forum/handicapper-think-tank/349745-which-is-the-std-dev-for-nba-totals | [R], **low confidence**: forum, 2010, totals then about 200 vs about 227 now |
| Consistency check: team SD 11.1 and total SD 18.3 imply within-game score correlation rho about 0.36 and margin SD about 12.6, matching the 12-point figure | [C] | — |
| League scoring environment (b-ref): points/team/game 102.7 (2015-16) to 114.7 (2022-23), 115.6 (2025-26), 113.7 for 2026-27 to date; pace 95.8 to 99.4; ORtg 106.4 to 115.7. Year-to-year drift of 1-4 points per team is common, and early 2026-27 is running ~2 below last season | https://www.basketball-reference.com/leagues/NBA_stats_per_game.html | [R] |
| Margin vs total correlation: **no published coefficient found**. Theory: Cov(X-Y, X+Y) = Var(X) - Var(Y), so roughly 0 unconditionally; late-game fouling in close games and starters resting in blowouts create non-linear dependence. Measure from own data | search results; [C] | [U] |
| Overtime creates a right tail in totals (unders lose big); share of OT games not verified | https://neilpaine.substack.com/p/where-sixers-knicks-ranks-among-recent | [S] |

**Totals modelling recipe** [C]: possessions = blend of both teams' pace (harmonic/opponent-adjusted) x a league-pace factor;
points = possessions x (team ORtg + opponent DRtg - league ORtg) / 100 with **league terms from a rolling
current-season window** (shrunk toward the prior season early). The early-season totals bias (Baryla 2007)
is exactly a slow adaptation to league drift. Use the season's league average as of date T only (PIT).

---

## 4. Free / programmatic data sources (with PIT status)

| Source | What | Access / caveats | PIT? | Conf. |
|---|---|---|---|---|
| nba_api (stats.nba.com wrapper) https://github.com/swar/nba_api | 130+ endpoints: box scores, game logs, play-by-play, lineups, plus live scoreboard/box/PBP | MIT; no key; throttles rapid calls; **often blocks cloud IPs** (fine from a home PC); endpoints change unannounced | Box scores are historical facts, safe to rebuild ratings as of T | [R] |
| Official injury report PDFs, e.g. https://ak-static.cms.nba.com/referee/injury/Injury-Report_2024-03-23_11PM.pdf , ..._2026-05-05_10_30PM.pdf | Status (Out/Doubtful/Questionable/Probable/Available) + reason per player | Filename = publish time (ET). Hourly names in 2024, 15-minute names in 2026 | **Yes**: filename timestamp = observed_at | [R] filename pattern |
| nbainjuries (PyPI) https://pypi.org/project/nbainjuries/ | Parses the PDFs; history from 2021-22; hourly or 15-minute snapshots | MIT; needs Java (tabula-py). Alt: nba-injury-report-pdf-to-df (pdfplumber, Jan-2025 layout) | Yes | [R] |
| Injury rules | Day-before ~5 pm local filing (third-party); game-day window 11 am-1 pm local (8-10 am for tips at or before 5 pm), added/clarified Dec 2025, not required on 2nd night of B2B | https://www.thescore.com/nba/news/3432280 [R]; a 2023-24 ESPN snippet describes a similar 11-1 resubmission, so **the dates of the rule change are inconsistent between sources: unresolved** | — | [R]/[S] |
| Projected/confirmed lineups | No free documented API with timestamps found. Options: scrape (RotoWire/FantasyPros/Kalshi lineup pages; ToS), SportsDataIO (paid), or derive starters from injury report + last game's starters | https://apis.io/apis/sportsdataio/sportsdataio-nba-v3-projections-api/ , https://kalshi.com/nba/starting-lineups | Only if you capture yourself | [S] |
| ESPN core odds API `sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events/{id}/competitions/{id}/odds` | **Tested 2026-11-20**: Jan-2017 game has 13 books with spread and total (single "current" value, presumably closing); Jan-2025 game has ESPN BET with open, close, current | Free, undocumented | Close values are benchmark only (no capture timestamps); fine for CLV | [R] tested |
| Princeton DSS Historical Sports Odds DB https://dss.princeton.edu/catalog/resource6741 | NBA 2006-2023 open/close spreads and totals (purchased from oddswarehouse) | Access terms not stated (likely Princeton affiliation) | Benchmark | [R] |
| SportsBookReviewsOnline NBA archive | Historic free open/close xlsx | Archive URL now **404**; mirrors exist on GitHub/Kaggle, not verified | Benchmark | [U] |
| The Odds API https://the-odds-api.com/historical-odds-data/ | Historical snapshots of spreads/totals/ML from mid-2020 | **Paid** | Yes (timestamped snapshots) | [S] |
| StatMuse odds https://blog.statmuse.com/introducing-statmuse-nba-historical-odds | Back to 2016-17 | Query UI, not bulk | — | [S] |
| Kaggle "NBA historical stats and betting data" (ehallmar); paid opening/closing file 2018-2026 with a free 20-game Kaggle sample | Columns and coverage not verified | — | — | [S] |
| basketball-reference | Box scores, BPM/VORP, league averages | Scraping rate limits apply (~20 req/min per Sports Reference bot policy) | Facts only | [U] for rate limit |
| EPM (dunksandthrees) | Daily EPM/skills since 2001-02 | API **subscription**; ToS on site | **Yes** (daily history) | [R] |
| DARKO (darko.app) | DPM leaderboard CSV, Rewind | Terms unstated; check before automating | Unclear | [R]/[U] |
| 538 nba_elo.csv | Historic Elo/RAPTOR probabilities | **Dead** (redirects); GitHub folder holds only README | — | [R] tested |

PIT warning: any rating fetched today and applied to past games leaks (season-end ratings include future games).
Backtests must rebuild ratings from box scores as of T, or use a provider's dated daily history (EPM API).

---

## 5. Evaluation methodology

| Claim | Source | Conf. |
|---|---|---|
| Pinnacle's head of trading: beating the closing line is a dependable sign of a sharp bettor; closing prices on high-volume events are efficient, inefficiencies sit between open and close | https://www.pinnacle.com/betting-resources/en/educational/how-to-solve-a-problem-like-efficiency-part-one/rn8j5rnqj2p8t68p , https://www.pinnacle.com/betting-resources/en/betting-strategy/beating-the-market-is-it-possible-to-be-a-profitable-bettor/k932vpwqkyfunxwb | [S] (body text did not load) |
| CLV is only meaningful against a liquid close; at sample sizes under ~5k bets randomness still matters for P&L-based judgement (Data Golf, using Buchdahl's method) | https://datagolf.com/analyzing-betting-odds | [S] |
| Moskowitz: closing moves slightly overshoot, so CLV is a strong but not perfect proxy | J. Finance 2021, above | [S] |

**Recommended protocol** [C unless cited]
1. **Primary metric: CLV in probability points** = devigged closing implied probability of the side bought minus price paid. Polymarket's own close is thin (392 bps median spread), so take the close from a liquid reference: devigged sportsbook consensus (ESPN core odds close), with Polymarket mid as secondary.
2. **Secondary: log-loss/Brier vs the market's devigged close on the same games** (paired difference per game), plus a calibration slope of outcome on (model - market). A slope near 0 means the deviations carry no information (the NCAAF/NBA "no market skill" finding in this project's memory is this test).
3. **Blend test**: fit p = market + lambda x (model - market) on train/validation; lambda about 0 means no skill. Manner's equal-weight blend roughly matched the market. The model's job is to earn a positive lambda.
4. **Sample sizes** (normal approximation, one-sided alpha 0.05, power 0.80, z-sum 2.49):
   - Win rate at near-even prices, detect a true 52% vs 50% (2 points): n = (2.49 x 0.5 / 0.02)^2 = **about 3,900 bets**; 1 point: **about 15,500**. Two-sided 95% CI excluding 0 at 2 points: about 2,400.
   - One NBA regular season = 1,230 games, so outcome-based proof of a 1-2 point edge takes 3-12 seasons of one bet per game. Spread, total and ML on the same game are correlated, so they are not 3x the sample; cluster standard errors by game and by slate date.
   - CLV, **illustrative** (no published per-bet CLV SD; measure yours): with per-bet CLV SD of 3 points, detecting mean CLV of 1 point needs about (2.49 x 3/1)^2 = **56 bets**; SD 4 needs about 99. This is why CLV is the practical metric.
   - Brier, illustrative: per-game paired Brier difference SD of about 0.03 against a true advantage of 0.0008 needs about 8,700 games. Brier vs market cannot confirm a small edge within a season, only reject a large one.
5. Report the observed ATS hit rate against the price paid (break-even 52.4% at a typical sportsbook margin; at Polymarket it equals the ask paid). The current 45% ATS at claimed 60%+ fails calibration outright: the predicted-vs-realized gap is about 15 points, far outside noise for any n above ~100.

---

## Not verified / gaps
- 538 methodology constants (home court, rest, travel, altitude, Elo K and points-per-Elo): site offline, no archive access.
- Any public model's ATS or Brier record against NBA closing lines: none found.
- Current-era (2023+) total SD and the margin/total correlation: compute from ESPN close + results.
- Exact rule dates for game-day injury windows (2023-24 vs Dec 2025 sources disagree).
- Kaggle/SBRO mirror coverage; DARKO Rewind as-of fidelity and terms; basketball-reference rate limit.
- Altitude, travel, pace-mismatch and referee effects: no rigorous evidence that the market misprices them.
