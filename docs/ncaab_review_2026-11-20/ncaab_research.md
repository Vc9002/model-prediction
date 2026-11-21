# NCAAB modelling research checklist (web research, 2026-11-20)

Scope: what strong public NCAAB models and sharp bettors use. This is a checklist of inputs and benchmarks, not a verdict on any specific model.
Prices: all as implied probability % or cents. No American odds.

Evidence tags:
- **[P]** fetched primary source (the system's own page or the study itself)
- **[S]** fetched secondary source (third party describing a system, or a betting-media analysis)
- **[N]** search snippet only; the page was not fetched (KenPom blog pages returned 403, the Harvard Sports Analysis post returned 404)

---

## 1. Public rating systems: methodology and early-season priors

- [ ] **KenPom adjusted efficiency (AdjO, AdjD, AdjEM, AdjT)** [N]
  - (a) Points per 100 possessions, adjusted for opponent, site and game date. Offense and defense are rated separately against the national average on the date played. Pace is adjusted for the opponent's preferred tempo. Win probability uses a Pythagorean or logistic form on the efficiency gap. It is predictive only and margin-based: the FAQ says it uses only margin and site.
  - (b) An older iteration used a flat 3.75-point home-court advantage (HCA). Recent games carry more weight. Covers says KenPom weights games equally, which conflicts with KenPom's own snippet; trust KenPom's.
  - (c) https://kenpom.com/blog/ratings-methodology/ , https://kenpom.com/blog/ratings-explanation/ , https://kenpom.com/blog/pomeroy-ratings-faq/
- [ ] **KenPom preseason prior** [N]
  - (a) Built from the program's prior seasons and returning personnel. In 2014 transfers and injured returners with D-I experience were added. Earlier versions said "all but 5-star recruits are virtually ignored." The goal shifted from projecting end-of-season strength to estimating current strength. The prior is phased out during the season.
  - (b) The exact weight and decay are not retrievable (403).
  - (c) https://kenpom.com/blog/?p=84 , https://kenpom.com/blog/preseason-ratings-2014/
- [ ] **Bart Torvik / T-Rank core** [S] (FAQ written by a former maintainer of the site)
  - (a) Iterative opponent-adjusted points per possession, offense and defense separately. Barthag is a Pythagorean value with exponent 11.5. Win probability uses log5 on Barthag. A spread or score projection is each team's AdjOE against the opponent's AdjDE, scaled by the national average, multiplied by projected possessions from both tempos.
  - (b) Recency weighting:
    - games under 40 days old count 100%
    - minus 1% per day after that
    - games 80 or more days old count 60%
  - (c) https://adamcwisports.blogspot.com/p/every-possession-counts.html , https://www.covers.com/ncaab/what-are-torvik-ratings
- [ ] **T-Rank blowout and garbage-time handling** [S]
  - (a) Games with a margin above 10 points between mismatched teams are discounted. At margins of 20 or more, game weight = 1 − 2·(Barthag gap − 0.5); for example, 0.80 vs 0.20 by 20 gives a weight of 80%. GameScript (average lead, locked once the game is decided) is also used.
  - (b) As stated above.
  - (c) https://adamcwisports.blogspot.com/p/every-possession-counts.html
- [ ] **T-Rank preseason prior** [S]
  - (a) Projection built from returning minutes, player efficiency, transfers and recruiting. Weights are not published.
  - (b) Blended in, then phased out once the team has played 13 adjusted games, which is about 15-16 actual games because blowouts count fractionally.
  - (c) https://adamcwisports.blogspot.com/p/every-possession-counts.html ; inputs per https://www.on3.com/teams/kentucky-wildcats/news/kentucky-basketball-preseason-rankings-bart-torvik-projections-win-total [N]
- [ ] **T-Rank home court** [S]
  - (a) Home and road adjustment of 1.4% to each team's AdjOE and AdjDE.
  - (b) About 2-4 points at a typical pace of about 68 possessions, depending on whether the 1.4% is applied once or to both sides. This is my arithmetic, not stated by the source; verify against Torvik game projections.
  - (c) https://adamcwisports.blogspot.com/p/every-possession-counts.html
- [ ] **EvanMiya BPR (player-based)** [P]
  - (a) Player offensive and defensive impact per 100 possessions. Built from a multi-year Bayesian RAPM, with a box-score prior ("Box BPR", trained on RAPM) as each player's prior mean. Box stats make up more than half the final weight for most players. Team points per possession = about 104 + HCA + sum of the five offensive BPRs − sum of the five opposing defensive BPRs.
  - (b) Preseason priors come from prior BPR, role, class, recruiting (for freshmen) and a separate transfer model. They keep about 15% weight at season's end and never reach zero.
    - Scale: best player above +10, top-50 player about +7, high-major starter about +4.
  - (c) https://blog.evanmiya.com/p/bayesian-performance-rating
- [ ] **EvanMiya roster talent** [P]
  - (a) Minutes-weighted sum of projected player BPR.
  - (b) Explains about 80% of the variation in current team rating.
  - (c) https://blog.evanmiya.com/p/roster-talent-reigns-supreme
- [ ] **Haslametrics** [P]
  - (a) Built mainly from play-by-play, used in more than 90% of games:
    - shot location mix and accuracy (rim, mid-range, three)
    - transition points after steals (within 10 seconds) and after offensive rebounds (within 5 seconds)
    - garbage time cut once a game is "analytically final"
    - recency-weighted by default
    - momentum (last 4-8 games vs season)
    
    Four Factors are deliberately rejected as too noisy.
  - (b) Preseason baseline since 2020-21 from:
    - program prestige
    - last season's rating
    - returning players
    - transfers
    - recruiting
    - coaching changes
  - (c) https://haslametrics.com/about.php
- [ ] **ESPN BPI (2016 description)** [P]
  - (a) Game model uses:
    - team and opponent strength
    - site
    - travel distance (a cross-country road game is treated differently from a typical one)
    - rest days
    - altitude (a few teams)
    - pace
    - down-weighting of extreme results
    - crowd edge for the closer team at neutral sites
  - (b) Preseason inputs, combined in a Bayesian hierarchical model:
    - coach's adjusted offensive and defensive history (replacement level for first-time coaches)
    - recruiting grade averaged across 4 services
    - number of 5-star recruits
    - returning minutes, using each player's career-high minutes share, and those players' adjusted ratings
    
    The prior decays but never fully disappears. Favorites won 75.6% of games since 2007-08. Teams given 50-60% won 55.8%.
  - (c) https://www.espn.com/blog/statsinfo/post/_/id/125994/bpi-and-strength-of-record-what-are-they-and-how-are-they-derived
- [ ] **Silver Bulletin SBCB (March 2025 Elo)** [P] *Closest analogue to a team-level Elo.*
  - (a) Elo mechanics:
    - k = 38, raised to as much as 56 early in the season and declining linearly to 38 by about game 20
    - tournament games at 1.25x
    - margin-of-victory multiplier (3 + margin)^0.85
  - (b) Home court and travel:
    - team-specific HCA, which carries over fully between seasons and moves slowly
    - travel adds about 8·miles^(1/3) Elo points (described as shrinking over time); it applies at neutral sites too
    - altitude is absorbed into each team's HCA
  - (c) Priors and conversion:
    - 30-35% reversion each season toward the conference mean
    - "Bayesian" version uses preseason AP and Coaches polls, then a 50/50 blend with pure Elo
    - D2 opponents lumped at about 660 Elo (road or neutral) and about 900 (home)
    - new D-I teams start at 1300
    - about 27 Elo = 1 point of margin
    - tournament rating gaps multiplied by 1.07
  - (d) https://www.natesilver.net/p/sbcb-methodology
- [ ] **Silver Bulletin COOPER (successor, 2025-26)** [P]
  - (a) Elo mechanics:
    - k = 55, doubled to 110 early in the season and declining until about game 15
    - margin is linear with no blowout cap, plus about 6 points of value for winning, judged against pregame win probability
  - (b) Travel, priors and conversion:
    - travel about 5·miles^(1/3) Elo points
    - team-specific HCA
    - 30% reversion each season toward the conference mean
    - polls and prior rating as priors
    - about 28.5 Elo = 1 point
  - (c) Distribution and injuries:
    - margin SD about 10 points, rising with the rating gap and in low-stakes non-conference games
    - "slightly fat-tailed"
    - injuries only in tournament forecasts, valued by Win Shares against a replacement player at 5 minutes per game or less; a top player is worth about 7-10 points against replacement
  - (d) Benchmark: about 1% more winners than SBCB. There is no comparison against the market.
  - (e) https://www.natesilver.net/p/introducing-cooper-silver-bulletins
- [ ] **FiveThirtyEight (old tournament model)** [N]
  - (a) Composite of 6 ratings: 4 computer systems (KenPom, Sagarin, BPI, LRMC or Moore) plus 2 human (the committee's S-curve and preseason polls). Its Elo used score, home court and site, with reversion toward the conference mean. The men's model included injury adjustments.
  - (b) Not quantified in the snippets.
  - (c) https://fivethirtyeight.com/features/how-our-march-madness-predictions-work
- [ ] **Sagarin** [N]
  - (a) "Predictor" is margin-based with diminishing returns on blowouts: winning 70-0 rates about the same as 35-0. Its "Elo chess" version uses wins and losses only. No injury input.
  - (b) Not quantified.
  - (c) https://en.wikipedia.org/wiki/Jeff_Sagarin
- [ ] **Massey** [N]
  - (a) Least squares on point differentials, solving XᵀX r = Xᵀy with a sum-to-zero constraint. This is the same family as a ridge margin engine.
  - (b) Not quantified.
  - (c) https://www.masseyratings.com/theory/ls.htm , https://masseyratings.com/theory/massey97.pdf
- [ ] **LRMC (Kvam and Sokol; Bayesian LRMC)** [N]
  - (a) Logistic regression on home-and-home margins feeds a Markov chain. Home court is modelled as a win-probability effect, not points. Sokol says "almost all home courts are about the same."
  - (b) Not quantified.
  - (c) https://www2.isye.gatech.edu/~jsokol/lrmc/about , https://www2.isye.gatech.edu/~jsokol/ncaa.pdf
- [ ] **TeamRankings preseason model** [P]
  - (a) Two-stage regression:
    - last-year rating and older program ratings
    - returning offensive and defensive production against a typical team (the average team returns about 50-55%)
    - recruiting from RSCI rankings (75% current class, 25% prior class)
    - D-I transfers, worth more to weaker teams; JUCO ignored
    - manual coaching-change adjustment
    - penalties for losing draft picks
  - (b) Correlations:
    - preseason projection with end-of-season rating: +0.90
    - year-to-year rating: +0.84
    - ratings from 2, 3 and 4 years back: +0.76, +0.72, +0.70
    
    Keeping some prior weight through season's end improved tournament predictions.
  - (c) https://teamrankings.com/blog/ncaa-basketball/preseason-rankings-ratings-explained
- [ ] **Kaggle March Machine Learning Mania** [N]
  - (a) Winning entries are usually simple gradient-boosted trees or logistic models on difference features: team quality, regular-season point differential, seeds, and how A's opponents fared against A. The 2023 winner reused a 2018 XGBoost with a few features.
  - (b) Not quantified.
  - (c) https://recapio.com/digest/kaggle-winners-walkthroughs-march-machine-learning-mania-2023-with-rustyb-by-kaggle , https://arxiv.org/pdf/1412.0248

## 2. Home-court advantage

- [ ] **League-average HCA, recent** [S]
  - (a) VSiN "true HCA" relative to a power rating, measured on home games from 2022-23 to January 2025.
  - (b) Values:
    - 3.3 points overall
    - about 3.3 in non-conference games
    - about 2.9 in conference games
    - bookmakers typically assign about 3.0
    - VSiN's own published values run 2.0-4.0, with about 0.4 points deducted in conference games
  - (c) https://vsin.com/college-basketball/determining-college-basketballs-true-home-court-advantage/
- [ ] **Trend over time: mixed, not a clean post-2020 decline** [N]
  - (a) Win-rate and margin evidence:
    - long-run secular decline in home win % (KenPom; Medium "Long Arc")
    - 2020-21 low of 57.3% home win rate (fans restricted)
    - one KenPom-cited season at 59.9%, against 57.5% for the prior two (season unnamed)
    - a 2017-18 spike
  - (b) Possible drivers: falling attendance since 2008, referee or foul channel, slower pace (fewer possessions to exert the edge). Conclusion: about 3 points now; do not assume a further step-down without measuring it on recent seasons.
  - (c) https://www.samford.edu/sports-analytics/fans/2021/Did-Minimal-Crowds-in-2020-2021-Impact-College-Basketball-Home-Team-Performance , https://medium.com/chart-shots/the-long-arc-of-home-court-advantage-dc54c591bb7f , https://www.cbssports.com/college-basketball/news/how-the-home-court-advantage-in-college-basketball-is-shrinking-this-season-due-to-fewer-fans-at-games
- [ ] **Team-specific HCA** [S/N]
  - (a) VSiN raw team spread:
    - highest: Alabama +9.2, Bradley +8.5, Houston +8.1
    - lowest: Loyola-MD −2.2
    - conference averages from Big 12 +5.3 to Patriot +0.8, MAAC +1.7
  - (b) KenPom:
    - predicts team HCA from 6 seasons of data, using home foul advantage (a better predictor than home scoring advantage) plus elevation
    - says ranks are very noisy: "10th vs 60th is shaky"
    - range about 2-4.5 points (Air Force 4.5 was the top in one snapshot)
    
    SBCB and COOPER carry team HCA forward slowly, for example Purdue at about +2 above the D-I average.
  - (c) https://kenpom.com/blog/how-to-measure-site-specific-home-court-advantage-part-two/ , https://www.natesilver.net/p/sbcb-methodology
  - (d) Implication: shrink team HCA heavily toward the mean. Conference-level HCA is a more stable middle ground.
- [ ] **Market-implied HCA** [N]
  - (a) KenPom estimates HCA as half the spread difference between home-and-home meetings.
  - (b) A cheap point-in-time-safe HCA estimate if you hold historical lines.
  - (c) https://kenpom.com/blog/mining-point-spread-data-home-court-advantage/
- [ ] **Home and road shooting splits** [S]
  - (a) Non-conference games 2018-2023:
    - home: 52.9% 2P, 34.9% 3P
    - road: 46.5% 2P, 32.0% 3P
    
    Conference games: home 50.7% / 34.6% vs road 49.2% / 33.7%. The non-conference road gap is inflated by buy games (weak road teams).
  - (b) Neutral-site efficiency was 100.7 against 101.1 for home or away games (basically the same). Maui games were outliers at 108.3, so venue matters for totals.
  - (c) https://statsbywill.substack.com/p/neutral-site-games-a-problem-for
- [ ] **Neutral sites and November multi-team events (MTEs)** [S]
  - (a) The November share of neutral-site games was **not found**; count it from schedule data. Neutral sites are heavily concentrated in November and December (Feast Week alone has 10+ ESPN events).
  - (b) VSiN results, 3,380 neutral-site games in November and December since 2016-17:
    - favorites of 15+ points: 43.3% against the spread (ATS)
    - favorites of 3 to 5.5 points: 54% ATS
    - non-power-conference favorites of 9+ points: 43.3% ATS
    - double-digit favorites after 7+ days of rest: 34.9% ATS (n=64)
    - back-to-back days, team that won by 10+ the prior day: 57.1% ATS, unders 58.7%
    
    These are promotional angles with data-mining risk; treat them as hypotheses.
  - (c) https://vsin.com/college-basketball/college-basketball-betting-systems-for-early-season-neutral-court-games/
- [ ] **Travel distance and time zones** [P/S]
  - (a) Elo terms: SBCB about 8·miles^(1/3), COOPER about 5·miles^(1/3), both said to be shrinking over time. BPI includes travel distance.
  - (b) Other evidence:
    - women's D-I study: games in a different time zone lost more often regardless of opponent
    - men's college game 2 of a two-game road trip won less often than game 1
    - no large men's D-I points estimate found
  - (c) https://www.natesilver.net/p/sbcb-methodology , https://openscholar.uga.edu/record/1923/files/VanZantRiverPHD.pdf , https://thesportjournal.org/article/evaluating-the-two-game-road-trip-in-college-sports-does-a-travel-partner-scheduling-approach-affect-team-competitiveness/
- [ ] **Altitude** [P/N]
  - (a) Absorbed into team HCA by SBCB and COOPER. Elevation is an explicit input in KenPom's HCA model. BPI says it applies to a few teams.
  - (b) Air Force was the top KenPom HCA at about 4.5 in one snapshot.
  - (c) as above
- [ ] **Rest and back-to-backs** [P/S]
  - (a) BPI includes rest days. In conference tournaments and MTEs, consecutive days are common.
  - (b) No clean college points estimate found. In the NBA, performance peaks at about 3 days of rest.
  - (c) https://www.espn.com/blog/statsinfo/post/_/id/125994/bpi-and-strength-of-record-what-are-they-and-how-are-they-derived , https://stacks.cdc.gov/view/cdc/196685/cdc_196685_DS1.pdf

## 3. Totals: drivers of error and market accuracy

- [ ] **Pace or possessions model** [S]
  - (a) Projected points = projected points per possession (team AdjOE against opponent AdjDE, scaled by the average) × projected possessions (from both teams' adjusted tempos). This is the standard construction (T-Rank).
  - (b) Possession-based beats points-per-game; it needs opponent-adjusted tempo.
  - (c) https://adamcwisports.blogspot.com/p/every-possession-counts.html
- [ ] **3-point % regression (defense in particular)** [N]
  - (a) KenPom: defenses have little control over opponent 3P%. Opponent 3PA rate is the controllable and stable stat. Year-to-year r is 0.61 for 3PA%, against 0.15 for 3P%.
  - (b) For totals and spreads:
    - regress observed offensive and defensive 3P% hard toward the mean (defensive 3P% almost entirely)
    - model 3PA rate as the skill
    - ShotQuality-style expected 3P% from shot location is the advanced version
  - (c) https://kenpom.com/blog/defense-has-little-control-over-opponents-3p/ , https://kenpom.com/blog/one-last-post-on-3p-defense/
- [ ] **Free-throw rate, fouls and officials** [N]
  - (a) Free-throw rate drives scoring; foul-heavy games lean over (small 2017 tournament sample: 14-6 over). KenPom finds most crews deviate by only "a couple of fouls" from normal. Crews rotate in college (no fixed crews), so crew modelling is weak. Assignments are not public until close to tip.
  - (b) Low priority.
  - (c) https://kenpom.com/blog/?p=157 , https://thescore.com/ncaab/news/1255371
- [ ] **Overtime** [S/N]
  - (a) Frequency:
    - 5.5% of D-I games in 2023-24 (346 of 6,243)
    - about 6% since 1993
    
    Each overtime adds roughly 5 minutes, about 12% of regulation time, so about 15-20 points to a total (my arithmetic).
  - (b) Models must include overtime in the distribution of totals (it makes the right tail heavier) and in moneyline ties.
  - (c) https://sleeper.com/blog/college-basketball-overtime-rules , https://fivethirtyeight.com/features/college-basketballs-problem-isnt-endless-nail-biters-its-long-blowouts
- [ ] **Late-game intentional fouling** [N]
  - (a) Adds free throws and possessions in close games. Its effect on totals and covers was **not found quantified**; measure it yourself.
  - (b) Unknown.
  - (c) https://www.predictem.com/college-basketball/tips/late-fouling/
- [ ] **Totals bias in the market** [N]
  - (a) Bettors prefer overs and books shade totals up. Totals land "just under" more often than "just over" (Borghesi, Paul, Weinbach). TeamRankings: 49.9% overs across 82,003 games since 2008.
  - (b) Near-zero naive edge today. Football markets have since corrected.
  - (c) https://ubplj.org/index.php/jpm/article/view/475 , https://www.teamrankings.com/ncb/odds-movement/over-under/results
- [ ] **Venue and early-season totals** [S/N]
  - (a) Neutral-site and early-season totals are hard to set: roster turnover and varying officiating. Venues differ: Maui 152 points per game vs 141.5 at other neutral sites. One handicapper notes neutral courts tend to be lower-scoring.
  - (b) Not quantified beyond the figures above.
  - (c) https://statsbywill.substack.com/p/neutral-site-games-a-problem-for , https://www.gambling.com/us/sportsbooks/strategy/betting-ncaa-college-basketball-over-under-odds-and-markets-1551700
- [ ] **Closing total vs actual total, RMSE or MAE** [—]
  - (a) **Not found** in published sources, even with an extended search. Compute it from your own closing totals.
  - (b) Proxies only:
    - Talisman Red reported mean absolute total error of 13.8 points for KenPom and 14.9 for its own system (2017-18). These are model-based, not the market; MAE 13.8 implies RMSE of about 17 if normal.
    - Derived per-team SD of about 7.5 gives a total SD of about 10.4 assuming independent team scores. That is a **floor**: shared pace and overtime push the real total SD higher.
  - (c) https://talismanred.com/ratings/hoops/explanation.shtml , https://drgreenandwhite.blogspot.com/2020/04/the-variance-of-college-basketball-how.html

## 4. Market efficiency and CLV

- [ ] **Closing spread accuracy** [S]
  - (a) Actual margin around the Vegas spread has an SD of about 10-10.3 points (fit 10.04), from Prediction Tracker data 2004-2018. It grows slightly with spread size. Normal fits better than logistic.
  - (b) Harvard Sports Analysis, 30k+ closing lines 1997-2011: close accuracy is roughly constant through the season, so the market is about as accurate in November as in February. [N, page 404]
  - (c) https://drgreenandwhite.blogspot.com/2020/04/the-variance-of-college-basketball-how.html , https://harvardsportsanalysis.org/2011/10/how-much-does-vegas-learn/
- [ ] **Straight-up accuracy benchmarks** [S]
  - (a) 2021-22, about 5,000 games:
    - opening Vegas line: 72.7% winners
    - BPI: 72.6%
    - Sagarin: 72.3%
    - KenPom: 72.2%
    
    Top systems are within about 0.5 points of the opener on winner %.
  - (b) An 8.5-9.5 spread MAE for Vegas came from a Macfax search snippet; the fetched page did not confirm it. [N]
  - (c) https://talismanred.com/ratings/hoops/explanation.shtml
- [ ] **Long-run efficiency tests** [N]
  - (a) Paul and Weinbach (1996-97 to 2003-04): efficiency is not rejected overall. Underdogs of 20+ points beat the line. The home-team bias runs opposite to other sports.
  - (b) A large-favorite / underdog effect persists in neutral-site November games: favorites of 15+ cover 43.3% (VSiN, above).
  - (c) https://econpapers.repec.org/RePEc:spr:jecfin:v:29:y:2005:i:3:p:403-408
- [ ] **Where inefficiency is claimed** [S/N]
  - (a) Claimed soft spots:
    - early season (low handle, public still on football)
    - low- and mid-major games and lesser conferences
    - openers more than closers
    - big non-conference buy-game spreads
  - (b) Evidence is mostly trade-media opinion; no large-sample study found.
  - (c) https://vsin.com/college-basketball/college-basketball-bets-for-wednesday-november-5-from-adam-burke/ , https://www.hoopshq.com/?p=15787 , https://boardroom.tv/?p=17957
- [ ] **How much sharp models beat the close** [—]
  - (a) **Not found** (no published figure).
  - (b) Torvik states the chance of covering is "always about 50%" and targets margin accuracy, not cover rate.
- [ ] **CLV as validation** [P]
  - (a) Whelan studied **NBA moneylines** (3,670 games, about ten US books):
    - positive CLV is correlated with returns, but only the top decile of CLV was clearly profitable (about +11.4%)
    - the ninth decile had about 5% CLV and only about 0.2% profit
    
    For college basketball he warns that the easiest CLV comes from betting big underdogs early, which can show CLV and still lose.
  - (b) Recommendation: use CLV as a process metric alongside calibration and Brier score, split by favorite or underdog and by price band.
  - (c) https://www.karlwhelan.com/?p=2595
- [ ] **Prediction-market venue cost** [S]
  - (a) March Madness 2026 sample, house take:
    - Polymarket US: about 1.3-1.6%
    - Kalshi: about 4.5% (similar to FanDuel at about 4.5%)
    
    Volume: Kalshi traded $2.27B in men's college basketball in February 2026. Polymarket largely sat out the 2026 tournament.
  - (b) Lower take means a lower break-even. Example: at a 50¢ fair price, a 1.5% total take puts the break-even near 50.75%.
  - (c) https://defirate.com/news/hidden-tax-inside-your-prediction-market-app-march-madness-odds-compared/ , https://www.pokernews.com/prediction-markets/news/2026/04/march-madness-were-the-markets-right-51036.htm

## 5. Player availability and injuries

- [ ] **NCAA tournament availability reports (2026 pilot)** [S]
  - (a) Required for the 2026 D-I men's and women's tournaments:
    - filed the night before and 2 hours before tip
    - players are presumed available unless listed questionable or out
    - fines start at $10,000
    - public on NCAA.com
    - HD Intelligence is the provider
    
    Not regular-season-wide.
  - (b) Unknown whether it continues for 2026-27.
  - (c) https://www.ncaa.org/news/2025/10/30/media-center-ncaa-announces-implementation-of-player-availability-reporting-for-2026-di-mens-and-womens-basketball-championships.aspx , https://www.ncaa.com/di-mens-basketball-player-availability
- [ ] **Conference reports** [S]
  - (a) Coverage:
    - SEC: mandatory for conference games since 2024-25 (probable / questionable / doubtful / out, plus a final report)
    - Big 12: night before conference games (available / game-time decision / out)
    - ACC and Big Ten reportedly already issue reports
  - (b) Coverage gap: non-conference games, including all of November, and low-major conferences.
  - (c) https://www.thescore.com/ncaab/news/3491438 , https://www.si.com/college/alabama/basketball/two-alabama-starters-listed-questionable-before-tennessee-game
- [ ] **Free or cheap aggregators** [S]
  - (a) RotoWire CBB news, RotoGrinders injury grid, TeamRankings matchup injury pages, Sportskeeda daily reports, CollegeBasketballData `LineupsApi` (Patreon tier).
  - (b) None found with clean, point-in-time-stamped history. Capture snapshots yourself with `observed_at` timestamps.
  - (c) https://web7.rotowire.com/cbasketball/news.php?conf=3 , https://rotogrinders.com/grids/college-basketball-injury-report-4005888 , https://www.teamrankings.com/ncaa-basketball/matchup/crimson-tide-fighting-illini-2025-11-19/injuries
- [ ] **Size of injury effects** [P/S/N]
  - (a) Estimates by source:
    - COOPER: a top player is worth about 7-10 points against replacement [P]
    - EvanMiya scale: high-major starter about +4 BPR, top-50 player about +7 [P]
    - a 2016 sportsbook manager: star college absences typically move lines "not more than 2" points [N]
    - Action Network: a mid-major starting point guard can move a line 4-5 points [S]
  - (b) Wide range. Shallow benches make star absences matter more in college than raw player share would suggest.
  - (c) https://www.natesilver.net/p/introducing-cooper-silver-bulletins , https://www.actionnetwork.com/education/how-injuries-affect-betting-lines-a-guide-to-market-movement
- [ ] **Cheap team-level proxy without injury feeds** [S]
  - (a) Returning-minutes share and roster continuity (Torvik's returning possession minutes back to about 2017; BPI uses each player's career-high minutes share). In season, a box-score "minutes concentration lost" proxy from ESPN team and player box scores, when a regular starter is missing from the last game(s).
  - (b) Unknown.
  - (c) https://adamcwisports.blogspot.com/p/data.html

## 6. Free data sources: historical, point-in-time, licensing

- [ ] **Torvik bulk files** [S]
  - (a) Point-in-time status by file:
    - `/timemachine/team_results/YYYYMMDD_team_results.json.gz`: **daily as-of-date ratings**, including the preseason prior and recency weighting, back to about 2011. **Point-in-time safe.**
    - `YYYY_results.csv`: game predictions with pregame spread and probability, back to 2015.
    - `YYYY_super_sked`: per-game data.
    - `getgamestats.php?year=&csv=1`: game-level advanced box stats.
    - `YYYY_rpm.json`: returning minutes.
    - `YYYY_team_results.csv`: **season-to-date or final only. Leaks if used for past dates.**
  - (b) Terms and robots.txt:
    - the author says bulk files exist "without the need to scrape"; mass scraping may be blocked; no warranty; he does not recommend it for gambling
    - `robots.txt` asks for a 10-second crawl delay and disallows `*.json`, `results.php`, `box.php`, `matchup.php` and several others
    - robots.txt fully blocks named AI crawlers, including ClaudeBot and GPTBot
    - no formal license; respect robots.txt, or contact Torvik and use cbbdata
  - (c) https://adamcwisports.blogspot.com/p/data.html , https://barttorvik.com/robots.txt
- [ ] **cbbdata (R, successor to toRvik)** [P]
  - (a) Contents:
    - `cbd_torvik_ratings_archive` (day-by-day ratings)
    - `cbd_torvik_game_prediction`
    - `cbd_torvik_game_factors` and `cbd_torvik_game_stats`
    - player game logs
    - `cbd_kenpom_ratings_archive` (day-by-day; needs your own KenPom subscription with a matching email)
  - (b) Free API key, updated every 15 minutes in season, MIT-licensed code; the data license is not stated. Archive start dates are not stated in the README; toRvik's `bart_archive` was "morning of date" back to 2014-15. Whether game predictions are stored pregame values is not stated; verify.
  - (c) https://github.com/andreweatherman/cbbdata/ , https://rdrr.io/cran/toRvik/man/bart_archive.html
- [ ] **hoopR / sportsdataverse** [S]
  - (a) ESPN men's college basketball play-by-play, team and player box scores, and schedules for 2006-2026 via `load_mbb_pbp`, `load_mbb_team_box`, `load_mbb_player_box` and `load_mbb_schedule`, prebuilt `.rds` files on GitHub releases; `espn_mbb_pbp(game_id)` for single games.
  - (b) Raw results only, so point-in-time safe if you build features yourself. Some hoopR functions also wrap KenPom (subscription).
  - (c) https://search.r-project.org/CRAN/refmans/hoopR/html/load_mbb_pbp.html , https://www.github.com/sportsdataverse/hoopR
- [ ] **ESPN APIs** [N]
  - (a) Endpoints:
    - `site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard` (scores and teams)
    - core API odds path `sports.core.api.espn.com/v2/sports/basketball/leagues/mens-college-basketball/events/{id}/competitions/{id}/odds`, plus `/odds/{provider}/history/0/movement`, by analogy with documented NFL paths
  - (b) **Unverified for men's college basketball; test with a known event ID.** Undocumented and unofficial, with no stated terms.
  - (c) https://gist.github.com/nntrn/ee26cb2a0716de0947a0a4e9a157bc1c
- [ ] **CollegeBasketballData.com (CBBD)** [N]
  - (a) Official REST API with `GET /lines` (multiple providers, 2013+) and `/lines/providers`, plus games, lineups and more. Free key; commercial use permitted per tier.
  - (b) Lines from 2013 make this the most practical free historical-lines source. Check whether lines carry timestamps (opening vs closing).
  - (c) https://www.github.com/willscot/cbbd-python , https://www.pkgstats.com/pkg:cbb-api
- [ ] **Kaggle March Machine Learning Mania data** [N]
  - (a) Regular-season compact and detailed box results (detailed since about 2003), tournament results, and `MMasseyOrdinals`: daily rankings from dozens of systems (KenPom, Massey, Sagarin, NET and others) keyed by `RankingDayNum`.
  - (b) Massey Ordinals are a **point-in-time** composite of public ratings. Ranks only, not ratings. Competition rules govern use.
  - (c) https://www.kaggle.com/competitions (March Machine Learning Mania), https://huggingface.co/spaces/Harshi/MarchMachineLearningMania/blob/main/marchmachinelearningmania2021.py
- [ ] **Sports-Reference CBB** [S]
  - (a) Rich historical box and schedule data, but:
    - asks users not to build tools on scraped data or use it to train AI without permission
    - rate-limits and blocks bots
    - paid licensing on request; suggests NatStat as a cheaper alternative
  - (b) Use for spot checks only, not for a pipeline.
  - (c) https://www.sports-reference.com/data_use.html , https://sportsreference.com/bot-traffic.html
- [ ] **Historical odds** [S/N]
  - (a) Sources:
    - The Odds API: NCAAB moneyline, spreads and totals snapshots from late 2020, paid plans from $30/month; true time-stamped snapshots, so point-in-time safe
    - TeamRankings: opening and closing lines for about 83k games since 2008 as web tables; scraping terms unclear
  - (b) For point-in-time closing-line or CLV research, timestamped snapshots are preferred over "closing" fields.
  - (c) https://the-odds-api.com/historical-odds-data/ , https://the-odds-api.com/sports-odds-data/ncaa-basketball-odds.html , https://www.teamrankings.com/ncb/odds-movement/ats/results
- [ ] **Prediction Tracker (Todd Beck)** [N]
  - (a) Tracks dozens of college basketball systems against the opening and updated lines (percentage correct, MAE, bias). Historically has had downloadable prediction CSVs.
  - (b) The results URL returned 404; locate the current path before relying on it.
  - (c) https://www.thepredictiontracker.com/

## 7. Distributional modelling

- [ ] **Margin SD around the closing spread** [S]
  - (a) About 10-10.3 points against the closing line (2004-2018), fit 10.04. It rises slightly with spread size. Normal fits better than logistic. About 68% of games land within 10 points of the spread and about 5% beyond 20.
  - (b) This is the **market's** residual. A model's own residual will be **wider**; estimate it on your own walk-forward predictions. COOPER uses an SD of about 10 that rises with the rating gap and in low-stakes non-conference games, "slightly fat-tailed."
  - (c) https://drgreenandwhite.blogspot.com/2020/04/the-variance-of-college-basketball-how.html , https://www.natesilver.net/p/introducing-cooper-silver-bulletins
- [ ] **Heteroscedastic SD** [P/S]
  - (a) SD grows with the expected margin (mismatches, garbage time) and the pace of the game. COOPER varies SD with pace and the rating gap.
  - (b) For spread and alternate-line pricing, model SD as a function of |expected margin| and expected possessions, not a constant.
  - (c) as above
- [ ] **Blowouts and garbage time** [N/S]
  - (a) Evidence:
    - KenPom: with 5 minutes left, the remaining margin's correlation with next-game margin reaches 0 at about an 18-point lead (the 18-23 bin), so late margin in blowouts is noise
    - T-Rank discounts mismatched blowouts
    - Haslametrics cuts after "analytically final"
    - SBCB uses (3 + margin)^0.85
    - Sagarin caps margin
    - COOPER is linear
  - (b) For a ridge or least-squares margin engine, cap or compress margins (for example, soft cap near 20-25 points, or a power transform) and/or down-weight mismatches.
  - (c) https://kenpom.com/blog/?p=338 , https://adamcwisports.blogspot.com/p/every-possession-counts.html
- [ ] **Totals distribution** [S]
  - (a) Per-team score SD is about 7.5. The total SD is at least about 10.4 even if the two scores were independent. Shared pace adds positive covariance and overtime (about 5.5-6% of games) adds a right tail, so the empirical total SD is larger.
  - (b) Measured value **not found**; fit on your own data, possibly as a mixture with an overtime component.
  - (c) https://drgreenandwhite.blogspot.com/2020/04/the-variance-of-college-basketball-how.html , https://sleeper.com/blog/college-basketball-overtime-rules
- [ ] **Tournament compression** [P]
  - (a) SBCB multiplies rating gaps by 1.07 in the NCAA tournament: fewer upsets than in the regular season, because effort is higher and there are no buy games.
  - (b) As stated.
  - (c) https://www.natesilver.net/p/sbcb-methodology

---

## Top 10 highest-leverage items for a team-level Elo / ridge model without player data

Ranked by points of impact × share of games affected × buildable from free, point-in-time-safe data. No claims about current model performance.

1. **Opponent-adjusted, per-possession efficiency with pace.** Replace or augment margin-only ratings with separate offensive and defensive per-possession ratings and adjusted tempo, computed from ESPN box possessions (hoopR). Spread = efficiency gap × projected possessions; total = points per possession × possessions. This is the core of KenPom, Torvik and Haslametrics and drives every market.
2. **Strong preseason prior with a measured decay.** Use:
   - prior-year rating regressed 30-35% toward the conference or program mean
   - returning-minutes share (Torvik `rpm` files, or computed from prior-season box scores)
   - recruiting and transfers where available
   - preseason polls as a cheap proxy (SBCB, COOPER)
   
   Phase it out over about 13-20 games but keep a residual weight (EvanMiya keeps about 15% at season's end; TeamRankings found residual weight helps). This affects most games in November and December, when markets are thinnest.
3. **Blowout and garbage-time compression in the margin target.** Use a margin cap or power transform, or down-weight mismatches (T-Rank formula; SBCB (3 + margin)^0.85; KenPom's 18-point finding). This matters most for buy games against low-majors and non-D1 opponents, common in November.
4. **Heteroscedastic, slightly fat-tailed margin distribution.** SD about 10 against the market. Estimate the model's own walk-forward SD as a function of |expected margin| and pace (COOPER). This directly sets moneyline and spread probabilities, and therefore edge size.
5. **Explicit neutral-site handling plus travel distance.** Flag neutral games; that flag is the dominant November site factor. Add a crowd edge for the nearer team and a miles^(1/3) travel term (SBCB 8x, COOPER 5x, both shrinking). Buildable from ESPN neutral flags plus venue and campus coordinates.
6. **Shrunken team-specific or conference-level HCA instead of a constant.** About 3 points on average, conference gaps of about 1-5 points. Shrink team estimates hard (KenPom: very noisy). Altitude teams fall out naturally. Re-measure the recent-season average rather than assuming a decline.
7. **Totals: regress 3P% (especially defensive) and model 3PA rate, FT rate and pace separately.** Defensive 3P% has a year-to-year r of about 0.15 against 0.61 for 3PA rate. Include overtime (5.5-6% of games) in the totals distribution. Calibrate the total SD on your own closing totals, which were not found published.
8. **Recency weighting.** For example, the T-Rank scheme: 100% weight to 40 days, then minus 1% per day to a 60% floor at 80 days. Alternatively, higher Elo k early (COOPER 2x until about game 15; SBCB 1.5x until about game 20) that decays. Cheap; captures in-season development and roster changes the model cannot see.
9. **Point-in-time external ratings as features or ensemble anchors.** Torvik time machine (daily, about 2011+), cbbdata Torvik archive, and Kaggle Massey Ordinals (daily ranks from many systems). Blending with KenPom is what SBCB and COOPER do for the tournament. Respect Torvik's robots.txt and crawl delay; season-end files leak.
10. **Validation on the market, not just outcomes.** Get historical lines (CBBD `/lines` 2013+, or The Odds API timestamped snapshots from 2020) and measure:
    - model error against closing-line error (market spread SD about 10)
    - CLV split by favorite or underdog and price band (Whelan caveat)
    - edge by early season, low-major and neutral-site segments, where inefficiency is claimed but unproven
    
    Polymarket's low take (about 1.3-1.6%) lowers the break-even, but fair-price edges still need to clear it.

Honourable mentions: a team-level injury or continuity proxy (minutes missing from the last box score, conference availability reports for SEC and Big 12 games); rest and back-to-back flags in MTEs and conference tournaments; tournament compression (1.07x).
