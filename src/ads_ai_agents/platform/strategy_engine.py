from __future__ import annotations

from typing import Any

from ads_ai_agents.platform.north_star import FunnelSnapshot
from ads_ai_agents.platform.product_facts import load_product_facts


def build_strategy_mission(
    *,
    funnel: FunnelSnapshot,
    competitor_brief: dict[str, Any],
    intent_brief: dict[str, Any],
    cro_brief: dict[str, Any],
    revenue_brief: dict[str, Any],
) -> dict[str, Any]:
    """Strategy Engine — weekly priorities for YOUR_BRAND."""
    zero_paid = funnel.paying_users <= 0
    priorities = []
    if zero_paid:
        priorities.extend(
            [
                {
                    "rank": 1,
                    "theme": "Prove paid path before scaling Search",
                    "do": "Instrument purchase + fix onboarding/paywall; fire-test form/chat/Purchase",
                    "dont": "Expand countries or broad match to chase cheap clicks",
                },
                {
                    "rank": 2,
                    "theme": "CRO over media",
                    "do": "Run ≤5 experiments: hero, onboarding, trial, paywall timing",
                    "dont": "Treat Google Conversions CPA as success",
                },
                {
                    "rank": 3,
                    "theme": "Intent scenarios",
                    "do": "Build AI Burnout / Sleep anxiety / Parent-support messaging (policy-safe)",
                    "dont": "Bid on clinical depression/psychiatrist terms",
                },
            ]
        )
    else:
        priorities.append(
            {
                "rank": 1,
                "theme": "Scale what pays",
                "do": "Allocate budget by CPPU, not CPQA alone",
                "dont": "Restart micro-conversion bidding",
            }
        )

    priorities.append(
        {
            "rank": 4,
            "theme": "Acquisition executor discipline",
            "do": "Keep Ads in CONTROLLED_LIVE (≤10% tilts, negatives, approval for pauses)",
            "dont": "Full autonomy on bidding/goals/geo",
        }
    )
    priorities.append(
        {
            "rank": 5,
            "theme": "Measurement integrity",
            "do": "GTM store clicks Secondary; purchase/activation via Stripe webhook + days counter",
            "dont": "Optimize to LP €1 teaser or store-click volume",
        }
    )

    facts = load_product_facts()
    country_stance = facts.get("country_stance") or {
        "UA": "core — keep Search content + home",
        "EN": "core — articles/search; tighten PMax",
        "DE": "LP live — prove chat→pay before Search scale",
        "US": "no auto-expansion",
    }

    return {
        "agent": "strategy",
        "horizon_days": 7,
        "priorities": priorities,
        "country_stance": country_stance,
        "channel_stance": {
            "google_ads_search": "maintain + hygiene",
            "pmax": "throttle until paid path exists",
            "youtube": "pause / demote goals; no scale",
            "seo_aso": "start evidence collection + store-click secondary tracking",
            "app_stores": "track click_store_* secondary on LP+chat; ASO later",
        },
        "inputs_used": {
            "competitor_signals": len(competitor_brief.get("signals") or []),
            "intent_scenarios": intent_brief.get("product_scenarios"),
            "cro_verdict": cro_brief.get("verdict"),
            "primary_kpi": revenue_brief.get("primary_kpi"),
            "product_facts": facts.get("status"),
        },
        "status": "active_with_product_facts" if facts.get("status") == "loaded" else "active_skeleton",
    }
