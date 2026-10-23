"""Force every non-removed campaign onto Maximize Conversions.

Google Ad Grants support repeatedly asked for Maximize Conversions. Enabled
campaigns already used it, but paused Maximize Clicks (TARGET_SPEND) and
Maximize Conversion Value campaigns — plus an orphan Target Spend portfolio —
still appear when reviewers open Tools → Bid strategies or Campaigns → All.
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
from ads_ai_agents.platform.account_ids import load_account_ids  # noqa: E402

# Cold-start: no tCPA. A $500 target on zero conversions suppresses bids.
TARGET_CPA_MICROS = 0
ORPHAN_TARGET_SPEND_PORTFOLIO_ID = str(load_account_ids()["portfolio_strategies"]["orphan_target_spend"])


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
        SELECT campaign.id, campaign.resource_name, campaign.name, campaign.status,
               campaign.advertising_channel_type, campaign.bidding_strategy_type,
               campaign.bidding_strategy,
               campaign.maximize_conversions.target_cpa_micros,
               campaign_budget.aligned_bidding_strategy_id
        FROM campaign
        WHERE campaign.status != 'REMOVED'
        ORDER BY campaign.status, campaign.name
        """,
    ):
        rows.append(
            {
                "id": str(row.campaign.id),
                "resource_name": row.campaign.resource_name,
                "name": row.campaign.name,
                "status": row.campaign.status.name,
                "channel": row.campaign.advertising_channel_type.name,
                "bid": row.campaign.bidding_strategy_type.name,
                "portfolio": row.campaign.bidding_strategy or "",
                "tcpa_micros": int(row.campaign.maximize_conversions.target_cpa_micros or 0),
                "aligned": int(row.campaign_budget.aligned_bidding_strategy_id or 0),
            }
        )
    return rows


def _set_standard_maximize_conversions(
    client: Any,
    customer_id: str,
    campaigns: list[dict[str, Any]],
) -> list[str]:
    """Switch non-portfolio campaigns that are not Maximize Conversions."""
    service = client.get_service("CampaignService")
    operations = []
    touched: list[str] = []
    for item in campaigns:
        if item["aligned"]:
            # Shared Grant budget is locked to the portfolio Max Conversions
            # strategy; campaign-level mutate fails BIDDING_STRATEGY_AND_BUDGET_
            # MUST_BE_ALIGNED. Already MAXIMIZE_CONVERSIONS via portfolio.
            continue
        if item["bid"] == "MAXIMIZE_CONVERSIONS" and not item["portfolio"]:
            continue
        op = client.get_type("CampaignOperation")
        op.update.resource_name = item["resource_name"]
        paths: list[str] = []
        if item["portfolio"]:
            op.update.bidding_strategy = ""
            paths.append("bidding_strategy")
        op.update.maximize_conversions.target_cpa_micros = TARGET_CPA_MICROS
        paths.append("maximize_conversions.target_cpa_micros")
        op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=paths))
        operations.append(op)
        touched.append(item["name"])

    if not operations:
        return []

    validation = client.get_type("MutateCampaignsRequest")
    validation.customer_id = customer_id
    validation.operations.extend(operations)
    validation.validate_only = True
    service.mutate_campaigns(request=validation)

    request = client.get_type("MutateCampaignsRequest")
    request.customer_id = customer_id
    request.operations.extend(operations)
    request.partial_failure = True
    response = service.mutate_campaigns(request=request)
    if response.partial_failure_error.code:
        raise RuntimeError(response.partial_failure_error.message)
    return touched


def _rename_portfolio(client: Any, customer_id: str, strategy_id: str, name: str) -> str | None:
    ga = client.get_service("GoogleAdsService")
    rows = list(
        ga.search(
            customer_id=customer_id,
            query=f"""
            SELECT bidding_strategy.resource_name, bidding_strategy.name,
                   bidding_strategy.type, bidding_strategy.status
            FROM bidding_strategy
            WHERE bidding_strategy.id = {strategy_id}
            """,
        )
    )
    if not rows:
        return None
    strategy = rows[0].bidding_strategy
    if strategy.name == name:
        return strategy.resource_name
    service = client.get_service("BiddingStrategyService")
    op = client.get_type("BiddingStrategyOperation")
    op.update.resource_name = strategy.resource_name
    op.update.name = name
    op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["name"]))
    service.mutate_bidding_strategies(customer_id=customer_id, operations=[op])
    return strategy.resource_name


