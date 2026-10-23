from fake_google_ads import fake_adapter

from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.intelligence.account_audit import run_full_audit
from ads_ai_agents.intelligence.report_store import ReportStore


def test_audit_with_mocked_search(tmp_path):
    settings = GoogleAdsSettings(
        developer_token="test-token",
        login_customer_id="YOUR_LOGIN_CUSTOMER_ID",
        customer_id="YOUR_CUSTOMER_ID",
        service_account_json=tmp_path / "sa.json",
    )
    (tmp_path / "sa.json").write_text("{}", encoding="utf-8")

    adapter = fake_adapter(settings)
    store = ReportStore(root=tmp_path / "reports")
    result = run_full_audit(adapter=adapter, store=store)
    assert "executive" in result
    assert (tmp_path / "reports").exists()
    assert result["executive"]["reported_conversions_mtd"] >= 0
