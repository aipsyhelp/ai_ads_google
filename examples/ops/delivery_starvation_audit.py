"""Read-only delivery audit for the Ad Grants Search account.

Answers one question with account facts: for every enabled Search campaign, is
there a complete serving path (keyword -> ad group -> ad -> targeting -> budget)
and is anything in the account actively blocking it (negatives, network, geo)?

Usage:
    python ads_ai_agents/scripts/delivery_starvation_audit.py [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from ads_ai_agents.intelligence.negative_match import blocked_by  # noqa: E402


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def audit() -> dict[str, Any]:
    from ads_ai_agents.adapters.google_ads_readonly import GoogleAdsReadOnlyAdapter

    adapter = GoogleAdsReadOnlyAdapter()
    ga = adapter.client.get_service("GoogleAdsService")
    cid = adapter.settings.customer_id
    enums = adapter.client.enums

    campaigns: dict[str, dict[str, Any]] = {}
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.id, campaign.name, campaign.status,
               campaign.primary_status, campaign.serving_status,
               campaign.bidding_strategy_type,
               campaign.network_settings.target_google_search,
               campaign.network_settings.target_search_network,
               campaign.network_settings.target_content_network,
               campaign_budget.id,
               campaign_budget.amount_micros,
               campaign_budget.explicitly_shared
        FROM campaign
        WHERE campaign.status = 'ENABLED'
          AND campaign.advertising_channel_type = 'SEARCH'
        """,
    ):
        campaigns[str(row.campaign.id)] = {
            "name": row.campaign.name,
            "primary_status": row.campaign.primary_status.name,
            "serving_status": row.campaign.serving_status.name,
            "bidding": row.campaign.bidding_strategy_type.name,
            "target_google_search": row.campaign.network_settings.target_google_search,
            "target_search_network": row.campaign.network_settings.target_search_network,
            "budget_id": str(row.campaign_budget.id),
            "budget_shared": bool(row.campaign_budget.explicitly_shared),
            "budget_usd": round(row.campaign_budget.amount_micros / 1e6, 2),
            "ad_groups": {},
            "campaign_negatives": [],
            "geo_targets": 0,
            "language_targets": [],
        }

    # Ad groups + enabled ad counts.
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.id, ad_group.id, ad_group.name, ad_group.status
        FROM ad_group
        WHERE campaign.status = 'ENABLED'
          AND campaign.advertising_channel_type = 'SEARCH'
          AND ad_group.status = 'ENABLED'
        """,
    ):
        campaign = campaigns.get(str(row.campaign.id))
        if campaign is None:
            continue
        campaign["ad_groups"][str(row.ad_group.id)] = {
            "name": row.ad_group.name,
            "enabled_ads": 0,
            "keywords": [],
            "negatives": [],
        }

    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.id, ad_group.id
        FROM ad_group_ad
        WHERE campaign.status = 'ENABLED'
          AND campaign.advertising_channel_type = 'SEARCH'
          AND ad_group.status = 'ENABLED'
          AND ad_group_ad.status = 'ENABLED'
        """,
    ):
        campaign = campaigns.get(str(row.campaign.id))
        if campaign is None:
            continue
        group = campaign["ad_groups"].get(str(row.ad_group.id))
        if group is not None:
            group["enabled_ads"] += 1

    # Positive + negative keywords at ad group level.
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.id, ad_group.id,
               ad_group_criterion.negative,
               ad_group_criterion.status,
               ad_group_criterion.keyword.text,
               ad_group_criterion.keyword.match_type,
               ad_group_criterion.quality_info.quality_score
        FROM ad_group_criterion
        WHERE campaign.status = 'ENABLED'
          AND campaign.advertising_channel_type = 'SEARCH'
          AND ad_group.status = 'ENABLED'
          AND ad_group_criterion.type = 'KEYWORD'
          AND ad_group_criterion.status != 'REMOVED'
        """,
    ):
        campaign = campaigns.get(str(row.campaign.id))
        if campaign is None:
            continue
        group = campaign["ad_groups"].get(str(row.ad_group.id))
        if group is None:
            continue
        criterion = row.ad_group_criterion
        entry = {
            "text": criterion.keyword.text,
            "match_type": enums.KeywordMatchTypeEnum(criterion.keyword.match_type).name,
            "status": enums.AdGroupCriterionStatusEnum(criterion.status).name,
            "quality_score": criterion.quality_info.quality_score,
        }
        if criterion.negative:
            group["negatives"].append(entry)
        else:
            group["keywords"].append(entry)

    # Campaign-level negatives.
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.id,
               campaign_criterion.negative,
               campaign_criterion.keyword.text,
               campaign_criterion.keyword.match_type
        FROM campaign_criterion
        WHERE campaign.status = 'ENABLED'
          AND campaign.advertising_channel_type = 'SEARCH'
          AND campaign_criterion.type = 'KEYWORD'
          AND campaign_criterion.negative = TRUE
        """,
    ):
        campaign = campaigns.get(str(row.campaign.id))
        if campaign is None:
            continue
        campaign["campaign_negatives"].append(
            {
                "text": row.campaign_criterion.keyword.text,
                "match_type": enums.KeywordMatchTypeEnum(
                    row.campaign_criterion.keyword.match_type
                ).name,
            }
        )

    # Shared negative lists attached to campaigns.
    shared_sets: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT shared_criterion.shared_set,
               shared_criterion.keyword.text,
               shared_criterion.keyword.match_type
        FROM shared_criterion
        WHERE shared_criterion.type = 'KEYWORD'
        """,
    ):
        shared_sets[row.shared_criterion.shared_set].append(
            {
                "text": row.shared_criterion.keyword.text,
                "match_type": enums.KeywordMatchTypeEnum(
                    row.shared_criterion.keyword.match_type
                ).name,
            }
        )
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.id, campaign_shared_set.shared_set,
               campaign_shared_set.status
        FROM campaign_shared_set
        WHERE campaign.status = 'ENABLED'
          AND campaign_shared_set.status = 'ENABLED'
        """,
    ):
        campaign = campaigns.get(str(row.campaign.id))
        if campaign is None:
            continue
        campaign["campaign_negatives"].extend(
            shared_sets.get(row.campaign_shared_set.shared_set, [])
        )

    # Geo + language targeting.
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.id, campaign_criterion.type,
               campaign_criterion.language.language_constant,
               campaign_criterion.negative
        FROM campaign_criterion
        WHERE campaign.status = 'ENABLED'
          AND campaign.advertising_channel_type = 'SEARCH'
          AND campaign_criterion.type IN ('LOCATION', 'LANGUAGE')
        """,
    ):
        campaign = campaigns.get(str(row.campaign.id))
        if campaign is None:
            continue
        kind = row.campaign_criterion.type.name
        if kind == "LOCATION" and not row.campaign_criterion.negative:
            campaign["geo_targets"] += 1
        elif kind == "LANGUAGE":
            campaign["language_targets"].append(
                row.campaign_criterion.language.language_constant
            )

    # Account-level conversion + tagging health.
    account: dict[str, Any] = {}
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT customer.id, customer.status, customer.test_account,
               customer.auto_tagging_enabled,
               customer.conversion_tracking_setting.conversion_tracking_status,
               customer.conversion_tracking_setting.google_ads_conversion_customer,
               customer.conversion_tracking_setting.conversion_tracking_id
        FROM customer
        """,
    ):
        setting = row.customer.conversion_tracking_setting
        account = {
            "status": row.customer.status.name,
            "test_account": row.customer.test_account,
            "auto_tagging_enabled": row.customer.auto_tagging_enabled,
            "conversion_tracking_status": setting.conversion_tracking_status.name,
            "conversion_tracking_id": setting.conversion_tracking_id,
        }

    # The v25 reporting surface does not expose google_ads_grant_info. Billing
    # status plus customer/campaign serving evidence are the API-safe checks;
    # suspension status itself must be confirmed in the Grants policy UI.
    account["grant_info_api_available"] = False

    # Daily delivery trend across every channel, to separate "Search only" from
    # "whole account" stalls.
    trend: dict[str, dict[str, Any]] = {}
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT segments.date, campaign.advertising_channel_type,
               metrics.impressions, metrics.clicks, metrics.cost_micros
        FROM campaign
        WHERE segments.date DURING LAST_30_DAYS
        """,
    ):
        day = trend.setdefault(
            row.segments.date,
            {"impressions": 0, "clicks": 0, "cost": 0.0, "channels": {}},
        )
        day["impressions"] += int(row.metrics.impressions)
        day["clicks"] += int(row.metrics.clicks)
        day["cost"] = round(day["cost"] + row.metrics.cost_micros / 1e6, 2)
        channel = row.campaign.advertising_channel_type.name
        day["channels"][channel] = day["channels"].get(channel, 0) + int(
            row.metrics.impressions
        )

    conversion_actions = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT conversion_action.name, conversion_action.status,
               conversion_action.primary_for_goal, conversion_action.category,
               conversion_action.include_in_conversions_metric
        FROM conversion_action
        WHERE conversion_action.status = 'ENABLED'
        """,
    ):
        action = row.conversion_action
        conversion_actions.append(
            {
                "name": action.name,
                "primary_for_goal": action.primary_for_goal,
                "category": action.category.name,
                "included": action.include_in_conversions_metric,
            }
        )

    # Derive blockers.
    findings = []
    for campaign_id, campaign in campaigns.items():
        negatives = campaign["campaign_negatives"]
        for group in campaign["ad_groups"].values():
            group_negatives = negatives + group["negatives"]
            servable = [
                keyword
                for keyword in group["keywords"]
                if keyword["status"] == "ENABLED"
            ]
            blocked = []
            for keyword in servable:
                hits = blocked_by(keyword["text"], group_negatives)
                if hits:
                    blocked.append({"keyword": keyword["text"], "blocked_by": hits})
            reasons = []
            if not servable:
                reasons.append("no_enabled_keywords")
            if group["enabled_ads"] == 0:
                reasons.append("no_enabled_ads")
            if blocked and len(blocked) == len(servable):
                reasons.append("all_keywords_negative_blocked")
            elif blocked:
                reasons.append("some_keywords_negative_blocked")
            if reasons:
                findings.append(
                    {
                        "campaign": campaign["name"],
                        "campaign_id": campaign_id,
                        "ad_group": group["name"],
                        "reasons": reasons,
                        "enabled_keywords": len(servable),
                        "enabled_ads": group["enabled_ads"],
                        "blocked_examples": blocked[:5],
                    }
                )

    summary = []
    for campaign_id, campaign in campaigns.items():
        enabled_keywords = sum(
            1
            for group in campaign["ad_groups"].values()
            for keyword in group["keywords"]
            if keyword["status"] == "ENABLED"
        )
        paused_keywords = sum(
            1
            for group in campaign["ad_groups"].values()
            for keyword in group["keywords"]
            if keyword["status"] == "PAUSED"
        )
        summary.append(
            {
                "campaign": campaign["name"],
                "campaign_id": campaign_id,
                "bidding": campaign["bidding"],
                "primary_status": campaign["primary_status"],
                "budget_id": campaign.get("budget_id"),
                "budget_shared": campaign.get("budget_shared"),
                "budget_usd": campaign["budget_usd"],
                "ad_groups": len(campaign["ad_groups"]),
                "enabled_keywords": enabled_keywords,
                "paused_keywords": paused_keywords,
                "campaign_negatives": len(campaign["campaign_negatives"]),
                "geo_targets": campaign["geo_targets"],
                "language_targets": len(campaign["language_targets"]),
                "target_google_search": campaign["target_google_search"],
            }
        )

    summary.sort(key=lambda item: item["enabled_keywords"])
    # Shared budgets must be counted once — summing per campaign falsely
    # reports $1100 when four Search campaigns share one $257 Grant budget.
    unique_budgets: dict[str, float] = {}
    for item in summary:
        key = str(item.get("budget_id") or f"campaign:{item['campaign_id']}")
        unique_budgets[key] = float(item["budget_usd"])
    budget_unique = round(sum(unique_budgets.values()), 2)
    return {
        "account": account,
        "daily_trend": dict(sorted(trend.items())),
        "conversion_actions": conversion_actions,
        "campaign_summary": summary,
        "blocking_findings": findings,
        "totals": {
            "campaigns": len(campaigns),
            "ad_groups": sum(len(c["ad_groups"]) for c in campaigns.values()),
            "enabled_keywords": sum(item["enabled_keywords"] for item in summary),
            "paused_keywords": sum(item["paused_keywords"] for item in summary),
            "budget_usd": budget_unique,
            "budget_usd_summed_per_campaign": round(
                sum(item["budget_usd"] for item in summary), 2
            ),
            "groups_with_blockers": len(findings),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    _load_env(root / ".env")
    report = audit()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    totals = report["totals"]
    print("== Delivery audit ==")
    print(
        f"campaigns={totals['campaigns']} ad_groups={totals['ad_groups']} "
        f"enabled_keywords={totals['enabled_keywords']} "
        f"paused_keywords={totals['paused_keywords']} "
        f"budget=${totals['budget_usd']}"
    )
    print(f"account={report['account']}")
    print(f"groups_with_blockers={totals['groups_with_blockers']}")
    print()
    print("-- daily trend (impressions/clicks/cost) --")
    for day, values in report["daily_trend"].items():
        print(
            f"  {day}: impr={values['impressions']} clicks={values['clicks']} "
            f"cost=${values['cost']} {values['channels']}"
        )
    print()
    for item in report["campaign_summary"]:
        print(
            f"- {item['campaign']}\n"
            f"    bidding={item['bidding']} status={item['primary_status']} "
            f"budget=${item['budget_usd']} groups={item['ad_groups']} "
            f"kw_enabled={item['enabled_keywords']} kw_paused={item['paused_keywords']} "
            f"neg={item['campaign_negatives']} geo={item['geo_targets']} "
            f"lang={item['language_targets']}"
        )
    print()
    for finding in report["blocking_findings"][:40]:
        print(
            f"! {finding['campaign']} / {finding['ad_group']}: "
            f"{','.join(finding['reasons'])} "
            f"(kw={finding['enabled_keywords']}, ads={finding['enabled_ads']})"
        )
        for example in finding["blocked_examples"]:
            print(f"      {example['keyword']} <- {example['blocked_by']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
