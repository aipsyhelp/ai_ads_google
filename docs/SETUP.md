# Setup guide (bring your own Google Ads account)

This repository ships **no credentials and no tenant business data**.
After clone/install you must supply your own Google Ads access and product facts.

## 1. Prerequisites

- Python **3.11+**
- A Google Ads account (Ad Grants or paid Search)
- Access to [Google Ads API Center](https://ads.google.com/aw/apicenter)
- A Google Cloud project with the **Google Ads API** enabled
- Either:
  - a **service account** JSON key (recommended for servers), or
  - OAuth desktop/web credentials (advanced; wire manually)

## 2. Install

```bash
git clone https://github.com/aipsyhelp/ai_ads_google.git
cd ai_ads_google

python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## 3. Configure in the dashboard (recommended)

```bash
ads-agents ui            # or: PYTHONPATH=src python -m ads_ai_agents.cli ui
```

This opens <http://127.0.0.1:8765>. It listens on your computer only.

1. **Overview → Create missing local config files**: copies the templates (`.env`, `config/*.json`).
2. **Your organization or business**: choose *Business* or *Nonprofit (NGO)*. Describe your organization, product, service or cause, or give your website, or both. The main offering is optional. Nonprofits can turn on Ad Grants mode here.
3. **Account & credentials**: developer token, service-account key (path or upload into `secrets/`), MCC and client customer IDs.
4. **Website & domains**, **Locales & tenant**, **Safety & automation mode**, **Account IDs**: everything else the wizard asks for, plus guard limits.
5. **Google Tags (GTM / Google tag)**, optional: paste your GTM or Google tag snippet or IDs, give the shown service-account e-mail *Publish* access in GTM, review the plan, and publish it (only in a live mode, after you confirm). If your site does not load the GTM container yet, install the snippet once; after that, tag changes are automated. Needs `pip install -e ".[gtm]"`.
6. **Overview → Test Google Ads connection**, then **Run (read-only)** → *Run read-only audit*.

Every value goes into the same files the code reads. The field-by-field map is in [`DASHBOARD.md`](DASHBOARD.md).

## 3b. Configure in the terminal (wizard)

```bash
python scripts/init_project.py
```

The wizard creates **local-only** files (gitignored):

| File | Purpose |
| --- | --- |
| `.env` | API tokens, customer IDs, kill switch |
| `config/product_facts.json` | Brand, landings, approved Grant domains |
| `config/tenant.json` | Tenant metadata (timezone, Grants flag) |
| `config/account_ids.json` | Google Ads resource IDs (copied from the example, fake IDs) |

Non-interactive alternative:

```bash
python scripts/init_project.py --non-interactive
# then edit .env + config/product_facts.json + config/tenant.json
```

### What you must provide

1. **`GOOGLE_ADS_DEVELOPER_TOKEN`** — from Ads API Center  
2. **`GOOGLE_ADS_LOGIN_CUSTOMER_ID`** — manager (MCC) ID, digits only  
3. **`GOOGLE_ADS_CUSTOMER_ID`** — client account ID, digits only  
4. **Service account JSON** — place under `secrets/` (folder is gitignored)  
5. **Product facts** — your domains, locales, conversion event names  
6. **Daily budget cap** — Ad Grants ≈ `$329/day` (`$10,000/month`)

### Link the service account

1. Google Cloud → IAM → create service account → download JSON  
2. Google Ads → Tools → API Center → **API access** → add the service account  
   (or use an MCC user that the SA impersonates via domain-wide delegation)  
3. Confirm the SA can **read** the client account before enabling writes

## 4. Verify (read-only)

```bash
export PYTHONPATH=src
python -m ads_ai_agents.cli ping
python -m ads_ai_agents.cli audit
python -m ads_ai_agents.cli platform agents
python -m ads_ai_agents.cli dashboard
```

If `ping` fails: check developer token, customer IDs, SA linkage, and that the Ads API is enabled on the GCP project.

## 5. Operating modes

| `ADS_AUTOMATION_MODE` | Behavior |
| --- | --- |
| `READ_ONLY` (default) | No Google Ads mutations |
| `CONTROLLED_LIVE` | Capped auto-apply (budget tilts / approved scopes) |
| `AUTONOMOUS_LIVE` | Broader automation; Guardian still required |

**Start in `READ_ONLY`.** Only flip to live modes after:

- conversion goals are clean,
- Grant destinations are owned domains only,
- you accept change-ledger / rollback discipline.

## 6. Optional schedules (macOS launchd)

Daily delivery watch (recommended after go-live):

```bash
# Default interval in installer is 86400s (once per day)
bash scripts/install_delivery_watch_hourly.sh
```

## 7. Tests

```bash
PYTHONPATH=src pytest -q
# optional browser tests for the dashboard:
pip install -e ".[dev,e2e]" && python -m playwright install chromium
PYTHONPATH=src pytest -q -m e2e
```

Tests use example fixtures and fake credentials only. Google Ads, GTM and your website are mocked, so the tests never call your live account or the network. The browser tests are skipped automatically when Playwright or Chromium is missing.

## 8. What is intentionally NOT in this repo

- Real `.env` / service-account keys  
- Your `product_facts.json` / CRM / BigQuery project  
- Historical `reports/` and `var/` recovery state  
- Third-party vendor clones (optional local knowledge packs)

You own all business-specific configuration after install.
