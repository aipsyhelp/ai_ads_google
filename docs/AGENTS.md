# Agent catalog

This control plane has **two complementary agent fleets**:

| Fleet | Count | Role |
| --- | ---: | --- |
| **Revenue Growth OS (platform)** | **31** | Strategy, creative, CRO, revenue — mostly draft/approval |
| **Google Ads enterprise ops** | **10** | Campaign / keyword / budget / policy execution loop |
| **Total agent roles** | **41** | Designed as a 25–35+ phased OS (see registry) |

The live registry is always authoritative:

```bash
PYTHONPATH=src python -m ads_ai_agents.cli platform agents
```

---

## A. Revenue Growth OS — 31 platform agents

Mutation policy legend:

- `never` — read / advise only
- `draft_only` — writes recommendations to reports, no Ads API mutate
- `controlled` — capped live writes when `ADS_AUTOMATION_MODE=CONTROLLED_LIVE`
- `approval` — requires explicit human approval before apply

### L1 — Market intelligence (6)

| ID | Name | Mutates | What it does |
| --- | --- | --- | --- |
| `competitor_intel` | Competitor Intelligence | draft_only | Public competitive diffs (Ads Transparency / SERP notes) |
| `search_opportunity` | Search Opportunity | draft_only | Demand spikes from trends / news / forums |
| `intent_mining` | Intent Mining | draft_only | Pain-language intents beyond raw keywords |
| `aso_intel` | App Store / Play Intel | draft_only | Reviews, rankings, competitor store listings |
| `regulatory_watch` | Regulatory Watch | draft_only | Advertising / wellbeing policy shifts |
| `serp_evidence` | SERP / Ads Transparency | draft_only | Public creative & auction evidence packs |

### L2 — Strategy (4)

| ID | Name | Mutates | What it does |
| --- | --- | --- | --- |
| `ceo` | CEO AI | draft_only | Weekly executive brief across layers |
| `strategy` | Strategy Engine | draft_only | Countries, segments, budget priorities |
| `portfolio` | Portfolio Agent | approval | Grant vs paid vs app allocation |
| `experiment_prioritizer` | Experiment Prioritizer | draft_only | Picks ≤5 weekly experiments |

### L3 — Creative studio (6)

| ID | Name | Mutates | What it does |
| --- | --- | --- | --- |
| `creative_lab` | Creative Lab | draft_only | High-volume Hero / CTA / RSA / video drafts |
| `copy_ai` | Copy AI | draft_only | Landing / RSA / paywall copy |
| `image_ai` | Image AI | draft_only | Image briefs / generations |
| `video_ai` | Video AI | draft_only | Video briefs / generations |
| `localization` | Localization AI | draft_only | Multi-locale packs (EN / UA / DE / …) |
| `brand_policy_gate` | Brand & Policy Gate | never | Blocks medical / crisis / prohibited claims |

### L4 — Acquisition (5)

| ID | Name | Mutates | What it does |
| --- | --- | --- | --- |
| `ads_controlled_live` | Google Ads CONTROLLED_LIVE | controlled | Capped Ads executor (budgets, negatives, assets) |
| `seo` | SEO Agent | draft_only | Content / SEO opportunities |
| `aso` | ASO Agent | draft_only | Store listing experiments |
| `email_content` | Email / Content Agent | approval | Lifecycle & content drafts |
| `demand_gen` | Demand Gen / YouTube | approval | Policy-gated video demand plans |

### L5 — Conversion optimization (5)

| ID | Name | Mutates | What it does |
| --- | --- | --- | --- |
| `cro_funnel` | CRO Funnel Agent | draft_only | Drop-off diagnosis toward CPPU |
| `landing_ai` | Landing AI | draft_only | Landing-page experiment briefs |
| `onboarding` | Onboarding Agent | approval | Screen order / habit-loop proposals |
| `paywall` | Paywall / Pricing Agent | approval | Trial / price tests |
| `activation_chat` | Activation Chat Agent | draft_only | First-session quality signals |

### L6 — Revenue & analytics (5)

| ID | Name | Mutates | What it does |
| --- | --- | --- | --- |
| `revenue` | Revenue Agent | never | CPPU / LTV / CAC — forbids CPA worship |
| `experiment` | Experiment Agent | approval | Runs ≤5/week, promotes winners |
| `analytics` | Analytics / Cohort | never | Cohorts & forecasts |
| `measurement` | Measurement / Data Manager | approval | Quality conversion pipeline |
| `guardian` | Rollback / Kill-switch Guardian | controlled | Freeze & rollback on regressions |

---

## B. Google Ads enterprise ops — 10 agents

Used by the daily Ads mission loop (`ads-agents plan` / `daily`):

| Agent | Stage | What it does |
| --- | --- | --- |
| MeasurementAgent | Sense | Conversion integrity, CPQA / reported CPA health |
| CompetitorAgent | Compete | Competitor / auction pressure notes |
| TrendAgent | Compete | Demand / trend refresh |
| StrategyAgent | Structure | Portfolio & campaign structure recommendations |
| CampaignBuilderAgent | Structure | Campaign / ad-group structure drafts |
| KeywordAgent | Structure | Keyword / negative proposals (Grant-safe) |
| AssetAgent | Create | RSA / sitelink / callout / snippet packs |
| BudgetAgent | Allocate | Pacing vs monthly Grant / paid budget |
| PolicyQAAgent | Guard | Brand & policy gate before publish |
| WatchdogAgent | Guard | Delivery / drift / kill-switch monitoring |

---

## Dashboards & monitors

| Surface | Command / job | Purpose |
| --- | --- | --- |
| Efficiency dashboard | `python -m ads_ai_agents.cli dashboard` | KPI board → `reports/YYYY-MM-DD/` |
| Daily intelligence | `python -m ads_ai_agents.cli daily-run` | Read-only daily pack |
| Delivery watch | `scripts/delivery_watch_hourly.py` | Grant CTR / delivery alerts (schedule daily) |
| Platform agents list | `python -m ads_ai_agents.cli platform agents` | Registry dump |

Default automation mode is **`READ_ONLY`**. No agent mutates Google Ads until you change `.env`.
