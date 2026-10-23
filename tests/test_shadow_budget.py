from ads_ai_agents.intelligence.shadow_budget import plan_shadow_reallocation


def test_shadow_proposal_respects_guardrails():
    campaigns = [
        {
            "name": "Search Articles EN",
            "status": "ENABLED",
            "cost": 1000,
            "conversions": 600,
            "cpa": 1.66,
            "daily_budget": 100,
            "channel": "SEARCH",
        },
        {
            "name": "PMax EN",
            "status": "ENABLED",
            "cost": 5000,
            "conversions": 660,
            "cpa": 7.56,
            "daily_budget": 200,
            "channel": "PERFORMANCE_MAX",
        },
    ]
    proposals = plan_shadow_reallocation(campaigns, observation_days=14)
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.mode == "shadow"
    assert proposal.approval_required is True
    assert proposal.proposed_change_usd_per_day <= 20
    assert proposal.from_campaign == "PMax EN"
    assert proposal.to_campaign == "Search Articles EN"


def test_shadow_blocks_unhealthy_tracking():
    campaigns = [
        {"name": "A", "status": "ENABLED", "cost": 100, "conversions": 50, "cpa": 2, "daily_budget": 50},
        {"name": "B", "status": "ENABLED", "cost": 400, "conversions": 40, "cpa": 10, "daily_budget": 50},
    ]
    proposals = plan_shadow_reallocation(
        campaigns,
        conversion_tracking_healthy=False,
        observation_days=3,
    )
    assert proposals
    assert "conversion_tracking_unhealthy" in proposals[0].blocked_reasons
    assert "less_than_14_days_data" in proposals[0].blocked_reasons
