from __future__ import annotations

from ads_ai_agents.core.domain import (
    AgentRecommendation,
    CampaignDraft,
    RiskLevel,
    WorkflowStage,
)


DEFAULT_COMPETITOR_BRANDS = (
    "Wysa",
    "Woebot",
    "Headspace",
    "Calm",
    "Replika",
    "Youper",
    "Talkspace",
    "BetterHelp",
    "BuddyHelp",
)

DEFAULT_COMPETITOR_DOMAINS = (
    "wysa.com",
    "woebothealth.com",
    "headspace.com",
    "calm.com",
    "replika.com",
    "youper.ai",
    "talkspace.com",
    "betterhelp.com",
    "example.com",
)


class CompetitorAgent:
    name = "competitor"
    stage = WorkflowStage.COMPETE

    def recommend(self, campaign: CampaignDraft) -> AgentRecommendation:
        brands = campaign.competitors.brands or DEFAULT_COMPETITOR_BRANDS
        domains = campaign.competitors.domains or DEFAULT_COMPETITOR_DOMAINS

        actions: list[str] = [
            "Weekly Auction Insights export (UI): overlap rate, outranking share, top-of-page rate vs peers.",
            "Weekly Ads Transparency Center snapshot per domain: creative angles, CTAs, geo/date coverage.",
            "Classify rivals by job: AI emotional support | structured self-help | human therapy | free/NGO trust.",
            "Extract winning creative patterns (tone, proof, CTA) → feed Asset Agent without copying medical claims.",
            "Protect brand SERP: exact brand + misspellings; exclude competitor brand terms unless conquest approved.",
        ]

        actions.append("Watchlist brands: " + ", ".join(brands) + ".")
        actions.append("Watchlist domains: " + ", ".join(domains) + ".")

        missing_exclusions = [b for b in brands if b.lower() not in {x.lower() for x in campaign.brand_exclusions}]
        if missing_exclusions:
            actions.append(
                "Recommend brand exclusions / negative brands: " + ", ".join(missing_exclusions[:8]) + "."
            )

        if campaign.competitors.notes:
            actions.append(campaign.competitors.notes)

        actions.append(
            "Diff our RSA headlines vs competitor angles; queue 2 counter-creatives per JTBD cluster weekly."
        )

        return AgentRecommendation(
            agent=self.name,
            stage=self.stage,
            title="Competitor & creative intelligence",
            rationale="Enterprise teams treat Auction Insights + Ads Transparency as a weekly operating rhythm.",
            actions=tuple(actions),
            risk=RiskLevel.LOW,
            requires_approval=False,
            auto_apply_safe=False,
            priority=15,
        )
