#!/bin/bash
# Daily refresh of the current NBA season, followed by a data-quality check.
# Invoked by the com.basketballds.nba-refresh LaunchAgent; safe to run by hand.

set -u

PROJECT_ROOT="/Volumes/Data-700GB/Basketball DS/nba_pipeline"
LOG_DIR="${PROJECT_ROOT}/logs"
LOCK_DIR="${LOG_DIR}/daily_refresh.lock"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*"
}

# The warehouse lives on an external volume that may not be mounted.
if [ ! -d "${PROJECT_ROOT}" ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') project root not available: ${PROJECT_ROOT}" >&2
    exit 1
fi

mkdir -p "${LOG_DIR}"
exec >> "${LOG_DIR}/daily_refresh.log" 2>&1

# Shot charts take hours; never start a second run on top of a live one.
if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
    log "a refresh is already running (${LOCK_DIR}); skipping this run"
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

STARTED_AT="$(python -c 'from datetime import datetime, timezone; print(datetime.now(timezone.utc).isoformat())')"
log "=== refresh started (${STARTED_AT}) ==="

refresh_status=0
python -m nba_pipeline refresh --force || refresh_status=$?
if [ "${refresh_status}" -eq 0 ]; then
    log "refresh finished"
else
    log "refresh FAILED with exit ${refresh_status}"
fi

# Run the check either way: a partial refresh still needs to be reported on.
check_status=0
python -m nba_pipeline check --since "${STARTED_AT}" || check_status=$?
if [ "${check_status}" -eq 0 ]; then
    log "data check passed"
else
    log "data check FAILED with exit ${check_status}"
fi

if [ "${refresh_status}" -ne 0 ] || [ "${check_status}" -ne 0 ]; then
    log "=== run finished with problems ==="
    exit 1
fi

log "=== run finished clean ==="
