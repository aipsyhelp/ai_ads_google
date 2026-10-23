from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class CompetitorProfile:
    rank: int
    brand: str
    category: str  # ai_chat | human_therapy_marketplace | mindfulness | companion
    website: str
    app_store_hint: str
    positioning: str
    offer_patterns: list[str] = field(default_factory=list)
    ideas_for_your_brand: list[str] = field(default_factory=list)
    policy_caution: str = "Do not copy clinical/therapy-replacement claims."

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Curated + market-researched top set for mental health / wellbeing (2025–2026 landscape).
# Agent refreshes narrative; live SERP/Ads Transparency connectors plug into evidence store later.
TOP10_MENTAL_WELLBEING: tuple[CompetitorProfile, ...] = (
    CompetitorProfile(
        1,
        "Wysa",
        "ai_chat",
        "https://www.wysa.com",
        "Wysa: Mental Health Support",
        "Evidence-leaning AI coach + optional human coaching; strong free tier",
        ["free core chat", "premium exercises library", "optional coach upsell"],
        ["Lead with free first conversation", "Optional coach later — not day-1 paywall"],
    ),
    CompetitorProfile(
        2,
        "Headspace (Ebb)",
        "mindfulness",
        "https://www.headspace.com",
        "Headspace",
        "Meditation library + AI companion (Ebb) for habit loops",
        ["content library", "AI companion upsell", "employer plans"],
        ["Pair chat with short check-ins / routines", "B2B path separate from consumer Ads"],
    ),
    CompetitorProfile(
        3,
        "Calm",
        "mindfulness",
        "https://www.calm.com",
        "Calm",
        "Sleep/stress brand with premium content + AI personalization",
        ["sleep stories", "annual plans", "soft wellness language"],
        ["Sleep anxiety / wind-down scenario as Search intent cluster"],
    ),
    CompetitorProfile(
        4,
        "Youper",
        "ai_chat",
        "https://www.youper.ai",
        "Youper",
        "Mood tracking + guided CBT-style AI",
        ["mood graphs", "trial then annual"],
        ["Surface mood/insights after first chat — reason to return"],
    ),
    CompetitorProfile(
        5,
        "Ash (Slingshot AI)",
        "ai_chat",
        "https://www.ash.com",
        "Ash",
        "Open-ended conversational / voice-first AI support",
        ["voice + text", "low-structure talk-it-through"],
        ["Differentiate voice+text 24/7 wellbeing (already a product strength)"],
    ),
    CompetitorProfile(
        6,
        "Earkick",
        "ai_chat",
        "https://earkick.com",
        "Earkick",
        "Privacy-focused quick check-ins + AI",
        ["free/privacy positioning", "fast check-ins"],
        ["2–3 min check-in CTA from LP ads"],
    ),
    CompetitorProfile(
        7,
        "BetterHelp",
        "human_therapy_marketplace",
        "https://www.betterhelp.com",
        "BetterHelp",
        "Human therapist marketplace — not AI-first, but owns paid search demand",
        ["quiz funnel", "matched therapist", "subscription therapy"],
        ["Capture self-help/wellbeing demand they don't serve; never claim therapy"],
        "High competition + clinical adjacency — negatives critical",
    ),
    CompetitorProfile(
        8,
        "Talkspace",
        "human_therapy_marketplace",
        "https://www.talkspace.com",
        "Talkspace",
        "Text/video therapy marketplace + AI-enhanced matching",
        ["async messaging therapy", "employer benefits"],
        ["B2B NGO/corporate pages already exist — keep separate from consumer Grant"],
    ),
    CompetitorProfile(
        9,
        "Replika",
        "companion",
        "https://replika.com",
        "Replika",
        "Companion AI (relationship), not clinical — owns 'talk to AI' intent",
        ["avatar companion", "subscription tiers"],
        ["Compete on wellbeing clarity, not romance/companion framing"],
    ),
    CompetitorProfile(
        10,
        "MindDoc / Selfapy (DACH)",
        "ai_chat",
        "https://minddoc.com",
        "MindDoc",
        "DE-market digital mental health / courses — relevant for DE locale",
        ["localized DE funnels", "structured programs"],
        ["DE Search only after DE activation events proven on chat"],
    ),
)


def discover_top_competitors(*, category: str = "mental_wellbeing") -> dict[str, Any]:
    """Autonomous top-10 pack for Strategy / Creative / CRO (observation, not cloning)."""
    profiles = [p.to_dict() for p in TOP10_MENTAL_WELLBEING]
    strategy_seeds = []
    for p in TOP10_MENTAL_WELLBEING:
        strategy_seeds.extend(p.ideas_for_your_brand[:1])

    return {
        "agent": "competitor_discovery",
        "category": category,
        "method": "market_research_curated_v1 + evidence_store_refresh",
        "count": len(profiles),
        "competitors": profiles,
        "your_brand_playbook": [
            "Primary JTBD: anonymous stress/burnout/sleep support — not therapy marketplace",
            "Stage KPI: Cost per Product Activation (web login OR app open → first chat)",
            "Defer optimizing to Purchase until volume exists; keep Purchase as observe",
            "Borrow patterns: free first value, check-in habit, insights return loop",
            "Never copy clinical diagnosis / medication / crisis treatment claims",
        ],
        "strategy_seeds": strategy_seeds,
        "daily_watch": [
            "Landing hero/CTA/trial diffs",
            "App Store screenshots & reviews themes",
            "Ads Transparency creatives",
            "Pricing / trial length changes",
        ],
        "sources": [
            "https://www.simplypsychology.com/articles/ai-mental-health-apps-comparison-2026",
            "https://psychology.com/ai-therapy/apps",
            "https://aitoolclinic.com/ai-mental-health-apps-evidence-based-comparison-2026/",
        ],
        "status": "ready_for_strategy_engine",
    }
