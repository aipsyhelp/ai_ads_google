#!/usr/bin/env python3
"""Scale enabled Search budgets to the safe $329/day Ad Grants ceiling.

Dry-run by default. Use --apply for a controlled live mutation.
The existing allocation ratio is preserved across unique shared budgets.
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

GRANT_CAP_USD = 329.0


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


def _enabled_budgets(ga: Any, customer_id: str) -> dict[str, dict[str, Any]]:
    budgets: dict[str, dict[str, Any]] = {}
    for row in ga.search(
        customer_id=customer_id,
        query="""
        SELECT campaign.name, campaign.advertising_channel_type,
               campaign_budget.resource_name, campaign_budget.amount_micros,
               campaign_budget.explicitly_shared
        FROM campaign
        WHERE campaign.status = 'ENABLED'
        """,
    ):
        resource_name = row.campaign_budget.resource_name
        item = budgets.setdefault(
            resource_name,
            {
                "amount_usd": row.campaign_budget.amount_micros / 1e6,
                "shared": bool(row.campaign_budget.explicitly_shared),
                "channels": set(),
                "campaigns": [],
            },
        )
        item["channels"].add(row.campaign.advertising_channel_type.name)
        item["campaigns"].append(row.campaign.name)
    return budgets


def _serialise(budgets: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "resource_name": resource_name,
            "budget_id": resource_name.rsplit("/", 1)[-1],
            "amount_usd": item["amount_usd"],
            "shared": item["shared"],
            "channels": sorted(item["channels"]),
            "campaigns": item["campaigns"],
        }
        for resource_name, item in budgets.items()
    ]


def main() -> int:
    apply = "--apply" in sys.argv
    _load_env()

    from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
    from ads_ai_agents.adapters.settings import GoogleAdsSettings
    from ads_ai_agents.adapters.write_guard import assert_write_service
    from google.ads.googleads.client import GoogleAdsClient

    if apply:
        assert_mutations_allowed()
    assert_write_service("CampaignBudgetService", scope="grant_delivery_recovery")

    settings = GoogleAdsSettings.from_env()
    client = GoogleAdsClient.load_from_dict(settings.client_config())
    ga = client.get_service("GoogleAdsService")
    customer_id = settings.customer_id
    before = _enabled_budgets(ga, customer_id)
    total_before = round(sum(item["amount_usd"] for item in before.values()), 2)

    unexpected = [
        resource_name
        for resource_name, item in before.items()
        if item["channels"] != {"SEARCH"}
    ]
    if unexpected:
        raise RuntimeError(
            "Refusing budget scale: enabled non-Search campaign budget detected: "
            + ", ".join(unexpected)
        )
    if not before or total_before <= 0:
        raise RuntimeError("No enabled Search budgets found")

    factor = GRANT_CAP_USD / total_before
    resource_names = sorted(before)
    targets: dict[str, float] = {}
    assigned = 0.0
    for resource_name in resource_names[:-1]:
        target = round(before[resource_name]["amount_usd"] * factor, 2)
        targets[resource_name] = target
        assigned += target
    targets[resource_names[-1]] = round(GRANT_CAP_USD - assigned, 2)

    operations = []
    for resource_name, target in targets.items():
        operation = client.get_type("CampaignBudgetOperation")
        operation.update.resource_name = resource_name
        operation.update.amount_micros = int(round(target * 1e6))
        operation.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["amount_micros"]))
        operations.append(operation)

    service = client.get_service("CampaignBudgetService")
    validation = client.get_type("MutateCampaignBudgetsRequest")
    validation.customer_id = customer_id
    validation.operations.extend(operations)
    validation.validate_only = True
    service.mutate_campaign_budgets(request=validation)

    report: dict[str, Any] = {
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "customer_id": customer_id,
        "mode": "APPLY" if apply else "DRY_RUN",
        "grant_cap_usd": GRANT_CAP_USD,
        "unique_budget_before_usd": total_before,
        "budgets_before": _serialise(before),
        "planned": [
            {
                "resource_name": resource_name,
                "from_usd": before[resource_name]["amount_usd"],
                "to_usd": target,
            }
            for resource_name, target in targets.items()
        ],
        "validation": "PASSED",
    }

    if apply:
        request = client.get_type("MutateCampaignBudgetsRequest")
        request.customer_id = customer_id
        request.operations.extend(operations)
        service.mutate_campaign_budgets(request=request)
        after = _enabled_budgets(ga, customer_id)
        total_after = round(sum(item["amount_usd"] for item in after.values()), 2)
        report["unique_budget_after_usd"] = total_after
        report["budgets_after"] = _serialise(after)
        report["post_validation"] = (
            "PASSED" if abs(total_after - GRANT_CAP_USD) < 0.01 else "FAILED"
        )

    out_dir = ROOT / "reports" / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "set_grant_budget_cap.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Wrote: {out}")
    return 0 if report.get("post_validation", "PASSED") == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
