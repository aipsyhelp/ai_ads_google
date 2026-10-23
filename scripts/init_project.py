#!/usr/bin/env python3
"""Interactive post-install wizard for Google Ads AI Automation.

Creates local-only files (.env, config/product_facts.json, config/tenant.json,
config/account_ids.json) from public templates. Never prints secret values back
to the terminal after input.

The file-writing logic lives in ``ads_ai_agents.config_store`` and is shared with
the local dashboard (``ads-agents ui``), so both always write the same files.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ads_ai_agents.config_store import (  # noqa: E402
    ConfigValidationError,
    WizardAnswers,
    apply_wizard,
    copy_templates,
    normalize_customer_id,
    project_paths,
)


def _prompt(label: str, default: str = "", *, required: bool = False, secret: bool = False) -> str:
    hint = f" [{default}]" if default else ""
    while True:
        if secret:
            import getpass

            raw = getpass.getpass(f"{label}{hint}: ").strip()
        else:
            raw = input(f"{label}{hint}: ").strip()
        value = raw or default
        if required and not value:
            print("  Required. Please enter a value.")
            continue
        if secret and value:
            print("  (stored locally — not echoed)")
        return value


def run_wizard(*, force: bool = False, non_interactive: bool = False, root: Path | None = None) -> int:
    paths = project_paths(root)
    print("=" * 64)
    print(" Google Ads AI Automation — setup wizard")
    print("=" * 64)
    print("This writes LOCAL files only (.env, config/*.json).")
    print("They are gitignored and must never be committed.")
    print("Prefer a browser? Run:  ads-agents ui\n")

    if paths.env.exists() and not force:
        print(f".env already exists at {paths.env}")
        if non_interactive:
            print("Use --force to overwrite.")
            return 1
        if _prompt("Overwrite .env? (y/N)", "N").lower() not in {"y", "yes"}:
            print("Aborted.")
            return 1

    if non_interactive:
        copy_templates(paths)
        print("Non-interactive: copied templates. Edit them (or run `ads-agents ui`), then run:")
        print("  PYTHONPATH=src python -m ads_ai_agents.cli ping")
        return 0

    answers = WizardAnswers(
        brand=_prompt("Brand / product name", "YOUR_BRAND", required=True),
        domain=_prompt("Primary website domain (no scheme)", "www.example.com", required=True),
        app_domain="",
        developer_token="",
        login_customer_id="",
        customer_id="",
        service_account_json="",
    )
    base = answers.domain.removeprefix("https://").removeprefix("http://").strip("/")
    answers.app_domain = _prompt("App / chat domain (optional)", f"app.{base.split('.', 1)[-1]}")
    answers.developer_token = _prompt("GOOGLE_ADS_DEVELOPER_TOKEN", required=True, secret=True)
    answers.login_customer_id = normalize_customer_id(_prompt("Manager (MCC) customer ID", required=True))
    answers.customer_id = normalize_customer_id(_prompt("Client account customer ID", required=True))
    answers.service_account_json = _prompt(
        "Path to service-account JSON",
        str(paths.secrets_dir / "google_ads_service_account.json"),
        required=True,
    )
    answers.daily_cap = _prompt("Max unique daily budget USD (Ad Grants ≈ 329)", "329")
    answers.is_grants = _prompt("Is this a Google Ad Grants account? (Y/n)", "Y").lower() not in {"n", "no"}

    copy_templates(paths)
    try:
        result = apply_wizard(answers, paths)
    except ConfigValidationError as exc:
        for key, msg in exc.errors.items():
            print(f"  ✗ {key}: {msg}")
        print("Nothing was written for the invalid values. Please run the wizard again.")
        return 1
    for path in result.written:
        print(f"Wrote {path}")

    print("\nNext steps:")
    print(f"  1. Place your Google Cloud service-account JSON at:\n     {answers.service_account_json}")
    print("  2. In Google Ads: Tools → API Center → link the service account / OAuth client.")
    print("  3. Verify API access (read-only):")
    print("       PYTHONPATH=src python -m ads_ai_agents.cli ping")
    print("  4. Open the local dashboard to review or change any setting:")
    print("       PYTHONPATH=src python -m ads_ai_agents.cli ui")
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
