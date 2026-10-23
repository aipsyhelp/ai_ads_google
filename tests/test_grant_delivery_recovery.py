from ads_ai_agents.intelligence.grant_delivery_recovery import GrantDeliveryRecovery


def _inventory(*, quality_conversions: float, market_cpc_micros: int) -> dict:
    return {
        "conversion_actions": [
            {
                "resource_name": "customers/1/conversionActions/1",
                "name": "www.example.com (web) chat_open",
                "status": "ENABLED",
                "primary_for_goal": False,
                "included": False,
                "counting_type": "MANY_PER_CLICK",
                "category": "DEFAULT",
            },
            {
                "resource_name": "customers/1/conversionActions/2",
                "name": "www.example.com (web) chat_message_sent",
                "status": "ENABLED",
                "primary_for_goal": True,
                "included": True,
                "counting_type": "ONE_PER_CLICK",
                "category": "CONTACT",
            },
        ],
        "conversion_goals": [
            {
                "resource_name": "customers/1/customerConversionGoals/CONTACT~WEBSITE",
                "category": "CONTACT",
                "origin": "WEBSITE",
                "biddable": True,
            }
        ],
        "campaigns": [
            {
                "resource_name": "customers/1/campaigns/1",
                "id": 1,
                "name": "Search UA",
                "primary_status": "ELIGIBLE",
                "bidding_strategy_type": "TARGET_SPEND",
            },
            {
                "resource_name": "customers/1/campaigns/2",
                "id": 2,
                "name": "Search EN",
                "primary_status": "ELIGIBLE",
                "bidding_strategy_type": "MAXIMIZE_CONVERSIONS",
            },
        ],
        "quality_conversions_30d": quality_conversions,
        "market_cpc_micros": market_cpc_micros,
    }


def test_expensive_auctions_force_smart_bidding_even_without_volume():
    """A $2 cap cannot win a $7 auction, so waiting for volume would never end."""
    recovery = GrantDeliveryRecovery.__new__(GrantDeliveryRecovery)
    recovery.inventory = lambda: _inventory(
        quality_conversions=0.0, market_cpc_micros=7_000_000
    )

    result = recovery.apply(dry_run=True)

    assert result.desired_bidding_strategy == "MAXIMIZE_CONVERSIONS"
    assert result.recovery_target_cpa_micros == 2_000_000_000
    assert [c["name"] for c in result.campaigns_planned] == ["Search UA"]
    assert any(w["op"] == "bidding_strategy" for w in result.warnings)


def test_cheap_auctions_still_stay_on_maximize_conversions():
    recovery = GrantDeliveryRecovery.__new__(GrantDeliveryRecovery)
    recovery.inventory = lambda: _inventory(
        quality_conversions=0.0, market_cpc_micros=900_000
    )

    result = recovery.apply(dry_run=True)

    assert result.desired_bidding_strategy == "MAXIMIZE_CONVERSIONS"
    assert result.recovery_target_cpa_micros == 0
    assert [c["name"] for c in result.campaigns_planned] == ["Search UA"]


def test_chat_entry_is_promoted_not_demoted():
    """Chat entry carries the volume; demoting it left bidding with a zero signal."""
    recovery = GrantDeliveryRecovery.__new__(GrantDeliveryRecovery)
    recovery.inventory = lambda: _inventory(
        quality_conversions=0.0, market_cpc_micros=7_000_000
    )

    result = recovery.apply(dry_run=True)

    assert len(result.conversion_actions_planned) == 1
    planned = result.conversion_actions_planned[0]
    assert planned["name"].endswith("chat_open")
    assert planned["desired_primary_for_goal"] is True
    assert planned["desired_category"] == "CONTACT"


def test_many_per_click_import_is_warning_not_impossible_mutation():
    recovery = GrantDeliveryRecovery.__new__(GrantDeliveryRecovery)
    recovery.inventory = lambda: {
        "conversion_actions": [
            {
                "resource_name": "customers/1/conversionActions/1",
                "name": "www.example.com (web) chat_message_sent",
                "status": "ENABLED",
                "primary_for_goal": True,
                "included": True,
                "counting_type": "MANY_PER_CLICK",
                "category": "CONTACT",
            }
        ],
        "conversion_goals": [
            {
                "resource_name": "customers/1/customerConversionGoals/CONTACT~WEBSITE",
                "category": "CONTACT",
                "origin": "WEBSITE",
                "biddable": True,
            }
        ],
        "campaigns": [],
        "quality_conversions_30d": 20.0,
    }

    result = recovery.apply(dry_run=True)

    assert result.conversion_actions_planned == []
    assert result.desired_bidding_strategy == "MAXIMIZE_CONVERSIONS"
    assert any(warning["op"] == "counting_type" for warning in result.warnings)
