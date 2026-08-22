#!/bin/sh
# Nightly runtime-root backup + offsite copy (ops brainstorm #2, 2026-08-17;
# wired 2026-08-22).
#
# Two legs, in order:
#   1. scripts/backup_runtime_databases.py — hot, integrity-checked, local
#      copies of the canonical SQLite stores into <runtime_root>/backups.
#      Read-only against the live databases (sqlite3 online backup API);
#      never contends with the daily/production/rebuild-shadow schedulers.
#   2. rsync of that directory to iCloud Drive — the offsite leg. Chosen
#      because it needs zero new credentials/accounts (this Mac is already
#      signed in) and iCloud syncs the copy off this machine automatically.
#      If this ever moves to rclone/S3/a real bucket, replace step 2 only;
#      step 1's local backups stay the source of truth for it to read from.
#
# --delete keeps the offsite copy mirroring local retention (14 timestamped
# copies per db, pruned by backup_runtime_databases.py) rather than growing
# unbounded.

set -eu

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_ROOT="${MODEL_PREDICTION_RUNTIME_ROOT:-$HOME/model-prediction-runtime}"
LOCAL_BACKUP_DIR="$RUNTIME_ROOT/backups"
OFFSITE_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/model-prediction-offsite-backups"

cd "$REPO_ROOT"
PYTHONPATH="src:." MODEL_PREDICTION_RUNTIME_ROOT="$RUNTIME_ROOT" \
  "$REPO_ROOT/.venv/bin/python" scripts/backup_runtime_databases.py

mkdir -p "$OFFSITE_DIR"
rsync -a --delete "$LOCAL_BACKUP_DIR/" "$OFFSITE_DIR/"
echo "offsite sync complete: $OFFSITE_DIR"
