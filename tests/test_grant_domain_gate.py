import pytest

from ads_ai_agents.intelligence.grant_domain_gate import (
    assert_grant_url,
    blocked_hosts,
    gate_urls,
    is_grant_allowed_url,
)


FACTS = {
    "grant_destinations": {
        "approved_domains": ["example.org", "www.example.org"],
        "blocked_for_grant": [
            "https://www.example.com/",
            "https://app.example.com/",
        ],
    }
}


def test_strict_gate_allows_only_approved_nonprofit_destination():
    assert is_grant_allowed_url("https://www.example.org/help-request/", facts=FACTS)
    assert not is_grant_allowed_url("https://www.example.com/uk", facts=FACTS)
    assert not is_grant_allowed_url("https://app.example.com/", facts=FACTS)
    assert not is_grant_allowed_url("https://apps.apple.com/app/id1", facts=FACTS)


def test_blocked_for_grant_overrides_approved_domains():
    assert "example.com" in blocked_hosts(FACTS)
    assert not is_grant_allowed_url("https://www.example.com/en", facts=FACTS)


def test_third_party_only_mode_is_explicitly_less_strict():
    assert is_grant_allowed_url(
        "https://www.example.com/en",
        facts=FACTS,
        third_party_only=True,
    )
    assert not is_grant_allowed_url(
        "https://play.google.com/store/apps/details?id=x",
        facts=FACTS,
        third_party_only=True,
    )


def test_unserved_www_host_is_rejected_in_both_modes():
    facts = {
        "grant_destinations": {
            "approved_domains": ["example.org"],
            "unserved_hosts": ["www.example.org"],
        }
    }
    assert is_grant_allowed_url("https://example.org/uk", facts=facts)
    assert not is_grant_allowed_url("https://www.example.org/uk", facts=facts)
    assert not is_grant_allowed_url(
        "https://www.example.org/uk",
        facts=facts,
        third_party_only=True,
    )


def test_assert_grant_url_fails_closed():
    with pytest.raises(ValueError, match="domain gate blocked"):
        assert_grant_url("https://www.example.com/uk", facts=FACTS)


def test_gate_report_separates_allowed_and_blocked_urls():
    report = gate_urls(
        [
            "https://www.example.org/",
            "https://www.example.com/uk",
            "https://youtube.com/watch?v=x",
        ],
        facts=FACTS,
    )
    assert report.allowed == ["https://www.example.org/"]
    assert {row["host"] for row in report.blocked} == {
        "www.example.com",
        "youtube.com",
    }
