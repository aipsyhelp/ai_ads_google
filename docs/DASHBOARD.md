# Local dashboard (`ads-agents ui`)

A small local web app for setting everything up and reading the reports. It uses only the
Python standard library (`http.server`), runs on **your computer** and listens on
**127.0.0.1 only**.

```bash
ads-agents ui                 # opens http://127.0.0.1:8765
ads-agents ui --port 9000 --no-browser
# without installing the CLI:
PYTHONPATH=src python -m ads_ai_agents.cli ui
```

## Where every field is saved

The dashboard, the setup wizard (`scripts/init_project.py`) and the agents all use
`ads_ai_agents/config_store.py`, so they always read and write the same files. All of these
files are gitignored.

| Dashboard tab / field | File → key | Read by |
| --- | --- | --- |
| **Your organization or business**: type (Business / Nonprofit (NGO)), description, website, main offering | `config/product_facts.json` → `organization.type`, `organization.description`, `organization.website_url`, `main_offering` | `organization_profile()` → planner north star, creative (Asset) agent, competitor discovery seeds |
| …“Use Google Ad Grants mode” (nonprofits only) | `config/tenant.json` → `google_ads.is_ad_grants`; `product_facts.json` → `grant_destinations.enforce_for_grant_account` | Grant domain gate, compliance checks |
| …“Read my website's title/description” (off by default) | `product_facts.json` → `organization.site_title`, `organization.site_meta_description` | seed terms |
| **Google Tags**: GTM / GA4 / Google Ads IDs, conversion labels | `product_facts.json` → `tracking.gtm`, `tracking.ga4`, `tracking.ads_conversion_id`, `tracking.ads_conversion_labels` | `gtm_tags.build_tag_plan()` |
| **Google Tags**: GTM account / container | `product_facts.json` → `tracking.gtm_api.*` (IDs only) | GTM API adapter |
| **Account & credentials**: developer token | `.env` → `GOOGLE_ADS_DEVELOPER_TOKEN` (write-only, never shown again) | `GoogleAdsSettings.from_env()` |
| …service-account key (path or upload) | `.env` → `GOOGLE_ADS_SERVICE_ACCOUNT_JSON`; uploads go to `secrets/google_ads_service_account.json` (mode 600) | `GoogleAdsSettings`, GTM API |
| …MCC / client customer ID | `.env` → `GOOGLE_ADS_LOGIN_CUSTOMER_ID`, `GOOGLE_ADS_CUSTOMER_ID`; mirrored to `tenant.json` and `account_ids.json` → `customer_id` | `GoogleAdsSettings`, `load_account_ids()` |
| **Website & domains**: brand, primary/app domain | `product_facts.json` → `product`, `landings`, `ads_destinations`, `grant_destinations.*`, `web_app`; `tenant.json` → `tenant_id`, `display_name` | `load_product_facts()` |
| …approved domains, unserved hosts | `product_facts.json` → `grant_destinations.approved_domains`, `preferred_grant_domain`, `unserved_hosts` | Grant domain gate |
| **Locales & tenant**: time zone, currency, locales, primary locale | `tenant.json` | `load_tenant()` |
| …focus cities | `product_facts.json` → `geo.focus_cities` | `focus_cities()` (city rules, pacing) |
| …alert e-mail / Slack webhook | `.env` → `ALERT_EMAIL`, `SLACK_WEBHOOK_URL` (webhook write-only) | alerts |
| **Safety**: guard limits | `.env` → `ADS_MAX_BUDGET_CHANGE_PERCENT`, `ADS_REQUIRE_APPROVAL_ABOVE_PERCENT`, `ADS_BUDGET_COOLDOWN_DAYS`, `ADS_MAX_ACCOUNT_DAILY_BUDGET`, `ADS_ASSET_PUBLISH`, `ADS_POSITIVE_KEYWORDS`, `REQUIRE_HUMAN_APPROVAL`; daily cap mirrored to `grant_policy.max_daily_budget_usd` | `load_controlled_policy()` |
| **Safety**: automation mode | `.env` → `ADS_AUTOMATION_MODE` (live modes need the typed phrase `ENABLE LIVE WRITES`) | `resolve_automation_mode()` |
| **Account IDs** (JSON editor) | `config/account_ids.json` | `load_account_ids()` |

After every save the dashboard reloads `.env` into the running process and clears the loader
caches. The **Overview** tab then shows what the loaders return (secrets only as “set”).

## Views and actions

* **Overview**: mode, credentials, limits, local files, a read-only **ping**, and the loaded settings.
* **Run (read-only)**: the 10-agent **plan** on a sample campaign (no Google Ads calls) and the read-only **audit** (reads the account, then writes `efficiency_dashboard.json`).
* **Reports**: the latest `reports/YYYY-MM-DD/efficiency_dashboard.json` and executive summary.
* **Agents**: the 41 agents (31 platform + 10 Google Ads ops).

## Google Tags (GTM / Google tag)

1. **Paste** a GTM container snippet, a Google tag (gtag.js) snippet, or just the IDs (`GTM-…`, `G-…`, `AW-…`). The IDs are extracted and validated.
2. **Connect**: GTM uses the same service account as Google Ads. Add the e-mail shown in the tab in **GTM → Admin → User Management** with **Publish** permission, then pick the container. Needs `pip install -e ".[gtm]"`.
3. **Plan**: the agent builds the tags. It adds Consent Mode v2 defaults, the Google tag (GA4), a Conversion Linker, GA4 key events and Google Ads conversion tags. Businesses get `generate_lead`, `sign_up` and `purchase`; nonprofits get `generate_lead`, `sign_up` and `donation`. App-store clicks from [`GTM_STORE_CLICKS.md`](GTM_STORE_CLICKS.md) are included too. The plan shows what will be created, updated or left unchanged.
4. **Apply**: in `READ_ONLY` this is a **dry run**. In a live mode, after you tick the confirmation, the agent creates a GTM workspace, adds the variables, triggers and tags, creates a version and publishes it. Every call is logged to `reports/gtm/changes.jsonl`. The previous live version id is kept, so **Roll back** re-publishes it.
5. **Verify**: optionally fetches your homepage to check that the container snippet is installed. If it is missing, the tab shows the one-time install snippet.

> **Honest note:** GTM can only change tags on pages that already load the container. If your site does not load it yet, paste the snippet once (or use your CMS's GTM plugin). After that, every tag change in this tab is automated.

Your site still has to push the key events, for example `dataLayer.push({event: "generate_lead"})` on the thank-you page. Most form and donation plugins can do this.

## Security

* Binds to loopback only. Other hosts are refused.
* Every API call needs a per-run token that is embedded in the page. The `Host` header must be a loopback name.
* Responses never contain the developer token, the webhook URL or the contents of the key file.
* No external scripts, fonts or CDNs. The CSP is `default-src 'self'`.
* All Google Ads access goes through the read-only adapter. The only write path is GTM publishing, which is gated by `ADS_AUTOMATION_MODE` plus an explicit confirmation.

## Tests

* `tests/test_config_store.py`, `tests/test_organization.py`, `tests/test_gtm_tags.py`: unit tests.
* `tests/test_ui_api.py`: HTTP API tests against a real server on 127.0.0.1, with Google Ads, GTM and the website mocked.
* `tests/test_dashboard_e2e.py`: Playwright browser tests (optional). Set up with `pip install -e ".[dev,e2e]" && python -m playwright install chromium`. These tests are skipped when Playwright or Chromium is missing.
