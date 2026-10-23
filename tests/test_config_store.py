"""Config read/write and round-trip into the runtime settings objects (fake values only)."""
import json
import os

import pytest
from conftest import FAKE_DEV_TOKEN, write_dummy_service_account

from ads_ai_agents import config_store
from ads_ai_agents.adapters.automation_mode import AutomationMode, resolve_automation_mode
from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.intelligence.controlled_policy import load_controlled_policy
from ads_ai_agents.intelligence.report_store import ReportStore
from ads_ai_agents.platform.account_ids import account_ids_path, load_account_ids
from ads_ai_agents.platform.product_facts import focus_cities, load_product_facts


def _save(paths, section, payload):
    return config_store.save_section(section, payload, paths)


def test_paths_follow_project_root(project):
    assert config_store.project_root() == project.root
    assert account_ids_path() == project.account_ids
    assert ReportStore().root == project.reports_dir
    assert load_product_facts()["path"] == str(project.facts)


def test_copy_templates_never_overwrites(project):
    project.env.write_text("ADS_AUTOMATION_MODE=READ_ONLY\n", encoding="utf-8")
    assert config_store.copy_templates(project) == []
    assert project.env.read_text(encoding="utf-8") == "ADS_AUTOMATION_MODE=READ_ONLY\n"


def test_credentials_and_account_round_trip_into_google_ads_settings(project):
    sa = write_dummy_service_account(project.secrets_dir / "sa.json")
    _save(project, "credentials", {"developer_token": FAKE_DEV_TOKEN, "service_account_json_path": str(sa)})
    _save(project, "account", {"login_customer_id": "000-000-0000", "customer_id": "111-111-1111"})

    env = config_store.read_env(project)
    assert env["GOOGLE_ADS_DEVELOPER_TOKEN"] == FAKE_DEV_TOKEN
    assert env["GOOGLE_ADS_CUSTOMER_ID"] == "1111111111"
    tenant = json.loads(project.tenant.read_text())
    assert tenant["google_ads"] == {"login_customer_id": "0000000000", "customer_id": "1111111111", "is_ad_grants": True}
    assert json.loads(project.account_ids.read_text())["customer_id"] == "1111111111"

    s = GoogleAdsSettings.from_env()
    assert (s.developer_token, s.login_customer_id, s.customer_id) == (FAKE_DEV_TOKEN, "0000000000", "1111111111")
    assert s.service_account_json == sa
    assert load_account_ids()["customer_id"] == "1111111111"


def test_secrets_are_never_returned_by_the_view(project):
    _save(project, "credentials", {"developer_token": FAKE_DEV_TOKEN})
    _save(project, "tenant", {"timezone": "Europe/Berlin", "currency": "EUR", "locales": "EN, DE", "slack_webhook_url": "https://hooks.example.com/FAKE"})
    view = json.dumps(config_store.config_view(project))
    settings = json.dumps(config_store.loaded_settings(project))
    for secret in (FAKE_DEV_TOKEN, "hooks.example.com/FAKE"):
        assert secret not in view and secret not in settings
    assert config_store.config_view(project)["credentials"]["developer_token_set"] is True
    assert "hooks.example.com" not in project.tenant.read_text()  # webhook lives in .env only


def test_empty_token_keeps_stored_token(project):
    _save(project, "credentials", {"developer_token": FAKE_DEV_TOKEN})
    _save(project, "credentials", {"developer_token": "", "impersonated_email": "ops@example.org"})
    assert config_store.read_env(project)["GOOGLE_ADS_DEVELOPER_TOKEN"] == FAKE_DEV_TOKEN


def test_service_account_upload_goes_to_secrets_with_private_mode(project):
    content = json.dumps({"type": "service_account", "client_email": "x@fake-project.iam.gserviceaccount.com"})
    res = _save(project, "credentials", {"service_account_json_content": content})
    dest = project.secrets_dir / config_store.SERVICE_ACCOUNT_FILENAME
    assert str(dest) in res.written and dest.is_file()
    assert oct(dest.stat().st_mode & 0o777) == "0o600"
    assert config_store.read_env(project)["GOOGLE_ADS_SERVICE_ACCOUNT_JSON"] == str(dest)


def test_guards_round_trip_into_controlled_policy(project):
    _save(project, "guards", {"max_budget_change_percent": "7.5", "require_approval_above_percent": "12", "budget_cooldown_days": "2", "max_account_daily_budget": "250", "asset_publish": "approval", "positive_keywords": "auto_gated"})
    p = load_controlled_policy()
    assert (p.max_budget_change_percent, p.require_approval_above_percent, p.cooldown_days, p.max_account_daily_budget) == (7.5, 12.0, 2, 250.0)
    assert p.positive_keywords == "auto_gated"
    assert load_product_facts()["grant_policy"]["max_daily_budget_usd"] == 250.0


