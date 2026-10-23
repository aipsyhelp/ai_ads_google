from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class FirewallDecision(str, Enum):
    AUTO_APPLY_CANDIDATE = "auto_apply_candidate"
    APPROVAL_REQUIRED = "approval_required"
    REJECT = "reject"


AUTO_APPLY_TYPES = {
    "KEYWORD",
    "USELESS_KEYWORDS",  # placeholder names vary by API version
    "RESPONSIVE_SEARCH_AD",
    "IMPROVE_PERFORMANCE_MAX_AD_STRENGTH",
    "FORECASTING_SET_TARGET_CPA",  # still approval in our policy — not listed here
    "CALLOUT_ASSET",
    "SITELINK_ASSET",
    "CALL_ASSET",
}

# Recommendations are evidence only for this Grant account. Even asset and
# keyword "improvements" can reset learning or introduce restricted claims.
AUTO_APPLY_ALLOWLIST = {
}

APPROVAL_TYPES = {
    "CAMPAIGN_BUDGET",
    "MOVE_UNUSED_BUDGET",
    "FORECASTING_SET_TARGET_CPA",
    "TARGET_CPA_OPT_IN",
    "TARGET_ROAS_OPT_IN",
    "MAXIMIZE_CONVERSIONS_OPT_IN",
    "USE_BROAD_MATCH_KEYWORD",
    "PERFORMANCE_MAX",
    "SHOPPING_ADD_AGE_GROUP",
    "SET_TARGET_CPA",
    "MARGIN",
}

REJECT_MARKERS = (
    "BROAD_MATCH",
    "AUDIENCE_EXPANSION",
    "SENSITIVE",
    "DISPLAY_EXPANSION",
    "OPTIMIZE_AD_ROTATION",
)


@dataclass
class EvaluatedRecommendation:
    resource_name: str
    type: str
    campaign: str
    decision: FirewallDecision
    reason: str
    approval_required: bool
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["decision"] = self.decision.value
        return payload


def evaluate_recommendation(row: dict[str, Any]) -> EvaluatedRecommendation:
    rec_type = str(row.get("recommendation.type") or row.get("type") or "UNKNOWN")
    resource = str(row.get("recommendation.resource_name") or row.get("resource_name") or "")
    campaign = str(row.get("recommendation.campaign") or row.get("campaign") or "")
    upper = rec_type.upper()

    if any(marker in upper for marker in REJECT_MARKERS) or "BROAD" in upper:
        decision = FirewallDecision.REJECT
        reason = "Default reject: expansion/broad/sensitive automation without quality evidence."
    elif upper in APPROVAL_TYPES or "BUDGET" in upper or "TARGET_CPA" in upper or "PERFORMANCE_MAX" in upper:
        decision = FirewallDecision.APPROVAL_REQUIRED
        reason = "Budget, bidding, geo, URL expansion, PMax, or goal changes require human approval."
    elif "ASSET" in upper or "RSA" in upper or "KEYWORD" in upper or upper in AUTO_APPLY_ALLOWLIST:
        decision = FirewallDecision.APPROVAL_REQUIRED
        reason = (
            "Grant assets and keywords require evidence review; automatic edits "
            "reset learning and can introduce policy-sensitive copy."
        )
    else:
        decision = FirewallDecision.APPROVAL_REQUIRED
        reason = "Unknown recommendation type — default to approval-required."

    return EvaluatedRecommendation(
        resource_name=resource,
        type=rec_type,
        campaign=campaign,
        decision=decision,
        reason=reason,
        approval_required=decision is not FirewallDecision.AUTO_APPLY_CANDIDATE,
        evidence={"source": "google_ads_recommendation", "mode": "firewall_read_only"},
    )


def evaluate_recommendations(rows: list[dict[str, Any]]) -> list[EvaluatedRecommendation]:
    return [evaluate_recommendation(row) for row in rows if "error" not in row or len(row) > 1]
