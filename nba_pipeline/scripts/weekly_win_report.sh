#!/bin/bash
# Weekly email report on win-predictor accuracy.
# Invoked by the com.basketballds.nba-weekly-report LaunchAgent each Friday,
# 30 minutes after the daily refresh starts; safe to run by hand.

set -u

PROJECT_ROOT="/Volumes/Data-700GB/Basketball DS/nba_pipeline"
LOG_DIR="${PROJECT_ROOT}/logs"
LOCK_DIR="${LOG_DIR}/weekly_report.lock"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*"
}

# The warehouse lives on an external volume that may not be mounted.
if [ ! -d "${PROJECT_ROOT}" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') project root not available: ${PROJECT_ROOT}" >&2
    exit 1
fi

mkdir -p "${LOG_DIR}"
exec >> "${LOG_DIR}/weekly_report.log" 2>&1

if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
    log "a weekly report run is already in progress (${LOCK_DIR}); skipping"
    exit 0
fi
trap 'rmdir "${LOCK_DIR}" 2>/dev/null' EXIT

cd "${PROJECT_ROOT}" || exit 1

if [ ! -f ".venv/bin/activate" ]; then
    log "missing virtualenv at ${PROJECT_ROOT}/.venv"
    exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

log "=== weekly win report started ==="

# The daily refresh already pulled today's team stats, so score offline.
score_status=0
python -m nba_pipeline check-wins --offline || score_status=$?
if [ "${score_status}" -eq 0 ]; then
    log "scored the win predictor against results to date"
else
    log "check-wins FAILED with exit ${score_status}; reporting on the last good snapshot"
fi

send_status=0
python -m nba_pipeline send-win-report || send_status=$?
if [ "${send_status}" -eq 0 ]; then
    log "weekly report sent"
else
    log "weekly report FAILED with exit ${send_status}"
fi

if [ "${send_status}" -ne 0 ]; then
    log "=== run finished with problems ==="
    exit 1
fi

log "=== run finished clean ==="
