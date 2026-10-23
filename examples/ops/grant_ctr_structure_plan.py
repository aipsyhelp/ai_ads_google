#!/usr/bin/env python3
"""Read-only plan for an Ad Grants structure that keeps monthly CTR above 5%.

Uses keyword history from the last period with normal delivery and classifies
every enabled positive keyword in enabled Search campaigns:

- keep:  enough history and CTR at or above the keep threshold;
- pause: enough history and CTR below the cut threshold;
- watch: little or no history, or CTR between the thresholds.

BROAD keywords are flagged separately: the Grant playbook allows only phrase
and exact for positives. The script never mutates the account.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

HISTORY_START = "2026-05-01"
HISTORY_END = "2026-08-05"
MIN_HISTORY_IMPRESSIONS = 50
KEEP_CTR = 0.06
CUT_CTR = 0.04


def _load_env() -> None:
    for raw in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def classify(impressions: int, clicks: int) -> str:
    if impressions < MIN_HISTORY_IMPRESSIONS:
        return "watch"
    ctr = clicks / impressions
    if ctr >= KEEP_CTR:
        return "keep"
    if ctr < CUT_CTR:
        return "pause"
    return "watch"


def _ctr(impressions: int, clicks: int) -> float | None:
    return round(clicks / impressions, 4) if impressions else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=HISTORY_START)
    parser.add_argument("--end", default=HISTORY_END)
    args = parser.parse_args()

    _load_env()
    import logging

    logging.getLogger("google.ads.googleads.client").setLevel(logging.CRITICAL)
    from ads_ai_agents.adapters.settings import GoogleAdsSettings
    from google.ads.googleads.client import GoogleAdsClient

    settings = GoogleAdsSettings.from_env()
    client = GoogleAdsClient.load_from_dict(settings.client_config())
    ga = client.get_service("GoogleAdsService")
    cid = settings.customer_id

    by_text: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    by_criterion: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    campaign_hist: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in ga.search(
        customer_id=cid,
        query=f"""
        SELECT campaign.name, ad_group_criterion.resource_name,
               ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type,
               metrics.impressions, metrics.clicks
        FROM keyword_view
        WHERE segments.date BETWEEN '{args.start}' AND '{args.end}'
          AND metrics.impressions > 0
        """,
    ):
        k = row.ad_group_criterion
        impr, clk = int(row.metrics.impressions), int(row.metrics.clicks)
        by_criterion[k.resource_name][0] += impr
        by_criterion[k.resource_name][1] += clk
        key = (k.keyword.text.lower().strip(), row.campaign.name)
        by_text[key][0] += impr
        by_text[key][1] += clk
        campaign_hist[row.campaign.name][0] += impr
        campaign_hist[row.campaign.name][1] += clk

    keywords = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.name, ad_group.name, ad_group_criterion.resource_name,
               ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type,
               ad_group_criterion.quality_info.quality_score
        FROM ad_group_criterion
        WHERE campaign.status = 'ENABLED' AND ad_group.status = 'ENABLED'
          AND campaign.advertising_channel_type = 'SEARCH'
          AND ad_group_criterion.status = 'ENABLED'
          AND ad_group_criterion.type = 'KEYWORD'
          AND ad_group_criterion.negative = FALSE
        """,
    ):
        k = row.ad_group_criterion
        hist = by_criterion.get(k.resource_name)
        source = "criterion"
        if not hist:
            hist = by_text.get((k.keyword.text.lower().strip(), row.campaign.name))
            source = "same_text_in_campaign" if hist else "none"
        impr, clk = hist if hist else (0, 0)
        keywords.append(
            {
                "campaign": row.campaign.name,
                "ad_group": row.ad_group.name,
                "resource_name": k.resource_name,
                "text": k.keyword.text,
                "match_type": k.keyword.match_type.name,
                "quality_score": int(k.quality_info.quality_score or 0),
                "history_impressions": impr,
                "history_clicks": clk,
                "history_ctr": _ctr(impr, clk),
                "history_source": source,
                "decision": classify(impr, clk),
                "broad": k.keyword.match_type.name == "BROAD",
            }
        )

    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    for kw in keywords:
        bucket = totals[kw["decision"]]
        bucket[0] += 1
        bucket[1] += kw["history_impressions"]
        bucket[2] += kw["history_clicks"]
    all_impr = sum(v[1] for v in totals.values())
    all_clk = sum(v[2] for v in totals.values())
    kept_impr = totals["keep"][1] + totals["watch"][1]
    kept_clk = totals["keep"][2] + totals["watch"][2]

    per_campaign = []
    for name in sorted({kw["campaign"] for kw in keywords}):
        rows = [kw for kw in keywords if kw["campaign"] == name]
        hi, hc = campaign_hist.get(name, [0, 0])
        per_campaign.append(
            {
                "campaign": name,
                "enabled_keywords": len(rows),
                "keep": sum(1 for kw in rows if kw["decision"] == "keep"),
                "pause": sum(1 for kw in rows if kw["decision"] == "pause"),
                "watch": sum(1 for kw in rows if kw["decision"] == "watch"),
                "broad": sum(1 for kw in rows if kw["broad"]),
                "history_ctr": _ctr(hi, hc),
            }
        )

    pause = sorted(
        (kw for kw in keywords if kw["decision"] == "pause"),
        key=lambda kw: kw["history_impressions"],
        reverse=True,
    )
    result = {
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "customer_id": cid,
        "mode": "PLAN_ONLY",
        "history_window": [args.start, args.end],
        "thresholds": {
            "min_history_impressions": MIN_HISTORY_IMPRESSIONS,
            "keep_ctr": KEEP_CTR,
            "cut_ctr": CUT_CTR,
            "grant_min_monthly_ctr": 0.05,
        },
        "summary": {
            "enabled_keywords": len(keywords),
            "keep": totals["keep"][0],
            "pause": totals["pause"][0],
            "watch": totals["watch"][0],
            "broad_enabled": sum(1 for kw in keywords if kw["broad"]),
            "history_ctr_current_set": _ctr(all_impr, all_clk),
            "history_ctr_after_pause": _ctr(kept_impr, kept_clk),
            "history_impressions_removed": totals["pause"][1],
        },
        "per_campaign": per_campaign,
        "pause_candidates": pause,
        "broad_keywords": [kw for kw in keywords if kw["broad"]],
        "rollback": "Plan only. If applied, re-enable the listed resource_names.",
    }

    out_dir = ROOT / "reports" / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "grant_ctr_structure_plan.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"summary": result["summary"], "per_campaign": per_campaign}, ensure_ascii=False, indent=2))
    print("top pause candidates:")
    for kw in pause[:25]:
        print(
            f"  {kw['history_ctr']:.2%} impr={kw['history_impressions']:5d} {kw['match_type']:<6} "
            f"'{kw['text']}' | {kw['campaign'][-30:]}"
        )
    print(f"Wrote: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
