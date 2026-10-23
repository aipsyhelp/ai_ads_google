# Tracking app-store clicks (secondary conversions)

If your landing pages link to the App Store or Google Play, track those clicks as **secondary** conversions. They are useful signals, but they are not proof of value, so they must not be used for bidding.

`ads-agents platform product` prints the store-click events derived from `config/product_facts.json`:

| Event | Matches link host | Google Ads role |
| --- | --- | --- |
| `click_store_android` | `play.google.com` | Secondary (observe only) |
| `click_store_ios` | `apps.apple.com` | Secondary (observe only) |

## Google Tag Manager setup (outline)

1. **Trigger:** Click – Just Links, where *Click URL* contains `play.google.com` (Android) or `apps.apple.com` (iOS).
2. **Tag:** a GA4 event (`click_store_android` / `click_store_ios`) and, optionally, a Google Ads conversion tag.
3. **In Google Ads:** set both conversion actions to **Secondary**, so they are excluded from the "Conversions" column used by Smart Bidding.
4. **Consent:** fire the tags only under your Consent Mode configuration.

> Ad Grants accounts may not use app-store or YouTube URLs as ad destinations. Store links may live **on** your landing page, but never as an ad's final URL.
