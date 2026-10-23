from datetime import date

from ads_ai_agents.intelligence.budget_pacing import build_remaining_month_plan


def test_remaining_month_plan_pauses_youtube_and_funds_articles():
    campaigns = [
        {
            "id": "1",
            "name": "Performance Max-YouTube",
            "status": "ENABLED",
            "channel": "PERFORMANCE_MAX",
            "daily_budget": 50,
            "budget_resource_name": "customers/1/campaignBudgets/1",
            "cost": 400,
            "conversions": 0,
        },
        {
            "id": "2",
            "name": "Ser / www.example.com / Articles / EN / Brand_Search",
            "status": "ENABLED",
            "channel": "SEARCH",
            "daily_budget": 100,
            "budget_resource_name": "customers/1/campaignBudgets/2",
            "cost": 1900,
            "conversions": 1400,
        },
        {
            "id": "3",
            "name": "DSA / www.example.com / Home Page / EN / Brand_Search",
            "status": "ENABLED",
            "channel": "SEARCH",
            "daily_budget": 300,
            "budget_resource_name": "customers/1/campaignBudgets/3",
            "cost": 80,
            "conversions": 13,
        },
    ]
    plan = build_remaining_month_plan(
        campaigns,
        spend_mtd=6647,
        today=date(2026, 7, 20),
    )
    assert plan["remaining_usd"] == 3353.0
    assert plan["days_left"] == 12
    by_name = {c["campaign_name"]: c for c in plan["changes"]}
    assert by_name["Performance Max-YouTube"]["action"] == "pause"
    assert by_name["Ser / www.example.com / Articles / EN / Brand_Search"]["action"] == "set_budget"
    assert by_name["DSA / www.example.com / Home Page / EN / Brand_Search"]["action"] == "pause"
