from __future__ import annotations

from typing import Any

from ads_ai_agents.adapters.automation_mode import resolve_automation_mode
from ads_ai_agents.adapters.google_ads_readonly import GoogleAdsReadOnlyAdapter
from ads_ai_agents.intelligence.report_store import ReportStore
from ads_ai_agents.logging_utils import configure_logging
from ads_ai_agents.platform.agents.competitor_discovery import discover_top_competitors
from ads_ai_agents.platform.agents.competitor_intel import build_competitor_brief
from ads_ai_agents.platform.agents.creative_lab import generate_creative_draft_pack
from ads_ai_agents.platform.agents.cro import run_cro_diagnosis
from ads_ai_agents.platform.agents.experiment import plan_weekly_experiments
from ads_ai_agents.platform.agents.intent_mining import mine_intent_opportunities
from ads_ai_agents.platform.agents.revenue import run_revenue_review
from ads_ai_agents.platform.agents.search_opportunity import build_search_opportunity_brief
from ads_ai_agents.platform.north_star import FunnelSnapshot
from ads_ai_agents.platform.product_facts import firetest_urls, load_product_facts
from ads_ai_agents.platform.registry import PLATFORM_AGENTS
from ads_ai_agents.platform.strategy_engine import build_strategy_mission

logger = configure_logging()


