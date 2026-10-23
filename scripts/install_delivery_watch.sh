#!/usr/bin/env bash
# Install the daily Grant delivery watch as a launchd agent (macOS).
#
#   scripts/install_delivery_watch.sh            # install / reinstall
#   scripts/install_delivery_watch.sh --uninstall
#   scripts/install_delivery_watch.sh --run-now
#
# The job is strictly read-only: it runs delivery_watch_daily.py, which queries
# the Ads API, stores a snapshot and diffs it against the previous run.
set -euo pipefail

LABEL="com.example.ads-agents.delivery-watch"
HOUR="${DELIVERY_WATCH_HOUR:-9}"
MINUTE="${DELIVERY_WATCH_MINUTE:-15}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"

# Logs live under the home directory: launchd refuses the job (EX_CONFIG) when
# its stdout/stderr paths sit on an external volume it cannot open at load time.
LOG_DIR="${HOME}/Library/Logs/ads-agents"

PYTHON="${DELIVERY_WATCH_PYTHON:-${HOME}/.venvs/ads-agents/bin/python}"

usage_error() {
  echo "unknown argument: $1" >&2
  echo "usage: $(basename "$0") [--uninstall|--run-now]" >&2
  exit 64
}

uninstall() {
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
  rm -f "${PLIST}"
  echo "removed ${LABEL}"
}

case "${1:-}" in
  --uninstall)
    uninstall
    exit 0
    ;;
  --run-now)
    exec "${PYTHON}" "${SCRIPT_DIR}/delivery_watch_daily.py"
    ;;
  "") ;;
  *) usage_error "$1" ;;
esac

if [[ ! -x "${PYTHON}" ]]; then
  echo "python interpreter not found or not executable: ${PYTHON}" >&2
  echo "set DELIVERY_WATCH_PYTHON to the interpreter that has google-ads installed" >&2
  exit 1
fi

mkdir -p "${LOG_DIR}" "${HOME}/Library/LaunchAgents"

cat > "${PLIST}" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON}</string>
        <string>${SCRIPT_DIR}/delivery_watch_daily.py</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>${HOUR}</integer>
        <key>Minute</key>
        <integer>${MINUTE}</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/delivery-watch.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/delivery-watch.err.log</string>
    <key>RunAtLoad</key>
    <false/>
    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
PLIST_EOF

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "${PLIST}"

echo "installed ${LABEL} — daily at ${HOUR}:$(printf '%02d' "${MINUTE}")"
echo "  plist:     ${PLIST}"
echo "  logs:      ${LOG_DIR}/delivery-watch.log"
echo "  snapshots: ${PROJECT_DIR}/var/delivery_watch/"
echo "  run now:   $(basename "$0") --run-now"
echo
echo "verify with: launchctl kickstart -w gui/$(id -u)/${LABEL}"
