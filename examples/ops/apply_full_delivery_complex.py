#!/usr/bin/env python3
"""Full Grant delivery complex (user-approved 2026-09-14).

1. Switch every enabled Search TARGET_SPEND → Maximize Conversions, NO tCPA
   (removes Ad Grants $2 CPC ceiling; cold-start policy).
2. Clear portfolio Max Conv tCPA to 0.
3. Promote intent conversion actions; demote vanity (page_view, time-on-site, etc.).
4. Ensure CONTACT~WEBSITE goal is biddable.
5. Raise budgets within $329 unique-daily Grant discipline (EN shared + starved cities).
6. Pause Recommendation Auto-Apply that reverts bidding to Max Clicks / tCPA.

Usage:
  PYTHONPATH=ads_ai_agents/src ADS_AUTOMATION_MODE=CONTROLLED_LIVE \\
    python ads_ai_agents/scripts/apply_full_delivery_complex.py --apply
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.protobuf import field_mask_pb2

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from ads_ai_agents.platform.account_ids import load_account_ids  # noqa: E402

GRANT_CAP = 329.0
NO_TARGET_CPA_MICROS = 0
PORTFOLIO_ID = int(load_account_ids()["portfolio_strategies"]["secondary"])  # from config/account_ids.json

PROMOTE = (
    "www.example.com (web) chat_open",
    "www.example.com (web) chat_message_sent",
    "www.example.com (web) form_submit",
    "www.example.com (web) login_success",
    "www.example.com (web) click_email",
    "www.example.com (web) click_messenger",
    "www.example.com (web) click_store_ios",
    "www.example.com (web) click_store_android",
    "www.example.com (web) go_to_chat",
)

VANITY_MARKERS = (
    "page_view",
    "Page view",
    "session_start",
    "first_visit",
    "user_engagement",
    "scroll_page",
    "seconds_on_page",
    "YouTube channel subscriptions",
    "YouTube follow-on",
)

PAUSE_AUTO_APPLY_TYPES = frozenset(
    {
        "USE_BROAD_MATCH_KEYWORD",
        "MAXIMIZE_CONVERSIONS_OPT_IN",
        "MAXIMIZE_CONVERSION_VALUE_OPT_IN",
        "MAXIMIZE_CLICKS_OPT_IN",
        "TARGET_CPA_OPT_IN",
        "TARGET_ROAS_OPT_IN",
        "SET_TARGET_CPA",
        "SET_TARGET_ROAS",
        "RAISE_TARGET_CPA",
        "LOWER_TARGET_ROAS",
        "DISPLAY_EXPANSION_OPT_IN",
    }
)


def _load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _unique_budgets(ga: Any, cid: str) -> dict[str, dict[str, Any]]:
    by: dict[str, dict[str, Any]] = {}
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.name, campaign.status, campaign.advertising_channel_type,
               campaign_budget.resource_name, campaign_budget.amount_micros,
               campaign_budget.explicitly_shared
        FROM campaign WHERE campaign.status = 'ENABLED'
        """,
    ):
        rn = row.campaign_budget.resource_name
        entry = by.setdefault(
            rn,
            {
                "amount": row.campaign_budget.amount_micros / 1e6,
                "shared": bool(row.campaign_budget.explicitly_shared),
                "campaigns": [],
            },
        )
        entry["campaigns"].append(row.campaign.name)
    return by


def main() -> int:
    from ads_ai_agents.intelligence.recovery_playbook import REFUSE_CLEAR_TCPA, refuse

    return refuse("apply_full_delivery_complex.py", REFUSE_CLEAR_TCPA)


