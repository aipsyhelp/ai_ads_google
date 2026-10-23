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

## 3. Configure (wizard)

```bash
python scripts/init_project.py
```

The wizard creates **local-only** files (gitignored):

| File | Purpose |
| --- | --- |
| `.env` | API tokens, customer IDs, kill switch |
| `config/product_facts.json` | Brand, landings, approved Grant domains |
| `config/tenant.json` | Tenant metadata (timezone, Grants flag) |

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
```

Some tests use example fixtures; they do not call your live account.

## 8. What is intentionally NOT in this repo

- Real `.env` / service-account keys  
- Your `product_facts.json` / CRM / BigQuery project  
- Historical `reports/` and `var/` recovery state  
- Third-party vendor clones (optional local knowledge packs)

You own all business-specific configuration after install.
