#!/usr/bin/env python3
"""Attach the google-ads-news-images assets to ALL ENABLED Search campaigns as
image extensions (CampaignAsset field_type=AD_IMAGE), since Google's automatic
image asset creation is not working.

Only links assets that meet image-extension aspect ratios:
  * SQUARE 1:1 (required) and LANDSCAPE 1.91:1 (recommended).
Skips assets already linked to a campaign; rotates the pool so campaigns get a
DIVERSE mix. Caps total AD_IMAGE per campaign to stay within limits.

Dry-run (validate_only) by default. Pass --apply to mutate.
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

MAX_IMAGES_PER_CAMPAIGN = 20  # Google image-extension cap per campaign
TARGET_SQUARE = 12            # aim per campaign (square is the required ratio)
TARGET_LANDSCAPE = 6


def _load_env() -> None:
    for raw in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _eligible_news_assets(ga: Any, cid: str) -> tuple[list[int], list[int]]:
    square: list[tuple[int, int]] = []      # (id, width) for stable sort
    landscape: list[tuple[int, int]] = []
    for r in ga.search(customer_id=cid, query="""
        SELECT asset.id, asset.name,
               asset.image_asset.full_size.width_pixels,
               asset.image_asset.full_size.height_pixels
        FROM asset WHERE asset.type='IMAGE'
    """):
        a = r.asset
        w = a.image_asset.full_size.width_pixels
        h = a.image_asset.full_size.height_pixels
        name = str(a.name)
        if not w or not h or not name.startswith("news-"):
            continue
        ratio = w / h
        if abs(ratio - 1.0) < 0.02 and w >= 300:
            square.append((a.id, w))
        elif abs(ratio - 1.91) < 0.06 and w >= 600:
            landscape.append((a.id, w))
    square.sort(key=lambda t: t[0])
    landscape.sort(key=lambda t: t[0])
    return [i for i, _ in square], [i for i, _ in landscape]


def _enabled_campaigns(ga: Any, cid: str) -> list[tuple[str, str]]:
    out = []
    for r in ga.search(customer_id=cid, query="""
        SELECT campaign.resource_name, campaign.name
        FROM campaign WHERE campaign.status='ENABLED' ORDER BY campaign.name
    """):
        out.append((r.campaign.resource_name, r.campaign.name))
    return out


def _existing_ad_image(ga: Any, cid: str) -> dict[str, set[str]]:
    linked: dict[str, set[str]] = defaultdict(set)
    for r in ga.search(customer_id=cid, query="""
        SELECT campaign.status, campaign.resource_name, campaign_asset.asset
        FROM campaign_asset
        WHERE campaign.status='ENABLED' AND campaign_asset.field_type='AD_IMAGE'
    """):
        linked[r.campaign.resource_name].add(r.campaign_asset.asset)
    return linked


def main() -> int:
    apply = "--apply" in sys.argv
    _load_env()
    from ads_ai_agents.adapters.settings import GoogleAdsSettings
    from ads_ai_agents.adapters.write_guard import assert_write_service
    from google.ads.googleads.client import GoogleAdsClient
    if apply:
        from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
        assert_mutations_allowed()
    assert_write_service("CampaignAssetService", scope="asset_publish")

    settings = GoogleAdsSettings.from_env()
    client = GoogleAdsClient.load_from_dict(settings.client_config())
    ga = client.get_service("GoogleAdsService")
    cid = settings.customer_id

    squares, landscapes = _eligible_news_assets(ga, cid)
    campaigns = _enabled_campaigns(ga, cid)
    linked = _existing_ad_image(ga, cid)

    def rn(asset_id: int) -> str:
        return f"customers/{cid}/assets/{asset_id}"

    IMAGE_FT = client.enums.AssetFieldTypeEnum.AD_IMAGE
    service = client.get_service("CampaignAssetService")

    # Build operations grouped PER CAMPAIGN — one request per campaign, because
    # the API forbids the same asset appearing in multiple ops of one request.
    ops_by_campaign: dict[str, list] = {}
    plan: dict[str, Any] = {}
    for ci, (camp_rn, camp_name) in enumerate(campaigns):
        have = linked.get(camp_rn, set())
        room = MAX_IMAGES_PER_CAMPAIGN - len(have)
        if room <= 0:
            plan[camp_name] = {"existing": len(have), "added": 0, "note": "at cap"}
            continue
        # rotate the pools per campaign for variety
        sq_rot = squares[ci % len(squares):] + squares[:ci % len(squares)] if squares else []
        ld_rot = landscapes[ci % len(landscapes):] + landscapes[:ci % len(landscapes)] if landscapes else []
        want_sq = [a for a in sq_rot if rn(a) not in have][:TARGET_SQUARE]
        want_ld = [a for a in ld_rot if rn(a) not in have][:TARGET_LANDSCAPE]
        # Dedupe by asset id (defensive: API forbids same asset twice per request).
        chosen = list(dict.fromkeys(want_sq + want_ld))[:room]
        ops = []
        for asset_id in chosen:
            op = client.get_type("CampaignAssetOperation")
            op.create.campaign = camp_rn
            op.create.asset = rn(asset_id)
            op.create.field_type = IMAGE_FT
            ops.append(op)
        if ops:
            ops_by_campaign[camp_name] = ops
        plan[camp_name] = {"existing": len(have), "added": len(ops)}

    total_ops = sum(len(v) for v in ops_by_campaign.values())
    result: dict[str, Any] = {
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "customer_id": cid,
        "mode": "APPLY" if apply else "DRY_RUN (validate_only)",
        "pool": {"square": len(squares), "landscape": len(landscapes)},
        "total_operations": total_ops,
        "plan_per_campaign": plan,
    }

    if not total_ops:
        result["note"] = "Nothing to add."
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    val_status: dict[str, str] = {}
    applied: dict[str, int] = {}
    for camp_name, ops in ops_by_campaign.items():
        vreq = client.get_type("MutateCampaignAssetsRequest")
        vreq.customer_id = cid
        vreq.operations.extend(ops)
        vreq.validate_only = True
        vreq.partial_failure = True
        vresp = service.mutate_campaign_assets(request=vreq)
        val_status[camp_name] = (
            vresp.partial_failure_error.message[:300]
            if vresp.partial_failure_error.code else "PASSED"
        )
        # Apply with partial_failure: valid links commit, content-duplicate or
        # already-linked ops are skipped without aborting the batch.
        if apply:
            areq = client.get_type("MutateCampaignAssetsRequest")
            areq.customer_id = cid
            areq.operations.extend(ops)
            areq.partial_failure = True
            aresp = service.mutate_campaign_assets(request=areq)
            applied[camp_name] = sum(1 for r in aresp.results if r.resource_name)
    result["validation"] = val_status
    if apply:
        result["applied_per_campaign"] = applied
        result["applied_total"] = sum(applied.values())

    out_dir = ROOT / "reports" / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "attach_news_image_assets.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Wrote: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
