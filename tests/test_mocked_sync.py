from ads_ai_agents.adapters.google_ads_readonly import GoogleAdsReadOnlyAdapter
from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.intelligence.account_audit import run_full_audit
from ads_ai_agents.intelligence.report_store import ReportStore


class _FakeCustomer:
    id = 1234567890  # fake test customer id
    descriptive_name = "Test NGO"
    currency_code = "USD"
    time_zone = "Europe/Berlin"
    auto_tagging_enabled = True
    manager = False
    optimization_score = 0.9

    class conversion_tracking_setting:
        conversion_tracking_status = type("S", (), {"name": "CONVERSION_TRACKING_ENABLED"})()
        enhanced_conversions_for_leads_enabled = False
        accepted_customer_data_terms = True


class _FakeRow:
    def __init__(self, **attrs):
        for key, value in attrs.items():
            setattr(self, key, value)


def test_audit_with_mocked_search(tmp_path):
    settings = GoogleAdsSettings(
        developer_token="test-token",
        login_customer_id="YOUR_LOGIN_CUSTOMER_ID",
        customer_id="YOUR_CUSTOMER_ID",
        service_account_json=tmp_path / "sa.json",
    )
    (tmp_path / "sa.json").write_text("{}", encoding="utf-8")

    adapter = GoogleAdsReadOnlyAdapter(settings=settings)

    def fake_search(customer_id: str, query: str):
        q = " ".join(query.lower().split())
        if "from customer" in q and "auto_tagging" in q:
            return [_FakeRow(customer=_FakeCustomer())]
        if "from conversion_action" in q:
            action = type(
                "CA",
                (),
                {
                    "id": 1,
                    "name": "page_view",
                    "status": type("E", (), {"name": "ENABLED"})(),
                    "type": type("E", (), {"name": "WEBPAGE"})(),
                    "category": type("E", (), {"name": "PAGE_VIEW"})(),
                    "origin": type("E", (), {"name": "WEBSITE"})(),
                    "primary_for_goal": True,
                    "counting_type": type("E", (), {"name": "MANY_PER_CLICK"})(),
                    "click_through_lookback_window_days": 30,
                    "view_through_lookback_window_days": 1,
                    "include_in_conversions_metric": True,
                    "value_settings": type("V", (), {"default_value": 0, "always_use_default_value": False})(),
                },
            )()
            metrics = type(
                "M",
                (),
                {
                    "conversions": 100.0,
                    "all_conversions": 120.0,
                    "conversions_value": 0.0,
                    "cost_per_conversion": 2.5,
                    "cost_micros": 250_000_000,
                    "clicks": 500,
                    "impressions": 10000,
                    "ctr": 0.05,
                },
            )()
            return [_FakeRow(conversion_action=action, metrics=metrics)]
        if "from campaign" in q:
            campaign = type(
                "C",
                (),
                {
                    "id": 11,
                    "name": "Search Articles EN",
                    "status": type("E", (), {"name": "ENABLED"})(),
                    "serving_status": type("E", (), {"name": "SERVING"})(),
                    "advertising_channel_type": type("E", (), {"name": "SEARCH"})(),
                    "advertising_channel_sub_type": type("E", (), {"name": "UNSPECIFIED"})(),
                    "bidding_strategy_type": type("E", (), {"name": "MAXIMIZE_CONVERSIONS"})(),
                    "start_date": "2025-01-01",
                    "end_date": "2037-12-30",
                    "final_url_suffix": "",
                    "optimization_score": 0.8,
                    "network_settings": type(
                        "N",
                        (),
                        {
                            "target_google_search": True,
                            "target_search_network": True,
                            "target_content_network": False,
                        },
                    )(),
                    "maximize_conversions": type("M", (), {"target_cpa_micros": 0})(),
                    "target_cpa": type("T", (), {"target_cpa_micros": 0})(),
                    "target_roas": type("R", (), {"target_roas": 0})(),
                },
            )()
            budget = type(
                "B",
                (),
                {
                    "id": 1,
                    "name": "budget",
                    "amount_micros": 50_000_000,
                    "explicitly_shared": False,
                },
            )()
            metrics = type(
                "M",
                (),
                {
                    "cost_micros": 100_000_000,
                    "clicks": 80,
                    "impressions": 1000,
                    "conversions": 40.0,
                    "conversions_value": 0,
                    "ctr": 0.08,
                    "search_impression_share": 0.5,
                    "search_budget_lost_impression_share": 0.1,
                    "search_rank_lost_impression_share": 0.2,
                },
            )()
            return [_FakeRow(campaign=campaign, campaign_budget=budget, metrics=metrics)]
        return []

    adapter._search_impl = fake_search
    store = ReportStore(root=tmp_path / "reports")
    result = run_full_audit(adapter=adapter, store=store)
    assert "executive" in result
    assert (tmp_path / "reports").exists()
    assert result["executive"]["reported_conversions_mtd"] >= 0
