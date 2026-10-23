from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Portfolio(str, Enum):
    GRANT_SEARCH = "grant_search"
    PAID_SEARCH = "paid_search"
    PERFORMANCE_MAX = "performance_max"
    APP_GROWTH = "app_growth"
    AI_MAX_SEARCH = "ai_max_search"


class ApprovalState(str, Enum):
    DRAFT = "draft"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    BLOCKED = "blocked"
    QUEUED = "queued"
    PUBLISHED = "published"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class WorkflowStage(str, Enum):
    SENSE = "sense"
    COMPETE = "compete"
    STRUCTURE = "structure"
    CREATE = "create"
    ALLOCATE = "allocate"
    GUARD = "guard"


@dataclass(frozen=True)
class BusinessGoal:
    name: str
    primary_conversion: str
    secondary_conversions: tuple[str, ...] = ()
    target_cpa_usd: float | None = None
    target_cpa_eur: float | None = None
    target_roas: float | None = None
    notes: str = ""

    @property
    def effective_target_cpa_usd(self) -> float | None:
        if self.target_cpa_usd is not None:
            return self.target_cpa_usd
        if self.target_cpa_eur is not None:
            return round(self.target_cpa_eur * 1.08, 2)
        return None


@dataclass(frozen=True)
class GrantEconomics:
    """Google Ad Grants economics for YOUR_BRAND."""

    monthly_budget_usd: float = 10_000.0
    target_cpa_usd: float = 2.0
    max_daily_budget_usd: float = 329.0
    currency: str = "USD"

    @property
    def target_daily_leads(self) -> float:
        if self.target_cpa_usd <= 0:
            return 0.0
        return round(self.max_daily_budget_usd / self.target_cpa_usd, 1)

    @property
    def target_monthly_leads(self) -> float:
        if self.target_cpa_usd <= 0:
            return 0.0
        return round(self.monthly_budget_usd / self.target_cpa_usd, 0)


@dataclass(frozen=True)
class IntentCluster:
    name: str
    jobs_to_be_done: tuple[str, ...]
    safe_language: tuple[str, ...]
    prohibited_language: tuple[str, ...]
    locales: tuple[str, ...] = ("en",)
    seed_keywords: tuple[str, ...] = ()
    negative_themes: tuple[str, ...] = ()


@dataclass(frozen=True)
class AssetSpec:
    cluster: str
    locale: str
    headlines: tuple[str, ...]
    descriptions: tuple[str, ...]
    image_briefs: tuple[str, ...] = ()
    video_briefs: tuple[str, ...] = ()
    landing_page_url: str | None = None


@dataclass(frozen=True)
class CompetitorWatchlist:
    brands: tuple[str, ...] = ()
    domains: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class PerformanceSnapshot:
    """Optional live/telemetry snapshot for daily correction logic."""

    spend_usd_today: float | None = None
    spend_usd_mtd: float | None = None
    leads_today: int | None = None
    leads_mtd: int | None = None
    cpa_usd: float | None = None
    ctr: float | None = None
    conversion_rate: float | None = None
    impression_share_lost_budget: float | None = None
    disapproved_assets: int = 0
    conversion_outage: bool = False


@dataclass(frozen=True)
class CampaignDraft:
    name: str
    portfolio: Portfolio
    market: str
    locale: str
    business_goal: BusinessGoal
    intent_clusters: tuple[IntentCluster, ...]
    daily_budget_eur: float
    bidding_strategy: str
    asset_specs: tuple[AssetSpec, ...] = ()
    negative_keywords: tuple[str, ...] = ()
    brand_exclusions: tuple[str, ...] = ()
    grant: GrantEconomics = field(default_factory=GrantEconomics)
    competitors: CompetitorWatchlist = field(default_factory=CompetitorWatchlist)
    performance: PerformanceSnapshot | None = None
    search_themes: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def daily_budget_usd(self) -> float:
        # Drafts historically stored EUR; grant economics are USD-first.
        explicit = self.metadata.get("daily_budget_usd")
        if explicit is not None:
            return float(explicit)
        if self.portfolio is Portfolio.GRANT_SEARCH:
            return min(float(self.daily_budget_eur), self.grant.max_daily_budget_usd)
        return float(self.daily_budget_eur)


@dataclass(frozen=True)
class PolicyFinding:
    code: str
    message: str
    risk: RiskLevel
    field: str | None = None


@dataclass(frozen=True)
class GateDecision:
    state: ApprovalState
    risk: RiskLevel
    findings: tuple[PolicyFinding, ...] = ()

    @property
    def can_publish(self) -> bool:
        return self.state is ApprovalState.APPROVED


@dataclass(frozen=True)
class AgentRecommendation:
    agent: str
    stage: WorkflowStage
    title: str
    rationale: str
    actions: tuple[str, ...]
    risk: RiskLevel = RiskLevel.LOW
    requires_approval: bool = True
    auto_apply_safe: bool = False
    priority: int = 50


@dataclass(frozen=True)
class WorkflowStep:
    stage: WorkflowStage
    objective: str
    agents: tuple[str, ...]


@dataclass(frozen=True)
class MissionPlan:
    campaign: CampaignDraft
    gate: GateDecision
    recommendations: tuple[AgentRecommendation, ...]
    workflow: tuple[WorkflowStep, ...] = ()
    daily_checklist: tuple[str, ...] = ()
    north_star: str = ""
    organization: dict | None = None
