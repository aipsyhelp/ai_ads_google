from __future__ import annotations

from ads_ai_agents.core.domain import (
    AgentRecommendation,
    CampaignDraft,
    Portfolio,
    RiskLevel,
    WorkflowStage,
)


class MeasurementAgent:
    name = "measurement"
    stage = WorkflowStage.SENSE

    def recommend(self, campaign: CampaignDraft) -> AgentRecommendation:
        goal = campaign.business_goal
        target = goal.effective_target_cpa_usd or campaign.grant.target_cpa_usd
        actions: list[str] = [
            (
                f"Primary for bidding: '{goal.primary_conversion}' only if it predicts durable value "
                f"compatible with ${target:.2f} CPA north-star."
            ),
            "Hierarchy: micro → lead → quality → enterprise; only quality/revenue-correlated events are primary.",
            "Do NOT enable Enhanced Conversions for Leads or CRM Customer Match on mental-health "
            "Ad Grants accounts — hashed email/phone from wellbeing forms triggers policy suspension.",
            "Fill Raw / Qualified / Converted lead stages — empty funnels train Smart Bidding on noise.",
            f"Daily KPI board: spend, leads, CPA vs ${target:.2f}, conversion rate, IS lost (budget), disapprovals.",
        ]

        if goal.secondary_conversions:
            actions.append("Secondary observe-only: " + ", ".join(goal.secondary_conversions) + ".")
        else:
            actions.append(
                "Add secondary quality signals: onboarding complete, first meaningful conversation, subscription start."
            )

        if campaign.portfolio in {Portfolio.PERFORMANCE_MAX, Portfolio.AI_MAX_SEARCH, Portfolio.APP_GROWTH}:
            actions.append("Block budget scale until post-click quality signals are live.")

        actions.append("Consent Mode + EU consent records required for EEA measurement reliability.")
        actions.append("Value rules: upweight corporate/DE/EN qualified onboarding vs low-intent TOFU.")

        risk = RiskLevel.HIGH if not goal.secondary_conversions else RiskLevel.MEDIUM
        return AgentRecommendation(
            agent=self.name,
            stage=self.stage,
            title="Goals & measurement control",
            rationale="At $2 CPA ambition, signal quality is the product; creatives cannot compensate for bad goals.",
            actions=tuple(actions),
            risk=risk,
            priority=5,
        )
