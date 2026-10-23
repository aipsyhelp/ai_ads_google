from __future__ import annotations

from typing import Any

from ads_ai_agents.adapters.google_ads_goals import GoogleAdsGoalsWriter
from ads_ai_agents.adapters.google_ads_readonly import GoogleAdsReadOnlyAdapter
from ads_ai_agents.intelligence.change_ledger import ChangeLedger
from ads_ai_agents.intelligence.channel_cpqa import build_channel_cpqa_report
from ads_ai_agents.intelligence.conversion_audit import audit_conversion_actions
from ads_ai_agents.intelligence.dashboard import write_efficiency_dashboard
from ads_ai_agents.intelligence.goal_cleanup import plan_from_integrity_file
from ads_ai_agents.intelligence.report_store import ReportStore
from ads_ai_agents.intelligence.shadow_budget import plan_shadow_reallocation
from ads_ai_agents.logging_utils import configure_logging

logger = configure_logging()


def run_goals_cleanup_wave(*, apply: bool = False) -> dict[str, Any]:
    """
    Enterprise Wave 1 pipeline:
      refresh integrity → cleanup plan → dry-run/apply demotions → re-audit →
      channel CPQA → shadow budget → dashboard

    Budgets/keywords are never mutated in this wave.
    """
    store = ReportStore()
    day = store.day_dir(kind="audit")
    adapter = GoogleAdsReadOnlyAdapter()

    # 1) Fresh conversion integrity
    logger.info("Wave1: conversion integrity refresh")
    conv_rows = adapter.list_conversion_actions()
    perf = adapter.performance_snapshot()
    integrity = audit_conversion_actions(conv_rows, spend_mtd=perf.spend_usd_mtd).to_dict()
    store.write_json(day, "conversions_integrity.json", integrity)
    store.write_csv(day, "conversions.csv", integrity["actions"])

    # 2) Cleanup plan
    plan = plan_from_integrity_file(day / "conversions_integrity.json", spend_mtd=perf.spend_usd_mtd)
    store.write_json(day, "goal_cleanup_plan.json", plan)

    # 3) Dry-run always; apply only if requested
    micro_before = list(integrity.get("micro_primaries") or [])
    writer = GoogleAdsGoalsWriter()
    dry = writer.apply_cleanup_plan(plan, dry_run=True)
    store.write_json(day, "goal_cleanup_dry_run.json", dry.to_dict())

    apply_result = None
    integrity_after = integrity
    if apply:
        logger.info("Wave1: APPLY demote primary_for_goal on %s actions", len(dry.attempted))
        apply_result = writer.apply_cleanup_plan(plan, dry_run=False)
        store.write_json(day, "goal_cleanup_apply_result.json", apply_result.to_dict())
        ledger = ChangeLedger(store)
        for mutation in apply_result.applied:
            ledger.record_decision(
                actor="pipeline:goals-cleanup-wave",
                agent_name="ConversionGoalMutator",
                customer_id=adapter.settings.customer_id,
                reason=f"Demote micro/noise conversion from primary: {mutation.name}",
                evidence=mutation.to_dict(),
                expected_effect="Reduce inflated Conversions; Smart Bidding trains on quality events",
                approval_id="user_wave1_launch",
                old_state={"primary_for_goal": mutation.old_value},
                new_state={"primary_for_goal": mutation.new_value},
                rollback_payload={
                    "action_id": mutation.action_id,
                    "primary_for_goal": True,
                    "note": "Re-enable only if demotion was incorrect",
                },
                resource_type="conversion_action",
                source="ads_ai_agents_pipeline",
            )
        # post-apply integrity
        conv_rows = adapter.list_conversion_actions()
        integrity_after = audit_conversion_actions(conv_rows, spend_mtd=perf.spend_usd_mtd).to_dict()
        store.write_json(day, "conversions_integrity_post_apply.json", integrity_after)
        store.write_json(day, "conversions_integrity.json", integrity_after)
        plan = plan_from_integrity_file(
            day / "conversions_integrity_post_apply.json",
            spend_mtd=perf.spend_usd_mtd,
        )
        store.write_json(day, "goal_cleanup_plan_post_apply.json", plan)

    # 4) Channel CPQA (use post-apply integrity when available)
    active_integrity = integrity_after
    quality_ids = [
        a["id"]
        for a in (active_integrity.get("actions") or [])
        if a.get("tier") in {"activation", "acquisition", "revenue"}
        or any(x in a.get("name", "").lower() for x in ("chat", "form", "purchase"))
    ]
    cpqa = build_channel_cpqa_report(adapter, quality_action_ids=[i for i in quality_ids if i])
    store.write_json(day, "channel_cpqa.json", cpqa)

    # 5) Shadow budget only
    campaigns = adapter.list_campaigns()
    shadow = [
        item.to_dict()
        for item in plan_shadow_reallocation(
            campaigns,
            conversion_tracking_healthy=not active_integrity.get("micro_primaries"),
            observation_days=7 if apply else 14,
        )
    ]
    store.write_json(day, "shadow_budget.json", shadow)

    # 6) Dashboard pack
    dash_path = write_efficiency_dashboard(store)
    summary = {
        "wave": "goals_cleanup",
        "apply": apply,
        "dry_run_attempted": len(dry.attempted),
        "applied": len(apply_result.applied) if apply_result else 0,
        "apply_errors": len(apply_result.errors) if apply_result else 0,
        "micro_primaries_before": micro_before,
        "micro_primaries_after": active_integrity.get("micro_primaries"),
        "primary_actions_after": active_integrity.get("primary_actions"),
        "reported_conversions_mtd": active_integrity.get("reported_conversions_mtd"),
        "cpqa_account": active_integrity.get("kpis", {}).get("cpqa"),
        "channel_cpqa": cpqa.get("channels"),
        "shadow_proposals": shadow,
        "dashboard": str(dash_path),
        "budgets_mutated": False,
        "artifacts": str(day),
    }

    store.write_json(day, "pipeline_wave1_summary.json", summary)
    store.write_markdown(day, "pipeline_wave1_summary.md", _summary_md(summary, dry, apply_result))
    return summary


def _summary_md(summary: dict[str, Any], dry: Any, apply_result: Any) -> str:
    lines = [
        "# Pipeline Wave 1 — Goal Cleanup",
        "",
        f"- Apply executed: **{summary.get('apply')}**",
        f"- Dry-run demotions queued: {summary.get('dry_run_attempted')}",
        f"- Applied: {summary.get('applied')}",
        f"- Errors: {summary.get('apply_errors')}",
        f"- Budgets mutated: {summary.get('budgets_mutated')}",
        "",
        "## Channel CPQA",
    ]
    for ch in summary.get("channel_cpqa") or []:
        lines.append(
            f"- {ch.get('channel')}: spend=${ch.get('spend_mtd')} "
            f"quality={ch.get('quality_conversions')} CPQA={ch.get('cpqa')} "
            f"CPA_reported={ch.get('cpa_reported')}"
        )
    lines.extend(["", "## Dry-run targets"])
    for item in dry.attempted[:20]:
        lines.append(f"- {item.name} (`{item.action_id}`): primary true→false")
    if apply_result and apply_result.errors:
        lines.extend(["", "## Errors"])
        for err in apply_result.errors:
            lines.append(f"- {err}")
    return "\n".join(lines) + "\n"
