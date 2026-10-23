#!/usr/bin/env python3
"""Pause enabled Search ad groups that have no enabled positive keywords.

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
    assert_write_service("AdGroupService", scope="grant_delivery_recovery")

    settings = GoogleAdsSettings.from_env()
    client = GoogleAdsClient.load_from_dict(settings.client_config())
    ga = client.get_service("GoogleAdsService")
    cid = settings.customer_id

    groups: dict[str, dict[str, object]] = {}
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.name, ad_group.resource_name, ad_group.name
        FROM ad_group
        WHERE campaign.status = 'ENABLED'
          AND campaign.advertising_channel_type = 'SEARCH'
          AND ad_group.status = 'ENABLED'
        """,
    ):
        groups[row.ad_group.resource_name] = {
            "campaign": row.campaign.name,
            "ad_group": row.ad_group.name,
            "enabled_keywords": 0,
        }

    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT ad_group.resource_name, ad_group_criterion.status
        FROM keyword_view
        WHERE campaign.status = 'ENABLED'
          AND ad_group.status = 'ENABLED'
          AND ad_group_criterion.status = 'ENABLED'
          AND ad_group_criterion.negative = FALSE
        """,
    ):
        item = groups.get(row.ad_group.resource_name)
        if item is not None:
            item["enabled_keywords"] = int(item["enabled_keywords"]) + 1

    hollow = [
        {"resource_name": rn, **item}
        for rn, item in groups.items()
        if int(item["enabled_keywords"]) == 0
    ]
    result = {
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "customer_id": cid,
        "mode": "APPLY" if apply else "DRY_RUN",
        "hollow_ad_groups": hollow,
    }

    ops = []
    for item in hollow:
        op = client.get_type("AdGroupOperation")
        op.update.resource_name = item["resource_name"]
        op.update.status = client.enums.AdGroupStatusEnum.PAUSED
        op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["status"]))
        ops.append(op)

    if ops:
        svc = client.get_service("AdGroupService")
        validation = client.get_type("MutateAdGroupsRequest")
        validation.customer_id = cid
        validation.operations.extend(ops)
        validation.validate_only = True
        svc.mutate_ad_groups(request=validation)
        result["validation"] = "PASSED"
        if apply:
            request = client.get_type("MutateAdGroupsRequest")
            request.customer_id = cid
            request.operations.extend(ops)
            response = svc.mutate_ad_groups(request=request)
            result["paused"] = [r.resource_name for r in response.results]
    else:
        result["validation"] = "nothing_to_change"

    out_dir = ROOT / "reports" / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "pause_hollow_ad_groups.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Wrote: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
