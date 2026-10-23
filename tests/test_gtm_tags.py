"""Google Tags tab: parsing, config, plans, gating, mocked GTM API sequence, rollback, verify."""
import json

import pytest
from fake_gtm import SITE_WITH_GTM, SITE_WITHOUT_GTM, FakeGtmApi, fake_site_opener

from ads_ai_agents import config_store
from ads_ai_agents.intelligence import gtm_tags

GTM_SNIPPET = """<!-- Google Tag Manager -->
<script>(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':
new Date().getTime(),event:'gtm.js'});var f=d.getElementsByTagName(s)[0],
j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=
'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);
})(window,document,'script','dataLayer','GTM-FAKE123');</script>"""
GTAG_SNIPPET = """<script async src="https://www.googletagmanager.com/gtag/js?id=G-FAKE12345"></script>
<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}
gtag('js', new Date()); gtag('config', 'G-FAKE12345'); gtag('config', 'AW-123456789');
gtag('event', 'conversion', {'send_to': 'AW-123456789/FakeLabel01'});</script>"""


def test_parse_gtm_container_snippet():
    p = gtm_tags.parse_tag_input(GTM_SNIPPET)
    assert p["kind"] == "gtm_container_snippet" and p["gtm"] == ["GTM-FAKE123"] and p["ga4"] == [] and p["ads"] == []


def test_parse_gtag_snippet_with_conversion_label():
    p = gtm_tags.parse_tag_input(GTAG_SNIPPET)
    assert p["kind"] == "google_tag_snippet"
    assert p["ga4"] == ["G-FAKE12345"] and p["ads"] == ["AW-123456789"]
    assert p["ads_labels"] == {"AW-123456789": ["FakeLabel01"]}


def test_parse_plain_ids_and_invalid_tokens():
    p = gtm_tags.parse_tag_input("gtm-xxxxxxx, G-XXXXXXXXXX AW-123456789 GTM-1 AW-12")
    assert p["gtm"] == ["GTM-XXXXXXX"] and p["ga4"] == ["G-XXXXXXXXXX"] and p["ads"] == ["AW-123456789"]
    assert any("GTM-1" in w for w in p["warnings"])
    assert gtm_tags.parse_tag_input("hello")["warnings"] == ["No GTM-, G- or AW- IDs found."]


def test_ids_saved_to_config(project):
    config_store.save_section("tags", {"text": GTM_SNIPPET + GTAG_SNIPPET, "ads_conversion_labels": {"generate_lead": "FakeLabel01"}}, project)
    t = json.loads(project.facts.read_text())["tracking"]
    assert (t["gtm"], t["ga4"], t["ads_conversion_id"]) == ("GTM-FAKE123", "G-FAKE12345", "AW-123456789")
    assert t["ads_conversion_labels"] == {"generate_lead": "FakeLabel01"}
    view = config_store.config_view(project)["tags"]
    assert view["gtm"] == "GTM-FAKE123"
    config_store.save_section("gtm_container", {"account_id": "100", "container_id": "200", "container_public_id": "GTM-FAKE123"}, project)
    assert config_store.config_view(project)["tags"]["gtm_api"]["container_path"] == "accounts/100/containers/200"


@pytest.mark.parametrize("payload,field", [({"text": "nothing here"}, "text"), ({"gtm": "GTM-1"}, "gtm"), ({"ga4": "G-1", "text": ""}, "ga4"), ({"gtm": "GTM-FAKE123", "ads_conversion_labels": {"generate_lead": "bad label!"}}, "label_generate_lead")])
def test_tag_validation(project, payload, field):
    with pytest.raises(config_store.ConfigValidationError) as err:
        config_store.save_section("tags", payload, project)
    assert field in err.value.errors


def _facts(org_type, labels=None):
    return {
        "product": "Example",
        "main_offering": "coding classes" if org_type == "nonprofit" else "coffee mugs",
        "organization": {"type": org_type, "description": "fake", "website_url": "https://www.example.org"},
        "tracking": {"gtm": "GTM-FAKE123", "ga4": "G-FAKE12345", "ads_conversion_id": "AW-123456789", "ads_conversion_labels": labels or {}},
        "stores": {"android": {"url": "https://play.google.com/store/apps/details?id=com.example"}},
    }


def test_business_plan():
    plan = gtm_tags.build_tag_plan(_facts("business", {"purchase": "FakeLabel02"})).to_dict()
    names = [i["name"] for i in plan["items"]]
    assert [e["event"] for e in plan["key_events"]] == ["generate_lead", "sign_up", "purchase"]
    for expected in ("Consent Mode defaults", "Google tag (GA4)", "Conversion Linker", "GA4 key event purchase", "Google Ads conversion purchase", "GA4 event click_store_android"):
        assert gtm_tags.PREFIX + expected in names
    assert gtm_tags.PREFIX + "Google Ads conversion generate_lead" not in names  # no label yet
    assert any("generate_lead" in n for n in plan["notes"])
    consent = next(i for i in plan["items"] if i["name"].endswith("Consent Mode defaults"))
    assert consent["triggers"] == [gtm_tags.CONSENT_INIT_TRIGGER_ID]


def test_nonprofit_plan_uses_donation():
    plan = gtm_tags.build_tag_plan(_facts("nonprofit")).to_dict()
    assert [e["event"] for e in plan["key_events"]] == ["generate_lead", "sign_up", "donation"]
    assert gtm_tags.PREFIX + "GA4 key event donation" in [i["name"] for i in plan["items"]]
    assert any("Nonprofit" in n for n in plan["notes"])


