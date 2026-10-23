# Bundled example vertical

Some modules ship with ready-made word lists and ad-copy packs, so the agents produce useful drafts out of the box. They come from the project's origin: a **nonprofit wellbeing / mental-health support** service advertising in **English, Ukrainian and German**.

| Module | What is vertical-specific |
| --- | --- |
| `intelligence/semantic_expansion.py`, `grant_keyword_normalizer.py` | Seed keywords, synonyms and stop-words (EN/UA/RU/DE) |
| `intelligence/rsa_prohibited_replace.py`, `agents/keywords.py` | Clinical and policy-sensitive terms that are replaced with safe wording |
| `intelligence/enterprise_asset_coverage.py`, `platform/agents/creative_lab.py`, `intelligence/campaign_verticals.py`, PMax copy | Headlines, descriptions, sitelinks and callouts |
| `intelligence/locale_strategy.py` | Locale detection and language priorities |
| `examples/ops/*` | Historical keyword lists used during real account operations |

None of these lists contain account IDs, brand names or personal data. City names, brand strings and conversion names have been replaced with neutral placeholders (`City_A`, `YOUR_BRAND`, `chat_open`, …) and are read from `config/product_facts.json` (`geo.focus_cities`, `brand`, …).

## Adapting it to your cause or business

1. Fill in `config/product_facts.json` (brand, domains, focus cities, prices).
2. Replace the seed keyword and copy lists in the modules above with your own terms, or override them in your fork.
3. Keep the policy word lists if you work in a sensitive category (health, finance). They help keep ads within Google Ads and Ad Grants policies.
4. Run `PYTHONPATH=src pytest -q` after you edit them.