def test_brand_domains_tenant_round_trip_into_product_facts(project):
    _save(project, "brand", {"brand": "Example Org", "domain": "https://www.example.org/", "app_domain": "app.example.org"})
    _save(project, "domains", {"approved_domains": ["www.example.org", "example.org"], "preferred_grant_domain": "www.example.org", "unserved_hosts": "old.example.org"})
    _save(project, "tenant", {"timezone": "America/New_York", "currency": "usd", "locales": ["en", "es"], "primary_locale": "EN", "focus_cities": "Springfield\nRiverton"})
    facts = load_product_facts()
    assert facts["product"] == "Example Org"
    assert facts["grant_destinations"]["approved_domains"] == ["www.example.org", "example.org"]
    assert facts["grant_destinations"]["unserved_hosts"] == ["old.example.org"]
    assert facts["landings"]["EN"] == "https://www.example.org/en"
    assert focus_cities() == ("Springfield", "Riverton")
    tenant = config_store.load_tenant(project)
    assert (tenant["timezone"], tenant["currency"], tenant["locales"], tenant["display_name"]) == ("America/New_York", "USD", ["EN", "ES"], "Example Org")


def test_mode_requires_confirmation_for_live(project):
    with pytest.raises(config_store.ConfigValidationError) as err:
        _save(project, "mode", {"mode": "CONTROLLED_LIVE"})
    assert "confirm" in err.value.errors
    assert resolve_automation_mode() is AutomationMode.READ_ONLY
    _save(project, "mode", {"mode": "CONTROLLED_LIVE", "confirm": config_store.LIVE_MODE_CONFIRMATION})
    assert resolve_automation_mode() is AutomationMode.CONTROLLED_LIVE
    assert load_controlled_policy().mutations_enabled is True
    _save(project, "mode", {"mode": "READ_ONLY"})
    assert resolve_automation_mode() is AutomationMode.READ_ONLY


@pytest.mark.parametrize(
    "section,payload,field",
    [
        ("account", {"login_customer_id": "12345", "customer_id": "000-000-0000"}, "login_customer_id"),
        ("brand", {"brand": "", "domain": "example.org"}, "brand"),
        ("domains", {"approved_domains": ["not a domain"]}, "approved_domains"),
        ("tenant", {"timezone": "Mars/Base", "currency": "USD", "locales": "EN"}, "timezone"),
        ("guards", {"max_budget_change_percent": "20", "require_approval_above_percent": "10", "budget_cooldown_days": "1", "max_account_daily_budget": "10"}, "require_approval_above_percent"),
        ("account_ids", {"account_ids": '{"portfolio_strategies": {"primary": "abc"}}'}, "portfolio_strategies.primary"),
        ("account_ids", {"account_ids": "{broken"}, "account_ids"),
        ("credentials", {"service_account_json_content": '{"type": "authorized_user"}'}, "service_account_json_content"),
        ("mode", {"mode": "YOLO"}, "mode"),
    ],
)
def test_validation_rejects_bad_values_and_writes_nothing(project, section, payload, field):
    before = {p: p.read_text() for p in (project.env, project.facts, project.tenant, project.account_ids)}
    with pytest.raises(config_store.ConfigValidationError) as err:
        _save(project, section, payload)
    assert field in err.value.errors
    assert before == {p: p.read_text() for p in before}


def test_account_ids_round_trip(project):
    doc = {"customer_id": "000-000-0000", "portfolio_strategies": {"primary": "1000000009"}, "assets": {"logo": ""}}
    _save(project, "account_ids", {"account_ids": json.dumps(doc)})
    ids = load_account_ids()
    assert ids["customer_id"] == "0000000000"
    assert ids["portfolio_strategies"]["primary"] == "1000000009"


def test_env_writer_keeps_comments_and_appends_new_keys(project):
    config_store.write_env({"ADS_AUTOMATION_MODE": "READ_ONLY", "NEW_KEY": "x"}, project)
    text = project.env.read_text()
    assert "# --- Governance / kill switch ---" in text
    assert text.count("ADS_AUTOMATION_MODE=") == 1 and "NEW_KEY=x" in text
    with pytest.raises(config_store.ConfigValidationError):
        config_store.write_env({"X": "a\nB=1"}, project)


def test_wizard_logic_is_shared(project):
    sa = write_dummy_service_account(project.secrets_dir / "sa.json")
    answers = config_store.WizardAnswers(
        brand="Example Org", domain="www.example.org", developer_token=FAKE_DEV_TOKEN,
        login_customer_id="000-000-0000", customer_id="0000000000", service_account_json=str(sa),
        daily_cap="300", is_grants=False,
    )
    config_store.apply_wizard(answers, project)
    config_store.reload_runtime(project)
    assert GoogleAdsSettings.from_env().customer_id == "0000000000"
    assert load_controlled_policy().max_account_daily_budget == 300.0
    facts = load_product_facts()
    assert facts["grant_destinations"]["enforce_for_grant_account"] is False
    assert config_store.load_tenant(project)["tenant_id"] == "example-org"
    assert os.environ["ADS_AUTOMATION_MODE"] == "READ_ONLY"


def test_init_project_script_uses_config_store(project, monkeypatch):
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "init_project.py"
    spec = importlib.util.spec_from_file_location("init_project_test", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    project.env.unlink()
    assert mod.run_wizard(non_interactive=True) == 0
    assert project.env.is_file()
