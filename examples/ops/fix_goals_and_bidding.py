#!/usr/bin/env python3
"""Fix conversion-goal funnel + keep Grant-safe Smart Bidding competitive.

Why Summary shows empty Groups
------------------------------
Google Ads Summary groups goals by category across the journey:
  Group 1 ≈ CONTACT / PAGE_VIEW
  Group 2 ≈ SIGNUP / SUBSCRIBE_PAID
  Group 3 ≈ PURCHASE

This account currently has primary=true junk occupying Groups 2–3
(Purchase, Subscribe, YouTube) with include_in_conversions=false and zero
Ads-attributed volume — so the UI prints "You're not measuring any goals".
Meanwhile the real NGO actions (form / email / telegram / chat) are mostly
category=DEFAULT, so they do not populate the Contacts row cleanly.

Bidding note
------------
Maximize Conversions is required here: market CPC is ~$6–12 and Ad Grants
caps non-Smart strategies at $2. We keep Max Conversions and optionally set a
high target CPA (~historical quality CPA) so learning is not overly timid.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from google.protobuf import field_mask_pb2

from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.adapters.write_guard import assert_write_service


# Primary + counted. Contact-shaped actions get category CONTACT so Summary
# Group 1 "Contacts" reflects them. login fills Group 2 as SIGNUP.
PROMOTE: dict[str, dict[str, Any]] = {
    "www.example.com (web) chat_open": {
        "category": "CONTACT",
        "primary": True,
    },
    "www.example.com (web) chat_message_sent": {
        "category": "CONTACT",
        "primary": True,
    },
    "www.example.com (web) form_submit": {
        "category": "CONTACT",
        "primary": True,
    },
    "www.example.com (web) click_email": {
        "category": "CONTACT",
        "primary": True,
    },
    "www.example.com (web) click_messenger": {
        "category": "CONTACT",
        "primary": True,
    },
    "www.example.com (web) login_success": {
        "category": "SIGNUP",
        "primary": True,
    },
    "www.example.com (web) click_store_ios": {
        "category": "DEFAULT",
        "primary": True,
    },
    "www.example.com (web) click_store_android": {
        "category": "DEFAULT",
        "primary": True,
    },
}

# Occupy journey slots with zero Ads signal and confuse Summary.
DEMOTE_PRIMARY = {
    "Purchase (1)",
    "Subscribe",
    "YouTube channel subscriptions",
}

# Never primary on Ad Grants.
FORCE_SECONDARY_MARKERS = (
    "page_view",
    "Page view",
    "session_start",
    "first_visit",
    "user_engagement",
    "scroll",
    "seconds_on_page",
    "scroll",
)

# Historical quality CPA (chat entry) Jul–Aug was ~$150. A high target keeps
# Smart Bidding allowed to clear $6–12 auctions while learning.
RECOVERY_TARGET_CPA_MICROS = 200_000_000  # $200

BIDDABLE_GOALS = (
    ("CONTACT", "WEBSITE"),
    ("DEFAULT", "WEBSITE"),
    ("SIGNUP", "WEBSITE"),
)


@dataclass
class FixResult:
    dry_run: bool
    actions_planned: list[dict[str, Any]] = field(default_factory=list)
    actions_updated: int = 0
    goals_planned: list[dict[str, Any]] = field(default_factory=list)
    goals_updated: int = 0
    campaigns_planned: list[dict[str, Any]] = field(default_factory=list)
    campaigns_updated: int = 0
    broad_paused: int = 0
    warnings: list[dict[str, str]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    after: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _Scoped:
    def __init__(self, client: Any, *, scope: str) -> None:
        self._client = client
        self._scope = scope

    def get_service(self, name: str):
        assert_write_service(name, scope=self._scope)
        return self._client.get_service(name)

    def get_type(self, name: str):
        return self._client.get_type(name)

    @property
    def enums(self):
        return self._client.enums

    def __getattr__(self, item: str):
        return getattr(self._client, item)


def fix(*, dry_run: bool = True, pause_broad: bool = True) -> FixResult:
    if not dry_run:
        assert_mutations_allowed()

    settings = GoogleAdsSettings.from_env()
    from google.ads.googleads.client import GoogleAdsClient

    raw = GoogleAdsClient.load_from_dict(settings.client_config())
    # goal_cleanup: ConversionActionService
    # grant_delivery_recovery: ConversionAction + CustomerConversionGoal + Campaign
    goals = _Scoped(raw, scope="goal_cleanup")
    recovery = _Scoped(raw, scope="grant_delivery_recovery")
    reactivate = _Scoped(raw, scope="grant_reactivate")
    ga = recovery.get_service("GoogleAdsService")
    cid = settings.customer_id
    result = FixResult(dry_run=dry_run)

    actions: dict[str, dict[str, Any]] = {}
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT conversion_action.resource_name, conversion_action.id,
               conversion_action.name, conversion_action.status,
               conversion_action.category, conversion_action.primary_for_goal,
               conversion_action.include_in_conversions_metric,
               conversion_action.type
        FROM conversion_action
        WHERE conversion_action.status != 'REMOVED'
        """,
    ):
        c = row.conversion_action
        actions[c.name] = {
            "resource_name": c.resource_name,
            "id": str(c.id),
            "name": c.name,
            "category": c.category.name,
            "primary": bool(c.primary_for_goal),
            "included": bool(c.include_in_conversions_metric),
            "type": c.type.name,
        }

    action_ops = []
    for name, spec in PROMOTE.items():
        cur = actions.get(name)
        if not cur:
            result.warnings.append({"op": "promote", "warning": f"missing:{name}"})
            continue
        desired_primary = bool(spec["primary"])
        desired_cat = spec["category"]
        desired_incl = desired_primary
        if (
            cur["primary"] == desired_primary
            and cur["included"] == desired_incl
            and cur["category"] == desired_cat
        ):
            continue
        plan = {
            **cur,
            "desired_primary": desired_primary,
            "desired_included": desired_incl,
            "desired_category": desired_cat,
            "reason": "promote_meaningful",
        }
        result.actions_planned.append(plan)
        op = recovery.get_type("ConversionActionOperation")
        op.update.resource_name = cur["resource_name"]
        op.update.primary_for_goal = desired_primary
        op.update.include_in_conversions_metric = desired_incl
        op.update.category = getattr(
            recovery.enums.ConversionActionCategoryEnum, desired_cat
        )
        op.update_mask.CopyFrom(
            field_mask_pb2.FieldMask(
                paths=[
                    "primary_for_goal",
                    "include_in_conversions_metric",
                    "category",
                ]
            )
        )
        action_ops.append(op)

    for name in DEMOTE_PRIMARY:
        cur = actions.get(name)
        if not cur:
            continue
        if not cur["primary"] and not cur["included"]:
            continue
        result.actions_planned.append(
            {
                **cur,
                "desired_primary": False,
                "desired_included": False,
                "desired_category": cur["category"],
                "reason": "demote_junk_funnel_slot",
            }
        )
        op = goals.get_type("ConversionActionOperation")
        op.update.resource_name = cur["resource_name"]
        op.update.primary_for_goal = False
        op.update.include_in_conversions_metric = False
        op.update_mask.CopyFrom(
            field_mask_pb2.FieldMask(
                paths=["primary_for_goal", "include_in_conversions_metric"]
            )
        )
        action_ops.append(op)

    for cur in actions.values():
        if not cur["primary"]:
            continue
        if not any(m in cur["name"] for m in FORCE_SECONDARY_MARKERS):
            continue
        result.actions_planned.append(
            {
                **cur,
                "desired_primary": False,
                "desired_included": False,
                "desired_category": cur["category"],
                "reason": "grant_forbids_engagement_primary",
            }
        )
        op = goals.get_type("ConversionActionOperation")
        op.update.resource_name = cur["resource_name"]
        op.update.primary_for_goal = False
        op.update.include_in_conversions_metric = False
        op.update_mask.CopyFrom(
            field_mask_pb2.FieldMask(
                paths=["primary_for_goal", "include_in_conversions_metric"]
            )
        )
        action_ops.append(op)

    if action_ops and not dry_run:
        # Split by scope: demotes via goal_cleanup, promotes via recovery.
        demote_ops = [
            op
            for op in action_ops
            if not op.update.primary_for_goal
        ]
        promote_ops = [
            op
            for op in action_ops
            if op.update.primary_for_goal
        ]
        try:
            if demote_ops:
                goals.get_service("ConversionActionService").mutate_conversion_actions(
                    customer_id=cid, operations=demote_ops
                )
                result.actions_updated += len(demote_ops)
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"op": "demote_actions", "error": str(exc)[:400]})
        try:
            if promote_ops:
                recovery.get_service(
                    "ConversionActionService"
                ).mutate_conversion_actions(customer_id=cid, operations=promote_ops)
                result.actions_updated += len(promote_ops)
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"op": "promote_actions", "error": str(exc)[:400]})

    # Customer conversion goals that Smart Bidding may optimize toward.
    goal_rows = {}
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT customer_conversion_goal.resource_name,
               customer_conversion_goal.category,
               customer_conversion_goal.origin,
               customer_conversion_goal.biddable
        FROM customer_conversion_goal
        """,
    ):
        g = row.customer_conversion_goal
        goal_rows[(g.category.name, g.origin.name)] = {
            "resource_name": g.resource_name,
            "biddable": bool(g.biddable),
            "category": g.category.name,
            "origin": g.origin.name,
        }

    goal_ops = []
    for category, origin in BIDDABLE_GOALS:
        cur = goal_rows.get((category, origin))
        if not cur:
            result.warnings.append(
                {
                    "op": "customer_goal",
                    "warning": f"missing goal {category}/{origin} (appears after category assign)",
                }
            )
            continue
        if cur["biddable"]:
            continue
        result.goals_planned.append({**cur, "desired_biddable": True})
        op = recovery.get_type("CustomerConversionGoalOperation")
        op.update.resource_name = cur["resource_name"]
        op.update.biddable = True
        op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["biddable"]))
        goal_ops.append(op)

    # Explicitly keep PAGE_VIEW / PURCHASE / YOUTUBE non-biddable.
    for key, cur in goal_rows.items():
        if key in BIDDABLE_GOALS:
            continue
        if not cur["biddable"]:
            continue
        result.goals_planned.append({**cur, "desired_biddable": False})
        op = recovery.get_type("CustomerConversionGoalOperation")
        op.update.resource_name = cur["resource_name"]
        op.update.biddable = False
        op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["biddable"]))
        goal_ops.append(op)

    if goal_ops and not dry_run:
        try:
            recovery.get_service(
                "CustomerConversionGoalService"
            ).mutate_customer_conversion_goals(customer_id=cid, operations=goal_ops)
            result.goals_updated = len(goal_ops)
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"op": "customer_goals", "error": str(exc)[:400]})

    # Bidding: Max Conversions + high tCPA for recovery.
    camp_ops = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.resource_name, campaign.name, campaign.bidding_strategy_type,
               campaign.maximize_conversions.target_cpa_micros
        FROM campaign
        WHERE campaign.status = 'ENABLED'
          AND campaign.advertising_channel_type = 'SEARCH'
        """,
    ):
        c = row.campaign
        need_strategy = c.bidding_strategy_type.name != "MAXIMIZE_CONVERSIONS"
        need_tcpa = (
            c.bidding_strategy_type.name == "MAXIMIZE_CONVERSIONS"
            and int(c.maximize_conversions.target_cpa_micros or 0)
            != RECOVERY_TARGET_CPA_MICROS
        )
        if not need_strategy and not need_tcpa:
            continue
        result.campaigns_planned.append(
            {
                "name": c.name,
                "from": c.bidding_strategy_type.name,
                "tcpa": int(c.maximize_conversions.target_cpa_micros or 0),
                "desired_tcpa": RECOVERY_TARGET_CPA_MICROS,
            }
        )
        op = recovery.get_type("CampaignOperation")
        op.update.resource_name = c.resource_name
        op.update.maximize_conversions.target_cpa_micros = RECOVERY_TARGET_CPA_MICROS
        op.update_mask.CopyFrom(
            field_mask_pb2.FieldMask(
                paths=["maximize_conversions.target_cpa_micros"]
            )
        )
        camp_ops.append(op)

    if camp_ops and not dry_run:
        try:
            recovery.get_service("CampaignService").mutate_campaigns(
                customer_id=cid, operations=camp_ops
            )
            result.campaigns_updated = len(camp_ops)
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"op": "campaign_bidding", "error": str(exc)[:400]})

    if pause_broad:
        broad = [
            row.ad_group_criterion.resource_name
            for row in ga.search(
                customer_id=cid,
                query="""
                SELECT ad_group_criterion.resource_name
                FROM keyword_view
                WHERE campaign.status = 'ENABLED'
                  AND ad_group.status = 'ENABLED'
                  AND ad_group_criterion.status = 'ENABLED'
                  AND ad_group_criterion.keyword.match_type = BROAD
                """,
            )
        ]
        if dry_run:
            result.broad_paused = len(broad)
        else:
            svc = reactivate.get_service("AdGroupCriterionService")
            for i in range(0, len(broad), 35):
                chunk = broad[i : i + 35]
                ops = []
                for rn in chunk:
                    op = reactivate.get_type("AdGroupCriterionOperation")
                    op.update.resource_name = rn
                    op.update.status = (
                        reactivate.enums.AdGroupCriterionStatusEnum.PAUSED
                    )
                    op.update_mask.CopyFrom(
                        field_mask_pb2.FieldMask(paths=["status"])
                    )
                    ops.append(op)
                try:
                    svc.mutate_ad_group_criteria(customer_id=cid, operations=ops)
                    result.broad_paused += len(ops)
                    time.sleep(1.2)
                except Exception as exc:  # noqa: BLE001
                    result.errors.append(
                        {"op": "pause_broad", "error": str(exc)[:300]}
                    )
                    result.warnings.append(
                        {
                            "op": "pause_broad",
                            "warning": (
                                f"stopped after {result.broad_paused}/{len(broad)}; "
                                "rerun with --pause-broad-only later"
                            ),
                        }
                    )
                    break

    result.after = _snapshot(ga, cid)
    return result


