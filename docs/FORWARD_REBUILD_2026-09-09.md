# All-model forward inference and incumbent replay — updated 2026-09-10

Later checkpoint: all 41 forecasts now have outcomes, and exact paired
evaluation/preparation/capture are implemented. The earlier awaiting-outcome
description below is historical. See
[September 10 paired rebuild evidence](PAIRED_REBUILD_2026-09-10.md).

The 21 first-generation candidates now have a common fixture inference and
source-capture path. This is an engineering milestone, not evidence that all
21 outperform their incumbents. The earlier MLB comparisons remain negative
or inconclusive; no profitability or promotion claim changes.

## Shared features and archived forecast inputs

Theory: historical training and upcoming-event inference must use the same
target-free feature calculation. Previously only labeled `Game` rows could
reach the generic feature builder. `Fixture`, `FeatureState`, and
`fixture_features` now calculate inputs without fabricated scores or outcomes.
The existing two-calendar-day result embargo remains, with a separate cutoff
at the actual observation date for upcoming fixtures.

Evidence and verification:

- Thirteen sports exactly reproduce the generation's saved feature rows.
- CS2's refreshed source removed `bo3:128641`, reducing 34,356 rows to 34,355
  and changing ten subsequent common rows. The archived original feature code
  and the refactored code produce identical outputs on that same refreshed
  source. This is source drift, not a refactor discrepancy. Evidence lives in
  `outputs/research/feature_refactor_cs2_drift_20260909.json`.
- All 21 artifacts pass synthetic contract inference and exact replay tests:
  `outputs/research/forward_all21_smoke_20260909_v2/report.json`. These are
  explicitly synthetic fixtures, not predictive evaluation or real games.
- Forty-one upcoming contracts from existing local MLB/NFL scoreboards were
  captured before their listed starts: ten MLB games across four markets and
  one NFL moneyline. Every prediction rebuilds exactly from the archived
  source bytes. Evidence: `outputs/research/forward_capture_20260909_v1/`,
  particularly `predictions.jsonl` and `independent_source_rebuild.json`.

The common command is `scripts/forecast_research_generation.py`. It accepts
an explicit fixture JSONL and a generation directory, and creates a new output
directory containing the source files, fitted artifacts, recipes, full named
features, prediction snapshots, and per-market coverage. Sources changing
during capture are rejected. Existing output directories are not overwritten.
Partial failures remain visible and return exit code 2; an empty request fails.

Fixtures require timezone-qualified future starts and the historical store's
exact participant IDs. Tennis uses sorted player IDs within the declared tour;
esports preserves source team1/team2 orientation. Cold starts are unavailable.
Totals/spreads require an explicit full-game period and exact numeric line;
spreads require a signed home line. NRFI requires named teams, starters, venue,
the frozen generation's training priors, and the archived first-inning source.
Its existing live feature builder uses a conservative results embargo.

The local schedules were captured, not re-fetched. Their spread/total lines
are ESPN's saved full-game lines, not newly verified executable quotes. No
execution profitability is calculated. Capturing source bytes now proves
what was used for this calculation, not when older historical rows first
became observable. `rebuild_forecast` verifies the archived source hash,
rebuilds features, checks the complete prediction snapshot, and independently
replays the artifact. Recipe metadata must also match the recipe embedded in
the hash-verified joblib file.

Failure modes addressed: target leakage from dummy completed fixtures,
orientation errors, stale source substitution, missing inputs being silently
defaulted for generic models, contract-line ambiguity, and mismatched recipes.
Rollback consists of using the preserved generation code and prior research
artifacts; no production champion configuration or existing model artifact was
changed by this work.

## NCAAF incumbent state capture

The NCAAF incumbent uses a stateful simulation engine. A seed and projected
scores cannot reproduce later games in a slate: the random-number generator
has already advanced. Each new prediction now retains its full-precision score
means, exact distribution parameters, actual pre-simulation RNG state, and the
distribution engine's source hash.

`cfb_replay.py` reconstructs all three markets, including integer-line pushes,
from these inputs. Decision validation checks model/event identity, selected
probability, pregame timestamp, and the signed line for the selected side.
These snapshots pass through the forecast logger, canonical SQLite decision
and feature payloads, XLSX export, and settlement. The incumbent replay helper
recognizes them. Historical NCAAF rows without this evidence remain
unreproducible; no RNG states or probabilities were backfilled.

This capture does not change the simulator, reset its RNG, replace coefficients,
or certify upstream historical feature availability. Existing legacy model IDs
remain as recorded, while the snapshot separately binds the actual engine
source. A changed engine source blocks replay rather than silently substituting
new code. This adds three incumbent market paths to the four learned-moneyline
paths instrumented earlier.

## Complete incumbent capture coverage — 2026-09-10

All 21 incumbent market paths now have explicit capture/replay implementations:

