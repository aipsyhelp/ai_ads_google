#!/usr/bin/env python3
"""Generate-ready Grant-safe image assets → upload → replace AD_IMAGE links.

Policy targets (Google Ads Personalized advertising / Creative):
  - NO text or graphic overlays
  - NO collages / multi-panel composites
  - NO dating & companionship cues (couples, romance, intimacy)
  - Prefer single continuous photo scenes (nature / abstract / empty calm interiors)

Also removes existing AD_IMAGE links on ENABLED Search campaigns so disapproved
news/screenshot assets stop polluting the Policy queue and Ad Strength.

Usage:
  PYTHONPATH=ads_ai_agents/src ADS_AUTOMATION_MODE=CONTROLLED_LIVE \\
    python ads_ai_agents/scripts/publish_grant_safe_images.py --apply \\
    --source-dir /path/to/generated/pngs
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

# Official recommended marketing sizes for Search image assets / PMax marketing.
SQUARE = (1200, 1200)       # 1:1
LANDSCAPE = (1200, 628)     # ~1.91:1
MAX_PER_CAMPAIGN = 12
DEFAULT_NAME_PREFIX = "grant-safe-example"


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


def _fit_cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Center-crop to exact aspect, then resize (no letterbox = no fake collage edges)."""
    tw, th = size
    src = img.convert("RGB")
    sw, sh = src.size
    target_ratio = tw / th
    src_ratio = sw / sh
    if src_ratio > target_ratio:
        new_w = int(round(sh * target_ratio))
        left = (sw - new_w) // 2
        src = src.crop((left, 0, left + new_w, sh))
    else:
        new_h = int(round(sw / target_ratio))
        top = (sh - new_h) // 2
        src = src.crop((0, top, sw, top + new_h))
    return src.resize(size, Image.Resampling.LANCZOS)


def _to_jpeg_bytes(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=88, optimize=True, progressive=True)
    data = buf.getvalue()
    if len(data) > 5_000_000:
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=75, optimize=True)
        data = buf.getvalue()
    return data