def test_plan_without_ids_skips_tags():
    plan = gtm_tags.build_tag_plan({"tracking": {"gtm": "GTM-FAKE123", "ga4": "G-XXXXXXXXXX"}}).to_dict()
    assert all(i["body"]["type"] not in {"googtag", "awct", "gaawe"} for i in plan["items"])


def test_diff_marks_create_update_unchanged():
    plan = gtm_tags.build_tag_plan(_facts("business"))
    ga = next(i for i in plan.items if i.name.endswith("Google tag (GA4)"))
    linker = next(i for i in plan.items if i.name.endswith("Conversion Linker"))
    existing = {"tags": [{"name": ga.name, "type": "googtag", "parameter": [{"type": "template", "key": "tagId", "value": "G-OLD0000"}]}, {"name": linker.name, **linker.body}]}
    gtm_tags.diff_plan(plan, existing)
    assert (ga.action, linker.action) == ("update", "unchanged")
    assert plan.to_dict()["summary"]["update"] == 1


def _apply(project, api, confirm):
    return gtm_tags.apply_tag_plan(api, gtm_tags.build_tag_plan(_facts("business", {"purchase": "FakeLabel02"})), container_path=api.container, confirm=confirm, log_path=project.reports_dir / "gtm" / "changes.jsonl")


def test_read_only_mode_is_dry_run(project):
    api = FakeGtmApi()
    res = _apply(project, api, confirm=True)
    assert res["dry_run"] and not res["applied"] and "READ_ONLY" in res["reason"]
    assert api.calls == []
    assert gtm_tags.read_log(project.reports_dir / "gtm" / "changes.jsonl")[-1]["action"] == "dry_run"


def test_live_mode_still_needs_confirm(project):
    config_store.save_section("mode", {"mode": "CONTROLLED_LIVE", "confirm": config_store.LIVE_MODE_CONFIRMATION}, project)
    api = FakeGtmApi()
    res = _apply(project, api, confirm=False)
    assert res["dry_run"] and api.calls == []


def test_apply_call_sequence_and_rollback(project):
    config_store.save_section("mode", {"mode": "CONTROLLED_LIVE", "confirm": config_store.LIVE_MODE_CONFIRMATION}, project)
    api = FakeGtmApi(live_version_id="7")
    res = _apply(project, api, confirm=True)
    assert res["applied"] and res["published_version_id"] == "8" and res["rollback_version_id"] == "7"
    names = api.call_names()
    assert names[:3] == ["live_version", "create_workspace", "list_entities"]
    first = {k: names.index(k) for k in ("create_variable", "create_trigger", "create_tag", "create_version", "publish_version")}
    assert first["create_variable"] < first["create_trigger"] < first["create_tag"] < first["create_version"] < first["publish_version"]
    assert names[-2:] == ["create_version", "publish_version"]
    tag_bodies = [c for c in api.calls if c[0] == "create_tag"]
    assert len(tag_bodies) == 8  # consent, GA4, linker, 3 key events, 1 Ads conversion, 1 store click
    log = gtm_tags.read_log(project.reports_dir / "gtm" / "changes.jsonl")
    assert log[0]["action"] == "start" and log[0]["rollback_version_id"] == "7"
    assert log[-1]["action"] == "publish_version"

    rb = gtm_tags.rollback(api, container_path=api.container, version_id="7", confirm=True, log_path=project.reports_dir / "gtm" / "changes.jsonl")
    assert rb["applied"] and api.calls[-1] == ("publish_version", "accounts/100/containers/200/versions/7")
    config_store.save_section("mode", {"mode": "READ_ONLY"}, project)
    assert gtm_tags.rollback(api, container_path=api.container, version_id="7", confirm=True, log_path=project.reports_dir / "x.jsonl")["dry_run"]


def test_tag_firing_triggers_are_resolved(project):
    config_store.save_section("mode", {"mode": "CONTROLLED_LIVE", "confirm": config_store.LIVE_MODE_CONFIRMATION}, project)
    api = FakeGtmApi()
    captured = []
    original = api.create_tag
    api.create_tag = lambda ws, body: (captured.append(body), original(ws, body))[1]
    _apply(project, api, confirm=True)
    purchase = next(b for b in captured if b["name"].endswith("GA4 key event purchase"))
    assert purchase["firingTriggerId"] and purchase["firingTriggerId"][0].isdigit()
    assert next(b for b in captured if b["name"].endswith("Google tag (GA4)"))["firingTriggerId"] == [gtm_tags.ALL_PAGES_TRIGGER_ID]


def test_verify_snippet():
    off = gtm_tags.verify_site_snippet("https://www.example.org", "GTM-FAKE123")
    assert off["checked"] is False and "GTM-FAKE123" in off["install_snippet"]["head"]
    ok = gtm_tags.verify_site_snippet("https://www.example.org", "GTM-FAKE123", fetch=True, opener=fake_site_opener(SITE_WITH_GTM))
    assert ok["installed"] is True
    missing = gtm_tags.verify_site_snippet("https://www.example.org", "GTM-FAKE123", fetch=True, opener=fake_site_opener(SITE_WITHOUT_GTM))
    assert missing["installed"] is False and "install the snippet" in missing["message"]


def test_service_account_email_reads_only_client_email(tmp_path):
    from conftest import FAKE_SA_EMAIL, write_dummy_service_account

    from ads_ai_agents.adapters.gtm_api import service_account_email

    assert service_account_email(write_dummy_service_account(tmp_path / "sa.json")) == FAKE_SA_EMAIL
    assert service_account_email(tmp_path / "missing.json") == ""
