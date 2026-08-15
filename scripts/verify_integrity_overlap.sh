#!/bin/bash
# verify_integrity_overlap.sh — consolidation I2: run BOTH audit-chain
# verifiers during the dual-write overlap period.
#
#   1. legacy chain:  python -m model_prediction.cli verify-chain
#                     (data/events.jsonl, repo-local)
#   2. SQLite chain:  python -m model_prediction.ledger_parity verify-integrity
#                     (ledger_events hash chain; resolves through
#                     RuntimePaths — honors MODEL_PREDICTION_RUNTIME_ROOT)
#
# Exit 0 only when both are green. The legacy CLI prints its verdict as
# JSON and always exits 0, so greenness for it comes from parsing
# chain_intact, not from its exit code; the SQLite side's exit code is
# authoritative (and its output is echoed for the record).
set -euo pipefail
cd "$(dirname "$0")/.."

PY="env PYTHONPATH=src:. .venv/bin/python"

FAIL=0

if [ -n "${MODEL_PREDICTION_RUNTIME_ROOT:-}" ]; then
    echo "SQLite store root: $MODEL_PREDICTION_RUNTIME_ROOT (MODEL_PREDICTION_RUNTIME_ROOT set)"
else
    echo "SQLite store root: repo data/ (MODEL_PREDICTION_RUNTIME_ROOT unset)"
fi
echo

echo "=== legacy verify-chain (data/events.jsonl) ==="
LEGACY_OUT="$($PY -m model_prediction.cli verify-chain 2>&1)" || {
    echo "verify-chain errored"
    echo "$LEGACY_OUT" | tail -5
    exit 1
}
LEGACY_INTACT="$($PY -c 'import json, sys; print(json.load(sys.stdin)["chain_intact"])' <<<"$LEGACY_OUT")"
LEGACY_LINES="$($PY -c 'import json, sys; print(json.load(sys.stdin)["audit_lines"])' <<<"$LEGACY_OUT")"
if [ "$LEGACY_INTACT" = "True" ]; then
    echo "legacy chain: OK ($LEGACY_LINES events)"
else
    echo "legacy chain: BROKEN"
    echo "$LEGACY_OUT" | grep -E "break_count|chain_intact|audit_lines" || true
    FAIL=1
fi
echo

echo "=== SQLite verify-integrity (ledger_events) ==="
SQLITE_CODE=0
SQLITE_OUT="$($PY -m model_prediction.ledger_parity verify-integrity 2>&1)" || SQLITE_CODE=$?
echo "$SQLITE_OUT"
if [ "$SQLITE_CODE" -ne 0 ]; then
    FAIL=1
fi
echo

if [ "$FAIL" -ne 0 ]; then
    echo "OVERLAP INTEGRITY: NOT GREEN"
    exit 1
fi
echo "OVERLAP INTEGRITY: GREEN (legacy + SQLite)"
