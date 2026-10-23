from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from google.protobuf import field_mask_pb2

from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.adapters.write_guard import assert_write_service
from ads_ai_agents.logging_utils import configure_logging

logger = configure_logging()


@dataclass
class BudgetApplyResult:
    dry_run: bool
    attempted: list[dict[str, Any]] = field(default_factory=list)
    applied: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GoogleAdsBudgetWriter:
    """Wave-2 writer: campaign budget amount + pause/enable only."""

    def __init__(self, settings: GoogleAdsSettings | None = None) -> None:
        self.settings = settings or GoogleAdsSettings.from_env()
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from google.ads.googleads.client import GoogleAdsClient

            raw = GoogleAdsClient.load_from_dict(self.settings.client_config())
            self._client = _ScopedWriteClientProxy(raw, scope="budget_pacing")
        return self._client

    def list_campaign_budgets(self) -> list[dict[str, Any]]:
        service = self.client.get_service("GoogleAdsService")
        query = """
            SELECT
              campaign.id,
              campaign.name,
              campaign.status,
              campaign.advertising_channel_type,
              campaign_budget.resource_name,
              campaign_budget.amount_micros,
              campaign_budget.explicitly_shared,
              metrics.cost_micros,
              metrics.conversions
            FROM campaign
            WHERE campaign.status != 'REMOVED'
              AND segments.date DURING THIS_MONTH
        """
        rows = []
        for row in service.search(customer_id=self.settings.customer_id, query=query):
            cost = int(row.metrics.cost_micros) / 1_000_000
            conversions = float(row.metrics.conversions)
            rows.append(
                {
                    "id": str(row.campaign.id),
                    "name": row.campaign.name,
                    "status": row.campaign.status.name,
                    "channel": row.campaign.advertising_channel_type.name,
                    "budget_resource_name": row.campaign_budget.resource_name,
                    "daily_budget": int(row.campaign_budget.amount_micros) / 1_000_000,
                    "shared": bool(row.campaign_budget.explicitly_shared),
                    "cost": round(cost, 2),
                    "conversions": round(conversions, 2),
                    "cpa": round(cost / conversions, 2) if conversions else None,
                }
            )
        return rows

    def apply_plan(self, plan: dict[str, Any], *, dry_run: bool = True) -> BudgetApplyResult:
        result = BudgetApplyResult(dry_run=dry_run)
        customer_id = self.settings.customer_id
        if not dry_run:
            assert_mutations_allowed()

        for change in plan.get("changes") or []:
            action = change.get("action")
            name = change.get("campaign_name")
            campaign_id = str(change.get("campaign_id") or "")
            budget_rn = str(change.get("budget_resource_name") or "")

            if action == "leave":
                result.skipped.append({**change, "reason": "leave"})
                continue

            result.attempted.append(change)

            if dry_run:
                continue

            try:
                if action == "pause":
                    self._set_campaign_status(customer_id, campaign_id, paused=True)
                elif action == "set_budget":
                    amount = float(change.get("proposed_daily_budget") or 0)
                    if amount <= 0:
                        raise RuntimeError("proposed_daily_budget must be > 0 for set_budget")
                    if not budget_rn:
                        raise RuntimeError("missing budget_resource_name")
                    if change.get("proposed_status") == "ENABLED" and change.get("current_status") == "PAUSED":
                        self._set_campaign_status(customer_id, campaign_id, paused=False)
                    self._set_budget_amount(customer_id, budget_rn, amount)
                else:
                    raise RuntimeError(f"unknown action {action}")
                result.applied.append(change)
                logger.info("Applied budget change: %s %s", action, name)
            except Exception as exc:  # noqa: BLE001
                logger.error("Budget change failed for %s: %s", name, exc)
                result.errors.append({"campaign": name, "action": action, "error": str(exc)})

        return result

    def _set_budget_amount(self, customer_id: str, budget_resource_name: str, daily_usd: float) -> None:
        service = self.client.get_service("CampaignBudgetService")
        operation = self.client.get_type("CampaignBudgetOperation")
        budget = operation.update
        budget.resource_name = budget_resource_name
        budget.amount_micros = int(round(daily_usd * 1_000_000))
        operation.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["amount_micros"]))
        service.mutate_campaign_budgets(customer_id=customer_id, operations=[operation])

    def _set_campaign_status(self, customer_id: str, campaign_id: str, *, paused: bool) -> None:
        service = self.client.get_service("CampaignService")
        operation = self.client.get_type("CampaignOperation")
        campaign = operation.update
        campaign.resource_name = service.campaign_path(customer_id, campaign_id)
        campaign.status = self.client.enums.CampaignStatusEnum.PAUSED if paused else self.client.enums.CampaignStatusEnum.ENABLED
        operation.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["status"]))
        service.mutate_campaigns(customer_id=customer_id, operations=[operation])


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
