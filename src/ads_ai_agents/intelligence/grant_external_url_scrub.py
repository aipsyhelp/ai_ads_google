"""Remove non-approved domains (apple.com, Play, YouTube, …) from Grant ads.

Unlinks sitelinks / customer assets; pauses App campaigns with store finals;
rewrites RSA finals to example.com when needed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.adapters.write_guard import assert_write_service
from ads_ai_agents.intelligence.grant_compliance import grant_public_urls, load_facts
from ads_ai_agents.intelligence.grant_domain_gate import (
    assert_grant_url,
    host_of,
    is_grant_allowed_url,
)
from ads_ai_agents.logging_utils import configure_logging

logger = configure_logging()


@dataclass
class ScrubResult:
    dry_run: bool
    non_owned_found: list[dict[str, Any]] = field(default_factory=list)
    sitelinks_unlinked: int = 0
    customer_assets_unlinked: int = 0
    campaigns_paused: list[str] = field(default_factory=list)
    finals_rewritten: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    after_remaining: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _Scoped:
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


class GrantExternalUrlScrubber:
    def __init__(
        self,
        settings: GoogleAdsSettings | None = None,
        *,
        third_party_only: bool = False,
    ) -> None:
        self.settings = settings or GoogleAdsSettings.from_env()
        self.facts = load_facts()
        self.safe = grant_public_urls(self.facts)
        # Fail closed by default: every destination must pass the approved-domain
        # allowlist. A third-party-only scan must be requested explicitly.
        self.third_party_only = third_party_only
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from google.ads.googleads.client import GoogleAdsClient

            raw = GoogleAdsClient.load_from_dict(self.settings.client_config())
            self._client = _Scoped(raw, scope="grant_reactivate")
        return self._client

    def inventory(self) -> list[dict[str, Any]]:
        ga = self.client.get_service("GoogleAdsService")
        cid = self.settings.customer_id
        out: list[dict[str, Any]] = []

        for r in ga.search(
            customer_id=cid,
            query="""
            SELECT campaign.name, campaign.status, ad_group_ad.ad.id,
                   ad_group_ad.ad.resource_name, ad_group_ad.ad.final_urls,
                   ad_group_ad.status
            FROM ad_group_ad
            WHERE campaign.status != 'REMOVED' AND ad_group_ad.status != 'REMOVED'
            """,
        ):
            for u in list(r.ad_group_ad.ad.final_urls or []):
                if not is_grant_allowed_url(
                    u, facts=self.facts, third_party_only=self.third_party_only
                ):
                    out.append(
                        {
                            "kind": "ad_final",
                            "campaign": r.campaign.name,
                            "url": u,
                            "host": host_of(u),
                            "ad_rn": r.ad_group_ad.ad.resource_name,
                            "campaign_status": int(r.campaign.status),
                        }
                    )

        for r in ga.search(
            customer_id=cid,
            query="""
            SELECT campaign.name, campaign.status, campaign.id,
                   campaign_asset.resource_name, asset.final_urls,
                   asset.sitelink_asset.link_text, campaign_asset.status
            FROM campaign_asset
            WHERE campaign.status != 'REMOVED' AND campaign_asset.status != 'REMOVED'
              AND campaign_asset.field_type = SITELINK
            """,
        ):
            for u in list(r.asset.final_urls or []):
                if not is_grant_allowed_url(
                    u, facts=self.facts, third_party_only=self.third_party_only
                ):
                    out.append(
                        {
                            "kind": "campaign_sitelink",
                            "campaign": r.campaign.name,
                            "url": u,
                            "host": host_of(u),
                            "link_text": r.asset.sitelink_asset.link_text,
                            "ca_rn": r.campaign_asset.resource_name,
                        }
                    )

        for r in ga.search(
            customer_id=cid,
            query="""
            SELECT asset_group.name, asset_group.resource_name, asset_group.final_urls,
                   campaign.name, campaign.status
            FROM asset_group
            WHERE campaign.status != 'REMOVED' AND asset_group.status != 'REMOVED'
            """,
        ):
            for u in list(r.asset_group.final_urls or []):
                if not is_grant_allowed_url(
                    u, facts=self.facts, third_party_only=self.third_party_only
                ):
                    out.append(
                        {
                            "kind": "pmax_final",
                            "campaign": r.campaign.name,
                            "url": u,
                            "host": host_of(u),
                            "ag_rn": r.asset_group.resource_name,
                        }
                    )

        try:
            for r in ga.search(
                customer_id=cid,
                query="""
                SELECT customer_asset.resource_name, asset.final_urls,
                       asset.sitelink_asset.link_text, customer_asset.status
                FROM customer_asset
                WHERE customer_asset.status != 'REMOVED'
                """,
            ):
                for u in list(r.asset.final_urls or []):
                    if not is_grant_allowed_url(
                        u, facts=self.facts, third_party_only=self.third_party_only
                    ):
                        out.append(
                            {
                                "kind": "customer_asset",
                                "campaign": "ACCOUNT",
                                "url": u,
                                "host": host_of(u),
                                "ca_rn": r.customer_asset.resource_name,
                                "link_text": getattr(r.asset.sitelink_asset, "link_text", ""),
                            }
                        )
        except Exception as exc:  # noqa: BLE001
            logger.warning("customer_asset inventory: %s", exc)

        return out

    def apply(self, *, dry_run: bool = True) -> ScrubResult:
        from google.protobuf import field_mask_pb2

        found = self.inventory()
        result = ScrubResult(dry_run=dry_run, non_owned_found=found)
        if not dry_run:
            assert_mutations_allowed()

        home = self.safe.get("home") or "https://www.example.com/"
        assert_grant_url(home, facts=self.facts)
        ca_svc = None if dry_run else self.client.get_service("CampaignAssetService")
        cust_svc = None if dry_run else self.client.get_service("CustomerAssetService")
        camp_svc = None if dry_run else self.client.get_service("CampaignService")
        ad_svc = None if dry_run else self.client.get_service("AdService")
        ag_svc = None if dry_run else self.client.get_service("AssetGroupService")

        # Unlink sitelinks + customer assets
        seen_ca: set[str] = set()
        for row in found:
            if row["kind"] == "campaign_sitelink":
                rn = row.get("ca_rn")
                if not rn or rn in seen_ca:
                    continue
                seen_ca.add(rn)
                result.sitelinks_unlinked += 1
                if dry_run:
                    continue
                try:
                    op = self.client.get_type("CampaignAssetOperation")
                    op.remove = rn
                    ca_svc.mutate_campaign_assets(
                        customer_id=self.settings.customer_id, operations=[op]
                    )
                except Exception as exc:  # noqa: BLE001
                    result.errors.append({"op": "unlink_sitelink", "rn": rn, "error": str(exc)[:200]})

            if row["kind"] == "customer_asset":
                rn = row.get("ca_rn")
                if not rn or rn in seen_ca:
                    continue
                seen_ca.add(rn)
                result.customer_assets_unlinked += 1
                if dry_run:
                    continue
                try:
                    # CustomerAssetService may need to be in write scope
                    assert_write_service("CustomerAssetService", scope="grant_reactivate")
                    op = self.client.get_type("CustomerAssetOperation")
                    op.remove = rn
                    cust_svc.mutate_customer_assets(
                        customer_id=self.settings.customer_id, operations=[op]
                    )
                except Exception as exc:  # noqa: BLE001
                    result.errors.append({"op": "unlink_customer", "rn": rn, "error": str(exc)[:200]})

        # Pause App verticals still pointing at stores
        paused_names: set[str] = set()
        for row in found:
            if row["kind"] != "ad_final":
                continue
            name = row.get("campaign") or ""
            if "/ App /" not in name and "Agents" not in name:
                # still rewrite finals below; pause only App campaigns
                pass
            if "/ App /" in name and name not in paused_names:
                paused_names.add(name)
                result.campaigns_paused.append(name)
                if dry_run:
                    continue
                try:
                    # lookup id
                    ga = self.client.get_service("GoogleAdsService")
                    safe = name.replace("\\", "\\\\").replace("'", "\\'")
                    rows = list(
                        ga.search(
                            customer_id=self.settings.customer_id,
                            query=(
                                "SELECT campaign.id, campaign.name FROM campaign "
                                f"WHERE campaign.name = '{safe}'"
                            ),
                        )
                    )
                    if not rows:
                        continue
                    op = self.client.get_type("CampaignOperation")
                    op.update.resource_name = (
                        f"customers/{self.settings.customer_id}/campaigns/{rows[0].campaign.id}"
                    )
                    op.update.status = self.client.enums.CampaignStatusEnum.PAUSED
                    op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["status"]))
                    camp_svc.mutate_campaigns(
                        customer_id=self.settings.customer_id, operations=[op]
                    )
                except Exception as exc:  # noqa: BLE001
                    result.errors.append({"op": "pause_app", "name": name, "error": str(exc)[:200]})

        # Rewrite ad finals
        seen_ads: set[str] = set()
        for row in found:
            if row["kind"] != "ad_final":
                continue
            rn = row.get("ad_rn")
            if not rn or rn in seen_ads:
                continue
            seen_ads.add(rn)
            result.finals_rewritten += 1
            if dry_run:
                continue
            try:
                op = self.client.get_type("AdOperation")
                op.update.resource_name = rn
                op.update.final_urls.append(home)
                op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["final_urls"]))
                ad_svc.mutate_ads(customer_id=self.settings.customer_id, operations=[op])
            except Exception as exc:  # noqa: BLE001
                result.errors.append({"op": "rewrite_ad", "rn": rn, "error": str(exc)[:200]})

        # PMax finals
        seen_ag: set[str] = set()
        for row in found:
            if row["kind"] != "pmax_final":
                continue
            rn = row.get("ag_rn")
            if not rn or rn in seen_ag:
                continue
            seen_ag.add(rn)
            result.finals_rewritten += 1
            if dry_run:
                continue
            try:
                op = self.client.get_type("AssetGroupOperation")
                op.update.resource_name = rn
                del op.update.final_urls[:]
                op.update.final_urls.append(home)
                op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["final_urls"]))
                ag_svc.mutate_asset_groups(
                    customer_id=self.settings.customer_id, operations=[op]
                )
            except Exception as exc:  # noqa: BLE001
                result.errors.append({"op": "rewrite_pmax", "rn": rn, "error": str(exc)[:200]})

        if not dry_run:
            result.after_remaining = self.inventory()
        else:
            result.after_remaining = found
        logger.info(
            "grant_external_scrub dry=%s found=%s unlinked=%s remaining=%s",
            dry_run,
            len(found),
            result.sitelinks_unlinked,
            len(result.after_remaining),
        )
        return result
