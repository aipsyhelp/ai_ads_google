"""HTTP API tests for the local dashboard (real server on 127.0.0.1, mocked Google APIs)."""
import json
import urllib.error
import urllib.request

import pytest
from conftest import FAKE_DEV_TOKEN, write_dummy_service_account

from ads_ai_agents import config_store
from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.ui.server import DashboardApp, DashboardServer


def call(server, method, path, body=None, *, token="test-ui-token", headers=None):
    hdrs = {"Content-Type": "application/json"}
    if token is not None:
        hdrs["X-UI-Token"] = token
    hdrs.update(headers or {})
    data = json.dumps(body).encode() if body is not None else (b"{}" if method == "POST" else None)
    req = urllib.request.Request(server.url.rstrip("/") + path, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), dict(e.headers)


def test_refuses_non_loopback_bind(project):
    with pytest.raises(ValueError):
        DashboardServer(DashboardApp(project), host="0.0.0.0", port=0)


def test_index_embeds_token_and_security_headers(dashboard):
    with urllib.request.urlopen(dashboard.url, timeout=10) as r:
        html = r.read().decode()
        assert 'content="test-ui-token"' in html
        assert "default-src 'self'" in r.headers["Content-Security-Policy"]
        assert r.headers["X-Frame-Options"] == "DENY"
    with urllib.request.urlopen(dashboard.url + "static/app.js", timeout=10) as r:
        assert r.headers["Content-Type"].startswith("text/javascript")


def test_api_requires_token_json_and_loopback_host(dashboard):
    assert call(dashboard, "GET", "/api/config", token=None)[0] == 403
    assert call(dashboard, "GET", "/api/config", token="wrong")[0] == 403
    assert call(dashboard, "GET", "/api/config", headers={"Host": "evil.example.com"})[0] == 403
    assert call(dashboard, "POST", "/api/config/mode", {"mode": "READ_ONLY"}, headers={"Content-Type": "text/plain"})[0] == 415
    assert call(dashboard, "GET", "/api/nope")[0] == 404


def test_save_round_trip_and_settings(dashboard, project):
    sa = write_dummy_service_account(project.secrets_dir / "sa.json")
    status, body, _ = call(dashboard, "POST", "/api/config/credentials", {"developer_token": FAKE_DEV_TOKEN, "service_account_json_path": str(sa)})
    assert status == 200 and body["ok"]
    assert FAKE_DEV_TOKEN not in json.dumps(body)
    status, body, _ = call(dashboard, "POST", "/api/config/account", {"login_customer_id": "000-000-0000", "customer_id": "000-000-0000"})
    assert body["settings"]["google_ads"] == {
        "ok": True, "developer_token": "set", "login_customer_id": "0000000000", "customer_id": "0000000000",
        "service_account_json": str(sa), "use_service_account": True,
    }
    assert GoogleAdsSettings.from_env().developer_token == FAKE_DEV_TOKEN
    assert "GOOGLE_ADS_DEVELOPER_TOKEN=" + FAKE_DEV_TOKEN in project.env.read_text()
    status, body, _ = call(dashboard, "GET", "/api/config")
    assert FAKE_DEV_TOKEN not in json.dumps(body) and body["config"]["credentials"]["developer_token_set"]


def test_validation_error_is_400(dashboard):
    status, body, _ = call(dashboard, "POST", "/api/config/account", {"login_customer_id": "1", "customer_id": "2"})
    assert status == 400 and set(body["errors"]) == {"login_customer_id", "customer_id"}


def test_mode_switch_needs_confirmation(dashboard):
    assert call(dashboard, "POST", "/api/config/mode", {"mode": "AUTONOMOUS_LIVE"})[0] == 400
    status, body, _ = call(dashboard, "POST", "/api/config/mode", {"mode": "AUTONOMOUS_LIVE", "confirm": config_store.LIVE_MODE_CONFIRMATION})
    assert status == 200 and body["settings"]["automation_mode"] == "AUTONOMOUS_LIVE"
    assert call(dashboard, "GET", "/api/status")[1]["mutations_enabled"] is True
    call(dashboard, "POST", "/api/config/mode", {"mode": "READ_ONLY"})
    assert call(dashboard, "GET", "/api/status")[1]["mode"] == "READ_ONLY"


def test_agents_endpoint_counts(dashboard):
    counts = call(dashboard, "GET", "/api/agents")[1]["counts"]
    assert counts == {"platform": 31, "ops": 10, "total": 41}


