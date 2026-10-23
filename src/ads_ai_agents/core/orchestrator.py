from __future__ import annotations

from typing import Protocol

from ads_ai_agents.agents import ENTERPRISE_AGENTS
from ads_ai_agents.core.domain import (
    AgentRecommendation,
    CampaignDraft,
    MissionPlan,
    WorkflowStage,
    WorkflowStep,
)
from ads_ai_agents.core.policy import evaluate_campaign_policy


ENTERPRISE_WORKFLOW: tuple[WorkflowStep, ...] = (
    WorkflowStep(
        stage=WorkflowStage.SENSE,
        objective="Read goals, conversions, and live KPI health before any spend decision.",
        agents=("measurement",),
    ),
    WorkflowStep(
        stage=WorkflowStage.COMPETE,
        objective="Refresh competitor creatives, auction pressure, and demand trends.",
        agents=("competitor", "trends"),
    ),
    WorkflowStep(
        stage=WorkflowStage.STRUCTURE,
        objective="Keep portfolio, campaigns, keywords, and negatives enterprise-tight.",
        agents=("strategy", "campaign_builder", "keywords"),
    ),
    WorkflowStep(
        stage=WorkflowStage.CREATE,
        objective="Ship policy-safe RSA/PMax asset packs aligned to JTBD clusters.",
        agents=("asset",),
    ),
    WorkflowStep(
        stage=WorkflowStage.ALLOCATE,
        objective="Pace the $10k grant and reallocate toward ≤$2 quality CPA winners.",
        agents=("budget",),
    ),
    WorkflowStep(
        stage=WorkflowStage.GUARD,
        objective="Policy QA + hourly/daily guardrails; approve only safe mutations.",
        agents=("policy_qa", "watchdog"),
    ),
)


class Recommender(Protocol):
    name: str
    stage: WorkflowStage

    def recommend(self, campaign: CampaignDraft) -> AgentRecommendation:
        """Return one role-specific recommendation for the campaign draft."""


class AdsOrchestrator:
    """Enterprise control-plane orchestrator for YOUR_BRAND Grant operations."""

    def __init__(self, agents: tuple[Recommender, ...] | None = None) -> None:
        self.agents = agents or ENTERPRISE_AGENTS

    def plan(self, campaign: CampaignDraft) -> MissionPlan:
        return self.daily_cycle(campaign)

    def daily_cycle(self, campaign: CampaignDraft) -> MissionPlan:
        gate = evaluate_campaign_policy(campaign)
        recommendations = tuple(
            sorted(
                (agent.recommend(campaign) for agent in self.agents),
                key=lambda item: (item.priority, item.agent),
            )
        )
        from ads_ai_agents.platform.agents.competitor_discovery import competitor_search_seeds
        from ads_ai_agents.platform.product_facts import organization_profile

        org = organization_profile()
        target = campaign.business_goal.effective_target_cpa_usd or campaign.grant.target_cpa_usd
        subject = org["main_offering"] or org["name"] or "YOUR_BRAND"
        north_star = (
            f"Utilize ${campaign.grant.monthly_budget_usd:,.0f}/mo Google Ad Grant for {subject} "
            f"at ~${target:.2f} qualified-lead CPA with daily sense→compete→structure→create→allocate→guard loop."
        )
        organization = {
            "type": org["type"],
            "main_offering": org["main_offering"],
            "description": org["description"],
            "website_url": org["website_url"],
            "product_seed_terms": org["seed_terms"],
            "competitor_search_seeds": competitor_search_seeds(org),
        }
        return MissionPlan(
            campaign=campaign,
            gate=gate,
            recommendations=recommendations,
            workflow=ENTERPRISE_WORKFLOW,
            daily_checklist=self._daily_checklist(campaign, recommendations),
            north_star=north_star,
            organization=organization,
        )

    def _daily_checklist(
        self,
        campaign: CampaignDraft,
        recommendations: tuple[AgentRecommendation, ...],
    ) -> tuple[str, ...]:
        target = campaign.business_goal.effective_target_cpa_usd or campaign.grant.target_cpa_usd
        checklist = [
            "Sync account snapshot (campaigns, budgets, change events, recommendations) — read-only.",
            "Pull Search Terms Insights; promote converters / negate waste.",
            f"Score CPA vs ${target:.2f}; draft budget shifts (approval if >20% move).",
            "Refresh competitor/creative notes if Auction Insights or Ads Transparency export landed.",
            "Queue asset fixes for weak Ad Strength or disapproved creatives.",
            "Policy gate must be non-blocked before any publish/queue action.",
            "Write audit record of recommendations and human decisions.",
        ]
        auto_safe = [rec.agent for rec in recommendations if rec.auto_apply_safe]
        if auto_safe:
            checklist.append("Auto-apply-safe agents today: " + ", ".join(auto_safe) + " (still audited).")
        needs_approval = [rec.agent for rec in recommendations if rec.requires_approval and rec.risk.value in {"high", "critical"}]
        if needs_approval:
            checklist.append("Human approval required before acting on: " + ", ".join(dict.fromkeys(needs_approval)) + ".")
        return tuple(checklist)
