# V10 prospective audit correction

The daily operational audit could report PASS for a missing or empty ledger,
silently discard malformed JSON, and report PASS despite orphaned settlements.
Non-object JSON records could also crash the audit. These behaviors obscured
whether prospective confirmation evidence was being collected.

The audit now reports NO_DATA when no predictions are available, and
FAIL_INTEGRITY for malformed or unknown records and orphaned settlements.
Integrity failures take precedence over NO_DATA. Valid prediction records keep
their existing behavior. This is a reporting correction, not a change to frozen
model parameters, confirmation thresholds, or production execution.

Verification: eight new negative cases failed against the prior implementation;
all 15 tests in test_mlb_v10_audit_boundary.py and test_mlb_structural_v10.py pass
after the correction. Ruff passes. The existing operational-audit test now
isolates its ledger and report paths rather than writing the live research report.

Current checkout audit: NO_DATA, zero predictions. The configured ledger
data/point_in_time/mlb_v10_prospective_ledger.jsonl is absent. A filename search
under the external model-prediction-runtime root found no corresponding v10
prospective ledger or predictions file. Repository references to the capture
runner and append function occur only in the dry-run pipeline. This does not
establish whether an external collector exists under another identity.

Next priority: establish and verify actual prospective capture before claiming
confirmation progress. Do not reconstruct past predictions as prospective rows.
No accuracy improvement or production readiness is established by this fix.
