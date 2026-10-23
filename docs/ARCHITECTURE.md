# Architecture (short)

```text
                    CEO / Strategy / Revenue agents
                                 │
              ┌──────────────────┼──────────────────┐
              │                  │                  │
         Market intel      Creative studio      CRO funnel
              │                  │                  │
              └──────────────────┼──────────────────┘
                                 │
                    Ads enterprise ops agents
                    (sense → compete → structure
                     → create → allocate → guard)
                                 │
                      Google Ads API adapter
                    + write_guard + kill switch
                                 │
                    reports/  ·  change ledger  ·  dashboards
```

## Layers

| Layer | Package | Responsibility |
| --- | --- | --- |
| CLI | `ads_ai_agents.cli` | Operator entrypoint |
| Platform agents | `ads_ai_agents.platform` | 31-agent Revenue Growth OS registry |
| Ads ops agents | `ads_ai_agents.agents` | 10-role daily Ads loop |
| Intelligence | `ads_ai_agents.intelligence` | Audits, Grant recovery, dashboards, firewalls |
| Adapters | `ads_ai_agents.adapters` | Settings, automation mode, Google Ads client, write scopes |
| Config | `config/product_facts.json` | **Your** brand / URLs / Grant domains (local) |

## Safety rails

1. **`ADS_AUTOMATION_MODE`** — global kill switch  
2. **`write_guard`** — service-level allowlist for mutate APIs  
3. **Brand / Grant domain gates** — block third-party & clinical claims  
4. **Recommendation firewall** — Google recommendations are evidence, not auto-apply  
5. **Change ledger** — independent history with rollback payloads  

## Dashboards

`intelligence/dashboard.py` builds an efficiency board (Grant pacing, CPQA, micro-conversion share, policy risk) into `reports/YYYY-MM-DD/`.

Delivery health is monitored by `scripts/delivery_watch_hourly.py` (schedule once per day in production).
