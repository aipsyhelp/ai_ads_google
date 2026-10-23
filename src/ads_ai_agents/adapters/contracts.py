from __future__ import annotations

from typing import Protocol

from ads_ai_agents.core.domain import CampaignDraft, GateDecision, PerformanceSnapshot


class AdsAccountReader(Protocol):
    def list_campaigns(self, customer_id: str) -> list[dict]:
        """Return current campaigns from an advertising account."""

    def list_change_events(self, customer_id: str, *, days: int = 7) -> list[dict]:
        """Return recent account changes for audit and drift detection."""

    def list_search_terms(self, customer_id: str, *, days: int = 7) -> list[dict]:
        """Return search terms for keyword promotion / negative mining."""

    def performance_snapshot(self, customer_id: str) -> PerformanceSnapshot:
        """Return spend/leads/CPA snapshot for the daily allocate+guard loop."""


class CampaignPublisher(Protocol):
    def publish_draft(self, campaign: CampaignDraft, gate: GateDecision) -> str:
        """Publish an approved campaign draft and return an external campaign id."""


class MeasurementWarehouse(Protocol):
    def write_quality_event(self, event: dict) -> None:
        """Persist CRM/offline quality signals for bidding and reporting."""


class AssetGenerator(Protocol):
    def generate_asset_briefs(self, campaign: CampaignDraft) -> list[dict]:
        """Generate copy, image, and video briefs that still require policy review."""


class CompetitorIntelSource(Protocol):
    def ingest_auction_insights_export(self, path: str) -> list[dict]:
        """Ingest UI-exported Auction Insights rows."""

    def ingest_ads_transparency_snapshot(self, payload: dict) -> list[dict]:
        """Store Ads Transparency Center creative snapshots for competitor analysis."""
