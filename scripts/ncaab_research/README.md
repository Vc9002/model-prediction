# NCAAB research harness (quick 261120-nip, lane B)

Every NCAAB candidate in the 261120 program is graded by one tool, `grader.py`, against the rules pinned in
`protocol_261120.json` (summary: `protocol_261120.md`). The pin's sha256 is
`72a8a9ff216c4f84cc80acd9dda6fcdac14b6cafb9131bb05fcb210ce3677813`. The grader hard-codes it and refuses to run
if the JSON changes. To change a rule, write an amendment: a new pin, written before any affected outcome is
joined, with K raised.

Addendum v2 (`protocol_261120_addendum_v2.json`, sha256
`75241bc7885378f266ba66ce383f3b50fe58869e9b699ae90f0ddd9c8ead6317`, also hard-coded) makes these changes:

- **Confirmation window:** the never-read block 2014-15..2019-20 plus 2021-22, touched once. 2013-14 is burn-in;
  2020-21 (COVID) is history only.
- **Line sources are pluggable:** `espn_archive`, `espn_backfill_261120` or `cbbd`.
  - `cbbd` is refused until the data lane pins its selection and id-crosswalk rule.
  - Development is graded on `espn_archive` only.
  - The source name, rule and sha256 are recorded on every scorecard.
- **Massey withdrawn:** D2_massey_pit is withdrawn and K is unchanged.

Pinned data files are re-hashed on every `load_archive`, and the served v7 constants are checked against the
frozen config. A rebuilt archive makes the grader refuse.

Run everything from the repo root with `OPENBLAS_NUM_THREADS=1` and `.venv/Scripts/python`.

## What the grader guarantees

- **Development window only.** Season years 2023-2026, scored leave-one-season-out.
  - The 2026-27 season (season year >= 2027, or any Eastern date >= 2026-07-01) is LOCKED.
  - A locked prediction row or fit season fails the whole call. The filter runs before any score or line is read.
  - The confirmation (backcast) window is sealed until its archive exists and a candidate is DEV_ELIGIBLE.
- **Identical games.** Each candidate is scored on the same games as three baselines:
  - the cohort is every D1-vs-D1 game with an ESPN closing spread line (22,947 games);
  - the baselines are `v7_served`, `v6_asof` and `closing_line`;
  - the candidate must cover at least 99% of the cohort in every season, otherwise the verdict is INCOMPLETE.
- **Echo check.** The `season`, `game_date`, `home_team`, `away_team` and `neutral` you submit must match the
  archive exactly for each `event_id`.
- **Logged reads.** Every real (unshuffled) score call is appended to
  `outputs/ncaab/research_261120/read_log.jsonl`. Only the first predictions file of a grid name carries a verdict; later different files under the same name get REREAD_NO_VERDICT.

## Producing predictions

Write one JSONL row per game. Field names are exact:

| field | meaning |
|---|---|
| `event_id` | ESPN event id, string |
| `season` | ESPN season year, int (2025 = 2024-25) |
| `game_date` | Eastern calendar day `YYYY-MM-DD` |
| `home_team`, `away_team` | ESPN display names as in `data/research/ncaab_market/scoreboard.jsonl` |
| `neutral` | bool, the scoreboard's neutral-site flag |
| `mu_margin` | expected home margin (home minus away points) |
| `scale_margin` | t8 scale or normal SD, > 0 |
| `dist` | `"t8"` or `"normal"` |
| `p_home_ml` | optional. If absent, the grader uses P(margin >= 1) from the margin distribution, which is the A_ml_w0 arm and NOT the served rule. Every non-A candidate should submit `grader.served_ml_blend(event_ids, mu, scale, dist)` (0.5 Elo + 0.5 margin, as served), so its moneyline rows compare like with like |
| `mu_total`, `scale_total` | optional pair. Totals are scored only when present |
| `dist_total` | optional. Defaults to `dist` |
| `games_played_min` | min of the two teams' prior current-season games, point-in-time (informational; the grader buckets on the archive's own count) |

Next to `preds.jsonl`, write a sidecar `preds.meta.json`:

```json
{
  "candidate": "B1 scale a+b|mu| v1",
  "grid_name": "B1_scale_linear_mu",
  "protocol_sha256": "72a8a9ff216c4f84cc80acd9dda6fcdac14b6cafb9131bb05fcb210ce3677813",
  "code_sha256": "<sha256 of your script>",
  "fit_seasons_by_predicted_season": {"2023": [2024, 2025, 2026], "2024": [2023, 2025, 2026],
                                      "2025": [2023, 2024, 2026], "2026": [2023, 2024, 2025]},
  "history_cutoff": "strictly before Eastern midnight of the game day",
  "data_sources": {"data/research/ncaab_market/v7ms_components.jsonl": "<sha256>"}
}
```

