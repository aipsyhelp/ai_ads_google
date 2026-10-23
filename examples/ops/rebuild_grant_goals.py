"""Rebuild the Grant conversion goal set so Smart Bidding has a reachable signal.

Today exactly one conversion action is counted in the Conversions metric
(chat_message_sent, category CONTACT) and it records zero events, because the
GTM tag behind it waits for a dataLayer push the application never emits. Every
action that does fire sits in a category whose customer goal is non-biddable or
has primary_for_goal=false, so bidding sees nothing at all. An Ad Grants account
with zero conversions in a month can be deactivated without warning.

This script promotes genuine user actions to primary and demotes signals that
are either mission-irrelevant or forbidden as primary by the Ad Grants policy
(homepage visits, page views, time on site, scroll depth).

Read-only by default; pass --apply to mutate.
"""

from __future__ import annotations

import argparse
import sys

from ads_ai_agents.adapters.google_ads_goals import GoogleAdsGoalsWriter
from ads_ai_agents.adapters.google_ads_readonly import GoogleAdsReadOnlyAdapter

# Real actions a visitor performs. Policy calls these meaningful: information
# requests, contact attempts, app acquisition, account creation.
PROMOTE = {
    "www.example.com (web) form_submit": "form submission — information request",
    "www.example.com (web) click_email": "email contact",
    "www.example.com (web) click_messenger": "messenger contact",
    "www.example.com (web) click_store_android": "Google Play click-through",
    "www.example.com (web) click_store_ios": "App Store click-through",
    "www.example.com (web) login_success": "successful login — account created",
    "www.example.com (web) chat_message_sent": "chat message — north star",
    # The only action with live volume. Mechanically it is a page load on the
    # chat host, so it stays here strictly as the bridge that keeps the account
    # compliant until chat_open / chat_message_sent are wired end to end.
    "www.example.com (web) chat_open": "chat entry — temporary bridge",
}

# Mission-irrelevant or Ad Grants–forbidden as primary.
DEMOTE = {
    "YouTube channel subscriptions": "YouTube subscriptions are not on-site actions",
    "www.example.com (web) scroll (1)": "scroll is not allowed as primary for Ad Grants",
    "Page view (Page load www.example.com/en)": "page view is not allowed as primary for Ad Grants",
    "Android installs (all other apps)": "generic Android installs — not our app",
    "www.example.com (web) login_start": "micro-event; only login_success is primary",
    "www.example.com (web) form_start (1)": "micro-event; only form_submit is primary",
    "www.example.com (web) chat_open": "bridge; bid on chat_message_sent / chat_open",
}

# Never promote: Ad Grants forbids these as primary conversions.
FORBIDDEN_AS_PRIMARY = (
    "page_view",
    "Page view",
    "session_start",
    "first_visit",
    "user_engagement",
    "scroll",
    "scroll_page",
    "seconds_on_page",
)

ACTIONS_QUERY = """
SELECT
  conversion_action.id,
  conversion_action.name,
  conversion_action.status,
  conversion_action.category,
  conversion_action.primary_for_goal,
  conversion_action.include_in_conversions_metric
FROM conversion_action
WHERE conversion_action.status = 'ENABLED'
"""


def load_actions() -> list[dict]:
    adapter = GoogleAdsReadOnlyAdapter()
    rows = adapter._search(adapter.settings.customer_id, ACTIONS_QUERY)
    out = []
    for row in rows:
        a = row.conversion_action
        out.append(
            {
                "id": str(a.id),
                "name": a.name,
                "category": a.category.name,
                "primary": bool(a.primary_for_goal),
                "counted": bool(a.include_in_conversions_metric),
            }
        )
    return out


def build_plan(actions: list[dict]) -> tuple[list[dict], list[str]]:
    plan: list[dict] = []
    warnings: list[str] = []
    by_name = {a["name"]: a for a in actions}

    for name, reason in PROMOTE.items():
        action = by_name.get(name)
        if action is None:
            warnings.append(f"no active action '{name}' found — skipped")
            continue
        if any(bad in name for bad in FORBIDDEN_AS_PRIMARY):
            warnings.append(f"REFUSED: '{name}' is not allowed as primary by policy")
            continue
        if action["primary"]:
            warnings.append(f"'{name}' is already primary — no change")
            continue
        plan.append({**action, "target": True, "reason": reason})

    for name, reason in DEMOTE.items():
        action = by_name.get(name)
        if action is None:
            warnings.append(f"no active action '{name}' found — skipped")
            continue
        if not action["primary"]:
            warnings.append(f"'{name}' is already not primary — no change")
            continue
        plan.append({**action, "target": False, "reason": reason})

    for action in actions:
        if action["primary"] and any(bad in action["name"] for bad in FORBIDDEN_AS_PRIMARY):
            warnings.append(
                f"WARNING: '{action['name']}' is primary although policy forbids it — demote it"
            )
    return plan, warnings


def main() -> int:
    from ads_ai_agents.intelligence.recovery_playbook import REFUSE_GOAL_DRIFT, refuse

    return refuse("rebuild_grant_goals.py", REFUSE_GOAL_DRIFT)


def _legacy_main_disabled() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply changes (otherwise plan only).")
    args = parser.parse_args()

    actions = load_actions()
    plan, warnings = build_plan(actions)

    print(f"=== active conversion actions: {len(actions)} ===")
    print(f"    primary now: {sum(1 for a in actions if a['primary'])}")
    print(f"    counted in 'Conversions': {sum(1 for a in actions if a['counted'])}")

    print(f"\n=== planned changes: {len(plan)} ===")
    for item in plan:
        arrow = "→ PRIMARY" if item["target"] else "→ secondary"
        print(f"  {arrow:<12} [{item['category']:<10}] {item['name']}")
        print(f"               {item['reason']}")

    if warnings:
        print("\n=== notes ===")
        for w in warnings:
            print(f"  {w}")

    if not args.apply:
        print("\n  plan mode. To apply: --apply")
        return 0
    if not plan:
        print("\n  nothing to change")
        return 0

    from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed

    assert_mutations_allowed()
    writer = GoogleAdsGoalsWriter()
    customer_id = writer.settings.customer_id
    ok, failed = 0, 0
    print()
    for item in plan:
        try:
            writer._set_primary_for_goal(customer_id, item["id"], primary=item["target"])
            ok += 1
            print(f"  applied: {item['name']} primary={item['target']}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {item['name']}: {exc}")
    print(f"\n  succeeded {ok}, failed {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