def prepare_variants(
    source_dir: Path, out_dir: Path, *, name_prefix: str
) -> list[dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    prepared: list[dict[str, Any]] = []
    sources = sorted(
        [p for p in source_dir.iterdir() if p.suffix.lower() in {".png", ".jpg", ".jpeg"}]
    )
    for path in sources:
        img = Image.open(path)
        w, h = img.size
        ratio = w / max(h, 1)
        # Landscape sources → landscape + optional square crop; square → square (+ landscape if wide enough)
        jobs: list[tuple[str, tuple[int, int]]] = []
        if abs(ratio - 1.0) < 0.15:
            jobs.append(("sq", SQUARE))
        if ratio >= 1.4 or abs(ratio - 1.91) < 0.25:
            jobs.append(("ls", LANDSCAPE))
        if not jobs:
            # fallback: produce both from any source
            jobs = [("sq", SQUARE), ("ls", LANDSCAPE)]
        for kind, size in jobs:
            out_name = f"{name_prefix}-{path.stem}-{kind}-{size[0]}x{size[1]}.jpg"
            out_path = out_dir / out_name
            fitted = _fit_cover(img, size)
            data = _to_jpeg_bytes(fitted)
            out_path.write_bytes(data)
            prepared.append(
                {
                    "name": out_name,
                    "path": str(out_path),
                    "kind": kind,
                    "width": size[0],
                    "height": size[1],
                    "bytes": len(data),
                    "source": path.name,
                }
            )
    return prepared


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--source-dir",
        default=str(ROOT / "var" / "grant_safe_images_source"),
        help="Folder with your source images (default: var/grant_safe_images_source).",
    )
    parser.add_argument(
        "--prepared-dir",
        default=str(ROOT / "var" / "grant_safe_images_example"),
    )
    parser.add_argument(
        "--file-prefix",
        default="grant-safe-",
        help="Only ingest files whose names start with this prefix (e.g. grant-safe-v2-).",
    )
    parser.add_argument(
        "--name-prefix",
        default=DEFAULT_NAME_PREFIX,
        help="Asset name prefix written into Google Ads.",
    )
    parser.add_argument(
        "--skip-remove",
        action="store_true",
        help="Keep existing AD_IMAGE links; only upload + attach new assets up to cap.",
    )
    args = parser.parse_args()
    apply = args.apply
    _load_env()

    source_dir = Path(args.source_dir)
    # Only our new grant-safe generations (ignore unrelated assets folder noise).
    safe_sources = [
        p
        for p in source_dir.iterdir()
        if p.name.startswith(args.file_prefix)
        and p.suffix.lower() in {".png", ".jpg", ".jpeg"}
    ]
    if not safe_sources:
        print(f"No {args.file_prefix}* images in {source_dir}")
        return 2

    # Stage only matching files into a temp staging view via prepared_dir sources list
    staging = Path(args.prepared_dir) / "_sources"
    staging.mkdir(parents=True, exist_ok=True)
    for p in safe_sources:
        dest = staging / p.name
        if not dest.exists() or dest.stat().st_mtime < p.stat().st_mtime:
            dest.write_bytes(p.read_bytes())

    prepared = prepare_variants(
        staging, Path(args.prepared_dir), name_prefix=args.name_prefix
    )
    report: dict[str, Any] = {
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "APPLY" if apply else "DRY_RUN",
        "file_prefix": args.file_prefix,
        "name_prefix": args.name_prefix,
        "skip_remove": bool(args.skip_remove),
        "prepared": prepared,
        "sources": [p.name for p in safe_sources],
    }

    from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
    from ads_ai_agents.adapters.settings import GoogleAdsSettings
    from ads_ai_agents.adapters.write_guard import assert_write_service
    from google.ads.googleads.client import GoogleAdsClient

    if apply:
        assert_mutations_allowed()
    assert_write_service("AssetService", scope="asset_publish")
    assert_write_service("CampaignAssetService", scope="asset_publish")

    settings = GoogleAdsSettings.from_env()
    client = GoogleAdsClient.load_from_dict(settings.client_config())
    ga = client.get_service("GoogleAdsService")
    asset_svc = client.get_service("AssetService")
    camp_asset_svc = client.get_service("CampaignAssetService")
    cid = settings.customer_id
    report["customer_id"] = cid

    # --- upload assets ---
    uploaded: list[dict[str, Any]] = []
    if apply:
        for item in prepared:
            data = Path(item["path"]).read_bytes()
            op = client.get_type("AssetOperation")
            op.create.name = item["name"]
            op.create.type_ = client.enums.AssetTypeEnum.IMAGE
            op.create.image_asset.data = data
            resp = asset_svc.mutate_assets(customer_id=cid, operations=[op])
            rn = resp.results[0].resource_name
            uploaded.append({**item, "resource_name": rn})
    else:
        uploaded = [{**item, "resource_name": f"(dry){item['name']}"} for item in prepared]
    report["uploaded"] = [
        {"name": u["name"], "kind": u["kind"], "resource_name": u["resource_name"], "bytes": u["bytes"]}
        for u in uploaded
    ]

    # --- collect ENABLED Search campaigns + existing AD_IMAGE links ---
    campaigns: list[tuple[str, str]] = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.resource_name, campaign.name,
               campaign.status, campaign.advertising_channel_type
        FROM campaign
        WHERE campaign.status = 'ENABLED'
          AND campaign.advertising_channel_type = 'SEARCH'
        ORDER BY campaign.name
        """,
    ):
        campaigns.append((row.campaign.resource_name, row.campaign.name))

    remove_ops = []
    linked_now: dict[str, int] = {}
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.resource_name, campaign.name, campaign.status,
               campaign.advertising_channel_type,
               campaign_asset.resource_name, campaign_asset.field_type,
               campaign_asset.status, asset.id, asset.name
        FROM campaign_asset
        WHERE campaign.status = 'ENABLED'
          AND campaign.advertising_channel_type = 'SEARCH'
          AND campaign_asset.field_type = 'AD_IMAGE'
          AND campaign_asset.status != 'REMOVED'
        """,
    ):
        linked_now[row.campaign.name] = linked_now.get(row.campaign.name, 0) + 1
        if not args.skip_remove:
            op = client.get_type("CampaignAssetOperation")
            op.remove = row.campaign_asset.resource_name
            remove_ops.append(op)
    report["ad_image_links_before"] = linked_now
    report["links_to_remove"] = len(remove_ops)

    if apply and remove_ops:
        # chunk removes to avoid oversized requests
        removed = 0
        for i in range(0, len(remove_ops), 40):
            chunk = remove_ops[i : i + 40]
            req = client.get_type("MutateCampaignAssetsRequest")
            req.customer_id = cid
            req.operations.extend(chunk)
            req.partial_failure = True
            resp = camp_asset_svc.mutate_campaign_assets(request=req)
            removed += sum(1 for r in resp.results if r.resource_name)
            if resp.partial_failure_error.code:
                report.setdefault("remove_partial_failures", []).append(
                    resp.partial_failure_error.message[:300]
                )
        report["links_removed"] = removed

    # --- attach new safe images (rotate per campaign) ---
    squares = [u for u in uploaded if u["kind"] == "sq"]
    landscapes = [u for u in uploaded if u["kind"] == "ls"]
    attach_plan: dict[str, Any] = {}
    applied_attach = 0
    IMAGE_FT = client.enums.AssetFieldTypeEnum.AD_IMAGE

    for ci, (camp_rn, camp_name) in enumerate(campaigns):
        already = 0 if not args.skip_remove else int(linked_now.get(camp_name, 0))
        room = max(0, MAX_PER_CAMPAIGN - already)
        if room <= 0:
            attach_plan[camp_name] = {
                "count": 0,
                "assets": [],
                "skipped": "at_cap",
                "already": already,
            }
            continue
        chosen: list[str] = []
        # Prefer balanced mix when adding atop existing; fill remaining slots.
        sq_budget = min(len(squares), max(1, room // 2)) if squares else 0
        ls_budget = min(len(landscapes), room - sq_budget) if landscapes else 0
        if squares and ls_budget + sq_budget < room:
            sq_budget = min(len(squares), room - ls_budget)
        if squares:
            rot = squares[ci % len(squares) :] + squares[: ci % len(squares)]
            chosen.extend([u["resource_name"] for u in rot[:sq_budget]])
        if landscapes:
            rot = landscapes[ci % len(landscapes) :] + landscapes[: ci % len(landscapes)]
            chosen.extend([u["resource_name"] for u in rot[:ls_budget]])
        chosen = list(dict.fromkeys(chosen))[:room]
        attach_plan[camp_name] = {
            "count": len(chosen),
            "assets": chosen,
            "already": already,
            "room": room,
        }
        if not apply or not chosen:
            continue
        ops = []
        for rn in chosen:
            op = client.get_type("CampaignAssetOperation")
            op.create.campaign = camp_rn
            op.create.asset = rn
            op.create.field_type = IMAGE_FT
            op.create.status = client.enums.AssetLinkStatusEnum.ENABLED
            ops.append(op)
        # validate then apply per campaign
        vreq = client.get_type("MutateCampaignAssetsRequest")
        vreq.customer_id = cid
        vreq.operations.extend(ops)
        vreq.validate_only = True
        vreq.partial_failure = True
        camp_asset_svc.mutate_campaign_assets(request=vreq)
        areq = client.get_type("MutateCampaignAssetsRequest")
        areq.customer_id = cid
        areq.operations.extend(ops)
        areq.partial_failure = True
        aresp = camp_asset_svc.mutate_campaign_assets(request=areq)
        n = sum(1 for r in aresp.results if r.resource_name)
        applied_attach += n
        attach_plan[camp_name]["applied"] = n
        if aresp.partial_failure_error.code:
            attach_plan[camp_name]["error"] = aresp.partial_failure_error.message[:240]

    report["attach_plan"] = {
        k: {kk: vv for kk, vv in v.items() if kk != "assets"}
        for k, v in attach_plan.items()
    }
    report["attach_applied_total"] = applied_attach

    out_dir = ROOT / "reports" / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "publish_grant_safe_images.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Wrote: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
