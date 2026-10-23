# What you need to provide

Printed by `ads-agents platform requirements`. Work through this list once, before you leave `READ_ONLY` mode.

## Required for read-only use

| Item | Where it goes | How to get it |
| --- | --- | --- |
| Google Ads developer token | `.env` → `GOOGLE_ADS_DEVELOPER_TOKEN` | Google Ads → Tools → API Center |
| Manager (MCC) account ID | `.env` → `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | Digits only |
| Client account ID | `.env` → `GOOGLE_ADS_CUSTOMER_ID` | Digits only |
| Service-account key (JSON) | `secrets/` + `.env` → `GOOGLE_ADS_SERVICE_ACCOUNT_JSON` | See [`TOKENS_AND_ACCESS.md`](TOKENS_AND_ACCESS.md) |
| Brand, landing pages, approved Grant domains | `config/product_facts.json` | Copy `config/product_facts.example.json` |
| Tenant metadata (timezone, Grants yes/no) | `config/tenant.json` | Copy `config/tenant.example.json` |

`python scripts/init_project.py` creates all of these local files for you.

## Recommended before any live change

- **Conversion map:** which conversion actions mean real intent (form submit, chat start, purchase) and which are micro-events (scroll, time on page). See [`ACTIVATION_TRACKING_SPEC.md`](ACTIVATION_TRACKING_SPEC.md).
- **Funnel numbers:** a 90-day funnel CSV in the format of `samples/funnel_90d_template.csv` (`ads-agents platform ingest-funnel`).
- **Resource IDs for optional ops modules:** `config/account_ids.json` (copy `config/account_ids.example.json`; all example values are fake).
- **Ad Grants only:** confirm the approved website domain(s). See [`AD_GRANTS_COMPLIANCE.md`](AD_GRANTS_COMPLIANCE.md).
- **Alerts (optional):** `ALERT_EMAIL` or `SLACK_WEBHOOK_URL` in `.env`.

Never commit any of these files. They are listed in `.gitignore`.
