"""Account-specific Google Ads resource IDs, loaded from config (never hardcoded).

Lookup order:
1. ``ADS_ACCOUNT_IDS_PATH`` environment variable
2. ``config/account_ids.json`` (gitignored, your real IDs)
3. ``config/account_ids.example.json`` (fake placeholder IDs, safe for tests/demos)
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any


def account_ids_path() -> Path:
    env = os.environ.get("ADS_ACCOUNT_IDS_PATH", "").strip()
    if env:
        return Path(env)
    from ads_ai_agents.config_store import project_paths

    paths = project_paths()
    return paths.account_ids if paths.account_ids.is_file() else paths.account_ids_example


@lru_cache(maxsize=4)
def _load(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def load_account_ids(path: str | Path | None = None) -> dict[str, Any]:
    return _load(str(path or account_ids_path()))


def customer_id(ids: dict[str, Any] | None = None) -> str:
    ids = ids if ids is not None else load_account_ids()
    return str(os.environ.get("GOOGLE_ADS_CUSTOMER_ID") or ids.get("customer_id") or "0000000000").replace("-", "")


def asset_resource(key: str, ids: dict[str, Any] | None = None) -> str:
    """Return ``customers/<cid>/assets/<id>`` for a configured asset, or "" if unset."""
    ids = ids if ids is not None else load_account_ids()
    asset_id = str((ids.get("assets") or {}).get(key) or "").strip()
    return f"customers/{customer_id(ids)}/assets/{asset_id}" if asset_id else ""