def _legacy_main_disabled() -> int:
    apply = "--apply" in sys.argv
    _load_env()

    from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
    from ads_ai_agents.adapters.settings import GoogleAdsSettings
    from ads_ai_agents.adapters.write_guard import assert_write_service
    from google.ads.googleads.client import GoogleAdsClient

    if apply:
        assert_mutations_allowed()
    for svc in (
        "CampaignService",
        "BiddingStrategyService",
        "CampaignBudgetService",
        "ConversionActionService",
        "CustomerConversionGoalService",
        "RecommendationSubscriptionService",
    ):
        scope = (
            "grant_guardian"
            if svc == "RecommendationSubscriptionService"
            else "grant_delivery_recovery"
        )
        assert_write_service(svc, scope=scope)

    settings = GoogleAdsSettings.from_env()
    client = GoogleAdsClient.load_from_dict(settings.client_config())
    ga = client.get_service("GoogleAdsService")
    cid = settings.customer_id
    report: dict[str, Any] = {
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "customer_id": cid,
        "mode": "APPLY" if apply else "DRY_RUN",
        "policy": "Max Conversions, no tCPA, intent goals, budget raise within $329",
    }

    # --- inventory ---
    camps = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.resource_name, campaign.id, campaign.name, campaign.status,
               campaign.advertising_channel_type, campaign.bidding_strategy_type,
               campaign.bidding_strategy,
               campaign.maximize_conversions.target_cpa_micros,
               campaign.target_spend.cpc_bid_ceiling_micros,
               campaign_budget.resource_name, campaign_budget.amount_micros,
               campaign_budget.aligned_bidding_strategy_id,
               campaign_budget.explicitly_shared
        FROM campaign
        WHERE campaign.status = 'ENABLED'
          AND campaign.advertising_channel_type = 'SEARCH'
        """,
    ):
        camps.append(
            {
                "rn": row.campaign.resource_name,
                "id": str(row.campaign.id),
                "name": row.campaign.name,
                "bid": row.campaign.bidding_strategy_type.name,
                "portfolio": row.campaign.bidding_strategy or "",
                "tcpa": (row.campaign.maximize_conversions.target_cpa_micros or 0) / 1e6,
                "ceiling": (row.campaign.target_spend.cpc_bid_ceiling_micros or 0) / 1e6,
                "budget_rn": row.campaign_budget.resource_name,
                "budget": row.campaign_budget.amount_micros / 1e6,
                "aligned": int(row.campaign_budget.aligned_bidding_strategy_id or 0),
                "shared": bool(row.campaign_budget.explicitly_shared),
            }
        )
    report["enabled_search_before"] = [
        {k: c[k] for k in ("name", "bid", "tcpa", "ceiling", "budget", "shared")}
        for c in camps
    ]
    budgets_before = _unique_budgets(ga, cid)
    unique_before = round(sum(b["amount"] for b in budgets_before.values()), 2)
    report["unique_budget_before"] = unique_before
    headroom = max(GRANT_CAP - unique_before, 0.0)
    report["budget_headroom"] = headroom

    # --- 1) bidding: TARGET_SPEND → Max Conv no tCPA ---
    bid_ops = []
    bid_targets = []
    for c in camps:
        needs = c["bid"] != "MAXIMIZE_CONVERSIONS" or (
            c["bid"] == "MAXIMIZE_CONVERSIONS" and not c["portfolio"] and c["tcpa"] > 0
        )
        # Portfolio-aligned already Max Conv — skip campaign-level (align rule).
        if c["aligned"] and c["bid"] == "MAXIMIZE_CONVERSIONS":
            continue
        if c["bid"] == "MAXIMIZE_CONVERSIONS" and not c["portfolio"] and c["tcpa"] == 0:
            continue
        if not needs and c["bid"] == "MAXIMIZE_CONVERSIONS":
            continue
        if c["bid"] != "TARGET_SPEND" and not (
            c["bid"] == "MAXIMIZE_CONVERSIONS" and c["tcpa"] > 0 and not c["portfolio"]
        ):
            if c["bid"] == "MAXIMIZE_CONVERSIONS":
                continue
        op = client.get_type("CampaignOperation")
        op.update.resource_name = c["rn"]
        paths = ["maximize_conversions.target_cpa_micros"]
        if c["portfolio"]:
            op.update.bidding_strategy = ""
            paths.insert(0, "bidding_strategy")
        op.update.maximize_conversions.target_cpa_micros = NO_TARGET_CPA_MICROS
        op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=paths))
        bid_ops.append(op)
        bid_targets.append(c["name"])
    report["bidding_switch_targets"] = bid_targets

    camp_svc = client.get_service("CampaignService")
    if bid_ops:
        vreq = client.get_type("MutateCampaignsRequest")
        vreq.customer_id = cid
        vreq.operations.extend(bid_ops)
        vreq.validate_only = True
        camp_svc.mutate_campaigns(request=vreq)
        report["bidding_validation"] = "PASSED"
        if apply:
            areq = client.get_type("MutateCampaignsRequest")
            areq.customer_id = cid
            areq.operations.extend(bid_ops)
            areq.partial_failure = True
            aresp = camp_svc.mutate_campaigns(request=areq)
            report["bidding_applied"] = len(aresp.results)
            if aresp.partial_failure_error.code:
                report["bidding_partial_failure"] = aresp.partial_failure_error.message[:500]

    # --- 2) portfolio tCPA → 0 ---
    strat_rows = list(
        ga.search(
            customer_id=cid,
            query=f"""
            SELECT bidding_strategy.resource_name,
                   bidding_strategy.maximize_conversions.target_cpa_micros
            FROM bidding_strategy
            WHERE bidding_strategy.id = {PORTFOLIO_ID}
            """,
        )
    )
    if strat_rows:
        strat = strat_rows[0].bidding_strategy
        cur = int(strat.maximize_conversions.target_cpa_micros or 0)
        report["portfolio_tcpa_before"] = cur / 1e6
        if cur != 0:
            report["portfolio_tcpa_plan"] = "clear_to_0"
            if apply:
                bsvc = client.get_service("BiddingStrategyService")
                bop = client.get_type("BiddingStrategyOperation")
                bop.update.resource_name = strat.resource_name
                bop.update.maximize_conversions.target_cpa_micros = 0
                bop.update_mask.CopyFrom(
                    field_mask_pb2.FieldMask(
                        paths=["maximize_conversions.target_cpa_micros"]
                    )
                )
                bsvc.mutate_bidding_strategies(customer_id=cid, operations=[bop])
                report["portfolio_tcpa_after"] = 0
        else:
            report["portfolio_tcpa_plan"] = "already_0"

    # --- 3) goals ---
    goal_ops = []
    promoted, demoted = [], []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT conversion_action.resource_name, conversion_action.name,
               conversion_action.primary_for_goal,
               conversion_action.include_in_conversions_metric,
               conversion_action.category, conversion_action.status
        FROM conversion_action
        WHERE conversion_action.status != 'REMOVED'
        """,
    ):
        a = row.conversion_action
        name = a.name
        is_promote = name in PROMOTE
        is_vanity = any(m in name for m in VANITY_MARKERS)
        want_primary = is_promote
        want_include = is_promote
        if not is_promote and not is_vanity and not a.primary_for_goal and not a.include_in_conversions_metric:
            continue
        if not is_promote and not is_vanity:
            # Leave other non-primary actions alone unless they are wrongly primary.
            if a.primary_for_goal or a.include_in_conversions_metric:
                want_primary = False
                want_include = False
            else:
                continue
        if a.primary_for_goal == want_primary and a.include_in_conversions_metric == want_include:
            continue
        op = client.get_type("ConversionActionOperation")
        op.update.resource_name = a.resource_name
        op.update.primary_for_goal = want_primary
        op.update.include_in_conversions_metric = want_include
        paths = ["primary_for_goal", "include_in_conversions_metric"]
        if is_promote and a.category.name not in ("CONTACT", "SIGNUP", "DOWNLOAD"):
            # Keep SIGNUP/DOWNLOAD categories; force CONTACT only for chat-like names.
            if any(m in name.lower() for m in ("chat", "form", "email", "messenger")):
                op.update.category = client.enums.ConversionActionCategoryEnum.CONTACT
                paths.append("category")
        op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=paths))
        goal_ops.append(op)
        (promoted if want_primary else demoted).append(name)
    report["goals_promote"] = promoted
    report["goals_demote"] = demoted
    if goal_ops and apply:
        gsvc = client.get_service("ConversionActionService")
        greq = client.get_type("MutateConversionActionsRequest")
        greq.customer_id = cid
        greq.operations.extend(goal_ops)
        greq.partial_failure = True
        gresp = gsvc.mutate_conversion_actions(request=greq)
        report["goals_mutated"] = sum(1 for r in gresp.results if r.resource_name)
        if gresp.partial_failure_error.code:
            report["goals_partial_failure"] = gresp.partial_failure_error.message[:500]

    # CONTACT website biddable
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT customer_conversion_goal.resource_name,
               customer_conversion_goal.biddable,
               customer_conversion_goal.category,
               customer_conversion_goal.origin
        FROM customer_conversion_goal
        """,
    ):
        g = row.customer_conversion_goal
        if g.category.name != "CONTACT" or g.origin.name != "WEBSITE":
            continue
        report["contact_goal_biddable_before"] = g.biddable
        if not g.biddable and apply:
            csvc = client.get_service("CustomerConversionGoalService")
            cop = client.get_type("CustomerConversionGoalOperation")
            cop.update.resource_name = g.resource_name
            cop.update.biddable = True
            cop.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["biddable"]))
            csvc.mutate_customer_conversion_goals(customer_id=cid, operations=[cop])
            report["contact_goal_biddable_after"] = True
        break

    # --- 4) budget raises within headroom ---
    # Priority: EN shared ($20 → up), then City_B ($25), then bridges if room left.
    budget_plan: list[dict[str, Any]] = []
    remaining = headroom
    # Find EN shared budget rn
    shared_candidates = [
        (rn, data)
        for rn, data in budgets_before.items()
        if data["shared"] and any("EN" in n and "Brand_Search" in n for n in data["campaigns"])
    ]
    if shared_candidates and remaining > 0:
        rn, data = shared_candidates[0]
        bump = min(40.0, remaining)  # +$40 toward ~$60 shared if headroom allows
        # If headroom is only ~25, use all of it on shared first
        bump = min(bump, remaining)
        if bump >= 1:
            budget_plan.append(
                {
                    "rn": rn,
                    "name": "EN shared portfolio",
                    "from": data["amount"],
                    "to": round(data["amount"] + bump, 2),
                }
            )
            remaining -= bump
    # If still headroom after a future unique drop, boost Odessa
    odessa = [
        (rn, data)
        for rn, data in budgets_before.items()
        if any("City_B" in n for n in data["campaigns"])
    ]
    if odessa and remaining >= 5:
        rn, data = odessa[0]
        bump = min(10.0, remaining)
        budget_plan.append(
            {
                "rn": rn,
                "name": "City_B",
                "from": data["amount"],
                "to": round(data["amount"] + bump, 2),
            }
        )
        remaining -= bump
    report["budget_raises"] = [
        {k: v for k, v in p.items() if k != "rn"} for p in budget_plan
    ]
    if budget_plan and apply:
        bsvc = client.get_service("CampaignBudgetService")
        bops = []
        for item in budget_plan:
            bop = client.get_type("CampaignBudgetOperation")
            bop.update.resource_name = item["rn"]
            bop.update.amount_micros = int(round(item["to"] * 1e6))
            bop.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["amount_micros"]))
            bops.append(bop)
        bsvc.mutate_campaign_budgets(customer_id=cid, operations=bops)
        report["budget_raises_applied"] = len(bops)

    # --- 5) pause dangerous auto-apply ---
    auto_ops = []
    paused_types = []
    try:
        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT recommendation_subscription.resource_name,
                   recommendation_subscription.type,
                   recommendation_subscription.status
            FROM recommendation_subscription
            """,
        ):
            sub = row.recommendation_subscription
            tname = sub.type.name
            if tname not in PAUSE_AUTO_APPLY_TYPES or sub.status.name == "PAUSED":
                continue
            op = client.get_type("RecommendationSubscriptionOperation")
            op.update.resource_name = sub.resource_name
            op.update.status = client.enums.RecommendationSubscriptionStatusEnum.PAUSED
            op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["status"]))
            auto_ops.append(op)
            paused_types.append(tname)
    except Exception as exc:  # noqa: BLE001
        report["auto_apply_error"] = str(exc)[:300]
    report["auto_apply_pause_types"] = paused_types
    if auto_ops and apply:
        asvc = client.get_service("RecommendationSubscriptionService")
        asvc.mutate_recommendation_subscription(customer_id=cid, operations=auto_ops)
        report["auto_apply_paused"] = len(auto_ops)

    # --- after snapshot ---
    if apply:
        after_camps = []
        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT campaign.name, campaign.bidding_strategy_type,
                   campaign.maximize_conversions.target_cpa_micros,
                   campaign.target_spend.cpc_bid_ceiling_micros,
                   campaign_budget.amount_micros, campaign_budget.explicitly_shared,
                   campaign.primary_status
            FROM campaign
            WHERE campaign.status = 'ENABLED'
              AND campaign.advertising_channel_type = 'SEARCH'
            """,
        ):
            after_camps.append(
                {
                    "name": row.campaign.name,
                    "bid": row.campaign.bidding_strategy_type.name,
                    "tcpa": (row.campaign.maximize_conversions.target_cpa_micros or 0)
                    / 1e6,
                    "ceiling": (row.campaign.target_spend.cpc_bid_ceiling_micros or 0)
                    / 1e6,
                    "budget": row.campaign_budget.amount_micros / 1e6,
                    "shared": bool(row.campaign_budget.explicitly_shared),
                    "primary": row.campaign.primary_status.name,
                }
            )
        report["enabled_search_after"] = after_camps
        budgets_after = _unique_budgets(ga, cid)
        report["unique_budget_after"] = round(
            sum(b["amount"] for b in budgets_after.values()), 2
        )

    out_dir = ROOT / "reports" / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "apply_full_delivery_complex.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Wrote: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
