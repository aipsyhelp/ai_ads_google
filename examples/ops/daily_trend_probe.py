#!/usr/bin/env python3
"""Read-only daily trend for Grant delivery + conversion signal.

Shows, per day over a window, ad delivery (impr/clicks/cost/conv from ENABLED
Search campaigns) alongside account-level Ads all_conversions. These remain
ad-attributed reports and cannot verify site-wide GA4/GTM health.

Usage:  PYTHONPATH=src python3 scripts/daily_trend_probe.py [DAYS]
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
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 45
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

    # Ad delivery per day (ENABLED Search campaigns)
    delivery: dict[str, list[float]] = {}
    for row in ga.search(
        customer_id=cid,
        query=f"""
        SELECT segments.date, metrics.impressions, metrics.clicks,
               metrics.cost_micros, metrics.conversions
        FROM campaign
        WHERE campaign.status = 'ENABLED' AND segments.date {between}
        """,
    ):
        d = row.segments.date
        agg = delivery.setdefault(d, [0.0, 0.0, 0.0, 0.0])
        agg[0] += int(row.metrics.impressions or 0)
        agg[1] += int(row.metrics.clicks or 0)
        agg[2] += int(row.metrics.cost_micros or 0)
        agg[3] += float(row.metrics.conversions or 0)

    # Account-level Ads all_conversions includes secondary conversion actions.
    ads_all: dict[str, float] = {}
    for row in ga.search(
        customer_id=cid,
        query=f"""
        SELECT segments.date, metrics.all_conversions
        FROM customer
        WHERE segments.date {between}
        """,
    ):
        ads_all[row.segments.date] = ads_all.get(row.segments.date, 0.0) + float(
            row.metrics.all_conversions or 0
        )

    all_days = sorted(set(delivery) | set(ads_all))
    print(f"\n=== Daily trend {start} .. {end} (cid={cid}) ===")
    print(f"{'date':<12}{'impr':>7}{'clicks':>8}{'cost$':>9}{'ad_conv':>9}{'ads_all':>11}")
    print("-" * 56)
    for d in all_days:
        a = delivery.get(d, [0, 0, 0, 0])
        sc = ads_all.get(d, 0.0)
        print(
            f"{d:<12}{int(a[0]):>7}{int(a[1]):>8}{a[2]/1e6:>9.2f}{a[3]:>9.1f}{sc:>11.1f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
