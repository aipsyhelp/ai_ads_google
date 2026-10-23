#!/usr/bin/env python3
"""Make the Grant Search funnel production-ready (operator-approved).

Steps (CONTROLLED_LIVE):
  1. Pause all ENABLED PMax (+ DSA if any) — restore unique budget ≤ $329
  2. Enforce Search Maximize Conversions, clear CPC ceilings / stray tCPA
  3. Intent goals primary+included; demote vanity (YouTube best-effort)
  4. CONTACT/SIGNUP customer goals biddable; vanity non-biddable
  5. Pause clinical/policy-labeled structured snippets; add wellness replacements
  6. Enterprise asset coverage (Bridge callouts/snippets/sitelinks + RSA top-up)
  7. Replace clinical ENABLED RSA copy (all Search, not cities-only)
  8. Pause residual HIGH clinical keywords + safe PHRASE synonyms
  9. Re-verify unique budget + serving path summary

Usage:
  PYTHONPATH=ads_ai_agents/src ADS_AUTOMATION_MODE=CONTROLLED_LIVE \\
    python ads_ai_agents/scripts/apply_funnel_perfect.py --apply
"""
from __future__ import annotations

import argparse
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
PORTFOLIO_ID = int(load_account_ids()["portfolio_strategies"]["secondary"])  # from config/account_ids.json

INTENT_KEEP = {
    "www.example.com (web) chat_open",
    "www.example.com (web) chat_message_sent",
    "www.example.com (web) form_submit",
    "www.example.com (web) login_success",
    "www.example.com (web) click_email",
    "www.example.com (web) click_messenger",
    "www.example.com (web) go_to_chat",
}

VANITY_NAME_MARKERS = (
    "page_view",
    "Page view",
    "session_start",
    "first_visit",
    "user_engagement",
    "scroll",
    "seconds_on_page",
    "YouTube",
    "click_store",
    "purchase",
    "checkout",
    "subscribe",
    "paypal",
)

SAFE_SNIPPETS = [
    {
        "header": "Types",
        "values": ["AI chat", "Mood diary", "Self-checks", "Analytics"],
        "locales": ("EN", "DE", "ALL"),
    },
    {
        "header": "Styles",
        "values": ["Calm tone", "No judgment", "Short sessions", "Privacy first"],
        "locales": ("EN", "DE", "ALL"),
    },
    {
        "header": "Amenities",
        "values": ["Web chat", "Android", "iOS", "24/7 access"],
        "locales": ("EN", "DE", "ALL"),
    },
    {
        "header": "Типи",
        "values": ["AI-чат", "Щоденник", "Тести", "Аналітика"],
        "locales": ("UA", "ALL"),
    },
    {
        "header": "Стилі",
        "values": ["Спокійний тон", "Без суду", "Короткі сесії", "Приватність"],
        "locales": ("UA", "ALL"),
    },
    {
        "header": "Зручності",
        "values": ["Веб-чат", "Android", "iOS", "Доступ 24/7"],
        "locales": ("UA", "ALL"),
    },
]

