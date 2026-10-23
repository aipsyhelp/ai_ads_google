from __future__ import annotations

from typing import Any

from ads_ai_agents.platform.north_star import FunnelSnapshot, RevenueKpis, compute_revenue_kpis


def run_revenue_review(
    funnel: FunnelSnapshot,
    *,
    ltv_usd: float | None = None,
    qualified_activations: float | None = None,
    ads_reported_conversions: float | None = None,
) -> dict[str, Any]:
    """Revenue Agent — forbids treating Ads CPA as the company north star."""
    kpis: RevenueKpis = compute_revenue_kpis(
        funnel,
        ltv_usd=ltv_usd,
        qualified_activations=qualified_activations,
        ads_reported_conversions=ads_reported_conversions,
    )
    warnings = []
    if kpis.ads_cpa_reported is not None and (kpis.cost_per_paying_user is None or funnel.paying_users == 0):
        warnings.append(
            "Ads CPA looks defined but CPPU is undefined/infinite — do not optimize campaigns to reported CPA."
        )
    if funnel.paying_users == 0:
        warnings.append("Primary KPI Cost-per-Paying-User cannot improve via more clicks alone.")

    return {
        "agent": "revenue",
        "primary_kpi": "cost_per_paying_user",
        "kpis": kpis.to_dict(),
        "warnings": warnings,
        "forbidden_optimizations": list(kpis.forbidden_as_primary),
        "required_inputs": ["payment_events", "ltv_or_arpu", "activation_events"],
        "status": "active_skeleton",
    }
