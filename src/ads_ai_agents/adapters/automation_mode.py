from __future__ import annotations

import os
from enum import Enum

from ads_ai_agents.adapters.settings import load_dotenv


class AutomationMode(str, Enum):
    READ_ONLY = "READ_ONLY"
    CONTROLLED_LIVE = "CONTROLLED_LIVE"
    AUTONOMOUS_LIVE = "AUTONOMOUS_LIVE"


VALID_MODES = {m.value for m in AutomationMode}

# Modes that may mutate Ads (still subject to policy + write_guard).
MUTATING_MODES = frozenset(
    {
        AutomationMode.CONTROLLED_LIVE,
        AutomationMode.AUTONOMOUS_LIVE,
    }
)


def resolve_automation_mode(raw: str | None = None) -> AutomationMode:
    """Resolve kill-switch mode. Invalid/missing config → READ_ONLY."""
    load_dotenv()
    value = (raw if raw is not None else os.environ.get("ADS_AUTOMATION_MODE", "")).strip().upper()
    if not value:
        # Live modes require an explicit ADS_AUTOMATION_MODE. The legacy
        # ADS_AUTOPILOT_MODE setting is ignored here and can never enable writes.
        return AutomationMode.READ_ONLY
    if value not in VALID_MODES:
        return AutomationMode.READ_ONLY
    return AutomationMode(value)


def mutations_allowed(mode: AutomationMode | None = None) -> bool:
    m = mode if mode is not None else resolve_automation_mode()
    return m in MUTATING_MODES


def assert_mutations_allowed(*, require_controlled_live: bool = True) -> AutomationMode:
    """Allow writes in CONTROLLED_LIVE or AUTONOMOUS_LIVE. READ_ONLY always blocks."""
    mode = resolve_automation_mode()
    if require_controlled_live and not mutations_allowed(mode):
        raise PermissionError(
            f"Mutations blocked: ADS_AUTOMATION_MODE={mode.value}. "
            "Set ADS_AUTOMATION_MODE=CONTROLLED_LIVE or AUTONOMOUS_LIVE to enable writes."
        )
    if mutations_allowed(mode):
        from ads_ai_agents.adapters.recovery_stability import assert_recovery_stable

        assert_recovery_stable()
    return mode
