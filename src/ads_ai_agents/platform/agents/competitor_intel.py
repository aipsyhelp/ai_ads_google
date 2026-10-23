from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


DEFAULT_WATCHLIST = (
    "BetterHelp",
    "Talkspace",
    "Calm",
    "Headspace",
    "Woebot",
    "Wysa",
    "Youper",
    "Replika",
    "MindDoc",
    "Selfapy",
)


@dataclass
class CompetitorSignal:
    brand: str
    signal_type: str
    summary: str
    evidence_urls: list[str] = field(default_factory=list)
    confidence: float = 0.5
    action_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_competitor_brief(
    *,
    watchlist: tuple[str, ...] = DEFAULT_WATCHLIST,
    known_changes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Competitor Intelligence — observation, not cloning.
    P1: structured evidence pack; connectors (SERP/Ads Transparency/reviews) plug in later.
    """
    known_changes = known_changes or []
    signals: list[CompetitorSignal] = []

    for change in known_changes:
        signals.append(
            CompetitorSignal(
                brand=str(change.get("brand") or "unknown"),
                signal_type=str(change.get("type") or "change"),
                summary=str(change.get("summary") or ""),
                evidence_urls=list(change.get("urls") or []),
                confidence=float(change.get("confidence") or 0.6),
                action_hint=str(change.get("action_hint") or "Review pattern; do not copy claims."),
            )
        )

    # Standing hypotheses for wellness AI category (public patterns — not scraped live yet).
    if not signals:
        signals.extend(
            [
                CompetitorSignal(
                    brand="BetterHelp",
                    signal_type="offer_pattern",
                    summary="Category leaders rotate Hero/CTA/trial aggressively; Ads alone rarely carries paywall.",
                    confidence=0.55,
                    action_hint="Track trial length + onboarding claims weekly; answer with differentiated AI support positioning.",
                ),
                CompetitorSignal(
                    brand="Calm/Headspace",
                    signal_type="positioning",
                    summary="Competes on habit/content, not clinical therapy — closer JTBD overlap for YOUR_BRAND.",
                    confidence=0.6,
                    action_hint="Mine stress/sleep/burnout intents; avoid clinical diagnosis language.",
                ),
                CompetitorSignal(
                    brand="Indirect",
                    signal_type="intent_sources",
                    summary="Reddit/forums/App reviews reveal pain language before keyword tools.",
                    confidence=0.7,
                    action_hint="Stand up Intent Mining + review miners before scaling Search spend.",
                ),
            ]
        )

    return {
        "agent": "competitor_intel",
        "mode": "observe_patterns",
        "watchlist": list(watchlist),
        "signals": [s.to_dict() for s in signals],
        "daily_collect": [
            "SERP / Ads Transparency creatives",
            "Landing hero/CTA/price/FAQ diffs",
            "App Store / Play creatives & reviews",
            "Reddit / LinkedIn / YouTube / News mentions",
            "SimilarWeb / Trends direction (directional only)",
        ],
        "forbidden": ["clone_clinical_claims", "scrape_private_data", "attack_competitors"],
        "status": "skeleton_awaiting_connectors_and_client_watchlist",
    }
