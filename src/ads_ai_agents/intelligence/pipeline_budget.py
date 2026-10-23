from __future__ import annotations

from typing import Any

from ads_ai_agents.adapters.google_ads_budgets import GoogleAdsBudgetWriter
from ads_ai_agents.adapters.google_ads_goals import GoogleAdsGoalsWriter
from ads_ai_agents.adapters.google_ads_readonly import GoogleAdsReadOnlyAdapter
from ads_ai_agents.intelligence.budget_pacing import build_remaining_month_plan
from ads_ai_agents.intelligence.change_ledger import ChangeLedger
from ads_ai_agents.intelligence.channel_cpqa import build_channel_cpqa_report
from ads_ai_agents.intelligence.conversion_audit import audit_conversion_actions
from ads_ai_agents.intelligence.dashboard import write_efficiency_dashboard
from ads_ai_agents.intelligence.report_store import ReportStore
from ads_ai_agents.logging_utils import configure_logging
from ads_ai_agents.platform.account_ids import load_account_ids

logger = configure_logging()

# Secondary-only YouTube conversion actions, from config/account_ids.json.
YOUTUBE_ACTION_IDS = tuple(
    str(x) for x in ((load_account_ids().get("conversion_actions") or {}).get("youtube") or [])
)


def run_budget_pacing_wave(*, apply: bool = False) -> dict[str, Any]:
    """
    Wave 2: remaining-month grant utilization.
    - retry YouTube goal demote (often API-blocked)
    - fire-test readiness check on form/chat/purchase
    - budget plan + optional apply (pause drains, fund Search Articles)
    - CPQA + dashboard refresh
    """
    store = ReportStore()
    day = store.day_dir(kind="audit")
    adapter = GoogleAdsReadOnlyAdapter()
    budget_writer = GoogleAdsBudgetWriter()
    ledger = ChangeLedger(store)

    # 1) YouTube demote retry (manual fallback noted if blocked)
    youtube_result = _try_youtube_demote()
    store.write_json(day, "youtube_demote_attempt.json", youtube_result)

    # 2) Fire-test readiness
    firetest = _firetest_readiness(adapter)
    store.write_json(day, "firetest_readiness.json", firetest)
    store.write_markdown(day, "FIRETEST.md", _firetest_md(firetest, youtube_result))

    # 3) Budget plan from live budget resources
    campaigns = budget_writer.list_campaign_budgets()
    perf = adapter.performance_snapshot()
    plan = build_remaining_month_plan(campaigns, spend_mtd=float(perf.spend_usd_mtd or 0))
    # attach resource names already on campaigns
    store.write_json(day, "budget_pacing_plan.json", plan)
    store.write_markdown(day, "budget_pacing_plan.md", _budget_md(plan))

    dry = budget_writer.apply_plan(plan, dry_run=True)
    store.write_json(day, "budget_pacing_dry_run.json", dry.to_dict())

    apply_result = None
    if apply:
        logger.info("Wave2 APPLY budget pacing: %s changes", len(dry.attempted))
        apply_result = budget_writer.apply_plan(plan, dry_run=False)
        store.write_json(day, "budget_pacing_apply_result.json", apply_result.to_dict())
        for change in apply_result.applied:
            ledger.record_decision(
                actor="pipeline:budget-pacing-wave",
                agent_name="BudgetPacingAgent",
                customer_id=adapter.settings.customer_id,
                campaign_id=str(change.get("campaign_id") or ""),
                reason=str(change.get("reason") or "remaining-month pacing"),
                evidence=change,
                expected_effect="Utilize remaining grant on higher-efficiency Search; stop drains",
                approval_id="user_wave2_remaining_grant",
                old_state={
                    "daily_budget": change.get("current_daily_budget"),
                    "status": change.get("current_status"),
                },
                new_state={
                    "daily_budget": change.get("proposed_daily_budget"),
                    "status": change.get("proposed_status"),
                    "action": change.get("action"),
                },
                rollback_payload={
                    "campaign_id": change.get("campaign_id"),
                    "budget_resource_name": change.get("budget_resource_name"),
                    "daily_budget": change.get("current_daily_budget"),
                    "status": change.get("current_status"),
                },
                resource_type="campaign_budget",
                source="ads_ai_agents_pipeline",
            )

    # 4) Integrity + CPQA
    integrity = audit_conversion_actions(
        adapter.list_conversion_actions(),
        spend_mtd=perf.spend_usd_mtd,
    ).to_dict()
    store.write_json(day, "conversions_integrity.json", integrity)

    quality_ids = [
        a["id"]
        for a in integrity.get("actions") or []
        if a.get("tier") in {"activation", "acquisition", "revenue"}
        or any(x in a.get("name", "").lower() for x in ("chat", "form", "purchase"))
    ]
    cpqa = build_channel_cpqa_report(adapter, quality_action_ids=[i for i in quality_ids if i])
    store.write_json(day, "channel_cpqa.json", cpqa)

    dash = write_efficiency_dashboard(store)
    summary = {
        "wave": "budget_pacing_remaining_month",
        "apply": apply,
        "remaining_usd": plan.get("remaining_usd"),
        "target_daily_usd": plan.get("target_daily_usd"),
        "proposed_daily_budget_sum": plan.get("proposed_daily_budget_sum"),
        "changes_queued": len(dry.attempted),
        "applied": len(apply_result.applied) if apply_result else 0,
        "errors": len(apply_result.errors) if apply_result else 0,
        "youtube_demote": youtube_result,
        "firetest": firetest,
        "channel_cpqa": cpqa.get("channels"),
        "dashboard": str(dash),
        "artifacts": str(day),
        "keywords_mutated": False,
    }
    store.write_json(day, "pipeline_wave2_summary.json", summary)
    store.write_markdown(day, "pipeline_wave2_summary.md", _summary_md(summary, plan))
    return summary


