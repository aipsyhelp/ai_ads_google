from __future__ import annotations

from ads_ai_agents.core.domain import (
    AgentRecommendation,
    CampaignDraft,
    Portfolio,
    RiskLevel,
    WorkflowStage,
)


class CampaignBuilderAgent:
    name = "campaign_builder"
    stage = WorkflowStage.STRUCTURE

    def recommend(self, campaign: CampaignDraft) -> AgentRecommendation:
        actions: list[str] = [
            f"Draft campaign shell '{campaign.name}' with bidding={campaign.bidding_strategy}.",
            "Create one ad group per intent cluster; map each cluster to a dedicated landing intent.",
            "Attach shared negative list + account brand exclusions before enabling broad/AI expansion.",
        ]

        for cluster in campaign.intent_clusters:
            seeds = ", ".join(cluster.seed_keywords[:4]) if cluster.seed_keywords else "derive from safe_language"
            actions.append(
                f"Ad group '{cluster.name}': seeds=[{seeds}]; match=phrase/exact first; locales={','.join(cluster.locales)}."
            )

        if campaign.search_themes:
            actions.append("Push search themes: " + "; ".join(campaign.search_themes[:8]) + ".")
        else:
            actions.append("Generate search themes from JTBD + safe_language for AI Max / broad exploration.")

        if campaign.portfolio is Portfolio.GRANT_SEARCH:
            actions.extend(
                [
                    f"Cap campaign daily budget at ${min(campaign.daily_budget_usd, campaign.grant.max_daily_budget_usd):.0f} inside ${campaign.grant.monthly_budget_usd:,.0f}/mo grant.",
                    "Prefer Maximize Conversions with tCPA once primary quality conversion has stable volume.",
                ]
            )

        if not campaign.brand_exclusions:
            actions.append("Seed brand exclusions from competitor watchlist before launch.")

        return AgentRecommendation(
            agent=self.name,
            stage=self.stage,
            title="Campaign / ad group build sheet",
            rationale="Enterprise builders ship structured drafts with negatives, themes, and budget caps before go-live.",
            actions=tuple(actions),
            risk=RiskLevel.MEDIUM,
            requires_approval=True,
            priority=20,
        )
