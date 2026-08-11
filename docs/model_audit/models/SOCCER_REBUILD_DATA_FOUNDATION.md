# Rebuild data foundation: Soccer

Session date 2026-08-11, branch `rebuild/soccer-model-curation-v1` from
`origin/main`. Pure data-foundation groundwork for a future curated
rebuild-native soccer model (`soccer-poisson-dc-rebuild-v1` or similar) --
**no model-fitting code was written, `_BasicEloAdapter`/`sport_adapter.py`
were not touched**. Raw evidence: `outputs/rebuild/soccer/data_audit.json`.

This document is about the **rebuild track only**
(`src/model_prediction/rebuild/soccer/`, `data/rebuild/`). It does not
touch, and has no bearing on, the incumbent `soccer-poisson-dc-v1` model,
which stays exactly as-is in the incumbent track (see
`docs/model_audit/models/SOCCER_POISSON_DC_V1.md` and
`docs/model_audit/features/SOCCER.md` for that model's own audit -- not
re-derived here).

## What existed before this session

Nothing. `data/rebuild/` did not exist at all prior to this session's
backfill -- confirmed by `find data/rebuild` returning "No such file or
directory" before the first `rebuild-data backfill --sport soccer` call.
Every number below comes from real network collection performed this
session.

## What was backfilled this session

Real ESPN Site v2 network calls via `rebuild-data backfill --sport soccer`
(invoked through `model_prediction.rebuild.data_cli.run`, the same code
path the CLI itself uses -- looped in-process across dates to avoid
per-call interpreter startup, not a bypass of the sanctioned entrypoint).

- **Leagues**: `eng.1` (EPL), `esp.1` (La Liga), `ger.1` (Bundesliga),
  `ita.1` (Serie A), `fra.1` (Ligue 1), `usa.1` (MLS) -- every league code
  the rebuild's `ESPNSoccerProvider` currently supports (see gap finding
  below).
- **Windows**: 2026-04-20 -- 2026-05-24 (season-tail of the 2025-26 top-5
  European league season, still in-season for MLS) and 2026-07-21 --
  2026-08-10 (current/recent; MLS in-season, top-5 European leagues in
  summer break/preseason ahead of their 2026-27 kickoff). 56 calendar
  dates total, 6 leagues each = 336 ESPN HTTP requests, all `AVAILABLE`
  (zero transport failures, zero `DEGRADED`/`UNAVAILABLE` responses).
- **StatsBomb Open Data**: reported `POLICY_BLOCKED` on every call, as
  designed -- no network request made (license prohibits commercial
  exploitation; this is enforced code, not a workaround target).
- **football-data.org**: not attempted. `FOOTBALL_DATA_TOKEN` is not set
  in this environment; per the documented fail-closed behavior this would
  have reported `TOKEN_NOT_CONFIGURED`/`UNAVAILABLE` per source rather than
  erroring, but the task scope explicitly says not to work around the
  missing token, so no attempt was made.

## Real coverage numbers

- **395 rows written = 395 distinct matches** (no duplicate observations;
  `rebuild-data audit --sport soccer` confirms `duplicate_observations: 0`).
- **128 distinct teams** by ESPN numeric `team_id`, matching real-world
  league membership exactly: EPL 20, La Liga 20, Serie A 20, Bundesliga 18,
  Ligue 1 18, MLS 32 (two-conference format).
- **Date range** (`event_start_utc`): 2026-04-20 through 2026-08-08.
- **Per-league row counts**: eng.1 52, esp.1 70, ger.1 36, ita.1 51,
  fra.1 39, usa.1 147 (MLS highest since it was in-season across both
  windows; the European five only contributed their season-tail).
- **394/395 completed** (`STATUS_FULL_TIME`); 1 `STATUS_ABANDONED`
  (Nantes vs. Toulouse, fra.1, 2026-05-17) -- flagged below as a real
  data-quality edge case, not hypothetical.
- **Official `rebuild-data audit --sport soccer`**: `status: DEGRADED`
  (this project's audit convention for "all structural/PIT/rights checks
  passed, but the data is inherently research-only" -- not an error state;
  `missing_provenance_columns`, `null_provenance_rows`,
  `malformed_raw_snapshot_hash_rows`, `timestamp_violations`, and
  `duplicate_observations` are all `0`, `rights_policy_valid: true`).

## Real bugs / gaps found

### 1. Rebuild's ESPN soccer provider supports only 6 of the incumbent's 19 configured leagues

