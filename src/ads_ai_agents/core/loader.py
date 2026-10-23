from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ads_ai_agents.core.domain import (
    AssetSpec,
    BusinessGoal,
    CampaignDraft,
    CompetitorWatchlist,
    GrantEconomics,
    IntentCluster,
    PerformanceSnapshot,
    Portfolio,
)


def load_campaign_draft(path: str | Path) -> CampaignDraft:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return campaign_from_dict(raw)


def campaign_from_dict(raw: dict[str, Any]) -> CampaignDraft:
    goal_raw = raw["business_goal"]
    goal = BusinessGoal(
        name=goal_raw["name"],
        primary_conversion=goal_raw.get("primary_conversion", ""),
        secondary_conversions=tuple(goal_raw.get("secondary_conversions", ())),
        target_cpa_usd=goal_raw.get("target_cpa_usd"),
        target_cpa_eur=goal_raw.get("target_cpa_eur"),
        target_roas=goal_raw.get("target_roas"),
        notes=goal_raw.get("notes", ""),
    )

    clusters = tuple(
        IntentCluster(
            name=item["name"],
            jobs_to_be_done=tuple(item.get("jobs_to_be_done", ())),
            safe_language=tuple(item.get("safe_language", ())),
            prohibited_language=tuple(item.get("prohibited_language", ())),
            locales=tuple(item.get("locales", ("en",))),
            seed_keywords=tuple(item.get("seed_keywords", ())),
            negative_themes=tuple(item.get("negative_themes", ())),
        )
        for item in raw.get("intent_clusters", ())
    )

    assets = tuple(
        AssetSpec(
            cluster=item["cluster"],
            locale=item["locale"],
            headlines=tuple(item.get("headlines", ())),
            descriptions=tuple(item.get("descriptions", ())),
            image_briefs=tuple(item.get("image_briefs", ())),
            video_briefs=tuple(item.get("video_briefs", ())),
            landing_page_url=item.get("landing_page_url"),
        )
        for item in raw.get("asset_specs", ())
    )

    grant_raw = raw.get("grant", {})
    grant = GrantEconomics(
        monthly_budget_usd=float(grant_raw.get("monthly_budget_usd", 10_000)),
        target_cpa_usd=float(grant_raw.get("target_cpa_usd", goal.effective_target_cpa_usd or 2.0)),
        max_daily_budget_usd=float(grant_raw.get("max_daily_budget_usd", 329)),
        currency=grant_raw.get("currency", "USD"),
    )

    competitors_raw = raw.get("competitors", {})
    competitors = CompetitorWatchlist(
        brands=tuple(competitors_raw.get("brands", ())),
        domains=tuple(competitors_raw.get("domains", ())),
        notes=competitors_raw.get("notes", ""),
    )

    performance = None
    if "performance" in raw:
        perf = raw["performance"] or {}
        performance = PerformanceSnapshot(
            spend_usd_today=perf.get("spend_usd_today"),
            spend_usd_mtd=perf.get("spend_usd_mtd"),
            leads_today=perf.get("leads_today"),
            leads_mtd=perf.get("leads_mtd"),
            cpa_usd=perf.get("cpa_usd"),
            ctr=perf.get("ctr"),
            conversion_rate=perf.get("conversion_rate"),
            impression_share_lost_budget=perf.get("impression_share_lost_budget"),
            disapproved_assets=int(perf.get("disapproved_assets", 0)),
            conversion_outage=bool(perf.get("conversion_outage", False)),
        )

    return CampaignDraft(
        name=raw["name"],
        portfolio=Portfolio(raw["portfolio"]),
        market=raw["market"],
        locale=raw["locale"],
        business_goal=goal,
        intent_clusters=clusters,
        daily_budget_eur=float(raw["daily_budget_eur"]),
        bidding_strategy=raw["bidding_strategy"],
        asset_specs=assets,
        negative_keywords=tuple(raw.get("negative_keywords", ())),
        brand_exclusions=tuple(raw.get("brand_exclusions", ())),
        grant=grant,
        competitors=competitors,
        performance=performance,
        search_themes=tuple(raw.get("search_themes", ())),
        metadata=dict(raw.get("metadata", {})),
    )