CLINICAL_SNIPPET_MARKERS = (
    "психолог",
    "терап",
    "лікуван",
    "depression",
    "anxiety",
    "panic",
    "mental health",
    "consult",
    "therap",
    "депрес",
    "тривог",
    "паніч",
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


def _enum(v: Any) -> str:
    return getattr(v, "name", str(v))


def _locale(name: str) -> str:
    n = name or ""
    if " / DE " in n or n.endswith("/ DE") or "/ DE /" in n:
        return "DE"
    if "/ EN" in n or " EN /" in n:
        return "EN"
    return "UA"


def unique_budget(client, cid: str) -> tuple[float, list[dict[str, Any]]]:
    ga = client.get_service("GoogleAdsService")
    by_b: dict[str, dict[str, Any]] = {}
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.name, campaign.status, campaign.advertising_channel_type,
               campaign_budget.resource_name, campaign_budget.amount_micros,
               campaign_budget.explicitly_shared
        FROM campaign
        WHERE campaign.status = 'ENABLED'
        """,
    ):
        rn = row.campaign_budget.resource_name
        by_b[rn] = {
            "amount": round(row.campaign_budget.amount_micros / 1e6, 2),
            "shared": bool(row.campaign_budget.explicitly_shared),
            "channel": _enum(row.campaign.advertising_channel_type),
            "campaign": row.campaign.name,
        }
    total = round(sum(v["amount"] for v in by_b.values()), 2)
    return total, list(by_b.values())


def pause_non_search(client, cid: str, report: dict[str, Any]) -> None:
    from ads_ai_agents.adapters.write_guard import assert_write_service

    assert_write_service("CampaignService", scope="grant_policy_remediation")
    ga = client.get_service("GoogleAdsService")
    svc = client.get_service("CampaignService")
    targets = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.resource_name, campaign.name, campaign.advertising_channel_type
        FROM campaign
        WHERE campaign.status = 'ENABLED'
          AND campaign.advertising_channel_type = 'PERFORMANCE_MAX'
        """,
    ):
        targets.append((row.campaign.resource_name, row.campaign.name, "PMAX"))
    try:
        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT campaign.resource_name, campaign.name,
                   campaign.advertising_channel_type,
                   campaign.advertising_channel_sub_type
            FROM campaign
            WHERE campaign.status = 'ENABLED'
              AND campaign.advertising_channel_sub_type = 'SEARCH_DYNAMIC_ADS'
            """,
        ):
            targets.append((row.campaign.resource_name, row.campaign.name, "DSA"))
    except Exception as e:  # noqa: BLE001
        report["dsa_query_note"] = str(e)[:160]

    ops = []
    for rn, name, kind in targets:
        op = client.get_type("CampaignOperation")
        op.update.resource_name = rn
        op.update.status = client.enums.CampaignStatusEnum.PAUSED
        op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["status"]))
        ops.append(op)
    report["non_search_to_pause"] = [{"name": n, "kind": k} for _, n, k in targets]
    if ops:
        svc.mutate_campaigns(customer_id=cid, operations=ops)
    report["non_search_paused"] = len(ops)


def enforce_max_conv(client, cid: str, report: dict[str, Any]) -> None:
    from ads_ai_agents.adapters.write_guard import assert_write_service

    assert_write_service("CampaignService", scope="grant_delivery_recovery")
    assert_write_service("BiddingStrategyService", scope="grant_delivery_recovery")
    ga = client.get_service("GoogleAdsService")
    camp_svc = client.get_service("CampaignService")
    bid_svc = client.get_service("BiddingStrategyService")

    # Clear portfolio tCPA
    for row in ga.search(
        customer_id=cid,
        query=f"""
        SELECT bidding_strategy.resource_name,
               bidding_strategy.maximize_conversions.target_cpa_micros
        FROM bidding_strategy
        WHERE bidding_strategy.id = {PORTFOLIO_ID}
        """,
    ):
        cur = int(row.bidding_strategy.maximize_conversions.target_cpa_micros or 0)
        report["portfolio_tcpa_before"] = cur / 1e6
        if cur != 0:
            op = client.get_type("BiddingStrategyOperation")
            op.update.resource_name = row.bidding_strategy.resource_name
            op.update.maximize_conversions.target_cpa_micros = 0
            op.update_mask.CopyFrom(
                field_mask_pb2.FieldMask(paths=["maximize_conversions.target_cpa_micros"])
            )
            bid_svc.mutate_bidding_strategies(customer_id=cid, operations=[op])
            report["portfolio_tcpa_cleared"] = True

    fixed = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.resource_name, campaign.name,
               campaign.bidding_strategy_type,
               campaign.bidding_strategy,
               campaign.maximize_conversions.target_cpa_micros,
               campaign.target_spend.cpc_bid_ceiling_micros
        FROM campaign
        WHERE campaign.status = 'ENABLED'
          AND campaign.advertising_channel_type = 'SEARCH'
        """,
    ):
        bid = row.campaign.bidding_strategy_type.name
        tcpa = int(row.campaign.maximize_conversions.target_cpa_micros or 0)
        ceil = int(row.campaign.target_spend.cpc_bid_ceiling_micros or 0)
        needs = bid != "MAXIMIZE_CONVERSIONS" or tcpa != 0
        if not needs and bid == "MAXIMIZE_CONVERSIONS":
            continue
        if bid == "MAXIMIZE_CONVERSIONS" and tcpa == 0 and ceil == 0:
            continue
        op = client.get_type("CampaignOperation")
        op.update.resource_name = row.campaign.resource_name
        paths: list[str] = []
        if bid != "MAXIMIZE_CONVERSIONS":
            # Switch to Max Conv without portfolio if currently TARGET_SPEND
            op.update.maximize_conversions.target_cpa_micros = 0
            paths.append("maximize_conversions.target_cpa_micros")
            # Clear bidding_strategy attachment if on Max Clicks portfolio
            if row.campaign.bidding_strategy:
                op.update.bidding_strategy = ""
                paths.append("bidding_strategy")
        if tcpa != 0:
            op.update.maximize_conversions.target_cpa_micros = 0
            if "maximize_conversions.target_cpa_micros" not in paths:
                paths.append("maximize_conversions.target_cpa_micros")
        if not paths:
            continue
        op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=paths))
        camp_svc.mutate_campaigns(customer_id=cid, operations=[op])
        fixed.append(
            {
                "name": row.campaign.name,
                "from": bid,
                "tcpa_was": tcpa / 1e6,
                "ceil_was": ceil / 1e6,
            }
        )
    report["bidding_fixed"] = fixed


