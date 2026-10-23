from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Callable

from ads_ai_agents.adapters.readonly_guard import assert_readonly_service
from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.core.domain import PerformanceSnapshot
from ads_ai_agents.logging_utils import configure_logging

logger = configure_logging()


@dataclass
class QueryResult:
    name: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class AccountSnapshot:
    customer_id: str
    login_customer_id: str
    descriptive_name: str
    currency_code: str
    time_zone: str
    campaigns: list[dict[str, Any]] = field(default_factory=list)
    change_events: list[dict[str, Any]] = field(default_factory=list)
    search_terms: list[dict[str, Any]] = field(default_factory=list)
    recommendations: list[dict[str, Any]] = field(default_factory=list)
    performance: PerformanceSnapshot | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GoogleAdsReadOnlyAdapter:
    """Read-only Google Ads API surface. Mutate / upload services are blocked."""

    def __init__(self, settings: GoogleAdsSettings | None = None) -> None:
        self.settings = settings or GoogleAdsSettings.from_env()
        self._client = None
        self._search_impl: Callable[[str, str], Any] | None = None

    @property
    def client(self):
        if self._client is None:
            from google.ads.googleads.client import GoogleAdsClient

            raw = GoogleAdsClient.load_from_dict(self.settings.client_config())
            self._client = _ReadOnlyClientProxy(raw)
        return self._client

    def get_service(self, name: str):
        return self.client.get_service(name)

    def ping(self) -> dict[str, str]:
        rows = list(
            self._search(
                self.settings.customer_id,
                """
                SELECT
                  customer.id,
                  customer.descriptive_name,
                  customer.currency_code,
                  customer.time_zone,
                  customer.auto_tagging_enabled,
                  customer.conversion_tracking_setting.conversion_tracking_status,
                  customer.conversion_tracking_setting.enhanced_conversions_for_leads_enabled
                FROM customer
                LIMIT 1
                """,
            )
        )
        if not rows:
            raise RuntimeError(f"No customer row for {self.settings.customer_id}")
        customer = rows[0].customer
        tracking = customer.conversion_tracking_setting
        return {
            "customer_id": str(customer.id),
            "descriptive_name": customer.descriptive_name,
            "currency_code": customer.currency_code,
            "time_zone": customer.time_zone,
            "login_customer_id": self.settings.login_customer_id,
            "auto_tagging_enabled": str(bool(customer.auto_tagging_enabled)),
            "conversion_tracking_status": tracking.conversion_tracking_status.name,
            "enhanced_conversions_for_leads_enabled": str(
                bool(tracking.enhanced_conversions_for_leads_enabled)
            ),
        }

    def run_query(self, name: str, query: str, customer_id: str | None = None) -> QueryResult:
        cid = customer_id or self.settings.customer_id
        try:
            rows = [self._row_to_dict(row) for row in self._search(cid, query)]
            return QueryResult(name=name, rows=rows)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Query %s failed: %s", name, exc)
            return QueryResult(name=name, error=str(exc))

    def fetch_audit_bundle(self, customer_id: str | None = None) -> dict[str, QueryResult]:
        cid = customer_id or self.settings.customer_id
        queries = {
            "account": """
                SELECT
                  customer.id,
                  customer.descriptive_name,
                  customer.currency_code,
                  customer.time_zone,
                  customer.auto_tagging_enabled,
                  customer.manager,
                  customer.optimization_score,
                  customer.conversion_tracking_setting.conversion_tracking_status,
                  customer.conversion_tracking_setting.enhanced_conversions_for_leads_enabled,
                  customer.conversion_tracking_setting.accepted_customer_data_terms,
                  metrics.cost_micros,
                  metrics.conversions,
                  metrics.clicks,
                  metrics.impressions
                FROM customer
                WHERE segments.date DURING THIS_MONTH
            """,
            "campaigns": """
                SELECT
                  campaign.id,
                  campaign.name,
                  campaign.status,
                  campaign.serving_status,
                  campaign.advertising_channel_type,
                  campaign.advertising_channel_sub_type,
                  campaign.bidding_strategy_type,
                  campaign.maximize_conversions.target_cpa_micros,
                  campaign.target_cpa.target_cpa_micros,
                  campaign.target_roas.target_roas,
                  campaign.final_url_suffix,
                  campaign.network_settings.target_google_search,
                  campaign.network_settings.target_search_network,
                  campaign.network_settings.target_content_network,
                  campaign.optimization_score,
                  campaign_budget.id,
                  campaign_budget.name,
                  campaign_budget.amount_micros,
                  campaign_budget.explicitly_shared,
                  metrics.cost_micros,
                  metrics.clicks,
                  metrics.impressions,
                  metrics.conversions,
                  metrics.conversions_value,
                  metrics.ctr,
                  metrics.search_impression_share,
                  metrics.search_budget_lost_impression_share,
                  metrics.search_rank_lost_impression_share
                FROM campaign
                WHERE campaign.status != 'REMOVED'
                  AND segments.date DURING THIS_MONTH
            """,
            "ad_groups": """
                SELECT
                  campaign.id,
                  campaign.name,
                  ad_group.id,
                  ad_group.name,
                  ad_group.status,
                  ad_group.type,
                  ad_group.cpc_bid_micros,
                  metrics.cost_micros,
                  metrics.clicks,
                  metrics.impressions,
                  metrics.conversions,
                  metrics.ctr
                FROM ad_group
                WHERE ad_group.status != 'REMOVED'
                  AND segments.date DURING THIS_MONTH
            """,
            "keywords": """
                SELECT
                  campaign.id,
                  campaign.name,
                  ad_group.id,
                  ad_group.name,
                  ad_group_criterion.criterion_id,
                  ad_group_criterion.keyword.text,
                  ad_group_criterion.keyword.match_type,
                  ad_group_criterion.status,
                  ad_group_criterion.quality_info.quality_score,
                  ad_group_criterion.quality_info.creative_quality_score,
                  ad_group_criterion.quality_info.post_click_quality_score,
                  ad_group_criterion.quality_info.search_predicted_ctr,
                  metrics.cost_micros,
                  metrics.clicks,
                  metrics.impressions,
                  metrics.conversions
                FROM keyword_view
                WHERE ad_group_criterion.status != 'REMOVED'
                  AND segments.date DURING THIS_MONTH
                ORDER BY metrics.cost_micros DESC
                LIMIT 1000
            """,
            "search_terms": """
                SELECT
                  search_term_view.search_term,
                  search_term_view.status,
                  campaign.id,
                  campaign.name,
                  ad_group.id,
                  ad_group.name,
                  metrics.impressions,
                  metrics.clicks,
                  metrics.cost_micros,
                  metrics.conversions
                FROM search_term_view
                WHERE segments.date DURING LAST_30_DAYS
                ORDER BY metrics.cost_micros DESC
                LIMIT 1000
            """,
            "negative_keywords_campaign": """
                SELECT
                  campaign.id,
                  campaign.name,
                  campaign_criterion.criterion_id,
                  campaign_criterion.keyword.text,
                  campaign_criterion.keyword.match_type,
                  campaign_criterion.negative
                FROM campaign_criterion
                WHERE campaign_criterion.negative = TRUE
                  AND campaign_criterion.type = 'KEYWORD'
                LIMIT 1000
            """,
            "shared_negative_sets": """
                SELECT
                  shared_set.id,
                  shared_set.name,
                  shared_set.type,
                  shared_set.status,
                  shared_set.member_count
                FROM shared_set
                WHERE shared_set.type = 'NEGATIVE_KEYWORDS'
            """,
            "ads": """
                SELECT
                  campaign.id,
                  campaign.name,
                  ad_group.id,
                  ad_group.name,
                  ad_group_ad.ad.id,
                  ad_group_ad.status,
                  ad_group_ad.ad.type,
                  ad_group_ad.ad.final_urls,
                  ad_group_ad.ad.responsive_search_ad.headlines,
                  ad_group_ad.ad.responsive_search_ad.descriptions,
                  ad_group_ad.policy_summary.approval_status,
                  metrics.cost_micros,
                  metrics.clicks,
                  metrics.impressions,
                  metrics.conversions
                FROM ad_group_ad
                WHERE ad_group_ad.status != 'REMOVED'
                  AND segments.date DURING THIS_MONTH
                LIMIT 500
            """,
            "assets": """
                SELECT
                  asset.id,
                  asset.name,
                  asset.type,
                  asset.source,
                  campaign.id,
                  campaign.name,
                  ad_group.id,
                  metrics.cost_micros,
                  metrics.clicks,
                  metrics.impressions,
                  metrics.conversions
                FROM ad_group_ad_asset_view
                WHERE segments.date DURING THIS_MONTH
                LIMIT 500
            """,
            "asset_groups": """
                SELECT
                  campaign.id,
                  campaign.name,
                  asset_group.id,
                  asset_group.name,
                  asset_group.status,
                  asset_group.final_urls,
                  asset_group.ad_strength,
                  metrics.cost_micros,
                  metrics.clicks,
                  metrics.impressions,
                  metrics.conversions
                FROM asset_group
                WHERE asset_group.status != 'REMOVED'
                  AND segments.date DURING THIS_MONTH
            """,
            "search_themes": """
                SELECT
                  asset_group.id,
                  asset_group.name,
                  asset_group_signal.search_theme.text
                FROM asset_group_signal
                WHERE asset_group_signal.search_theme.text IS NOT NULL
                LIMIT 2000
            """,
            "conversions": """
                SELECT
                  conversion_action.id,
                  conversion_action.name,
                  conversion_action.status,
                  conversion_action.type,
                  conversion_action.category,
                  conversion_action.origin,
                  conversion_action.primary_for_goal,
                  conversion_action.counting_type,
                  conversion_action.click_through_lookback_window_days,
                  conversion_action.view_through_lookback_window_days,
                  conversion_action.value_settings.default_value,
                  conversion_action.value_settings.always_use_default_value,
                  conversion_action.include_in_conversions_metric
                FROM conversion_action
                WHERE conversion_action.status != 'REMOVED'
            """,
            "conversion_metrics": """
                SELECT
                  segments.conversion_action,
                  segments.conversion_action_name,
                  metrics.conversions,
                  metrics.all_conversions,
                  metrics.conversions_value
                FROM campaign
                WHERE segments.date DURING THIS_MONTH
                  AND campaign.status != 'REMOVED'
            """,
            "conversion_goals": """
                SELECT
                  custom_conversion_goal.id,
                  custom_conversion_goal.name,
                  custom_conversion_goal.status
                FROM custom_conversion_goal
            """,
            "geo": """
                SELECT
                  campaign.id,
                  campaign.name,
                  campaign_criterion.location.geo_target_constant,
                  campaign_criterion.bid_modifier,
                  campaign_criterion.negative
                FROM campaign_criterion
                WHERE campaign_criterion.type = 'LOCATION'
                LIMIT 1000
            """,
            "languages": """
                SELECT
                  campaign.id,
                  campaign.name,
                  campaign_criterion.language.language_constant
                FROM campaign_criterion
                WHERE campaign_criterion.type = 'LANGUAGE'
                LIMIT 1000
            """,
            "recommendations": """
                SELECT
                  recommendation.resource_name,
                  recommendation.type,
                  recommendation.campaign
                FROM recommendation
                LIMIT 200
            """,
            "change_events": """
                SELECT
                  change_event.change_date_time,
                  change_event.change_resource_type,
                  change_event.change_resource_name,
                  change_event.resource_change_operation,
                  change_event.user_email,
                  change_event.client_type,
                  change_event.changed_fields
                FROM change_event
                WHERE change_event.change_date_time DURING LAST_14_DAYS
                ORDER BY change_event.change_date_time DESC
                LIMIT 500
            """,
            "shared_budgets": """
                SELECT
                  campaign_budget.id,
                  campaign_budget.name,
                  campaign_budget.amount_micros,
                  campaign_budget.explicitly_shared,
                  campaign_budget.status,
                  campaign_budget.reference_count
                FROM campaign_budget
                WHERE campaign_budget.explicitly_shared = TRUE
            """,
            "bidding_strategies": """
                SELECT
                  bidding_strategy.id,
                  bidding_strategy.name,
                  bidding_strategy.type,
                  bidding_strategy.status,
                  bidding_strategy.currency_code
                FROM bidding_strategy
            """,
            "policy_topics": """
                SELECT
                  ad_group_ad.ad.id,
                  ad_group_ad.policy_summary.approval_status,
                  ad_group_ad.policy_summary.policy_topic_entries
                FROM ad_group_ad
                WHERE ad_group_ad.policy_summary.approval_status != 'APPROVED'
                LIMIT 500
            """,
        }
        return {name: self.run_query(name, query, cid) for name, query in queries.items()}

    def list_campaigns(self, customer_id: str | None = None) -> list[dict]:
        result = self.run_query(
            "campaigns_simple",
            """
            SELECT
              campaign.id,
              campaign.name,
              campaign.status,
              campaign.advertising_channel_type,
              campaign.bidding_strategy_type,
              campaign_budget.amount_micros,
              metrics.cost_micros,
              metrics.clicks,
              metrics.impressions,
              metrics.conversions,
              metrics.ctr
            FROM campaign
            WHERE campaign.status != 'REMOVED'
              AND segments.date DURING THIS_MONTH
            ORDER BY metrics.cost_micros DESC
            """,
            customer_id,
        )
        if not result.ok:
            return [{"error": result.error}]
        out: list[dict] = []
        for row in result.rows:
            cost = _micros(row.get("metrics.cost_micros"))
            conversions = float(row.get("metrics.conversions") or 0)
            cpa = (cost / conversions) if conversions > 0 else None
            out.append(
                {
                    "id": str(row.get("campaign.id")),
                    "name": row.get("campaign.name"),
                    "status": row.get("campaign.status"),
                    "channel": row.get("campaign.advertising_channel_type"),
                    "bidding": row.get("campaign.bidding_strategy_type"),
                    "daily_budget": _micros(row.get("campaign_budget.amount_micros")),
                    "cost": round(cost, 2),
                    "clicks": int(row.get("metrics.clicks") or 0),
                    "impressions": int(row.get("metrics.impressions") or 0),
                    "conversions": round(conversions, 2),
                    "ctr": float(row.get("metrics.ctr") or 0),
                    "cpa": round(cpa, 2) if cpa is not None else None,
                }
            )
        return out

    def list_change_events(self, customer_id: str | None = None, *, days: int = 14) -> list[dict]:
        window = min(max(days, 1), 30)
        result = self.run_query(
            "change_events",
            f"""
            SELECT
              change_event.change_date_time,
              change_event.change_resource_type,
              change_event.resource_change_operation,
              change_event.user_email,
              change_event.client_type
            FROM change_event
            WHERE change_event.change_date_time DURING LAST_{window}_DAYS
            ORDER BY change_event.change_date_time DESC
            LIMIT 500
            """,
            customer_id,
        )
        if not result.ok:
            return [{"error": result.error}]
        return result.rows

    def list_search_terms(self, customer_id: str | None = None, *, days: int = 30) -> list[dict]:
        window = min(max(days, 1), 30)
        result = self.run_query(
            "search_terms",
            f"""
            SELECT
              search_term_view.search_term,
              campaign.name,
              ad_group.name,
              metrics.impressions,
              metrics.clicks,
              metrics.cost_micros,
              metrics.conversions
            FROM search_term_view
            WHERE segments.date DURING LAST_{window}_DAYS
            ORDER BY metrics.cost_micros DESC
            LIMIT 1000
            """,
            customer_id,
        )
        if not result.ok:
            return [{"error": result.error}]
        return result.rows

    def list_recommendations(self, customer_id: str | None = None) -> list[dict]:
        result = self.run_query(
            "recommendations",
            """
            SELECT
              recommendation.resource_name,
              recommendation.type,
              recommendation.campaign
            FROM recommendation
            LIMIT 200
            """,
            customer_id,
        )
        if not result.ok:
            return [{"error": result.error}]
        return result.rows

    def list_conversion_actions(self, customer_id: str | None = None) -> list[dict]:
        meta = self.run_query(
            "conversions",
            """
            SELECT
              conversion_action.id,
              conversion_action.name,
              conversion_action.status,
              conversion_action.type,
              conversion_action.category,
              conversion_action.origin,
              conversion_action.primary_for_goal,
              conversion_action.counting_type,
              conversion_action.click_through_lookback_window_days,
              conversion_action.view_through_lookback_window_days,
              conversion_action.value_settings.default_value,
              conversion_action.include_in_conversions_metric
            FROM conversion_action
            WHERE conversion_action.status != 'REMOVED'
            """,
            customer_id,
        )
        if not meta.ok:
            return [{"error": meta.error}]

        metrics = self.run_query(
            "conversion_metrics",
            """
            SELECT
              segments.conversion_action,
              segments.conversion_action_name,
              metrics.conversions,
              metrics.all_conversions,
              metrics.conversions_value
            FROM campaign
            WHERE segments.date DURING THIS_MONTH
              AND campaign.status != 'REMOVED'
            """,
            customer_id,
        )
        metrics_by_id: dict[str, dict] = {}
        metrics_by_name: dict[str, dict] = {}
        if metrics.ok:
            for row in metrics.rows:
                resource = str(row.get("segments.conversion_action") or "")
                action_id = resource.rsplit("/", 1)[-1] if resource else ""
                name = str(row.get("segments.conversion_action_name") or "")
                add_conversions = float(row.get("metrics.conversions") or 0)
                add_all = float(row.get("metrics.all_conversions") or 0)
                add_value = float(row.get("metrics.conversions_value") or 0)
                for key, bucket in ((action_id, metrics_by_id), (name, metrics_by_name)):
                    if not key:
                        continue
                    current = bucket.get(key) or {
                        "metrics.conversions": 0.0,
                        "metrics.all_conversions": 0.0,
                        "metrics.conversions_value": 0.0,
                    }
                    current["metrics.conversions"] = float(current["metrics.conversions"]) + add_conversions
                    current["metrics.all_conversions"] = float(current["metrics.all_conversions"]) + add_all
                    current["metrics.conversions_value"] = float(current["metrics.conversions_value"]) + add_value
                    bucket[key] = current
        else:
            # Fallback: account-level totals only (no per-action split).
            logger.warning("Per-action conversion metrics unavailable: %s", metrics.error)

        merged: list[dict] = []
        for row in meta.rows:
            action_id = str(row.get("conversion_action.id") or "")
            name = str(row.get("conversion_action.name") or "")
            stats = metrics_by_id.get(action_id) or metrics_by_name.get(name) or {}
            merged.append({**row, **stats})
        return merged

    def performance_snapshot(self, customer_id: str | None = None) -> PerformanceSnapshot:
        cid = customer_id or self.settings.customer_id
        today = date.today()
        month_start = today.replace(day=1).isoformat()
        today_s = today.isoformat()
        today_metrics = self._aggregate_metrics(cid, f"segments.date = '{today_s}'")
        mtd_metrics = self._aggregate_metrics(cid, f"segments.date BETWEEN '{month_start}' AND '{today_s}'")
        spend_today = today_metrics["cost"]
        leads_today = int(round(today_metrics["conversions"]))
        spend_mtd = mtd_metrics["cost"]
        leads_mtd = int(round(mtd_metrics["conversions"]))
        cpa = (spend_mtd / leads_mtd) if leads_mtd > 0 else None
        return PerformanceSnapshot(
            spend_usd_today=spend_today,
            spend_usd_mtd=spend_mtd,
            leads_today=leads_today,
            leads_mtd=leads_mtd,
            cpa_usd=round(cpa, 2) if cpa is not None else None,
            ctr=mtd_metrics["ctr"],
            conversion_rate=mtd_metrics["conversion_rate"],
            impression_share_lost_budget=None,
            disapproved_assets=0,
            conversion_outage=leads_today == 0 and spend_today > 50,
        )

    def snapshot(self, customer_id: str | None = None) -> AccountSnapshot:
        cid = customer_id or self.settings.customer_id
        meta = self.ping()
        return AccountSnapshot(
            customer_id=meta["customer_id"],
            login_customer_id=meta["login_customer_id"],
            descriptive_name=meta["descriptive_name"],
            currency_code=meta["currency_code"],
            time_zone=meta["time_zone"],
            campaigns=self.list_campaigns(cid),
            change_events=self.list_change_events(cid),
            search_terms=self.list_search_terms(cid),
            recommendations=self.list_recommendations(cid),
            performance=self.performance_snapshot(cid),
        )

    def _aggregate_metrics(self, customer_id: str, date_predicate: str) -> dict[str, float]:
        query = f"""
            SELECT
              metrics.cost_micros,
              metrics.clicks,
              metrics.impressions,
              metrics.conversions,
              metrics.ctr
            FROM customer
            WHERE {date_predicate}
        """
        cost = clicks = impressions = conversions = 0.0
        ctr = 0.0
        for row in self._search(customer_id, query):
            cost += int(row.metrics.cost_micros) / 1_000_000
            clicks += int(row.metrics.clicks)
            impressions += int(row.metrics.impressions)
            conversions += float(row.metrics.conversions)
            ctr = float(row.metrics.ctr)
        conversion_rate = (conversions / clicks) if clicks else 0.0
        return {
            "cost": round(cost, 2),
            "clicks": clicks,
            "impressions": impressions,
            "conversions": round(conversions, 2),
            "ctr": round(ctr, 4),
            "conversion_rate": round(conversion_rate, 4),
        }

    def _search(self, customer_id: str, query: str):
        if self._search_impl is not None:
            return self._search_impl(customer_id, query)
        service = self.client.get_service("GoogleAdsService")
        return service.search(customer_id=customer_id, query=query)

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        """Flatten proto-plus Search row into dotted keys for CSV/JSON exports."""
        out: dict[str, Any] = {}

        def walk(prefix: str, value: Any) -> None:
            if value is None:
                return
            if hasattr(value, "_pb"):
                # proto-plus message
                for field_name in value.meta.fields.keys() if hasattr(value, "meta") else []:
                    try:
                        child = getattr(value, field_name)
                    except Exception:  # noqa: BLE001
                        continue
                    # skip empty repeated / default
                    walk(f"{prefix}.{field_name}" if prefix else field_name, child)
                # fallback: common Google Ads row attributes
                for attr in (
                    "customer",
                    "campaign",
                    "campaign_budget",
                    "ad_group",
                    "ad_group_criterion",
                    "ad_group_ad",
                    "asset",
                    "asset_group",
                    "asset_group_signal",
                    "conversion_action",
                    "custom_conversion_goal",
                    "campaign_criterion",
                    "recommendation",
                    "change_event",
                    "search_term_view",
                    "shared_set",
                    "bidding_strategy",
                    "metrics",
                    "segments",
                ):
                    if hasattr(row if not prefix else value, attr) and not prefix:
                        walk(attr, getattr(row, attr))
                if not prefix:
                    return
                # enum / scalar extraction
                if hasattr(value, "name") and not hasattr(value, "_pb"):
                    out[prefix] = value.name
                    return
            if isinstance(value, (str, int, float, bool)):
                out[prefix] = value
                return
            if isinstance(value, (list, tuple)):
                serializable = []
                for item in value:
                    if hasattr(item, "text"):
                        serializable.append(getattr(item, "text"))
                    elif hasattr(item, "name"):
                        serializable.append(getattr(item, "name"))
                    else:
                        serializable.append(str(item))
                out[prefix] = serializable
                return
            # enum
            name = getattr(value, "name", None)
            if isinstance(name, str) and name.isupper():
                out[prefix] = name
                return
            # micros / generic
            try:
                out[prefix] = value
            except Exception:  # noqa: BLE001
                out[prefix] = str(value)

        # Prefer structured extraction from known roots on the row object.
        for attr in (
            "customer",
            "campaign",
            "campaign_budget",
            "ad_group",
            "ad_group_criterion",
            "ad_group_ad",
            "asset",
            "asset_group",
            "asset_group_signal",
            "conversion_action",
            "custom_conversion_goal",
            "campaign_criterion",
            "recommendation",
            "change_event",
            "search_term_view",
            "shared_set",
            "bidding_strategy",
            "metrics",
            "segments",
        ):
            if hasattr(row, attr):
                _flatten_message(out, attr, getattr(row, attr))
        if not out:
            out["raw"] = str(row)
        return out


