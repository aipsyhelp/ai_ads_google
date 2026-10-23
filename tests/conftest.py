"""Isolate tests from the live account's on-disk recovery freeze."""
import pytest


@pytest.fixture(autouse=True)
def isolated_recovery_state(monkeypatch, tmp_path):
    from ads_ai_agents.adapters import recovery_stability

    monkeypatch.setattr(recovery_stability, "DEFAULT_STATE", tmp_path / "unconfigured.json")


@pytest.fixture(autouse=True)
def example_product_facts(monkeypatch):
    """Run tests against the committed example config, never a local product_facts.json."""
    from pathlib import Path

    from ads_ai_agents.platform import product_facts

    example = Path(__file__).resolve().parents[1] / "config" / "product_facts.example.json"
    monkeypatch.setenv("ADS_PRODUCT_FACTS_PATH", str(example))
    monkeypatch.setenv("ADS_ACCOUNT_IDS_PATH", str(example.with_name("account_ids.example.json")))
    product_facts.load_product_facts.cache_clear()
    yield
    product_facts.load_product_facts.cache_clear()


FAKE_DEV_TOKEN = "FAKE-DEV-TOKEN-0000"
FAKE_SA_EMAIL = "ads-agents-test@fake-project.iam.gserviceaccount.com"


def write_dummy_service_account(path):
    """A syntactically valid but fake service-account key (no real key material)."""
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "type": "service_account",
                "project_id": "fake-project",
                "client_email": FAKE_SA_EMAIL,
                "client_id": "000000000000000000000",
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def project(tmp_path, monkeypatch):
    """An isolated project root: config files live in tmp_path, env is restored afterwards."""
    import os

    from ads_ai_agents import config_store
    from ads_ai_agents.platform import account_ids, product_facts

    saved = dict(os.environ)
    monkeypatch.setenv("ADS_PROJECT_ROOT", str(tmp_path))
    monkeypatch.delenv("ADS_PRODUCT_FACTS_PATH", raising=False)
    monkeypatch.delenv("ADS_ACCOUNT_IDS_PATH", raising=False)
    for key in list(os.environ):
        if key.startswith(("GOOGLE_ADS_", "ADS_MAX", "ADS_REQUIRE", "ADS_BUDGET", "ADS_ASSET", "ADS_POSITIVE")) or key == "ADS_AUTOMATION_MODE":
            monkeypatch.delenv(key, raising=False)
    product_facts.load_product_facts.cache_clear()
    account_ids._load.cache_clear()
    paths = config_store.project_paths(tmp_path)
    config_store.copy_templates(paths)
    yield paths
    os.environ.clear()
    os.environ.update(saved)
    product_facts.load_product_facts.cache_clear()
    account_ids._load.cache_clear()


@pytest.fixture
def dashboard(project):
    """Running dashboard on 127.0.0.1:<random> with mocked Google Ads, GTM and website."""
    from fake_google_ads import fake_adapter
    from fake_gtm import SITE_WITH_GTM, FakeGtmApi, fake_site_opener

    from ads_ai_agents.ui.server import DashboardApp, DashboardServer

    gtm = FakeGtmApi()
    app = DashboardApp(
        project,
        adapter_factory=fake_adapter,
        gtm_api_factory=lambda: gtm,
        site_opener=fake_site_opener(SITE_WITH_GTM),
        token="test-ui-token",
    )
    server = DashboardServer(app, port=0).start()
    server.gtm = gtm
    yield server
    server.stop()
