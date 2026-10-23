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
