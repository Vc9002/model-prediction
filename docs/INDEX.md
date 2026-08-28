# Central Documentation Directory (docs/INDEX.md)

Welcome to the unified documentation directory for the `model-prediction` platform. All project documentation, architecture specifications, research dives, operational manuals, and audit histories are centrally cataloged here.

---

## 1. Core Operating Truth & Verification

| Document | Purpose | Audience / When to Use |
| :--- | :--- | :--- |
| [`MASTER.md`](MASTER.md) | **Comprehensive Source of Truth**: Deep running log of all real bugs, fixes, session notes, and system state. | Primary reference for codebase state & history |
| [`PROJECT_STATUS.md`](PROJECT_STATUS.md) | **Operational Status Entry Point**: High-level verdict, verified test metrics, and active champions. | First document to read for current health |
| [`DEBUG.md`](DEBUG.md) | **Exact Reproduction & Audit Ledger**: Repro commands, incident logs, line refs, and defect triage. | When diagnosing issues or reproducing bug states |
| [`SYSTEM_DEFECTS_AND_GAPS_AUDIT.md`](SYSTEM_DEFECTS_AND_GAPS_AUDIT.md) | **Comprehensive Defect & Risk Audit**: Detailed catalog of serving landmines, data gaps, PIT integrity, and technical debt. | High-priority audit of system gaps & risks |
| [`ROADMAP.md`](ROADMAP.md) | **Active Task Queue**: consolidated engineering, dashboard, portfolio, and research roadmap. | Planning next development & research tasks |
| [`CHECKLIST.md`](CHECKLIST.md) | **Operational Verification Protocols**: Step-by-step checklists for daily runs, audits, and promotion. | Executing release and verification gates |
| [`CHANGELOG.md`](CHANGELOG.md) | **Release History**: Chronological log of major updates, new sports, and architectural changes. | Reviewing version evolution over time |

---

## 2. System Architecture & Production Operations

| Document | Purpose |
| :--- | :--- |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | System design, data ingestion, feature stores, model registry, and order pipelines. |
| [`LEDGER_ROUTING.md`](LEDGER_ROUTING.md) | Multi-tier ledger topology (Main, Flat, Research, Gated Research) and workbook separation. |
| [`PRODUCTION.md`](PRODUCTION.md) | Production canary, run supervisor, launchd scheduling, and fail-closed safety gates. |
| [`FEATURE_REGISTRY.md`](FEATURE_REGISTRY.md) | Canonical registry of engineered features and their strict Point-in-Time (PIT) contracts. |
| [`CHAMPION_CHALLENGER.md`](CHAMPION_CHALLENGER.md) | Promotion gating, bootstrap confidence intervals, and shadow evaluation framework. |
| [`CONSOLIDATION.md`](CONSOLIDATION.md) | Record of the runtime consolidation and external runtime root migration. |
| [`BURN_IN.md`](BURN_IN.md) | Verification and health checklist during multi-day operational burn-in periods. |

---

## 3. Research, Quantitative Literature & Modeling Plans

| Document | Purpose |
| :--- | :--- |
| [`RESEARCH_LITERATURE_DIVE_4_2026-08-20.md`](RESEARCH_LITERATURE_DIVE_4_2026-08-20.md) | **Literature Dive #4**: Plate-Appearance Monte Carlo, Catcher Framing, and 15–30m Lineup Alpha Window. |
| [`V8_REPRODUCTION.md`](V8_REPRODUCTION.md) | Forensic reproduction report and holdout cohort definitions for MLB v8. |
| [`FEATURE_MODEL_AUDIT.md`](FEATURE_MODEL_AUDIT.md) | Comprehensive model and feature audit across all active sport models. |
| [`PRODUCTION_MODEL_AUDIT.md`](PRODUCTION_MODEL_AUDIT.md) | Multi-sport production artifact and calibration verification audit. |
| [`V8_PARITY_BASELINE_2026-08-17.md`](V8_PARITY_BASELINE_2026-08-17.md) | MLB v8 parity baseline and feature parity verification sample. |
| [`RESEARCH_LITERATURE_DIVE_3_2026-08-17.md`](RESEARCH_LITERATURE_DIVE_3_2026-08-17.md) | **Literature Dive #3**: Prior quantitative modeling and sport-specific feature research. |
| [`RESEARCH_LITERATURE_DIVE_2_2026-08-17.md`](RESEARCH_LITERATURE_DIVE_2_2026-08-17.md) | **Literature Dive #2**: Quantitative methods and statistical modeling surveys. |
| [`RESEARCH_LITERATURE_DIVE_2026-08-17.md`](RESEARCH_LITERATURE_DIVE_2026-08-17.md) | **Literature Dive #1**: Baseline quantitative sports betting research review. |
| [`DISTRIBUTION_MIGRATION_PLAN.md`](DISTRIBUTION_MIGRATION_PLAN.md) | Migration from decoupled GLMs to unified joint score distributions (Poisson / Negative Binomial). |

---

## 4. Historical & League Archives (`docs/archive/`)

Historical briefs, decommissioned feature backlogs, and previous research iterations are preserved in [`docs/archive/`](archive/):
- **League Briefs**: [`docs/archive/leagues/`](archive/leagues/) (MLB, NBA, WNBA, NFL, Soccer, Tennis, Esports, KBO, NPB, World Cup)
- **Settlement & Audit Archives**: [`SETTLEMENT_GAP.md`](archive/SETTLEMENT_GAP.md), [`WORKING_TREE_CLASSIFICATION.md`](archive/WORKING_TREE_CLASSIFICATION.md)
