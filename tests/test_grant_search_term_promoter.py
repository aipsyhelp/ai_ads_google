from ads_ai_agents.intelligence.grant_search_term_promoter import (
    GrantSearchTermPromoter,
)


def test_search_term_policy_and_language_gate():
    safe = GrantSearchTermPromoter._is_safe

    assert safe(
        "burnout symptoms",
        "Ser / www.example.com / Articles / EN / Brand_Search",
        "burnout at work",
    )
    assert safe(
        "ознаки професійного вигорання",
        "Ser / www.example.com / Articles / UA / National / Brand_Search",
        "професійне вигорання",
    )
    assert not safe(
        "psychologist online",
        "Ser / www.example.com / Articles / EN / Brand_Search",
        "burnout at work",
    )
    assert not safe(
        "panic attack medication",
        "Ser / www.example.com / Articles / EN / Brand_Search",
        "burnout at work",
    )
    assert not safe(
        "ознаки вигорання",
        "Ser / www.example.com / Articles / EN / Brand_Search",
        "burnout at work",
    )
    assert not safe(
        "burnout symptoms",
        "Ser / www.example.com / Articles / UA / National / Brand_Search",
        "професійне вигорання",
    )
    assert not safe(
        "burnout",
        "Ser / www.example.com / Articles / EN / Brand_Search",
        "burnout at work",
    )
    assert not safe(
        "тест на депресію",
        "Ser / www.example.com / Articles / UA / National / Brand_Search",
        "боротьба з депресією",
    )
    assert not safe(
        "паническая атака признаки",
        "Ser / www.example.com / Articles / UA / National / Brand_Search",
        "як побороти панічні атаки",
    )


def test_search_term_promoter_dry_run_reports_plan(monkeypatch):
    promoter = GrantSearchTermPromoter.__new__(GrantSearchTermPromoter)
    candidates = [
        {
            "campaign": "Search / EN",
            "ad_group_id": "123",
            "ad_group": "burnout at work",
            "term": "burnout symptoms",
            "match_type": "EXACT",
            "impressions_60d": 137,
            "clicks_60d": 7,
            "ctr_60d": 0.0511,
            "reported_conversions_60d": 21.0,
        }
    ]
    monkeypatch.setattr(promoter, "inventory", lambda **kwargs: candidates)

    result = promoter.apply(dry_run=True)

    assert result.candidates_found == 1
    assert result.keywords_planned == candidates
    assert result.keywords_created == 0
    assert result.errors == []
