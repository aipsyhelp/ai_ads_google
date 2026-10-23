import pytest

from ads_ai_agents.adapters.readonly_guard import ReadOnlyViolation, assert_readonly_service


def test_google_ads_service_allowed():
    assert_readonly_service("GoogleAdsService")


def test_mutate_and_upload_forbidden():
    with pytest.raises(ReadOnlyViolation):
        assert_readonly_service("CampaignService")
    with pytest.raises(ReadOnlyViolation):
        assert_readonly_service("ConversionUploadService")
    with pytest.raises(ReadOnlyViolation):
        assert_readonly_service("OfflineUserDataJobService")
