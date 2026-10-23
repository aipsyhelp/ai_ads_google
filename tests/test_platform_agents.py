from ads_ai_agents.platform.agents.competitor_intel import build_competitor_brief
from ads_ai_agents.platform.agents.creative_lab import generate_creative_draft_pack
from ads_ai_agents.platform.agents.experiment import plan_weekly_experiments
from ads_ai_agents.platform.agents.intent_mining import mine_intent_opportunities
from ads_ai_agents.platform.north_star import FunnelSnapshot
from ads_ai_agents.platform.agents.cro import run_cro_diagnosis
from ads_ai_agents.platform.registry import PLATFORM_AGENTS
from ads_ai_agents.platform.strategy_engine import build_strategy_mission
from ads_ai_agents.platform.agents.revenue import run_revenue_review


def test_registry_has_core_layers():
    layers = {a.layer.value for a in PLATFORM_AGENTS}
    assert "market_intelligence" in layers
    assert "acquisition" in layers
    assert "revenue_analytics" in layers
    assert len(PLATFORM_AGENTS) >= 25


def test_intent_and_creative_cover_locales():
    intent = mine_intent_opportunities([])
    assert "EN" in intent["seed_pain_intents"]
    creative = generate_creative_draft_pack()
    assert set(creative["locales"]) >= {"UA", "EN", "DE"}


def test_strategy_blocks_scale_when_zero_paid():
    funnel = FunnelSnapshot(period_days=30, ad_spend_usd=9000, ad_clicks=5000, paying_users=0)
    cro = run_cro_diagnosis(funnel)
    rev = run_revenue_review(funnel)
    strategy = build_strategy_mission(
        funnel=funnel,
        competitor_brief=build_competitor_brief(),
        intent_brief=mine_intent_opportunities([]),
        cro_brief=cro,
        revenue_brief=rev,
    )
    assert "Prove paid path" in strategy["priorities"][0]["theme"]
    assert plan_weekly_experiments()["max_per_week"] == 5