def _flatten_message(out: dict[str, Any], prefix: str, message: Any, *, depth: int = 0) -> None:
    if message is None or depth > 4:
        return
    if isinstance(message, (str, int, float, bool)):
        out[prefix] = message
        return
    name = getattr(message, "name", None)
    if isinstance(name, str) and name.isupper() and not hasattr(message, "_pb"):
        out[prefix] = name
        return
    if isinstance(message, (list, tuple)):
        if message and all(hasattr(item, "text") for item in message):
            out[prefix] = [item.text for item in message]
        else:
            out[prefix] = [str(item) for item in message[:20]]
        return

    fields = None
    meta = getattr(message, "_meta", None) or getattr(message, "meta", None)
    if meta is not None:
        fields = getattr(meta, "fields", None)
    if fields:
        for field_name in fields:
            try:
                child = getattr(message, field_name)
            except Exception:  # noqa: BLE001
                continue
            _flatten_message(out, f"{prefix}.{field_name}", child, depth=depth + 1)
        return

    # Narrow fallback for proto-plus messages without usable meta.
    for attr in ("id", "name", "text", "status", "type", "resource_name", "primary_for_goal"):
        if hasattr(message, attr):
            try:
                child = getattr(message, attr)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(child, (str, int, float, bool)):
                out[f"{prefix}.{attr}"] = child
            elif hasattr(child, "name") and isinstance(child.name, str):
                out[f"{prefix}.{attr}"] = child.name


def _micros(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    return float(value) / 1_000_000


class _ReadOnlyClientProxy:
    def __init__(self, client: Any) -> None:
        self._client = client

    def get_service(self, name: str):
        assert_readonly_service(name)
        return self._client.get_service(name)

    def __getattr__(self, item: str):
        return getattr(self._client, item)
