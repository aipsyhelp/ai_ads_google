from __future__ import annotations

from collections.abc import Iterable

from ads_ai_agents.core.domain import (
    ApprovalState,
    CampaignDraft,
    GateDecision,
    PolicyFinding,
    Portfolio,
    RiskLevel,
)


MEDICAL_CLAIM_TERMS = (
    "diagnose",
    "diagnosis",
    "therapy",
    "therapist",
    "treatment",
    "treat depression",
    "cure",
    "panic attack treatment",
    "clinical depression",
    "mental illness",
    "suicidal",
)

SENSITIVE_AUDIENCE_MARKERS = (
    "depression audience",
    "anxiety audience",
    "mental health interest",
    "panic attacks audience",
    "health-sensitive",
    "remarketing from symptom pages",
)

SAFE_POSITIONING_TERMS = (
    "wellbeing",
    "self-help",
    "daily support",
    "stress",
    "burnout",
    "sleep",
    "emotional support",
    "reflection",
    "anonymous support",
)


def evaluate_campaign_policy(campaign: CampaignDraft) -> GateDecision:
    findings: list[PolicyFinding] = []

    if campaign.daily_budget_eur <= 0:
        findings.append(
            PolicyFinding(
                code="budget.invalid",
                message="Daily budget must be positive before a campaign can be queued.",
                risk=RiskLevel.HIGH,
                field="daily_budget_eur",
            )
        )

    if campaign.portfolio is Portfolio.GRANT_SEARCH:
        if campaign.daily_budget_usd > campaign.grant.max_daily_budget_usd:
            findings.append(
                PolicyFinding(
                    code="grant.budget_cap_review",
                    message=(
                        f"Grant Search daily budget ${campaign.daily_budget_usd:.0f} exceeds "
                        f"practical share ${campaign.grant.max_daily_budget_usd:.0f} of "
                        f"${campaign.grant.monthly_budget_usd:,.0f}/mo grant."
                    ),
                    risk=RiskLevel.MEDIUM,
                    field="daily_budget_eur",
                )
            )
        target = campaign.business_goal.effective_target_cpa_usd or campaign.grant.target_cpa_usd
        if target and target > 5:
            findings.append(
                PolicyFinding(
                    code="grant.cpa_target_loose",
                    message=(
                        f"Target CPA ${target:.2f} is loose vs YOUR_BRAND Grant north-star ~$2/lead. "
                        "Tighten primary conversion quality before scaling spend."
                    ),
                    risk=RiskLevel.MEDIUM,
                    field="business_goal.target_cpa_usd",
                )
            )

    if campaign.portfolio in {Portfolio.PERFORMANCE_MAX, Portfolio.APP_GROWTH}:
        findings.append(
            PolicyFinding(
                code="portfolio.paid_requires_signal_review",
                message="Paid/PMax/App portfolios require measurement readiness and human approval before launch.",
                risk=RiskLevel.MEDIUM,
                field="portfolio",
            )
        )

    if len(campaign.negative_keywords) < 5 and campaign.portfolio is Portfolio.GRANT_SEARCH:
        findings.append(
            PolicyFinding(
                code="keywords.negatives_thin",
                message="Grant Search needs a thicker shared negative architecture before broad/AI expansion.",
                risk=RiskLevel.MEDIUM,
                field="negative_keywords",
            )
        )

    all_copy = _campaign_text(campaign)
    findings.extend(_find_medical_claims(all_copy))
    findings.extend(_find_sensitive_audience_usage(campaign))
    findings.extend(_find_unsafe_cluster_language(campaign))
    findings.extend(_find_measurement_gaps(campaign))
    findings.extend(_find_performance_alerts(campaign))

    risk = _max_risk(f.risk for f in findings)
    state = _approval_state(findings)
    return GateDecision(state=state, risk=risk, findings=tuple(findings))


def _campaign_text(campaign: CampaignDraft) -> str:
    values: list[str] = [campaign.name, campaign.market, campaign.locale, campaign.business_goal.name]
    for cluster in campaign.intent_clusters:
        values.extend(cluster.jobs_to_be_done)
        values.extend(cluster.safe_language)
        values.extend(cluster.seed_keywords)
    for spec in campaign.asset_specs:
        values.extend(spec.headlines)
        values.extend(spec.descriptions)
        values.extend(spec.image_briefs)
        values.extend(spec.video_briefs)
        if spec.landing_page_url:
            values.append(spec.landing_page_url)
    values.extend(campaign.brand_exclusions)
    values.extend(campaign.competitors.brands)
    values.extend(str(value) for value in campaign.metadata.values())
    return "\n".join(values).lower()


