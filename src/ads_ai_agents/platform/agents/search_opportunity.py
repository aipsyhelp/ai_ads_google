from __future__ import annotations

from typing import Any


def build_search_opportunity_brief(
    *,
    external_signals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Search Opportunity Agent — looks at the internet, not the Ads account."""
    external_signals = external_signals or []
    return {
        "agent": "search_opportunity",
        "mode": "internet_first",
        "ignores": ["your_google_ads_account_performance"],
        "signal_sources": [
            "Google Trends",
            "News / legislation",
            "Reddit topic velocity",
            "YouTube rising queries",
            "App Store trending wellness apps",
        ],
        "signals": external_signals
        or [
            {
                "topic": "burnout",
                "direction": "watch",
                "note": "Connect Trends API / manual CSV to auto-flag +42% style spikes.",
                "proposed_mission": "Draft GRANT_SEARCH experiment cluster AI Burnout — approval required",
            }
        ],
        "rule": "Spike → draft campaign mission for Strategy Engine; never auto-create campaigns.",
        "status": "awaiting_trends_connectors",
    }
