---
phase: quick-261121-v7
plan: 01
status: complete
branch: ncaaf-v7-261121 (worktree E:\MP-wt\ncaaf-v7)
verdict: PASS -> WIRE AS CHALLENGER (registered, not routed)
---

# quick-261121-v7 summary: cfb-structural-v7 rating core

## Result

Pinned rule `cfb-v7-holdout-rule-1`, sha256 `fd7e9d0f2a8f5bbc22e8a7b3eadb101f3577488de1cc5854c985becd81ff00d3`
(`outputs/ncaaf/cfb_v7_rule.json`, committed in dc95efe4 before the read). The holdout was read once at
2026-11-21T21:12:25Z. Report: `outputs/ncaaf/cfb_v7_report.{md,json}`.

Holdout 2024-11-15 to 2026-11-19: 1,948 games. v5 has 1,949 replay rows; one has no ESPN ids and v7 made 0
no-calls. v5's published holdout row reproduced exactly before any v7 holdout file was opened (n 1949, MAE 13.1674,
Brier 0.17197).

| arm (same games) | margin MAE | total MAE | Brier | log loss |
|---|---|---|---|---|
| v7 selected (CFBD + portal-out prior) | 12.467 | 12.892 | 0.16356 | 0.48661 |
| v7 core (no CFBD) | 12.519 | 12.930 | 0.16389 | 0.48754 |
| v5 champion | 13.173 | 12.899 | 0.17202 | 0.51214 |
| ESPN close, lined subset n 1889 (v7 12.440) | 11.843 | 12.371 | 0.15772 | 0.46961 |
| CFBD multi-book opener, n 1899 (v7 12.462) | 12.001 | 12.513 | 0.15778 | 0.47025 |

Paired v7 minus v5 (date-clustered 95% CI): margin MAE -0.706 [-0.950, -0.428], Brier -0.0085 [-0.0128, -0.0043],
log loss -0.0255 [-0.0364, -0.0148]. The core arm also passes (MAE -0.654 [-0.890, -0.398]), which the rule requires.

Early season (weeks 0-4, n 645): MAE -1.061 [-1.621, -0.512], log loss -0.0359. v5's actual-minus-model bias there is
+4.83 against v7's -0.84. CFBD minus core in weeks 0-4 is -0.207 [-0.354, -0.046]. Weeks 5-14: -0.610. Postseason
(n 93): v7 is +0.52 [-0.38, +1.45] worse, and CFBD minus core is significantly worse there (+0.28).

FBS vs FCS (n 254): MAE -3.08, because v5 under-projects favourites by 9.4 points.

The market remains better. The ESPN close beats v7 by 0.60 [0.37, 0.84] MAE and the opener beats it by 0.46
[0.21, 0.71]. Measured against ESPN closes, all DESIGN-INFORMED: the selected arm shows NO_MARKET_SKILL on spreads
(corr +0.031 [-0.009, 0.068]; orthogonalised slope +0.104 [-0.013, 0.223]; ATS 3+ 489-448, 52.2%; 7+ 179-135, 57.0%
[51.5, 62.4]). It also shows none on totals (corr 0.000; ATS 3+ 48.5%). The core arm narrowly met the pinned
MARKET_SKILL criterion on spreads (corr CI low +0.004, orthogonalised CI low +0.027). That is one of 8 market cells,
on lines read many times before, so it is a hypothesis for the 2027 gate, not evidence.

## What was built

- `src/model_prediction/features/cfb_features_v7.py` is the single rating core. It fits PIT ridge ratings for
  offence and defence on 7 garbage-filtered metrics. It shrinks toward a prior centre, rho x last season's final
  plus CFBD gammas, with a fitted strength lambda. League terms shrink toward last season. A game counts only once
  kickoff + 5 h <= decision. It fails closed with NO_CALL_* codes.
- `scripts/cfb_v7_fit.py` runs prep, walk, fit, consensus, v5repro, dryrun, pin, holdout and artifact. Holdout
  rows sit in separate files, and the read marker is in `data/research/ncaaf_v7_work/holdout_read.json`.
- `src/model_prediction/models/cfb_structural_v7.py` is the serving module (pmfs, `price_game`,
  `check_pbp_complete`). `tests/test_cfb_structural_v7.py` holds the parity, PIT, fail-closed and coherence tests
  (7 pass).
