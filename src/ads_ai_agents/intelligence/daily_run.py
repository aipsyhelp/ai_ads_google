from __future__ import annotations

from typing import Any

from ads_ai_agents.adapters.google_ads_readonly import GoogleAdsReadOnlyAdapter
from ads_ai_agents.intelligence.account_audit import run_full_audit
from ads_ai_agents.intelligence.anomalies import detect_anomalies
from ads_ai_agents.intelligence.change_ledger import ChangeLedger
from ads_ai_agents.intelligence.recommendation_firewall import evaluate_recommendations
from ads_ai_agents.intelligence.report_store import ReportStore
from ads_ai_agents.intelligence.shadow_budget import plan_shadow_reallocation
from ads_ai_agents.logging_utils import configure_logging

logger = configure_logging()


def run_daily_intelligence(
    adapter: GoogleAdsReadOnlyAdapter | None = None,
    store: ReportStore | None = None,
) -> dict[str, Any]:
    """
    Daily read-only loop:
    sync → freshness → KPIs → anomalies → search terms → policy → pacing →
    recommendations → diff → executive report.
    Never mutates Google Ads.
    """
    adapter = adapter or GoogleAdsReadOnlyAdapter()
    store = store or ReportStore()
    day_dir = store.day_dir(kind="daily")

    # Reuse full audit artifacts, then write daily-specific executive pack.
    audit = run_full_audit(adapter=adapter, store=store)
    executive = audit["executive"]
    conversion_report = audit["conversion_report"]

    campaigns = adapter.list_campaigns()
    search_terms = adapter.list_search_terms()
    recommendations = [
        item.to_dict() for item in evaluate_recommendations(adapter.list_recommendations())
    ]
    change_events = adapter.list_change_events()
    ChangeLedger(store).sync_from_google_events(
        change_events, customer_id=str(executive.get("customer_id") or "")
    )

    spend_mtd = float(executive.get("spend_mtd") or 0)
    anomalies = [
        item.to_dict()
        for item in detect_anomalies(
            campaigns=campaigns,
            search_terms=search_terms,
            conversion_report=conversion_report,
            spend_mtd=spend_mtd,
        )
    ]
    shadow = [
        item.to_dict()
        for item in plan_shadow_reallocation(
            campaigns,
            conversion_tracking_healthy=not conversion_report.get("micro_primaries"),
        )
    ]

    approval_required = [
        rec for rec in recommendations if rec.get("approval_required")
    ]
    auto_candidates = [
        rec for rec in recommendations if rec.get("decision") == "auto_apply_candidate"
    ]

    daily = {
        "generated_at": store.stamp(),
        "account_health": {
            "customer_id": executive.get("customer_id"),
            "account": executive.get("account"),
            "reported_conversions_mtd": executive.get("reported_conversions_mtd"),
            "primary_actions": executive.get("primary_actions"),
            "micro_primaries": executive.get("micro_primaries"),
        },
        "grant_pacing": {
            "spend_mtd": spend_mtd,
            "grant_monthly": 10_000,
            "target_cpa_usd": 2.0,
            "cpa_reported_conversions": executive.get("cpa_reported_conversions"),
            "cpqa": executive.get("cpqa"),
            "cac": executive.get("cac"),
        },
        "spend_anomalies": [a for a in anomalies if a["code"].startswith("pacing") or a["code"].startswith("campaign")],
        "conversion_anomalies": [a for a in anomalies if a["code"].startswith("conversion")],
        "search_term_risks": [a for a in anomalies if a["code"].startswith("search_term")],
        "campaign_winners": executive.get("winners_preliminary"),
        "campaign_losers": executive.get("losers_preliminary"),
        "policy_risks": anomalies,  # filtered in markdown
        "recommended_actions": auto_candidates[:20],
        "approval_required_actions": approval_required[:20] + [
            {
                "action": "shadow_budget",
                "items": shadow,
                "note": "Shadow only — do not apply until conversion integrity proven.",
            }
        ],
        "terminology": conversion_report.get("terminology"),
        "audit_directory": audit["directory"],
    }

    store.write_json(day_dir, "daily_report.json", daily)
    store.write_markdown(day_dir, "daily_report.md", _daily_markdown(daily))
    store.write_json(day_dir, "recommendations.json", recommendations)
    store.write_json(day_dir, "anomalies.json", anomalies)
    store.write_json(day_dir, "shadow_budget.json", shadow)

    logger.info("Daily intelligence written to %s", day_dir)
    return {"directory": str(day_dir), "daily": daily}


def _daily_markdown(daily: dict[str, Any]) -> str:
    health = daily["account_health"]
    pacing = daily["grant_pacing"]
    lines = [
        f"# Daily Intelligence — {health.get('account')}",
        "",
        "## Account health",
        f"- Customer: `{health.get('customer_id')}`",
        f"- Reported conversions MTD: {health.get('reported_conversions_mtd')}",
        f"- Primary actions: {', '.join(health.get('primary_actions') or []) or 'n/a'}",
        "",
        "## Grant pacing",
        f"- Spend MTD: ${pacing.get('spend_mtd')}",
        f"- Grant: ${pacing.get('grant_monthly')}",
        f"- CPA (reported conversions): {pacing.get('cpa_reported_conversions')}",
        f"- CPQA: {pacing.get('cpqa')}",
        f"- CAC: {pacing.get('cac')}",
        "",
        "## Campaign winners (preliminary)",
    ]
    for item in daily.get("campaign_winners") or []:
        lines.append(f"- {item.get('name')} CPA={item.get('cpa')} conv={item.get('conversions')}")
    lines.extend(["", "## Campaign losers / diagnosis queue"])
    for item in daily.get("campaign_losers") or []:
        lines.append(f"- {item.get('name')} CPA={item.get('cpa')} cost={item.get('cost')}")
    lines.extend(["", "## Approval-required actions"])
    for item in daily.get("approval_required_actions") or []:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Guardrail",
            "- Mode: read-only / shadow recommendations only",
            "- No mutate operations executed",
            f"- Terminology: say '{daily.get('terminology', {}).get('say_instead')}'",
        ]
    )
    return "\n".join(lines) + "\n"
