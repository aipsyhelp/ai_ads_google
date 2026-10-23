# Google Ad Grants compliance checklist

`ads-agents platform grant-compliance` runs read-only HTTP probes and account checks against this list. Google's official policy is the source of truth: <https://support.google.com/grants/answer/117827>.

## Account rules the tools check

- **Approved domain only:** every final URL and sitelink must be on your approved nonprofit website (`grant_destinations.approved_domains` in `config/product_facts.json`). App stores, Google Play, YouTube and social links are blocked by the domain gate.
- **HTTPS:** the advertised site must load over valid HTTPS. List legacy hosts that no longer serve correctly under `grant_destinations.unserved_hosts`.
- **CTR ≥ 5%** account-wide each month. Low-CTR keywords are the main risk.
- **No single-word keywords** (except brand or approved exceptions) and **no keywords with Quality Score 1–2**.
- **Structure:** at least 2 ad groups per campaign, at least 2 ads per ad group, and at least 2 sitelinks.
- **Conversion tracking:** at least 1 meaningful conversion per month.
- **Mission-first website:** an about page, clear nonprofit mission, contact page and privacy policy. No commercial-only landing pages.

## If the account is deactivated

1. Fix every item above. The `grant-reactivate` and `grant-scrub-domains` commands help, and both are dry-run unless you pass `--apply` in a live mode.
2. Submit Google's reactivation form: <https://support.google.com/nonprofits/contact/Request_for_reactivation_cases_2?hl=en>.
3. Keep `ADS_AUTOMATION_MODE=READ_ONLY` until Google confirms reactivation.
