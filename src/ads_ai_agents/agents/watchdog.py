from __future__ import annotations

from ads_ai_agents.core.domain import (
    AgentRecommendation,
    CampaignDraft,
    RiskLevel,
    WorkflowStage,
)


class WatchdogAgent:
    name = "watchdog"
    stage = WorkflowStage.GUARD

    def recommend(self, campaign: CampaignDraft) -> AgentRecommendation:
        grant = campaign.grant
        target = campaign.business_goal.effective_target_cpa_usd or grant.target_cpa_usd
        first_day_guard = max(min(campaign.daily_budget_usd, grant.max_daily_budget_usd) * 0.35, 10)

        actions: list[str] = [
            "Hourly: spend pacing, conversion outage, CPC/CPA spikes, disapproved assets, policy flags.",
            "Daily: Change Event audit vs intended agent actions; alert on unexpected human/API edits.",
            f"CPA breach alert at >${target * 1.5:.2f}; emergency pause candidates at >${target * 2:.2f} for 24h.",
            f"First-day / unstable telemetry budget guard ≤ ${first_day_guard:.2f} until conversions healthy.",
            "Scripts layer for fast defense; API/control plane for durable mutations + audit trail.",
            "Never auto-publish budget +20%, new portfolios, or sensitive copy — queue for approval.",
        ]

        perf = campaign.performance
        risk = RiskLevel.MEDIUM
        if perf is not None:
            if perf.conversion_outage:
                actions.append("ACTIVE: conversion outage — pause non-brand learning, page on-call.")
                risk = RiskLevel.CRITICAL
            if perf.disapproved_assets:
                actions.append(f"ACTIVE: {perf.disapproved_assets} disapproved assets — creative/policy hotfix.")
                risk = RiskLevel.HIGH if risk is not RiskLevel.CRITICAL else risk
            if perf.cpa_usd is not None and perf.cpa_usd > target * 2:
                actions.append(f"ACTIVE: CPA ${perf.cpa_usd:.2f} breach — trigger Budget Agent reallocation.")
                risk = RiskLevel.HIGH if risk not in {RiskLevel.CRITICAL} else risk

        return AgentRecommendation(
            agent=self.name,
            stage=self.stage,
            title="Daily ops guardrails",
            rationale="Enterprise automation stays trusted only with hourly defense and immutable audit.",
            actions=tuple(actions),
            risk=risk,
            auto_apply_safe=True,
            requires_approval=False,
            priority=60,
        )
