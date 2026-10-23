#!/usr/bin/env python3
"""Fallback: switch standalone TARGET_SPEND campaigns back to Maximize
Conversions with NO tCPA (cold-start policy).

Use when Max Clicks $2 still cannot clear this account's auctions (historical
winning CPC ~$7). Smart Bidding is the only Grant strategy that lifts the $2
CPC cap. Keep each campaign's own budget — do NOT re-attach to the $20 shared
portfolio, or Ukraine-wide would lose its $82/day room.

Dry-run (validate_only) by default. Pass --apply to mutate.

Usage:
  PYTHONPATH=src python3 scripts/revert_to_maximize_conversions.py
  PYTHONPATH=src python3 scripts/revert_to_maximize_conversions.py --apply
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

# Cold-start: no tCPA until quality conversions accumulate.
NO_TARGET_CPA_MICROS = 0


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
               campaign.maximize_conversions.target_cpa_micros,
               campaign_budget.id,
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
                "budget_id": str(row.campaign_budget.id),
                "budget": row.campaign_budget.amount_micros / 1e6,
                "tcpa": (row.campaign.maximize_conversions.target_cpa_micros or 0) / 1e6,
            }
        )
    return rows


def main() -> int:
    from ads_ai_agents.intelligence.recovery_playbook import REFUSE_CLEAR_TCPA, refuse

    return refuse("revert_to_maximize_conversions.py", REFUSE_CLEAR_TCPA)


def _legacy_main_disabled() -> int:
    apply = "--apply" in sys.argv
    _load_env()
    from ads_ai_agents.adapters.settings import GoogleAdsSettings
    from ads_ai_agents.adapters.write_guard import assert_write_service
    from google.ads.googleads.client import GoogleAdsClient

    if apply:
        from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed

        assert_mutations_allowed()
    assert_write_service("CampaignService", scope="grant_delivery_recovery")

    settings = GoogleAdsSettings.from_env()
    client = GoogleAdsClient.load_from_dict(settings.client_config())
    ga = client.get_service("GoogleAdsService")
    cid = settings.customer_id
    service = client.get_service("CampaignService")

    inv = _inventory(ga, cid)
    targets = [
        it
        for it in inv
        if it["bid"] == "TARGET_SPEND" and not it["portfolio"] and it["aligned"] == 0
    ]
    left_on_portfolio = [
        {"name": it["name"], "bid": it["bid"], "budget": it["budget"]}
        for it in inv
        if it["bid"] != "TARGET_SPEND"
    ]

    operations = []
    for it in targets:
        op = client.get_type("CampaignOperation")
        op.update.resource_name = it["resource_name"]
        op.update.bidding_strategy = ""
        op.update.maximize_conversions.target_cpa_micros = NO_TARGET_CPA_MICROS
        op.update_mask.CopyFrom(
            field_mask_pb2.FieldMask(
                paths=["bidding_strategy", "maximize_conversions.target_cpa_micros"]
            )
        )
        operations.append(op)

    result: dict[str, Any] = {
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "customer_id": cid,
        "mode": "APPLY" if apply else "DRY_RUN (validate_only)",
        "policy": "Maximize Conversions, no tCPA (cold-start), keep own budgets",
        "targets_to_max_conversions": [
            {"name": it["name"], "from": it["bid"], "budget_per_day": it["budget"]}
            for it in targets
        ],
        "left_untouched": left_on_portfolio,
        "total_distinct_budget": round(
            sum({it["budget_id"]: it["budget"] for it in inv}.values()), 1
        ),
    }

    if not operations:
        result["note"] = "No TARGET_SPEND standalone campaigns to revert."
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    validation = client.get_type("MutateCampaignsRequest")
    validation.customer_id = cid
    validation.operations.extend(operations)
    validation.validate_only = True
    service.mutate_campaigns(request=validation)
    result["validation"] = "PASSED"

    if apply:
        request = client.get_type("MutateCampaignsRequest")
        request.customer_id = cid
        request.operations.extend(operations)
        request.partial_failure = True
        response = service.mutate_campaigns(request=request)
        if response.partial_failure_error.code:
            result["partial_failure"] = response.partial_failure_error.message
        result["applied"] = [r.resource_name for r in response.results]

    out_dir = ROOT / "reports" / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "revert_to_maximize_conversions.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Wrote: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