def fix_goals(client, cid: str, report: dict[str, Any]) -> None:
    from ads_ai_agents.adapters.write_guard import assert_write_service

    assert_write_service("ConversionActionService", scope="goal_cleanup")
    ga = client.get_service("GoogleAdsService")
    svc = client.get_service("ConversionActionService")
    ops = []
    demoted = []
    promoted = []
    locked = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT conversion_action.resource_name, conversion_action.name,
               conversion_action.primary_for_goal,
               conversion_action.include_in_conversions_metric,
               conversion_action.category, conversion_action.origin
        FROM conversion_action
        WHERE conversion_action.status != 'REMOVED'
        """,
    ):
        a = row.conversion_action
        name = a.name
        keep = name in INTENT_KEEP
        vanity = any(m.lower() in name.lower() for m in VANITY_NAME_MARKERS)
        want_primary = keep
        want_include = keep
        if a.primary_for_goal == want_primary and a.include_in_conversions_metric == want_include:
            continue
        if not keep and not vanity and not a.primary_for_goal and not a.include_in_conversions_metric:
            continue
        # Only mutate intent keepers or vanity/primary noise
        if not keep and not (a.primary_for_goal or a.include_in_conversions_metric or vanity):
            continue
        op = client.get_type("ConversionActionOperation")
        op.update.resource_name = a.resource_name
        op.update.primary_for_goal = want_primary
        op.update.include_in_conversions_metric = want_include
        paths = ["primary_for_goal", "include_in_conversions_metric"]
        if keep:
            op.update.category = (
                client.enums.ConversionActionCategoryEnum.SIGNUP
                if "login" in name.lower()
                else client.enums.ConversionActionCategoryEnum.CONTACT
            )
            paths.append("category")
            promoted.append(name)
        else:
            demoted.append(name)
        op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=paths))
        ops.append(op)
    report["goals_promoted"] = promoted
    report["goals_demoted"] = demoted
    if ops:
        req = client.get_type("MutateConversionActionsRequest")
        req.customer_id = cid
        req.operations.extend(ops)
        req.partial_failure = True
        resp = svc.mutate_conversion_actions(request=req)
        report["goals_mutated"] = sum(1 for r in resp.results if r.resource_name)
        if resp.partial_failure_error.code:
            report["goals_partial_failure"] = resp.partial_failure_error.message[:500]
            locked.append(resp.partial_failure_error.message[:200])
    report["goals_locked_notes"] = locked

    # Customer conversion goals biddable flags
    try:
        assert_write_service("CustomerConversionGoalService", scope="goal_cleanup")
        cg_svc = client.get_service("CustomerConversionGoalService")
        cg_ops = []
        desired = {
            ("CONTACT", "WEBSITE"): True,
            ("SIGNUP", "WEBSITE"): True,
            ("DEFAULT", "WEBSITE"): False,
            ("PAGE_VIEW", "WEBSITE"): False,
            ("PURCHASE", "WEBSITE"): False,
            ("ENGAGEMENT", "YOUTUBE_HOSTED"): False,
        }
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
            key = (g.category.name, g.origin.name)
            if key not in desired:
                continue
            want = desired[key]
            if bool(g.biddable) == want:
                continue
            op = client.get_type("CustomerConversionGoalOperation")
            op.update.resource_name = g.resource_name
            op.update.biddable = want
            op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["biddable"]))
            cg_ops.append(op)
        if cg_ops:
            cg_svc.mutate_customer_conversion_goals(customer_id=cid, operations=cg_ops)
        report["customer_goals_mutated"] = len(cg_ops)
    except Exception as e:  # noqa: BLE001
        report["customer_goals_error"] = str(e)[:300]


def remediate_snippets(client, cid: str, report: dict[str, Any]) -> None:
    from ads_ai_agents.adapters.write_guard import assert_write_service

    assert_write_service("AssetService", scope="asset_publish")
    assert_write_service("CampaignAssetService", scope="asset_publish")
    assert_write_service("CustomerAssetService", scope="asset_publish")
    ga = client.get_service("GoogleAdsService")
    asset_svc = client.get_service("AssetService")
    camp_asset_svc = client.get_service("CampaignAssetService")
    cust_asset_svc = client.get_service("CustomerAssetService")

    paused_links = []
    # Pause clinical / policy customer snippets
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT customer_asset.resource_name, customer_asset.status,
               asset.structured_snippet_asset.header,
               asset.structured_snippet_asset.values
        FROM customer_asset
        WHERE customer_asset.field_type = 'STRUCTURED_SNIPPET'
          AND customer_asset.status != 'REMOVED'
        """,
    ):
        vals = " ".join(row.asset.structured_snippet_asset.values).lower()
        header = (row.asset.structured_snippet_asset.header or "").lower()
        blob = header + " " + vals
        if any(m in blob for m in CLINICAL_SNIPPET_MARKERS):
            if row.customer_asset.status.name != "PAUSED":
                op = client.get_type("CustomerAssetOperation")
                op.update.resource_name = row.customer_asset.resource_name
                op.update.status = client.enums.AssetLinkStatusEnum.PAUSED
                op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["status"]))
                cust_asset_svc.mutate_customer_assets(customer_id=cid, operations=[op])
                paused_links.append({"level": "customer", "header": header, "values": vals[:80]})
            else:
                paused_links.append({"level": "customer_already", "header": header})

    # Pause clinical campaign snippets on enabled Search
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.name, campaign.status, campaign.advertising_channel_type,
               campaign_asset.resource_name, campaign_asset.status,
               asset.structured_snippet_asset.header,
               asset.structured_snippet_asset.values
        FROM campaign_asset
        WHERE campaign.status = 'ENABLED'
          AND campaign.advertising_channel_type = 'SEARCH'
          AND campaign_asset.field_type = 'STRUCTURED_SNIPPET'
          AND campaign_asset.status != 'REMOVED'
        """,
    ):
        vals = list(row.asset.structured_snippet_asset.values)
        header = row.asset.structured_snippet_asset.header or ""
        blob = (header + " " + " ".join(vals)).lower()
        if any(m in blob for m in CLINICAL_SNIPPET_MARKERS):
            if row.campaign_asset.status.name != "PAUSED":
                op = client.get_type("CampaignAssetOperation")
                op.update.resource_name = row.campaign_asset.resource_name
                op.update.status = client.enums.AssetLinkStatusEnum.PAUSED
                op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["status"]))
                camp_asset_svc.mutate_campaign_assets(customer_id=cid, operations=[op])
            paused_links.append(
                {
                    "level": "campaign",
                    "campaign": row.campaign.name,
                    "header": header,
                    "values": vals,
                }
            )
    report["snippets_paused"] = paused_links

    # Existing enabled snippet counts per campaign (one GAQL pass).
    existing_by_camp: dict[str, int] = {}
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.resource_name, campaign.status,
               campaign.advertising_channel_type, campaign_asset.status
        FROM campaign_asset
        WHERE campaign.status = 'ENABLED'
          AND campaign.advertising_channel_type = 'SEARCH'
          AND campaign_asset.field_type = 'STRUCTURED_SNIPPET'
          AND campaign_asset.status = 'ENABLED'
        """,
    ):
        rn = row.campaign.resource_name
        existing_by_camp[rn] = existing_by_camp.get(rn, 0) + 1

    # Create wellness snippet assets once, link to campaigns missing snippets / EN / Bridge
    created_assets: dict[str, str] = {}
    for spec in SAFE_SNIPPETS:
        key = spec["header"] + "|" + ",".join(spec["values"])
        op = client.get_type("AssetOperation")
        op.create.name = f"funnel-safe-snippet-{spec['header']}-20260914"
        op.create.type_ = client.enums.AssetTypeEnum.STRUCTURED_SNIPPET
        op.create.structured_snippet_asset.header = spec["header"]
        op.create.structured_snippet_asset.values.extend(spec["values"])
        resp = asset_svc.mutate_assets(customer_id=cid, operations=[op])
        created_assets[key] = resp.results[0].resource_name
    report["snippets_created"] = list(created_assets.keys())

    linked = 0
    link_errors = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.resource_name, campaign.name
        FROM campaign
        WHERE campaign.status = 'ENABLED'
          AND campaign.advertising_channel_type = 'SEARCH'
        """,
    ):
        loc = _locale(row.campaign.name)
        existing = existing_by_camp.get(row.campaign.resource_name, 0)
        if existing >= 3:
            continue
        for spec in SAFE_SNIPPETS:
            if loc not in spec["locales"] and "ALL" not in spec["locales"]:
                continue
            if loc == "UA" and spec["header"] in {"Types", "Styles", "Amenities"}:
                continue
            if loc != "UA" and spec["header"] in {"Типи", "Стилі", "Зручності"}:
                continue
            key = spec["header"] + "|" + ",".join(spec["values"])
            asset_rn = created_assets[key]
            op = client.get_type("CampaignAssetOperation")
            op.create.campaign = row.campaign.resource_name
            op.create.asset = asset_rn
            op.create.field_type = client.enums.AssetFieldTypeEnum.STRUCTURED_SNIPPET
            op.create.status = client.enums.AssetLinkStatusEnum.ENABLED
            try:
                req = client.get_type("MutateCampaignAssetsRequest")
                req.customer_id = cid
                req.operations.append(op)
                req.partial_failure = True
                resp = camp_asset_svc.mutate_campaign_assets(request=req)
                if resp.results and resp.results[0].resource_name:
                    linked += 1
                    existing += 1
                if resp.partial_failure_error.code:
                    link_errors.append(resp.partial_failure_error.message[:160])
            except Exception as e:  # noqa: BLE001
                link_errors.append(str(e)[:160])
            if existing >= 3:
                break
    report["snippets_linked"] = linked
    report["snippet_link_errors"] = link_errors[:20]


def main() -> int:
    from ads_ai_agents.intelligence.recovery_playbook import REFUSE_CLEAR_TCPA, refuse

    return refuse("apply_funnel_perfect.py", REFUSE_CLEAR_TCPA)


def _legacy_main_disabled() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        print("Pass --apply to mutate. Dry preview not implemented; refusing.")
        return 2

    _load_env()
    # Allow enterprise asset coverage in this session
    os.environ.setdefault("ADS_ASSET_PUBLISH", "auto_gated")

    from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
    from ads_ai_agents.adapters.settings import GoogleAdsSettings
    from google.ads.googleads.client import GoogleAdsClient

    assert_mutations_allowed()
    settings = GoogleAdsSettings.from_env()
    client = GoogleAdsClient.load_from_dict(settings.client_config())
    cid = settings.customer_id
    report: dict[str, Any] = {
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "customer_id": cid,
        "mode": "APPLY",
    }

    before, before_rows = unique_budget(client, cid)
    report["unique_budget_before"] = before
    report["budgets_before"] = before_rows

    print("[1/8] pause PMax/DSA…")
    pause_non_search(client, cid, report)
    print(f"  paused={report.get('non_search_paused')}")

    print("[2/8] enforce Max Conversions…")
    enforce_max_conv(client, cid, report)
    print(f"  bidding_fixed={len(report.get('bidding_fixed') or [])}")

    print("[3/8] fix conversion goals…")
    fix_goals(client, cid, report)
    print(
        f"  mutated={report.get('goals_mutated', 0)} "
        f"customer_goals={report.get('customer_goals_mutated', 0)}"
    )

    print("[4/8] remediate structured snippets…")
    remediate_snippets(client, cid, report)
    print(
        f"  paused={len(report.get('snippets_paused') or [])} "
        f"linked={report.get('snippets_linked')}"
    )

    print("[5/8] enterprise asset coverage (callouts/sitelinks/RSA)…")
    from ads_ai_agents.intelligence.enterprise_asset_coverage import EnterpriseAssetCoverage

    cov = EnterpriseAssetCoverage().apply(dry_run=False, top_up_rsa=True, max_rsa=120)
    report["asset_coverage"] = {
        "campaigns": cov.campaigns,
        "sitelinks_linked": cov.sitelinks_linked,
        "callouts_linked": cov.callouts_linked,
        "snippets_linked": cov.snippets_linked,
        "rsa_topped_up": cov.rsa_topped_up,
        "errors": cov.errors[:15],
        "skipped": cov.skipped[:20],
    }
    print(
        f"  callouts+={cov.callouts_linked} sitelinks+={cov.sitelinks_linked} "
        f"rsa+={cov.rsa_topped_up}"
    )

    print("[6/8] replace clinical RSA…")
    from ads_ai_agents.intelligence.rsa_prohibited_replace import ProhibitedRsaReplacer

    rsa = ProhibitedRsaReplacer().apply(dry_run=False, cities_only=False)
    report["rsa_replace"] = rsa.to_dict()
    print(f"  created={rsa.created} paused={rsa.paused} errors={len(rsa.errors)}")

    print("[7/8] clinical keyword hygiene…")
    from ads_ai_agents.adapters.write_guard import assert_write_service

    assert_write_service("AdGroupCriterionService", scope="controlled_live_keywords")
    import importlib.util

    hy_path = ROOT / "scripts" / "enterprise_grant_hygiene.py"
    spec = importlib.util.spec_from_file_location("enterprise_grant_hygiene", hy_path)
    assert spec and spec.loader
    ehy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ehy)
    kw = ehy.remediate_keywords(client, cid, dry_run=False)
    report["keyword_hygiene"] = kw
    print(f"  keywords paused/created={kw.get('paused')}/{kw.get('created')}")

    print("[8/8] verify budget + enabled Search…")
    after, after_rows = unique_budget(client, cid)
    report["unique_budget_after"] = after
    report["budgets_after"] = after_rows
    report["grant_budget_ok"] = after <= GRANT_CAP + 0.05

    ga = client.get_service("GoogleAdsService")
    enabled = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.name, campaign.status, campaign.advertising_channel_type,
               campaign.bidding_strategy_type, campaign.primary_status,
               campaign_budget.amount_micros
        FROM campaign
        WHERE campaign.status = 'ENABLED'
        ORDER BY campaign.advertising_channel_type, campaign.name
        """,
    ):
        enabled.append(
            {
                "name": row.campaign.name,
                "channel": _enum(row.campaign.advertising_channel_type),
                "bidding": _enum(row.campaign.bidding_strategy_type),
                "primary": _enum(row.campaign.primary_status),
                "budget": round(row.campaign_budget.amount_micros / 1e6, 2),
            }
        )
    report["enabled_after"] = enabled

    out_dir = ROOT / "reports" / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "apply_funnel_perfect.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": {
        "unique_budget_before": before,
        "unique_budget_after": after,
        "grant_budget_ok": report["grant_budget_ok"],
        "pmax_paused": report.get("non_search_paused"),
        "snippets_linked": report.get("snippets_linked"),
        "callouts_linked": cov.callouts_linked,
        "rsa_created": rsa.created,
        "rsa_paused": rsa.paused,
        "enabled": enabled,
    }}, ensure_ascii=False, indent=2))
    print(f"Wrote: {path}")
    return 0 if report["grant_budget_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
