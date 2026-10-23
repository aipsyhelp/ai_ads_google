from __future__ import annotations

from typing import Any

from ads_ai_agents.platform.north_star import FunnelSnapshot, diagnose_funnel_bottleneck
from ads_ai_agents.platform.product_facts import load_product_facts


def run_cro_diagnosis(funnel: FunnelSnapshot) -> dict[str, Any]:
    findings = diagnose_funnel_bottleneck(funnel)
    rates = funnel.rates()
    facts = load_product_facts()
    pricing = (facts.get("pricing") or {}).get("stripe_live") or {}
    eur = pricing.get("eur_monthly")

    playbooks = {
        "registration": [
            "Simplify LP hero to one promise + one CTA → app.example.com",
            "Remove medical claims; lead with anonymous daily support",
            "Track click_web_app + store clicks separately (secondary only)",
        ],
        "first_chat": [
            "Post-login lands on /chat safe-space — push first message <60s",
            "Pre-fill first prompt from ad intent (burnout/sleep)",
            "Instrument chat_start / chat_meaningful for quality bidding",
        ],
        "retention": [
            "Day-1 and Day-3 habit prompts",
            "Session recap that creates reason to return",
            "Streak / routine framing (non-gamified clinical)",
        ],
        "paywall": [
            f"Live checkout price: €{eur}/mo — keep ad and landing-page price claims identical to checkout",
            "Paywall entry: side menu → Buy subscription → Stripe (YOUR_LEGAL_ENTITY)",
            "Parallel path: side menu → Enter activation code (inline)",
            "Show value after first meaningful chat, then paywall",
        ],
        "payment": [
            "Post-pay: redirect /chat; success = subscription days counter (no thank-you page)",
            "Fire purchase via Stripe webhook → Data Manager with gclid + value",
            "Also fire activation_success when code increases days",
        ],
        "payment_zero": [
            "Stop scaling Ads until activation→paid path proven",
            "Instrument purchase + activation_success to Ads via Data Manager",
            "Keep click_store_* / login_success Secondary; never Primary — bid only on chat_message_sent",
            "Run CRO experiments before budget expansion",
        ],
    }

    actions: list[str] = []
    for f in findings:
        stage = str(f.get("stage") or "")
        if f.get("code") == "zero_paying_users":
            actions.extend(playbooks["payment_zero"])
        actions.extend(playbooks.get(stage, [])[:2])

    # Dedupe preserve order
    seen: set[str] = set()
    uniq_actions = []
    for a in actions:
        if a not in seen:
            seen.add(a)
            uniq_actions.append(a)

    return {
        "agent": "cro_funnel",
        "verdict": (
            "Problem is likely NOT Google Ads alone."
            if funnel.paying_users <= 0 and funnel.ad_spend_usd > 0
            else "Locate largest drop-off and run ≤2 CRO experiments."
        ),
        "funnel": funnel.to_dict(),
        "rates": rates,
        "findings": findings,
        "recommended_actions": uniq_actions[:12],
        "experiments_suggested": [
            {"name": "Hero A/B", "variable": "lp_hero", "approval": True},
            {"name": "Paywall after first chat", "variable": "paywall_timing", "approval": True},
            {"name": "UAH vs EUR default on Checkout", "variable": "checkout_currency", "approval": True},
        ],
        "product_facts": {
            "status": facts.get("status"),
            "landings": facts.get("landings"),
            "pricing_stripe_live": pricing,
            "post_pay": "redirect /chat + days counter",
            "stores": facts.get("stores"),
        },
        "status": (
            "diagnosis_with_product_facts"
            if facts.get("status") == "loaded"
            else "diagnosis_ready_needs_real_funnel_counts"
        ),
    }
