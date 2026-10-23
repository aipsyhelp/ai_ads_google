import pytest

from ads_ai_agents.adapters.readonly_guard import ReadOnlyViolation
from ads_ai_agents.adapters.write_guard import assert_write_service


def test_goal_cleanup_allows_conversion_action_service():
    assert_write_service("ConversionActionService", scope="goal_cleanup")
    assert_write_service("GoogleAdsService", scope="goal_cleanup")


def test_goal_cleanup_forbids_budget_and_campaign():
    with pytest.raises(ReadOnlyViolation):
        assert_write_service("CampaignBudgetService", scope="goal_cleanup")
    with pytest.raises(ReadOnlyViolation):
        assert_write_service("CampaignService", scope="goal_cleanup")


def test_budget_pacing_allows_budget_and_campaign_not_upload():
    assert_write_service("CampaignBudgetService", scope="budget_pacing")
    assert_write_service("CampaignService", scope="budget_pacing")
    with pytest.raises(ReadOnlyViolation):
        assert_write_service("ConversionUploadService", scope="budget_pacing")


def test_controlled_live_keywords_scope():
    assert_write_service("CampaignCriterionService", scope="controlled_live_keywords")
    with pytest.raises(ReadOnlyViolation):
        assert_write_service("CampaignBudgetService", scope="controlled_live_keywords")


def test_grant_guardian_scope_is_structural_only():
    assert_write_service("RecommendationSubscriptionService", scope="grant_guardian")
    assert_write_service("AdGroupService", scope="grant_guardian")
    assert_write_service("AdGroupCriterionService", scope="grant_guardian")
    assert_write_service("CampaignAssetService", scope="grant_guardian")
    with pytest.raises(ReadOnlyViolation):
        assert_write_service("CampaignBudgetService", scope="grant_guardian")
    with pytest.raises(ReadOnlyViolation):
        assert_write_service("AdService", scope="grant_guardian")


def test_grant_keyword_normalize_scope_cannot_touch_ads_or_budgets():
    assert_write_service("AdGroupService", scope="grant_keyword_normalize")
    assert_write_service("AdGroupCriterionService", scope="grant_keyword_normalize")
    with pytest.raises(ReadOnlyViolation):
        assert_write_service("AdService", scope="grant_keyword_normalize")
    with pytest.raises(ReadOnlyViolation):
        assert_write_service("CampaignBudgetService", scope="grant_keyword_normalize")


def test_grant_delivery_recovery_scope_allows_delivery_controls():
    assert_write_service("GoogleAdsService", scope="grant_delivery_recovery")
    assert_write_service("ConversionActionService", scope="grant_delivery_recovery")
    assert_write_service("CustomerConversionGoalService", scope="grant_delivery_recovery")
    assert_write_service("CampaignService", scope="grant_delivery_recovery")
    assert_write_service("CampaignBudgetService", scope="grant_delivery_recovery")
    assert_write_service("AdService", scope="grant_delivery_recovery")
    with pytest.raises(ReadOnlyViolation):
        assert_write_service("ConversionUploadService", scope="grant_delivery_recovery")


def test_live_campaign_creation_requires_separate_approval(monkeypatch):
    monkeypatch.setenv("ADS_AUTOMATION_MODE", "CONTROLLED_LIVE")
    monkeypatch.delenv("ADS_ENABLE_CAMPAIGN_CREATION", raising=False)
    assert_write_service("GoogleAdsService", scope="campaign_create")
    with pytest.raises(ReadOnlyViolation):
        assert_write_service("CampaignService", scope="campaign_create")
    monkeypatch.setenv("ADS_ENABLE_CAMPAIGN_CREATION", "APPROVED_ONCE")
    assert_write_service("CampaignService", scope="campaign_create")


def test_live_asset_publish_requires_separate_approval(monkeypatch):
    monkeypatch.setenv("ADS_AUTOMATION_MODE", "AUTONOMOUS_LIVE")
    monkeypatch.delenv("ADS_ENABLE_ASSET_MUTATIONS", raising=False)
    assert_write_service("GoogleAdsService", scope="asset_publish")
    with pytest.raises(ReadOnlyViolation):
        assert_write_service("AdService", scope="asset_publish")
    monkeypatch.setenv("ADS_ENABLE_ASSET_MUTATIONS", "APPROVED_ONCE")
    assert_write_service("AdService", scope="asset_publish")
