# Historical operations examples

> ⚠️ **These are historical examples, not part of the supported CLI.** They are one-off scripts written for specific incidents on one Google Ad Grants account (delivery recovery, bidding fixes, goal clean-up, asset repairs). They are kept so you can see how real problems were diagnosed and fixed with the Google Ads API.

**Before you run anything here:**

1. Read the script first. Several of them change campaigns, budgets, bidding or conversion goals.
2. Keep `ADS_AUTOMATION_MODE=READ_ONLY` unless you have reviewed the plan output.
3. Many scripts default to a dry run or `validate_only`. Some legacy entry points deliberately refuse to run (`main()` returns `2`) and are kept for reference only.
4. Resource IDs (portfolio strategies, budgets, campaigns, ad groups, assets) are **not** hardcoded. They come from `config/account_ids.json` (copy `config/account_ids.example.json`; all example IDs are fake). Campaign names follow the example naming scheme `<Channel> / <domain> / <Section> / <Locale> / <Geo> / <Suffix>`.
5. Some scripts contain Ukrainian-language (UA locale) keyword lists, ad copy and policy filter terms from the bundled **example vertical** (nonprofit wellbeing support). Replace them with your own vertical and languages.

Run from the repository root, for example:

```bash
export PYTHONPATH=src
python examples/ops/conversion_chain_probe.py        # read-only probe
python examples/ops/amend_recovery.py                # validate-only by default
```

| Group | Scripts |
| --- | --- |
| Read-only probes | `change_history_forensics.py`, `conversion_chain_probe.py`, `daily_trend_probe.py`, `delivery_deadlock_probe.py`, `delivery_starvation_audit.py`, `serving_eligibility_probe.py`, `grant_ctr_structure_plan.py`, `enterprise_grant_full_checklist.py` |
| Delivery recovery | `amend_recovery.py`, `apply_grant_recovery.py`*, `bridge_maxclicks_recovery.py`*, `clear_tcpa_pause_pmax.py`*, `enforce_maximize_conversions.py`*, `revert_to_maximize_conversions.py`*, `set_recovery_tcpa.py`, `unblock_delivery.py`, `apply_full_delivery_complex.py`*, `set_grant_budget_cap.py`, `reopen_ua_cities.py` |
| Conversion goals | `fix_goals_and_bidding.py`*, `promote_chat_open_goal.py`*, `rebuild_grant_goals.py`*, `repair_recovery_goals.py`, `fix_customer_match_policy.py` |
| Structure, keywords and assets | `add_safe_longtail_keywords.py`, `apply_funnel_perfect.py`*, `attach_news_image_assets.py`, `enterprise_grant_hygiene.py`, `fix_disapproved_policy_assets.py`, `pause_hollow_ad_groups.py`, `publish_grant_safe_images.py`, `remediate_limited_sitelinks.py`, `repair_hollow_ad_groups.py` |
| Shell | `run_grant_reactivate.sh` |

\* Legacy entry point: refuses to run and is kept for reference only.