`grid_name` must be one of the nine names in the protocol. Any other name is scored as UNREGISTERED and gets no
verdict.

Rules a candidate must follow:

- **Point in time.** Per-game inputs use only games that tipped before Eastern midnight of the game day.
  - A date-only comparison against the same day leaks.
  - Box stats of the game being predicted are outcomes.
- **Training folds only.** Every free parameter, including scales and blend weights, is fit on the fold's three
  training seasons. The predicted season never appears among its own fit seasons.
- **Start from the existing archives.**
  - `v7ms_components.jsonl` holds per-game v6 as-of, Elo margin, the 48 ridge variants, sample and D1 flags.
  - `v7ms_loso_fit_preds.jsonl` holds v7 LOSO predictions on 2023 and 2024.
  - `grader.baseline_predictions(grader.load_archive())` gives the baselines as DataFrames. Those include outcomes, so use only the prediction columns.
- **Or drive a model day by day.** `grader.predictions_from_callable(fn, seasons)` calls
  `fn(game_date, slate, history)`:
  - `slate` holds the day's games with identity and pre-game fields only: teams, ids, conference ids, AP rank, neutral.
  - `history` holds every completed game that tipped strictly before Eastern midnight, scores included.

## Worked example: the served v7 baseline as a "candidate"

```bash
# 1. export served v7 in the submission schema (outcome-free); writes v7.jsonl and v7.meta.json
.venv/Scripts/python scripts/ncaab_research/grader.py export --model v7_served --out data/research/ncaab_market/candidates_261120/v7.jsonl

# 2. plumbing first: score on outcomes shuffled within season (no read is logged, no number means anything)
.venv/Scripts/python scripts/ncaab_research/grader.py score --pred data/research/ncaab_market/candidates_261120/v7.jsonl --meta data/research/ncaab_market/candidates_261120/v7.meta.json \
    --shuffle-seed 5 --out-dir data/research/ncaab_market/candidates_261120/shuf

# 3. the real read (logged): writes <out-dir>/v7.md and v7.json
.venv/Scripts/python scripts/ncaab_research/grader.py score --pred data/research/ncaab_market/candidates_261120/v7.jsonl --meta data/research/ncaab_market/candidates_261120/v7.meta.json
```

The served v7 exported in step 1 differs from the baseline in only two ways, so every paired delta is exactly 0:

- Its 2023 and 2024 rows are the m5r leave-one-season-out predictions. The served constants were fit on those seasons.
- Its moneyline is the served 0.5 Elo blend. The Elo half is reconstructed from `v6_p_home`, and the
  reconstruction is gated against the Elo formula: 98% of games agree within 0.002. The misses are the eight
  altitude home teams, whose Elo margin carries the altitude bonus.

Its grid name `BASELINE_v7_served` is unregistered, so it gets no verdict. From Python:

```python
import json
import sys

sys.path.insert(0, "scripts/ncaab_research")
import grader

card = grader.score("preds.jsonl", json.load(open("preds.meta.json")))
print(card["verdict"], card["paired_vs_v7"]["spread_corr_delta_vs_v7"])
open("card.md", "w").write(grader.render_markdown(card))
```

## Reading the scorecard

- **Primary, market-relative.** Slope and correlation of (actual - closing line) on (model - closing line), plus the
  ncaab-residual-rule-1 ridge run LOSO. Its K-adjusted MSE-delta bound decides "edge-eligible".
- **Candidate minus served v7**, on identical games:
  - correlation delta (mean candidates);
  - cover log-loss delta at the closing line (scale candidates);
  - moneyline log-loss delta (moneyline candidate);
  - RMSE and Brier deltas.
  Each comes with 95% and K-adjusted (alpha 0.025/K) intervals and the 2025+2026-folds point.
- **Verdict.** DEV_ELIGIBLE means "goes to the sealed confirmation window", not "beats v7". "Beats v7" needs the
  confirmation read as well. Promotion is always a separate operator decision.

## Files

- `protocol_261120.json` / `.md`: the pin.
- `grader.py`: the grader, CLI and API.
- `espn_backfill_261120.py`: resumable backfill of scoreboards for 2015-16..2021-22 and lines for 2017-18,
  2018-19, 2019-20 and 2021-22. It writes `data/research/ncaab_market/backfill_261120/`.
- `outputs/ncaab/research_261120/baseline_scorecard.{md,json}`: the baseline read.
- `outputs/ncaab/research_261120/read_log.jsonl`: every logged read.
- Tests: `tests/research/test_ncaab_grader_261120.py`
  (`.venv/Scripts/python -m pytest tests/research/test_ncaab_grader_261120.py -o addopts='' -q`).
