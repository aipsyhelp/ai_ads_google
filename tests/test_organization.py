"""'Your organization or business' inputs → product_facts → agents (fake data only)."""
import json

import pytest
from fake_gtm import SITE_WITH_GTM, fake_site_opener

from ads_ai_agents import config_store
from ads_ai_agents.agents.assets import AssetAgent
from ads_ai_agents.core.loader import load_campaign_draft
from ads_ai_agents.core.orchestrator import AdsOrchestrator
from ads_ai_agents.platform.agents.competitor_discovery import discover_top_competitors
from ads_ai_agents.platform.product_facts import load_product_facts, organization_profile

SAMPLE = config_store.project_paths().samples_dir / "grant_search_campaign.example.json"
BUSINESS = {"type": "business", "description": "Handmade ceramic coffee mugs shipped across Europe", "website_url": "https://shop.example.com", "main_offering": "ceramic coffee mugs"}
NONPROFIT = {"type": "nonprofit", "description": "Free weekend coding classes for teenagers", "website_url": "www.example.org", "main_offering": "coding classes for teens"}


def test_business_inputs_round_trip(project):
    config_store.save_section("organization", BUSINESS, project)
    facts = json.loads(project.facts.read_text())
    assert facts["organization"]["description"] == BUSINESS["description"]
    assert facts["organization"]["website_url"] == "https://shop.example.com"
    assert facts["main_offering"] == "ceramic coffee mugs"
    org = organization_profile()
    assert org["type"] == "business" and org["main_offering"] == "ceramic coffee mugs"
    assert "ceramic" in org["seed_terms"] and "mugs" in org["seed_terms"]
    # Businesses cannot use Ad Grants → Grant mode switched off.
    assert config_store.load_tenant(project)["google_ads"]["is_ad_grants"] is False
    assert load_product_facts()["grant_destinations"]["enforce_for_grant_account"] is False
    assert config_store.loaded_settings(project)["organization"]["main_offering"] == "ceramic coffee mugs"


def test_nonprofit_links_to_ad_grants_mode(project):
    res = config_store.save_section("organization", NONPROFIT, project)
    assert any("Ad Grants" in w for w in res.warnings)
    assert config_store.load_tenant(project)["google_ads"]["is_ad_grants"] is True
    assert config_store.load_tenant(project)["organization_type"] == "nonprofit"
    assert load_product_facts()["organization"]["website_url"] == "https://www.example.org"
    config_store.save_section("organization", {**NONPROFIT, "use_ad_grants": False}, project)
    assert config_store.load_tenant(project)["google_ads"]["is_ad_grants"] is False


def test_either_field_is_enough(project):
    config_store.save_section("organization", {"type": "business", "description": "Bike repair workshop"}, project)
    config_store.save_section("organization", {"type": "business", "website_url": "example.com"}, project)
    assert organization_profile()["website_domain"] == "example.com"


@pytest.mark.parametrize(
    "payload,field",
    [
        ({"type": "business"}, "description"),
        ({"type": "club", "description": "x"}, "type"),
        ({"description": "x", "website_url": "javascript:alert(1)"}, "website_url"),
        ({"description": "x", "website_url": "https://someone@example.com"}, "website_url"),
        ({"description": "x" * 2001}, "description"),
    ],
)
def test_organization_validation(project, payload, field):
    with pytest.raises(config_store.ConfigValidationError) as err:
        config_store.save_section("organization", payload, project)
    assert field in err.value.errors


def test_metadata_fetch_is_off_by_default_and_mockable(project):
    opener = fake_site_opener(SITE_WITH_GTM)
    config_store.save_section("organization", NONPROFIT, project, opener=opener)
    assert opener.calls == []
    config_store.save_section("organization", {**NONPROFIT, "fetch_metadata": True}, project, opener=opener)
    assert opener.calls == ["https://www.example.org"]
    org = load_product_facts()["organization"]
    assert org["site_title"].startswith("Example Org")
    assert "teens" in organization_profile()["seed_terms"]


def test_competitor_discovery_uses_business_seeds(project):
    config_store.save_section("organization", BUSINESS, project)
    pack = discover_top_competitors()
    assert "ceramic coffee mugs alternatives" in pack["competitor_search_seeds"]
    assert "sites like shop.example.com" in pack["competitor_search_seeds"]
    assert "ceramic coffee mugs" in pack["product_seed_terms"]
    assert pack["organization"]["type"] == "business"


def test_competitor_discovery_uses_nonprofit_seeds(project):
    config_store.save_section("organization", NONPROFIT, project)
    seeds = discover_top_competitors()["competitor_search_seeds"]
    assert "organizations offering coding classes for teens" in seeds
    assert any(s.endswith("nonprofit organizations") for s in seeds)


def test_planner_and_creative_agent_show_main_offering(project):
    config_store.save_section("organization", NONPROFIT, project)
    mission = AdsOrchestrator().daily_cycle(load_campaign_draft(SAMPLE))
    assert "coding classes for teens" in mission.north_star
    assert mission.organization["main_offering"] == "coding classes for teens"
    assert "organizations offering coding classes for teens" in mission.organization["competitor_search_seeds"]
    actions = AssetAgent().recommend(load_campaign_draft(SAMPLE)).actions
    assert actions[0] == "Main offering to advertise (cause/program): coding classes for teens."
    assert any("Free weekend coding classes" in a for a in actions)


def test_cli_plan_prints_main_offering(project, capsys):
    from ads_ai_agents.cli import main

    config_store.save_section("organization", BUSINESS, project)
    assert main(["plan", str(SAMPLE)]) == 0
    out = capsys.readouterr().out
    assert "Main offering (business): ceramic coffee mugs" in out
    assert "ceramic coffee mugs alternatives" in out


def test_unconfigured_profile_keeps_defaults():
    org = organization_profile({"product": "YOUR_BRAND"})
    assert org["configured"] is False and org["main_offering"] == "" and org["type"] == "business"
    pack = discover_top_competitors()
    assert pack["count"] == 10
