from __future__ import annotations

from ads_ai_agents.core.domain import (
    AgentRecommendation,
    CampaignDraft,
    Portfolio,
    RiskLevel,
    WorkflowStage,
)


class BudgetAgent:
    name = "budget"
    stage = WorkflowStage.ALLOCATE

    def recommend(self, campaign: CampaignDraft) -> AgentRecommendation:
        grant = campaign.grant
        target = campaign.business_goal.effective_target_cpa_usd or grant.target_cpa_usd
        daily_cap = min(campaign.daily_budget_usd, grant.max_daily_budget_usd)
        actions: list[str] = [
            (
                f"Pacing target: ${grant.monthly_budget_usd:,.0f}/mo ≈ ${grant.max_daily_budget_usd:.0f}/day; "
                f"this draft daily=${daily_cap:.0f}; north-star CPA=${target:.2f}."
            ),
            "Reallocate daily toward campaigns/ad groups with CPA ≤ target and stable lead quality.",
            "Throttle or pause units with CPA > 2× target for 3+ days unless strategic brand/TOFU with proven assist.",
            "Do not starve Core winners to fund low-quality TOFU; fund TOFU only with leftover grant headroom.",
        ]

        perf = campaign.performance
        risk = RiskLevel.MEDIUM
        if perf is not None:
            if perf.spend_usd_mtd is not None:
                actions.append(
                    f"MTD spend ${perf.spend_usd_mtd:,.0f} vs monthly grant ${grant.monthly_budget_usd:,.0f} — "
                    "adjust daily caps to finish month near full utilization without dumping into junk queries."
                )
            if perf.cpa_usd is not None:
                if perf.cpa_usd <= target:
                    actions.append(
                        f"CPA ${perf.cpa_usd:.2f} ≤ target ${target:.2f}: increase budget on winning ad groups up to daily cap."
                    )
                    risk = RiskLevel.LOW
                elif perf.cpa_usd <= target * 2:
                    actions.append(
                        f"CPA ${perf.cpa_usd:.2f} above target: freeze exploration; tighten negatives; keep Core only."
                    )
                    risk = RiskLevel.MEDIUM
                else:
                    actions.append(
                        f"CPA ${perf.cpa_usd:.2f} >> ${target:.2f}: emergency reallocation — pause offenders, protect Core."
                    )
                    risk = RiskLevel.HIGH
            if perf.impression_share_lost_budget is not None and perf.impression_share_lost_budget > 0.2:
                if perf.cpa_usd is None or perf.cpa_usd <= target * 1.25:
                    actions.append(
                        f"Lost IS (budget) {perf.impression_share_lost_budget:.0%}: room to raise budget on efficient units."
                    )
            if perf.leads_mtd is not None and target > 0:
                implied = grant.monthly_budget_usd / max(perf.leads_mtd, 1)
                actions.append(
                    f"MTD leads={perf.leads_mtd}; implied monthly CPA≈${implied:.2f} if spend fully utilized."
                )
        else:
            actions.append(
                "No live snapshot yet — run read-only sync, then reallocation rules against CPA=$2 guardrails."
            )

        if campaign.portfolio is Portfolio.GRANT_SEARCH:
            actions.append(
                "Grant rule: maximize efficient utilization of unused grant; never buy volume that breaks $2 quality CPA."
            )

        actions.append("Any single-day budget uplift >20% requires human approval.")

        return AgentRecommendation(
            agent=self.name,
            stage=self.stage,
            title="Budget pacing & reallocation",
            rationale="Enterprise media teams reallocate daily against CPA and quality, not optimization score.",
            actions=tuple(actions),
            risk=risk,
            requires_approval=True,
            priority=40,
        )
