"""Bring enabled Search campaigns back inside the Ad Grants operating envelope.

Two Ad Grants account rules are enforced here because breaking either one is
grounds for deactivation:

1. every campaign needs at least two ad groups and two sitelinks;
2. total enabled daily budget must stay inside the Grant cap.

Only campaigns that violate rule 1 *and* cannot serve anyway (no enabled
keywords) are paused. A campaign that still delivers traffic is reported
instead, because pausing it would trade a policy warning for a traffic outage.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from google.protobuf import field_mask_pb2

from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.adapters.write_guard import assert_write_service


GRANT_DAILY_CAP_USD = 329.0
MIN_AD_GROUPS_PER_CAMPAIGN = 2
MIN_SITELINKS_PER_CAMPAIGN = 2


@dataclass
class GrantPolicyRemediationResult:
    dry_run: bool
    campaigns: list[dict[str, Any]] = field(default_factory=list)
    violations: list[dict[str, Any]] = field(default_factory=list)
    pause_planned: list[dict[str, Any]] = field(default_factory=list)
    campaigns_paused: int = 0
    budget_before_usd: float = 0.0
    budget_after_usd: float = 0.0
    warnings: list[dict[str, str]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _ScopedWriteClientProxy:
    def __init__(self, client: Any) -> None:
        self._client = client

    def get_service(self, name: str):
        assert_write_service(name, scope="grant_policy_remediation")
        return self._client.get_service(name)

    def get_type(self, name: str):
        return self._client.get_type(name)

    @property
    def enums(self):
        return self._client.enums

    def __getattr__(self, item: str):
        return getattr(self._client, item)


class GrantPolicyRemediation:
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

    def inventory(self) -> list[dict[str, Any]]:
        ga = self.client.get_service("GoogleAdsService")
        cid = self.settings.customer_id
        campaigns: dict[str, dict[str, Any]] = {}
        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT campaign.resource_name, campaign.id, campaign.name,
                   campaign.status, campaign.bidding_strategy_type,
                   campaign_budget.amount_micros
            FROM campaign
            WHERE campaign.status = 'ENABLED'
              AND campaign.advertising_channel_type = 'SEARCH'
            """,
        ):
            campaigns[str(row.campaign.id)] = {
                "resource_name": row.campaign.resource_name,
                "id": str(row.campaign.id),
                "name": row.campaign.name,
                "bidding_strategy_type": row.campaign.bidding_strategy_type.name,
                "budget_usd": round(row.campaign_budget.amount_micros / 1e6, 2),
                "ad_groups": 0,
                "sitelinks": 0,
                "enabled_keywords": 0,
            }

        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT campaign.id, campaign.status, ad_group.status
            FROM ad_group
            WHERE campaign.status = 'ENABLED'
              AND campaign.advertising_channel_type = 'SEARCH'
              AND ad_group.status = 'ENABLED'
            """,
        ):
            campaign = campaigns.get(str(row.campaign.id))
            if campaign is not None:
                campaign["ad_groups"] += 1

        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT campaign.id, campaign.status,
                   campaign_asset.status, campaign_asset.field_type
            FROM campaign_asset
            WHERE campaign.status = 'ENABLED'
              AND campaign_asset.status = 'ENABLED'
              AND campaign_asset.field_type = 'SITELINK'
            """,
        ):
            campaign = campaigns.get(str(row.campaign.id))
            if campaign is not None:
                campaign["sitelinks"] += 1

        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT campaign.id, campaign.status, ad_group.status,
                   ad_group_criterion.status
            FROM ad_group_criterion
            WHERE campaign.status = 'ENABLED'
              AND campaign.advertising_channel_type = 'SEARCH'
              AND ad_group.status = 'ENABLED'
              AND ad_group_criterion.status = 'ENABLED'
              AND ad_group_criterion.type = 'KEYWORD'
              AND ad_group_criterion.negative = FALSE
            """,
        ):
            campaign = campaigns.get(str(row.campaign.id))
            if campaign is not None:
                campaign["enabled_keywords"] += 1

        return list(campaigns.values())

    @staticmethod
    def plan(campaigns: list[dict[str, Any]]) -> dict[str, Any]:
        violations: list[dict[str, Any]] = []
        pause_planned: list[dict[str, Any]] = []
        warnings: list[dict[str, str]] = []

        for campaign in campaigns:
            reasons = []
            if campaign["ad_groups"] < MIN_AD_GROUPS_PER_CAMPAIGN:
                reasons.append(
                    f"{campaign['ad_groups']} ad groups (Grant minimum "
                    f"{MIN_AD_GROUPS_PER_CAMPAIGN})"
                )
            if campaign["sitelinks"] < MIN_SITELINKS_PER_CAMPAIGN:
                reasons.append(
                    f"{campaign['sitelinks']} sitelinks (Grant minimum "
                    f"{MIN_SITELINKS_PER_CAMPAIGN})"
                )
            if not reasons:
                continue
            violations.append({**campaign, "reasons": reasons})
            if campaign["enabled_keywords"] == 0:
                pause_planned.append({**campaign, "reasons": reasons})
            else:
                warnings.append(
                    {
                        "op": "structure_violation",
                        "campaign": campaign["name"],
                        "warning": (
                            "Campaign breaks Grant structure rules but still has live "
                            "keywords; fix structure instead of pausing delivery: "
                            + "; ".join(reasons)
                        ),
                    }
                )

        budget_before = round(sum(item["budget_usd"] for item in campaigns), 2)
        paused_ids = {item["id"] for item in pause_planned}
        budget_after = round(
            sum(
                item["budget_usd"]
                for item in campaigns
                if item["id"] not in paused_ids
            ),
            2,
        )
        if budget_after > GRANT_DAILY_CAP_USD:
            warnings.append(
                {
                    "op": "budget_cap",
                    "warning": (
                        f"Enabled daily budget ${budget_after} still exceeds the Grant "
                        f"cap ${GRANT_DAILY_CAP_USD}; rebalance campaign budgets"
                    ),
                }
            )
        return {
            "violations": violations,
            "pause_planned": pause_planned,
            "warnings": warnings,
            "budget_before_usd": budget_before,
            "budget_after_usd": budget_after,
        }

    def apply(self, *, dry_run: bool = True) -> GrantPolicyRemediationResult:
        campaigns = self.inventory()
        plan = self.plan(campaigns)
        result = GrantPolicyRemediationResult(
            dry_run=dry_run,
            campaigns=campaigns,
            violations=plan["violations"],
            pause_planned=plan["pause_planned"],
            budget_before_usd=plan["budget_before_usd"],
            budget_after_usd=plan["budget_after_usd"],
            warnings=list(plan["warnings"]),
        )
        if dry_run or not result.pause_planned:
            return result

        assert_mutations_allowed()
        service = self.client.get_service("CampaignService")
        operations = []
        for item in result.pause_planned:
            operation = self.client.get_type("CampaignOperation")
            operation.update.resource_name = item["resource_name"]
            operation.update.status = self.client.enums.CampaignStatusEnum.PAUSED
            operation.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["status"]))
            operations.append(operation)
        try:
            validation = self.client.get_type("MutateCampaignsRequest")
            validation.customer_id = self.settings.customer_id
            validation.operations.extend(operations)
            validation.validate_only = True
            service.mutate_campaigns(request=validation)

            request = self.client.get_type("MutateCampaignsRequest")
            request.customer_id = self.settings.customer_id
            request.operations.extend(operations)
            response = service.mutate_campaigns(request=request)
            result.campaigns_paused = len(response.results)
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"op": "pause_campaigns", "error": str(exc)[:500]})
        return result
