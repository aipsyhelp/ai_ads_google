#!/usr/bin/env bash
# Install hourly Grant delivery pulse as launchd (survives IDE session aborts).
#
#   scripts/install_delivery_watch_hourly.sh
#   scripts/install_delivery_watch_hourly.sh --uninstall
#   scripts/install_delivery_watch_hourly.sh --run-now
set -euo pipefail

LABEL="com.example.ads-agents.delivery-watch-hourly"
INTERVAL="${DELIVERY_WATCH_HOURLY_INTERVAL:-86400}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="${HOME}/Library/Logs/ads-agents"
PYTHON="${DELIVERY_WATCH_PYTHON:-${HOME}/.venvs/ads-agents/bin/python}"
WRAPPER="${LOG_DIR}/run-delivery-watch-hourly.sh"

usage_error() {
  echo "usage: $(basename "$0") [--uninstall|--run-now]" >&2
  exit 64
}

uninstall() {
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
  rm -f "${PLIST}"
  echo "removed ${LABEL}"
}

case "${1:-}" in
  --uninstall) uninstall; exit 0 ;;
  --run-now) exec "${PYTHON}" "${SCRIPT_DIR}/delivery_watch_hourly.py" ;;
  "") ;;
  *) usage_error ;;
esac

if [[ ! -x "${PYTHON}" ]]; then
  echo "python not found: ${PYTHON}" >&2
  exit 1
fi

mkdir -p "${LOG_DIR}" "${HOME}/Library/LaunchAgents" "${PROJECT_DIR}/var/delivery_watch_hourly"

# Wrapper so launchd can set PYTHONPATH and flag first-impression alerts.
cat > "${WRAPPER}" <<WRAP
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="${PROJECT_DIR}/src"
RC=0
OUT="\$("${PYTHON}" "${SCRIPT_DIR}/delivery_watch_hourly.py")" || RC=\$?
echo "\$OUT"
if [[ "\$RC" == "1" ]]; then
  echo "\$OUT" >> "${LOG_DIR}/delivery-watch-hourly-ALERTS.log"
  /usr/bin/osascript -e 'display notification "New delivery event detected. Check the Google Ads recovery log." with title "Google Ads AI Automation"' >/dev/null 2>&1 || true
  # Exit 1 is a new alert, not an execution failure.
  exit 0
fi
exit "\$RC"
WRAP
chmod +x "${WRAPPER}"

cat > "${PLIST}" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${WRAPPER}</string>
    </array>
    <key>StartInterval</key>
    <integer>${INTERVAL}</integer>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/delivery-watch-hourly.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/delivery-watch-hourly.err.log</string>
    <key>RunAtLoad</key>
    <true/>
    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
PLIST_EOF

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "${PLIST}"

echo "installed ${LABEL} — every ${INTERVAL}s"
echo "  plist:     ${PLIST}"
echo "  logs:      ${LOG_DIR}/delivery-watch-hourly.log"
echo "  snapshots: ${PROJECT_DIR}/var/delivery_watch_hourly/"
echo "  alert file: ${PROJECT_DIR}/var/delivery_watch_hourly/FIRST_IMPRESSIONS_ALERT.txt"
echo "  run now:   $(basename "$0") --run-now"
