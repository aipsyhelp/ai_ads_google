from __future__ import annotations

from ads_ai_agents.core.domain import (
    AgentRecommendation,
    CampaignDraft,
    Portfolio,
    RiskLevel,
    WorkflowStage,
)


class StrategyAgent:
    name = "strategy"
    stage = WorkflowStage.STRUCTURE

    def recommend(self, campaign: CampaignDraft) -> AgentRecommendation:
        grant = campaign.grant
        target_cpa = campaign.business_goal.effective_target_cpa_usd or grant.target_cpa_usd
        actions: list[str] = [
            (
                f"North star: utilize ${grant.monthly_budget_usd:,.0f}/mo Ad Grant at ~${target_cpa:.2f} CPA "
                f"(~{grant.target_monthly_leads:.0f} qualified leads/mo, ~{grant.target_daily_leads:.0f}/day)."
            ),
            "Keep grant vs paid learning loops separate; never let App/PMax cannibalize Grant Search signal.",
        ]

        if campaign.portfolio is Portfolio.GRANT_SEARCH:
            actions.extend(
                [
                    "Compact Grant Search skeleton: Brand | Core wellbeing | TOFU content | B2B corporate wellbeing.",
                    "Collapse city micro-campaigns unless they have unique LP, budget, or language value.",
                    "Core = phrase/exact; exploration = controlled broad/AI Max only after negatives + quality CPA.",
                ]
            )
        if campaign.portfolio is Portfolio.PERFORMANCE_MAX:
            actions.append("PMax only as paid expansion beside Search, with brand exclusions and signal readiness.")
        if campaign.portfolio is Portfolio.AI_MAX_SEARCH:
            actions.append("Govern AI Max: URL inclusions/exclusions, brand controls, text guidelines, search themes.")

        if len(campaign.intent_clusters) < 2:
            actions.append("Add at least two JTBD clusters (e.g. stress overload + corporate wellbeing) before scaling.")

        locales = sorted({locale for cluster in campaign.intent_clusters for locale in cluster.locales})
        if locales:
            actions.append(f"Localize only where LP exists: {', '.join(locales)}.")

        return AgentRecommendation(
            agent=self.name,
            stage=self.stage,
            title="Enterprise portfolio strategy",
            rationale="Top-tier marketers optimize portfolio architecture and learning loops before creative volume.",
            actions=tuple(actions),
            risk=RiskLevel.MEDIUM if campaign.portfolio is not Portfolio.GRANT_SEARCH else RiskLevel.LOW,
            priority=10,
        )
