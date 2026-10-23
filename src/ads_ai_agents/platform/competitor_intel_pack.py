"""Public competitor intel seeds — structured evidence (not empty shells)."""
from __future__ import annotations

from typing import Any

# Curated public-category intel for YOUR_BRAND Differentiation (not scraped live).
# Updated by autonomous cycle into competitor_evidence/<Brand>/YYYY-MM-DD.json

PUBLIC_COMPETITOR_INTEL: dict[str, dict[str, Any]] = {
    "BetterHelp": {
        "category": "teletherapy marketplace",
        "hero_patterns": [
            "Online therapy matching — licensed therapists",
            "Therapy that fits your schedule",
        ],
        "cta_patterns": ["Get started", "Get matched"],
        "pricing_signal": "Subscription therapy; often trial/discount promos in Ads",
        "risk_for_us": "Clinical therapy claims — do NOT mirror; stay wellness AI",
        "counter_hooks": [
            "Anonymous AI check-in without therapist wait",
            "Daily support between sessions — not a replacement for therapy",
        ],
        "channels": ["Search", "YouTube", "Meta"],
        "confidence": 0.72,
    },
    "Talkspace": {
        "category": "text/video therapy",
        "hero_patterns": ["Therapy from your phone", "Text your therapist"],
        "cta_patterns": ["Start therapy", "See plans"],
        "pricing_signal": "Plans by message/video frequency",
        "risk_for_us": "Therapy framing — avoid 'therapist' language in RSA",
        "counter_hooks": [
            "Calm AI companion for work stress",
            "Burnout-friendly check-ins — no diagnosis",
        ],
        "channels": ["Search", "App"],
        "confidence": 0.68,
    },
    "Calm": {
        "category": "meditation / sleep content",
        "hero_patterns": ["Sleep stories", "Daily Calm"],
        "cta_patterns": ["Try Calm", "Start free"],
        "pricing_signal": "Freemium → annual premium",
        "risk_for_us": "Content library war — we win on chat activation not library size",
        "counter_hooks": [
            "Talk it through anonymously tonight",
            "Wind-down check-in after work",
        ],
        "channels": ["Search", "Brand", "YouTube"],
        "confidence": 0.75,
    },
    "Headspace": {
        "category": "meditation / mindfulness",
        "hero_patterns": ["Meditation made simple", "Mindfulness for everyday"],
        "cta_patterns": ["Try Headspace", "Start free trial"],
        "pricing_signal": "Trial → subscription",
        "risk_for_us": "Habit/content overlap — differentiate with conversational support",
        "counter_hooks": [
            "When meditation isn't enough — talk it out",
            "Anonymous support for work overwhelm",
        ],
        "channels": ["Search", "App", "Partnerships"],
        "confidence": 0.74,
    },
    "Woebot": {
        "category": "AI CBT chat",
        "hero_patterns": ["AI ally for mental health", "CBT-inspired chat"],
        "cta_patterns": ["Chat with Woebot", "Get the app"],
        "pricing_signal": "App / enterprise",
        "risk_for_us": "Closest AI peer — watch clinical CBT claims",
        "counter_hooks": [
            "Wellness AI for stress — not clinical CBT claims",
            "First meaningful chat as north star",
        ],
        "channels": ["App", "Search"],
        "confidence": 0.7,
    },
    "Wysa": {
        "category": "AI emotional support",
        "hero_patterns": ["AI coach for emotional wellbeing"],
        "cta_patterns": ["Talk to Wysa"],
        "pricing_signal": "Freemium + coach add-ons",
        "risk_for_us": "Similar JTBD — win on UA/EN localization + Grant Search",
        "counter_hooks": ["UA-first burnout language", "Grant Search → chat_message_sent"],
        "channels": ["App", "Search"],
        "confidence": 0.65,
    },
    "Youper": {
        "category": "AI emotional health",
        "hero_patterns": ["Emotional health assistant"],
        "cta_patterns": ["Start chatting"],
        "pricing_signal": "Subscription",
        "risk_for_us": "Mood tracking angle",
        "counter_hooks": ["Actionable check-in not mood diary only"],
        "channels": ["App"],
        "confidence": 0.6,
    },
    "Replika": {
        "category": "AI companion",
        "hero_patterns": ["AI companion who cares"],
        "cta_patterns": ["Create your Replika"],
        "pricing_signal": "Freemium / Pro",
        "risk_for_us": "Companion framing can feel romantic — keep professional calm tone",
        "counter_hooks": ["Professional calm support for work stress"],
        "channels": ["App", "Brand"],
        "confidence": 0.62,
    },
    "MindDoc": {
        "category": "EU mental health app",
        "hero_patterns": ["Mental health diary + insights"],
        "cta_patterns": ["Download MindDoc"],
        "pricing_signal": "EU app store",
        "risk_for_us": "DE market competitor",
        "counter_hooks": ["DE: ruhiger KI-Begleiter nach Feierabend"],
        "channels": ["App", "DE Search"],
        "confidence": 0.58,
    },
    "Selfapy": {
        "category": "DE digital therapeutics",
        "hero_patterns": ["Online courses for mental health (DE)"],
        "cta_patterns": ["Kurs starten"],
        "pricing_signal": "Often insurance / prescription path in DE",
        "risk_for_us": "Clinical/DTx — do not compete on medical claims",
        "counter_hooks": ["Wellness only — no DTx claims"],
        "channels": ["DE", "Partnerships"],
        "confidence": 0.55,
    },
}


def intel_to_changes(brand: str, intel: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten intel into evidence change rows for daily JSON."""
    return [
        {
            "type": "positioning",
            "summary": f"{brand}: {intel.get('category')} — heroes rotate around {', '.join((intel.get('hero_patterns') or [])[:2])}",
            "hero": (intel.get("hero_patterns") or [None])[0],
            "cta": (intel.get("cta_patterns") or [None])[0],
            "pricing": intel.get("pricing_signal"),
            "channels": intel.get("channels"),
            "risk_for_us": intel.get("risk_for_us"),
            "counter_hooks": intel.get("counter_hooks"),
            "confidence": intel.get("confidence"),
            "source": "public_category_intel_v1",
        }
    ]
