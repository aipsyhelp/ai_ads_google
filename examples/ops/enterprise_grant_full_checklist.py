#!/usr/bin/env python3
"""Read-only Deloitte-style Ad Grants checklist for CID YOUR_BRAND.

Covers: account/billing, campaigns/bidding/budget, networks, geo/lang,
keywords (single-word/broad/QS), RSA strength+policy, assets coverage+policy,
goals/conversion settings, audiences/CM, auto-apply, TODAY delivery.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

SINGLE_WORD_RE = re.compile(r"^[^\s\"\[\]]+$")
CLINICAL_HINTS = re.compile(
    r"(психолог|терап|лікуван|лечени|diagnos|treatment|clinic|клиник|"
    r"психологічн(а|ої)?\s+допомог|psychological\s+help|"
    r"mental\s+health\s+treatment)",
    re.I,
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


def main() -> int:
    _load_env()
    from ads_ai_agents.adapters.settings import GoogleAdsSettings
    from google.ads.googleads.client import GoogleAdsClient

    settings = GoogleAdsSettings.from_env()
    client = GoogleAdsClient.load_from_dict(settings.client_config())
    ga = client.get_service("GoogleAdsService")
    cid = settings.customer_id
    out: dict[str, Any] = {
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "customer_id": cid,
        "findings": [],
        "ok": [],
        "warn": [],
        "block": [],
    }

    def note(level: str, code: str, msg: str, **extra: Any) -> None:
        item = {"code": code, "msg": msg, **extra}
        out["findings"].append({"level": level, **item})
        out[level].append(item)

    # --- customer / billing ---
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT customer.id, customer.descriptive_name, customer.status,
               customer.currency_code, customer.time_zone,
               customer.optimization_score, customer.auto_tagging_enabled,
               customer.tracking_url_template,
               customer.conversion_tracking_setting.conversion_tracking_status,
               customer.conversion_tracking_setting.conversion_tracking_id
        FROM customer
        LIMIT 1
        """,
    ):
        out["customer"] = {
            "id": row.customer.id,
            "name": row.customer.descriptive_name,
            "status": _enum(row.customer.status),
            "currency": row.customer.currency_code,
            "tz": row.customer.time_zone,
            "opt_score": row.customer.optimization_score,
            "auto_tagging": row.customer.auto_tagging_enabled,
            "tracking_template": row.customer.tracking_url_template or "",
            "conv_tracking": _enum(
                row.customer.conversion_tracking_setting.conversion_tracking_status
            ),
            "conv_id": row.customer.conversion_tracking_setting.conversion_tracking_id,
        }
        if row.customer.status.name != "ENABLED":
            note("block", "ACC_DISABLED", "Customer not ENABLED")
        else:
            note("ok", "ACC_ENABLED", "Customer ENABLED")
        if row.customer.auto_tagging_enabled:
            note("ok", "AUTO_TAG", "Auto-tagging on")
        else:
            note("warn", "AUTO_TAG_OFF", "Auto-tagging off")

    try:
        billing = []
        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT billing_setup.id, billing_setup.status,
                   billing_setup.payments_account_info.payments_account_name
            FROM billing_setup
            """,
        ):
            billing.append(
                {
                    "id": row.billing_setup.id,
                    "status": _enum(row.billing_setup.status),
                    "payments": row.billing_setup.payments_account_info.payments_account_name,
                }
            )
        out["billing"] = billing
        if any(b["status"] == "APPROVED" for b in billing):
            note("ok", "BILLING", "Billing APPROVED")
        else:
            note("block", "BILLING", "No APPROVED billing setup", billing=billing)
    except Exception as e:  # noqa: BLE001
        out["billing_error"] = str(e)[:240]
        note("warn", "BILLING_ERR", "Could not read billing_setup")

    # --- campaigns ---
    campaigns: list[dict[str, Any]] = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.id, campaign.name, campaign.status,
               campaign.primary_status, campaign.serving_status,
               campaign.advertising_channel_type, campaign.bidding_strategy_type,
               campaign.bidding_strategy,
               campaign.maximize_conversions.target_cpa_micros,
               campaign.target_cpa.target_cpa_micros,
               campaign.target_spend.cpc_bid_ceiling_micros,
               campaign.network_settings.target_google_search,
               campaign.network_settings.target_search_network,
               campaign.network_settings.target_content_network,
               campaign.geo_target_type_setting.positive_geo_target_type,
               campaign.geo_target_type_setting.negative_geo_target_type,
               campaign_budget.id, campaign_budget.amount_micros,
               campaign_budget.explicitly_shared, campaign_budget.name,
               metrics.impressions, metrics.clicks, metrics.cost_micros,
               metrics.search_rank_lost_impression_share,
               metrics.search_budget_lost_impression_share,
               metrics.search_impression_share
        FROM campaign
        WHERE segments.date DURING TODAY
        ORDER BY campaign.status, campaign.name
        """,
    ):
        bid = _enum(row.campaign.bidding_strategy_type)
        tcpa = 0.0
        if row.campaign.maximize_conversions.target_cpa_micros:
            tcpa = row.campaign.maximize_conversions.target_cpa_micros / 1e6
        elif row.campaign.target_cpa.target_cpa_micros:
            tcpa = row.campaign.target_cpa.target_cpa_micros / 1e6
        cpc_ceil = (row.campaign.target_spend.cpc_bid_ceiling_micros or 0) / 1e6
        campaigns.append(
            {
                "id": str(row.campaign.id),
                "name": row.campaign.name,
                "status": _enum(row.campaign.status),
                "primary": _enum(row.campaign.primary_status),
                "serving": _enum(row.campaign.serving_status),
                "channel": _enum(row.campaign.advertising_channel_type),
                "bidding": bid,
                "portfolio": row.campaign.bidding_strategy or "",
                "tcpa": tcpa,
                "cpc_ceiling": cpc_ceil,
                "google_search": row.campaign.network_settings.target_google_search,
                "search_partners": row.campaign.network_settings.target_search_network,
                "display": row.campaign.network_settings.target_content_network,
                "geo_positive": _enum(
                    row.campaign.geo_target_type_setting.positive_geo_target_type
                ),
                "budget_id": str(row.campaign_budget.id),
                "budget": round(row.campaign_budget.amount_micros / 1e6, 2),
                "shared": bool(row.campaign_budget.explicitly_shared),
                "impr": int(row.metrics.impressions),
                "clicks": int(row.metrics.clicks),
                "cost": round(row.metrics.cost_micros / 1e6, 2),
                "rank_lost": float(row.metrics.search_rank_lost_impression_share or 0),
                "budget_lost": float(
                    row.metrics.search_budget_lost_impression_share or 0
                ),
                "is": float(row.metrics.search_impression_share or 0),
            }
        )
    out["campaigns"] = campaigns

    en_search = [
        c
        for c in campaigns
        if c["status"] == "ENABLED" and c["channel"] == "SEARCH"
    ]
    en_other = [
        c
        for c in campaigns
        if c["status"] == "ENABLED" and c["channel"] != "SEARCH"
    ]
    if en_other:
        note(
            "warn",
            "NON_SEARCH_ENABLED",
            "Non-Search enabled during Search recovery",
            names=[c["name"] for c in en_other],
        )
    else:
        note("ok", "ONLY_SEARCH", "Only Search enabled (PMax/DSA paused)")

    bad_bid = [c for c in en_search if c["bidding"] != "MAXIMIZE_CONVERSIONS"]
    if bad_bid:
        note(
            "block",
            "BIDDING",
            "Enabled Search not on Maximize Conversions",
            campaigns=[{k: c[k] for k in ("name", "bidding", "cpc_ceiling", "tcpa")} for c in bad_bid],
        )
    else:
        note("ok", "BIDDING", f"All {len(en_search)} enabled Search on Max Conversions")

    capped = [c for c in en_search if c["cpc_ceiling"] > 0]
    if capped:
        note("block", "CPC_CEILING", "CPC ceiling still set", capped=capped)
    else:
        note("ok", "NO_CPC_CEILING", "No TARGET_SPEND CPC ceilings on enabled Search")

    # unique budget sum
    uniq: dict[str, float] = {}
    for c in en_search:
        uniq[c["budget_id"]] = c["budget"]
    unique_sum = round(sum(uniq.values()), 2)
    out["unique_daily_budget"] = unique_sum
    out["enabled_search_count"] = len(en_search)
    if unique_sum > 329.5:
        note("block", "BUDGET_OVER", f"Unique daily budget ${unique_sum} > $329")
    elif unique_sum < 50:
        note("warn", "BUDGET_LOW", f"Unique daily budget only ${unique_sum}")
    else:
        note("ok", "BUDGET", f"Unique daily budget ${unique_sum} ≤ $329")

    for c in en_search:
        if not c["google_search"]:
            note("block", "NET_GS", "Google Search network OFF", name=c["name"])
        if c["display"]:
            note("warn", "NET_DISPLAY", "Display network ON (Grant risk)", name=c["name"])

    # --- portfolio strategies ---
    portfolios = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT bidding_strategy.id, bidding_strategy.name, bidding_strategy.status,
               bidding_strategy.type,
               bidding_strategy.target_cpa.target_cpa_micros,
               bidding_strategy.maximize_conversions.target_cpa_micros
        FROM bidding_strategy
        """,
    ):
        portfolios.append(
            {
                "id": str(row.bidding_strategy.id),
                "name": row.bidding_strategy.name,
                "status": _enum(row.bidding_strategy.status),
                "type": _enum(row.bidding_strategy.type_),
                "tcpa": (
                    (row.bidding_strategy.maximize_conversions.target_cpa_micros or 0)
                    or (row.bidding_strategy.target_cpa.target_cpa_micros or 0)
                )
                / 1e6,
            }
        )
    out["portfolios"] = portfolios

    # --- conversion goals / actions ---
    actions = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT conversion_action.id, conversion_action.name, conversion_action.status,
               conversion_action.type, conversion_action.category,
               conversion_action.primary_for_goal,
               conversion_action.include_in_conversions_metric,
               conversion_action.counting_type,
               conversion_action.attribution_model_settings.attribution_model,
               conversion_action.origin
        FROM conversion_action
        WHERE conversion_action.status != 'REMOVED'
        ORDER BY conversion_action.name
        """,
    ):
        actions.append(
            {
                "id": str(row.conversion_action.id),
                "name": row.conversion_action.name,
                "status": _enum(row.conversion_action.status),
                "type": _enum(row.conversion_action.type_),
                "category": _enum(row.conversion_action.category),
                "primary": bool(row.conversion_action.primary_for_goal),
                "included": bool(row.conversion_action.include_in_conversions_metric),
                "counting": _enum(row.conversion_action.counting_type),
                "attribution": _enum(
                    row.conversion_action.attribution_model_settings.attribution_model
                ),
                "origin": _enum(row.conversion_action.origin),
            }
        )
    out["conversion_actions"] = actions
    primary_included = [a for a in actions if a["primary"] and a["included"]]
    vanity_primary = [
        a
        for a in primary_included
        if a["category"] in {"DEFAULT", "PAGE_VIEW", "ENGAGEMENT", "PURCHASE"}
        or re.search(r"page_view|first_visit|scroll|subscribe|youtube", a["name"], re.I)
    ]
    good_primary = [
        a
        for a in primary_included
        if a["category"] in {"CONTACT", "SIGNUP", "SUBMIT_LEAD_FORM", "BOOK_APPOINTMENT"}
    ]
    if vanity_primary:
        note(
            "warn",
            "VANITY_PRIMARY",
            "Vanity/engagement still primary+included",
            actions=vanity_primary,
        )
    if good_primary:
        note(
            "ok",
            "INTENT_GOALS",
            f"{len(good_primary)} intent primary+included goals",
            actions=[a["name"] for a in good_primary],
        )
    else:
        note("block", "NO_INTENT_GOALS", "No CONTACT/SIGNUP primary+included")

    # customer conversion goals
    goals = []
    try:
        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT customer_conversion_goal.category,
                   customer_conversion_goal.origin,
                   customer_conversion_goal.biddable
            FROM customer_conversion_goal
            """,
        ):
            goals.append(
                {
                    "category": _enum(row.customer_conversion_goal.category),
                    "origin": _enum(row.customer_conversion_goal.origin),
                    "biddable": bool(row.customer_conversion_goal.biddable),
                }
            )
        out["customer_conversion_goals"] = goals
        for g in goals:
            key = f"{g['category']}~{g['origin']}"
            if g["category"] in {"PAGE_VIEW", "DEFAULT", "ENGAGEMENT"} and g["biddable"]:
                note("warn", "GOAL_BIDDABLE_VANITY", f"{key} still biddable")
            if g["category"] in {"CONTACT", "SIGNUP"} and g["origin"] == "WEBSITE" and not g["biddable"]:
                note("block", "GOAL_CONTACT_OFF", f"{key} not biddable")
        if any(
            g["category"] == "CONTACT" and g["origin"] == "WEBSITE" and g["biddable"]
            for g in goals
        ):
            note("ok", "CONTACT_BIDDABLE", "CONTACT~WEBSITE biddable")
    except Exception as e:  # noqa: BLE001
        out["goals_error"] = str(e)[:240]

    # --- keywords ---
    kw_stats = Counter()
    single_words = []
    broad = []
    low_qs = []
    clinical_kw = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.name, campaign.status, ad_group.name, ad_group.status,
               ad_group_criterion.keyword.text,
               ad_group_criterion.keyword.match_type,
               ad_group_criterion.status,
               ad_group_criterion.quality_info.quality_score,
               ad_group_criterion.approval_status,
               ad_group_criterion.system_serving_status
        FROM keyword_view
        WHERE campaign.status = 'ENABLED'
          AND ad_group.status = 'ENABLED'
          AND ad_group_criterion.status = 'ENABLED'
          AND campaign.advertising_channel_type = 'SEARCH'
        """,
    ):
        text = row.ad_group_criterion.keyword.text.strip()
        mt = _enum(row.ad_group_criterion.keyword.match_type)
        kw_stats["total"] += 1
        kw_stats[mt] += 1
        qs = row.ad_group_criterion.quality_info.quality_score
        if qs and qs < 3:
            low_qs.append({"kw": text, "qs": qs, "campaign": row.campaign.name})
        cleaned = text.strip('"[]')
        if " " not in cleaned and SINGLE_WORD_RE.match(cleaned):
            single_words.append(
                {"kw": text, "match": mt, "campaign": row.campaign.name, "ag": row.ad_group.name}
            )
        if mt == "BROAD":
            broad.append({"kw": text, "campaign": row.campaign.name})
        if CLINICAL_HINTS.search(text):
            clinical_kw.append({"kw": text, "campaign": row.campaign.name})
        if _enum(row.ad_group_criterion.approval_status) not in {"APPROVED", "APPROVED_LIMITED"}:
            kw_stats[f"approval_{_enum(row.ad_group_criterion.approval_status)}"] += 1
    out["keywords"] = {
        "stats": dict(kw_stats),
        "single_word_count": len(single_words),
        "single_words_sample": single_words[:30],
        "broad_count": len(broad),
        "broad_sample": broad[:20],
        "low_qs_count": len(low_qs),
        "low_qs_sample": low_qs[:20],
        "clinical_hint_count": len(clinical_kw),
        "clinical_sample": clinical_kw[:25],
    }
    if single_words:
        note("block", "SINGLE_WORD", f"{len(single_words)} enabled single-word positives")
    else:
        note("ok", "NO_SINGLE_WORD", "0 enabled single-word positives")
    if broad:
        note("warn", "BROAD_MATCH", f"{len(broad)} enabled BROAD keywords")
    else:
        note("ok", "NO_BROAD", "No enabled BROAD positives")
    if low_qs:
        note("warn", "LOW_QS", f"{len(low_qs)} keywords QS < 3")
    else:
        note("ok", "QS_OK", "No enabled QS < 3")

    # --- RSA ads ---
    ads_summary = []
    strength = Counter()
    policy_ads = []
    enabled_rsa = 0
    ag_rsa: dict[str, int] = defaultdict(int)
    clinical_ads = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.name, campaign.status, ad_group.id, ad_group.name,
               ad_group.status, ad_group_ad.status, ad_group_ad.ad.id,
               ad_group_ad.ad.type, ad_group_ad.ad_strength,
               ad_group_ad.policy_summary.approval_status,
               ad_group_ad.policy_summary.review_status,
               ad_group_ad.ad.final_urls,
               ad_group_ad.ad.responsive_search_ad.headlines,
               ad_group_ad.ad.responsive_search_ad.descriptions
        FROM ad_group_ad
        WHERE campaign.status = 'ENABLED'
          AND campaign.advertising_channel_type = 'SEARCH'
          AND ad_group_ad.ad.type = 'RESPONSIVE_SEARCH_AD'
        """,
    ):
        st = _enum(row.ad_group_ad.status)
        strength[_enum(row.ad_group_ad.ad_strength)] += 1
        headlines = [h.text for h in row.ad_group_ad.ad.responsive_search_ad.headlines]
        descs = [d.text for d in row.ad_group_ad.ad.responsive_search_ad.descriptions]
        urls = list(row.ad_group_ad.ad.final_urls)
        approval = _enum(row.ad_group_ad.policy_summary.approval_status)
        item = {
            "campaign": row.campaign.name,
            "ag": row.ad_group.name,
            "ag_id": str(row.ad_group.id),
            "ad_id": str(row.ad_group_ad.ad.id),
            "status": st,
            "strength": _enum(row.ad_group_ad.ad_strength),
            "approval": approval,
            "review": _enum(row.ad_group_ad.policy_summary.review_status),
            "h_count": len(headlines),
            "d_count": len(descs),
            "urls": urls,
        }
        ads_summary.append(item)
        if st == "ENABLED" and row.ad_group.status.name == "ENABLED":
            enabled_rsa += 1
            ag_rsa[f"{row.campaign.name}||{row.ad_group.name}"] += 1
            if approval not in {"APPROVED", "APPROVED_LIMITED"}:
                policy_ads.append(item)
            blob = " | ".join(headlines + descs)
            if CLINICAL_HINTS.search(blob):
                clinical_ads.append(
                    {**item, "sample": [t for t in headlines + descs if CLINICAL_HINTS.search(t)][:4]}
                )
            bad_url = [u for u in urls if "example.com" not in u.lower()]
            if bad_url:
                note("block", "BAD_URL", "Non-owned final URL", urls=bad_url, ad=item["ad_id"])
            if len(headlines) < 8 or len(descs) < 3:
                note(
                    "warn",
                    "RSA_THIN",
                    f"Thin RSA H={len(headlines)} D={len(descs)}",
                    campaign=row.campaign.name,
                    ag=row.ad_group.name,
                )
    hollow = [k for k, v in ag_rsa.items() if v < 1]
    out["rsa"] = {
        "enabled_count": enabled_rsa,
        "strength": dict(strength),
        "policy_issues": policy_ads[:40],
        "clinical_enabled_sample": clinical_ads[:30],
        "clinical_enabled_count": len(clinical_ads),
        "ad_groups_with_rsa": len(ag_rsa),
        "hollow_ad_groups": hollow[:40],
        "sample": ads_summary[:5],
    }
    if hollow:
        note("block", "HOLLOW_AG", f"{len(hollow)} enabled AG without ENABLED RSA")
    else:
        note("ok", "RSA_COVERAGE", f"{len(ag_rsa)} AG have ≥1 ENABLED RSA")
    if clinical_ads:
        note(
            "warn",
            "CLINICAL_RSA",
            f"{len(clinical_ads)} ENABLED RSA still have clinical-ish copy",
        )
    poor_en = [
        a
        for a in ads_summary
        if a["status"] == "ENABLED" and a["strength"] in {"POOR", "UNSPECIFIED"}
    ]
    if poor_en:
        note("warn", "RSA_POOR", f"{len(poor_en)} ENABLED RSA with Poor/unspecified strength")

    # --- assets (campaign + account) ---
    asset_links = []
    asset_by_type: dict[str, Counter] = defaultdict(Counter)
    policy_assets = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.name, campaign.status,
               campaign.advertising_channel_type,
               campaign_asset.field_type, campaign_asset.status,
               campaign_asset.primary_status,
               campaign_asset.primary_status_reasons,
               asset.id, asset.name, asset.type,
               asset.policy_summary.approval_status,
               asset.policy_summary.review_status,
               asset.sitelink_asset.link_text,
               asset.callout_asset.callout_text,
               asset.structured_snippet_asset.header,
               asset.structured_snippet_asset.values,
               asset.text_asset.text
        FROM campaign_asset
        WHERE campaign.status = 'ENABLED'
          AND campaign.advertising_channel_type = 'SEARCH'
          AND campaign_asset.status != 'REMOVED'
        """,
    ):
        ft = _enum(row.campaign_asset.field_type)
        st = _enum(row.campaign_asset.status)
        asset_by_type[row.campaign.name][f"{ft}:{st}"] += 1
        ps = _enum(row.campaign_asset.primary_status) if row.campaign_asset.primary_status else ""
        reasons = [_enum(r) for r in row.campaign_asset.primary_status_reasons]
        approval = _enum(row.asset.policy_summary.approval_status)
        entry = {
            "campaign": row.campaign.name,
            "field": ft,
            "link_status": st,
            "primary": ps,
            "reasons": reasons,
            "asset_id": str(row.asset.id),
            "asset_name": row.asset.name,
            "asset_type": _enum(row.asset.type_),
            "approval": approval,
            "review": _enum(row.asset.policy_summary.review_status),
            "sitelink": row.asset.sitelink_asset.link_text or "",
            "callout": row.asset.callout_asset.callout_text or "",
            "snippet_header": row.asset.structured_snippet_asset.header or "",
            "snippet_values": list(row.asset.structured_snippet_asset.values),
        }
        asset_links.append(entry)
        if (
            approval not in {"APPROVED", "APPROVED_LIMITED", "UNKNOWN", "UNSPECIFIED"}
            or "POLICY" in ps
            or any("POLICY" in r for r in reasons)
            or st == "PAUSED"
        ):
            if st == "PAUSED" or "POLICY" in ps or approval in {
                "DISAPPROVED",
                "AREA_OF_INTEREST_ONLY",
            }:
                policy_assets.append(entry)

    # customer-level assets
    cust_assets = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT customer_asset.field_type, customer_asset.status,
               customer_asset.primary_status,
               asset.id, asset.name, asset.type,
               asset.policy_summary.approval_status,
               asset.structured_snippet_asset.header,
               asset.structured_snippet_asset.values
        FROM customer_asset
        WHERE customer_asset.status != 'REMOVED'
        """,
    ):
        cust_assets.append(
            {
                "field": _enum(row.customer_asset.field_type),
                "status": _enum(row.customer_asset.status),
                "primary": _enum(row.customer_asset.primary_status)
                if row.customer_asset.primary_status
                else "",
                "asset_id": str(row.asset.id),
                "asset_name": row.asset.name,
                "approval": _enum(row.asset.policy_summary.approval_status),
                "snippet_header": row.asset.structured_snippet_asset.header or "",
                "snippet_values": list(row.asset.structured_snippet_asset.values),
            }
        )

    coverage = {}
    required_fields = {
        "SITELINK",
        "CALLOUT",
        "STRUCTURED_SNIPPET",
        "AD_IMAGE",
        "BUSINESS_NAME",
        "BUSINESS_LOGO",
    }
    for c in en_search:
        name = c["name"]
        counts = asset_by_type.get(name, Counter())
        enabled_ft = Counter()
        for k, v in counts.items():
            ft, st = k.split(":", 1)
            if st == "ENABLED":
                enabled_ft[ft] += v
        missing = sorted(required_fields - set(enabled_ft) - {"BUSINESS_NAME", "BUSINESS_LOGO"})
        # BUSINESS_* often account-level
        coverage[name] = {
            "enabled_by_field": dict(enabled_ft),
            "missing_campaign_level": missing,
        }
        if enabled_ft.get("SITELINK", 0) < 4:
            note("warn", "SITELINK_FEW", f"<4 sitelinks on {name[:40]}…", n=enabled_ft.get("SITELINK", 0))
        if enabled_ft.get("CALLOUT", 0) < 4:
            note("warn", "CALLOUT_FEW", f"<4 callouts on {name[:40]}…", n=enabled_ft.get("CALLOUT", 0))
        if enabled_ft.get("AD_IMAGE", 0) < 1:
            note("warn", "NO_IMAGE", f"No AD_IMAGE on {name[:40]}…")
        if enabled_ft.get("STRUCTURED_SNIPPET", 0) < 1:
            note("warn", "NO_SNIPPET", f"No structured snippet on {name[:40]}…")

    out["assets"] = {
        "campaign_links": len(asset_links),
        "coverage": coverage,
        "policy_or_paused_sample": policy_assets[:50],
        "policy_or_paused_count": len(policy_assets),
        "customer_assets": cust_assets,
    }
    paused_snippets = [
        a
        for a in cust_assets + policy_assets
        if a.get("field") == "STRUCTURED_SNIPPET"
        and (a.get("status") == "PAUSED" or a.get("link_status") == "PAUSED")
    ]
    if paused_snippets:
        note(
            "warn",
            "SNIPPET_POLICY",
            "Structured snippet paused/policy (often Health personalized)",
            sample=paused_snippets[:5],
        )

    acct_logo = any(a["field"] == "BUSINESS_LOGO" and a["status"] == "ENABLED" for a in cust_assets)
    acct_name = any(a["field"] == "BUSINESS_NAME" and a["status"] == "ENABLED" for a in cust_assets)
    if acct_logo:
        note("ok", "LOGO", "Account business logo present")
    else:
        note("warn", "NO_LOGO", "No ENABLED account BUSINESS_LOGO")
    if acct_name:
        note("ok", "BIZ_NAME", "Account business name present")
    else:
        note("warn", "NO_BIZ_NAME", "No ENABLED account BUSINESS_NAME")

    # --- audiences / CM ---
    audiences = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.name, campaign_criterion.type,
               campaign_criterion.status,
               campaign_criterion.user_list.user_list
        FROM campaign_criterion
        WHERE campaign.status = 'ENABLED'
          AND campaign_criterion.type IN (
            'USER_LIST', 'USER_INTEREST', 'CUSTOM_AUDIENCE',
            'COMBINED_AUDIENCE', 'AUDIENCE'
          )
        """,
    ):
        audiences.append(
            {
                "campaign": row.campaign.name,
                "type": _enum(row.campaign_criterion.type_),
                "status": _enum(row.campaign_criterion.status),
                "user_list": row.campaign_criterion.user_list.user_list or "",
            }
        )
    out["audiences"] = audiences
    cm = [a for a in audiences if a["type"] == "USER_LIST"]
    if cm:
        note("block", "CUSTOMER_MATCH", "USER_LIST criteria on enabled campaigns", sample=cm[:10])
    else:
        note("ok", "NO_CM", "No Customer Match / USER_LIST on enabled campaigns")

    # --- geo / language sample ---
    geo_lang = defaultdict(lambda: {"geo": 0, "lang": []})
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.name, campaign_criterion.type,
               campaign_criterion.location.geo_target_constant,
               campaign_criterion.language.language_constant,
               campaign_criterion.negative
        FROM campaign_criterion
        WHERE campaign.status = 'ENABLED'
          AND campaign_criterion.type IN ('LOCATION', 'LANGUAGE')
          AND campaign_criterion.negative = FALSE
        """,
    ):
        if row.campaign_criterion.type_.name == "LOCATION":
            geo_lang[row.campaign.name]["geo"] += 1
        else:
            geo_lang[row.campaign.name]["lang"].append(
                row.campaign_criterion.language.language_constant
            )
    out["geo_lang"] = {k: v for k, v in geo_lang.items()}
    for c in en_search:
        g = geo_lang.get(c["name"], {"geo": 0, "lang": []})
        if g["geo"] < 1:
            note("warn", "NO_GEO", "No positive location criteria", name=c["name"])
        if not g["lang"]:
            note("warn", "NO_LANG", "No language criteria", name=c["name"])

    # --- recommendations / auto-apply ---
    subs = []
    try:
        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT recommendation_subscription.type,
                   recommendation_subscription.status
            FROM recommendation_subscription
            """,
        ):
            subs.append(
                {
                    "type": _enum(row.recommendation_subscription.type_),
                    "status": _enum(row.recommendation_subscription.status),
                }
            )
        out["recommendation_subscriptions"] = subs
        enabled_subs = [s for s in subs if s["status"] == "ENABLED"]
        risky = [
            s
            for s in enabled_subs
            if s["type"]
            in {
                "MAXIMIZE_CLICKS_OPT_IN",
                "TARGET_CPA_OPT_IN",
                "TARGET_ROAS_OPT_IN",
                "SET_TARGET_CPA",
                "USE_BROAD_MATCH_KEYWORD",
                "KEYWORD_MATCH_TYPE",
            }
        ]
        if risky:
            note("warn", "AUTO_APPLY_ON", "Risky auto-apply still ENABLED", risky=risky)
        else:
            note("ok", "AUTO_APPLY", "Risky auto-apply subscriptions not enabled")
    except Exception as e:  # noqa: BLE001
        out["reco_error"] = str(e)[:240]

    # --- TODAY rollup ---
    today = {
        "impr": sum(c["impr"] for c in en_search),
        "clicks": sum(c["clicks"] for c in en_search),
        "cost": round(sum(c["cost"] for c in en_search), 2),
    }
    out["today_enabled_search"] = today
    if today["impr"] == 0:
        note(
            "warn",
            "ZERO_TODAY",
            "0 impressions TODAY on enabled Search (learning / rank / review lag)",
        )

    out["summary"] = {
        "ok": len(out["ok"]),
        "warn": len(out["warn"]),
        "block": len(out["block"]),
        "enabled_search": len(en_search),
        "unique_budget": unique_sum,
    }

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = ROOT / "reports" / day
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "enterprise_grant_full_checklist.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": out["summary"], "block": out["block"], "warn": out["warn"][:40], "ok": [x["code"] for x in out["ok"]]}, ensure_ascii=False, indent=2))
    print(f"Wrote: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
