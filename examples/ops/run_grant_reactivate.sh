#!/usr/bin/env bash
# Apply Grant reactivation fixes when Explorer API quota recovers.
set -euo pipefail
cd "$(dirname "$0")/../.."
set -a
# shellcheck disable=SC1091
source ./.env
set +a
export PYTHONPATH=src
echo "=== grant-compliance ==="
~/.venvs/ads-agents/bin/python -m ads_ai_agents.cli platform grant-compliance || true
echo "=== grant-reactivate --apply ==="
~/.venvs/ads-agents/bin/python -m ads_ai_agents.cli platform grant-reactivate --apply
echo "=== enterprise-assets (optional fill) ==="
~/.venvs/ads-agents/bin/python -m ads_ai_agents.cli platform enterprise-assets --apply || true
echo "Done. Then submit:"
echo "https://support.google.com/nonprofits/contact/Request_for_reactivation_cases_2?hl=en"
