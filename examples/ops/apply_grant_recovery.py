#!/usr/bin/env python3
"""One-shot Grant delivery recovery apply — no stubs.

Order of operations (each verified before the next):
1. Pause re-enabled PMax so unique daily budget returns to the $329 Grant cap.
2. Keep Smart Bidding (Maximize Conversions + recovery tCPA) — Maximize Clicks
   cannot clear this account's $6–12 auctions under the Ad Grants $2 CPC policy.
3. Collapse Primary/Conversions to the two chat contact actions that carry signal.
4. Pause dangerous Recommendation Auto-Apply subscriptions.
5. Normalize keywords (pause broad, restore phrase floor).
6. Expand proven semantics (dry sources only if Keyword Planner is locked).

Usage:
  PYTHONPATH=ads_ai_agents/src python ads_ai_agents/scripts/apply_grant_recovery.py
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from ads_ai_agents.platform.account_ids import load_account_ids  # noqa: E402

GRANT_CAP = 329.0
KEEP_PRIMARY = (
    "www.example.com (web) chat_message_sent",
    "www.example.com (web) chat_open",
)
# Auto-apply types that re-inflate budget, switch bidding, or flood broad match.
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
        "KEYWORD",
        "DISPLAY_EXPANSION_OPT_IN",
    }
)
PORTFOLIO_ID = int(load_account_ids()["portfolio_strategies"]["secondary"])  # from config/account_ids.json
RECOVERY_TCPA_MICROS = 500_000_000  # cold-start floor; was $200 with persistent rank-lost


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


def _client_raw():
    from google.ads.googleads.client import GoogleAdsClient
    from ads_ai_agents.adapters.settings import GoogleAdsSettings

    return GoogleAdsClient.load_from_dict(GoogleAdsSettings.from_env().client_config())


def _unique_budget(ga, cid: str) -> tuple[float, list[dict[str, Any]]]:
    by_budget: dict[str, dict[str, Any]] = {}
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.name, campaign.advertising_channel_type,
               campaign_budget.resource_name, campaign_budget.amount_micros,
               campaign_budget.explicitly_shared
        FROM campaign WHERE campaign.status = 'ENABLED'
        """,
    ):
        rn = row.campaign_budget.resource_name
        entry = by_budget.setdefault(
            rn,
            {
                "amount": row.campaign_budget.amount_micros / 1e6,
                "shared": row.campaign_budget.explicitly_shared,
                "campaigns": [],
            },
        )
        entry["campaigns"].append(
            f"[{row.campaign.advertising_channel_type.name}] {row.campaign.name}"
        )
    total = round(sum(item["amount"] for item in by_budget.values()), 2)
    rows = [
        {
            "budget": rn.split("/")[-1],
            "amount": data["amount"],
            "shared": data["shared"],
            "campaigns": data["campaigns"],
        }
        for rn, data in by_budget.items()
    ]
    return total, rows