def _snapshot(ga: Any, cid: str) -> dict[str, Any]:
    primaries = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT conversion_action.name, conversion_action.category,
               conversion_action.primary_for_goal,
               conversion_action.include_in_conversions_metric
        FROM conversion_action
        WHERE conversion_action.status != 'REMOVED'
          AND conversion_action.primary_for_goal = TRUE
        ORDER BY conversion_action.name
        """,
    ):
        c = row.conversion_action
        primaries.append(
            {
                "name": c.name,
                "category": c.category.name,
                "included": bool(c.include_in_conversions_metric),
            }
        )
    goals = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT customer_conversion_goal.category, customer_conversion_goal.origin,
               customer_conversion_goal.biddable
        FROM customer_conversion_goal
        """,
    ):
        g = row.customer_conversion_goal
        if g.biddable:
            goals.append(f"{g.category.name}/{g.origin.name}")
    camps = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.name, campaign.bidding_strategy_type,
               campaign.maximize_conversions.target_cpa_micros,
               campaign.primary_status
        FROM campaign WHERE campaign.status = 'ENABLED'
        """,
    ):
        c = row.campaign
        camps.append(
            {
                "name": c.name[-40:],
                "bid": c.bidding_strategy_type.name,
                "tcpa": int(c.maximize_conversions.target_cpa_micros or 0) / 1e6,
                "status": c.primary_status.name,
            }
        )
    return {"primaries": primaries, "biddable_goals": goals, "campaigns": camps}


def main() -> int:
    from ads_ai_agents.intelligence.recovery_playbook import REFUSE_LEGACY_TCPA, refuse

    return refuse("fix_goals_and_bidding.py", REFUSE_LEGACY_TCPA)


def _legacy_main_disabled() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--no-pause-broad",
        action="store_true",
        help="Skip Broad pause (goals/bidding only).",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = fix(dry_run=not args.apply, pause_broad=not args.no_pause_broad)
    payload = result.to_dict()
    if args.json:
        import json

        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1 if result.errors else 0

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Fix goals + bidding [{mode}]")
    print(f"  actions_planned={len(result.actions_planned)}")
    for item in result.actions_planned:
        print(
            f"    {item['reason']}: {item['name'][:55]} "
            f"prim={item.get('desired_primary')} cat={item.get('desired_category')}"
        )
    print(f"  goals_planned={len(result.goals_planned)} campaigns_planned={len(result.campaigns_planned)}")
    print(
        f"  updated actions={result.actions_updated} goals={result.goals_updated} "
        f"campaigns={result.campaigns_updated} broad_paused={result.broad_paused}"
    )
    for w in result.warnings[:8]:
        print(f"  WARN {w}")
    for e in result.errors[:8]:
        print(f"  ERR  {e}")
    print("  after.primaries:")
    for p in result.after.get("primaries") or []:
        print(f"    [{p['category']}] incl={p['included']} {p['name']}")
    print(f"  after.biddable_goals={result.after.get('biddable_goals')}")
    for c in result.after.get("campaigns") or []:
        print(f"    {c['status']:<10} tcpa=${c['tcpa']:.0f} {c['bid']:<20} {c['name']}")
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
