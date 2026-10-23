"""Ads-safe public destinations — no auth-walled deep links.

Google Ads sitelinks fail destination crawl when URL requires login
(e.g. app.example.com/diary). Only publicly reachable surfaces may be used.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

# Path segment on www.example.com (UA locale uses /uk, not /ua).
LOCALE_PATH = {"UA": "uk", "EN": "en", "DE": "de", "LT": "lt"}


def locale_path(locale: str) -> str:
    return LOCALE_PATH.get((locale or "EN").upper(), "en")


def topic_article_url(locale: str, ad_group_name: str) -> str:
    """Return the most relevant owned article URL for a Search ad group."""
    name = (ad_group_name or "").lower()
    article_markers = (
        (19, ("academ", "student", "навчан")),
        (20, ("workplace", "work stress", "стрес на робот")),
        (21, ("anger", "гнів", "зліст")),
        (16, ("burnout", "burnt out", "вигоран")),
        (14, ("panic", "паніч")),
        (15, ("depress", "депрес")),
        (18, ("fear", "страх")),
        (17, ("anxiety", "тривож")),
        (22, ("stress", "стрес")),
    )
    article_id = next(
        (item_id for item_id, markers in article_markers if any(marker in name for marker in markers)),
        None,
    )
    lang = locale_path(locale)
    suffix = f"/{article_id}" if article_id is not None else ""
    return f"https://www.example.com/{lang}/news{suffix}"


def public_urls(facts: dict[str, Any] | None = None, *, locale: str = "UA") -> dict[str, str]:
    """Canonical public URL map for sitelinks / future campaign finals.

    When Ad Grants safe mode is on (default via grant_destinations.enforce_for_grant_account
    or ADS_GRANT_SAFE_DESTINATIONS=1), returns nonprofit mission URLs on example.com
    instead of SaaS landings / chat login.
    """
    facts = facts or {}
    landings = facts.get("landings") or {}
    stores = facts.get("stores") or {}
    ads = facts.get("ads_destinations") or {}
    loc = (locale or "UA").upper()
    lang = locale_path(loc)

    # Lazy import avoids circular deps with grant_compliance helpers.
    try:
        from ads_ai_agents.intelligence.grant_compliance import (
            grant_public_urls,
            grant_safe_mode_enabled,
        )

        if grant_safe_mode_enabled(facts):
            g = grant_public_urls(facts)
            landing = landings.get(loc) or f"https://www.example.com/{lang}"
            news = (ads.get("news") or {}).get(loc) or f"{landing}/news"
            corporate = (ads.get("corporate") or {}).get(loc) or f"{landing}/corporate"
            ngo = (ads.get("ngo") or {}).get(loc) or f"{landing}/ngo"
            # Canonical route: /privacy-policy is only a 301 alias, and ad final
            # URLs should land directly instead of through a redirect hop.
            privacy = f"https://www.example.com/{lang}/privacy"
            # Owned locale map: app-store and chat destinations collapse to the
            # public landing, while content/business pages retain their routes.
            return {
                "site_root": "https://www.example.com/",
                "chat_entry": landing,
                "landing": landing,
                "news": news,
                "corporate": corporate,
                "ngo": ngo,
                "android": landing,
                "ios": landing,
                "help_request": landing,
                "contact": ngo,
                "privacy": g.get("privacy") or privacy,
                "grant_safe": "1",
            }
    except Exception:  # noqa: BLE001
        pass

    site_root = ads.get("site_root") or "https://www.example.com/"
    chat_entry = ads.get("chat_entry") or "https://app.example.com/"
    lp = landings.get(loc) or f"https://www.example.com/{lang}"
    news = (ads.get("news") or {}).get(loc) or f"https://www.example.com/{lang}/news"
    corporate = (ads.get("corporate") or {}).get(loc) or f"https://www.example.com/{lang}/corporate"
    ngo = (ads.get("ngo") or {}).get(loc) or f"https://www.example.com/{lang}/ngo"
    android = (stores.get("android") or {}).get("url") or (
        ""
    )
    ios = (stores.get("ios") or {}).get("url") or ""

    return {
        "site_root": site_root.rstrip("/") + "/",
        "chat_entry": chat_entry.rstrip("/") + "/",
        "landing": lp,
        "news": news,
        "corporate": corporate,
        "ngo": ngo,
        "android": android,
        "ios": ios,
        "grant_safe": "0",
    }


def is_blocked_destination(url: str) -> bool:
    """True if URL is auth-walled, third-party, or otherwise unsafe for Grant ads."""
    raw = (url or "").strip()
    if not raw:
        return True
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").rstrip("/") or "/"

    try:
        from ads_ai_agents.intelligence.grant_compliance import grant_safe_mode_enabled, load_facts
        from ads_ai_agents.intelligence.grant_domain_gate import is_grant_allowed_url

        facts = load_facts()
        if grant_safe_mode_enabled(facts):
            # Strict: only approved owned domains (example.com by default).
            if not is_grant_allowed_url(raw, facts=facts):
                return True
    except Exception:  # noqa: BLE001
        # Fail closed on store domains even if gate import fails.
        if "play.google.com" in host or "apple.com" in host or "youtu" in host:
            return True

    if host in ("app.example.com", "www.app.example.com"):
        # Only the login/entry root is publicly usable without auth.
        return path != "/"

    # Legacy deep paths that Google flagged Destination not working
    blocked_paths = {"/diary", "/tests", "/my-analytics", "/faq", "/chat"}
    if path in blocked_paths:
        return True
    return False