def _remove_orphan_target_spend(client: Any, customer_id: str) -> str | None:
    ga = client.get_service("GoogleAdsService")
    rows = list(
        ga.search(
            customer_id=customer_id,
            query=f"""
            SELECT bidding_strategy.resource_name, bidding_strategy.name,
                   bidding_strategy.type, bidding_strategy.status,
                   bidding_strategy.campaign_count
            FROM bidding_strategy
            WHERE bidding_strategy.id = {ORPHAN_TARGET_SPEND_PORTFOLIO_ID}
            """,
        )
    )
    if not rows:
        return None
    strategy = rows[0].bidding_strategy
    if int(strategy.campaign_count or 0) > 0:
        return f"skipped_in_use:{strategy.campaign_count}"
    service = client.get_service("BiddingStrategyService")
    op = client.get_type("BiddingStrategyOperation")
    op.remove = strategy.resource_name
    try:
        service.mutate_bidding_strategies(customer_id=customer_id, operations=[op])
        return strategy.resource_name
    except Exception as exc:  # noqa: BLE001
        # Some accounts disallow remove; pause by renaming is not enough.
        return f"remove_failed:{exc}"[:240]


def main() -> int:
    from ads_ai_agents.intelligence.recovery_playbook import REFUSE_CLEAR_TCPA, refuse

    return refuse("enforce_maximize_conversions.py", REFUSE_CLEAR_TCPA)


def _legacy_main_disabled() -> int:
    _load_env()
    from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
    from ads_ai_agents.adapters.settings import GoogleAdsSettings
    from ads_ai_agents.adapters.write_guard import assert_write_service
    from google.ads.googleads.client import GoogleAdsClient

    if "--apply" not in sys.argv:
        print("DRY_RUN: pass --apply to mutate. Default tCPA is 0 (cold-start).")
        return 0
    assert_mutations_allowed()
    assert_write_service("CampaignService", scope="grant_delivery_recovery")
    assert_write_service("BiddingStrategyService", scope="grant_delivery_recovery")

    settings = GoogleAdsSettings.from_env()
    client = GoogleAdsClient.load_from_dict(settings.client_config())
    ga = client.get_service("GoogleAdsService")
    customer_id = settings.customer_id

    before = _inventory(ga, customer_id)
    needs_fix = [item for item in before if item["bid"] != "MAXIMIZE_CONVERSIONS"]
    updated = _set_standard_maximize_conversions(client, customer_id, needs_fix)
    renamed = _rename_portfolio(
        client,
        customer_id,
        str(load_account_ids()["portfolio_strategies"]["secondary"]),
        "Maximize conversions · Grant Search shared",
    )
    removed = _remove_orphan_target_spend(client, customer_id)
    after = _inventory(ga, customer_id)

    report = {
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "customer_id": customer_id,
        "before_non_maximize_conversions": needs_fix,
        "campaigns_updated": updated,
        "portfolio_renamed": renamed,
        "orphan_target_spend_portfolio": removed,
        "after_by_bid": {
            bid: sum(1 for item in after if item["bid"] == bid)
            for bid in sorted({item["bid"] for item in after})
        },
        "after_enabled_by_bid": {
            bid: sum(1 for item in after if item["status"] == "ENABLED" and item["bid"] == bid)
            for bid in sorted({item["bid"] for item in after if item["status"] == "ENABLED"})
        },
        "after_campaigns": after,
    }
    out_dir = ROOT / "reports" / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "enforce_maximize_conversions.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Wrote: {out}")
    remaining = [item for item in after if item["bid"] != "MAXIMIZE_CONVERSIONS"]
    return 0 if not remaining else 2


if __name__ == "__main__":
    raise SystemExit(main())
