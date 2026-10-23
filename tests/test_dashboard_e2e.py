"""Browser end-to-end tests for the local dashboard (Playwright + Chromium).

Optional: ``pip install -e ".[dev,e2e]" && python -m playwright install chromium``.
Skipped automatically when Playwright or its browser is not installed.
Only fake data; Google Ads, GTM and the website are mocked in-process.
"""
import json

import pytest
from conftest import FAKE_DEV_TOKEN, write_dummy_service_account

from ads_ai_agents.adapters.automation_mode import resolve_automation_mode
from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.intelligence.controlled_policy import load_controlled_policy

pytestmark = pytest.mark.e2e
sync_api = pytest.importorskip("playwright.sync_api", reason="Playwright not installed (optional e2e extra)")


@pytest.fixture(scope="module")
def browser():
    try:
        pw = sync_api.sync_playwright().start()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Playwright unavailable: {exc}")
    try:
        b = pw.chromium.launch()
    except Exception as exc:  # noqa: BLE001
        pw.stop()
        pytest.skip(f"Chromium not installed (python -m playwright install chromium): {exc}")
    yield b
    b.close()
    pw.stop()


@pytest.fixture
def ui(browser, dashboard):
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(dashboard.url)
    page.wait_for_selector("#loaded-settings:not(:empty)")
    yield page
    ctx.close()
    assert errors == [], errors


def nav(page, name):
    page.click(f'nav button[data-nav="{name}"]')


def save(page, section):
    page.click(f'[data-save="{section}"]')
    msg = page.locator(f"#msg-{section}")
    sync_api.expect(msg).not_to_have_text("Saving…")
    return msg.inner_text()


def test_setup_forms_write_files_and_settings(ui, project):
    sa = write_dummy_service_account(project.secrets_dir / "sa.json")
    nav(ui, "account")
    ui.fill("#form-credentials [name=developer_token]", FAKE_DEV_TOKEN)
    ui.fill("#form-credentials [name=service_account_json_path]", str(sa))
    assert "Saved" in save(ui, "credentials")
    assert ui.input_value("#form-credentials [name=developer_token]") == ""  # never echoed back
    assert "saved" in ui.get_attribute("#form-credentials [name=developer_token]", "placeholder")
    ui.fill("#form-account [name=login_customer_id]", "000-000-0000")
    ui.fill("#form-account [name=customer_id]", "000-000-0000")
    assert "Saved" in save(ui, "account")

    nav(ui, "site")
    ui.fill("#form-brand [name=brand]", "Example Org")
    ui.fill("#form-brand [name=domain]", "www.example.org")
    assert "Saved" in save(ui, "brand")

    nav(ui, "safety")
    ui.fill("#form-guards [name=max_budget_change_percent]", "8")
    ui.fill("#form-guards [name=max_account_daily_budget]", "250")
    assert "Saved" in save(ui, "guards")

    assert f"GOOGLE_ADS_DEVELOPER_TOKEN={FAKE_DEV_TOKEN}" in project.env.read_text()
    assert "GOOGLE_ADS_CUSTOMER_ID=0000000000" in project.env.read_text()
    assert json.loads(project.facts.read_text())["product"] == "Example Org"
    s = GoogleAdsSettings.from_env()
    assert (s.customer_id, s.developer_token) == ("0000000000", FAKE_DEV_TOKEN)
    p = load_controlled_policy()
    assert (p.max_budget_change_percent, p.max_account_daily_budget) == (8.0, 250.0)
    assert FAKE_DEV_TOKEN not in ui.content()

    # Mode switch needs the typed confirmation.
    ui.select_option("#form-mode [name=mode]", "CONTROLLED_LIVE")
    assert "confirm" in save(ui, "mode")
    assert resolve_automation_mode().value == "READ_ONLY"
    ui.fill("#form-mode [name=confirm]", "ENABLE LIVE WRITES")
    assert "Saved" in save(ui, "mode")
    sync_api.expect(ui.locator("#mode-pill")).to_have_text("CONTROLLED_LIVE")
    assert resolve_automation_mode().value == "CONTROLLED_LIVE"
    ui.select_option("#form-mode [name=mode]", "READ_ONLY")
    save(ui, "mode")
    sync_api.expect(ui.locator("#mode-pill")).to_have_text("READ_ONLY")
    assert "ADS_AUTOMATION_MODE=READ_ONLY" in project.env.read_text()


