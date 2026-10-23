"""Revenue Growth AI Operating System — Ads is one acquisition executor."""

from ads_ai_agents.platform.north_star import FunnelSnapshot, RevenueKpis, compute_revenue_kpis
from ads_ai_agents.platform.registry import PLATFORM_AGENTS, AgentLayer

__all__ = [
    "FunnelSnapshot",
    "RevenueKpis",
    "compute_revenue_kpis",
    "PLATFORM_AGENTS",
    "AgentLayer",
]
