"""Single Grant recovery contract for CID YOUR-CUSTOMER-ID.

Conflicting one-shot scripts (Max Clicks, cold-start tCPA $500) are refused so
agents cannot ping-pong bidding and conversion goals.
"""
from __future__ import annotations

import json

CANONICAL_BIDDING = "MAXIMIZE_CONVERSIONS"
CANONICAL_TCPA = "$2000 recovery experiment; review after 72 hours; not the acquisition KPI"
CANONICAL_COMMAND = "python -m ads_ai_agents.cli platform grant-delivery-recovery"

REFUSE_MAX_CLICKS = (
    "Maximize Clicks ($2 CPC cap) loses the $6–12 auctions on this Grant account. "
    "Keep the current Maximize Conversions recovery experiment stable and review its evidence."
)
REFUSE_LEGACY_TCPA = (
    "Legacy recovery scripts force tCPA $200–$500 and collapse primary goals. "
    "That starves cold-start delivery. Use grant_delivery_recovery instead."
)

REFUSE_CLEAR_TCPA = (
    "This legacy script clears the current recovery tCPA and invalidates the experiment. "
    "Use the read-only recovery monitor; review delivery after the stability window."
)

REFUSE_GOAL_DRIFT = (
    "This legacy script modifies conversion goals outside the canonical MEANINGFUL_PRIMARY_ACTIONS "
    "contract (e.g., promoting click_store_* or chat_open). Use amend_recovery_october.py instead."
)



def refuse(script: str, reason: str) -> int:
    print(
        json.dumps(
            {
                "refused": True,
                "script": script,
                "reason": reason,
                "canonical_bidding": CANONICAL_BIDDING,
                "canonical_tcpa": CANONICAL_TCPA,
                "canonical_command": CANONICAL_COMMAND,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 2
