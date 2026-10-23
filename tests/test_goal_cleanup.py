from ads_ai_agents.intelligence.goal_cleanup import build_goal_cleanup_plan


def _action(name: str, tier: str, *, conversions: float, primary: bool = True) -> dict:
    return {
        "id": name,
        "name": name,
        "tier": tier,
        "primary_for_goal": primary,
        "include_in_conversions_metric": False,
        "conversions": conversions,
    }


def test_only_deepest_chat_action_stays_primary():
    """Proxy goals must not stay primary, even when their tier looks valuable.

    `chat_open` (chat opened) and the generic GA4 form event inflate the
    Conversions column ahead of the real activation event, so bidding learns on
    the proxy instead of `chat_message_sent`.
    """
    actions = [
        _action("www.example.com (web) session_start", "micro", conversions=652),
        _action(
            "www.example.com (web) chat_open",
            "activation",
            conversions=30,
        ),
        _action("www.example.com (web) form_submit", "acquisition", conversions=12),
        _action("www.example.com (web) chat_message_sent", "activation", conversions=18),
    ]

    plan = build_goal_cleanup_plan(actions, spend_mtd=6647)
    by_name = {item["name"]: item for item in plan["actions"]}

    assert by_name["www.example.com (web) session_start"]["decision"] == "demote_to_secondary"
    assert (
        by_name["www.example.com (web) chat_open"]["decision"]
        == "demote_to_secondary"
    )
    assert by_name["www.example.com (web) form_submit"]["decision"] == "demote_to_secondary"
    assert by_name["www.example.com (web) chat_message_sent"]["decision"] == "keep_primary"
    assert plan["mutate_executed"] is False


def test_primary_quality_goal_without_volume_requires_tracking_repair():
    actions = [_action("www.example.com (web) chat_message_sent", "activation", conversions=0)]

    plan = build_goal_cleanup_plan(actions)

    assert plan["actions"][0]["decision"] == "keep_primary_after_tracking_repair"


def test_demoting_proxies_shrinks_projected_reported_conversions():
    actions = [
        _action("www.example.com (web) session_start", "micro", conversions=652),
        _action("www.example.com (web) chat_message_sent", "activation", conversions=18),
    ]

    plan = build_goal_cleanup_plan(actions, spend_mtd=1800)
    summary = plan["summary"]

    assert summary["micro_conversions_currently_in_mix"] == 652
    assert summary["projected_reported_conversions_after_cleanup"] == 18
    assert summary["projected_cpa_after_cleanup"] == 100.0


def test_plan_never_mutates():
    plan = build_goal_cleanup_plan([_action("whatever_unmapped_event", "unknown", conversions=5)])

    assert plan["mode"] == "plan_only_no_mutate"
    assert plan["approval_required"] is True
    assert plan["actions"][0]["decision"] == "manual_review"
