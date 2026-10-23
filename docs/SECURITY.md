# Security & secrets policy

## Public-repo rules

This project is intended for a **public GitHub/GitLab repository**.

**Never commit:**

- `.env` or any file matching `.env.*` (except `.env.example`)
- Service-account JSON / OAuth client secrets / PEM / P12 keys
- `config/product_facts.json` or `config/tenant.json` (templates only)
- Anything under `secrets/`, `var/`, or live `reports/`
- Customer IDs, developer tokens, webhook URLs with tokens

The root `.gitignore` blocks these paths by default.

## Redaction

Runtime logs use `ads_ai_agents.logging_utils.redact_secrets` to strip private-key blocks and common secret patterns from printed output. Do not disable redaction in shared logs.

## Principle of least privilege

1. Start with **`ADS_AUTOMATION_MODE=READ_ONLY`**.
2. Grant the service account **only** the Google Ads access it needs.
3. Prefer a dedicated MCC + client account for automation.
4. Keep `REQUIRE_HUMAN_APPROVAL=true` until change-ledger discipline is proven.
5. Rotate developer tokens and SA keys if they ever leak.

## Pre-publish checklist

Before pushing to a public remote:

```bash
# Fail if residual secrets / private tenant markers appear
rg -n "BEGIN [A-Z ]*PRIVATE KEY|client_secret|AIza[0-9A-Za-z_-]{20}|sk-live|sk-proj" \
  --glob '!.git/**' --glob '!.venv/**' || true

rg -n "example-of-private-tenant|YOUR_REAL_DOMAIN_HERE" --glob '!.git/**' || true

# Confirm gitignored secrets are not staged
git status
git check-ignore -v .env secrets config/product_facts.json || true
```

## Responsible disclosure

If you find a credential in a fork or mirror, rotate it immediately and open an issue **without** pasting the secret.
