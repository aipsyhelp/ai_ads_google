from __future__ import annotations

from ads_ai_agents.core.domain import (
    AgentRecommendation,
    CampaignDraft,
    RiskLevel,
    WorkflowStage,
)


class TrendAgent:
    name = "trends"
    stage = WorkflowStage.COMPETE

    def recommend(self, campaign: CampaignDraft) -> AgentRecommendation:
        themes = list(campaign.search_themes)
        if not themes:
            for cluster in campaign.intent_clusters:
                themes.extend(cluster.safe_language[:2])

        actions: list[str] = [
            "Pull Search Terms Insights categories daily; tag rising / falling / converting intent buckets.",
            "Cross-check macro demand via Google Trends for stress, burnout, sleep, employee wellbeing by market.",
            "Promote rising converting themes into Core; quarantine noisy rising themes into Exploration only.",
            "Seasonality flags: exam periods, New Year, winter blues, workplace burnout quarters → budget tilt.",
        ]

        if themes:
            actions.append("Active theme radar: " + "; ".join(themes[:10]) + ".")

        markets = campaign.market
        actions.append(
            f"Market lens '{markets}/{campaign.locale}': prioritize themes with LP + localization ready before spend."
        )
        actions.append(
            "Output for Budget Agent: theme-level CPA vs target; recommend +/− budget only on quality CPA themes."
        )

        return AgentRecommendation(
            agent=self.name,
            stage=self.stage,
            title="Demand & trend radar",
            rationale="Budget follows demand only when trends are converted into measurable theme economics.",
            actions=tuple(actions),
            risk=RiskLevel.LOW,
            requires_approval=False,
            priority=18,
        )
