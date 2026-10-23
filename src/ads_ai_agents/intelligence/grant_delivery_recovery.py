"""Evidence-led delivery recovery for the YOUR_BRAND Ad Grants account.

The recovery changes only two coupled controls:
1. keep the whole set of meaningful intent actions primary, so bidding has signal;
2. pick the bidding strategy that can actually clear this account's auctions.

Ad Grants caps CPC at $2 for every strategy except Smart Bidding. This account's
auctions clear at $6-12, so Maximize Clicks cannot win an impression here: after
it was applied on 2026-08-05 delivery fell from ~700 impressions/day to zero,
with 90% of impressions lost to bid rank. Removing the explicit ceiling by hand
on 2026-08-13 and 2026-08-14 changed nothing, which confirms the cap is enforced
by policy rather than by the ceiling field. Maximize Clicks is therefore only
selected when measured auction prices actually fit under the cap.

Budgets, targeting, keywords, negatives and creatives are deliberately excluded
so delivery can be attributed to one coherent recovery intervention.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

from google.protobuf import field_mask_pb2

from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.adapters.write_guard import assert_write_service
from ads_ai_agents.platform.account_ids import load_account_ids


CHAT_CONTACT_ACTIONS = (
    "www.example.com (web) chat_open",
    "www.example.com (web) chat_message_sent",
)
# Every action that expresses real intent toward the product. Narrowing this to a
# single deep action starved bidding of signal: chat_message_sent recorded zero
# conversions across 60 days while chat entry recorded 83.
MEANINGFUL_PRIMARY_ACTIONS = CHAT_CONTACT_ACTIONS + (
    "www.example.com (web) form_submit",
    "www.example.com (web) login_success",
    "www.example.com (web) click_email",
    "www.example.com (web) click_messenger",
)
MIN_QUALITY_CONVERSIONS_FOR_SMART_BIDDING = 15.0
# Ad Grants hard cap on non-Smart-Bidding strategies.
GRANT_CPC_CAP_MICROS = 2_000_000
# Recovery bidding policy (revised 2026-10-07, raised the same day).
#
# No-target Maximize Conversions did not restore auction entry after the outage.
# A $500 recovery tCPA was live on 2026-10-07 and rank-lost IS was still 90%
# on the UA/EN home campaigns. Historical CPC is $6–12. The target being too
# restrictive is a working hypothesis: the API exposes neither auction-time
# bids nor predicted conversion probabilities. $2000 is a recovery experiment,
# not an acquisition KPI or a guarantee of auction entry.
# A stalled 90-day window with zero clicks must not look like a cheap market:
# that path used to clear the target and put the account back into rank loss.
NO_TARGET_CPA_MICROS = 0
WARM_RECOVERY_TARGET_CPA_MICROS = 2_000_000_000
STALLED_MARKET_CPC_MICROS = 7_000_000
# Primary and secondary locale groups use separate shared Max Conversions
# portfolios. IDs come from config/account_ids.json ("portfolio_strategies").
ALIGNED_PORTFOLIO_STRATEGY_IDS = tuple(
    int(v)
    for k, v in sorted((load_account_ids().get("portfolio_strategies") or {}).items())
    if k in {"primary", "secondary"} and str(v).isdigit()
)


@dataclass
class GrantDeliveryRecoveryResult:
    dry_run: bool
    conversion_actions_planned: list[dict[str, Any]] = field(default_factory=list)
    conversion_actions_updated: int = 0
    conversion_goals_planned: list[dict[str, Any]] = field(default_factory=list)
    conversion_goals_updated: int = 0
    campaigns_planned: list[dict[str, Any]] = field(default_factory=list)
    campaigns_updated: int = 0
    quality_conversions_30d: float = 0.0
    market_cpc_micros: int = 0
    desired_bidding_strategy: str = ""
    recovery_target_cpa_micros: int = 0
    warnings: list[dict[str, str]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    after: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _ScopedWriteClientProxy:
    def __init__(self, client: Any) -> None:
        self._client = client

    def get_service(self, name: str):
        assert_write_service(name, scope="grant_delivery_recovery")
        return self._client.get_service(name)

    def get_type(self, name: str):
        return self._client.get_type(name)

    @property
    def enums(self):
        return self._client.enums

    def __getattr__(self, item: str):
        return getattr(self._client, item)


class GrantDeliveryRecovery:
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
        conversions: list[dict[str, Any]] = []
        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT conversion_action.resource_name, conversion_action.name,
                   conversion_action.status, conversion_action.primary_for_goal,
                   conversion_action.include_in_conversions_metric,
                   conversion_action.counting_type, conversion_action.category
            FROM conversion_action
            WHERE conversion_action.status != 'REMOVED'
            """,
        ):
            action = row.conversion_action
            if action.name not in MEANINGFUL_PRIMARY_ACTIONS:
                continue
            conversions.append(
                {
                    "resource_name": action.resource_name,
                    "name": action.name,
                    "status": action.status.name,
                    "primary_for_goal": action.primary_for_goal,
                    "included": action.include_in_conversions_metric,
                    "counting_type": action.counting_type.name,
                    "category": action.category.name,
                }
            )

        goals: list[dict[str, Any]] = []
        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT customer_conversion_goal.resource_name,
                   customer_conversion_goal.category,
                   customer_conversion_goal.origin,
                   customer_conversion_goal.biddable
            FROM customer_conversion_goal
            """,
        ):
            goal = row.customer_conversion_goal
            if goal.category.name != "CONTACT" or goal.origin.name != "WEBSITE":
                continue
            goals.append(
                {
                    "resource_name": goal.resource_name,
                    "category": goal.category.name,
                    "origin": goal.origin.name,
                    "biddable": goal.biddable,
                }
            )

        campaigns: list[dict[str, Any]] = []
        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT campaign.resource_name, campaign.id, campaign.name,
                   campaign.status, campaign.primary_status,
                   campaign.bidding_strategy_type, campaign.bidding_strategy
            FROM campaign
            WHERE campaign.status = 'ENABLED'
              AND campaign.advertising_channel_type = 'SEARCH'
            """,
        ):
            campaigns.append(
                {
                    "resource_name": row.campaign.resource_name,
                    "id": row.campaign.id,
                    "name": row.campaign.name,
                    "primary_status": row.campaign.primary_status.name,
                    "bidding_strategy_type": row.campaign.bidding_strategy_type.name,
                    "bidding_strategy": row.campaign.bidding_strategy or "",
                }
            )
        quality_conversions = 0.0
        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT segments.conversion_action_name, metrics.conversions
            FROM customer
            WHERE segments.date DURING LAST_30_DAYS
            """,
        ):
            if row.segments.conversion_action_name in MEANINGFUL_PRIMARY_ACTIONS:
                quality_conversions += float(row.metrics.conversions)

        # Auction price decides whether the $2 cap is survivable at all. Measured
        # over 90 days so a stalled month cannot report a misleadingly cheap CPC.
        cost_micros = 0
        clicks = 0
        window_end = date.today()
        window_start = window_end - timedelta(days=90)
        for row in ga.search(
            customer_id=cid,
            query=f"""
            SELECT metrics.cost_micros, metrics.clicks
            FROM customer
            WHERE segments.date BETWEEN '{window_start.isoformat()}'
                                    AND '{window_end.isoformat()}'
            """,
        ):
            cost_micros += int(row.metrics.cost_micros)
            clicks += int(row.metrics.clicks)
        market_cpc_micros = (
            int(cost_micros / clicks) if clicks else STALLED_MARKET_CPC_MICROS
        )
        return {
            "conversion_actions": conversions,
            "conversion_goals": goals,
            "campaigns": campaigns,
            "quality_conversions_30d": round(quality_conversions, 2),
            "market_cpc_micros": market_cpc_micros,
        }

    def apply(self, *, dry_run: bool = True) -> GrantDeliveryRecoveryResult:
        if not dry_run:
            assert_mutations_allowed()
        before = self.inventory()
        quality_conversions = float(before["quality_conversions_30d"])
        market_cpc_micros = int(before.get("market_cpc_micros", 0))
        # Maximize Clicks cannot clear this account's $6–12 auctions under the
        # Grant $2 CPC cap. Never switch enabled Search to TARGET_SPEND.
        desired_bidding = "MAXIMIZE_CONVERSIONS"
        market_above_cap = market_cpc_micros > GRANT_CPC_CAP_MICROS
        # Expensive auctions need a permissive recovery target even before new
        # conversion volume arrives; cheap auctions can cold-start without one.
        recovery_target_cpa_micros = (
            WARM_RECOVERY_TARGET_CPA_MICROS
            if (
                market_above_cap
                or quality_conversions >= MIN_QUALITY_CONVERSIONS_FOR_SMART_BIDDING
            )
            else NO_TARGET_CPA_MICROS
        )
        result = GrantDeliveryRecoveryResult(
            dry_run=dry_run,
            quality_conversions_30d=quality_conversions,
            market_cpc_micros=market_cpc_micros,
            desired_bidding_strategy=desired_bidding,
            recovery_target_cpa_micros=recovery_target_cpa_micros,
        )
        if recovery_target_cpa_micros == WARM_RECOVERY_TARGET_CPA_MICROS:
            result.warnings.append(
                {
                    "op": "bidding_target",
                    "warning": (
                        f"Using permissive recovery tCPA $"
                        f"{WARM_RECOVERY_TARGET_CPA_MICROS / 1_000_000:.0f}; "
                        f"market CPC is ${market_cpc_micros / 1_000_000:.2f} and "
                        f"quality conversions are {quality_conversions:.0f}/30d. "
                        "This is an auction-entry control, not the reporting KPI."
                    ),
                }
            )
        if market_above_cap:
            result.warnings.append(
                {
                    "op": "bidding_strategy",
                    "warning": (
                        f"Measured CPC ${market_cpc_micros / 1_000_000:.2f} exceeds the Ad Grants "
                        f"${GRANT_CPC_CAP_MICROS / 1_000_000:.2f} cap, so Maximize Clicks cannot win "
                        "auctions here. Smart Bidding is the only strategy that lifts the cap."
                    ),
                }
            )

        for action in before["conversion_actions"]:
            # Category is only forced for the chat actions; an app-store click or a
            # form submit is not a Contact and mislabelling it distorts reporting.
            desired_category = (
                "CONTACT" if action["name"] in CHAT_CONTACT_ACTIONS else action["category"]
            )
            if (
                action["primary_for_goal"]
                and action["included"]
                and action["category"] == desired_category
            ):
                pass
            else:
                result.conversion_actions_planned.append(
                    {
                        **action,
                        "desired_primary_for_goal": True,
                        "desired_category": desired_category,
                    }
                )
            if action["counting_type"] == "MANY_PER_CLICK":
                result.warnings.append(
                    {
                        "op": "counting_type",
                        "action": action["name"],
                        "warning": (
                            "Imported action counting_type is immutable via Ads API, so this "
                            "action can report more than one conversion per click."
                        ),
                    }
                )
        if not before["conversion_goals"] or not before["conversion_goals"][0]["biddable"]:
            result.conversion_goals_planned.append(
                before["conversion_goals"][0]
                if before["conversion_goals"]
                else {"category": "CONTACT", "origin": "WEBSITE", "biddable": False}
            )
        for campaign in before["campaigns"]:
            if campaign["bidding_strategy_type"] == desired_bidding:
                continue
            result.campaigns_planned.append(campaign)

        if dry_run:
            result.conversion_actions_updated = len(result.conversion_actions_planned)
            result.conversion_goals_updated = len(result.conversion_goals_planned)
            result.campaigns_updated = len(result.campaigns_planned)
            result.after = before
            return result

        self._update_conversion_actions(result)
        self._ensure_contact_goal(result)
        self._update_campaign_bidding(result)
        result.after = self.inventory()
        return result

    def _update_conversion_actions(self, result: GrantDeliveryRecoveryResult) -> None:
        service = self.client.get_service("ConversionActionService")
        operations = []
        for item in result.conversion_actions_planned:
            operation = self.client.get_type("ConversionActionOperation")
            operation.update.resource_name = item["resource_name"]
            operation.update.primary_for_goal = item["desired_primary_for_goal"]
            operation.update.include_in_conversions_metric = item["desired_primary_for_goal"]
            operation.update.category = getattr(
                self.client.enums.ConversionActionCategoryEnum,
                item.get("desired_category") or item["category"],
            )
            operation.update_mask.CopyFrom(
                field_mask_pb2.FieldMask(
                    paths=[
                        "primary_for_goal",
                        "include_in_conversions_metric",
                        "category",
                    ]
                )
            )
            operations.append(operation)
        if not operations:
            return
        try:
            response = service.mutate_conversion_actions(
                customer_id=self.settings.customer_id,
                operations=operations,
            )
            result.conversion_actions_updated = len(response.results)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(
                {"op": "conversion_actions", "error": str(exc)[:500]}
            )

    def _ensure_contact_goal(self, result: GrantDeliveryRecoveryResult) -> None:
        ga = self.client.get_service("GoogleAdsService")
        rows = list(
            ga.search(
                customer_id=self.settings.customer_id,
                query="""
                SELECT customer_conversion_goal.resource_name,
                       customer_conversion_goal.biddable
                FROM customer_conversion_goal
                WHERE customer_conversion_goal.category = 'CONTACT'
                  AND customer_conversion_goal.origin = 'WEBSITE'
                """,
            )
        )
        if not rows:
            result.errors.append(
                {
                    "op": "contact_goal",
                    "error": "CONTACT~WEBSITE goal was not created after category update",
                }
            )
            return
        goal = rows[0].customer_conversion_goal
        if goal.biddable:
            return
        try:
            service = self.client.get_service("CustomerConversionGoalService")
            operation = self.client.get_type("CustomerConversionGoalOperation")
            operation.update.resource_name = goal.resource_name
            operation.update.biddable = True
            operation.update_mask.CopyFrom(
                field_mask_pb2.FieldMask(paths=["biddable"])
            )
            response = service.mutate_customer_conversion_goals(
                customer_id=self.settings.customer_id,
                operations=[operation],
            )
            result.conversion_goals_updated = len(response.results)
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"op": "contact_goal", "error": str(exc)[:500]})

    def _update_campaign_bidding(self, result: GrantDeliveryRecoveryResult) -> None:
        # Four main Search campaigns share a $257 budget aligned to a portfolio
        # Max Conversions strategy. Campaign-level tCPA mutates fail with
        # BIDDING_STRATEGY_AND_BUDGET_MUST_BE_ALIGNED — update the portfolio.
        if result.desired_bidding_strategy == "MAXIMIZE_CONVERSIONS":
            try:
                self._set_portfolio_recovery_tcpa(result)
            except Exception as exc:  # noqa: BLE001
                result.errors.append(
                    {"op": "portfolio_bidding", "error": str(exc)[:500]}
                )

        service = self.client.get_service("CampaignService")
        operations = []
        for item in result.campaigns_planned:
            # Portfolio-attached campaigns inherit tCPA from the shared strategy.
            if item.get("bidding_strategy"):
                continue
            operation = self.client.get_type("CampaignOperation")
            operation.update.resource_name = item["resource_name"]
            operation.update.maximize_conversions.target_cpa_micros = (
                result.recovery_target_cpa_micros
            )
            paths = ["maximize_conversions.target_cpa_micros"]
            operation.update_mask.CopyFrom(
                field_mask_pb2.FieldMask(
                    paths=paths
                )
            )
            operations.append(operation)
        if not operations:
            return
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
            result.campaigns_updated = len(response.results)
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"op": "campaign_bidding", "error": str(exc)[:500]})

    def _set_portfolio_recovery_tcpa(self, result: GrantDeliveryRecoveryResult) -> None:
        ga = self.client.get_service("GoogleAdsService")
        desired = int(result.recovery_target_cpa_micros)
        service = self.client.get_service("BiddingStrategyService")
        operations = []
        previous_targets = []
        for strategy_id in ALIGNED_PORTFOLIO_STRATEGY_IDS:
            rows = list(
                ga.search(
                    customer_id=self.settings.customer_id,
                    query=f"""
                    SELECT bidding_strategy.resource_name,
                           bidding_strategy.maximize_conversions.target_cpa_micros
                    FROM bidding_strategy
                    WHERE bidding_strategy.id = {strategy_id}
                    """,
                )
            )
            if not rows:
                result.warnings.append(
                    {
                        "op": "portfolio_bidding",
                        "warning": f"portfolio {strategy_id} not found",
                    }
                )
                continue
            strategy = rows[0].bidding_strategy
            current = int(strategy.maximize_conversions.target_cpa_micros or 0)
            if current == desired:
                continue
            operation = self.client.get_type("BiddingStrategyOperation")
            operation.update.resource_name = strategy.resource_name
            operation.update.maximize_conversions.target_cpa_micros = desired
            operation.update_mask.CopyFrom(
                field_mask_pb2.FieldMask(
                    paths=["maximize_conversions.target_cpa_micros"]
                )
            )
            operations.append(operation)
            previous_targets.append((strategy_id, current))
        if not operations:
            return
        service.mutate_bidding_strategies(
            customer_id=self.settings.customer_id,
            operations=operations,
        )
        result.campaigns_updated += len(operations)
        label = (
            "removed (no target, cold-start)"
            if desired == 0
            else f"set to ${desired / 1e6:.0f}"
        )
        for strategy_id, current in previous_targets:
            result.warnings.append(
                {
                    "op": "portfolio_bidding",
                    "warning": (
                        f"portfolio {strategy_id} tCPA {label} "
                        f"(was ${current / 1e6:.0f})"
                    ),
                }
            )
