#!/usr/bin/env python3
"""Restore Grant Search delivery: remove $2000 tCPA from Max Conversions
portfolios and pause the two ENABLED PMax campaigns that are stuck in LEARNING.

Evidence (2026-10-06):
  * Search is ELIGIBLE/SERVING but lost_IS_rank=0.90 and 0 impressions.
  * Both Search portfolios carry tCPA $2000 with 0 conversions — cold-start
    Max Conversions then bids near-zero (same trap as Aug/Sep).
  * Two PMax campaigns are ENABLED + LEARNING + tCPA $2000, $120/day unique
    budget, 0 delivery. Ad Grants recovery stays on Search; PMax was paused
    on 2026-09-23 for the same reason.

Keeps unique enabled budget well under the $329 Grant cap.
Dry-run by default. --apply to mutate.
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

# Shared Maximize Conversions portfolios (secondary/EN and primary/UA groups),
# read from config/account_ids.json → portfolio_strategies.
PORTFOLIO_IDS = tuple(
    int(load_account_ids()["portfolio_strategies"][key]) for key in ("secondary", "primary")
)
NO_TARGET_CPA_MICROS = 0


def _load_env() -> None:
    for raw in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    from ads_ai_agents.intelligence.recovery_playbook import REFUSE_CLEAR_TCPA, refuse

    return refuse("clear_tcpa_pause_pmax.py", REFUSE_CLEAR_TCPA)


def _legacy_main_disabled() -> int:
    apply = "--apply" in sys.argv
    _load_env()
    from ads_ai_agents.adapters.settings import GoogleAdsSettings
    from ads_ai_agents.adapters.write_guard import assert_write_service
    from google.ads.googleads.client import GoogleAdsClient

    if apply:
        from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed

        assert_mutations_allowed()
    assert_write_service("BiddingStrategyService", scope="grant_delivery_recovery")
    assert_write_service("CampaignService", scope="grant_delivery_recovery")

    settings = GoogleAdsSettings.from_env()
    client = GoogleAdsClient.load_from_dict(settings.client_config())
    ga = client.get_service("GoogleAdsService")
    cid = settings.customer_id

    portfolios: list[dict[str, Any]] = []
    for pid in PORTFOLIO_IDS:
        rows = list(
            ga.search(
                customer_id=cid,
                query=f"""
                SELECT bidding_strategy.resource_name, bidding_strategy.name,
                       bidding_strategy.maximize_conversions.target_cpa_micros,
                       bidding_strategy.campaign_count
                FROM bidding_strategy
                WHERE bidding_strategy.id = {pid}
                """,
            )
        )
        if not rows:
            continue
        b = rows[0].bidding_strategy
        portfolios.append(
            {
                "id": pid,
                "resource_name": b.resource_name,
                "name": b.name,
                "tcpa": (b.maximize_conversions.target_cpa_micros or 0) / 1e6,
                "campaigns": int(b.campaign_count or 0),
            }
        )

    pmax: list[dict[str, Any]] = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.resource_name, campaign.name, campaign.status,
               campaign.primary_status,
               campaign.maximize_conversions.target_cpa_micros,
               campaign_budget.amount_micros
        FROM campaign
        WHERE campaign.status = 'ENABLED'
          AND campaign.advertising_channel_type = 'PERFORMANCE_MAX'
        """,
    ):
        pmax.append(
            {
                "resource_name": row.campaign.resource_name,
                "name": row.campaign.name,
                "primary": row.campaign.primary_status.name,
                "tcpa": (row.campaign.maximize_conversions.target_cpa_micros or 0) / 1e6,
                "budget": row.campaign_budget.amount_micros / 1e6,
            }
        )

    result: dict[str, Any] = {
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "customer_id": cid,
        "mode": "APPLY" if apply else "DRY_RUN",
        "portfolios": portfolios,
        "pmax_to_pause": pmax,
    }

    bsvc = client.get_service("BiddingStrategyService")
    csvc = client.get_service("CampaignService")

    # 1) Clear portfolio tCPA
    bops = []
    for p in portfolios:
        if p["tcpa"] == 0:
            continue
        op = client.get_type("BiddingStrategyOperation")
        op.update.resource_name = p["resource_name"]
        op.update.maximize_conversions.target_cpa_micros = NO_TARGET_CPA_MICROS
        op.update_mask.CopyFrom(
            field_mask_pb2.FieldMask(paths=["maximize_conversions.target_cpa_micros"])
        )
        bops.append(op)

    if bops:
        v = client.get_type("MutateBiddingStrategiesRequest")
        v.customer_id = cid
        v.operations.extend(bops)
        v.validate_only = True
        bsvc.mutate_bidding_strategies(request=v)
        result["portfolio_validation"] = "PASSED"
        if apply:
            a = client.get_type("MutateBiddingStrategiesRequest")
            a.customer_id = cid
            a.operations.extend(bops)
            bsvc.mutate_bidding_strategies(request=a)
            result["portfolios_cleared"] = [p["name"] for p in portfolios if p["tcpa"] > 0]
    else:
        result["portfolio_validation"] = "nothing_to_change"

    # 2) Pause ENABLED PMax
    cops = []
    for item in pmax:
        op = client.get_type("CampaignOperation")
        op.update.resource_name = item["resource_name"]
        op.update.status = client.enums.CampaignStatusEnum.PAUSED
        op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["status"]))
        cops.append(op)

    if cops:
        v = client.get_type("MutateCampaignsRequest")
        v.customer_id = cid
        v.operations.extend(cops)
        v.validate_only = True
        csvc.mutate_campaigns(request=v)
        result["pmax_validation"] = "PASSED"
        if apply:
            a = client.get_type("MutateCampaignsRequest")
            a.customer_id = cid
            a.operations.extend(cops)
            a.partial_failure = True
            resp = csvc.mutate_campaigns(request=a)
            result["pmax_paused"] = [r.resource_name for r in resp.results]
            if resp.partial_failure_error.code:
                result["pmax_partial_failure"] = resp.partial_failure_error.message
    else:
        result["pmax_validation"] = "nothing_to_change"

    out_dir = ROOT / "reports" / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "clear_tcpa_pause_pmax.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Wrote: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