def step_pause_pmax(client, cid: str, report: dict[str, Any]) -> None:
    from google.protobuf import field_mask_pb2
    from ads_ai_agents.adapters.write_guard import assert_write_service

    assert_write_service("CampaignService", scope="grant_policy_remediation")
    ga = client.get_service("GoogleAdsService")
    service = client.get_service("CampaignService")
    targets = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.resource_name, campaign.name
        FROM campaign
        WHERE campaign.status = 'ENABLED'
          AND campaign.advertising_channel_type = 'PERFORMANCE_MAX'
        """,
    ):
        targets.append({"resource_name": row.campaign.resource_name, "name": row.campaign.name})
    report["pmax_found"] = targets
    if not targets:
        report["pmax_paused"] = 0
        return
    ops = []
    for item in targets:
        op = client.get_type("CampaignOperation")
        op.update.resource_name = item["resource_name"]
        op.update.status = client.enums.CampaignStatusEnum.PAUSED
        op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["status"]))
        ops.append(op)
    service.mutate_campaigns(customer_id=cid, operations=ops)
    report["pmax_paused"] = len(ops)
    report["pmax_paused_names"] = [item["name"] for item in targets]


def step_ensure_portfolio_tcpa(client, cid: str, report: dict[str, Any]) -> None:
    from google.protobuf import field_mask_pb2
    from ads_ai_agents.adapters.write_guard import assert_write_service

    assert_write_service("BiddingStrategyService", scope="grant_delivery_recovery")
    ga = client.get_service("GoogleAdsService")
    rows = list(
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
    if not rows:
        report["portfolio_tcpa"] = "missing"
        return
    strategy = rows[0].bidding_strategy
    current = int(strategy.maximize_conversions.target_cpa_micros or 0)
    report["portfolio_tcpa_before"] = current / 1e6
    if current == RECOVERY_TCPA_MICROS:
        report["portfolio_tcpa"] = "unchanged"
        return
    assert_write_service("BiddingStrategyService", scope="grant_delivery_recovery")
    service = client.get_service("BiddingStrategyService")
    op = client.get_type("BiddingStrategyOperation")
    op.update.resource_name = strategy.resource_name
    op.update.maximize_conversions.target_cpa_micros = RECOVERY_TCPA_MICROS
    op.update_mask.CopyFrom(
        field_mask_pb2.FieldMask(paths=["maximize_conversions.target_cpa_micros"])
    )
    service.mutate_bidding_strategies(customer_id=cid, operations=[op])
    report["portfolio_tcpa"] = "updated"
    report["portfolio_tcpa_after"] = RECOVERY_TCPA_MICROS / 1e6


def step_goals(client, cid: str, report: dict[str, Any]) -> None:
    from google.protobuf import field_mask_pb2
    from ads_ai_agents.adapters.write_guard import assert_write_service

    assert_write_service("ConversionActionService", scope="goal_cleanup")
    ga = client.get_service("GoogleAdsService")
    service = client.get_service("ConversionActionService")
    demoted = []
    kept = []
    ops = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT conversion_action.resource_name, conversion_action.name,
               conversion_action.primary_for_goal,
               conversion_action.include_in_conversions_metric,
               conversion_action.status
        FROM conversion_action
        WHERE conversion_action.status != 'REMOVED'
        """,
    ):
        action = row.conversion_action
        name = action.name
        keep = name in KEEP_PRIMARY
        want_primary = keep
        want_include = keep
        if action.primary_for_goal == want_primary and action.include_in_conversions_metric == want_include:
            if keep:
                kept.append(name)
            continue
        op = client.get_type("ConversionActionOperation")
        op.update.resource_name = action.resource_name
        op.update.primary_for_goal = want_primary
        op.update.include_in_conversions_metric = want_include
        paths = ["primary_for_goal", "include_in_conversions_metric"]
        if keep:
            # Ensure bidding recognises chat as Contact.
            op.update.category = client.enums.ConversionActionCategoryEnum.CONTACT
            paths.append("category")
            kept.append(name)
        else:
            demoted.append(name)
        op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=paths))
        ops.append(op)
    report["goals_kept"] = kept
    report["goals_demoted"] = demoted
    if not ops:
        report["goals_mutated"] = 0
        return
    # Partial failure so UI-locked actions (YouTube) do not abort the batch.
    request = client.get_type("MutateConversionActionsRequest")
    request.customer_id = cid
    request.operations.extend(ops)
    request.partial_failure = True
    response = service.mutate_conversion_actions(request=request)
    applied = sum(1 for item in response.results if item.resource_name)
    report["goals_mutated"] = applied
    if response.partial_failure_error.code:
        report["goals_partial_failure"] = response.partial_failure_error.message[:400]


