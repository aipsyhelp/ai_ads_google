from ads_ai_agents.intelligence.asset_publisher import GoogleAdsAssetPublisher
from ads_ai_agents.intelligence.brand_gate import brand_gate_text
from ads_ai_agents.intelligence.enterprise_asset_coverage import (
    _build_callout_specs,
    _build_snippet_specs,
    _rsa_descriptions,
    _rsa_hooks,
    _rsa_topic_hooks,
)


def test_ua_hooks_do_not_mix_english_campaign_copy():
    hooks = _rsa_hooks("UA")
    assert "Онлайн-ресурси підтримки" in hooks
    assert "Online Support Resources" not in hooks


def test_topic_hooks_keep_educational_search_relevance():
    hooks = _rsa_topic_hooks("EN", "dealing with panic attack")
    assert "Grounding Techniques" in hooks
    assert "Panic Attack Resources" in hooks
    assert len(hooks) >= 7
    assert all(len(hook) <= 30 for hook in hooks)
    assert all(brand_gate_text(hook)["pass"] for hook in hooks)


def test_asset_publisher_builds_locale_specific_hooks():
    # Copy generation has no account dependency and must work without secrets.
    from pathlib import Path
    from ads_ai_agents.adapters.settings import GoogleAdsSettings
    publisher = GoogleAdsAssetPublisher(GoogleAdsSettings('test', '123', '123', Path('/unused/test-key.json')))
    ua = publisher._hook_headlines(locale="UA")
    en = publisher._hook_headlines(locale="EN")
    assert ua
    assert en
    assert any(any("\u0400" <= char <= "\u04ff" for char in hook) for hook in ua)
    assert not any(any("\u0400" <= char <= "\u04ff" for char in hook) for hook in en)


def test_grant_assets_do_not_contain_direct_sale_copy():
    texts = _build_callout_specs("UA") + _build_callout_specs("EN")
    for snippet in _build_snippet_specs("UA") + _build_snippet_specs("EN"):
        texts.extend(snippet["values"])
    texts.extend(_rsa_descriptions("UA"))
    texts.extend(_rsa_descriptions("EN"))
    assert all(brand_gate_text(text)["pass"] for text in texts)


def test_topic_descriptions_are_relevant_without_policy_jargon():
    descriptions = _rsa_descriptions("EN", "burnout at work")
    assert len(descriptions) == 4
    assert "burnout" in descriptions[0].lower()
    assert all("claim" not in text.lower() for text in descriptions)
    assert all(len(text) <= 90 for text in descriptions)
    assert all(brand_gate_text(text)["pass"] for text in descriptions)


def test_brand_gate_blocks_direct_sale_copy():
    assert not brand_gate_text("From €4.99/mo")["pass"]
    assert not brand_gate_text("Підписка від €9.99")["pass"]
    assert not brand_gate_text("Web and mobile app")["pass"]
