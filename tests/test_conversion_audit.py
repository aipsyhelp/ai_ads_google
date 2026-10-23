from ads_ai_agents.intelligence.conversion_audit import (
    ConversionTier,
    audit_conversion_actions,
    classify_conversion_name,
)


def test_classify_micro_and_activation():
    assert classify_conversion_name("Page view") is ConversionTier.MICRO
    assert classify_conversion_name("qualified_onboarding_complete") is ConversionTier.ACTIVATION
    assert classify_conversion_name("subscription_started") is ConversionTier.REVENUE


def test_ga4_underscore_event_names_classify_as_micro():
    for name in ("page_view", "www.example.com (web) page_view", "screen_view", "first_open"):
        assert classify_conversion_name(name) is ConversionTier.MICRO, name


def test_micro_primary_flagged_and_terminology():
    rows = [
        {
            "conversion_action.id": "1",
            "conversion_action.name": "page_view",
            "conversion_action.primary_for_goal": True,
            "conversion_action.origin": "WEBSITE",
            "conversion_action.category": "PAGE_VIEW",
            "metrics.conversions": 2510,
            "metrics.all_conversions": 3000,
        },
        {
            "conversion_action.id": "2",
            "conversion_action.name": "qualified_onboarding_complete",
            "conversion_action.primary_for_goal": False,
            "conversion_action.origin": "WEBSITE",
            "conversion_action.category": "SUBMIT_LEAD_FORM",
            "metrics.conversions": 40,
            "metrics.all_conversions": 40,
        },
    ]
    report = audit_conversion_actions(rows, spend_mtd=6647)
    payload = report.to_dict()
    assert "page_view" in report.micro_primaries
    assert payload["terminology"]["say_instead"] == "reported conversions"
    assert report.kpis["cpa_reported_conversions"] is not None
    assert report.kpis["cpqa"] is not None
    assert any("micro events marked primary" in line for line in report.narrative)
    assert "micro_event_is_primary_for_goal" in report.actions[0].risks