def step_pause_auto_apply(client, cid: str, report: dict[str, Any]) -> None:
    from google.protobuf import field_mask_pb2
    from ads_ai_agents.adapters.write_guard import assert_write_service

    assert_write_service("RecommendationSubscriptionService", scope="grant_guardian")
    ga = client.get_service("GoogleAdsService")
    service = client.get_service("RecommendationSubscriptionService")
    ops = []
    paused_types = []
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
        type_name = sub.type.name
        if type_name == "UNKNOWN" or type_name not in PAUSE_AUTO_APPLY_TYPES:
            continue
        if sub.status.name == "PAUSED":
            continue
        op = client.get_type("RecommendationSubscriptionOperation")
        op.update.resource_name = sub.resource_name
        op.update.status = client.enums.RecommendationSubscriptionStatusEnum.PAUSED
        op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["status"]))
        ops.append(op)
        paused_types.append(type_name)
    report["auto_apply_paused_types"] = paused_types
    if not ops:
        report["auto_apply_paused"] = 0
        return
    service.mutate_recommendation_subscription(customer_id=cid, operations=ops)
    report["auto_apply_paused"] = len(ops)


def step_keywords(report: dict[str, Any]) -> None:
    from ads_ai_agents.intelligence.grant_keyword_normalizer import GrantKeywordNormalizer
    from ads_ai_agents.intelligence.semantic_expansion import GrantSemanticExpander

    normalizer = GrantKeywordNormalizer().apply(dry_run=False)
    report["normalizer"] = {
        "phrase_created": normalizer.phrase_keywords_created,
        "phrase_enabled": normalizer.phrase_keywords_enabled,
        "broad_paused": normalizer.broad_keywords_paused,
        "warnings": len(normalizer.warnings),
        "errors": normalizer.errors,
    }
    expander = GrantSemanticExpander().apply(dry_run=False, use_keyword_ideas=False)
    report["expander"] = {
        "planned": len(expander.candidates_planned),
        "created": expander.keywords_created,
        "coverage": expander.coverage,
        "warnings": len(expander.warnings),
        "errors": expander.errors,
    }


def step_contact_goal_biddable(client, cid: str, report: dict[str, Any]) -> None:
    from google.protobuf import field_mask_pb2
    from ads_ai_agents.adapters.write_guard import assert_write_service

    assert_write_service("CustomerConversionGoalService", scope="grant_delivery_recovery")
    ga = client.get_service("GoogleAdsService")
    service = client.get_service("CustomerConversionGoalService")
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
        goal = row.customer_conversion_goal
        if goal.category.name != "CONTACT" or goal.origin.name != "WEBSITE":
            continue
        if goal.biddable:
            report["contact_goal_biddable"] = True
            return
        op = client.get_type("CustomerConversionGoalOperation")
        op.update.resource_name = goal.resource_name
        op.update.biddable = True
        op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["biddable"]))
        service.mutate_customer_conversion_goals(customer_id=cid, operations=[op])
        report["contact_goal_biddable"] = "updated"
        return
    report["contact_goal_biddable"] = "missing"


def main() -> int:
    from ads_ai_agents.intelligence.recovery_playbook import REFUSE_LEGACY_TCPA, refuse

    return refuse("apply_grant_recovery.py", REFUSE_LEGACY_TCPA)


