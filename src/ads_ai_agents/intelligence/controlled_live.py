from __future__ import annotations

import os
from typing import Any

from ads_ai_agents.adapters.automation_mode import MUTATING_MODES
from ads_ai_agents.adapters.google_ads_budgets import GoogleAdsBudgetWriter
from ads_ai_agents.adapters.google_ads_keywords import GoogleAdsKeywordWriter
from ads_ai_agents.adapters.google_ads_readonly import GoogleAdsReadOnlyAdapter
from ads_ai_agents.intelligence.budget_pilot import plan_budget_pilot
from ads_ai_agents.intelligence.change_ledger import ChangeLedger
from ads_ai_agents.intelligence.controlled_policy import load_controlled_policy
from ads_ai_agents.intelligence.cpqa_rollback import evaluate_cpqa_rollbacks
from ads_ai_agents.intelligence.historical_audit import run_historical_audit
from ads_ai_agents.intelligence.locale_strategy import build_locale_strategy_pack
from ads_ai_agents.intelligence.negative_miner import mine_negative_proposals
from ads_ai_agents.intelligence.recommendation_firewall import evaluate_recommendations
from ads_ai_agents.intelligence.report_store import ReportStore
from ads_ai_agents.logging_utils import configure_logging

logger = configure_logging()


def run_controlled_live_cycle(*, apply: bool = False) -> dict[str, Any]:
    """
    CONTROLLED_LIVE / AUTONOMOUS_LIVE production cycle:
    - historical 7/30/180d audit
    - proven query promotion into policy-safe exact keywords
    - locale strategy drafts (UA/DE/EN)
    - auto negatives (high confidence)
    - budget tilts with cooldown (no campaign pause without approval)
    - AUTONOMOUS_LIVE: Brand-gated RSA/PMax asset publish
    - recommendations as evidence only
    - CPQA rollback evaluation
    """
    policy = load_controlled_policy()
    store = ReportStore()
    day_dir = store.day_dir(kind="audit")
    ledger = ChangeLedger(store)
    adapter = GoogleAdsReadOnlyAdapter()
    customer_id = adapter.settings.customer_id

    if apply and policy.mode not in MUTATING_MODES:
        raise PermissionError(
            f"Apply blocked: ADS_AUTOMATION_MODE={policy.mode.value}. "
            "Set ADS_AUTOMATION_MODE=CONTROLLED_LIVE or AUTONOMOUS_LIVE (kill switch)."
        )
    if apply:
        from ads_ai_agents.adapters.recovery_stability import assert_recovery_stable

        assert_recovery_stable()

    logger.info("Ads cycle start mode=%s apply=%s", policy.mode.value, apply)
    growth_apply = apply and os.getenv("ADS_ENABLE_GROWTH_MUTATIONS", "0") == "1"

    from ads_ai_agents.intelligence.grant_keyword_normalizer import (
        GrantKeywordNormalizer,
    )

    normalizer = GrantKeywordNormalizer().apply(dry_run=not apply)
    normalizer_result = normalizer.to_dict()
    store.write_json(day_dir, "grant_keyword_normalizer.json", normalizer_result)
    if apply and (
        normalizer.errors
        or normalizer.after_broad_keywords
        or normalizer.after_direct_groups
    ):
        raise RuntimeError(
            "Grant keyword normalizer could not preserve safe serving semantics."
        )

    from ads_ai_agents.intelligence.grant_account_guardian import GrantAccountGuardian

    guardian = GrantAccountGuardian().apply(dry_run=not apply)
    guardian_result = guardian.to_dict()
    store.write_json(day_dir, "grant_account_guardian.json", guardian_result)
    if apply and (
        guardian.errors
        or guardian.after_auto_apply_enabled
        or guardian.after_empty_ad_groups
        or guardian.after_direct_service_groups
        or guardian.after_broad_keywords
        or guardian.after_asset_policy_failures
        or guardian.after_rsa_policy_failures
    ):
        raise RuntimeError(
            "Grant guardian could not achieve a clean account structure; "
            "refusing downstream mutations."
        )

    from ads_ai_agents.intelligence.grant_delivery_recovery import GrantDeliveryRecovery

    delivery = GrantDeliveryRecovery().apply(dry_run=not apply)
    delivery_result = delivery.to_dict()
    store.write_json(day_dir, "grant_delivery_recovery.json", delivery_result)
    if (
        delivery.conversion_actions_planned
        or delivery.conversion_goals_planned
        or delivery.campaigns_planned
        or delivery.portfolio_strategies_planned
    ):
        ledger.record_decision(
            actor="controlled_live",
            agent_name="grant_delivery_recovery",
            customer_id=customer_id,
            campaign_id="",
            resource_type="grant_delivery_recovery",
            reason="Align conversion goals and Maximize Conversions bidding state",
            evidence={
                "bidding_strategy_chosen": delivery.bidding_strategy_chosen,
                "target_cpa_micros_chosen": delivery.target_cpa_micros_chosen,
            },
            expected_effect="maintain_canonical_grant_delivery_controls",
            approval_id=None,
            old_state=delivery.before,
            new_state=delivery.after,
            proposed_state={
                "conversion_actions_planned": delivery.conversion_actions_planned,
                "conversion_goals_planned": delivery.conversion_goals_planned,
                "campaigns_planned": delivery.campaigns_planned,
                "portfolio_strategies_planned": delivery.portfolio_strategies_planned,
            },
            rollback_payload={},
            impact_estimate={},
            major_variable="bidding_or_goals",
            applied=bool(apply and not delivery.errors),
            dry_run=not apply,
        )
    if apply and delivery.errors:
        raise RuntimeError(
            "Grant delivery guard could not preserve meaningful Contact goals "
            "and the evidence-based bidding state."
        )

    from ads_ai_agents.intelligence.grant_search_term_promoter import (
        GrantSearchTermPromoter,
    )

    promoter = GrantSearchTermPromoter().apply(dry_run=not growth_apply)
    promoter_result = promoter.to_dict()
    store.write_json(day_dir, "grant_search_term_promoter.json", promoter_result)
    if growth_apply and promoter.errors:
        raise RuntimeError(
            "Grant search-term promoter could not restore proven exact-match demand."
        )

    # Growth step: the normalizer and promoter keep the proven core healthy,
    # the expander widens it per locale from live demand and cross-locale intent.
    from ads_ai_agents.intelligence.semantic_expansion import GrantSemanticExpander

    expander = GrantSemanticExpander().apply(
        dry_run=not growth_apply,
        locales=policy.locales,
    )
    expander_result = expander.to_dict()
    store.write_json(day_dir, "grant_semantic_expansion.json", expander_result)
    if growth_apply and expander.errors:
        raise RuntimeError("Semantic expansion failed; keyword core was not widened.")

    audit = run_historical_audit(adapter, windows=policy.windows_days, store=store)
    window_30 = (audit["windows"].get("30d") or {})
    campaigns_30 = window_30.get("campaigns") or []
    search_terms_30 = window_30.get("search_terms") or []
    # Prefer 180d search terms for mining breadth when available
    search_terms_180 = (audit["windows"].get("180d") or {}).get("search_terms") or search_terms_30

    locale_pack = build_locale_strategy_pack(search_terms=search_terms_180, locales=policy.locales)
    store.write_json(day_dir, "locale_strategy_ua_de_en.json", locale_pack)

    # Recommendations = evidence only
    recs = [item.to_dict() for item in evaluate_recommendations(adapter.list_recommendations())]
    store.write_json(day_dir, "recommendations_evidence_only.json", recs)

    keyword_writer = GoogleAdsKeywordWriter()
    existing_negatives: set[str] = set()
    try:
        existing_negatives = keyword_writer.list_campaign_negatives()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not list existing negatives: %s", exc)

    negative_proposals = [
        p.to_dict()
        for p in mine_negative_proposals(search_terms_180, existing_negatives=existing_negatives)
    ]
    store.write_json(day_dir, "negative_proposals.json", negative_proposals)

    account_daily = 0.0
    seen_budgets: set[str] = set()
    for c in campaigns_30:
        rn = str(c.get("budget_resource_name") or "")
        if rn and rn in seen_budgets:
            continue
        if rn:
            seen_budgets.add(rn)
        if str(c.get("status") or "").upper() in {"ENABLED", "2"} or "ENABLED" in str(c.get("status") or "").upper():
            account_daily += float(c.get("daily_budget") or 0)

    budget_changes = [
        c.to_dict()
        for c in plan_budget_pilot(
            campaigns_30,
            policy=policy,
            ledger=ledger,
            account_daily_sum=account_daily,
        )
    ]
    store.write_json(day_dir, "budget_pilot_proposals.json", budget_changes)

    approval_queue = []
    for item in budget_changes:
        if item.get("requires_approval") or item.get("direction") == "pause_approval":
            approval_queue.append({"type": "budget_or_pause", **item})
    for item in negative_proposals:
        if not item.get("auto_apply"):
            approval_queue.append({"type": "negative", **item})
    store.write_json(day_dir, "approval_queue.json", approval_queue)

    # Baseline CPQA map for rollback monitor (compare 7d current vs 30d baseline / ledger metadata)
    window_7 = (audit["windows"].get("7d") or {})
    campaigns_7 = window_7.get("campaigns") or campaigns_30
    current_cpqa = {
        str(c.get("id")): c.get("cpqa") for c in campaigns_7 if c.get("id")
    }
    current_cpqa.update({str(c.get("name")): c.get("cpqa") for c in campaigns_7})

    baseline_cpqa = {
        str(c.get("id")): c.get("cpqa") for c in campaigns_30 if c.get("id")
    }
    baseline_cpqa.update({str(c.get("name")): c.get("cpqa") for c in campaigns_30})
    store.write_json(day_dir, "cpqa_baseline_30d.json", baseline_cpqa)

    rollback_eval = evaluate_cpqa_rollbacks(
        current_cpqa_by_campaign=current_cpqa,
        baseline_cpqa_by_campaign=baseline_cpqa,
        policy=policy,
        ledger=ledger,
        apply=False,  # first cycle: evaluate only; apply after delay with prior baseline
    )

    keyword_result: dict[str, Any] = {"dry_run": not apply, "attempted": [], "applied": [], "skipped": [], "errors": []}
    budget_result: dict[str, Any] = {"dry_run": not apply, "attempted": [], "applied": [], "skipped": [], "errors": []}

    auto_negatives = (
        [p for p in negative_proposals if p.get("auto_apply")]
        if policy.auto_add_negatives
        else []
    )
    # Prefer unique bank/harmful terms; writer expands to search campaigns
    keyword_result = keyword_writer.apply_negatives(
        auto_negatives,
        dry_run=not apply,
        max_apply=60,
    ).to_dict()

    for item in keyword_result.get("attempted") or []:
        applied_flag = apply and any(
            a.get("term") == item.get("term") and a.get("campaign_id") == item.get("campaign_id")
            for a in (keyword_result.get("applied") or [])
        )
        ledger.record_decision(
            actor="controlled_live",
            agent_name="keywords",
            customer_id=customer_id,
            campaign_id=str(item.get("campaign_id") or ""),
            resource_type="campaign_negative_keyword",
            reason=str(item.get("reason") or "high_confidence_negative"),
            evidence=item.get("evidence") or {},
            expected_effect="reduce_harmful_or_irrelevant_spend",
            approval_id=None if item.get("auto_apply") else "queued",
            old_state={"negative_present": False, "term": item.get("term")},
            new_state={"negative_present": True, "term": item.get("term"), "match_type": item.get("match_type")},
            proposed_state={"term": item.get("term"), "match_type": item.get("match_type")},
            rollback_payload=item.get("rollback_value") or {},
            impact_estimate={"confidence": item.get("confidence")},
            major_variable="negatives",
            applied=applied_flag,
            dry_run=not apply,
        )

    auto_budgets = [
        b
        for b in budget_changes
        if policy.auto_budget_under_cap
        and b.get("auto_apply")
        and b.get("direction") in {"increase", "decrease"}
        and b.get("budget_resource_name")
    ]
    # One major variable: if we already applied negatives to a campaign, skip budget same cycle
    if policy.one_major_variable_per_campaign:
        negative_campaigns = {
            str(a.get("campaign_id"))
            for a in (keyword_result.get("applied") or keyword_result.get("attempted") or [])
            if a.get("campaign_id")
        }
        auto_budgets = [b for b in auto_budgets if str(b.get("campaign_id")) not in negative_campaigns]

    budget_writer = GoogleAdsBudgetWriter()
    plan = {
        "changes": [
            {
                "action": "set_budget",
                "campaign_id": b["campaign_id"],
                "campaign_name": b["campaign_name"],
                "budget_resource_name": b["budget_resource_name"],
                "proposed_daily_budget": b["proposed_daily_budget"],
                "current_daily_budget": b["current_daily_budget"],
                "current_status": "ENABLED",
                "proposed_status": "ENABLED",
                "reason": b["reason"],
            }
            for b in auto_budgets
        ]
    }
    budget_result = budget_writer.apply_plan(plan, dry_run=not apply).to_dict()

    for b in auto_budgets:
        applied_flag = apply and any(
            a.get("campaign_id") == b.get("campaign_id") for a in (budget_result.get("applied") or [])
        )
        ledger.record_decision(
            actor="controlled_live",
            agent_name="budget",
            customer_id=customer_id,
            campaign_id=str(b.get("campaign_id") or ""),
            resource_type="campaign_budget",
            reason=b.get("reason") or "",
            evidence=b.get("evidence") or {},
            expected_effect=str((b.get("expected_impact") or {}).get("expected") or "budget_tilt"),
            approval_id=None,
            old_state={
                "daily_budget": b.get("current_daily_budget"),
                "campaign_name": b.get("campaign_name"),
            },
            new_state={
                "daily_budget": b.get("proposed_daily_budget"),
                "campaign_name": b.get("campaign_name"),
            },
            proposed_state={"daily_budget": b.get("proposed_daily_budget")},
            rollback_payload=b.get("rollback_value") or {},
            impact_estimate=b.get("expected_impact") or {},
            major_variable="budget",
            applied=applied_flag,
            dry_run=not apply,
            metadata={"cpqa_baseline": baseline_cpqa.get(str(b.get("campaign_id")))},
        )

    # Even AUTONOMOUS_LIVE may only propose assets by default. Publishing many
    # RSAs at once reset review/learning in August 2026, so live asset writes
    # require a separate explicit one-shot approval.
    asset_result: dict[str, Any] = {
        "skipped": True,
        "reason": "asset_publish_not_enabled_or_not_autonomous",
    }
    if policy.assets_auto_publish:
        from ads_ai_agents.intelligence.asset_publisher import GoogleAdsAssetPublisher

        asset_apply = (
            apply
            and os.getenv("ADS_ENABLE_ASSET_MUTATIONS", "").strip() == "APPROVED_ONCE"
        )
        publisher = GoogleAdsAssetPublisher()
        asset_pub = publisher.publish_gated_assets(
            dry_run=not asset_apply,
            max_rsa=80,
            link_pmax=True,
            scrub_clinical=True,
            rotate_pmax=True,
            locales=tuple(loc for loc in policy.locales if loc in {"UA", "EN"}),
        )
        asset_result = asset_pub.to_dict()
        ledger.record_decision(
            actor="autonomous_live",
            agent_name="creative_lab",
            customer_id=customer_id,
            campaign_id="",
            resource_type="rsa_pmax_assets",
            reason="Brand-gated Creative Lab hooks + PMax images",
            evidence={"gate": asset_result.get("gate"), "headlines": asset_result.get("headlines_used")},
            expected_effect="improve_rsa_relevance_and_pmax_creative_coverage",
            approval_id=None if policy.assets_auto_publish else "queued",
            old_state={},
            new_state={
                "rsa_patched": asset_result.get("rsa_patched"),
                "pmax_linked": asset_result.get("pmax_linked"),
            },
            proposed_state=asset_result,
            rollback_payload={"note": "Revert RSA headlines / unlink assets via Ads UI or prior ledger entry"},
            impact_estimate={"rsa_patched": asset_result.get("rsa_patched"), "pmax_linked": asset_result.get("pmax_linked")},
            major_variable="assets",
            applied=bool(
                asset_apply
                and (asset_result.get("rsa_patched") or asset_result.get("pmax_linked"))
            ),
            dry_run=not asset_apply,
        )

    summary = {
        "mode": policy.mode.value,
        "apply": apply,
        "policy": policy.to_dict(),
        "grant_guardian": guardian_result,
        "grant_keyword_normalizer": normalizer_result,
        "grant_delivery_recovery": delivery_result,
        "grant_search_term_promoter": promoter_result,
        "grant_semantic_expansion": {
            "demand_signals": expander_result.get("demand_signals"),
            "planned": len(expander_result.get("candidates_planned") or []),
            "created": expander_result.get("keywords_created"),
            "coverage": expander_result.get("coverage"),
        },
        "historical_audit": audit["summary"],
        "locale_strategy": {
            "locales": list(locale_pack.get("locales") or {}),
            "publish_assets": locale_pack.get("publish_assets"),
        },
        "negatives": {
            "proposed": len(negative_proposals),
            "auto": len(auto_negatives),
            "attempted": len(keyword_result.get("attempted") or []),
            "applied": len(keyword_result.get("applied") or []),
            "errors": len(keyword_result.get("errors") or []),
        },
        "budgets": {
            "proposed": len(budget_changes),
            "auto": len(auto_budgets),
            "attempted": len(budget_result.get("attempted") or []),
            "applied": len(budget_result.get("applied") or []),
            "errors": len(budget_result.get("errors") or []),
            "approval_pauses": len([b for b in budget_changes if b.get("direction") == "pause_approval"]),
        },
        "assets": asset_result,
        "approval_queue_count": len(approval_queue),
        "recommendations_evidence_only": len(recs),
        "rollback_eval": rollback_eval,
        "keyword_result": keyword_result,
        "budget_result": budget_result,
        "artifacts": str(day_dir),
        "prohibited": {
            "general_goals_mutate": policy.goals_mutate,
            "general_bidding_mutate": policy.bidding_mutate,
            "grant_delivery_exception": (
                "CONTACT goal quality + staged Maximize Clicks/Conversions are "
                "enforced by GrantDeliveryRecovery"
            ),
            "geo_expansion": policy.geo_expansion,
            "broad_match": "prohibited",
            "campaign_pause_without_approval": True,
            "asset_publish": policy.asset_publish,
        },
    }
    store.write_json(day_dir, "controlled_live_summary.json", summary)
    store.write_markdown(day_dir, "controlled_live.md", _markdown(summary, approval_queue))
    return summary


