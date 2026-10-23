#!/usr/bin/env bash
# Phase 2.1 — read-only daily intelligence (no Google Ads mutations).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${ADS_AGENTS_PYTHON:-$HOME/.venvs/ads-agents/bin/python}"
export PYTHONPATH="$ROOT/src"

cd "$ROOT"
echo "[ads-agents] daily intelligence starting $(date -u +%Y-%m-%dT%H:%M:%SZ)"
"$PY" -m ads_ai_agents.cli ping
"$PY" -m ads_ai_agents.cli daily-run
"$PY" -m ads_ai_agents.cli goals cleanup-plan
"$PY" -m ads_ai_agents.cli recommendations evaluate
"$PY" -m ads_ai_agents.cli budget-plan --shadow
"$PY" -m ads_ai_agents.cli dashboard
echo "[ads-agents] done → reports/ and reports/daily/"