| Family | Markets | Retained serving state |
|---|---:|---|
| MLB/WNBA/NBA/NFL learned moneyline | 4 | Full artifact, exact features, availability transform |
| NCAAF | 3 | Means, parameters, pre-call RNG state, engine hash |
| Five esports titles | 5 | Matchup Elo state, decay/calibration settings, reference time |
| WNBA spread/total and MLB NRFI | 3 | Full artifact and exact normal/logistic inputs |
| KBO/NPB | 2 | Elo state and tie policy; win/draw probabilities distinct from settlement values |
| Soccer/tennis moneyline | 2 | Full goal rates or overall/surface Elo ratings and sample counts |
| MLB spread | 1 | Full features, formula, calibration artifact and deterministic seed inputs |
| MLB structural v10 total | 1 | Full structural artifact/features, exact line, empirical residual mapper |

The v10 capture is wired into `scripts/mlb_v10_prospective_shadow.py`, its
separate prospective runner. The main forecast CLI still contains the older
Measured Edge totals path; this work does not claim to have activated a v10
production-CLI route. V10 now refuses capture before its T-30 window or after
event start. NRFI similarly skips started fixtures instead of backdating them.

Soccer retains draw probabilities. Tennis reproduces the actual six-decimal
model rounding followed by four-decimal contract rounding. MLB spread retains
raw push probabilities and separately calibrated side values: those calibrated
sides must not be silently normalized into a joint distribution. KBO/NPB draws
pay half value, so binary win/loss calibration is inappropriate for their
stored expected settlement values.

Snapshots survive canonical SQLite storage, XLSX export, and settlement in
focused tests. They prove reproducibility of captured serving state, not
upstream historical observation provenance. Historical rows without these
snapshots remain unreproducible. No inputs, RNG states or outcomes were
backfilled. Existing artifact JSON files and champion configuration remain
preserved. Serving source files were instrumented; probability formulas remain
unchanged, apart from rejecting falsely backdated prospective captures.

The legacy ready-challenger command now reads canonical SQLite, preserves zero
probabilities, pairs exact contracts/timestamps, rejects inconsistent outcomes
and context conflicts, and requires incumbent replay before metrics. Its real
battery run returned blocked comparisons, not invented gains. Its binary
comparison branch explicitly rejects KBO/NPB settlement-value metrics. The
older `model_lifecycle.load_challenger_evidence` function is not repaired by
this change; the command bypasses it.

## Outcome scoring and fresh audit

`scripts/score_research_forward.py` now rebuilds candidate predictions from
archived feature-source bytes and artifacts before joining completed outcomes.
It checks exact participants, starts, signed lines, tennis surface orientation,
and unique NRFI team/start identity across StatsAPI and scoreboard IDs.
Incomplete events, conflicting completed scores, altered inputs, and mismatched
artifacts never receive scores. Draws and integer-line pushes remain distinct
classes. The reported vector-sum Brier convention is twice scalar binary Brier
on binary markets; reports must not mix these scales.

The verified run is `outputs/research/forward_outcome_scoring_20260910_v3/`:
all 41 prior forecasts replay and remain `AWAITING_OUTCOME` because current
local source files contain no completed matching result. The command archives
current outcome stores and code, preserves existing output directories, and
returns 2 when there is no scored evidence or an integrity failure. This is
not a claim that all games are still in progress. No outcome-source refresh,
new trades, candidate retraining or promotion occurs in this command.

The refreshed canonical-ledger audit is
`outputs/research/incumbent_comparison_20260910_capture_adapters/`. It includes
newly settled legacy contexts but no settled decision with the new complete
state snapshots yet. All 21 adapters are implemented; their absence from old
records is a data limitation. The same 371 MLB comparison events reproduce
the earlier negative/inconclusive results exactly. Non-learned captured state
can pass the replay gate without being sent into the binary moneyline economics
branch; it receives an explicit awaiting-market-pairs status instead.

Verification evidence is in `outputs/research/rebuild_verification_20260910/`.
The extracted v10 empirical mapper exactly matches the prior HEAD method on
486 line/delta cases using the unchanged artifact. Lint passes on all 57
changed/new Python files, and explicit-package-base type checking passes on
20 rebuild modules. The final full suite passes: 2,821 passed, 3 skipped, no failures (293.77
seconds). Its log, code snapshot, and hashes are recorded with `validation.json`.
Rollback can restore the instrumented source while retaining appended snapshots;
existing artifact JSON files and champion configuration remain byte-identical.

## Remaining work

Candidate inference/capture and incumbent state adapters cover all 21 markets.
End-to-end market-aware paired evaluation and executable economic comparison
remain to be completed beyond learned moneyline. Historical WNBA gaps remain;
source identities, full contract archives, fees, depth and independent closing
quotes need complete linkage. Generic generation v1 and MLB residual v2 remain
research candidates with no demonstrated reliable superiority or profitability.
Sport-specific iterations and fresh settled confirmation are still required;
the rebuild is not complete merely because capture coverage reaches 21/21.
