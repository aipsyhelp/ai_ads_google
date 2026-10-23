#!/usr/bin/env python3
"""Cold-start delivery recovery: put STANDALONE Grant Search campaigns on
Maximize Clicks (TARGET_SPEND) with a $2 CPC ceiling to force affordable
long-tail traffic that warms the account conversion signal.

Rationale (evidence-based):
  * Tracking is verified healthy (GA4 + Ads conversion pings fire live).
  * ENABLED campaigns are ELIGIBLE but lose 63-90% impression share to RANK,
    not budget: Max Conversions has no fresh ad-attributed conversions, so it
    bids near-zero and cannot clear the $7-10 Grant auctions -> a cold-start
    deadlock (0 impressions -> 0 clicks -> 0 ad conversions -> 0 signal).
  * Maximize Clicks with the Grant $2 CPC cap is the DEFAULT Grant bidding and
    100% policy-safe. It wins the cheap UA long-tail, generating clicks ->
    page_view / engagement conversions -> the shared Max Conversions portfolio
    warms up -> the main (portfolio) campaigns recover.

Scope safety:
  * Only touches STANDALONE campaigns (own budget, NOT on a portfolio and NOT
    aligned to a shared bid strategy) to avoid BIDDING_STRATEGY_AND_BUDGET_
    MUST_BE_ALIGNED. The 4 portfolio Max Conversions campaigns are left intact.
  * Dry-run (validate_only) by default. Pass --apply to mutate.

Usage:
  PYTHONPATH=src python3 scripts/bridge_maxclicks_recovery.py           # dry-run
  PYTHONPATH=src python3 scripts/bridge_maxclicks_recovery.py --apply   # live
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

CPC_BID_CEILING_MICROS = 2_000_000  # $2.00 — Grant non-Smart-Bidding CPC cap


def _load_env() -> None:
    for raw in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _inventory(ga: Any, customer_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in ga.search(
        customer_id=customer_id,
        query="""
        SELECT campaign.id, campaign.resource_name, campaign.name,
               campaign.bidding_strategy, campaign.bidding_strategy_type,
               campaign_budget.aligned_bidding_strategy_id,
               campaign_budget.amount_micros
        FROM campaign
        WHERE campaign.status = 'ENABLED'
        ORDER BY campaign.name
        """,
    ):
        rows.append(
            {
                "id": str(row.campaign.id),
                "resource_name": row.campaign.resource_name,
                "name": row.campaign.name,
                "bid": row.campaign.bidding_strategy_type.name,
                "portfolio": row.campaign.bidding_strategy or "",
                "aligned": int(row.campaign_budget.aligned_bidding_strategy_id or 0),
                "budget": row.campaign_budget.amount_micros / 1e6,
            }
        )
    return rows


def _is_standalone(item: dict[str, Any]) -> bool:
    return not item["portfolio"] and item["aligned"] == 0


def main() -> int:
    from ads_ai_agents.intelligence.recovery_playbook import REFUSE_MAX_CLICKS, refuse

    return refuse("bridge_maxclicks_recovery.py", REFUSE_MAX_CLICKS)


def _legacy_main_disabled(apply: bool = False) -> int:
    """Kept for reference only; main() refuses to run it."""
    from google.ads.googleads.client import GoogleAdsClient

    from ads_ai_agents.adapters.settings import GoogleAdsSettings

    settings = GoogleAdsSettings.from_env()
    client = GoogleAdsClient.load_from_dict(settings.client_config())
    ga = client.get_service("GoogleAdsService")
    customer_id = settings.customer_id
    service = client.get_service("CampaignService")

    inv = _inventory(ga, customer_id)
    targets = [
        it for it in inv
        if _is_standalone(it) and it["bid"] != "TARGET_SPEND"
    ]
    skipped_portfolio = [it["name"] for it in inv if not _is_standalone(it)]

    operations = []
    for it in targets:
        op = client.get_type("CampaignOperation")
        op.update.resource_name = it["resource_name"]
        # Detach any campaign-level portfolio (none for standalone, but safe).
        op.update.bidding_strategy = ""
        op.update.target_spend.cpc_bid_ceiling_micros = CPC_BID_CEILING_MICROS
        op.update_mask.CopyFrom(
            field_mask_pb2.FieldMask(
                paths=["bidding_strategy", "target_spend.cpc_bid_ceiling_micros"]
            )
        )
        operations.append(op)

    result: dict[str, Any] = {
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "customer_id": customer_id,
        "mode": "APPLY" if apply else "DRY_RUN (validate_only)",
        "cpc_bid_ceiling": CPC_BID_CEILING_MICROS / 1e6,
        "targets_to_maximize_clicks": [
            {"name": it["name"], "from": it["bid"], "budget_per_day": it["budget"]}
            for it in targets
        ],
        "left_on_portfolio_max_conversions": skipped_portfolio,
    }

    if not operations:
        result["note"] = "No standalone campaigns needed switching."
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    # Always validate first.
    validation = client.get_type("MutateCampaignsRequest")
    validation.customer_id = customer_id
    validation.operations.extend(operations)
    validation.validate_only = True
    service.mutate_campaigns(request=validation)
    result["validation"] = "PASSED"

    if apply:
        request = client.get_type("MutateCampaignsRequest")
        request.customer_id = customer_id
        request.operations.extend(operations)
        request.partial_failure = True
        response = service.mutate_campaigns(request=request)
        if response.partial_failure_error.code:
            result["partial_failure"] = response.partial_failure_error.message
        result["applied"] = [r.resource_name for r in response.results]

    out_dir = ROOT / "reports" / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "bridge_maxclicks_recovery.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Wrote: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
