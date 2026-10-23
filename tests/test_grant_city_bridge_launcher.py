from ads_ai_agents.intelligence.ads_public_urls import topic_article_url
from ads_ai_agents.intelligence.grant_city_bridge_launcher import (
    CITY_BRIDGES,
    GrantCityBridgeLauncher,
)


def test_city_bridge_specs_are_grant_safe():
    for bridge in CITY_BRIDGES:
        assert bridge["budget_usd"] > 0
        assert bridge["geo_ids"]
        assert len(bridge["ad_groups"]) >= 2
        for group in bridge["ad_groups"]:
            assert len(group["keywords"]) >= 2
            assert all(" " in keyword for keyword in group["keywords"])
            url = topic_article_url(bridge["locale"], group["name"])
            assert url.startswith("https://www.example.com/")
            assert "/news" in url


def test_city_bridge_dry_run_plans_without_mutation(monkeypatch):
    launcher = GrantCityBridgeLauncher.__new__(GrantCityBridgeLauncher)
    monkeypatch.setattr(
        launcher,
        "_brand_gate",
        lambda: {"pass": True, "failed": [], "checked": 1},
    )
    monkeypatch.setattr(
        launcher,
        "existing_campaigns",
        lambda: {
            "Ser / www.example.com / Home Page / UA / City_A / Brand_Search": {
                "id": "1",
                "status": "ENABLED",
                "budget_resource_name": "customers/1/campaignBudgets/1",
                "daily_budget": 8.0,
            }
        },
    )

    result = launcher.apply(dry_run=True)

    assert result.gate["pass"] is True
    assert len(result.planned) == 2
    assert result.created == []
    assert result.paused_existing == [
        "Ser / www.example.com / Home Page / UA / City_A / Brand_Search"
    ]
    assert result.errors == []
