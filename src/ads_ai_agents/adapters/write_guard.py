from __future__ import annotations

"""Narrow write scopes for enterprise controlled mutations."""

import os

from ads_ai_agents.adapters.automation_mode import mutations_allowed
from ads_ai_agents.adapters.readonly_guard import ReadOnlyViolation


GOAL_CLEANUP_ALLOWED_SERVICES = frozenset(
    {
        "GoogleAdsService",
        "ConversionActionService",
        "CustomerConversionGoalService",
        "CampaignConversionGoalService",
        "CustomConversionGoalService",
        "ConversionGoalCampaignConfigService",
    }
)

BUDGET_PACING_ALLOWED_SERVICES = frozenset(
    {
        "GoogleAdsService",
        "CampaignBudgetService",
        "CampaignService",
    }
)

CONTROLLED_LIVE_KEYWORDS_ALLOWED_SERVICES = frozenset(
    {
        "GoogleAdsService",
        "CampaignCriterionService",
        "AdGroupCriterionService",
        "SharedSetService",
        "SharedCriterionService",
        "CampaignSharedSetService",
    }
)

GRANT_GUARDIAN_ALLOWED_SERVICES = frozenset(
    {
        "GoogleAdsService",
        "RecommendationSubscriptionService",
        "AdGroupService",
        "AdGroupCriterionService",
        "CampaignAssetService",
    }
)

GRANT_KEYWORD_NORMALIZE_ALLOWED_SERVICES = frozenset(
    {
        "GoogleAdsService",
        "AdGroupService",
        "AdGroupCriterionService",
    }
)

GRANT_SEMANTIC_EXPAND_ALLOWED_SERVICES = frozenset(
    {
        "GoogleAdsService",
        "AdGroupCriterionService",
        # Read-only demand lookup; requires a Basic-access developer token.
        "KeywordPlanIdeaService",
    }
)

GRANT_DELIVERY_RECOVERY_ALLOWED_SERVICES = frozenset(
    {
        "GoogleAdsService",
        "ConversionActionService",
        "CustomerConversionGoalService",
        "CampaignConversionGoalService",
        "CustomConversionGoalService",
        "ConversionGoalCampaignConfigService",
        "CampaignService",
        # Shared $257 Grant budget is aligned to a portfolio Max Conversions
        # strategy; campaign-level tCPA mutates fail with BIDDING_STRATEGY_AND_
        # BUDGET_MUST_BE_ALIGNED until the portfolio itself is updated.
        "BiddingStrategyService",
        # Device bid_modifier=0 opts out of that device; clearing 0→1.0 is a
        # delivery recovery control (CampaignCriterion + AdGroupBidModifier).
        "CampaignCriterionService",
        "AdGroupBidModifierService",
        "AdGroupService",
        # Starved micro-budgets ($7/$15) cap Max Conversions room; raising them
        # within the Grant $329/day cap is a delivery recovery control.
        "CampaignBudgetService",
        # RSA relevance/Ad Strength improvements (headlines/descriptions) lift
        # Quality Score, which is an Ad Rank factor in the Grant auction.
        "AdService",
        "AdGroupAdService",
    }
)

GRANT_POLICY_REMEDIATION_ALLOWED_SERVICES = frozenset(
    {
        "GoogleAdsService",
        "CampaignService",
    }
)

ASSET_PUBLISH_ALLOWED_SERVICES = frozenset(
    {
        "GoogleAdsService",
        "AdGroupAdService",
        "AdService",
        "AssetGroupAssetService",
        "AssetService",
        "CampaignAssetService",
        "CustomerAssetService",
    }
)

CAMPAIGN_CREATE_ALLOWED_SERVICES = frozenset(
    {
        "GoogleAdsService",
        "CampaignBudgetService",
        "CampaignService",
        "CampaignCriterionService",
        "AdGroupService",
        "AdGroupCriterionService",
        "AdGroupAdService",
        "AssetService",
        "CampaignAssetService",
    }
)

GRANT_REACTIVATE_ALLOWED_SERVICES = frozenset(
    {
        "GoogleAdsService",
        "CampaignBudgetService",
        "CampaignService",
        "AdGroupService",
        "AdGroupCriterionService",
        "AdGroupAdService",
        "AdService",
        "AssetService",
        "CampaignAssetService",
        "CustomerAssetService",
        "AssetGroupService",
    }
)

CUSTOMER_MATCH_REMEDIATION_ALLOWED_SERVICES = frozenset(
    {
        "GoogleAdsService",
        "CustomerService",
        "AudienceService",
        "AssetGroupSignalService",
    }
)

SCOPES = {
    "goal_cleanup": GOAL_CLEANUP_ALLOWED_SERVICES,
    "budget_pacing": BUDGET_PACING_ALLOWED_SERVICES,
    "controlled_live_keywords": CONTROLLED_LIVE_KEYWORDS_ALLOWED_SERVICES,
    "grant_guardian": GRANT_GUARDIAN_ALLOWED_SERVICES,
    "grant_keyword_normalize": GRANT_KEYWORD_NORMALIZE_ALLOWED_SERVICES,
    "grant_semantic_expand": GRANT_SEMANTIC_EXPAND_ALLOWED_SERVICES,
    "grant_delivery_recovery": GRANT_DELIVERY_RECOVERY_ALLOWED_SERVICES,
    "grant_policy_remediation": GRANT_POLICY_REMEDIATION_ALLOWED_SERVICES,
    "asset_publish": ASSET_PUBLISH_ALLOWED_SERVICES,
    "campaign_create": CAMPAIGN_CREATE_ALLOWED_SERVICES,
    "grant_reactivate": GRANT_REACTIVATE_ALLOWED_SERVICES,
    "customer_match_remediation": CUSTOMER_MATCH_REMEDIATION_ALLOWED_SERVICES,
}


def assert_write_service(service_name: str, *, scope: str) -> None:
    allowed = SCOPES.get(scope)
    if allowed is None:
        raise ReadOnlyViolation(f"Unknown write scope '{scope}'.")
    if service_name not in allowed:
        raise ReadOnlyViolation(
            f"Write scope '{scope}' forbids '{service_name}'. Allowed: {sorted(allowed)}"
        )
    if not mutations_allowed():
        return
    if service_name not in {"GoogleAdsService", "KeywordPlanIdeaService"}:
        from ads_ai_agents.adapters.recovery_stability import assert_recovery_stable

        assert_recovery_stable()
    if (
        scope == "campaign_create"
        and service_name != "GoogleAdsService"
        and os.getenv("ADS_ENABLE_CAMPAIGN_CREATION", "").strip() != "APPROVED_ONCE"
    ):
        raise ReadOnlyViolation(
            "Live campaign creation requires "
            "ADS_ENABLE_CAMPAIGN_CREATION=APPROVED_ONCE."
        )
    if (
        scope == "asset_publish"
        and service_name != "GoogleAdsService"
        and os.getenv("ADS_ENABLE_ASSET_MUTATIONS", "").strip() != "APPROVED_ONCE"
    ):
        raise ReadOnlyViolation(
            "Live asset publishing requires "
            "ADS_ENABLE_ASSET_MUTATIONS=APPROVED_ONCE."
        )
