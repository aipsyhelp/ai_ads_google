# Tokens, IDs & access checklist

Fill these **locally** after `python scripts/init_project.py`. Nothing here belongs in Git.

## Google Ads API

| Secret / ID | Env var | Where to get it |
| --- | --- | --- |
| Developer token | `GOOGLE_ADS_DEVELOPER_TOKEN` | Google Ads → Tools → **API Center** |
| Manager (MCC) ID | `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | Top-right account number of the manager (digits only) |
| Client account ID | `GOOGLE_ADS_CUSTOMER_ID` | Client account that owns campaigns (digits only) |
| Service account key | `GOOGLE_ADS_SERVICE_ACCOUNT_JSON` | Google Cloud → IAM → Service accounts → Keys → JSON |

### Access wiring

1. Enable **Google Ads API** on your GCP project.  
2. Create a service account; download JSON into `secrets/` (gitignored).  
3. In Google Ads API Center, link that service account (or configure domain-wide delegation + `GOOGLE_ADS_IMPERSONATED_EMAIL`).  
4. Confirm the SA can see the client account under the MCC.  
5. `python -m ads_ai_agents.cli ping`

### Test vs production developer token

- **Test token**: fine for read-only exploration; some mutate calls may be rejected.  
- **Explore / Basic / Standard**: required for production mutations. Apply via API Center.

## Optional integrations

| Item | Env var | Purpose |
| --- | --- | --- |
| BigQuery project | `BIGQUERY_PROJECT_ID` | Warehouse / Data Manager offline uploads |
| BigQuery dataset | `BIGQUERY_DATASET` | Default `ads_agentic_control_plane` |
| Alert email | `ALERT_EMAIL` | Human alerts |
| Slack webhook | `SLACK_WEBHOOK_URL` | Alert channel (treat as secret) |

## Product / Grant config (not tokens, still private)

| File | Contains |
| --- | --- |
| `config/product_facts.json` | Brand name, landing URLs, approved Grant domains, conversion event map |
| `config/tenant.json` | Tenant id, timezone, Grants flag, customer IDs mirror |

Templates: `config/*.example.json`.

## Kill switch

```bash
ADS_AUTOMATION_MODE=READ_ONLY        # safe default
ADS_AUTOMATION_MODE=CONTROLLED_LIVE  # capped writes
ADS_AUTOMATION_MODE=AUTONOMOUS_LIVE  # broader writes + Guardian
```

Never ship a public demo with live mode enabled.