def _legacy_main_disabled() -> int:
    """Kept for reference only; main() refuses to run it."""
    from ads_ai_agents.adapters.settings import GoogleAdsSettings

    settings = GoogleAdsSettings.from_env()
    cid = settings.customer_id
    client = _client_raw()
    ga = client.get_service("GoogleAdsService")

    report: dict[str, Any] = {
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "customer_id": cid,
    }

    before, before_rows = _unique_budget(ga, cid)
    report["budget_unique_before"] = before
    report["budget_rows_before"] = before_rows
    print(f"[1/7] unique enabled budget before: ${before:.2f}")

    print("[2/7] pausing enabled PMax…")
    step_pause_pmax(client, cid, report)
    print(f"  paused={report.get('pmax_paused')} {report.get('pmax_paused_names')}")

    after, after_rows = _unique_budget(ga, cid)
    report["budget_unique_after"] = after
    report["budget_rows_after"] = after_rows
    print(f"[3/7] unique enabled budget after: ${after:.2f} (cap ${GRANT_CAP})")
    if after > GRANT_CAP + 0.01:
        report["errors"] = report.get("errors", [])
        report["errors"].append(f"budget still over cap: ${after}")
        print("ABORT: budget still over Grant cap")
        _write(report)
        return 2

    print("[4/7] portfolio tCPA + CONTACT goal…")
    step_ensure_portfolio_tcpa(client, cid, report)
    step_contact_goal_biddable(client, cid, report)
    print(f"  portfolio={report.get('portfolio_tcpa')} contact={report.get('contact_goal_biddable')}")

    print("[5/7] collapsing Primary goals to chat contact pair…")
    step_goals(client, cid, report)
    print(
        f"  mutated={report.get('goals_mutated')} kept={report.get('goals_kept')} "
        f"demoted={len(report.get('goals_demoted') or [])}"
    )
    if report.get("goals_partial_failure"):
        print(f"  partial: {report['goals_partial_failure'][:200]}")

    print("[6/7] pausing dangerous Auto-Apply…")
    step_pause_auto_apply(client, cid, report)
    print(f"  paused={report.get('auto_apply_paused')} types={report.get('auto_apply_paused_types')}")

    print("[7/7] keyword normalizer + semantic expand…")
    step_keywords(report)
    print(f"  normalizer={report.get('normalizer')}")
    print(f"  expander created={report.get('expander', {}).get('created')} planned={report.get('expander', {}).get('planned')}")

    # Final inventory snapshot
    report["final"] = _final_snapshot(client, cid)
    print("\n== FINAL ==")
    print(json.dumps(report["final"], ensure_ascii=False, indent=2))
    _write(report)
    return 0 if after <= GRANT_CAP + 0.01 else 2


def _final_snapshot(client, cid: str) -> dict[str, Any]:
    ga = client.get_service("GoogleAdsService")
    total, rows = _unique_budget(ga, cid)
    primaries = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT conversion_action.name, conversion_action.primary_for_goal,
               conversion_action.include_in_conversions_metric
        FROM conversion_action
        WHERE conversion_action.status != 'REMOVED'
          AND conversion_action.primary_for_goal = TRUE
        """,
    ):
        primaries.append(
            {
                "name": row.conversion_action.name,
                "included": row.conversion_action.include_in_conversions_metric,
            }
        )
    bidding = defaultdict(int)
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.bidding_strategy_type, campaign.advertising_channel_type
        FROM campaign WHERE campaign.status = 'ENABLED'
        """,
    ):
        bidding[f"{row.campaign.advertising_channel_type.name}:{row.campaign.bidding_strategy_type.name}"] += 1
    enabled_kw = {"PHRASE": 0, "BROAD": 0, "EXACT": 0}
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT ad_group_criterion.keyword.match_type
        FROM ad_group_criterion
        WHERE campaign.status = 'ENABLED'
          AND campaign.advertising_channel_type = 'SEARCH'
          AND ad_group.status = 'ENABLED'
          AND ad_group_criterion.type = 'KEYWORD'
          AND ad_group_criterion.negative = FALSE
          AND ad_group_criterion.status = 'ENABLED'
        """,
    ):
        enabled_kw[row.ad_group_criterion.keyword.match_type.name] = (
            enabled_kw.get(row.ad_group_criterion.keyword.match_type.name, 0) + 1
        )
    return {
        "unique_budget_usd": total,
        "budget_rows": rows,
        "primaries": primaries,
        "bidding": dict(bidding),
        "enabled_keywords_by_match": enabled_kw,
        "enabled_campaigns": sum(bidding.values()),
    }


def _write(report: dict[str, Any]) -> None:
    out_dir = ROOT / "reports" / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "grant_recovery_apply.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote: {path}")


if __name__ == "__main__":
    raise SystemExit(main())
