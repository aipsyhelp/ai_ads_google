# Activation tracking spec

Smart Bidding learns from whatever you mark as a **primary** conversion. If micro-events such as scrolls, time on page or outbound clicks are primary, the bidder optimises for noise. The goal-cleanup tools (`ads-agents goals cleanup-plan`, `ads-agents dashboard`) use the tiers below.

| Tier | Example events | Google Ads role |
| --- | --- | --- |
| micro | `scroll`, `seconds_on_page`, `click_email`, `click_messenger` | Secondary (analytics only) |
| acquisition / lead | `form_submit`, qualified lead | Primary |
| activation | `chat_open`, `chat_message_sent`, `signup_complete` | Primary (the north star for most products) |
| revenue | `purchase`, `payment_success` | Primary or value-based once volume allows |
| store / social | `click_store_android`, `click_store_ios`, YouTube actions | Secondary |

## Rules

1. Exactly one consistent **primary stack**. Everything else is secondary.
2. Preserve `gclid` / `gbraid` through every redirect and login step.
3. Fire-test each primary action (`ads-agents platform firetest --gclid TEST`) and confirm it in Google Ads → Goals → Diagnostics.
4. For sensitive categories (health, wellbeing) do **not** enable Enhanced Conversions for Leads or Customer Match uploads.

Name your conversion actions however you like. The tools match these names case-insensitively (for example `chat_open`, `form_submit`, `purchase`).
