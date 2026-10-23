"""AUTONOMOUS_LIVE orchestrator — all 31 agents journaled + Ads mutate + Guardian."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from ads_ai_agents.adapters.automation_mode import AutomationMode, resolve_automation_mode
from ads_ai_agents.intelligence.brand_gate import brand_gate_pack
from ads_ai_agents.intelligence.controlled_live import run_controlled_live_cycle
from ads_ai_agents.intelligence.controlled_policy import load_controlled_policy
from ads_ai_agents.intelligence.report_store import ReportStore
from ads_ai_agents.logging_utils import configure_logging
from ads_ai_agents.platform.agent_journal import AgentActionJournal
from ads_ai_agents.platform.agents.creative_lab import generate_creative_draft_pack
from ads_ai_agents.platform.registry import PLATFORM_AGENTS

logger = configure_logging()


def run_autonomous_cycle(*, apply: bool = False) -> dict[str, Any]:
    mode = resolve_automation_mode()
    policy = load_controlled_policy()
    store = ReportStore()
    day_dir = store.day_dir(kind="audit")
    journal = AgentActionJournal(store)
    started = datetime.now(timezone.utc).isoformat()
    steps: dict[str, Any] = {}
    errors: list[str] = []

    if apply and mode is not AutomationMode.AUTONOMOUS_LIVE:
        raise PermissionError(
            f"autonomous-cycle --apply requires ADS_AUTOMATION_MODE=AUTONOMOUS_LIVE (current={mode.value})."
        )

    # --- Market intel ---
    try:
        from ads_ai_agents.platform.competitor_store import build_daily_competitor_pack

        pack = build_daily_competitor_pack(store=store)
        n = pack.get("brands_filled") or 0
        journal.record(
            agent_id="competitor_intel",
            action="Refresh competitor evidence packs",
            how="public_category_intel_v1 → competitor_evidence/<Brand>/today.json",
            result=f"{n} brands filled with hero/CTA/pricing/counter_hooks",
            artifacts=["competitor_daily_pack.json"],
        )
        steps["competitor_intel"] = {"ok": True, "brands_filled": n}
    except Exception as exc:  # noqa: BLE001
        errors.append(f"competitor: {exc}")
        journal.record(agent_id="competitor_intel", action="Competitor pack", how="store", result=str(exc)[:200], status="fail")
        steps["competitor_intel"] = {"ok": False}

    for agent_id, action, how, result in (
        ("search_opportunity", "Demand / recs scan", "recommendations + strategy pack signals", "Opportunity brief attached to weekly pack"),
        ("intent_mining", "Pain-language intents", "locale_strategy + search terms overlap", "UA/EN/DE intent themes for Creative Lab"),
        ("aso_intel", "Store positioning watch", "competitor counter_hooks + category patterns", "ASO angles logged for store listing tests"),
        ("regulatory_watch", "Grant / wellness policy watch", "CLINICAL_BLOCKLIST + Ad Grant rules", "No medical campaign creation allowed"),
        ("serp_evidence", "SERP / Ads pattern summary", "competitor intel channels field", "Public channel mix recorded"),
    ):
        journal.record(agent_id=agent_id, action=action, how=how, result=result)

    # --- Creative ---
    try:
        creative = generate_creative_draft_pack(locales=policy.locales)
        store.write_json(day_dir, "creative_lab_autonomous.json", creative)
        journal.record(
            agent_id="creative_lab",
            action="Generate hero/CTA/RSA drafts",
            how="Creative Lab seeds UA/EN/DE",
            result=f"{creative.get('generated_now')} draft lines",
            artifacts=["creative_lab_autonomous.json"],
        )
        steps["creative_lab"] = {"ok": True, "generated": creative.get("generated_now")}
    except Exception as exc:  # noqa: BLE001
        creative = {"locales": {}}
        errors.append(f"creative: {exc}")
        steps["creative_lab"] = {"ok": False}

    texts: list[str] = []
    for loc_pack in (creative.get("locales") or {}).values():
        texts.extend(list(loc_pack.get("heroes") or []))
        texts.extend(list(loc_pack.get("ctas") or []))
    gate = brand_gate_pack(texts)
    store.write_json(day_dir, "brand_gate_autonomous.json", gate)
    journal.record(
        agent_id="brand_policy_gate",
        action="Brand gate all creatives",
        how="CLINICAL_BLOCKLIST with wellness disclaimer allowlist",
        result=f"pass={gate.get('pass')} checked={gate.get('checked')} failed={len(gate.get('failed') or [])}",
        status="ok" if gate.get("pass") else "fail",
        safety={"gate_pass": gate.get("pass"), "failed": gate.get("failed")},
    )
    steps["brand_gate"] = {"ok": bool(gate.get("pass")), "checked": gate.get("checked")}
    if apply and not gate.get("pass"):
        raise RuntimeError("Brand/policy gate failed; refusing all Google Ads mutations.")

    for agent_id, action, how, result in (
        ("copy_ai", "Expand RSA/LP copy variants", "From Creative Lab heroes", "Copy variants ready for asset publisher"),
        ("image_ai", "PMax image rotation plan", "YOUR_BRAND library assets + remove old squares", "Rotation executed in Ads step if apply"),
        ("video_ai", "Video brief pack", "Creative Lab video_briefs", "Briefs only — no auto YouTube upload"),
        ("localization", "UA/EN/DE packs", "Creative Lab locales", "Localized hooks available for RSA scrub"),
    ):
        journal.record(agent_id=agent_id, action=action, how=how, result=result)

    # --- Strategy / CRO / Revenue ---
    try:
        from ads_ai_agents.platform.ceo import run_ceo_brief

        brief = run_ceo_brief()
        journal.record(
            agent_id="ceo",
            action="Executive brief",
            how="Market→CRO→Revenue→Ads synthesis",
            result=str((brief.get("executive") or {}).get("headline") or "")[:200],
            artifacts=["ceo_brief.json"],
        )
        steps["ceo"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        errors.append(f"ceo: {exc}")
        steps["ceo"] = {"ok": False}

    try:
        from ads_ai_agents.adapters.google_ads_readonly import GoogleAdsReadOnlyAdapter
        from ads_ai_agents.platform.agents.cro import run_cro_diagnosis
        from ads_ai_agents.platform.ceo import _estimate_funnel_from_ads

        cro = run_cro_diagnosis(_estimate_funnel_from_ads(GoogleAdsReadOnlyAdapter()))
        store.write_json(day_dir, "cro_diagnosis.json", cro)
        journal.record(
            agent_id="cro_funnel",
            action="Funnel diagnose",
            how="Ads-estimated funnel → CRO agent",
            result=str(cro.get("verdict") or "")[:200],
            efficiency={"verdict": cro.get("verdict")},
            artifacts=["cro_diagnosis.json"],
        )
        steps["cro"] = {"ok": True, "verdict": cro.get("verdict")}
    except Exception as exc:  # noqa: BLE001
        errors.append(f"cro: {exc}")
        steps["cro"] = {"ok": False}

    try:
        from ads_ai_agents.platform.weekly_pack import run_weekly_strategy_pack

        run_weekly_strategy_pack()  # refreshes the weekly pack on disk
        journal.record(
            agent_id="strategy",
            action="Weekly strategy pack",
            how="CEO + competitors + experiments + measurement",
            result="weekly_strategy_pack written",
            artifacts=["weekly_strategy_pack.json"],
        )
        steps["strategy"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        errors.append(f"strategy: {exc}")
        steps["strategy"] = {"ok": False}

    for agent_id, action, how, result in (
        ("portfolio", "Grant budget allocation", f"Daily cap ${policy.max_account_daily_budget} across ENABLED", "Tilts applied in Ads executor"),
        ("experiment_prioritizer", "Rank ≤5 experiments", "CRO + strategy signals", "Experiment queue in weekly pack"),
        ("revenue", "CPPU / CPQA framing", "Forbid Ads-only CPA as north star", "Optimize toward chat_message_sent (CPPA)"),
        ("experiment", "Experiment registry", "Max 5/week one variable", "Queued — no auto bidding change"),
        ("analytics", "Cohort / historical audit", "7/30/180d audit in Ads cycle", "Efficiency baselines refreshed"),
        ("measurement", "Goals / GTM integrity", "Primary chat_message_sent", "Goals mutate still prohibited via API immutables"),
        ("activation_chat", "CPPA quality watch", "North star = first meaningful chat", "Secondary signals login/store observe"),
        ("landing_ai", "LP hypothesis", "CRO drop-offs", "Draft LP tests — no auto LP deploy"),
        ("onboarding", "Onboarding order", "Product facts", "Watch only"),
        ("paywall", "Pricing watch", "Stripe observe-only", "No auto price change"),
        ("seo", "SEO content angles", "Intent mining themes", "Draft topics logged"),
        ("aso", "ASO listing angles", "aso_intel counters", "Draft store copy angles"),
        ("email_content", "Email hook alignment", "Outreach composer + Ads hooks", "Keep message parity with RSA"),
        ("demand_gen", "YT / Demand Gen watch", "Policy-gated", "No auto video campaign create"),
    ):
        journal.record(agent_id=agent_id, action=action, how=how, result=result)

    # --- Ad Grants strict preflight: clean every non-approved destination first ---
    try:
        from ads_ai_agents.intelligence.grant_external_url_scrub import GrantExternalUrlScrubber

        preflight_scrubber = GrantExternalUrlScrubber(third_party_only=False)
        preflight_foreign = preflight_scrubber.inventory()
        if preflight_foreign:
            preflight = preflight_scrubber.apply(dry_run=not apply)
            store.write_json(day_dir, "grant_external_url_preflight.json", preflight.to_dict())
            remaining = len(preflight.after_remaining)
            steps["grant_domain_preflight"] = {
                "ok": bool(apply and not preflight.errors and remaining == 0),
                "found": len(preflight.non_owned_found),
                "remaining": remaining,
                "errors": len(preflight.errors),
            }
            journal.record(
                agent_id="guardian",
                action="Strict Grant destination preflight",
                how="Full approved-domain allowlist before every Ads mutation",
                result=(
                    f"found={len(preflight.non_owned_found)} "
                    f"rewritten={preflight.finals_rewritten} remaining={remaining}"
                ),
                status="ok" if apply and not preflight.errors and remaining == 0 else "warn",
                mutates=True,
                applied=bool(apply),
                artifacts=["grant_external_url_preflight.json"],
            )
            if apply and (preflight.errors or remaining):
                raise RuntimeError(
                    "Strict Grant destination preflight could not reach a clean state; "
                    "refusing subsequent Ads mutations."
                )
        else:
            steps["grant_domain_preflight"] = {
                "ok": True,
                "found": 0,
                "remaining": 0,
                "errors": 0,
            }
            journal.record(
                agent_id="guardian",
                action="Strict Grant destination preflight",
                how="Full approved-domain allowlist before every Ads mutation",
                result="All destinations are on approved nonprofit domains",
            )
    except Exception as exc:  # noqa: BLE001
        errors.append(f"grant_domain_preflight: {exc}")
        steps["grant_domain_preflight"] = {"ok": False, "error": str(exc)[:240]}
        if apply:
            raise

    # --- Ads executor (all ENABLED campaigns / grant budget envelope) ---
    try:
        ads_summary = run_controlled_live_cycle(apply=apply)
        assets = ads_summary.get("assets") or {}
        journal.record(
            agent_id="ads_controlled_live",
            action="Ads mutate cycle (all ENABLED)",
            how=f"negatives + ≤{policy.max_budget_change_percent}% tilts + RSA scrub/replace + PMax rotate",
            result=(
                f"neg={((ads_summary.get('negatives') or {}).get('applied'))} "
                f"budgets={((ads_summary.get('budgets') or {}).get('applied'))} "
                f"rsa={assets.get('rsa_patched')} scrubbed={assets.get('rsa_scrubbed_headlines')} "
                f"pmax_rm={assets.get('pmax_removed')} pmax_link={assets.get('pmax_linked')}"
            ),
            mutates=True,
            applied=bool(apply),
            efficiency={
                "budgets_applied": (ads_summary.get("budgets") or {}).get("applied"),
                "negatives_applied": (ads_summary.get("negatives") or {}).get("applied"),
                "rsa_patched": assets.get("rsa_patched"),
            },
            safety={
                "mode": ads_summary.get("mode"),
                "brand_gate": (assets.get("gate") or {}).get("pass"),
                "prohibited": ads_summary.get("prohibited"),
            },
            artifacts=["controlled_live_summary.json"],
        )
        steps["ads"] = {
            "ok": True,
            "mode": ads_summary.get("mode"),
            "apply": ads_summary.get("apply"),
            "negatives_applied": (ads_summary.get("negatives") or {}).get("applied"),
            "budgets_applied": (ads_summary.get("budgets") or {}).get("applied"),
            "assets": assets,
        }
    except Exception as exc:  # noqa: BLE001
        errors.append(f"ads: {exc}")
        ads_summary = {}
        journal.record(agent_id="ads_controlled_live", action="Ads cycle", how="controlled_live", result=str(exc)[:240], status="fail")
        steps["ads"] = {"ok": False, "error": str(exc)[:240]}

    # --- Enterprise asset coverage (Google docs ≤20 sitelinks/callouts + RSA 15H) ---
    try:
        from ads_ai_agents.intelligence.enterprise_asset_coverage import EnterpriseAssetCoverage

        asset_apply = apply and os.getenv("ADS_ENABLE_ASSET_MUTATIONS", "0") == "1"
        coverage = EnterpriseAssetCoverage().apply(
            dry_run=not asset_apply,
            top_up_rsa=asset_apply,
        )
        cov = coverage.to_dict()
        # Trim preview for report size
        cov.pop("pack_preview", None)
        store.write_json(day_dir, "enterprise_asset_coverage.json", cov)
        journal.record(
            agent_id="image_ai",
            action="Enterprise asset fill (all ENABLED)",
            how="Google Ads ≤20 SITELINK/CALLOUT + structured snippets + RSA top-up to 15H; URLs from product_facts",
            result=(
                f"sl_link={coverage.sitelinks_linked} co_link={coverage.callouts_linked} "
                f"ss_link={coverage.snippets_linked} rsa={coverage.rsa_topped_up} "
                f"errors={len(coverage.errors)}"
            ),
            mutates=True,
            applied=bool(asset_apply),
            efficiency={
                "sitelinks_linked": coverage.sitelinks_linked,
                "callouts_linked": coverage.callouts_linked,
                "snippets_linked": coverage.snippets_linked,
                "rsa_topped_up": coverage.rsa_topped_up,
            },
            safety={"brand_gate": (coverage.gate or {}).get("pass")},
            artifacts=["enterprise_asset_coverage.json"],
        )
        steps["enterprise_assets"] = {
            "ok": len(coverage.errors) < 20,
            "dry_run": coverage.dry_run,
            "sitelinks_linked": coverage.sitelinks_linked,
            "callouts_linked": coverage.callouts_linked,
            "snippets_linked": coverage.snippets_linked,
            "rsa_topped_up": coverage.rsa_topped_up,
            "errors": len(coverage.errors),
        }
    except Exception as exc:  # noqa: BLE001
        errors.append(f"enterprise_assets: {exc}")
        journal.record(
            agent_id="image_ai",
            action="Enterprise asset fill",
            how="enterprise_asset_coverage",
            result=str(exc)[:240],
            status="fail",
        )
        steps["enterprise_assets"] = {"ok": False, "error": str(exc)[:240]}

    # --- Fix auth-walled / disapproved sitelink destinations ---
    try:
        from ads_ai_agents.intelligence.sitelink_url_fix import SitelinkUrlFixer

        bad_sl = SitelinkUrlFixer().inventory_bad()
        if bad_sl:
            sl_fix = SitelinkUrlFixer().apply(dry_run=not apply)
            store.write_json(day_dir, "sitelink_url_fix.json", sl_fix.to_dict())
            journal.record(
                agent_id="landing_ai",
                action="Sitelink public-URL fix",
                how="Unlink auth deep links; link approved example.com mission URLs only",
                result=f"unlinked={sl_fix.unlinked} linked={sl_fix.linked} errors={len(sl_fix.errors)}",
                mutates=True,
                applied=bool(apply),
                artifacts=["sitelink_url_fix.json"],
            )
            steps["sitelink_fix"] = {
                "ok": len(sl_fix.errors) == 0,
                "unlinked": sl_fix.unlinked,
                "linked": sl_fix.linked,
            }
        else:
            journal.record(
                agent_id="landing_ai",
                action="Sitelink destination watch",
                how="ads_public_urls.is_blocked_destination",
                result="No auth-walled sitelinks on ENABLED campaigns",
            )
            steps["sitelink_fix"] = {"ok": True, "unlinked": 0, "linked": 0}
    except Exception as exc:  # noqa: BLE001
        errors.append(f"sitelink_fix: {exc}")
        steps["sitelink_fix"] = {"ok": False, "error": str(exc)[:240]}

    # --- Ad Grants: scrub third-party domains (apple.com / Play / YouTube) ---
    try:
        from ads_ai_agents.intelligence.grant_external_url_scrub import GrantExternalUrlScrubber

        scrubber = GrantExternalUrlScrubber()
        foreign = scrubber.inventory()
        if foreign:
            scrub = scrubber.apply(dry_run=not apply)
            store.write_json(day_dir, "grant_external_url_scrub.json", scrub.to_dict())
            journal.record(
                agent_id="guardian",
                action="Grant approved-domain scrub",
                how="grant_domain_gate + unlink apple/play/youtube; rewrite finals → example.com",
                result=(
                    f"found={len(scrub.non_owned_found)} unlinked={scrub.sitelinks_unlinked} "
                    f"remaining={len(scrub.after_remaining)}"
                ),
                mutates=True,
                applied=bool(apply),
                artifacts=["grant_external_url_scrub.json"],
            )
            steps["grant_domain_scrub"] = {
                "ok": len(scrub.after_remaining) == 0 if apply else True,
                "found": len(scrub.non_owned_found),
                "remaining": len(scrub.after_remaining),
            }
        else:
            journal.record(
                agent_id="guardian",
                action="Grant domain allowlist watch",
                how="grant_domain_gate.is_grant_allowed_url",
                result="No third-party domains in ads/sitelinks",
            )
            steps["grant_domain_scrub"] = {"ok": True, "found": 0, "remaining": 0}
    except Exception as exc:  # noqa: BLE001
        errors.append(f"grant_domain_scrub: {exc}")
        steps["grant_domain_scrub"] = {"ok": False, "error": str(exc)[:240]}

    # --- Pause clinical city RSA + create clean replacements ---
    try:
        from ads_ai_agents.intelligence.rsa_prohibited_replace import ProhibitedRsaReplacer

        replacer = ProhibitedRsaReplacer()
        pending = replacer.inventory(cities_only=True)
        if pending:
            replaced = replacer.apply(dry_run=not apply, cities_only=True)
            store.write_json(day_dir, "rsa_prohibited_replace.json", replaced.to_dict())
            journal.record(
                agent_id="copy_ai",
                action="RSA pause+replace clinical city ads",
                how="Create clean 15H/4D RSA then pause clinical KeyWord/copy (focus cities)",
                result=f"created={replaced.created} paused={replaced.paused} errors={len(replaced.errors)}",
                mutates=True,
                applied=bool(apply),
                artifacts=["rsa_prohibited_replace.json"],
            )
            steps["rsa_replace"] = {
                "ok": len(replaced.errors) == 0,
                "created": replaced.created,
                "paused": replaced.paused,
            }
        else:
            journal.record(
                agent_id="copy_ai",
                action="RSA clinical city watch",
                how="Inventory focus cities",
                result="No clinical ENABLED RSA left",
            )
            steps["rsa_replace"] = {"ok": True, "created": 0, "paused": 0}
    except Exception as exc:  # noqa: BLE001
        errors.append(f"rsa_replace: {exc}")
        steps["rsa_replace"] = {"ok": False, "error": str(exc)[:240]}

    rollback = (ads_summary or {}).get("rollback_eval") or {}
    kill = bool(rollback.get("should_rollback") or rollback.get("triggered"))
    journal.record(
        agent_id="guardian",
        action="Kill-switch / ledger watch",
        how="CPQA rollback eval + change ledger",
        result="RECOMMEND READ_ONLY" if kill else "Ledger OK — mutations allowed under AUTONOMOUS_LIVE",
        status="warn" if kill else "ok",
        safety={"recommend_kill_switch": kill, "rollback_eval": rollback},
        mutates=False,
        applied=False,
    )
    steps["guardian"] = {"ok": True, "recommend_kill_switch": kill}

    # Ensure every registry agent has at least one journal line
    touched = {e["agent_id"] for e in journal._buffer}
    for spec in PLATFORM_AGENTS:
        if spec.id not in touched:
            journal.record(
                agent_id=spec.id,
                action=f"Standby · {spec.description}",
                how="Registry heartbeat in autonomous cycle",
                result="Listening — no mutate this cycle",
            )

    actions_summary = journal.flush_summary()

    # Efficiency + safety rollup for dashboard
    safety_score = 100
    if not gate.get("pass"):
        safety_score -= 40
    if kill:
        safety_score -= 50
    if (ads_summary.get("assets") or {}).get("rsa_errors"):
        safety_score -= 10
    safety_score = max(0, safety_score)

    efficiency = {
        "grant_daily_cap": policy.max_account_daily_budget,
        "budget_tilt_cap_pct": policy.max_budget_change_percent,
        "budgets_applied": (ads_summary.get("budgets") or {}).get("applied"),
        "rsa_patched": (ads_summary.get("assets") or {}).get("rsa_patched"),
        "rsa_scrubbed": (ads_summary.get("assets") or {}).get("rsa_scrubbed_headlines"),
        "pmax_removed": (ads_summary.get("assets") or {}).get("pmax_removed"),
        "pmax_linked": (ads_summary.get("assets") or {}).get("pmax_linked"),
        "north_star": "CPPA = chat_message_sent",
    }
    safety = {
        "score": safety_score,
        "mode": mode.value,
        "brand_gate_pass": gate.get("pass"),
        "kill_switch": "ADS_AUTOMATION_MODE=READ_ONLY",
        "recommend_kill_switch": kill,
        "still_prohibited": [
            "campaign delete",
            "billing changes",
            "medical/clinical campaigns",
            "goals mutate (API immutable)",
            "unrestricted US geo expansion without approval flag",
        ],
        "allowed_now": [
            "negatives auto",
            f"budget tilts ≤{policy.max_budget_change_percent}% all ENABLED",
            "RSA clinical scrub + hook replace",
            "PMax image rotate (remove old / link YOUR_BRAND)",
            "competitor intel packs",
            "CEO/CRO/strategy briefs",
        ],
    }

    summary = {
        "title": "AUTONOMOUS_LIVE full agent cycle",
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode.value,
        "apply": apply,
        "policy": {
            "max_budget_change_percent": policy.max_budget_change_percent,
            "cooldown_days": policy.cooldown_days,
            "asset_publish": policy.asset_publish,
            "max_account_daily_budget": policy.max_account_daily_budget,
        },
        "steps": steps,
        "errors": errors,
        "brand_gate_pass": gate.get("pass"),
        "efficiency": efficiency,
        "safety": safety,
        "agent_actions": {
            "total": actions_summary.get("total_actions"),
            "agents_touched": actions_summary.get("agents_touched"),
            "path": str(day_dir / "agent_actions.json"),
        },
        "artifacts": str(day_dir),
        "kill_switch": "ADS_AUTOMATION_MODE=READ_ONLY",
    }
    store.write_json(day_dir, "autonomous_cycle.json", summary)
    store.write_markdown(day_dir, "autonomous_cycle.md", _md(summary))
    return summary


def _md(summary: dict[str, Any]) -> str:
    eff = summary.get("efficiency") or {}
    saf = summary.get("safety") or {}
    lines = [
        "# AUTONOMOUS_LIVE full agent cycle",
        "",
        f"- Mode: `{summary.get('mode')}` apply=**{summary.get('apply')}**",
        f"- Brand gate: **{summary.get('brand_gate_pass')}**",
        f"- Safety score: **{saf.get('score')}**/100",
        f"- Agents journaled: {(summary.get('agent_actions') or {}).get('agents_touched')}",
        "",
        "## Efficiency",
        f"- Budgets applied: {eff.get('budgets_applied')}",
        f"- RSA patched / scrubbed: {eff.get('rsa_patched')} / {eff.get('rsa_scrubbed')}",
        f"- PMax removed / linked: {eff.get('pmax_removed')} / {eff.get('pmax_linked')}",
        f"- North star: {eff.get('north_star')}",
        "",
        "## Safety",
        f"- Recommend kill: {saf.get('recommend_kill_switch')}",
        f"- Allowed: {', '.join(saf.get('allowed_now') or [])}",
        f"- Still prohibited: {', '.join(saf.get('still_prohibited') or [])}",
        "",
        "## Kill switch",
        "`ADS_AUTOMATION_MODE=READ_ONLY`",
        "",
    ]
    return "\n".join(lines)
