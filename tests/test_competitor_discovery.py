from ads_ai_agents.platform.agents.competitor_discovery import discover_top_competitors


def test_top10_competitors_cover_categories():
    pack = discover_top_competitors()
    assert pack["count"] == 10
    brands = {c["brand"] for c in pack["competitors"]}
    assert "Wysa" in brands
    assert "BetterHelp" in brands
    assert "Headspace (Ebb)" in brands
    cats = {c["category"] for c in pack["competitors"]}
    assert "ai_chat" in cats
    assert pack["your_brand_playbook"]