def _find_medical_claims(text: str) -> list[PolicyFinding]:
    findings: list[PolicyFinding] = []
    for term in MEDICAL_CLAIM_TERMS:
        if term in text:
            findings.append(
                PolicyFinding(
                    code="policy.medical_claim",
                    message=f"Unsafe medical or clinical claim detected: '{term}'. Use wellness/self-help language.",
                    risk=RiskLevel.CRITICAL,
                    field="copy",
                )
            )
    return findings


def _find_sensitive_audience_usage(campaign: CampaignDraft) -> list[PolicyFinding]:
    audience_text = str(campaign.metadata.get("audiences", "")).lower()
    findings: list[PolicyFinding] = []
    for marker in SENSITIVE_AUDIENCE_MARKERS:
        if marker in audience_text:
            findings.append(
                PolicyFinding(
                    code="policy.sensitive_audience",
                    message=f"Advertiser-curated sensitive audience marker detected: '{marker}'.",
                    risk=RiskLevel.CRITICAL,
                    field="metadata.audiences",
                )
            )
    return findings


def _find_unsafe_cluster_language(campaign: CampaignDraft) -> list[PolicyFinding]:
    findings: list[PolicyFinding] = []
    for cluster in campaign.intent_clusters:
        safe_text = " ".join(cluster.safe_language).lower()
        if not any(term in safe_text for term in SAFE_POSITIONING_TERMS):
            findings.append(
                PolicyFinding(
                    code="policy.safe_positioning_missing",
                    message=f"Intent cluster '{cluster.name}' lacks safe wellness positioning terms.",
                    risk=RiskLevel.MEDIUM,
                    field=f"intent_clusters.{cluster.name}.safe_language",
                )
            )
    return findings


def _find_measurement_gaps(campaign: CampaignDraft) -> list[PolicyFinding]:
    goal = campaign.business_goal
    findings: list[PolicyFinding] = []
    if not goal.primary_conversion:
        findings.append(
            PolicyFinding(
                code="measurement.primary_conversion_missing",
                message="A primary conversion is required so Smart Bidding does not optimize to noisy activity.",
                risk=RiskLevel.HIGH,
                field="business_goal.primary_conversion",
            )
        )
    if campaign.portfolio in {Portfolio.PERFORMANCE_MAX, Portfolio.AI_MAX_SEARCH} and not goal.secondary_conversions:
        findings.append(
            PolicyFinding(
                code="measurement.quality_signal_missing",
                message="PMax/AI Max should include secondary quality signals before scaling.",
                risk=RiskLevel.MEDIUM,
                field="business_goal.secondary_conversions",
            )
        )
    return findings


def _find_performance_alerts(campaign: CampaignDraft) -> list[PolicyFinding]:
    perf = campaign.performance
    if perf is None:
        return []
    findings: list[PolicyFinding] = []
    target = campaign.business_goal.effective_target_cpa_usd or campaign.grant.target_cpa_usd
    if perf.conversion_outage:
        findings.append(
            PolicyFinding(
                code="ops.conversion_outage",
                message="Conversion outage detected — pause learning campaigns and protect grant spend.",
                risk=RiskLevel.CRITICAL,
                field="performance.conversion_outage",
            )
        )
    if perf.cpa_usd is not None and target and perf.cpa_usd > target * 2:
        findings.append(
            PolicyFinding(
                code="ops.cpa_breach",
                message=f"Observed CPA ${perf.cpa_usd:.2f} is >2x target ${target:.2f}. Trigger reallocation.",
                risk=RiskLevel.HIGH,
                field="performance.cpa_usd",
            )
        )
    if perf.disapproved_assets > 0:
        findings.append(
            PolicyFinding(
                code="ops.disapproved_assets",
                message=f"{perf.disapproved_assets} disapproved asset(s) need immediate creative/policy repair.",
                risk=RiskLevel.HIGH,
                field="performance.disapproved_assets",
            )
        )
    return findings


def _max_risk(risks: Iterable[RiskLevel]) -> RiskLevel:
    order = {
        RiskLevel.LOW: 0,
        RiskLevel.MEDIUM: 1,
        RiskLevel.HIGH: 2,
        RiskLevel.CRITICAL: 3,
    }
    return max(risks, key=lambda risk: order[risk], default=RiskLevel.LOW)


def _approval_state(findings: Iterable[PolicyFinding]) -> ApprovalState:
    findings_tuple = tuple(findings)
    if any(finding.risk is RiskLevel.CRITICAL for finding in findings_tuple):
        return ApprovalState.BLOCKED
    if findings_tuple:
        return ApprovalState.NEEDS_REVIEW
    return ApprovalState.APPROVED
