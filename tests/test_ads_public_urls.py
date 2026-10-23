from ads_ai_agents.intelligence.ads_public_urls import topic_article_url
from ads_ai_agents.intelligence.grant_reactivation_fix import GrantReactivationFixer


def test_topic_article_url_maps_search_intent_to_owned_article():
    assert topic_article_url("UA", "боротьба з депресією") == (
        "https://www.example.com/uk/news/15"
    )
    assert topic_article_url("EN", "burnout at work") == (
        "https://www.example.com/en/news/16"
    )
    assert topic_article_url("EN", "workplace stress") == (
        "https://www.example.com/en/news/20"
    )


def test_topic_article_url_falls_back_to_locale_news_index():
    assert topic_article_url("DE", "general support") == (
        "https://www.example.com/de/news"
    )


def test_reactivation_does_not_override_specialist_agents(monkeypatch):
    fixer = GrantReactivationFixer.__new__(GrantReactivationFixer)
    called: list[str] = []

    for name in (
        "_pause_risky_campaigns",
        "_rename_enabled_campaigns",
        "_update_finals",
        "_update_pmax_finals",
        "_ensure_structure",
        "_ensure_sitelinks",
    ):
        monkeypatch.setattr(
            fixer,
            name,
            lambda result, *, dry_run, method=name: called.append(method),
        )
    monkeypatch.setattr(fixer, "_structure_snapshot", lambda: [])
    monkeypatch.setattr(fixer, "_form_checklist", lambda result: {})
    monkeypatch.setattr(
        fixer,
        "_set_search_bidding_to_maximize_clicks",
        lambda *args, **kwargs: called.append("legacy_bidding"),
    )
    monkeypatch.setattr(
        fixer,
        "_pause_risky_keywords",
        lambda *args, **kwargs: called.append("legacy_keywords"),
    )
    monkeypatch.setattr(
        fixer,
        "_replace_clinical_rsas",
        lambda *args, **kwargs: called.append("legacy_creatives"),
    )

    fixer.apply(dry_run=True)

    assert "legacy_bidding" not in called
    assert "legacy_keywords" not in called
    assert "legacy_creatives" not in called
