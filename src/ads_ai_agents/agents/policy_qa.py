from __future__ import annotations

from ads_ai_agents.core.domain import (
    AgentRecommendation,
    CampaignDraft,
    RiskLevel,
    WorkflowStage,
)
from ads_ai_agents.core.policy import evaluate_campaign_policy


class PolicyQAAgent:
    name = "policy_qa"
    stage = WorkflowStage.GUARD

    def recommend(self, campaign: CampaignDraft) -> AgentRecommendation:
        gate = evaluate_campaign_policy(campaign)
        actions: list[str] = [
            "Pre-publish QA: medical claims, sensitive audiences, LP fit, crisis-adjacent queries.",
            "Reject advertiser-curated health-sensitive audiences; prefer contextual Search intent + safe copy.",
            "Verify Play/web positioning remains general wellness / self-help — not therapy replacement.",
        ]

        if gate.findings:
            for finding in gate.findings[:8]:
                actions.append(f"Gate [{finding.risk.value}] {finding.code}: {finding.message}")
        else:
            actions.append("No blocking policy findings on current draft — still require human review for first publish.")

        actions.append("Maintain crisis escalation disclaimer on LP; ads must not imply emergency care.")

        risk = gate.risk if gate.findings else RiskLevel.LOW
        return AgentRecommendation(
            agent=self.name,
            stage=self.stage,
            title="Policy & brand safety QA",
            rationale="In sensitive wellbeing categories, policy QA is a performance system, not a legal afterthought.",
            actions=tuple(actions),
            risk=risk,
            requires_approval=True,
            priority=55,
        )
