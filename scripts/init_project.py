#!/usr/bin/env python3
"""Interactive post-install wizard for Google Ads AI Automation.

Creates local-only files (.env, config/product_facts.json, config/tenant.json)
from public templates. Never prints secret values back to the terminal after input.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = ROOT / ".env.example"
ENV_PATH = ROOT / ".env"
FACTS_EXAMPLE = ROOT / "config" / "product_facts.example.json"
FACTS_PATH = ROOT / "config" / "product_facts.json"
TENANT_EXAMPLE = ROOT / "config" / "tenant.example.json"
TENANT_PATH = ROOT / "config" / "tenant.json"
SECRETS_DIR = ROOT / "secrets"


def _prompt(label: str, default: str = "", *, required: bool = False, secret: bool = False) -> str:
    hint = f" [{default}]" if default else ""
    while True:
        raw = input(f"{label}{hint}: ").strip()
        value = raw or default
        if required and not value:
            print("  Required. Please enter a value.")
            continue
        if secret and value:
            print("  (stored locally — not echoed)")
        return value


def _digits_only(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def _write_env(values: dict[str, str]) -> None:
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            out.append(line)
            continue
        key, _, _ = line.partition("=")
        key = key.strip()
        if key in values:
            out.append(f"{key}={values[key]}")
        else:
            out.append(line)
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


def _deep_set(data: dict, path: tuple[str, ...], value: object) -> None:
    cur: dict = data
    for key in path[:-1]:
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[key] = nxt
        cur = nxt
    cur[path[-1]] = value


def run_wizard(*, force: bool = False, non_interactive: bool = False) -> int:
    print("=" * 64)
    print(" Google Ads AI Automation — setup wizard")
    print("=" * 64)
    print("This writes LOCAL files only (.env, config/*.json).")
    print("They are gitignored and must never be committed.\n")

    if ENV_PATH.exists() and not force:
        print(f".env already exists at {ENV_PATH}")
        if non_interactive:
            print("Use --force to overwrite.")
            return 1
        if _prompt("Overwrite .env? (y/N)", "N").lower() not in {"y", "yes"}:
            print("Aborted.")
            return 1

    SECRETS_DIR.mkdir(parents=True, exist_ok=True)
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "var").mkdir(exist_ok=True)

    if non_interactive:
        if not ENV_PATH.exists():
            shutil.copy2(ENV_EXAMPLE, ENV_PATH)
        if not FACTS_PATH.exists():
            shutil.copy2(FACTS_EXAMPLE, FACTS_PATH)
        if not TENANT_PATH.exists():
            shutil.copy2(TENANT_EXAMPLE, TENANT_PATH)
        print("Non-interactive: copied templates. Edit .env and config/*.json, then run:")
        print("  PYTHONPATH=src python -m ads_ai_agents.cli ping")
        return 0

    brand = _prompt("Brand / product name", "YOUR_BRAND", required=True)
    domain = _prompt("Primary website domain (no scheme)", "www.example.com", required=True)
    domain = domain.removeprefix("https://").removeprefix("http://").strip("/")
    site = f"https://{domain}"
    app_domain = _prompt("App / chat domain (optional)", f"app.{domain.split('.', 1)[-1]}")
    app_url = f"https://{app_domain.removeprefix('https://').removeprefix('http://')}"

    developer_token = _prompt("GOOGLE_ADS_DEVELOPER_TOKEN", required=True, secret=True)
    login_customer = _digits_only(_prompt("Manager (MCC) customer ID", required=True))
    customer_id = _digits_only(_prompt("Client account customer ID", required=True))
    sa_path = _prompt(
        "Path to service-account JSON",
        str(SECRETS_DIR / "google_ads_service_account.json"),
        required=True,
    )
    daily_cap = _prompt("Max unique daily budget USD (Ad Grants ≈ 329)", "329")
    is_grants = _prompt("Is this a Google Ad Grants account? (Y/n)", "Y").lower() not in {"n", "no"}

    _write_env(
        {
            "GOOGLE_ADS_DEVELOPER_TOKEN": developer_token,
            "GOOGLE_ADS_LOGIN_CUSTOMER_ID": login_customer,
            "GOOGLE_ADS_CUSTOMER_ID": customer_id,
            "GOOGLE_ADS_USE_SERVICE_ACCOUNT": "true",
            "GOOGLE_ADS_SERVICE_ACCOUNT_JSON": sa_path,
            "ADS_AUTOMATION_MODE": "READ_ONLY",
            "ADS_MAX_ACCOUNT_DAILY_BUDGET": daily_cap,
        }
    )
    print(f"Wrote {ENV_PATH}")

    facts = json.loads(FACTS_EXAMPLE.read_text(encoding="utf-8"))
    facts["product"] = brand
    facts["landings"] = {
        "EN": f"{site}/en",
        "UA": f"{site}/uk",
        "DE": f"{site}/de",
    }
    facts["ads_destinations"]["site_root"] = site.rstrip("/") + "/"
    facts["ads_destinations"]["chat_entry"] = app_url.rstrip("/") + "/"
    for key in ("news", "corporate", "ngo"):
        facts["ads_destinations"][key] = {
            "EN": f"{site}/en/{key}",
            "UA": f"{site}/uk/{key}",
            "DE": f"{site}/de/{key}",
        }
    gd = facts["grant_destinations"]
    gd["approved_domains"] = [domain, domain.removeprefix("www.")]
    gd["preferred_grant_domain"] = domain
    gd["home"] = f"{site}/en"
    gd["help_request"] = f"{site}/en"
    gd["contact"] = f"{site}/en/contact"
    gd["privacy"] = f"{site}/en/privacy"
    gd["about_ngo"] = f"{site}/en/about"
    gd["enforce_for_grant_account"] = is_grants
    facts["web_app"]["login"] = app_url.rstrip("/") + "/"
    facts["web_app"]["home"] = app_url.rstrip("/") + "/home"
    facts["grant_policy"]["max_daily_budget_usd"] = float(daily_cap)
    FACTS_PATH.write_text(json.dumps(facts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {FACTS_PATH}")

    tenant = json.loads(TENANT_EXAMPLE.read_text(encoding="utf-8"))
    tenant["tenant_id"] = re.sub(r"[^a-z0-9-]+", "-", brand.lower()).strip("-") or "tenant"
    tenant["display_name"] = brand
    tenant["google_ads"]["login_customer_id"] = login_customer
    tenant["google_ads"]["customer_id"] = customer_id
    tenant["google_ads"]["is_ad_grants"] = is_grants
    TENANT_PATH.write_text(json.dumps(tenant, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {TENANT_PATH}")

    print("\nNext steps:")
    print(f"  1. Place your Google Cloud service-account JSON at:\n     {sa_path}")
    print("  2. In Google Ads: Tools → API Center → link the service account / OAuth client.")
    print("  3. Verify API access (read-only):")
    print("       PYTHONPATH=src python -m ads_ai_agents.cli ping")
    print("  4. List agents / open dashboard:")
    print("       PYTHONPATH=src python -m ads_ai_agents.cli platform agents")
    print("       PYTHONPATH=src python -m ads_ai_agents.cli dashboard")
    print("  5. Keep ADS_AUTOMATION_MODE=READ_ONLY until you explicitly enable live writes.")
    print("\nDone.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Initialize local tenant config for this repo.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing .env / config files.")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Only copy templates; you edit values manually.",
    )
    args = parser.parse_args(argv)
    try:
        return run_wizard(force=args.force, non_interactive=args.non_interactive)
    except KeyboardInterrupt:
        print("\nAborted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
