from ads_ai_agents.intelligence.recommendation_firewall import (
    FirewallDecision,
    evaluate_recommendation,
)


def test_budget_requires_approval():
    result = evaluate_recommendation({"recommendation.type": "CAMPAIGN_BUDGET"})
    assert result.decision is FirewallDecision.APPROVAL_REQUIRED


def test_broad_match_rejected():
    result = evaluate_recommendation({"recommendation.type": "USE_BROAD_MATCH_KEYWORD"})
    assert result.decision is FirewallDecision.REJECT


def test_asset_hygiene_requires_approval():
    result = evaluate_recommendation({"recommendation.type": "RESPONSIVE_SEARCH_AD_ASSET"})
    assert result.decision is FirewallDecision.APPROVAL_REQUIRED


def test_rsa_rewrite_requires_approval():
    result = evaluate_recommendation({"recommendation.type": "RESPONSIVE_SEARCH_AD"})
    assert result.decision is FirewallDecision.APPROVAL_REQUIRED


def test_keyword_recommendation_requires_approval():
    result = evaluate_recommendation({"recommendation.type": "KEYWORD"})
    assert result.decision is FirewallDecision.APPROVAL_REQUIRED