def _estimate_funnel_from_ads(adapter: GoogleAdsReadOnlyAdapter) -> FunnelSnapshot:
    """Until GA4/CRM arrives: top-of-funnel from Ads; paid=0 unless client supplies."""
    try:
        perf = adapter.performance_snapshot()
        campaigns = adapter.list_campaigns()
        spend = float(perf.spend_usd_mtd or 0)
        clicks = float(sum(int(c.get("clicks") or 0) for c in campaigns if not c.get("error")))
        # Quality proxy: chat activations from conversion list if available
        first_chat = 0.0
        for row in adapter.list_conversion_actions():
            name = str(row.get("conversion_action.name") or "").lower()
            if "chat" in name:
                first_chat += float(row.get("metrics.conversions") or 0)
        return FunnelSnapshot(
            period_days=30,
            ad_clicks=clicks,
            ad_spend_usd=spend,
            landing_sessions=clicks,  # unknown; assume ≈ clicks until GA4
            registrations=0.0,
            first_chat=first_chat,
            returned_users=0.0,
            subscriptions_started=0.0,
            paying_users=0.0,
            revenue_usd=0.0,
            source="ads_estimated_until_ga4_crm",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Ads funnel estimate failed: %s", exc)
        return FunnelSnapshot(period_days=30, source="empty")


def run_ceo_brief(
    *,
    funnel: FunnelSnapshot | None = None,
    store: ReportStore | None = None,
    include_ads_live: bool = True,
) -> dict[str, Any]:
    """CEO AI — executive brief across Market → Strategy → CRO → Revenue → Ads executor."""
    store = store or ReportStore()
    day_dir = store.day_dir(kind="audit")
    mode = resolve_automation_mode()

    adapter = None
    search_terms: list[dict[str, Any]] = []
    if include_ads_live:
        try:
            adapter = GoogleAdsReadOnlyAdapter()
            search_terms = adapter.list_search_terms(days=30)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Live Ads unavailable for CEO brief: %s", exc)

    if funnel is None:
        funnel = _estimate_funnel_from_ads(adapter) if adapter else FunnelSnapshot(period_days=30)

    competitor = build_competitor_brief()
    competitors_top10 = discover_top_competitors()
    opportunity = build_search_opportunity_brief()
    intent = mine_intent_opportunities(search_terms)
    cro = run_cro_diagnosis(funnel)
    revenue = run_revenue_review(
        funnel,
        qualified_activations=funnel.first_chat or None,
    )
    # Stage narrative: activation over payments
    if funnel.paying_users <= 3:
        cro = {
            **cro,
            "stage_override": (
                "Payments are rare — optimize Cost per Product Activation "
                "(login/app open → first chat), keep Purchase as observe-only."
            ),
        }
    strategy = build_strategy_mission(
        funnel=funnel,
        competitor_brief={**competitor, "top10": competitors_top10},
        intent_brief=intent,
        cro_brief=cro,
        revenue_brief=revenue,
    )
    creative = generate_creative_draft_pack()
    experiments = plan_weekly_experiments()
    facts = load_product_facts()

    executive = {
        "title": "YOUR_BRAND — CEO AI Executive Brief",
        "north_star": "Cost per Product Activation (CPPA) → later CPPU when payments scale",
        "ads_automation_mode": mode.value,
        "headline": (
            "Zero paying users with material Ads spend ⇒ rebuild Growth OS around funnel/revenue, "
            "not Ads CPA."
            if funnel.paying_users <= 0 and funnel.ad_spend_usd > 0
            else "Optimize allocation by CPPU once payment truth exists."
        ),
        "cro_verdict": cro.get("verdict"),
        "top_priorities": strategy.get("priorities"),
        "country_stance": strategy.get("country_stance"),
        "channel_stance": strategy.get("channel_stance"),
        "blocked_on_client": [
            "UI demote still-primary: YouTube channel subscriptions + Purchase (1) webpage",
            "Fire-test chat_message_sent + purchase(gclid) → Ads Diagnostics / Realtime",
            "GA4 / CRM 90d funnel volumes (login→chat_message_sent→paid) for CPPA baseline",
            "Competitor priority list (countries)",
            "Brand approver for Creative Lab assets",
        ],
        "product_facts_status": facts.get("status"),
        "pricing_live": (facts.get("pricing") or {}).get("stripe_live"),
        "firetest_urls": firetest_urls(),
    }

    brief = {
        "executive": executive,
        "funnel": funnel.to_dict(),
        "product_facts": {
            "status": facts.get("status"),
            "landings": facts.get("landings"),
            "web_app": facts.get("web_app"),
            "stores": facts.get("stores"),
            "pricing": facts.get("pricing"),
            "monetization_paths": facts.get("monetization_paths"),
            "tracking": facts.get("tracking"),
        },
        "agents": {
            "competitor_intel": competitor,
            "competitor_discovery": competitors_top10,
            "search_opportunity": opportunity,
            "intent_mining": intent,
            "cro": cro,
            "revenue": revenue,
            "strategy": strategy,
            "creative_lab": creative,
            "experiment": experiments,
            "ads_executor": {
                "id": "ads_controlled_live",
                "role": "acquisition_executor_only",
                "commands": [
                    "controlled-live audit",
                    "controlled-live cycle",
                    "controlled-live cycle --apply",
                ],
                "mode": mode.value,
            },
        },
        "registry_count": len(PLATFORM_AGENTS),
        "artifacts": str(day_dir),
    }

    store.write_json(day_dir, "ceo_brief.json", brief)
    store.write_markdown(day_dir, "ceo_brief.md", _markdown(brief))
    store.write_json(day_dir, "platform_agent_registry.json", [
        {
            "id": a.id,
            "name": a.name,
            "layer": a.layer.value,
            "phase": a.phase,
            "mutates": a.mutates,
        }
        for a in PLATFORM_AGENTS
    ])
    return brief


def _markdown(brief: dict[str, Any]) -> str:
    ex = brief["executive"]
    lines = [
        f"# {ex['title']}",
        "",
        f"**North star:** {ex['north_star']}",
        f"**Ads mode:** `{ex['ads_automation_mode']}`",
        "",
        ex["headline"],
        "",
        "## CRO verdict",
        str(ex.get("cro_verdict") or ""),
        "",
        "## Priorities",
    ]
    for p in ex.get("top_priorities") or []:
        lines.append(f"{p.get('rank')}. **{p.get('theme')}** — DO: {p.get('do')} / DON'T: {p.get('dont')}")
    lines.extend(["", "## Blocked on client inputs"])
    for item in ex.get("blocked_on_client") or []:
        lines.append(f"- [ ] {item}")
    lines.extend(
        [
            "",
            "## Next commands",
            "```bash",
            "python -m ads_ai_agents.cli platform brief",
            "python -m ads_ai_agents.cli platform diagnose",
            "python -m ads_ai_agents.cli controlled-live cycle",
            "```",
            "",
            "See `docs/REQUIREMENTS_FROM_CLIENT.md`.",
            "",
        ]
    )
    return "\n".join(lines)
