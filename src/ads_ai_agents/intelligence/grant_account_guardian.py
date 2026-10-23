"""Fail-closed structural guardian for the YOUR_BRAND Ad Grants account.

Google recommendation auto-apply bypasses our NGO policy gate. The guardian
therefore pauses every auto-apply subscription and pauses enabled ad groups
that cannot serve because they have no enabled positive keyword.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any

from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.adapters.write_guard import assert_write_service
from ads_ai_agents.intelligence.brand_gate import brand_gate_text
from ads_ai_agents.intelligence.grant_keyword_normalizer import DIRECT_SERVICE_GROUPS


@dataclass
class GrantGuardianResult:
    dry_run: bool
    auto_apply_enabled: list[dict[str, str]] = field(default_factory=list)
    unknown_auto_apply_enabled: int = 0
    auto_apply_paused: int = 0
    empty_ad_groups: list[dict[str, Any]] = field(default_factory=list)
    direct_service_groups: list[dict[str, Any]] = field(default_factory=list)
    ad_groups_paused: int = 0
    broad_keywords: list[str] = field(default_factory=list)
    broad_keywords_paused: int = 0
    asset_policy_failures: list[dict[str, Any]] = field(default_factory=list)
    campaign_assets_unlinked: int = 0
    rsa_policy_failures: int = 0
    rsa_strength: dict[str, int] = field(default_factory=dict)
    serving_keywords: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)
    after_auto_apply_enabled: int = 0
    after_empty_ad_groups: int = 0
    after_direct_service_groups: int = 0
    after_broad_keywords: int = 0
    after_asset_policy_failures: int = 0
    after_rsa_policy_failures: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _ScopedWriteClientProxy:
    def __init__(self, client: Any) -> None:
        self._client = client

    def get_service(self, name: str):
        assert_write_service(name, scope="grant_guardian")
        return self._client.get_service(name)

    def get_type(self, name: str):
        return self._client.get_type(name)

    @property
    def enums(self):
        return self._client.enums

    def __getattr__(self, item: str):
        return getattr(self._client, item)


def _enum_name(enum_cls: Any, value: Any) -> str:
    try:
        return enum_cls(value).name
    except Exception:  # noqa: BLE001
        return str(int(value))


class GrantAccountGuardian:
    def __init__(self, settings: GoogleAdsSettings | None = None) -> None:
        self.settings = settings or GoogleAdsSettings.from_env()
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from google.ads.googleads.client import GoogleAdsClient

            raw = GoogleAdsClient.load_from_dict(self.settings.client_config())
            self._client = _ScopedWriteClientProxy(raw)
        return self._client

    def inventory(self) -> dict[str, Any]:
        ga = self.client.get_service("GoogleAdsService")
        cid = self.settings.customer_id

        auto_apply: list[dict[str, str]] = []
        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT recommendation_subscription.resource_name,
                   recommendation_subscription.type,
                   recommendation_subscription.status
            FROM recommendation_subscription
            WHERE recommendation_subscription.status = 'ENABLED'
            """,
        ):
            sub = row.recommendation_subscription
            auto_apply.append(
                {
                    "resource_name": sub.resource_name,
                    "type": _enum_name(self.client.enums.RecommendationTypeEnum, sub.type),
                }
            )

        groups: dict[int, dict[str, Any]] = {}
        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT campaign.id, campaign.name, campaign.status,
                   ad_group.id, ad_group.name, ad_group.status
            FROM ad_group
            WHERE campaign.status = 'ENABLED'
              AND campaign.advertising_channel_type = 'SEARCH'
              AND ad_group.status = 'ENABLED'
            """,
        ):
            groups[row.ad_group.id] = {
                "id": row.ad_group.id,
                "resource_name": f"customers/{cid}/adGroups/{row.ad_group.id}",
                "campaign": row.campaign.name,
                "name": row.ad_group.name,
                "enabled_keywords": 0,
            }

        serving_keywords = 0
        broad_keywords: list[str] = []
        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT campaign.status, ad_group.status, ad_group.id,
                   ad_group_criterion.status, ad_group_criterion.negative
                   , ad_group_criterion.resource_name
                   , ad_group_criterion.keyword.match_type
            FROM keyword_view
            WHERE campaign.status = 'ENABLED'
              AND ad_group.status = 'ENABLED'
              AND ad_group_criterion.status = 'ENABLED'
              AND ad_group_criterion.negative = FALSE
              AND ad_group_criterion.type = 'KEYWORD'
            """,
        ):
            if row.ad_group.id in groups:
                groups[row.ad_group.id]["enabled_keywords"] += 1
                serving_keywords += 1
                if (
                    self.client.enums.KeywordMatchTypeEnum(
                        row.ad_group_criterion.keyword.match_type
                    ).name
                    == "BROAD"
                ):
                    broad_keywords.append(row.ad_group_criterion.resource_name)

        empty = [group for group in groups.values() if group["enabled_keywords"] == 0]
        direct_groups = [
            group
            for group in groups.values()
            if group["name"].strip().lower() in DIRECT_SERVICE_GROUPS
        ]

        asset_failures: list[dict[str, Any]] = []
        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT campaign.name, campaign.status,
                   campaign_asset.resource_name, campaign_asset.status,
                   campaign_asset.field_type, asset.id,
                   asset.sitelink_asset.link_text,
                   asset.sitelink_asset.description1,
                   asset.sitelink_asset.description2,
                   asset.callout_asset.callout_text,
                   asset.structured_snippet_asset.values
            FROM campaign_asset
            WHERE campaign.status = 'ENABLED'
              AND campaign_asset.status != 'REMOVED'
            """,
        ):
            texts = [
                row.asset.sitelink_asset.link_text,
                row.asset.sitelink_asset.description1,
                row.asset.sitelink_asset.description2,
                row.asset.callout_asset.callout_text,
                *list(row.asset.structured_snippet_asset.values),
            ]
            failed = [
                {"text": text, "hits": brand_gate_text(text)["hits"]}
                for text in texts
                if text and not brand_gate_text(text)["pass"]
            ]
            if failed:
                asset_failures.append(
                    {
                        "resource_name": row.campaign_asset.resource_name,
                        "campaign": row.campaign.name,
                        "asset_id": row.asset.id,
                        "field_type": _enum_name(
                            self.client.enums.AssetFieldTypeEnum,
                            row.campaign_asset.field_type,
                        ),
                        "failed": failed,
                    }
                )

        strengths: Counter[str] = Counter()
        rsa_policy_failures = 0
        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT campaign.status, ad_group.status, ad_group_ad.status,
                   ad_group_ad.ad.type, ad_group_ad.ad_strength,
                   ad_group_ad.ad.responsive_search_ad.headlines,
                   ad_group_ad.ad.responsive_search_ad.descriptions
            FROM ad_group_ad
            WHERE campaign.status = 'ENABLED'
              AND ad_group.status = 'ENABLED'
              AND ad_group_ad.status = 'ENABLED'
              AND ad_group_ad.ad.type = 'RESPONSIVE_SEARCH_AD'
            """,
        ):
            strengths[
                _enum_name(self.client.enums.AdStrengthEnum, row.ad_group_ad.ad_strength)
            ] += 1
            texts = [
                item.text
                for item in (
                    list(row.ad_group_ad.ad.responsive_search_ad.headlines)
                    + list(row.ad_group_ad.ad.responsive_search_ad.descriptions)
                )
            ]
            if any(not brand_gate_text(text)["pass"] for text in texts):
                rsa_policy_failures += 1

        return {
            "auto_apply_enabled": auto_apply,
            "empty_ad_groups": empty,
            "direct_service_groups": direct_groups,
            "broad_keywords": broad_keywords,
            "asset_policy_failures": asset_failures,
            "rsa_policy_failures": rsa_policy_failures,
            "rsa_strength": dict(strengths),
            "serving_keywords": serving_keywords,
        }

    def apply(self, *, dry_run: bool = True) -> GrantGuardianResult:
        before = self.inventory()
        result = GrantGuardianResult(
            dry_run=dry_run,
            auto_apply_enabled=before["auto_apply_enabled"],
            unknown_auto_apply_enabled=sum(
                item["type"] == "UNKNOWN" for item in before["auto_apply_enabled"]
            ),
            empty_ad_groups=before["empty_ad_groups"],
            direct_service_groups=before["direct_service_groups"],
            broad_keywords=before["broad_keywords"],
            asset_policy_failures=before["asset_policy_failures"],
            rsa_policy_failures=before["rsa_policy_failures"],
            rsa_strength=before["rsa_strength"],
            serving_keywords=before["serving_keywords"],
        )
        if dry_run:
            result.auto_apply_paused = sum(
                item["type"] != "UNKNOWN" for item in result.auto_apply_enabled
            )
            result.ad_groups_paused = len(
                {
                    item["resource_name"]
                    for item in result.empty_ad_groups + result.direct_service_groups
                }
            )
            result.broad_keywords_paused = len(result.broad_keywords)
            result.campaign_assets_unlinked = len(result.asset_policy_failures)
            result.after_auto_apply_enabled = 0
            result.after_empty_ad_groups = 0
            result.after_direct_service_groups = 0
            result.after_broad_keywords = 0
            result.after_asset_policy_failures = 0
            result.after_rsa_policy_failures = result.rsa_policy_failures
            return result

        assert_mutations_allowed()
        self._pause_auto_apply(result)
        self._pause_broad_keywords(result)
        mid = self.inventory()
        result.empty_ad_groups = mid["empty_ad_groups"]
        result.direct_service_groups = mid["direct_service_groups"]
        self._pause_empty_groups(result)
        self._unlink_failed_assets(result)

        after = self.inventory()
        result.after_auto_apply_enabled = sum(
            item["type"] != "UNKNOWN" for item in after["auto_apply_enabled"]
        )
        result.after_empty_ad_groups = len(after["empty_ad_groups"])
        result.after_direct_service_groups = len(after["direct_service_groups"])
        result.after_broad_keywords = len(after["broad_keywords"])
        result.after_asset_policy_failures = len(after["asset_policy_failures"])
        result.after_rsa_policy_failures = after["rsa_policy_failures"]
        return result

    def _pause_auto_apply(self, result: GrantGuardianResult) -> None:
        from google.protobuf.field_mask_pb2 import FieldMask

        service = self.client.get_service("RecommendationSubscriptionService")
        operations = []
        for item in result.auto_apply_enabled:
            # Older client enums expose newer subscription types as UNKNOWN and
            # return a synthetic, non-mutable resource name. Keep them visible
            # in the report; known subscriptions are the enforceable gate.
            if item["type"] == "UNKNOWN":
                continue
            op = self.client.get_type("RecommendationSubscriptionOperation")
            op.update.resource_name = item["resource_name"]
            op.update.status = self.client.enums.RecommendationSubscriptionStatusEnum.PAUSED
            op.update_mask.CopyFrom(FieldMask(paths=["status"]))
            operations.append(op)
        if not operations:
            return
        try:
            service.mutate_recommendation_subscription(
                customer_id=self.settings.customer_id,
                operations=operations,
            )
            result.auto_apply_paused = len(operations)
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"op": "pause_auto_apply", "error": str(exc)[:300]})

    def _pause_empty_groups(self, result: GrantGuardianResult) -> None:
        from google.protobuf.field_mask_pb2 import FieldMask

        service = self.client.get_service("AdGroupService")
        operations = []
        targets = {
            item["resource_name"]: item
            for item in result.empty_ad_groups + result.direct_service_groups
        }
        for item in targets.values():
            op = self.client.get_type("AdGroupOperation")
            op.update.resource_name = item["resource_name"]
            op.update.status = self.client.enums.AdGroupStatusEnum.PAUSED
            op.update_mask.CopyFrom(FieldMask(paths=["status"]))
            operations.append(op)
        if not operations:
            return
        try:
            service.mutate_ad_groups(
                customer_id=self.settings.customer_id,
                operations=operations,
            )
            result.ad_groups_paused = len(operations)
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"op": "pause_empty_ad_groups", "error": str(exc)[:300]})

    def _pause_broad_keywords(self, result: GrantGuardianResult) -> None:
        from google.protobuf.field_mask_pb2 import FieldMask

        operations = []
        for resource_name in result.broad_keywords:
            op = self.client.get_type("AdGroupCriterionOperation")
            op.update.resource_name = resource_name
            op.update.status = self.client.enums.AdGroupCriterionStatusEnum.PAUSED
            op.update_mask.CopyFrom(FieldMask(paths=["status"]))
            operations.append(op)
        if not operations:
            return
        try:
            self.client.get_service("AdGroupCriterionService").mutate_ad_group_criteria(
                customer_id=self.settings.customer_id,
                operations=operations,
            )
            result.broad_keywords_paused = len(operations)
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"op": "pause_broad_keywords", "error": str(exc)[:300]})

    def _unlink_failed_assets(self, result: GrantGuardianResult) -> None:
        service = self.client.get_service("CampaignAssetService")
        operations = []
        for item in result.asset_policy_failures:
            op = self.client.get_type("CampaignAssetOperation")
            op.remove = item["resource_name"]
            operations.append(op)
        if not operations:
            return
        try:
            service.mutate_campaign_assets(
                customer_id=self.settings.customer_id,
                operations=operations,
            )
            result.campaign_assets_unlinked = len(operations)
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"op": "unlink_failed_assets", "error": str(exc)[:300]})
