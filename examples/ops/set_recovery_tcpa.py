#!/usr/bin/env python3
"""Set evidence-based recovery tCPA on both Grant Search portfolios.

This account historically clears auctions at $6–12 CPC. With no target after a
long outage, Maximize Conversions remains rank-starved (90% rank-lost IS).
The recovery tCPA is the canonical auction-entry control from
GrantDeliveryRecovery (currently $2000 after $500 left rank-lost IS at 90%).
It is not a performance target and must be reviewed after delivery returns.

Dry-run by default; pass --apply for CONTROLLED_LIVE.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from google.protobuf import field_mask_pb2

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ads_ai_agents.intelligence.grant_delivery_recovery import (  # noqa: E402
    ALIGNED_PORTFOLIO_STRATEGY_IDS as PORTFOLIO_IDS,
    WARM_RECOVERY_TARGET_CPA_MICROS as RECOVERY_TCPA_MICROS,
)


def _load_env() -> None:
    for raw in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    apply = "--apply" in sys.argv
    _load_env()

    from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
    from ads_ai_agents.adapters.settings import GoogleAdsSettings
    from ads_ai_agents.adapters.write_guard import assert_write_service
    from google.ads.googleads.client import GoogleAdsClient

    if apply:
        assert_mutations_allowed()
    assert_write_service("BiddingStrategyService", scope="grant_delivery_recovery")

    settings = GoogleAdsSettings.from_env()
    client = GoogleAdsClient.load_from_dict(settings.client_config())
    ga = client.get_service("GoogleAdsService")
    cid = settings.customer_id
    svc = client.get_service("BiddingStrategyService")

    before = []
    ops = []
    for strategy_id in PORTFOLIO_IDS:
        rows = list(
            ga.search(
                customer_id=cid,
                query=f"""
                SELECT bidding_strategy.resource_name, bidding_strategy.name,
                       bidding_strategy.status, bidding_strategy.campaign_count,
                       bidding_strategy.maximize_conversions.target_cpa_micros
                FROM bidding_strategy
                WHERE bidding_strategy.id = {strategy_id}
                """,
            )
        )
        if not rows:
            continue
        b = rows[0].bidding_strategy
        current = int(b.maximize_conversions.target_cpa_micros or 0)
        before.append(
            {
                "id": strategy_id,
                "name": b.name,
                "campaigns": int(b.campaign_count or 0),
                "from_tcpa_usd": current / 1e6,
                "to_tcpa_usd": RECOVERY_TCPA_MICROS / 1e6,
            }
        )
        if current == RECOVERY_TCPA_MICROS:
            continue
        op = client.get_type("BiddingStrategyOperation")
        op.update.resource_name = b.resource_name
        op.update.maximize_conversions.target_cpa_micros = RECOVERY_TCPA_MICROS
        op.update_mask.CopyFrom(
            field_mask_pb2.FieldMask(
                paths=["maximize_conversions.target_cpa_micros"]
            )
        )
        ops.append(op)

    result = {
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "customer_id": cid,
        "mode": "APPLY" if apply else "DRY_RUN",
        "strategies": before,
        "reason": "90% rank-lost IS; historical clearing CPC $6–12",
        "rollback": "set portfolio tCPA back to 0",
    }

    if ops:
        validation = client.get_type("MutateBiddingStrategiesRequest")
        validation.customer_id = cid
        validation.operations.extend(ops)
        validation.validate_only = True
        svc.mutate_bidding_strategies(request=validation)
        result["validation"] = "PASSED"

        if apply:
            request = client.get_type("MutateBiddingStrategiesRequest")
            request.customer_id = cid
            request.operations.extend(ops)
            response = svc.mutate_bidding_strategies(request=request)
            result["applied"] = [r.resource_name for r in response.results]
    else:
        result["validation"] = "nothing_to_change"

    out_dir = ROOT / "reports" / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "set_recovery_tcpa.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Wrote: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