- `config/models/cfb-structural-v7.json` is the artifact, `artifact_hash`
  d4d6ee9d1fda5f1166861d94ac84036c4fd0353c75275225d3ed499c89feb305.
- `config/production.yaml` gets a registry entry, `allowed_models` and `artifact_map` only. `challengers.NCAAF`
  stays v6.2 because the registry maps one model per sport/market. Champions are untouched, and so are the
  auto-buyer and v5.
- `docs/CFB_V7_2027_GATE.md` and `outputs/ncaaf/cfb_v7_2027_gate.json` hold the 2027 gate, sha256 of the LF bytes
  `0f79b19884dae28ab0acc693b95fea1e293538f0992c8ba1aec3caf13a9b8f59`.

FBS-FBS only (n 1,694): MAE -0.350 [-0.581, -0.097], Brier -0.0080, log loss -0.0207. FBS-FCS games are 13% of the
sample and carry about 57% of the headline gain. An FBS-FBS-only early-season cut was not pinned and was not run.

## Post-read changes (serving only; no model number changed)

- `price_game` now requires a season-level `classes` map and fails closed with NO_CALL_V7_NO_CLASS_LABEL. Before
  this, a week-0 FBS team whose class came only from admitted rows silently got the FCS offset. With classes
  derived from past rows only, 7 of 12 fixture cases differed; they now no-call. A new test checks that
  truncated-history pricing equals full-history pricing when the class map is supplied.
- `holdout` refuses to run when `outputs/ncaaf/cfb_v7_report.json` exists in the tree or in history, because the
  read marker sits in an untracked directory.
- FBS/FCS class came from pbp `game_class` (FBS = plays any FBS-FBS game that season), not from
  `cfb_team_identity`.
- Windows are the v5 artifact's explicit date boundaries, not a `chronological_split` call. The counts match the
  recheck: 5,704 / 1,858 / 1,949.

## Untracked inputs (keep; not in git)

- `E:\MP-wt\ncaaf-v7\data\research\ncaaf_v7_work\` holds the walk files (train/validation and `.holdout`),
  `holdout_read.json`, `v5_replay_rows.jsonl` (the only durable copy of the v5 recheck replay rows, sha256 prefix
  68caa21f0fc0f4e3) and the dry-run report.
- `E:\MP\data\research\ncaaf_v7\team_game_pbp.jsonl`, `E:\MP\data\research\ncaaf_v6\history_snapshot.jsonl` and
  `espn_lines.jsonl`, plus `E:\MP\data\research\ncaaf_v7\cfbd\{team_season_priors,lines}.jsonl`. The rule file
  pins their sha256.

## Not done

- `docs/ROADMAP.md`, `docs/INDEX.md` and `docs/DEBUG.md` were not updated; other sessions edit them.
- The PLAN predates the CFBD arms.
- Two failures in `tests/test_qualification_integrity.py` (MLB) already fail at HEAD 28a4a03e.

## Caveats

- CFBD values are current-revised (`as_published_verified` false), and 2026 was fetched mid-season, so the CFBD
  arm's edge over core is an upper bound. That edge is -0.05 overall (CI includes 0) and -0.21 in weeks 0-4.
- Portal-out's gamma was fit on 2021 alone, the only train season with portal data.
- Team class (FBS/FCS) is season-level schedule metadata.
- The stage-2 turnover-rate coefficient has the wrong sign (+33 points per unit rate). It is small in effect and
  was left as fitted.
- The half-life grid (none, 45 days) was not extended although 45 won; the difference was about 0.015 MAE.
- v5's replay admits same-day earlier games without a buffer, which is a small information advantage to v5.

## Live serving gaps (not wired)

- `scripts/cfb_espn_pbp_crawl.py crawl` can run incrementally. The manifest skips finished events and picks up
  completed games from `data/processed/ncaaf/games.jsonl`. But it lives in `scripts/`, writes under the repo's
  `data/research/`, not `RuntimePaths`, and holds a single-instance lock.
- New FCS-vs-FCS games after the 2026-11-14 v6 snapshot are never discovered, because live ingest has no group 81.
  FCS ratings therefore go stale mid-season.
- `scripts/cfb_pbp_features.py` rebuilds every raw file each run.
- Serving needs this season's CFBD preseason rows (the selected arm), or it emits NO_CALL_V7_NO_CFBD_PRIOR.
- There is no daily step and no Flat-pick path. Routing needs an operator decision: replace v6.2 in
  `challengers.NCAAF`, or add multi-challenger support.
