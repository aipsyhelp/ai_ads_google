from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AgentLayer(str, Enum):
    MARKET = "market_intelligence"
    STRATEGY = "strategy"
    CREATIVE = "creative_studio"
    ACQUISITION = "acquisition"
    CRO = "conversion_optimization"
    REVENUE = "revenue_analytics"


@dataclass(frozen=True)
class PlatformAgentSpec:
    id: str
    name: str
    layer: AgentLayer
    phase: str  # P0 P1 P2 P3 P4
    mutates: str  # never | draft_only | controlled | approval
    description: str


PLATFORM_AGENTS: tuple[PlatformAgentSpec, ...] = (
    # L1 Market
    PlatformAgentSpec("competitor_intel", "Competitor Intelligence", AgentLayer.MARKET, "P1", "draft_only", "Daily public competitive diffs"),
    PlatformAgentSpec("search_opportunity", "Search Opportunity", AgentLayer.MARKET, "P1", "draft_only", "Trends/News/Reddit demand spikes"),
    PlatformAgentSpec("intent_mining", "Intent Mining", AgentLayer.MARKET, "P1", "draft_only", "Pain-language intents, not just keywords"),
    PlatformAgentSpec("aso_intel", "App Store / Play Intel", AgentLayer.MARKET, "P1", "draft_only", "Reviews, rankings, competitor ASOs"),
    PlatformAgentSpec("regulatory_watch", "Regulatory Watch", AgentLayer.MARKET, "P2", "draft_only", "Digital wellbeing / advertising policy shifts"),
    PlatformAgentSpec("serp_evidence", "SERP / Ads Transparency", AgentLayer.MARKET, "P1", "draft_only", "Public ad creatives & SERP evidence"),
    # L2 Strategy
    PlatformAgentSpec("ceo", "CEO AI", AgentLayer.STRATEGY, "P0", "draft_only", "Weekly executive review"),
    PlatformAgentSpec("strategy", "Strategy Engine", AgentLayer.STRATEGY, "P0", "draft_only", "Countries, segments, budget priorities"),
    PlatformAgentSpec("portfolio", "Portfolio Agent", AgentLayer.STRATEGY, "P2", "approval", "Grant vs Paid vs App allocation"),
    PlatformAgentSpec("experiment_prioritizer", "Experiment Prioritizer", AgentLayer.STRATEGY, "P2", "draft_only", "Pick ≤5 weekly experiments"),
    # L3 Creative
    PlatformAgentSpec("creative_lab", "Creative Lab", AgentLayer.CREATIVE, "P2", "draft_only", "100+/day Hero/CTA/RSA/video drafts"),
    PlatformAgentSpec("copy_ai", "Copy AI", AgentLayer.CREATIVE, "P2", "draft_only", "LP/RSA/paywall copy"),
    PlatformAgentSpec("image_ai", "Image AI", AgentLayer.CREATIVE, "P2", "draft_only", "Image briefs / generations"),
    PlatformAgentSpec("video_ai", "Video AI", AgentLayer.CREATIVE, "P2", "draft_only", "Video briefs / generations"),
    PlatformAgentSpec("localization", "Localization AI", AgentLayer.CREATIVE, "P2", "draft_only", "UA/EN/DE packs"),
    PlatformAgentSpec("brand_policy_gate", "Brand & Policy Gate", AgentLayer.CREATIVE, "P1", "never", "Block medical/crisis claims"),
    # L4 Acquisition
    PlatformAgentSpec("ads_controlled_live", "Google Ads CONTROLLED_LIVE", AgentLayer.ACQUISITION, "P0", "controlled", "Capped Ads executor"),
    PlatformAgentSpec("seo", "SEO Agent", AgentLayer.ACQUISITION, "P3", "draft_only", "Content/SEO opportunities"),
    PlatformAgentSpec("aso", "ASO Agent", AgentLayer.ACQUISITION, "P3", "draft_only", "Store listing experiments"),
    PlatformAgentSpec("email_content", "Email / Content Agent", AgentLayer.ACQUISITION, "P3", "approval", "Lifecycle & content"),
    PlatformAgentSpec("demand_gen", "Demand Gen / YouTube", AgentLayer.ACQUISITION, "P3", "approval", "Policy-gated video demand"),
    # L5 CRO
    PlatformAgentSpec("cro_funnel", "CRO Funnel Agent", AgentLayer.CRO, "P0", "draft_only", "Drop-off diagnosis"),
    PlatformAgentSpec("landing_ai", "Landing AI", AgentLayer.CRO, "P2", "draft_only", "LP experiments"),
    PlatformAgentSpec("onboarding", "Onboarding Agent", AgentLayer.CRO, "P2", "approval", "Screen order / habit loop"),
    PlatformAgentSpec("paywall", "Paywall / Pricing Agent", AgentLayer.CRO, "P2", "approval", "Trial/price tests"),
    PlatformAgentSpec("activation_chat", "Activation Chat Agent", AgentLayer.CRO, "P2", "draft_only", "First chat quality"),
    # L6 Revenue
    PlatformAgentSpec("revenue", "Revenue Agent", AgentLayer.REVENUE, "P0", "never", "CPPU/LTV/CAC — forbids CPA worship"),
    PlatformAgentSpec("experiment", "Experiment Agent", AgentLayer.REVENUE, "P2", "approval", "Run ≤5/week, promote winners"),
    PlatformAgentSpec("analytics", "Analytics / Cohort", AgentLayer.REVENUE, "P1", "never", "Cohorts & forecasts"),
    PlatformAgentSpec("measurement", "Measurement / Data Manager", AgentLayer.REVENUE, "P1", "approval", "Quality conversion pipeline"),
    PlatformAgentSpec("guardian", "Rollback / Kill-switch Guardian", AgentLayer.REVENUE, "P0", "controlled", "Freeze & rollback"),
)
