#!/bin/bash
# Rebuild shadow scheduler entrypoint.
# Called every 3 hours by launchd via
#   ~/Library/LaunchAgents/com.modelprediction.rebuild-shadow.plist

set -euo pipefail

REPO_ROOT="${MODEL_PREDICTION_REPO_ROOT:-/Users/vincentc9002/model-prediction}"
RUNTIME_ROOT="${MODEL_PREDICTION_RUNTIME_ROOT:-/Users/vincentc9002/model-prediction-runtime}"

mkdir -p "${RUNTIME_ROOT}/logs"

VENV_PYTHON="${REPO_ROOT}/.venv/bin/python"
if [ ! -x "${VENV_PYTHON}" ]; then
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] ERROR: venv python not found at ${VENV_PYTHON}" >&2
    exit 1
fi

cd "${REPO_ROOT}"
export MODEL_PREDICTION_REPO_ROOT="${REPO_ROOT}"
export MODEL_PREDICTION_RUNTIME_ROOT="${RUNTIME_ROOT}"

exec "${VENV_PYTHON}" -m model_prediction.cli forecast --all --log
