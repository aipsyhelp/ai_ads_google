from ads_ai_agents.intelligence.recovery_playbook import (
    CANONICAL_BIDDING,
    REFUSE_CLEAR_TCPA,
    REFUSE_GOAL_DRIFT,
    REFUSE_LEGACY_TCPA,
    REFUSE_MAX_CLICKS,
    refuse,
)


def test_canonical_bidding_is_maximize_conversions():
    assert CANONICAL_BIDDING == "MAXIMIZE_CONVERSIONS"


def test_refuse_max_clicks_returns_nonzero():
    code = refuse("bridge_maxclicks_recovery.py", REFUSE_MAX_CLICKS)
    assert code == 2
    assert "Maximize Clicks" in REFUSE_MAX_CLICKS


def test_refuse_legacy_tcpa_returns_nonzero():
    code = refuse("apply_grant_recovery.py", REFUSE_LEGACY_TCPA)
    assert code == 2
    assert "tCPA $200–$500" in REFUSE_LEGACY_TCPA


def test_refuse_clear_tcpa_and_goal_drift_return_nonzero():
    assert refuse("apply_full_delivery_complex.py", REFUSE_CLEAR_TCPA) == 2
    assert refuse("apply_funnel_perfect.py", REFUSE_CLEAR_TCPA) == 2
    assert refuse("revert_to_maximize_conversions.py", REFUSE_CLEAR_TCPA) == 2
    assert refuse("rebuild_grant_goals.py", REFUSE_GOAL_DRIFT) == 2
    assert refuse("promote_chat_open_goal.py", REFUSE_GOAL_DRIFT) == 2

