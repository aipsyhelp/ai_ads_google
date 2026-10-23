from ads_ai_agents.intelligence.locale_strategy import build_locale_strategy_pack


def test_locale_pack_covers_ua_de_en():
    pack = build_locale_strategy_pack(search_terms=[])
    assert set(pack["locales"]) == {"UA", "DE", "EN"}
    assert pack["publish_assets"] == "approval_required"
    assert pack["locales"]["EN"]["seed_keywords"]
    assert pack["locales"]["DE"]["structure_recommendation"]
