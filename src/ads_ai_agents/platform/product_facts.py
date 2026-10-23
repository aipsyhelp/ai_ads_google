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
    return Path(__file__).resolve().parents[3] / "config" / "product_facts.json"


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
