#!/bin/bash
# Rebuild shadow scheduler entrypoint.
# Called every 3 hours by launchd via
#   ~/Library/LaunchAgents/com.modelprediction.rebuild-shadow.plist
#
# Runs the ISOLATED rebuild pipeline (rebuild-shadow CLI, shadow-only,
# no live execution) once per supported sport for today's ET date. This
# is what writes the rebuild shadow.db the dashboard's Rebuild Shadow
# view reads. It deliberately does NOT invoke the incumbent
# `model_prediction.cli forecast` — that is the incumbent daily job's
# job (run_daily.sh / com.modelprediction.daily), and mixing the two
# pipelines in one script violates the rebuild/incumbent isolation
# boundary (docs/rebuild/ARCHITECTURE.md).

set -uo pipefail

# First output line, deliberately before anything can fail silently:
# a zero-output failed run is undiagnosable (seen once, 2026-08-15T23:15Z).
echo "[start] rebuild-shadow $(date -u '+%Y-%m-%dT%H:%M:%SZ')"

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

RUN_DATE=$(TZ=America/New_York date +%Y-%m-%d)
# Late horizon matches docs/rebuild/OPERATIONS.md's standard smoke shape.
HORIZON="late"
# Only run sports config/rebuild.yaml currently enables — the CLI treats a
# disabled sport as an error, and a disabled sport is a deliberate state,
# not a per-cycle failure to retry every 3 hours.
SPORTS=$("${VENV_PYTHON}" - <<'PY'
import yaml
from pathlib import Path
cfg = yaml.safe_load(Path("config/rebuild.yaml").read_text())
print(" ".join(
    sport for sport, entry in cfg.get("sports", {}).items()
    if isinstance(entry, dict) and entry.get("enabled", True)
))
PY
)

# An empty SPORTS list is a config-read failure (or every sport disabled),
# not a legitimate "nothing to do" — silently exiting 0 here would report
# the whole rebuild-shadow pipeline healthy while it runs nothing.
if [ -z "${SPORTS}" ]; then
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] rebuild-shadow: no enabled sports derived from config/rebuild.yaml — exiting non-zero" \
        >> "${RUNTIME_ROOT}/logs/rebuild.log"
    exit 1
fi

# One sport failing must not stop the others (same per-sport independence
# convention as run_daily.sh); a non-zero exit at the end signals partial
# failure to launchd.
FAILED=0
for sport in ${SPORTS}; do
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] rebuild-shadow ${sport} ${RUN_DATE} ${HORIZON}" \
        >> "${RUNTIME_ROOT}/logs/rebuild.log"
    if ! "${VENV_PYTHON}" -m model_prediction.rebuild.cli \
        --sport "${sport}" --date "${RUN_DATE}" --horizon "${HORIZON}" \
        >> "${RUNTIME_ROOT}/logs/rebuild.log" 2>&1; then
        echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] rebuild-shadow ${sport} FAILED" \
            >> "${RUNTIME_ROOT}/logs/rebuild.log"
        FAILED=1
    fi
done

exit ${FAILED}