def _try_youtube_demote() -> dict[str, Any]:
    writer = GoogleAdsGoalsWriter()
    names = ("YouTube channel subscriptions", "YouTube follow-on views")
    plan = {
        "actions": [
            {
                "id": action_id,
                "name": names[i] if i < len(names) else f"YouTube action {action_id}",
                "decision": "demote_to_secondary",
                "current_primary": True,
            }
            for i, action_id in enumerate(YOUTUBE_ACTION_IDS)
        ]
    }
    result = writer.apply_cleanup_plan(plan, dry_run=False)
    payload = result.to_dict()
    payload["manual_ui_required"] = len(result.errors) > 0
    payload["manual_steps"] = [
        "Google Ads → Goals → Conversions",
        "YouTube channel subscriptions → Secondary / exclude from Conversions",
        "YouTube follow-on views → Secondary / exclude from Conversions",
    ]
    return payload


def _firetest_readiness(adapter: GoogleAdsReadOnlyAdapter) -> dict[str, Any]:
    rows = adapter.list_conversion_actions()
    checks = []
    for row in rows:
        name = str(row.get("conversion_action.name") or "")
        lower = name.lower()
        if not any(x in lower for x in ("form", "chat", "purchase")):
            continue
        if "paypal" in lower:
            continue
        checks.append(
            {
                "name": name,
                "id": str(row.get("conversion_action.id") or ""),
                "primary_for_goal": bool(row.get("conversion_action.primary_for_goal")),
                "include_in_conversions": row.get("conversion_action.include_in_conversions_metric"),
                "conversions_mtd": float(row.get("metrics.conversions") or 0),
                "status": "needs_fire_test"
                if float(row.get("metrics.conversions") or 0) <= 0
                else "receiving_volume",
                "checklist": [
                    "Submit real/test form with gclid preserved",
                    "Open chat path from ad click",
                    "Complete Purchase sandbox/live once",
                    "Confirm event in Google Ads → Goals → Diagnostics within 3h",
                ],
            }
        )
    return {
        "actions": checks,
        "ready_for_bidding": any(
            c["primary_for_goal"] and c["conversions_mtd"] > 0 for c in checks
        ),
        "note": "Form/chat/purchase must fire from real ad clicks; MTD 0 means tagging/path repair needed.",
    }


def _firetest_md(firetest: dict[str, Any], youtube: dict[str, Any]) -> str:
    lines = ["# Fire-test & YouTube cleanup", ""]
    if youtube.get("manual_ui_required"):
        lines.append("## YouTube (manual — API MUTATE_NOT_ALLOWED)")
        for step in youtube.get("manual_steps") or []:
            lines.append(f"- [ ] {step}")
        lines.append("")
    lines.append("## Quality conversion fire-tests")
    for action in firetest.get("actions") or []:
        lines.append(
            f"### {action['name']} — {action['status']} (MTD {action['conversions_mtd']})"
        )
        lines.append(f"- primary_for_goal: {action['primary_for_goal']}")
        for item in action.get("checklist") or []:
            lines.append(f"- [ ] {item}")
        lines.append("")
    return "\n".join(lines)


def _budget_md(plan: dict[str, Any]) -> str:
    lines = [
        "# Budget pacing plan — remaining month",
        "",
        f"- Remaining: **${plan.get('remaining_usd')}**",
        f"- Days left: **{plan.get('days_left')}**",
        f"- Target daily: **${plan.get('target_daily_usd')}**",
        f"- Proposed daily sum: **${plan.get('proposed_daily_budget_sum')}**",
        "",
        "## Changes",
    ]
    for ch in plan.get("changes") or []:
        lines.append(
            f"- `{ch.get('action')}` {ch.get('campaign_name')}: "
            f"${ch.get('current_daily_budget')} → {ch.get('proposed_daily_budget')} "
            f"| {ch.get('current_status')}→{ch.get('proposed_status')} — {ch.get('reason')}"
        )
    return "\n".join(lines) + "\n"


def _summary_md(summary: dict[str, Any], plan: dict[str, Any]) -> str:
    lines = [
        "# Pipeline Wave 2 — Budget pacing",
        "",
        f"- Apply: **{summary.get('apply')}**",
        f"- Remaining: ${summary.get('remaining_usd')} / target daily ${summary.get('target_daily_usd')}",
        f"- Applied changes: {summary.get('applied')} (errors {summary.get('errors')})",
        f"- YouTube manual required: {summary.get('youtube_demote', {}).get('manual_ui_required')}",
        "",
        "## Channel CPQA",
    ]
    for ch in summary.get("channel_cpqa") or []:
        lines.append(
            f"- {ch.get('channel')}: CPQA={ch.get('cpqa')} quality={ch.get('quality_conversions')} "
            f"spend=${ch.get('spend_mtd')}"
        )
    lines.extend(["", "## Narrative"])
    for item in plan.get("narrative") or []:
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"
