"""Brand & Policy Gate — block clinical/crisis claims before asset publish."""
from __future__ import annotations

import re
from typing import Any

from ads_ai_agents.intelligence.controlled_policy import (
    CLINICAL_BLOCKLIST,
    GRANT_COMMERCIAL_BLOCKLIST,
)

# Allowed wellness disclaimers that contain blocked stems.
ALLOW_PATTERNS = (
    r"\bno\s+diagnos",
    r"\bwithout\s+diagnos",
    r"\bне\s+діагн",
    r"\bбез\s+діагн",
    r"\bohne\s+diagnos",
    r"\bnot\s+a\s+therap",
    r"\bне\s+терап",
)


def brand_gate_text(text: str) -> dict[str, Any]:
    """Return pass/fail for a single headline or description."""
    raw = text or ""
    lowered = raw.lower()
    # Strip allowed disclaimer spans before stem check
    scrubbed = lowered
    for pat in ALLOW_PATTERNS:
        scrubbed = re.sub(pat, " ", scrubbed, flags=re.IGNORECASE)
    hits = [b for b in CLINICAL_BLOCKLIST if b in scrubbed]
    hits.extend(b for b in GRANT_COMMERCIAL_BLOCKLIST if b in scrubbed)
    return {
        "text": text,
        "pass": not hits,
        "hits": hits,
    }


def brand_gate_pack(texts: list[str]) -> dict[str, Any]:
    checked = [brand_gate_text(t) for t in texts if (t or "").strip()]
    failed = [c for c in checked if not c["pass"]]
    return {
        "agent": "brand_policy_gate",
        "pass": len(failed) == 0,
        "checked": len(checked),
        "failed": failed,
        "passed_texts": [c["text"] for c in checked if c["pass"]],
    }
