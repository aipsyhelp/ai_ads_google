#!/usr/bin/env python3
"""Remove Kyiv, Lviv and Odesa exclusions from enabled Ukraine Search.

Those cities were excluded so dedicated city campaigns would not overlap.
The city campaigns are paused, so the exclusions are now a coverage hole
on the national homepage campaign. Article campaigns already cover Ukraine.

Dry-run by default; pass --apply for CONTROLLED_LIVE.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

EXCLUDED_CITY_IDS = frozenset({1012852, 1012859, 1012861})


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
    assert_write_service("CampaignCriterionService", scope="grant_delivery_recovery")

    settings = GoogleAdsSettings.from_env()
    client = GoogleAdsClient.load_from_dict(settings.client_config())
    ga = client.get_service("GoogleAdsService")
    cid = settings.customer_id

    planned = []
    ops = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.name, campaign_criterion.resource_name,
               campaign_criterion.negative,
               campaign_criterion.location.geo_target_constant
        FROM campaign_criterion
        WHERE campaign.status = 'ENABLED'
          AND campaign_criterion.negative = TRUE
          AND campaign_criterion.type = 'LOCATION'
        """,
    ):
        geo = row.campaign_criterion.location.geo_target_constant or ""
        geo_id = int(geo.rsplit("/", 1)[-1]) if geo else 0
        if geo_id not in EXCLUDED_CITY_IDS:
            continue
        planned.append(
            {
                "campaign": row.campaign.name,
                "geo_id": geo_id,
                "resource_name": row.campaign_criterion.resource_name,
            }
        )
        op = client.get_type("CampaignCriterionOperation")
        op.remove = row.campaign_criterion.resource_name
        ops.append(op)

    result = {
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "customer_id": cid,
        "mode": "APPLY" if apply else "DRY_RUN",
        "removed_exclusions": planned,
        "reason": "City campaigns are paused; national UA Search must include Kyiv, Lviv and Odesa.",
    }

    if ops:
        svc = client.get_service("CampaignCriterionService")
        validation = client.get_type("MutateCampaignCriteriaRequest")
        validation.customer_id = cid
        validation.operations.extend(ops)
        validation.validate_only = True
        svc.mutate_campaign_criteria(request=validation)
        result["validation"] = "PASSED"
        if apply:
            request = client.get_type("MutateCampaignCriteriaRequest")
            request.customer_id = cid
            request.operations.extend(ops)
            response = svc.mutate_campaign_criteria(request=request)
            result["applied"] = [r.resource_name for r in response.results]
    else:
        result["validation"] = "nothing_to_change"

    out_dir = ROOT / "reports" / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "reopen_ua_cities.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Wrote: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
