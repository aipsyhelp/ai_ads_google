from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class FunnelSnapshot:
    """End-to-end growth funnel — Ads is only the top of the chain."""

    period_days: int
    ad_clicks: float = 0.0
    ad_spend_usd: float = 0.0
    landing_sessions: float = 0.0
    registrations: float = 0.0
    first_chat: float = 0.0
    returned_users: float = 0.0
    subscriptions_started: float = 0.0
    paying_users: float = 0.0
    revenue_usd: float = 0.0
    source: str = "manual_or_estimated"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def rates(self) -> dict[str, float | None]:
        def _r(num: float, den: float) -> float | None:
            return round(num / den, 4) if den else None

        return {
            "click_to_landing": _r(self.landing_sessions, self.ad_clicks),
            "landing_to_signup": _r(self.registrations, self.landing_sessions or self.ad_clicks),
            "signup_to_chat": _r(self.first_chat, self.registrations),
            "chat_to_return": _r(self.returned_users, self.first_chat),
            "return_to_subscribe": _r(self.subscriptions_started, self.returned_users or self.first_chat),
            "subscribe_to_paid": _r(self.paying_users, self.subscriptions_started or self.first_chat),
            "click_to_paid": _r(self.paying_users, self.ad_clicks),
        }


@dataclass(frozen=True)
class RevenueKpis:
    """North-star metrics. Raw Ads CPA is diagnostic only."""

    cost_per_paying_user: float | None
    cac: float | None
    ltv: float | None
    payback_days: float | None
    revenue_per_acquired_click: float | None
    activation_rate: float | None
    paid_conversion_rate: float | None
    ads_cpa_reported: float | None
    cpqa: float | None
    primary_kpi: str = "cost_per_paying_user"
    forbidden_as_primary: tuple[str, ...] = ("ads_cpa_reported", "ctr", "clicks")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_revenue_kpis(
    funnel: FunnelSnapshot,
    *,
    ltv_usd: float | None = None,
    qualified_activations: float | None = None,
    ads_reported_conversions: float | None = None,
) -> RevenueKpis:
    spend = funnel.ad_spend_usd
    paying = funnel.paying_users
    clicks = funnel.ad_clicks
    activations = qualified_activations if qualified_activations is not None else funnel.first_chat

    cppu = (spend / paying) if paying else None
    cac = cppu
    activation_rate = (activations / clicks) if clicks else None
    paid_rate = (paying / clicks) if clicks else None
    rev_per_click = (funnel.revenue_usd / clicks) if clicks else None
    ads_cpa = (spend / ads_reported_conversions) if ads_reported_conversions else None
    cpqa = (spend / activations) if activations else None

    payback = None
    if ltv_usd and cac and ltv_usd > 0:
        # Rough: days to recover CAC assuming LTV realized over 30d uniform — placeholder.
        daily = ltv_usd / 30.0
        payback = round(cac / daily, 1) if daily else None

    return RevenueKpis(
        cost_per_paying_user=round(cppu, 2) if cppu is not None else None,
        cac=round(cac, 2) if cac is not None else None,
        ltv=ltv_usd,
        payback_days=payback,
        revenue_per_acquired_click=round(rev_per_click, 4) if rev_per_click is not None else None,
        activation_rate=round(activation_rate, 4) if activation_rate is not None else None,
        paid_conversion_rate=round(paid_rate, 4) if paid_rate is not None else None,
        ads_cpa_reported=round(ads_cpa, 2) if ads_cpa is not None else None,
        cpqa=round(cpqa, 2) if cpqa is not None else None,
    )


def diagnose_funnel_bottleneck(funnel: FunnelSnapshot) -> list[dict[str, Any]]:
    """Rank where value dies — Ads is rarely the only answer when paying_users == 0."""
    rates = funnel.rates()
    findings: list[dict[str, Any]] = []

    if funnel.paying_users <= 0 and funnel.ad_spend_usd > 0:
        findings.append(
            {
                "severity": "critical",
                "stage": "payment",
                "code": "zero_paying_users",
                "message": (
                    f"${funnel.ad_spend_usd:,.0f} spend / {funnel.ad_clicks:,.0f} clicks produced "
                    "0 paying users — problem is in the value chain, not only Ads efficiency."
                ),
            }
        )

    checks = [
        ("landing_to_signup", "registration", "Weak click→signup — LP/offer/trust issue."),
        ("signup_to_chat", "first_chat", "Signups do not open chat — onboarding/CTA issue."),
        ("chat_to_return", "retention", "No return after first chat — product habit loop weak."),
        ("return_to_subscribe", "paywall", "Returns do not start subscription — value/price unclear."),
        ("subscribe_to_paid", "payment", "Subscribe starts but payment fails — checkout/friction."),
    ]
    for key, stage, msg in checks:
        rate = rates.get(key)
        if rate is None:
            continue
        if rate < 0.05:
            findings.append(
                {
                    "severity": "high",
                    "stage": stage,
                    "code": f"dropoff_{key}",
                    "message": f"{msg} Rate={rate:.1%}",
                    "rate": rate,
                }
            )
        elif rate < 0.15:
            findings.append(
                {
                    "severity": "medium",
                    "stage": stage,
                    "code": f"soft_dropoff_{key}",
                    "message": f"{msg} Rate={rate:.1%}",
                    "rate": rate,
                }
            )

    if not findings:
        findings.append(
            {
                "severity": "info",
                "stage": "observe",
                "code": "insufficient_funnel_data",
                "message": "Provide GA4/CRM funnel counts to locate the true bottleneck.",
            }
        )
    return findings