@pytest.mark.parametrize(
    "org_type,description,url,offering,seed",
    [
        ("nonprofit", "Free weekend coding classes for teenagers", "www.example.org", "coding classes for teens", "organizations offering coding classes for teens"),
        ("business", "Handmade ceramic coffee mugs shipped across Europe", "https://shop.example.com", "", "sites like shop.example.com"),
    ],
)
def test_organization_inputs_reach_the_plan(ui, project, org_type, description, url, offering, seed):
    nav(ui, "organization")
    ui.check(f"#form-organization [name=type][value={org_type}]")
    ui.fill("#form-organization [name=description]", description)
    ui.fill("#form-organization [name=website_url]", url)
    ui.fill("#form-organization [name=main_offering]", offering)
    assert "Saved" in save(ui, "organization")
    sync_api.expect(ui.locator("#org-usage")).to_contain_text(org_type)
    facts = json.loads(project.facts.read_text())
    assert facts["organization"]["description"] == description
    assert facts["organization"]["type"] == org_type

    nav(ui, "run")
    ui.click("#run-plan")
    sync_api.expect(ui.locator("#plan-seeds")).to_contain_text(seed)
    if offering:
        sync_api.expect(ui.locator("#plan-main-offering")).to_have_text(offering)
        sync_api.expect(ui.locator("#plan-north-star")).to_contain_text(offering)
    sync_api.expect(ui.locator("#plan-card")).to_contain_text("read-only")


def test_read_only_audit_with_mocked_google_ads_renders_reports(ui, project):
    sa = write_dummy_service_account(project.secrets_dir / "sa.json")
    nav(ui, "account")
    ui.fill("#form-credentials [name=developer_token]", FAKE_DEV_TOKEN)
    ui.fill("#form-credentials [name=service_account_json_path]", str(sa))
    save(ui, "credentials")
    ui.fill("#form-account [name=login_customer_id]", "0000000000")
    ui.fill("#form-account [name=customer_id]", "0000000000")
    save(ui, "account")
    nav(ui, "overview")
    ui.click("#run-ping")
    sync_api.expect(ui.locator("#ping-result")).to_contain_text("Test NGO")
    nav(ui, "run")
    ui.click("#run-audit")
    sync_api.expect(ui.locator("#audit-card")).to_contain_text("Test NGO")
    nav(ui, "reports")
    sync_api.expect(ui.locator("#reports-out")).to_contain_text("Efficiency dashboard")
    nav(ui, "agents")
    sync_api.expect(ui.locator("#agent-total")).to_have_text("41")


def test_google_tags_paste_connect_plan_apply(ui, project, dashboard):
    sa = write_dummy_service_account(project.secrets_dir / "sa.json")
    nav(ui, "account")
    ui.fill("#form-credentials [name=service_account_json_path]", str(sa))
    save(ui, "credentials")

    nav(ui, "tags")
    snippet = (
        "<script async src=\"https://www.googletagmanager.com/gtag/js?id=G-FAKE12345\"></script>"
        "<script>gtag('config','G-FAKE12345'); gtag('config','AW-123456789');</script>"
        "<script>(function(w,d,s,l,i){})(window,document,'script','dataLayer','GTM-FAKE123');</script>"
    )
    ui.fill("#form-tags [name=text]", snippet)
    ui.click("#tags-detect")
    for tag_id in ("GTM-FAKE123", "G-FAKE12345", "AW-123456789"):
        sync_api.expect(ui.locator("#tags-detected")).to_contain_text(tag_id)
    assert "Saved" in save(ui, "tags")
    assert json.loads(project.facts.read_text())["tracking"]["gtm"] == "GTM-FAKE123"

    sync_api.expect(ui.locator("#gtm-sa-email")).to_contain_text("iam.gserviceaccount.com")
    ui.click("#gtm-load-accounts")
    sync_api.expect(ui.locator("#gtm-container option")).to_have_count(1)
    assert "Saved" in save(ui, "gtm_container")

    ui.click("#gtm-plan")
    sync_api.expect(ui.locator("#gtm-plan-table")).to_contain_text("Consent Mode defaults")
    sync_api.expect(ui.locator("#gtm-plan-table")).to_contain_text("Conversion Linker")

    ui.check("#gtm-confirm")
    ui.click("#gtm-apply")
    sync_api.expect(ui.locator("#gtm-dry-run")).to_contain_text("READ_ONLY")
    assert "publish_version" not in dashboard.gtm.call_names()

    nav(ui, "safety")
    ui.select_option("#form-mode [name=mode]", "CONTROLLED_LIVE")
    ui.fill("#form-mode [name=confirm]", "ENABLE LIVE WRITES")
    save(ui, "mode")
    nav(ui, "tags")
    ui.check("#gtm-confirm")
    ui.click("#gtm-apply")
    sync_api.expect(ui.locator("#gtm-published")).to_contain_text("Published GTM version 8")
    names = dashboard.gtm.call_names()
    assert names.index("create_workspace") < names.index("create_tag") < names.index("create_version") < names.index("publish_version")

    nav(ui, "organization")
    ui.fill("#form-organization [name=website_url]", "https://www.example.org")
    ui.fill("#form-organization [name=description]", "Example org")
    save(ui, "organization")
    nav(ui, "tags")
    ui.check("#gtm-fetch")
    ui.click("#gtm-verify")
    sync_api.expect(ui.locator("#gtm-verify-status")).to_contain_text("Pass")
