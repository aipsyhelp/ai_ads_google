from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any


def _default_path() -> Path:
    # <repo>/config/product_facts.json (repo root = parents[3] from this file).
    # ADS_PRODUCT_FACTS_PATH overrides it (tests point it at the example file).
    env = os.environ.get("ADS_PRODUCT_FACTS_PATH", "").strip()
    if env:
        return Path(env)
    from ads_ai_agents.config_store import project_paths

    return project_paths().facts


@lru_cache(maxsize=4)
def load_product_facts(path: str | None = None) -> dict[str, Any]:
    """Canonical product / funnel facts for CRO, Strategy, Measurement agents."""
    p = Path(path) if path else _default_path()
    if not p.is_file():
        return {"status": "missing", "path": str(p)}
    data = json.loads(p.read_text(encoding="utf-8"))
    data["status"] = "loaded"
    data["path"] = str(p)
    return data


def store_click_events(facts: dict[str, Any] | None = None) -> list[dict[str, str]]:
    facts = facts or load_product_facts()
    stores = facts.get("stores") or {}
    android = (stores.get("android") or {}).get("url") or ""
    ios = (stores.get("ios") or {}).get("url") or ""
    return [
        {
            "event": "click_store_android",
            "match": "play.google.com",
            "url": android,
            "ads_role": "secondary",
        },
        {
            "event": "click_store_ios",
            "match": "apps.apple.com",
            "url": ios,
            "ads_role": "secondary",
        },
    ]


def firetest_urls(*, gclid: str = "FIRETEST_GCLID_REPLACE") -> dict[str, str]:
    facts = load_product_facts()
    landings = facts.get("landings") or {}
    stores = facts.get("stores") or {}
    web = facts.get("web_app") or {}
    q = f"?gclid={gclid}&utm_source=google&utm_medium=cpc&utm_campaign=firetest"

    def with_q(url: str) -> str:
        return f"{url.rstrip('/')}{q}" if url else ""

    return {
        "lp_ua": with_q(str(landings.get("UA") or "")),
        "lp_en": with_q(str(landings.get("EN") or "")),
        "lp_de": with_q(str(landings.get("DE") or "")),
        "chat_login": with_q(str(web.get("login") or "")),
        "chat_home": str(web.get("home") or ""),
        "store_android": str((stores.get("android") or {}).get("url") or ""),
        "store_ios": str((stores.get("ios") or {}).get("url") or ""),
    }


def focus_cities(facts: dict[str, Any] | None = None) -> tuple[str, ...]:
    """City labels used in city-campaign names (product_facts.json → geo.focus_cities)."""
    facts = facts if facts is not None else load_product_facts()
    cities = ((facts.get("geo") or {}).get("focus_cities")) or ["City_A", "City_B", "City_C"]
    return tuple(str(c) for c in cities if str(c).strip())


_SEED_STOPWORDS = frozenset(
    """a an and are as at be by for from has have help helps in into is it its of on or our that the their them
    they this to us we with you your who what which will can more most all also about over than other via per
    business organization organisation nonprofit company website online people service services product products
    program programs offer offering free best""".split()
)


def organization_profile(facts: dict[str, Any] | None = None) -> dict[str, Any]:
    """Who/what we advertise (product_facts.json → organization, main_offering).

    Works for businesses and nonprofits alike. Planner, creative and competitor-discovery
    agents read this so the user's own description drives what gets advertised.
    """
    import re
    from urllib.parse import urlparse

    facts = facts if facts is not None else load_product_facts()
    org = facts.get("organization") or {}
    description = str(org.get("description") or "").strip()
    website_url = str(org.get("website_url") or "").strip()
    org_type = str(org.get("type") or "business").strip().lower()
    if org_type not in {"business", "nonprofit"}:
        org_type = "business"
    main_offering = str(facts.get("main_offering") or "").strip()
    domain = urlparse(website_url).hostname or "" if website_url else ""
    name = str(facts.get("product") or "").strip()

    words: list[str] = []
    for text in (main_offering, description, str(org.get("site_title") or ""), str(org.get("site_meta_description") or "")):
        for token in re.findall(r"[^\W\d_][\w'-]{2,}", text.lower()):
            token = token.strip("'-")
            if len(token) >= 4 and token not in _SEED_STOPWORDS and token not in words:
                words.append(token)
    stem = domain.removeprefix("www.").split(".")[0] if domain else ""
    seed_terms = ([main_offering.lower()] if main_offering else []) + words[:12]
    if stem and stem not in seed_terms:
        seed_terms.append(stem)
    return {
        "configured": bool(description or website_url or main_offering),
        "type": org_type,
        "name": name,
        "description": description,
        "website_url": website_url,
        "website_domain": domain,
        "main_offering": main_offering or (name if name and name != "YOUR_BRAND" else ""),
        "seed_terms": seed_terms,
    }
