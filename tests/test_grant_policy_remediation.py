from ads_ai_agents.intelligence.grant_policy_remediation import (
    GRANT_DAILY_CAP_USD,
    GrantPolicyRemediation,
)


def _campaign(
    campaign_id: str,
    name: str,
    *,
    ad_groups: int = 3,
    sitelinks: int = 16,
    enabled_keywords: int = 20,
    budget_usd: float = 20.0,
) -> dict:
    return {
        "resource_name": f"customers/1/campaigns/{campaign_id}",
        "id": campaign_id,
        "name": name,
        "bidding_strategy_type": "TARGET_SPEND",
        "budget_usd": budget_usd,
        "ad_groups": ad_groups,
        "sitelinks": sitelinks,
        "enabled_keywords": enabled_keywords,
    }


def test_compliant_account_plans_no_changes():
    plan = GrantPolicyRemediation.plan(
        [_campaign("1", "Ser / UA"), _campaign("2", "Ser / EN")]
    )

    assert plan["violations"] == []
    assert plan["pause_planned"] == []
    assert plan["warnings"] == []
    assert plan["budget_after_usd"] == 40.0


def test_non_serving_violator_is_paused_and_frees_budget():
    campaigns = [
        _campaign("1", "Ser / UA", budget_usd=29.0),
        _campaign(
            "2",
            "DSA / EN",
            ad_groups=1,
            sitelinks=0,
            enabled_keywords=0,
            budget_usd=300.0,
        ),
    ]

    plan = GrantPolicyRemediation.plan(campaigns)

    assert [item["name"] for item in plan["pause_planned"]] == ["DSA / EN"]
    assert plan["budget_before_usd"] == 329.0
    assert plan["budget_after_usd"] == 29.0
    assert not any(item["op"] == "budget_cap" for item in plan["warnings"])


def test_serving_violator_is_reported_not_paused():
    campaigns = [
        _campaign("1", "Ser / UA", ad_groups=1, sitelinks=1, enabled_keywords=12)
    ]

    plan = GrantPolicyRemediation.plan(campaigns)

    assert plan["pause_planned"] == []
    assert len(plan["violations"]) == 1
    assert plan["warnings"][0]["op"] == "structure_violation"


def test_budget_over_cap_is_flagged_after_pauses():
    campaigns = [
        _campaign("1", "Ser / UA", budget_usd=GRANT_DAILY_CAP_USD),
        _campaign("2", "Ser / EN", budget_usd=50.0),
    ]

    plan = GrantPolicyRemediation.plan(campaigns)

    assert plan["pause_planned"] == []
    assert any(item["op"] == "budget_cap" for item in plan["warnings"])
