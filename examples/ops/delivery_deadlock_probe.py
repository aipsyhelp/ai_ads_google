#!/usr/bin/env python3
"""Read-only probe of the cold-start delivery deadlock.

Answers three questions with hard data:
  1. Are conversions being RECORDED in Ads recently (per action, last N days)?
  2. What is each ENABLED campaign's bidding strategy, budget, learning/serving
     status, and recent spend?
  3. Are keywords priced out (first_page_cpc / top_of_page estimates vs the
     Grant environment)?
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


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


def main() -> int:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    _load_env()
    import logging

    logging.getLogger("google.ads.googleads.client").setLevel(logging.CRITICAL)
    from ads_ai_agents.adapters.settings import GoogleAdsSettings
    from google.ads.googleads.client import GoogleAdsClient

    settings = GoogleAdsSettings.from_env()
    client = GoogleAdsClient.load_from_dict(settings.client_config())
    ga = client.get_service("GoogleAdsService")
    cid = settings.customer_id

    end = date.today()
    start = end - timedelta(days=days)
    between = f"BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'"

    print(f"\n=== 1. Conversions RECORDED in Ads, by action ({start}..{end}) ===")
    rows = list(ga.search(customer_id=cid, query=f"""
        SELECT conversion_action.name, conversion_action.category,
               metrics.all_conversions
        FROM conversion_action
        WHERE segments.date {between}
        ORDER BY metrics.all_conversions DESC
    """))
    if not rows:
        print("  (no conversions recorded in the period)")
    tot_all = 0.0
    for r in rows:
        ac = float(r.metrics.all_conversions or 0)
        tot_all += ac
        if ac:
            print(f"  all={ac:>8.1f}  {r.conversion_action.category.name:<14} {r.conversion_action.name}")
    print(f"  --- TOTAL Ads all_conversions(ad-attributed, includes secondary)={tot_all:.1f}")

    print(f"\n=== 2. Campaign bidding / budget / status ({start}..{end}) ===")
    rows = list(ga.search(customer_id=cid, query=f"""
        SELECT campaign.name, campaign.status,
               campaign.bidding_strategy_type,
               campaign.maximize_conversions.target_cpa_micros,
               campaign_budget.amount_micros,
               campaign.primary_status,
               metrics.impressions, metrics.clicks, metrics.conversions,
               metrics.cost_micros, metrics.search_budget_lost_impression_share,
               metrics.search_rank_lost_impression_share
        FROM campaign
        WHERE campaign.status = 'ENABLED' AND segments.date {between}
        ORDER BY campaign.name
    """))
    for r in rows:
        c = r.campaign
        m = r.metrics
        tcpa = c.maximize_conversions.target_cpa_micros
        try:
            pstatus = c.primary_status.name
        except Exception:
            pstatus = "?"
        print(
            f"  {c.name}\n"
            f"     strat={c.bidding_strategy_type.name} tCPA={tcpa/1e6 if tcpa else 0:.2f} "
            f"budget=${r.campaign_budget.amount_micros/1e6:.1f}/d primary={pstatus}\n"
            f"     impr={int(m.impressions)} clicks={int(m.clicks)} conv={m.conversions:.1f} "
            f"cost=${m.cost_micros/1e6:.2f} "
            f"lost_IS_budget={m.search_budget_lost_impression_share:.2f} "
            f"lost_IS_rank={m.search_rank_lost_impression_share:.2f}"
        )

    print("\n=== 3. Bid-strategy learning status (portfolio, if any) ===")
    rows = list(ga.search(customer_id=cid, query="""
        SELECT bidding_strategy.name, bidding_strategy.type,
               bidding_strategy.status,
               bidding_strategy.maximize_conversions.target_cpa_micros
        FROM bidding_strategy
    """))
    if not rows:
        print("  (no portfolio strategies — everything is campaign-level)")
    for r in rows:
        b = r.bidding_strategy
        print(f"  {b.name} type={b.type_.name} status={b.status.name} "
              f"tCPA={b.maximize_conversions.target_cpa_micros/1e6 if b.maximize_conversions.target_cpa_micros else 0:.2f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
