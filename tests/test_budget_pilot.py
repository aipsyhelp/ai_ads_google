from ads_ai_agents.intelligence.budget_pilot import plan_budget_pilot
from ads_ai_agents.intelligence.change_ledger import ChangeLedger
from ads_ai_agents.intelligence.controlled_policy import ControlledLivePolicy
from ads_ai_agents.adapters.automation_mode import AutomationMode


def _pilot_campaigns() -> list[dict]:
    return [
        {
            "id": "101",
            "name": "Ser / www.example.com / Articles / EN / Brand_Search",
            "status": "ENABLED",
            "daily_budget": 100.0,
            "budget_resource_name": "customers/1/campaignBudgets/1",
            "quality_conversions": 5,
            "cpqa": 180,
            "classification": "stable_winner_candidate",
            "spend": 500,
        },
        {
            "id": "102",
            "name": "PMax / www.example.com / Home Page / EN / Brand_Search",
            "status": "ENABLED",
            "daily_budget": 50.0,
            "budget_resource_name": "customers/1/campaignBudgets/2",
            "quality_conversions": 0,
            "cpqa": None,
            "classification": "cheap_useless_conversions",
            "spend": 400,
        },
    ]


def test_budget_pilot_caps_at_10_percent(tmp_path):
    policy = ControlledLivePolicy(
        mode=AutomationMode.CONTROLLED_LIVE,
        max_budget_change_percent=10.0,
        auto_budget_under_cap=True,
    )
    ledger = ChangeLedger()
    ledger.path = tmp_path / "ledger.jsonl"

    changes = plan_budget_pilot(
        _pilot_campaigns(), policy=policy, ledger=ledger, account_daily_sum=150
    )

    assert changes
    for change in changes:
        if change.direction in {"increase", "decrease"}:
            assert change.change_percent <= 10.01
            assert change.auto_apply is True


def test_budget_changes_stay_manual_until_auto_budget_gate_is_opened(tmp_path):
    """`auto_budget_under_cap` is the kill switch for unattended budget writes."""
    policy = ControlledLivePolicy(
        mode=AutomationMode.CONTROLLED_LIVE,
        max_budget_change_percent=10.0,
    )
    ledger = ChangeLedger()
    ledger.path = tmp_path / "ledger.jsonl"

    changes = plan_budget_pilot(
        _pilot_campaigns(), policy=policy, ledger=ledger, account_daily_sum=150
    )

    assert changes
    assert all(change.auto_apply is False for change in changes)


def test_increase_blocked_when_it_would_break_the_grant_account_cap(tmp_path):
    policy = ControlledLivePolicy(
        mode=AutomationMode.CONTROLLED_LIVE,
        max_budget_change_percent=10.0,
        auto_budget_under_cap=True,
        max_account_daily_budget=329.0,
    )
    ledger = ChangeLedger()
    ledger.path = tmp_path / "ledger.jsonl"

    changes = plan_budget_pilot(
        _pilot_campaigns(), policy=policy, ledger=ledger, account_daily_sum=329.0
    )

    increases = [change for change in changes if change.direction == "increase"]
    assert increases
    for change in increases:
        assert change.blocked_reasons == ["account_daily_budget_cap"]
        assert change.auto_apply is False
        assert change.requires_approval is True


def test_youtube_pause_requires_approval(tmp_path):
    policy = ControlledLivePolicy(mode=AutomationMode.CONTROLLED_LIVE)
    ledger = ChangeLedger()
    ledger.path = tmp_path / "ledger.jsonl"
    campaigns = [
        {
            "id": "201",
            "name": "Performance Max-YouTube",
            "status": "ENABLED",
            "daily_budget": 20.0,
            "budget_resource_name": "customers/1/campaignBudgets/9",
            "quality_conversions": 0,
            "classification": "zero_quality_spender",
            "spend": 300,
        }
    ]
    changes = plan_budget_pilot(campaigns, policy=policy, ledger=ledger)
    assert len(changes) == 1
    assert changes[0].direction == "pause_approval"
    assert changes[0].requires_approval is True
    assert changes[0].auto_apply is False
