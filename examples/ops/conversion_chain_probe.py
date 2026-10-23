"""Read-only probe of the conversion chain: actions, goal membership, campaign wiring.

The `conversions audit` report leaves include_in_conversions_metric, counting_type
and category empty, so the account can look like "0 conversions" without showing
whether the cause is a missing signal or a signal that is deliberately excluded
from bidding. This probe asks for those fields directly.
"""

from __future__ import annotations

import sys

from ads_ai_agents.adapters.google_ads_readonly import GoogleAdsReadOnlyAdapter

ACTIONS = """
SELECT
  conversion_action.id,
  conversion_action.name,
  conversion_action.status,
  conversion_action.type,
  conversion_action.category,
  conversion_action.origin,
  conversion_action.primary_for_goal,
  conversion_action.include_in_conversions_metric,
  conversion_action.counting_type,
  conversion_action.click_through_lookback_window_days
FROM conversion_action
ORDER BY conversion_action.name
"""

# Per-campaign goal wiring. A campaign either inherits the account-default
# conversion goals or overrides them, and that distinction decides whether
# fixing primary_for_goal at account level is enough.
CAMPAIGN_GOALS = """
SELECT
  campaign.id,
  campaign.name,
  campaign.status,
  campaign.advertising_channel_type,
  campaign.bidding_strategy_type,
  campaign.selective_optimization.conversion_actions,
  campaign_budget.amount_micros
FROM campaign
WHERE campaign.status = 'ENABLED'
ORDER BY campaign.name
"""

CUSTOMER_GOALS = """
SELECT
  customer_conversion_goal.category,
  customer_conversion_goal.origin,
  customer_conversion_goal.biddable
FROM customer_conversion_goal
"""

TRACKING = """
SELECT
  customer.id,
  customer.descriptive_name,
  customer.conversion_tracking_setting.conversion_tracking_status,
  customer.conversion_tracking_setting.conversion_tracking_id,
  customer.conversion_tracking_setting.google_ads_conversion_customer
FROM customer
LIMIT 1
"""


def run(adapter: GoogleAdsReadOnlyAdapter, title: str, query: str, render) -> None:
    print(f"\n=== {title} ===")
    try:
        rows = list(adapter._search(adapter.settings.customer_id, query))
    except Exception as exc:  # pragma: no cover - live API surface
        print(f"  query failed: {exc}")
        return
    if not rows:
        print("  no rows")
        return
    for line in render(rows):
        print("  " + line)


def render_tracking(rows):
    for r in rows:
        s = r.customer.conversion_tracking_setting
        yield f"account={r.customer.descriptive_name} id={r.customer.id}"
        yield f"tracking_status={s.conversion_tracking_status.name}"
        yield f"conversion_tracking_id={s.conversion_tracking_id}"
        yield f"conversion_owner={s.google_ads_conversion_customer}"


def render_actions(rows):
    yield f"{'primary':<8}{'incl':<6}{'status':<10}{'category':<26}{'origin':<14}{'counting':<12}name"
    yield "-" * 118
    for r in rows:
        a = r.conversion_action
        yield (
            f"{'PRIMARY' if a.primary_for_goal else '-':<8}"
            f"{'yes' if a.include_in_conversions_metric else 'NO':<6}"
            f"{a.status.name:<10}{a.category.name:<26}{a.origin.name:<14}"
            f"{a.counting_type.name:<12}{a.name}"
        )


def render_customer_goals(rows):
    yield f"{'biddable':<10}{'category':<28}origin"
    yield "-" * 60
    for r in rows:
        g = r.customer_conversion_goal
        yield f"{'YES' if g.biddable else 'no':<10}{g.category.name:<28}{g.origin.name}"


def render_campaigns(rows):
    yield f"{'strategy':<26}{'channel':<10}{'own goals':<14}name"
    yield "-" * 90
    for r in rows:
        c = r.campaign
        sel = list(c.selective_optimization.conversion_actions)
        yield (
            f"{c.bidding_strategy_type.name:<26}"
            f"{c.advertising_channel_type.name:<10}"
            f"{(str(len(sel)) + ' actions') if sel else 'inherits':<14}{c.name}"
        )


def main() -> int:
    adapter = GoogleAdsReadOnlyAdapter()
    run(adapter, "conversion tracking status", TRACKING, render_tracking)
    run(adapter, "account conversion goals (what actually drives bidding)", CUSTOMER_GOALS, render_customer_goals)
    run(adapter, "conversion actions", ACTIONS, render_actions)
    run(adapter, "campaigns and their goals", CAMPAIGN_GOALS, render_campaigns)
    return 0


if __name__ == "__main__":
    sys.exit(main())