`src/model_prediction/rebuild/providers/soccer_espn.py`'s
`ESPN_SOCCER_LEAGUES` dict hardcodes exactly six codes (`eng.1`, `esp.1`,
`ger.1`, `ita.1`, `fra.1`, `usa.1`). `ESPNSoccerProvider._league()` rejects
anything else with `"unsupported ESPN soccer league: <value>"` before any
network call is made. The incumbent's own
`src/model_prediction/data_sources/espn.py` `LEAGUE_PATHS` dict already
has working codes for all 19 leagues `config/model.yaml`'s `SOCCER.leagues`
lists (`uefa.champions`, `bra.1`, `bra.2`, `arg.1`, `arg.2`, `col.1`,
`chi.1`, `uru.1`, `ecu.1`, `per.1`, `conmebol.sudamericana`,
`fifa.friendly`, `club.friendly`), but that mapping is not reused by the
rebuild's provider -- confirmed by code inspection, not just by a failed
CLI call.

**Leagues currently unreachable via `rebuild-data backfill --sport
soccer`**: `UCL`, `BRASILEIRAO`, `BRAZIL_SERIE_B`, `ARGENTINA`,
`ARGENTINA_2`, `COLOMBIA`, `CHILE`, `URUGUAY`, `ECUADOR`, `PERU`,
`SUDAMERICANA`, `FRIENDLIES`, `CLUB_FRIENDLIES` -- 13 of 19, essentially
all of South America plus continental cups and friendlies. This session
covered every league the rebuild *can* currently reach (5 of the
incumbent's configured leagues, plus Ligue 1 which the rebuild supports
but the incumbent's config does not list). Widening
`ESPN_SOCCER_LEAGUES` to mirror `data_sources/espn.py`'s `LEAGUE_PATHS` is
a small, well-scoped fix for a future pass -- not done here, since it is
provider code, not data-foundation collection, and the task scope was
explicitly collection/audit only.

### 2. Backfilled data is `capture_time_only` -- not yet walk-forward-simulation-safe

Every normalized row carries `availability_basis: "capture_time_only"` and
`observed_at_utc`/`available_at_utc` stamped at *this session's* capture
time (2026-08-11), regardless of the real historical `event_start_utc`
(as far back as April). This is not a soccer-specific defect -- it is the
same project-wide, deliberately documented pattern used by
`mlb_v3`/`wnba`/`nfl`/`tennis` foundations (`docs/rebuild/MLB_V3_DATA.md`:
*"no claim that capture-time historical snapshots are retrospective PIT
data"*). But it has a concrete, previously-undocumented-for-soccer
consequence worth spelling out for whoever fits the model next:
`soccer/pit.py`'s `eligible_matches_as_of()`/`prior_team_matches_as_of()`
gate strictly on `observed_at_utc <= decision_time`. Run that function with
any `decision_time_utc` before this session's capture run, and it returns
**zero rows** -- the entire 395-match backfill is invisible to a walk-
forward simulation that respects the live-serving PIT contract literally.

This is a real, open design question for the model-fitting phase (not
resolved anywhere in this repo -- `rebuild-model` reports
`NOT_IMPLEMENTED` for every sport, so there is no existing precedent to
follow for any sport, not just soccer):

- **Option A**: train using `event_start_utc` as the real-world PIT
  boundary for *completed* matches (`completed == True`). A final score,
  once the match is over, is an immutable historical fact independent of
  when this repository happened to scrape it -- defensible for training
  purposes even though it is not proof the row would have been available
  for *live serving* on that historical date.
- **Option B**: commit to sustained day-by-day prospective collection
  (e.g. a scheduled daily `rebuild-data backfill --sport soccer --date
  <today>` run) to build genuine per-day capture provenance over weeks/
  months, matching the live-serving contract literally at the cost of a
  long wait before enough history accumulates.

Neither is chosen here; this document surfaces the decision, it does not
make it.

### 3. One real data-quality edge case: abandoned match with a non-null 0-0 score

`STATUS_ABANDONED` match id `746714` (Nantes vs. Toulouse, fra.1,
2026-05-17) has `completed: false` but `home_score`/`away_score` = `0`/`0`
(not null). Today's schema is already safe against this *if* every
consumer filters on `completed == True` (which correctly excludes this
row) -- but a future consumer that reads scores without checking
`completed` would silently treat this as a genuine 0-0 draw. Flagged
because it is real observed data from this session, not a hypothetical.
No fix needed in this pass; a note for whoever builds the training-row
filter.

## Team-identity / collision check (the incumbent's known bug, checked against rebuild data)

The incumbent has a documented, already-fixed bug (`DEBUG.md`, 2026-07-31):
`soccer_forward.py`'s `_team_matches_title` fuzzy word-matching collided
"Manchester United" with "Manchester City" by treating both "city" and
"united" as strippable generic words, reducing both to `{"manchester"}`.

**That bug class cannot occur in the rebuild-normalized data collected
this session**, for a structural reason, not a lucky sample: rebuild team
identity is keyed by ESPN's numeric `team_id` (`home_team_id`/
`away_team_id`), never by name-string matching. Checked directly against
all 395 matches / 128 teams:

- 0 team IDs map to more than one distinct name.
- 0 team names map to more than one distinct ID.
- 0 `source_match_id` values span more than one competition.
- 0 rows have `home_team_id == away_team_id`.

**Caveat, not a clean bill of health project-wide**: the incumbent's bug
lives specifically in a *market-matching* layer (fuzzy-matching a team
name against a live Polymarket contract title), which the rebuild has not
built yet -- soccer's `match_markets`/`predict`/`decide` stages are
`STAGE_NOT_IMPLEMENTED` (routed through `_BasicEloAdapter` today, a
deliberate foundation stage per `docs/model_audit/ARCHITECTURE_CORRECTION.md`,
not touched in this pass). This check only confirms the rebuild's
*storage/identity* layer is collision-free today. Once a real
`match_markets` stage is eventually built for soccer, the same class of
fuzzy-name-collision risk reappears at that new seam and will need its own
explicit guard -- this finding does not retire that future risk, it just
confirms today's normalized data has none of it.

## Volume vs. the incumbent's own qualification gate

The incumbent's `soccer-poisson-dc-v1` gates `model_inputs_valid` on
`MINIMUM_TEAM_GAMES = 10` (`cli.py:1635,1856`). In this session's sample:
**108 of 128 team/competition rows fall below that threshold** -- average
4-9 games per team across the 5-week season-tail window (MLS teams
average ~9 across the two combined windows since MLS was in-season
throughout; the five European leagues, only covered by their season-tail,
average 4-7). This volume is enough for structural/distributional
research (sanity-checking goal-rate shape, EWMA prior plausibility) but
is far short of what `qualify_soccer_poisson_model`/
`qualify_soccer_total_model`-style walk-forward validation would need.

## Go/no-go recommendation for the next phase (model fitting)

**No-go for immediate model fitting on this corpus as collected.**
Recommend the following before starting real Dixon-Coles fitting work:

1. **Resolve the capture-time-only PIT question above (finding #2)**
   explicitly and in writing before writing any training-row filter --
   this determines whether the *existing* 395-match backfill is usable at
   all for training, or whether it can only ever be used for structural
   sanity checks while prospective collection builds a genuinely
   walk-forward-safe corpus in parallel.
2. **Widen league coverage** (finding #1) by extending
   `ESPN_SOCCER_LEAGUES` to the incumbent's full 19-league set (or an
   explicitly-scoped subset) before committing to a "safe leagues" list
   for training -- 13 of 19 incumbent leagues are currently invisible to
   the rebuild entirely, which would otherwise silently narrow the
   candidate's training distribution relative to the incumbent it's meant
   to eventually compare against.
3. **Expand the historical window substantially** once (1) is resolved --
   a single 5-week season-tail plus 3 weeks of MLS is nowhere near enough
   volume per team (108/128 team-competition pairs below even the
   incumbent's own 10-game minimum). A multi-season backfill (2-3 completed
   seasons per major league, matching the incumbent's unbounded-history
   design per `docs/model_audit/features/SOCCER.md`'s "History window /
   competition pooling" entry) is the realistic target, not a few more
   weeks.
4. **football-data.org remains unattempted** (no token configured) --
   worth deciding deliberately whether to acquire one, since it could
   supply an independent, differently-sourced second opinion on scores/
   fixtures that ESPN alone cannot provide, and its rights profile
   (`subscription_required: true`, `attribution_required: true`) is
   already wired and ready to use the moment a token exists.

What's already solid and does **not** need rework: the normalized schema,
provenance/rights bookkeeping, and content-addressed storage are all
clean -- zero PIT-column violations, zero malformed hashes, zero duplicate
observations, zero team-identity collisions, correct rights fail-closed
behavior for StatsBomb, and a working (if narrow) ESPN collection path.
The foundation code itself needs widening (leagues) and a PIT-strategy
decision (capture-time question), not a rewrite.

## Reproduction

```bash
cd /Users/vincentc9002/model-prediction-soccer-curation
PYTHONPATH=src:. /Users/vincentc9002/.venvs/model-prediction/bin/python -m \
  model_prediction.rebuild.data_cli backfill --sport soccer --date 2026-08-09 \
  --espn-league eng.1 --espn-league esp.1 --espn-league ger.1 \
  --espn-league ita.1 --espn-league fra.1 --espn-league usa.1

PYTHONPATH=src:. /Users/vincentc9002/.venvs/model-prediction/bin/python -m \
  model_prediction.rebuild.data_cli audit --sport soccer
```

Full machine-readable evidence: `outputs/rebuild/soccer/data_audit.json`.
