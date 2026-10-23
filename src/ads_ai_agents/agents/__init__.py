"""Role-specialized agents used by the advertising control plane."""

from ads_ai_agents.agents.assets import AssetAgent
from ads_ai_agents.agents.budget import BudgetAgent
from ads_ai_agents.agents.campaign_builder import CampaignBuilderAgent
from ads_ai_agents.agents.competitors import CompetitorAgent
from ads_ai_agents.agents.keywords import KeywordAgent
from ads_ai_agents.agents.measurement import MeasurementAgent
from ads_ai_agents.agents.policy_qa import PolicyQAAgent
from ads_ai_agents.agents.strategy import StrategyAgent
from ads_ai_agents.agents.trends import TrendAgent
from ads_ai_agents.agents.watchdog import WatchdogAgent

ENTERPRISE_AGENTS = (
    MeasurementAgent(),
    CompetitorAgent(),
    TrendAgent(),
    StrategyAgent(),
    CampaignBuilderAgent(),
    KeywordAgent(),
    AssetAgent(),
    BudgetAgent(),
    PolicyQAAgent(),
    WatchdogAgent(),
)

__all__ = [
    "AssetAgent",
    "BudgetAgent",
    "CampaignBuilderAgent",
    "CompetitorAgent",
    "ENTERPRISE_AGENTS",
    "KeywordAgent",
    "MeasurementAgent",
    "PolicyQAAgent",
    "StrategyAgent",
    "TrendAgent",
    "WatchdogAgent",
]
