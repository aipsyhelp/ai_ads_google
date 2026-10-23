from ads_ai_agents.platform.north_star import FunnelSnapshot, compute_revenue_kpis, diagnose_funnel_bottleneck


def test_cppu_undefined_when_zero_paying():
    funnel = FunnelSnapshot(period_days=30, ad_clicks=1000, ad_spend_usd=5000, paying_users=0)
    kpis = compute_revenue_kpis(funnel, ads_reported_conversions=2000)
    assert kpis.cost_per_paying_user is None
    assert kpis.ads_cpa_reported == 2.5
    assert "ads_cpa_reported" in kpis.forbidden_as_primary


def test_bottleneck_flags_zero_paying():
    funnel = FunnelSnapshot(
        period_days=30,
        ad_clicks=1000,
        ad_spend_usd=8000,
        landing_sessions=900,
        registrations=100,
        first_chat=20,
        paying_users=0,
    )
    findings = diagnose_funnel_bottleneck(funnel)
    assert any(f["code"] == "zero_paying_users" for f in findings)


def test_cppu_when_paying_exists():
    funnel = FunnelSnapshot(period_days=30, ad_spend_usd=1000, paying_users=10, ad_clicks=500)
    kpis = compute_revenue_kpis(funnel, ltv_usd=60)
    assert kpis.cost_per_paying_user == 100.0
    assert kpis.payback_days is not None