def _markdown(summary: dict[str, Any], approval_queue: list[dict[str, Any]]) -> str:
    assets = summary.get("assets") or {}
    lines = [
        f"# Ads cycle ({summary.get('mode')})",
        "",
        f"- Mode: `{summary.get('mode')}`",
        f"- Apply: **{summary.get('apply')}**",
        f"- Negatives applied/attempted: {summary['negatives']['applied']} / {summary['negatives']['attempted']}",
        f"- Budget auto applied/attempted: {summary['budgets']['applied']} / {summary['budgets']['attempted']}",
        f"- Assets RSA patched / PMax linked: {assets.get('rsa_patched', '—')} / {assets.get('pmax_linked', '—')}",
        f"- Approval queue: {summary.get('approval_queue_count')}",
        f"- Campaign pause approvals pending: {summary['budgets']['approval_pauses']}",
        "",
        "## Kill switch",
        "`ADS_AUTOMATION_MODE=READ_ONLY` blocks all mutations; "
        "`CONTROLLED_LIVE` / `AUTONOMOUS_LIVE` enable capped auto-apply.",
        "",
        "## Approval queue (sample)",
    ]
    for item in approval_queue[:20]:
        lines.append(
            f"- `{item.get('type')}` | {item.get('campaign_name') or item.get('term')} | "
            f"{item.get('direction') or item.get('action')} | {item.get('reason', '')[:120]}"
        )
    lines.extend(
        [
            "",
            "## Prohibited without approval",
            "- goals / conversion actions mutate",
            "- bidding strategy mutate",
            "- geo expansion / new countries",
            "- broad match",
            "- pause entire campaign",
            "- asset publish when policy.asset_publish=approval",
            "",
        ]
    )
    return "\n".join(lines) + "\n"
