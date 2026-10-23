from ads_ai_agents.platform.agents.cro import run_cro_diagnosis
from ads_ai_agents.platform.north_star import FunnelSnapshot
from ads_ai_agents.platform.product_facts import (
    firetest_urls,
    load_product_facts,
    store_click_events,
)
from ads_ai_agents.platform.strategy_engine import build_strategy_mission
from ads_ai_agents.platform.agents.competitor_intel import build_competitor_brief
from ads_ai_agents.platform.agents.intent_mining import mine_intent_opportunities
from ads_ai_agents.platform.agents.revenue import run_revenue_review


def test_product_facts_loaded():
    facts = load_product_facts()
    assert facts["status"] == "loaded"
    assert facts["landings"]["UA"].endswith("/uk")
    assert facts["pricing"]["stripe_live"]["eur_monthly"] == 9.99  # demo price from config/product_facts.example.json
    assert "app.example.com" in facts["stores"]["surfaces"]


def test_store_click_events_secondary():
    events = store_click_events()
    names = {e["event"] for e in events}
    assert names == {"click_store_android", "click_store_ios"}
    assert all(e["ads_role"] == "secondary" for e in events)


def test_firetest_urls_embed_gclid():
    urls = firetest_urls(gclid="abc123")
    assert "gclid=abc123" in urls["lp_en"]
    assert urls["chat_home"].endswith("/chat")


def test_cro_and_strategy_use_product_facts():
    funnel = FunnelSnapshot(period_days=30, ad_spend_usd=1000, paying_users=0)
    cro = run_cro_diagnosis(funnel)
    assert cro["product_facts"]["status"] == "loaded"
    assert cro["product_facts"]["pricing_stripe_live"]["eur_monthly"] == 9.99  # demo price from config/product_facts.example.json
    assert any("Secondary" in a or "store" in a.lower() for a in cro["recommended_actions"])
    strategy = build_strategy_mission(
        funnel=funnel,
        competitor_brief=build_competitor_brief(),
        intent_brief=mine_intent_opportunities([]),
        cro_brief=cro,
        revenue_brief=run_revenue_review(funnel),
    )
    assert "DE" in strategy["country_stance"]
    assert strategy["status"] == "active_with_product_facts"
    assert any(p.get("theme") == "Measurement integrity" for p in strategy["priorities"])
