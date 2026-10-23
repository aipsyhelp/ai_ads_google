"""Remove disapproved campaign asset links and restore a legal business name.

The Google Ads Policy page aggregates enabled links from enabled and paused
campaigns. Assets can remain globally present; removing the link is enough to
clear them from serving and from the actionable policy queue.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

LEGAL_BUSINESS_NAME = "YOUR_LEGAL_NAME"  # your registered organisation name


def _load_env() -> None:
    for raw in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _not_eligible_links(ga: Any, customer_id: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for row in ga.search(
        customer_id=customer_id,
        query="""
        SELECT campaign.resource_name, campaign.name, campaign.status,
               campaign_asset.resource_name,
               campaign_asset.status, campaign_asset.field_type,
               campaign_asset.primary_status, campaign_asset.primary_status_reasons,
               asset.id, asset.name, asset.text_asset.text
        FROM campaign_asset
        WHERE campaign_asset.status != 'REMOVED'
          AND campaign_asset.primary_status = 'NOT_ELIGIBLE'
        """,
    ):
        reasons = {reason.name for reason in row.campaign_asset.primary_status_reasons}
        if "ASSET_DISAPPROVED" not in reasons:
            continue
        links.append(
            {
                "resource_name": row.campaign_asset.resource_name,
                "campaign_resource_name": row.campaign.resource_name,
                "campaign": row.campaign.name,
                "campaign_status": row.campaign.status.name,
                "field_type": row.campaign_asset.field_type.name,
                "asset_id": str(row.asset.id),
                "asset_name": row.asset.text_asset.text or row.asset.name,
            }
        )
    return links


def _replace_links(
    client: Any,
    customer_id: str,
    links: list[dict[str, str]],
    legal_asset: str,
) -> tuple[int, int]:
    if not links:
        return 0, 0
    service = client.get_service("CampaignAssetService")
    operations = []
    for item in links:
        operation = client.get_type("CampaignAssetOperation")
        operation.remove = item["resource_name"]
        operations.append(operation)
    business_campaigns = {
        item["campaign_resource_name"]
        for item in links
        if item["field_type"] == "BUSINESS_NAME"
    }
    for campaign_resource_name in business_campaigns:
        operation = client.get_type("CampaignAssetOperation")
        operation.create.campaign = campaign_resource_name
        operation.create.asset = legal_asset
        operation.create.field_type = client.enums.AssetFieldTypeEnum.BUSINESS_NAME
        operation.create.status = client.enums.AssetLinkStatusEnum.ENABLED
        operations.append(operation)

    validation = client.get_type("MutateCampaignAssetsRequest")
    validation.customer_id = customer_id
    validation.operations.extend(operations)
    validation.validate_only = True
    service.mutate_campaign_assets(request=validation)

    request = client.get_type("MutateCampaignAssetsRequest")
    request.customer_id = customer_id
    request.operations.extend(operations)
    request.partial_failure = True
    response = service.mutate_campaign_assets(request=request)
    if response.partial_failure_error.code:
        raise RuntimeError(response.partial_failure_error.message)
    succeeded = sum(1 for result in response.results if result.resource_name)
    return min(succeeded, len(links)), max(succeeded - len(links), 0)


def _create_or_find_business_name(
    client: Any,
    ga: Any,
    customer_id: str,
) -> str:
    for row in ga.search(
        customer_id=customer_id,
        query="""
        SELECT asset.resource_name, asset.name, asset.text_asset.text
        FROM asset
        WHERE asset.type = 'TEXT'
        """,
    ):
        if (
            row.asset.name == "Grant legal business name"
            and row.asset.text_asset.text == LEGAL_BUSINESS_NAME
        ):
            return row.asset.resource_name

    service = client.get_service("AssetService")
    operation = client.get_type("AssetOperation")
    operation.create.name = "Grant legal business name"
    operation.create.text_asset.text = LEGAL_BUSINESS_NAME
    response = service.mutate_assets(customer_id=customer_id, operations=[operation])
    return response.results[0].resource_name


def main() -> int:
    _load_env()
    from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
    from ads_ai_agents.adapters.settings import GoogleAdsSettings
    from ads_ai_agents.adapters.write_guard import assert_write_service
    from google.ads.googleads.client import GoogleAdsClient

    assert_mutations_allowed()
    assert_write_service("CampaignAssetService", scope="asset_publish")
    assert_write_service("AssetService", scope="asset_publish")

    settings = GoogleAdsSettings.from_env()
    client = GoogleAdsClient.load_from_dict(settings.client_config())
    ga = client.get_service("GoogleAdsService")
    customer_id = settings.customer_id

    links = _not_eligible_links(ga, customer_id)
    business_campaigns = {
        item["campaign_resource_name"]: {
            "resource_name": item["campaign_resource_name"],
            "name": item["campaign"],
        }
        for item in links
        if item["field_type"] == "BUSINESS_NAME"
    }
    legal_asset = _create_or_find_business_name(client, ga, customer_id)
    removed, linked = _replace_links(
        client,
        customer_id,
        links,
        legal_asset,
    )

    remaining = _not_eligible_links(ga, customer_id)
    report = {
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "legal_business_name": LEGAL_BUSINESS_NAME,
        "disapproved_links_before": links,
        "links_removed": removed,
        "replacement_asset": legal_asset,
        "replacement_links_created": linked,
        "business_name_campaigns": list(business_campaigns.values()),
        "disapproved_links_after": remaining,
    }
    output_dir = ROOT / "reports" / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / "policy_asset_fix.json"
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Wrote: {output}")
    return 0 if not remaining else 2


if __name__ == "__main__":
    raise SystemExit(main())
