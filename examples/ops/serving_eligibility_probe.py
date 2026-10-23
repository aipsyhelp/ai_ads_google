#!/usr/bin/env python3
"""Read-only: why are campaigns not serving? Check ad groups, ads, keywords."""
from __future__ import annotations
import os, sys
from pathlib import Path
from collections import Counter, defaultdict

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

def _load_env():
    for raw in (ROOT / ".env").read_text().splitlines():
        l = raw.strip()
        if l and not l.startswith("#") and "=" in l:
            k, v = l.split("=", 1); os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

def main():
    _load_env()
    import logging; logging.getLogger("google.ads.googleads.client").setLevel(logging.CRITICAL)
    from ads_ai_agents.adapters.settings import GoogleAdsSettings
    from google.ads.googleads.client import GoogleAdsClient
    s = GoogleAdsSettings.from_env(); c = GoogleAdsClient.load_from_dict(s.client_config())
    ga = c.get_service("GoogleAdsService"); cid = s.customer_id

    print("=== AD GROUPS (ENABLED campaigns) ===")
    ags = Counter()
    for r in ga.search(customer_id=cid, query="""
        SELECT ad_group.status, campaign.name FROM ad_group
        WHERE campaign.status='ENABLED' """):
        ags[r.ad_group.status.name] += 1
    print(" ", dict(ags))

    print("\n=== ADS by approval/status (ENABLED campaigns) ===")
    ad_stat = Counter(); ad_appr = Counter(); per_camp = defaultdict(Counter)
    for r in ga.search(customer_id=cid, query="""
        SELECT ad_group_ad.status, ad_group_ad.policy_summary.approval_status,
               ad_group_ad.policy_summary.review_status, campaign.name, ad_group.status
        FROM ad_group_ad
        WHERE campaign.status='ENABLED' AND ad_group.status='ENABLED' """):
        a = r.ad_group_ad
        ad_stat[a.status.name] += 1
        ad_appr[a.policy_summary.approval_status.name] += 1
        per_camp[r.campaign.name][a.policy_summary.approval_status.name] += 1
    print("  status:", dict(ad_stat))
    print("  approval:", dict(ad_appr))
    for camp, cc in per_camp.items():
        print(f"    {camp[:55]:<57} {dict(cc)}")

    print("\n=== KEYWORDS: status + system_serving + first_page bid (ENABLED campaigns) ===")
    kw_stat = Counter(); kw_serv = Counter(); low_qs = 0; total = 0
    eligible_examples = []
    for r in ga.search(customer_id=cid, query="""
        SELECT ad_group_criterion.status,
               ad_group_criterion.system_serving_status,
               ad_group_criterion.quality_info.quality_score,
               ad_group_criterion.keyword.text,
               ad_group_criterion.keyword.match_type,
               ad_group_criterion.negative,
               campaign.name
        FROM ad_group_criterion
        WHERE campaign.status='ENABLED' AND ad_group.status='ENABLED'
              AND ad_group_criterion.type='KEYWORD'
              AND ad_group_criterion.negative=FALSE """):
        k = r.ad_group_criterion
        total += 1
        kw_stat[k.status.name] += 1
        kw_serv[k.system_serving_status.name] += 1
        qs = k.quality_info.quality_score
        if qs and qs <= 2:
            low_qs += 1
        if k.status.name == "ENABLED" and k.system_serving_status.name == "ELIGIBLE" and len(eligible_examples) < 15:
            eligible_examples.append(f"{k.keyword.text} [{k.keyword.match_type.name}] QS={qs}")
    print(f"  total positive keywords: {total}")
    print("  status:", dict(kw_stat))
    print("  system_serving:", dict(kw_serv))
    print(f"  QS<=2: {low_qs}")
    print("  eligible examples:")
    for e in eligible_examples:
        print("    -", e)

    print("\n=== NEGATIVE keyword volume (could over-block) ===")
    neg = Counter()
    for r in ga.search(customer_id=cid, query="""
        SELECT campaign.name, campaign_criterion.type
        FROM campaign_criterion
        WHERE campaign.status='ENABLED' AND campaign_criterion.type='KEYWORD'
              AND campaign_criterion.negative=TRUE """):
        neg[r.campaign.name] += 1
    print("  campaign-level negatives:", dict(neg) if neg else "none")

if __name__ == "__main__":
    main()
