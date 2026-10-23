"""Fix disapproved / auth-walled sitelink destinations (Google Ads optimization).

Unlinks sitelinks whose final URL requires login (diary/tests/chat/faq/analytics),
then links a public crawlable pack (chat root, LP, news, corporate, ngo, stores).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.adapters.write_guard import assert_write_service
from ads_ai_agents.intelligence.ads_public_urls import is_blocked_destination, public_urls
from ads_ai_agents.intelligence.brand_gate import brand_gate_pack
from ads_ai_agents.intelligence.grant_domain_gate import assert_grant_url
from ads_ai_agents.intelligence.enterprise_asset_coverage import (
    TARGET_SITELINKS,
    _build_sitelink_specs,
    _locale_from_campaign,
)
from ads_ai_agents.logging_utils import configure_logging
from ads_ai_agents.platform.product_facts import load_product_facts

logger = configure_logging()

# Labels that historically pointed at auth walls — always rotate even if URL already fixed.
LEGACY_BAD_LABELS = {
    "щоденник",
    "diary",
    "tagebuch",
    "тести",
    "tests",
    "аналітика",
    "analytics",
    "faq",
}


@dataclass
class SitelinkFixResult:
    dry_run: bool
    scanned: int = 0
    bad_found: int = 0
    unlinked: int = 0
    created: int = 0
    linked: int = 0
    campaigns_touched: int = 0
    gate: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    by_url: dict[str, int] = field(default_factory=dict)
    after_sample: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _ScopedWriteClientProxy:
    def __init__(self, client: Any, *, scope: str) -> None:
        self._client = client
        self._scope = scope

    def get_service(self, name: str):
        assert_write_service(name, scope=self._scope)
        return self._client.get_service(name)

    def get_type(self, name: str):
        return self._client.get_type(name)

    @property
    def enums(self):
        return self._client.enums

    def __getattr__(self, item: str):
        return getattr(self._client, item)


class SitelinkUrlFixer:
    def __init__(self, settings: GoogleAdsSettings | None = None) -> None:
        self.settings = settings or GoogleAdsSettings.from_env()
        self.facts = load_product_facts()
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from google.ads.googleads.client import GoogleAdsClient

            raw = GoogleAdsClient.load_from_dict(self.settings.client_config())
            self._client = _ScopedWriteClientProxy(raw, scope="asset_publish")
        return self._client

    def inventory_bad(self) -> list[dict[str, Any]]:
        ga = self.client.get_service("GoogleAdsService")
        cid = self.settings.customer_id
        out: list[dict[str, Any]] = []
        for r in ga.search(
            customer_id=cid,
            query="""
            SELECT campaign.id, campaign.name, campaign.status,
                   campaign_asset.resource_name, campaign_asset.status,
                   asset.id, asset.sitelink_asset.link_text, asset.final_urls,
                   asset.policy_summary.approval_status
            FROM campaign_asset
            WHERE campaign.status = 'ENABLED'
              AND campaign_asset.field_type = SITELINK
              AND campaign_asset.status != 'REMOVED'
            """,
        ):
            urls = list(r.asset.final_urls or [])
            url = urls[0] if urls else ""
            label = (r.asset.sitelink_asset.link_text or "").strip()
            try:
                appr = self.client.enums.PolicyApprovalStatusEnum.PolicyApprovalStatus.Name(
                    r.asset.policy_summary.approval_status
                )
            except Exception:  # noqa: BLE001
                appr = str(r.asset.policy_summary.approval_status)
            bad = (
                is_blocked_destination(url)
                or label.lower() in LEGACY_BAD_LABELS
                or appr == "DISAPPROVED"
            )
            if not bad:
                continue
            out.append(
                {
                    "campaign_id": r.campaign.id,
                    "campaign": r.campaign.name,
                    "ca_rn": r.campaign_asset.resource_name,
                    "asset_id": r.asset.id,
                    "link_text": label,
                    "final_url": url,
                    "approval": appr,
                    "locale": _locale_from_campaign(r.campaign.name),
                }
            )
        return out

    def apply(self, *, dry_run: bool = True) -> SitelinkFixResult:
        bad = self.inventory_bad()
        by_url: dict[str, int] = {}
        for b in bad:
            by_url[b["final_url"] or "(empty)"] = by_url.get(b["final_url"] or "(empty)", 0) + 1

        # Gate the replacement pack copy
        all_texts: list[str] = []
        for loc in ("UA", "EN", "DE"):
            for s in _build_sitelink_specs(self.facts, loc):
                all_texts.extend([s["link_text"], s["description1"], s["description2"]])
        gate = brand_gate_pack(all_texts)
        result = SitelinkFixResult(
            dry_run=dry_run,
            scanned=len(bad),
            bad_found=len(bad),
            by_url=by_url,
            gate=gate,
        )
        if not gate.get("pass"):
            result.errors.append({"error": "brand_gate_failed", "failed": gate.get("failed")})
            return result
        if not dry_run:
            assert_mutations_allowed()

        ca_svc = None if dry_run else self.client.get_service("CampaignAssetService")
        asset_svc = None if dry_run else self.client.get_service("AssetService")

        # 1) Unlink bad sitelinks
        camps_touched: set[int] = set()
        for row in bad:
            camps_touched.add(row["campaign_id"])
            if dry_run:
                result.unlinked += 1
                continue
            try:
                op = self.client.get_type("CampaignAssetOperation")
                op.remove = row["ca_rn"]
                ca_svc.mutate_campaign_assets(
                    customer_id=self.settings.customer_id, operations=[op]
                )
                result.unlinked += 1
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                if "removed" in msg or "not found" in msg:
                    result.unlinked += 1
                else:
                    result.errors.append(
                        {"op": "unlink", "link": row["link_text"], "error": str(exc)[:200]}
                    )

        result.campaigns_touched = len(camps_touched)

        # 2) Link public pack into every ENABLED campaign (fill to target)
        camps = []
        ga = self.client.get_service("GoogleAdsService")
        for r in ga.search(
            customer_id=self.settings.customer_id,
            query="""
            SELECT campaign.id, campaign.name, campaign.status
            FROM campaign WHERE campaign.status = 'ENABLED'
            """,
        ):
            camps.append({"id": r.campaign.id, "name": r.campaign.name})

        # Count current sitelinks after unlink
        counts: dict[int, int] = {c["id"]: 0 for c in camps}
        existing_labels: dict[int, set[str]] = {c["id"]: set() for c in camps}
        for r in ga.search(
            customer_id=self.settings.customer_id,
            query="""
            SELECT campaign.id, campaign.status, asset.sitelink_asset.link_text,
                   asset.final_urls, campaign_asset.status
            FROM campaign_asset
            WHERE campaign.status = 'ENABLED'
              AND campaign_asset.field_type = SITELINK
              AND campaign_asset.status != 'REMOVED'
            """,
        ):
            counts[r.campaign.id] = counts.get(r.campaign.id, 0) + 1
            existing_labels.setdefault(r.campaign.id, set()).add(
                (r.asset.sitelink_asset.link_text or "").strip().lower()
            )

        cache: dict[tuple[str, str], str] = {}
        for camp in camps:
            locale = _locale_from_campaign(camp["name"])
            camp_rn = f"customers/{self.settings.customer_id}/campaigns/{camp['id']}"
            need = max(0, TARGET_SITELINKS - counts.get(camp["id"], 0))
            labels = existing_labels.get(camp["id"], set())
            for spec in _build_sitelink_specs(self.facts, locale):
                if need <= 0:
                    break
                if is_blocked_destination(spec["final_url"]):
                    continue
                if spec["link_text"].lower() in labels:
                    continue
                key = (spec["link_text"].lower(), spec["final_url"])
                rn = cache.get(key)
                if not rn:
                    if dry_run:
                        rn = f"dry_run/{spec['link_text']}"
                        cache[key] = rn
                        result.created += 1
                    else:
                        try:
                            op = self.client.get_type("AssetOperation")
                            asset = op.create
                            asset.name = f"SL · {spec['link_text']}"
                            asset.sitelink_asset.link_text = spec["link_text"]
                            asset.sitelink_asset.description1 = spec["description1"]
                            asset.sitelink_asset.description2 = spec["description2"]
                            assert_grant_url(spec["final_url"], facts=self.facts)
                            asset.final_urls.append(spec["final_url"])
                            resp = asset_svc.mutate_assets(
                                customer_id=self.settings.customer_id, operations=[op]
                            )
                            rn = resp.results[0].resource_name
                            cache[key] = rn
                            result.created += 1
                        except Exception as exc:  # noqa: BLE001
                            result.errors.append(
                                {
                                    "op": "create",
                                    "link": spec["link_text"],
                                    "error": str(exc)[:200],
                                }
                            )
                            continue
                try:
                    if dry_run:
                        result.linked += 1
                    else:
                        op = self.client.get_type("CampaignAssetOperation")
                        op.create.campaign = camp_rn
                        op.create.asset = rn
                        op.create.field_type = self.client.enums.AssetFieldTypeEnum.SITELINK
                        ca_svc.mutate_campaign_assets(
                            customer_id=self.settings.customer_id, operations=[op]
                        )
                        result.linked += 1
                    labels.add(spec["link_text"].lower())
                    need -= 1
                    counts[camp["id"]] = counts.get(camp["id"], 0) + 1
                except Exception as exc:  # noqa: BLE001
                    msg = str(exc).lower()
                    if "already" in msg or "duplicate" in msg:
                        labels.add(spec["link_text"].lower())
                        need -= 1
                    else:
                        result.errors.append(
                            {
                                "op": "link",
                                "campaign": camp["name"][:40],
                                "link": spec["link_text"],
                                "error": str(exc)[:200],
                            }
                        )

        # After sample for report
        if not dry_run:
            sample_urls: dict[str, int] = {}
            for r in ga.search(
                customer_id=self.settings.customer_id,
                query="""
                SELECT campaign.status, asset.final_urls, campaign_asset.status
                FROM campaign_asset
                WHERE campaign.status = 'ENABLED'
                  AND campaign_asset.field_type = SITELINK
                  AND campaign_asset.status != 'REMOVED'
                """,
            ):
                u = (list(r.asset.final_urls or []) or [""])[0]
                sample_urls[u] = sample_urls.get(u, 0) + 1
            result.after_sample = [
                {"url": u, "count": n, "blocked": is_blocked_destination(u)}
                for u, n in sorted(sample_urls.items(), key=lambda kv: -kv[1])
            ]
        else:
            # Preview intended public URLs
            result.after_sample = [
                {"locale": loc, **public_urls(self.facts, locale=loc)} for loc in ("UA", "EN", "DE")
            ]
        return result