def test_plan_runs_read_only_with_organization(dashboard):
    call(dashboard, "POST", "/api/config/organization", {"type": "business", "description": "Handmade ceramic coffee mugs", "website_url": "https://shop.example.com", "main_offering": "ceramic coffee mugs"})
    status, body, _ = call(dashboard, "POST", "/api/run/plan", {"sample": "grant_search_campaign.example.json"})
    assert status == 200 and body["read_only"]
    assert body["mission"]["organization"]["main_offering"] == "ceramic coffee mugs"
    assert "ceramic coffee mugs alternatives" in body["mission"]["organization"]["competitor_search_seeds"]
    assert call(dashboard, "POST", "/api/run/plan", {"sample": "../../etc/passwd"})[0] == 400


def test_ping_and_audit_with_mocked_google_ads(dashboard, project):
    sa = write_dummy_service_account(project.secrets_dir / "sa.json")
    call(dashboard, "POST", "/api/config/credentials", {"developer_token": FAKE_DEV_TOKEN, "service_account_json_path": str(sa)})
    call(dashboard, "POST", "/api/config/account", {"login_customer_id": "0000000000", "customer_id": "0000000000"})
    ping = call(dashboard, "POST", "/api/ping")[1]
    assert ping["ok"] and ping["meta"]["descriptive_name"] == "Test NGO"
    audit = call(dashboard, "POST", "/api/run/audit")[1]
    assert audit["ok"] and audit["read_only"] and audit["executive"]["campaign_count"] == 1
    assert (project.root / audit["directory"] / "efficiency_dashboard.json").is_file()
    reports = call(dashboard, "GET", "/api/reports")[1]
    assert reports["latest"]["dashboard"] and reports["latest"]["executive"]["account"] == "Test NGO"


def test_ping_error_is_redacted(dashboard):
    body = call(dashboard, "POST", "/api/ping")[1]
    assert body["ok"] is False and "Missing Google Ads settings" in body["error"]


def test_gtm_flow_over_api(dashboard, project):
    sa = write_dummy_service_account(project.secrets_dir / "sa.json")
    call(dashboard, "POST", "/api/config/credentials", {"service_account_json_path": str(sa)})
    parsed = call(dashboard, "POST", "/api/gtm/parse", {"text": "GTM-FAKE123 G-FAKE12345 AW-123456789"})[1]["parsed"]
    assert parsed["gtm"] == ["GTM-FAKE123"]
    assert call(dashboard, "POST", "/api/config/tags", {"text": "GTM-FAKE123 G-FAKE12345 AW-123456789"})[0] == 200
    info = call(dashboard, "GET", "/api/gtm/connect-info")[1]
    assert info["service_account_email"].endswith("iam.gserviceaccount.com") and info["permission"] == "Publish"
    accounts = call(dashboard, "POST", "/api/gtm/accounts")[1]["accounts"]
    assert accounts[0]["containers"][0]["public_id"] == "GTM-FAKE123"
    call(dashboard, "POST", "/api/config/gtm_container", {"account_id": "100", "container_id": "200", "container_public_id": "GTM-FAKE123"})
    plan = call(dashboard, "POST", "/api/gtm/plan")[1]
    assert plan["connected"] and plan["plan"]["summary"]["create"] > 0
    dry = call(dashboard, "POST", "/api/gtm/apply", {"confirm": True})[1]
    assert dry["dry_run"] and "publish_version" not in dashboard.gtm.call_names()
    call(dashboard, "POST", "/api/config/mode", {"mode": "CONTROLLED_LIVE", "confirm": config_store.LIVE_MODE_CONFIRMATION})
    assert call(dashboard, "POST", "/api/gtm/apply", {"confirm": False})[1]["dry_run"]
    live = call(dashboard, "POST", "/api/gtm/apply", {"confirm": True})[1]
    assert live["applied"] and live["rollback_version_id"] == "7"
    assert dashboard.gtm.call_names()[-1] == "publish_version"
    rb = call(dashboard, "POST", "/api/gtm/rollback", {"confirm": True, "version_id": "7"})[1]
    assert rb["applied"]
    log = call(dashboard, "GET", "/api/gtm/log")[1]["entries"]
    assert [e["action"] for e in log][-1] == "rollback_publish"
    call(dashboard, "POST", "/api/config/organization", {"type": "business", "website_url": "https://www.example.org"})
    verify = call(dashboard, "POST", "/api/gtm/verify", {"fetch": True})[1]
    assert verify["installed"] is True
    assert call(dashboard, "POST", "/api/gtm/verify", {"fetch": False})[1]["checked"] is False
