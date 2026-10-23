"""Replace legacy health-sensitive campaign sitelinks with policy-safe copy.

Keeps ad-group auto sitelinks (Google AI) untouched — only swaps ADVERTISER
campaign-level links 1:1 so sitelink count and URLs stay the same.
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

# Old link_text → new link_text; locale-specific overrides win over "ALL".
LEGACY_LINK_TEXTS_ALL: dict[str, str] = {
    # UA
    "Лендінг": "Сервіс",
    "Перевірка": "Швидкий reset",
    "Вигорання": "Підтримка змін",
    "Після зміни": "Підтримка змін",
    "Стрес на роботі": "Після дня",
    "Після роботи": "Після дня",
    "Старт сьогодні": "Почати зараз",
    "Місія": "Про ГО",
    "Допомога": "Огляд сервісу",
    "Як це працює": "Огляд сервісу",
    # EN
    "Landing": "Service",
    "Website": "Public site",
    "Quick check-in": "Quick reset",
    "Burnout care": "Shift support",
    "Work stress": "After work",
    "Start today": "Start now",
    "Mission": "About NGO",
    "Get help": "How it works",
    # DE
    "Schnell-Check": "Kurzer Reset",
    "Burnout": "Nach der Schicht",
    "Arbeitsstress": "Nach der Arbeit",
    "Heute starten": "Jetzt starten",
    "Hilfe": "So geht's",
}

LEGACY_LINK_TEXTS_BY_LOCALE: dict[str, dict[str, str]] = {
    "UA": {"Wind-down": "Вечірній режим"},
    "EN": {"Wind-down": "Evening mode"},
    "DE": {"Wind-down": "Abendmodus"},
}


def _replacement_text(locale: str, old_text: str) -> str | None:
    by_loc = LEGACY_LINK_TEXTS_BY_LOCALE.get(locale, {})
    if old_text in by_loc:
        return by_loc[old_text]
    return LEGACY_LINK_TEXTS_ALL.get(old_text)


def _load_env() -> None:
    for raw in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _locale_from_campaign(name: str) -> str:
    n = name or ""
    if " / DE " in n or "/ DE/" in n or n.endswith("/ DE"):
        return "DE"
    if "/ EN" in n or " EN /" in n or "/EN" in n:
        return "EN"
    return "UA"


def _spec_by_link_text(specs: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {s["link_text"]: s for s in specs}


def _find_or_create_sitelink(
    client: Any,
    customer_id: str,
    spec: dict[str, str],
    library: dict[tuple[str, str], str],
    *,
    dry_run: bool,
) -> str | None:
    key = (spec["link_text"].lower(), spec["final_url"])
    if key in library:
        return library[key]
    if dry_run:
        return f"dry_run/sitelink/{spec['link_text']}"
    from ads_ai_agents.intelligence.grant_domain_gate import assert_grant_url
    from ads_ai_agents.platform.product_facts import load_product_facts

    assert_grant_url(spec["final_url"], facts=load_product_facts())
    asset_svc = client.get_service("AssetService")
    op = client.get_type("AssetOperation")
    asset = op.create
    asset.name = f"SL policy-safe · {spec['link_text']}"
    asset.sitelink_asset.link_text = spec["link_text"]
    asset.sitelink_asset.description1 = spec["description1"]
    asset.sitelink_asset.description2 = spec["description2"]
    asset.final_urls.append(spec["final_url"])
    resp = asset_svc.mutate_assets(customer_id=customer_id, operations=[op])
    rn = resp.results[0].resource_name
    library[key] = rn
    return rn


def _library_sitelinks(client: Any, customer_id: str) -> dict[tuple[str, str], str]:
    ga = client.get_service("GoogleAdsService")
    out: dict[tuple[str, str], str] = {}
    for row in ga.search(
        customer_id=customer_id,
        query="""
        SELECT asset.resource_name, asset.sitelink_asset.link_text, asset.final_urls
        FROM asset WHERE asset.type = 'SITELINK'
        """,
    ):
        text = (row.asset.sitelink_asset.link_text or "").strip().lower()
        urls = list(row.asset.final_urls or [])
        url = (urls[0] if urls else "").strip()
        if text and url and (text, url) not in out:
            out[(text, url)] = row.asset.resource_name
    return out


def _legacy_campaign_links(ga: Any, customer_id: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for row in ga.search(
        customer_id=customer_id,
        query="""
        SELECT campaign.resource_name, campaign.name, campaign.status,
               campaign_asset.resource_name, campaign_asset.source,
               campaign_asset.primary_status,
               asset.sitelink_asset.link_text
        FROM campaign_asset
        WHERE campaign.status = 'ENABLED'
          AND campaign_asset.status = 'ENABLED'
          AND campaign_asset.field_type = 'SITELINK'
          AND campaign_asset.source = 'ADVERTISER'
        """,
    ):
        link_text = (row.asset.sitelink_asset.link_text or "").strip()
        locale = _locale_from_campaign(row.campaign.name)
        new_text = _replacement_text(locale, link_text)
        if not new_text:
            continue
        links.append(
            {
                "campaign_asset_rn": row.campaign_asset.resource_name,
                "campaign_rn": row.campaign.resource_name,
                "campaign": row.campaign.name,
                "old_link_text": link_text,
                "new_link_text": new_text,
                "locale": locale,
                "primary_status": row.campaign_asset.primary_status.name,
            }
        )
    return links


def _replace_links(
    client: Any,
    customer_id: str,
    operations: list[Any],
    *,
    dry_run: bool,
) -> int:
    if dry_run or not operations:
        return len(operations)
    service = client.get_service("CampaignAssetService")
    request = client.get_type("MutateCampaignAssetsRequest")
    request.customer_id = customer_id
    request.operations.extend(operations)
    request.partial_failure = True
    response = service.mutate_campaign_assets(request=request)
    if response.partial_failure_error.code:
        raise RuntimeError(response.partial_failure_error.message)
    return sum(1 for result in response.results if result.resource_name)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Swap legacy campaign sitelinks for policy-safe copy.")
    parser.add_argument("--apply", action="store_true", help="Execute mutations (default: dry-run).")
    args = parser.parse_args()
    dry_run = not args.apply

    _load_env()
    from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
    from ads_ai_agents.adapters.settings import GoogleAdsSettings
    from ads_ai_agents.adapters.write_guard import assert_write_service
    from ads_ai_agents.intelligence.enterprise_asset_coverage import _build_sitelink_specs
    from ads_ai_agents.platform.product_facts import load_product_facts
    from google.ads.googleads.client import GoogleAdsClient

    if not dry_run:
        assert_mutations_allowed()
        assert_write_service("CampaignAssetService", scope="asset_publish")
        assert_write_service("AssetService", scope="asset_publish")

    settings = GoogleAdsSettings.from_env()
    client = GoogleAdsClient.load_from_dict(settings.client_config())
    ga = client.get_service("GoogleAdsService")
    customer_id = settings.customer_id
    facts = load_product_facts()

    specs_by_locale = {
        loc: _spec_by_link_text(_build_sitelink_specs(facts, loc)) for loc in ("UA", "EN", "DE")
    }
    library = _library_sitelinks(client, customer_id) if not dry_run else {}
    links = _legacy_campaign_links(ga, customer_id)

    replaced = 0
    created_assets = 0
    skipped: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    batch: list[Any] = []

    for item in links:
        locale = item["locale"]
        new_text = item["new_link_text"]
        spec = specs_by_locale.get(locale, {}).get(new_text)
        if not spec:
            spec = specs_by_locale.get("EN", {}).get(new_text)
        if not spec:
            skipped.append({**item, "reason": "missing_new_spec"})
            continue
        if item["old_link_text"] == spec["link_text"]:
            skipped.append({**item, "reason": "already_updated"})
            continue
        try:
            asset_rn = _find_or_create_sitelink(
                client, customer_id, spec, library, dry_run=dry_run
            )
            if not asset_rn:
                skipped.append({**item, "reason": "asset_create_failed"})
                continue
            if not dry_run and asset_rn.startswith("customers/"):
                created_assets += 1
            remove_op = client.get_type("CampaignAssetOperation")
            remove_op.remove = item["campaign_asset_rn"]
            batch.append(remove_op)
            create_op = client.get_type("CampaignAssetOperation")
            create_op.create.campaign = item["campaign_rn"]
            create_op.create.asset = asset_rn
            create_op.create.field_type = client.enums.AssetFieldTypeEnum.SITELINK
            batch.append(create_op)
            replaced += 1
        except Exception as exc:  # noqa: BLE001
            errors.append({"campaign": item["campaign"], "error": str(exc)[:240]})

    if batch:
        try:
            _replace_links(client, customer_id, batch, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            errors.append({"error": "mutate_batch", "detail": str(exc)[:240]})

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "legacy_links_found": len(links),
        "replaced": replaced,
        "assets_created_or_reused": created_assets,
        "skipped": skipped,
        "errors": errors,
        "note": "Ad-group Google AI sitelinks were not modified.",
        "samples": links[:12],
    }
    out_dir = ROOT / "reports" / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "remediate_limited_sitelinks.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    mode = "APPLY" if not dry_run else "DRY-RUN"
    print(f"Limited sitelink remediation [{mode}] found={len(links)} replaced={replaced} errors={len(errors)}")
    print(f"Wrote: {out_path}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
