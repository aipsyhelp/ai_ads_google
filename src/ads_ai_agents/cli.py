from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ads_ai_agents.core.domain import MissionPlan, WorkflowStage
from ads_ai_agents.core.loader import load_campaign_draft
from ads_ai_agents.core.orchestrator import AdsOrchestrator
from ads_ai_agents.logging_utils import configure_logging, redact_secrets

logger = configure_logging()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ads-agents")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="Run enterprise mission plan for a campaign draft.")
    plan_parser.add_argument("campaign_json", type=Path)
    plan_parser.add_argument("--json", action="store_true")

    daily_parser = subparsers.add_parser("daily", help="Local agent daily cycle (draft recommendations).")
    daily_parser.add_argument("campaign_json", type=Path)
    daily_parser.add_argument("--json", action="store_true")
    daily_parser.add_argument("--stage", choices=[stage.value for stage in WorkflowStage])

    ping_parser = subparsers.add_parser("ping", help="Verify Google Ads API credentials.")
    ping_parser.add_argument("--json", action="store_true")

    sync_parser = subparsers.add_parser("sync", help="Lightweight read-only snapshot.")
    sync_parser.add_argument("--json", action="store_true")
    sync_parser.add_argument("--out", type=Path)

    subparsers.add_parser("audit", help="Full account audit snapshot → reports/YYYY-MM-DD/")

    conversions_parser = subparsers.add_parser("conversions", help="Conversion integrity tools.")
    conversions_sub = conversions_parser.add_subparsers(dest="conversions_command", required=True)
    conversions_sub.add_parser("audit", help="Classify conversion actions and primary_for_goal risks.")

    subparsers.add_parser("daily-run", help="Daily read-only intelligence loop → reports/daily/YYYY-MM-DD/")

    changes_parser = subparsers.add_parser("changes", help="Change ledger tools.")
    changes_sub = changes_parser.add_subparsers(dest="changes_command", required=True)
    changes_sub.add_parser("sync", help="Import Google change_event into independent ledger.")

    recommendations_parser = subparsers.add_parser("recommendations", help="Recommendation firewall.")
    recommendations_sub = recommendations_parser.add_subparsers(dest="recommendations_command", required=True)
    recommendations_sub.add_parser("evaluate", help="Classify Google recommendations by risk.")

    budget_parser = subparsers.add_parser("budget-plan", help="Shadow-mode budget reallocation planner.")
    budget_parser.add_argument(
        "--shadow",
        action="store_true",
        default=True,
        help="Shadow mode (default). No Google Ads mutations.",
    )
    budget_parser.add_argument("--json", action="store_true")

    goals_parser = subparsers.add_parser("goals", help="Conversion goal governance (plan-only).")
    goals_sub = goals_parser.add_subparsers(dest="goals_command", required=True)
    cleanup_parser = goals_sub.add_parser(
        "cleanup-plan",
        help="Build demote/keep/verify plan from conversions_integrity.json (no mutate).",
    )
    cleanup_parser.add_argument(
        "--from",
        dest="integrity_path",
        type=Path,
        help="Path to conversions_integrity.json (default: latest reports/YYYY-MM-DD/).",
    )
    cleanup_parser.add_argument("--json", action="store_true")

    apply_cleanup = goals_sub.add_parser(
        "apply-cleanup",
        help="Wave-1: demote micro primary_for_goal via ConversionActionService (budgets untouched).",
    )
    apply_cleanup.add_argument(
        "--apply",
        action="store_true",
        help="Actually mutate Google Ads. Without this flag, dry-run only.",
    )
    apply_cleanup.add_argument("--json", action="store_true")

    pipeline_parser = subparsers.add_parser("pipeline", help="Enterprise operating waves.")
    pipeline_sub = pipeline_parser.add_subparsers(dest="pipeline_command", required=True)
    wave1 = pipeline_sub.add_parser(
        "goals-cleanup",
        help="Full wave: integrity → demote micro (dry-run/apply) → CPQA → shadow budget → dashboard.",
    )
    wave1.add_argument(
        "--apply",
        action="store_true",
        help="Execute ConversionAction demotions. Default is dry-run only.",
    )
    wave1.add_argument("--json", action="store_true")

    wave2 = pipeline_sub.add_parser(
        "budget-pacing",
        help="Wave 2: remaining-month grant pacing (pause drains, fund Search Articles) + fire-test pack.",
    )
    wave2.add_argument(
        "--apply",
        action="store_true",
        help="Execute campaign budget/pause mutations. Default dry-run.",
    )
    wave2.add_argument("--json", action="store_true")

    controlled = subparsers.add_parser(
        "controlled-live",
        help="CONTROLLED_LIVE: 7/30/180d audit + locale strategy + capped auto negatives/budgets.",
    )
    controlled_sub = controlled.add_subparsers(dest="controlled_command", required=True)
    controlled_sub.add_parser(
        "audit",
        help="Historical multi-window audit only (7/30/180d). Always read-only.",
    )
    cycle = controlled_sub.add_parser(
        "cycle",
        help="Full controlled cycle. Default dry-run; --apply requires ADS_AUTOMATION_MODE=CONTROLLED_LIVE.",
    )
    cycle.add_argument(
        "--apply",
        action="store_true",
        help="Apply auto-safe changes (negatives + ≤10% budgets). Pauses/goals/bidding stay approval-only.",
    )
    cycle.add_argument("--json", action="store_true")
    controlled_sub.add_parser("mode", help="Show ADS_AUTOMATION_MODE kill-switch status.")

    platform = subparsers.add_parser(
        "platform",
        help="Revenue Growth AI OS (CEO brief, CRO diagnose, agent registry).",
    )
    platform_sub = platform.add_subparsers(dest="platform_command", required=True)
    brief_p = platform_sub.add_parser("brief", help="CEO AI executive brief across Market→CRO→Revenue→Ads.")
    brief_p.add_argument("--funnel-csv", type=Path, help="Optional funnel_90d.csv for real CPPU/CRO.")
    diag_p = platform_sub.add_parser("diagnose", help="CRO funnel diagnosis (CPPU north-star).")
    diag_p.add_argument("--funnel-csv", type=Path, help="Optional funnel_90d.csv.")
    platform_sub.add_parser("agents", help="List platform agent registry by layer.")
    platform_sub.add_parser("requirements", help="Print what client must provide next.")
    ingest_p = platform_sub.add_parser("ingest-funnel", help="Validate/aggregate funnel CSV → CPPU inputs.")
    ingest_p.add_argument("csv_path", type=Path)
    ingest_p.add_argument("--source", default=None)
    ingest_p.add_argument("--locale", default=None)
    ingest_p.add_argument("--json", action="store_true")
    weekly_p = platform_sub.add_parser("weekly", help="Weekly strategy pack (CEO+competitors+experiments+measurement).")
    weekly_p.add_argument("--funnel-csv", type=Path, help="Optional funnel_90d.csv.")
    platform_sub.add_parser("product", help="Print loaded product facts (LP/paywall/stores).")
    platform_sub.add_parser(
        "competitors",
        help="Autonomous top-10 mental health/wellbeing competitor pack.",
    )
    firetest_p = platform_sub.add_parser(
        "firetest",
        help="Print gclid fire-test URLs + store/purchase checklist.",
    )
    firetest_p.add_argument(
        "--gclid",
        default="FIRETEST_GCLID_REPLACE",
        help="gclid to embed in test landing URLs.",
    )
    auto_p = platform_sub.add_parser(
        "autonomous-cycle",
        help="AUTONOMOUS_LIVE orchestrator: creative→gate→CEO→CRO→strategy→Ads→guardian.",
    )
    auto_p.add_argument(
        "--apply",
        action="store_true",
        help="Apply Ads mutations (requires ADS_AUTOMATION_MODE=AUTONOMOUS_LIVE).",
    )
    auto_p.add_argument("--json", action="store_true")

    ent_p = platform_sub.add_parser(
        "enterprise-assets",
        help="Fill all ENABLED campaigns to Google Ads asset targets (sitelinks/callouts/snippets/RSA).",
    )
    ent_p.add_argument(
        "--apply",
        action="store_true",
        help="Create+link assets (requires ADS_AUTOMATION_MODE=AUTONOMOUS_LIVE).",
    )
    ent_p.add_argument("--json", action="store_true")
    ent_p.add_argument("--audit-only", action="store_true", help="Print gap audit only.")

    rsa_p = platform_sub.add_parser(
        "rsa-replace-prohibited",
        help="Pause clinical/PROHIBITED city RSAs and create clean brand-gated replacements.",
    )
    rsa_p.add_argument("--apply", action="store_true", help="Pause+create (AUTONOMOUS_LIVE required).")
    rsa_p.add_argument("--json", action="store_true")
    rsa_p.add_argument(
        "--all-search",
        action="store_true",
        help="Include all ENABLED Search (default: focus cities only).",
    )

    sl_p = platform_sub.add_parser(
        "sitelink-fix",
        help="Unlink auth-walled/disapproved sitelinks; link public crawlable destinations.",
    )
    sl_p.add_argument("--apply", action="store_true", help="Mutate Ads (AUTONOMOUS_LIVE required).")
    sl_p.add_argument("--json", action="store_true")

    vert_p = platform_sub.add_parser(
        "campaign-verticals",
        help="Create+ENABLE Corporate/NGO/App/News Search campaigns with public finals.",
    )
    vert_p.add_argument("--apply", action="store_true", help="Create in production (AUTONOMOUS_LIVE).")
    vert_p.add_argument("--json", action="store_true")
    vert_p.add_argument(
        "--paused",
        action="store_true",
        help="Create as PAUSED instead of ENABLED.",
    )

    bridge_p = platform_sub.add_parser(
        "grant-city-bridge",
        help="Create Grant-safe Kyiv/Berlin Search MaxClicks bridges (phrase KW, owned URLs).",
    )
    bridge_p.add_argument(
        "--apply",
        action="store_true",
        help="Create+ENABLE campaigns (CONTROLLED_LIVE/AUTONOMOUS_LIVE required).",
    )
    bridge_p.add_argument("--json", action="store_true")
    bridge_p.add_argument(
        "--paused",
        action="store_true",
        help="Create as PAUSED instead of ENABLED.",
    )

    expand_p = platform_sub.add_parser(
        "grant-semantic-expand",
        help="Grow the keyword core per locale from proven queries, cross-locale intents and demand.",
    )
    expand_p.add_argument(
        "--apply",
        action="store_true",
        help="Create the planned PHRASE keywords (CONTROLLED_LIVE/AUTONOMOUS_LIVE required).",
    )
    expand_p.add_argument("--json", action="store_true")
    expand_p.add_argument(
        "--locales",
        default="UA,EN,DE",
        help="Comma-separated locales to expand (default: UA,EN,DE).",
    )
    expand_p.add_argument(
        "--max-total",
        type=int,
        default=None,
        help="Cap new keywords for this run.",
    )
    expand_p.add_argument(
        "--no-keyword-ideas",
        action="store_true",
        help="Skip Keyword Planner demand (needs a Basic-access developer token).",
    )

    pmax_p = platform_sub.add_parser(
        "pmax-strength",
        help="Boost PMax asset groups: 15H/5D/logos/business name/videos toward Excellent.",
    )
    pmax_p.add_argument("--apply", action="store_true", help="Mutate Ads (AUTONOMOUS_LIVE).")
    pmax_p.add_argument("--json", action="store_true")

    grant_p = platform_sub.add_parser(
        "grant-compliance",
        help="Audit Ad Grants Website policy + permissions/certifications checklist.",
    )
    grant_p.add_argument("--json", action="store_true")

    greact_p = platform_sub.add_parser(
        "grant-reactivate",
        help="Pause risky verticals, scrub clinical/QS keywords, grant-safe finals + structure.",
    )
    greact_p.add_argument("--apply", action="store_true", help="Mutate Ads (AUTONOMOUS_LIVE).")
    greact_p.add_argument("--json", action="store_true")

    grecovery_p = platform_sub.add_parser(
        "grant-delivery-recovery",
        help="Recover Grant delivery with meaningful chat goals + Maximize Conversions.",
    )
    grecovery_p.add_argument(
        "--apply",
        action="store_true",
        help="Mutate conversion goals and Search bidding (controlled live mode required).",
    )
    grecovery_p.add_argument("--json", action="store_true")

    gscrub_p = platform_sub.add_parser(
        "grant-scrub-domains",
        help="Remove apple.com / Play / YouTube / non-approved domains from all ads & sitelinks.",
    )
    gscrub_p.add_argument("--apply", action="store_true", help="Mutate Ads (AUTONOMOUS_LIVE).")
    gscrub_p.add_argument("--json", action="store_true")

    subparsers.add_parser(
        "dashboard",
        help="Build efficiency dashboard JSON + tracking spec from latest audit reports.",
    )

    ui_parser = subparsers.add_parser(
        "ui",
        help="Local web dashboard + setup UI (http://127.0.0.1:8765, loopback only).",
    )
    ui_parser.add_argument("--host", default="127.0.0.1", help="Loopback address only (127.0.0.1, localhost or ::1).")
    ui_parser.add_argument("--port", type=int, default=8765)
    ui_parser.add_argument("--no-browser", action="store_true", help="Do not open a browser window.")

    init_parser = subparsers.add_parser(
        "init",
        help="Interactive local setup wizard (.env + product_facts + tenant; gitignored).",
    )
    init_parser.add_argument("--force", action="store_true", help="Overwrite existing local config.")
    init_parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Copy templates only; edit values manually.",
    )

    args = parser.parse_args(argv)

    if args.command == "ui":
        from ads_ai_agents.ui.server import run as run_ui

        try:
            return run_ui(args.host, args.port, open_browser=not args.no_browser)
        except ValueError as exc:
            print(exc)
            return 2

    if args.command == "init":
        return _init_project(force=args.force, non_interactive=args.non_interactive)

    if args.command == "plan":
        return _run(args.campaign_json, as_json=args.json, stage=None)
    if args.command == "daily":
        return _run(args.campaign_json, as_json=args.json, stage=args.stage)
    if args.command == "ping":
        return _ping(as_json=args.json)
    if args.command == "sync":
        return _sync(as_json=args.json, out=args.out)
    if args.command == "audit":
        return _audit()
    if args.command == "conversions" and args.conversions_command == "audit":
        return _conversions_audit()
    if args.command == "daily-run":
        return _daily_run()
    if args.command == "changes" and args.changes_command == "sync":
        return _changes_sync()
    if args.command == "recommendations" and args.recommendations_command == "evaluate":
        return _recommendations_evaluate()
    if args.command == "budget-plan":
        return _budget_plan(as_json=args.json)
    if args.command == "goals" and args.goals_command == "cleanup-plan":
        return _goals_cleanup_plan(integrity_path=args.integrity_path, as_json=args.json)
    if args.command == "goals" and args.goals_command == "apply-cleanup":
        return _pipeline_goals_cleanup(apply=args.apply, as_json=args.json)
    if args.command == "pipeline" and args.pipeline_command == "goals-cleanup":
        return _pipeline_goals_cleanup(apply=args.apply, as_json=args.json)
    if args.command == "pipeline" and args.pipeline_command == "budget-pacing":
        return _pipeline_budget_pacing(apply=args.apply, as_json=args.json)
    if args.command == "controlled-live" and args.controlled_command == "audit":
        return _controlled_audit()
    if args.command == "controlled-live" and args.controlled_command == "cycle":
        return _controlled_cycle(apply=args.apply, as_json=args.json)
    if args.command == "controlled-live" and args.controlled_command == "mode":
        return _controlled_mode()
    if args.command == "platform" and args.platform_command == "brief":
        return _platform_brief(funnel_csv=args.funnel_csv)
    if args.command == "platform" and args.platform_command == "diagnose":
        return _platform_diagnose(funnel_csv=args.funnel_csv)
    if args.command == "platform" and args.platform_command == "agents":
        return _platform_agents()
    if args.command == "platform" and args.platform_command == "requirements":
        return _platform_requirements()
    if args.command == "platform" and args.platform_command == "ingest-funnel":
        return _platform_ingest_funnel(args.csv_path, source=args.source, locale=args.locale, as_json=args.json)
    if args.command == "platform" and args.platform_command == "weekly":
        return _platform_weekly(funnel_csv=args.funnel_csv)
    if args.command == "platform" and args.platform_command == "product":
        return _platform_product()
    if args.command == "platform" and args.platform_command == "competitors":
        return _platform_competitors()
    if args.command == "platform" and args.platform_command == "firetest":
        return _platform_firetest(gclid=args.gclid)
    if args.command == "platform" and args.platform_command == "autonomous-cycle":
        return _platform_autonomous_cycle(apply=args.apply, as_json=args.json)
    if args.command == "platform" and args.platform_command == "enterprise-assets":
        return _platform_enterprise_assets(
            apply=args.apply, as_json=args.json, audit_only=args.audit_only
        )
    if args.command == "platform" and args.platform_command == "rsa-replace-prohibited":
        return _platform_rsa_replace(
            apply=args.apply, as_json=args.json, cities_only=not args.all_search
        )
    if args.command == "platform" and args.platform_command == "sitelink-fix":
        return _platform_sitelink_fix(apply=args.apply, as_json=args.json)
    if args.command == "platform" and args.platform_command == "campaign-verticals":
        return _platform_campaign_verticals(
            apply=args.apply, as_json=args.json, enable=not args.paused
        )
    if args.command == "platform" and args.platform_command == "grant-city-bridge":
        return _platform_grant_city_bridge(
            apply=args.apply, as_json=args.json, enable=not args.paused
        )
    if args.command == "platform" and args.platform_command == "grant-semantic-expand":
        return _platform_grant_semantic_expand(
            apply=args.apply,
            as_json=args.json,
            locales=args.locales,
            max_total=args.max_total,
            use_keyword_ideas=not args.no_keyword_ideas,
        )
    if args.command == "platform" and args.platform_command == "pmax-strength":
        return _platform_pmax_strength(apply=args.apply, as_json=args.json)
    if args.command == "platform" and args.platform_command == "grant-compliance":
        return _platform_grant_compliance(as_json=args.json)
    if args.command == "platform" and args.platform_command == "grant-reactivate":
        return _platform_grant_reactivate(apply=args.apply, as_json=args.json)
    if args.command == "platform" and args.platform_command == "grant-delivery-recovery":
        return _platform_grant_delivery_recovery(apply=args.apply, as_json=args.json)
    if args.command == "platform" and args.platform_command == "grant-scrub-domains":
        return _platform_grant_scrub_domains(apply=args.apply, as_json=args.json)
    if args.command == "dashboard":
        return _dashboard()

    parser.error(f"Unknown command: {args.command}")
    return 2


def _run(path: Path, *, as_json: bool, stage: str | None) -> int:
    campaign = load_campaign_draft(path)
    mission = AdsOrchestrator().daily_cycle(campaign)
    if stage:
        mission = _filter_stage(mission, stage)
    _emit(mission if as_json else _format_mission(mission), as_json=as_json)
    return 0 if mission.gate.can_publish else 1


def _init_project(*, force: bool = False, non_interactive: bool = False) -> int:
    """Delegate to scripts/init_project.py so the wizard stays a single source of truth."""
    import importlib.util

    wizard = Path(__file__).resolve().parents[2] / "scripts" / "init_project.py"
    if not wizard.is_file():
        # Editable install / src-layout fallback: repo root is parents[2] from
        # src/ads_ai_agents/cli.py → <root>/scripts/init_project.py
        wizard = Path.cwd() / "scripts" / "init_project.py"
    if not wizard.is_file():
        print("init wizard not found: scripts/init_project.py")
        return 1
    spec = importlib.util.spec_from_file_location("ads_init_wizard", wizard)
    if spec is None or spec.loader is None:
        print("failed to load init wizard")
        return 1
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return int(mod.run_wizard(force=force, non_interactive=non_interactive))


def _ping(*, as_json: bool) -> int:
    from ads_ai_agents.adapters.google_ads_readonly import GoogleAdsReadOnlyAdapter

    try:
        meta = GoogleAdsReadOnlyAdapter().ping()
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"Google Ads ping failed: {exc}"))
        return 1
    if as_json:
        print(json.dumps(meta, ensure_ascii=False, indent=2))
    else:
        print("Google Ads API OK (read-only)")
        print(f"- MCC / login customer: {meta['login_customer_id']}")
        print(f"- Customer: {meta['customer_id']} ({meta['descriptive_name']})")
        print(f"- Currency / TZ: {meta['currency_code']} / {meta['time_zone']}")
        print(f"- Auto-tagging: {meta.get('auto_tagging_enabled')}")
        print(f"- Conversion tracking: {meta.get('conversion_tracking_status')}")
        print(f"- Enhanced conversions for leads: {meta.get('enhanced_conversions_for_leads_enabled')}")
    return 0


def _sync(*, as_json: bool, out: Path | None) -> int:
    from ads_ai_agents.adapters.google_ads_readonly import GoogleAdsReadOnlyAdapter

    try:
        snapshot = GoogleAdsReadOnlyAdapter().snapshot()
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"Google Ads sync failed: {exc}"))
        return 1
    payload = _to_jsonable(snapshot)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Wrote snapshot: {out}")
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(f"Account: {snapshot.descriptive_name} ({snapshot.customer_id})")
    print(f"Campaigns: {len(snapshot.campaigns)}")
    if snapshot.performance:
        perf = snapshot.performance
        print(
            f"MTD: spend=${perf.spend_usd_mtd or 0:.2f} "
            f"reported_conversions={perf.leads_mtd or 0} "
            f"cpa_reported=${perf.cpa_usd if perf.cpa_usd is not None else 'n/a'}"
        )
        print("Note: use 'reported conversions', not 'qualified leads', until conversion audit proves quality.")
    return 0


def _audit() -> int:
    from ads_ai_agents.intelligence.account_audit import run_full_audit

    try:
        result = run_full_audit()
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"Audit failed: {exc}"))
        return 1
    executive = result["executive"]
    print(f"Audit written: {result['directory']}")
    print(f"Spend MTD: ${executive.get('spend_mtd')}")
    print(f"Reported conversions MTD: {executive.get('reported_conversions_mtd')}")
    print(f"CPA (reported): {executive.get('cpa_reported_conversions')}")
    print(f"CPQA: {executive.get('cpqa')} | CAC: {executive.get('cac')}")
    print(f"Primary actions: {executive.get('primary_actions')}")
    if executive.get("micro_primaries"):
        print(f"CRITICAL micro primaries: {executive['micro_primaries']}")
    return 0


def _conversions_audit() -> int:
    from ads_ai_agents.adapters.google_ads_readonly import GoogleAdsReadOnlyAdapter
    from ads_ai_agents.intelligence.conversion_audit import audit_conversion_actions
    from ads_ai_agents.intelligence.report_store import ReportStore

    adapter = GoogleAdsReadOnlyAdapter()
    store = ReportStore()
    day_dir = store.day_dir(kind="audit")
    try:
        rows = adapter.list_conversion_actions()
        perf = adapter.performance_snapshot()
        report = audit_conversion_actions(rows, spend_mtd=perf.spend_usd_mtd).to_dict()
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"Conversions audit failed: {exc}"))
        return 1

    store.write_json(day_dir, "conversions_integrity.json", report)
    store.write_csv(day_dir, "conversions.csv", report["actions"])
    store.write_markdown(day_dir, "conversions_audit.md", _conversions_markdown(report))

    print(f"Conversions audit written: {day_dir}")
    print(f"Reported conversions MTD: {report['reported_conversions_mtd']}")
    print(f"Primary: {report['primary_actions']}")
    print(f"Micro primaries: {report['micro_primaries'] or 'none'}")
    print(f"KPIs: {report['kpis']}")
    for line in report["narrative"]:
        print(f"- {line}")
    return 1 if report["micro_primaries"] else 0


def _daily_run() -> int:
    from ads_ai_agents.intelligence.daily_run import run_daily_intelligence

    try:
        result = run_daily_intelligence()
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"Daily-run failed: {exc}"))
        return 1
    daily = result["daily"]
    print(f"Daily intelligence written: {result['directory']}")
    print(f"Audit directory: {daily.get('audit_directory')}")
    pacing = daily.get("grant_pacing") or {}
    print(
        f"Spend MTD ${pacing.get('spend_mtd')} | "
        f"CPA reported {pacing.get('cpa_reported_conversions')} | "
        f"CPQA {pacing.get('cpqa')}"
    )
    return 0


def _changes_sync() -> int:
    from ads_ai_agents.adapters.google_ads_readonly import GoogleAdsReadOnlyAdapter
    from ads_ai_agents.intelligence.change_ledger import ChangeLedger

    adapter = GoogleAdsReadOnlyAdapter()
    try:
        meta = adapter.ping()
        events = adapter.list_change_events()
        created = ChangeLedger().sync_from_google_events(events, customer_id=meta["customer_id"])
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"Changes sync failed: {exc}"))
        return 1
    print(f"Imported {len(created)} change_event rows into independent ledger.")
    print("Note: Google change_event may be incomplete vs UI — local decision journal remains source of truth.")
    return 0


def _recommendations_evaluate() -> int:
    from ads_ai_agents.adapters.google_ads_readonly import GoogleAdsReadOnlyAdapter
    from ads_ai_agents.intelligence.recommendation_firewall import evaluate_recommendations
    from ads_ai_agents.intelligence.report_store import ReportStore

    adapter = GoogleAdsReadOnlyAdapter()
    store = ReportStore()
    day_dir = store.day_dir(kind="audit")
    try:
        evaluated = [item.to_dict() for item in evaluate_recommendations(adapter.list_recommendations())]
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"Recommendations evaluate failed: {exc}"))
        return 1
    store.write_json(day_dir, "recommendations.json", evaluated)
    counts: dict[str, int] = {}
    for item in evaluated:
        counts[item["decision"]] = counts.get(item["decision"], 0) + 1
    print(f"Evaluated {len(evaluated)} recommendations → {day_dir / 'recommendations.json'}")
    print(f"Decisions: {counts}")
    print("No recommendations were applied.")
    return 0


def _goals_cleanup_plan(*, integrity_path: Path | None, as_json: bool) -> int:
    from ads_ai_agents.intelligence.goal_cleanup import plan_from_integrity_file
    from ads_ai_agents.intelligence.report_store import ReportStore

    store = ReportStore()
    day_dir = store.day_dir(kind="audit")
    path = integrity_path or (day_dir / "conversions_integrity.json")
    if not path.exists():
        print(f"Missing integrity report: {path}. Run: ads-agents conversions audit")
        return 1
    try:
        plan = plan_from_integrity_file(path)
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"Goal cleanup plan failed: {exc}"))
        return 1

    out = store.write_json(day_dir, "goal_cleanup_plan.json", plan)
    store.write_markdown(day_dir, "goal_cleanup_plan.md", _goal_cleanup_markdown(plan))
    if as_json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    else:
        summary = plan["summary"]
        print(f"Goal cleanup plan written: {out}")
        print(f"Demote: {summary['demote_count']} | Keep: {summary['keep_count']} | Verify/review: {summary['verify_or_review_count']}")
        print(
            f"Micro conv in mix: {summary['micro_conversions_currently_in_mix']} → "
            f"projected quality reported: {summary['projected_reported_conversions_after_cleanup']}"
        )
        print(f"Projected CPA after cleanup: {summary['projected_cpa_after_cleanup']}")
        print("Target primary stack:")
        for name in plan["target_primary_stack"]:
            print(f"  - {name}")
        print("Mode=plan_only. No Google Ads goal mutations executed.")
    return 0


def _pipeline_goals_cleanup(*, apply: bool, as_json: bool) -> int:
    from ads_ai_agents.intelligence.pipeline import run_goals_cleanup_wave

    try:
        summary = run_goals_cleanup_wave(apply=apply)
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"Pipeline goals-cleanup failed: {exc}"))
        return 1

    if as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    else:
        mode = "APPLY" if apply else "DRY-RUN"
        print(f"Pipeline Wave 1 ({mode}) complete → {summary.get('artifacts')}")
        print(f"Demotions queued: {summary.get('dry_run_attempted')} | applied: {summary.get('applied')} | errors: {summary.get('apply_errors')}")
        print(f"Micro primaries before: {len(summary.get('micro_primaries_before') or [])}")
        print(f"Micro primaries after:  {len(summary.get('micro_primaries_after') or [])}")
        print(f"Budgets mutated: {summary.get('budgets_mutated')}")
        for ch in summary.get("channel_cpqa") or []:
            print(
                f"- {ch.get('channel')}: spend=${ch.get('spend_mtd')} "
                f"quality={ch.get('quality_conversions')} CPQA={ch.get('cpqa')}"
            )
        print(f"Summary: {Path(summary['artifacts']) / 'pipeline_wave1_summary.md'}")
    return 1 if summary.get("apply_errors") else 0


def _pipeline_budget_pacing(*, apply: bool, as_json: bool) -> int:
    from ads_ai_agents.intelligence.pipeline_budget import run_budget_pacing_wave

    try:
        summary = run_budget_pacing_wave(apply=apply)
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"Pipeline budget-pacing failed: {exc}"))
        return 1

    if as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    else:
        mode = "APPLY" if apply else "DRY-RUN"
        print(f"Pipeline Wave 2 ({mode}) complete → {summary.get('artifacts')}")
        print(
            f"Remaining ${summary.get('remaining_usd')} | target/day ${summary.get('target_daily_usd')} | "
            f"proposed daily sum ${summary.get('proposed_daily_budget_sum')}"
        )
        print(f"Queued: {summary.get('changes_queued')} | applied: {summary.get('applied')} | errors: {summary.get('errors')}")
        yt = summary.get("youtube_demote") or {}
        if yt.get("manual_ui_required"):
            print("YouTube demote: MANUAL UI required (API MUTATE_NOT_ALLOWED)")
        for ch in summary.get("channel_cpqa") or []:
            print(
                f"- {ch.get('channel')}: CPQA={ch.get('cpqa')} quality={ch.get('quality_conversions')} "
                f"spend=${ch.get('spend_mtd')}"
            )
        print(f"Plan: {Path(summary['artifacts']) / 'budget_pacing_plan.md'}")
        print(f"Fire-test: {Path(summary['artifacts']) / 'FIRETEST.md'}")
    return 1 if summary.get("errors") else 0


def _dashboard() -> int:
    from ads_ai_agents.intelligence.dashboard import write_efficiency_dashboard

    try:
        path = write_efficiency_dashboard()
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"Dashboard build failed: {exc}"))
        return 1
    print(f"Efficiency dashboard written: {path}")
    print(f"Tracking spec: {path.parent / 'TRACKING.md'}")
    return 0


def _controlled_mode() -> int:
    from ads_ai_agents.adapters.automation_mode import resolve_automation_mode
    from ads_ai_agents.intelligence.controlled_policy import load_controlled_policy

    mode = resolve_automation_mode()
    policy = load_controlled_policy()
    print(f"ADS_AUTOMATION_MODE={mode.value}")
    print(f"mutations_enabled={policy.mutations_enabled}")
    print(
        f"budget_cap={policy.max_budget_change_percent}% cooldown={policy.cooldown_days}d "
        f"approval_above={policy.require_approval_above_percent}% "
        f"max_account_daily=${policy.max_account_daily_budget}"
    )
    print("Kill switch: set ADS_AUTOMATION_MODE=READ_ONLY to freeze all writes.")
    print("Modes: READ_ONLY | CONTROLLED_LIVE | AUTONOMOUS_LIVE")
    return 0


def _load_optional_funnel(funnel_csv: Path | None):
    if not funnel_csv:
        return None
    from ads_ai_agents.platform.funnel_ingest import aggregate_funnel, load_funnel_csv

    return aggregate_funnel(load_funnel_csv(funnel_csv))


def _platform_brief(*, funnel_csv: Path | None = None) -> int:
    from ads_ai_agents.platform.ceo import run_ceo_brief

    try:
        brief = run_ceo_brief(funnel=_load_optional_funnel(funnel_csv))
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"Platform brief failed: {exc}"))
        return 1
    ex = brief["executive"]
    print(ex["title"])
    print(f"North star: {ex['north_star']}")
    print(f"Ads mode: {ex['ads_automation_mode']}")
    print(ex["headline"])
    print(f"CRO: {ex.get('cro_verdict')}")
    for p in (ex.get("top_priorities") or [])[:4]:
        print(f"  {p.get('rank')}. {p.get('theme')}")
    print(f"Artifacts: {brief.get('artifacts')}/ceo_brief.md")
    print("Client inputs: docs/REQUIREMENTS_FROM_CLIENT.md")
    return 0


def _platform_diagnose(*, funnel_csv: Path | None = None) -> int:
    from ads_ai_agents.platform.ceo import _estimate_funnel_from_ads
    from ads_ai_agents.platform.agents.cro import run_cro_diagnosis
    from ads_ai_agents.adapters.google_ads_readonly import GoogleAdsReadOnlyAdapter
    from ads_ai_agents.intelligence.report_store import ReportStore

    try:
        funnel = _load_optional_funnel(funnel_csv)
        if funnel is None:
            adapter = GoogleAdsReadOnlyAdapter()
            funnel = _estimate_funnel_from_ads(adapter)
        cro = run_cro_diagnosis(funnel)
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"Platform diagnose failed: {exc}"))
        return 1
    store = ReportStore()
    day_dir = store.day_dir(kind="audit")
    store.write_json(day_dir, "cro_diagnosis.json", cro)
    print(cro.get("verdict"))
    for f in cro.get("findings") or []:
        print(f"- [{f.get('severity')}] {f.get('stage')}: {f.get('message')}")
    print("Actions:")
    for a in (cro.get("recommended_actions") or [])[:8]:
        print(f"  * {a}")
    print(f"Wrote: {day_dir / 'cro_diagnosis.json'}")
    return 0


def _platform_ingest_funnel(
    csv_path: Path, *, source: str | None, locale: str | None, as_json: bool
) -> int:
    from ads_ai_agents.intelligence.report_store import ReportStore
    from ads_ai_agents.platform.funnel_ingest import ingest_funnel_file
    from ads_ai_agents.platform.agents.revenue import run_revenue_review
    from ads_ai_agents.platform.north_star import FunnelSnapshot

    try:
        result = ingest_funnel_file(csv_path, source=source, locale=locale)
        funnel = FunnelSnapshot(**result["funnel"])
        revenue = run_revenue_review(funnel)
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"Funnel ingest failed: {exc}"))
        return 1
    store = ReportStore()
    day_dir = store.day_dir(kind="audit")
    payload = {**result, "revenue": revenue}
    store.write_json(day_dir, "funnel_ingest.json", payload)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        f = result["funnel"]
        print(f"Ingested {result['rows']} rows from {csv_path}")
        print(
            f"Spend ${f['ad_spend_usd']:.2f} | clicks {f['ad_clicks']:.0f} | "
            f"chat {f['first_chat']:.0f} | paying {f['paying_users']:.0f} | revenue ${f['revenue_usd']:.2f}"
        )
        print(f"CPPU: {revenue['kpis'].get('cost_per_paying_user')}")
        print(f"Wrote: {day_dir / 'funnel_ingest.json'}")
        print("Template: samples/funnel_90d_template.csv")
    return 0


def _platform_weekly(*, funnel_csv: Path | None = None) -> int:
    from ads_ai_agents.platform.weekly_pack import run_weekly_strategy_pack

    try:
        pack = run_weekly_strategy_pack(funnel_csv=funnel_csv)
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"Weekly pack failed: {exc}"))
        return 1
    print("Weekly strategy pack ready")
    print(pack.get("ceo_headline"))
    print(f"Experiments: {len((pack.get('experiments') or {}).get('selected') or [])}")
    print(f"Artifacts: {pack.get('artifacts')}/weekly_strategy_pack.md")
    return 0


def _platform_agents() -> int:
    from ads_ai_agents.platform.registry import PLATFORM_AGENTS

    by_layer: dict[str, list[str]] = {}
    for agent in PLATFORM_AGENTS:
        by_layer.setdefault(agent.layer.value, []).append(
            f"{agent.id} [{agent.phase}/{agent.mutates}] — {agent.name}"
        )
    for layer, items in by_layer.items():
        print(f"\n## {layer} ({len(items)})")
        for item in items:
            print(f"  - {item}")
    print(f"\nTotal agents: {len(PLATFORM_AGENTS)}")
    return 0


def _platform_requirements() -> int:
    path = Path(__file__).resolve().parents[2] / "docs" / "REQUIREMENTS_FROM_CLIENT.md"
    if not path.is_file():
        print("Missing docs/REQUIREMENTS_FROM_CLIENT.md")
        return 1
    print(path.read_text(encoding="utf-8"))
    return 0


def _platform_product() -> int:
    import json

    from ads_ai_agents.platform.product_facts import load_product_facts, store_click_events

    facts = load_product_facts()
    print(json.dumps(facts, ensure_ascii=False, indent=2))
    print("\n## Store click events (Secondary only)")
    for ev in store_click_events(facts):
        print(f"- {ev['event']}: match `{ev['match']}` → {ev['url']}")
    print("\nGTM guide: docs/GTM_STORE_CLICKS.md")
    print("Activation tracking: docs/ACTIVATION_TRACKING_SPEC.md")
    return 0 if facts.get("status") == "loaded" else 1


def _platform_competitors() -> int:
    from ads_ai_agents.intelligence.report_store import ReportStore
    from ads_ai_agents.platform.agents.competitor_discovery import discover_top_competitors

    pack = discover_top_competitors()
    store = ReportStore()
    day_dir = store.day_dir(kind="audit")
    store.write_json(day_dir, "competitors_top10.json", pack)
    print(f"Top {pack['count']} mental health / wellbeing competitors")
    for c in pack["competitors"]:
        print(f"  {c['rank']}. {c['brand']} [{c['category']}] — {c['positioning'][:80]}")
    print("Playbook:")
    for line in pack["your_brand_playbook"]:
        print(f"  * {line}")
    print(f"Wrote: {day_dir / 'competitors_top10.json'}")
    return 0


def _platform_firetest(*, gclid: str) -> int:
    from ads_ai_agents.intelligence.report_store import ReportStore
    from ads_ai_agents.platform.product_facts import firetest_urls, load_product_facts

    facts = load_product_facts()
    urls = firetest_urls(gclid=gclid)
    pricing = (facts.get("pricing") or {}).get("stripe_live") or {}
    lines = [
        "# Fire-test pack (product facts)",
        "",
        f"gclid=`{gclid}` — replace with a real click id from Ads diagnostics if testing end-to-end.",
        "",
        "## 1) Store clicks (after GTM publish)",
        f"- LP UA: {urls['lp_ua']}",
        f"- LP EN: {urls['lp_en']}",
        f"- LP DE: {urls['lp_de']}",
        f"- Chat login: {urls['chat_login']}",
        "- Click Google Play + App Store on each surface once",
        "- Expect GA4: `click_store_android` / `click_store_ios` (Secondary in Ads)",
        "",
        "## 2) Chat path",
        f"- Open chat home after login: {urls['chat_home']}",
        "- Send one message (chat_start)",
        "",
        "## 3) Purchase path (no separate thank-you page)",
        "- Side menu → Buy subscription → Stripe Checkout",
        f"- Live price: €{pricing.get('eur_monthly')} monthly",
        "- Complete sandbox/real pay OR stop at Checkout if not charging",
        "- Success signal: redirect `/chat` + subscription days increase",
        "- Ads: confirm `Purchase` in Diagnostics ≤3h when payment fires with gclid",
        "",
        "## 4) Activation code path",
        "- Side menu → Enter activation code → submit",
        "- Success: days counter increases → event `activation_success`",
        "",
        "Docs: `docs/GTM_STORE_CLICKS.md`, `reports/*/evidence/event_map_recommended.md`",
        "",
    ]
    body = "\n".join(lines)
    store = ReportStore()
    day_dir = store.day_dir(kind="audit")
    path = day_dir / "FIRETEST_PRODUCT.md"
    path.write_text(body, encoding="utf-8")
    print(body)
    print(f"Wrote: {path}")
    return 0


def _platform_autonomous_cycle(*, apply: bool, as_json: bool) -> int:
    import json

    from ads_ai_agents.platform.autonomous_cycle import run_autonomous_cycle

    try:
        summary = run_autonomous_cycle(apply=apply)
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"AUTONOMOUS_LIVE cycle failed: {exc}"))
        return 1
    if as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        return 0
    print(f"AUTONOMOUS_LIVE cycle mode={summary.get('mode')} apply={summary.get('apply')}")
    print(f"Brand gate: {summary.get('brand_gate_pass')}")
    for name, step in (summary.get("steps") or {}).items():
        status = "OK" if step.get("ok") else "FAIL"
        print(f"  [{status}] {name}")
    if summary.get("errors"):
        for e in summary["errors"]:
            print(f"  ! {e}")
    guardian = (summary.get("steps") or {}).get("guardian") or {}
    if guardian.get("recommend_kill_switch"):
        print(f"GUARDIAN: {guardian.get('message')}")
    print(f"Artifacts: {summary.get('artifacts')}/autonomous_cycle.md")
    return 0


def _platform_enterprise_assets(*, apply: bool, as_json: bool, audit_only: bool) -> int:
    import json

    from ads_ai_agents.intelligence.enterprise_asset_coverage import EnterpriseAssetCoverage
    from ads_ai_agents.intelligence.report_store import ReportStore

    cov = EnterpriseAssetCoverage()
    if audit_only:
        audit = cov.audit()
        if as_json:
            print(json.dumps(audit, ensure_ascii=False, indent=2, default=str))
        else:
            print(f"Enterprise asset audit · {audit.get('standard')}")
            print(f"Targets: {audit.get('targets')}")
            for c in audit.get("campaigns") or []:
                g = c.get("gaps") or {}
                print(
                    f"  [{c.get('locale')}] {c.get('name', '')[:55]} | "
                    f"SL {c.get('sitelinks')}/16 CO {c.get('callouts')}/16 SS {c.get('snippets')}/4 | "
                    f"RSA n={c.get('rsa_count')} H≈{c.get('rsa_avg_headlines')} | "
                    f"gaps SL+{g.get('sitelinks_to_target')} CO+{g.get('callouts_to_target')} "
                    f"SS+{g.get('snippets_to_target')} RSA_short={g.get('rsa_headlines_short')}"
                )
        return 0

    try:
        result = cov.apply(dry_run=not apply, top_up_rsa=True)
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"Enterprise assets failed: {exc}"))
        return 1

    store = ReportStore()
    day_dir = store.day_dir(kind="audit")
    payload = result.to_dict()
    payload.pop("pack_preview", None)
    out = store.write_json(day_dir, "enterprise_asset_coverage.json", payload)

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0 if len(result.errors) < 50 else 1

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"Enterprise assets [{mode}] campaigns={result.campaigns} gate={result.gate.get('pass')}")
    print(
        f"  sitelinks created={result.sitelinks_created} linked={result.sitelinks_linked} | "
        f"callouts created={result.callouts_created} linked={result.callouts_linked} | "
        f"snippets created={result.snippets_created} linked={result.snippets_linked}"
    )
    print(f"  RSA topped up={result.rsa_topped_up}/{result.rsa_attempted}")
    if result.errors:
        print(f"  errors={len(result.errors)} (showing ≤8):")
        for e in result.errors[:8]:
            print(f"    ! {e}")
    print(f"Wrote: {out}")
    return 0 if len(result.errors) < 50 else 1


def _platform_grant_scrub_domains(*, apply: bool, as_json: bool) -> int:
    import json
    from collections import Counter

    from ads_ai_agents.intelligence.grant_external_url_scrub import GrantExternalUrlScrubber
    from ads_ai_agents.intelligence.report_store import ReportStore

    try:
        result = GrantExternalUrlScrubber().apply(dry_run=not apply)
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"Grant domain scrub failed: {exc}"))
        return 1

    payload = result.to_dict()
    store = ReportStore()
    day_dir = store.day_dir(kind="audit")
    out = store.write_json(day_dir, "grant_external_url_scrub.json", payload)

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0 if not result.after_remaining else 1

    mode = "APPLY" if apply else "DRY-RUN"
    hosts = Counter(r.get("host") for r in result.non_owned_found)
    print(f"Grant domain scrub [{mode}] found={len(result.non_owned_found)} hosts={dict(hosts)}")
    print(
        f"  sitelinks_unlinked={result.sitelinks_unlinked} "
        f"customer_unlinked={result.customer_assets_unlinked} "
        f"paused={len(result.campaigns_paused)} finals_rewritten={result.finals_rewritten} "
        f"errors={len(result.errors)}"
    )
    print(f"  remaining_non_owned={len(result.after_remaining)}")
    for row in (result.after_remaining or [])[:15]:
        print(f"    LEFT {row.get('host')} | {row.get('kind')} | {str(row.get('campaign'))[:50]}")
    if result.errors:
        for e in result.errors[:8]:
            print(f"    ! {e}")
    print(f"Wrote: {out}")
    return 0 if (apply and not result.after_remaining) or (not apply) else 1


def _platform_grant_reactivate(*, apply: bool, as_json: bool) -> int:
    import json

    from ads_ai_agents.intelligence.grant_reactivation_fix import GrantReactivationFixer
    from ads_ai_agents.intelligence.report_store import ReportStore

    try:
        result = GrantReactivationFixer().apply(dry_run=not apply)
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"Grant reactivate fix failed: {exc}"))
        return 1

    payload = result.to_dict()
    store = ReportStore()
    day_dir = store.day_dir(kind="audit")
    out = store.write_json(day_dir, "grant_reactivation_fix.json", payload)

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0 if len(result.errors) < 25 else 1

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"Grant reactivation fix [{mode}]")
    print(f"  paused_campaigns={len(result.campaigns_paused)} renamed={len(result.campaigns_renamed)}")
    print(
        f"  keywords_paused={result.keywords_paused} finals={result.finals_updated} "
        f"ags={result.ad_groups_created} rsa={result.rsa_created} sitelinks={result.sitelinks_linked} "
        f"errors={len(result.errors)}"
    )
    for n in result.campaigns_paused[:20]:
        print(f"    PAUSE {n}")
    for row in (result.campaigns_renamed or [])[:12]:
        print(f"    RENAME {row.get('from')} → {row.get('to')}")
    for s in (result.structure or [])[:15]:
        mark = "OK" if s.get("grant_ok") else "GAP"
        print(
            f"    {mark} AG={s.get('ad_groups')} ads={s.get('ads')} SL={s.get('sitelinks')} | {s.get('name')}"
        )
    fc = result.form_checklist or {}
    print(f"  Form: {fc.get('form_url')}")
    if fc.get("message_draft_uk"):
        print(f"  Draft: {fc['message_draft_uk'][:280]}...")
    if result.errors:
        for e in result.errors[:8]:
            print(f"    ! {e}")
    print(f"Wrote: {out}")
    return 0 if len(result.errors) < 25 else 1


def _platform_grant_compliance(*, as_json: bool) -> int:
    import json

    from ads_ai_agents.intelligence.grant_compliance import audit
    from ads_ai_agents.intelligence.report_store import ReportStore

    try:
        report = audit()
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"Grant compliance audit failed: {exc}"))
        return 1

    payload = report.to_dict()
    store = ReportStore()
    day_dir = store.day_dir(kind="audit")
    out = store.write_json(day_dir, "grant_compliance.json", payload)

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0 if report.overall != "FAIL" else 1

    print(f"Ad Grants compliance: {report.overall} (website={report.website_verdict})")
    print(f"  {report.config_note}")
    if report.blockers:
        print("  Blockers:")
        for b in report.blockers[:12]:
            print(f"    - {b}")
    if report.warnings:
        print("  Warnings:")
        for w in report.warnings[:8]:
            print(f"    - {w}")
    print("  Permissions / certifications:")
    for item in report.permissions_checklist:
        print(f"    [{item['priority']}] {item['status']:12} {item['title']}")
        print(f"             → {item['action']}")
    print("  Reactivation:")
    for step in report.reactivation_steps[:7]:
        print(f"    • {step}")
    print("Docs: docs/AD_GRANTS_COMPLIANCE.md")
    print(f"Wrote: {out}")
    return 0 if report.overall != "FAIL" else 1


def _platform_pmax_strength(*, apply: bool, as_json: bool) -> int:
    import json

    from ads_ai_agents.intelligence.pmax_ad_strength import PMaxAdStrengthBooster
    from ads_ai_agents.intelligence.report_store import ReportStore

    try:
        result = PMaxAdStrengthBooster().apply(dry_run=not apply)
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"PMax strength boost failed: {exc}"))
        return 1

    store = ReportStore()
    day_dir = store.day_dir(kind="audit")
    payload = result.to_dict()
    out = store.write_json(day_dir, "pmax_ad_strength.json", payload)

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0 if len(result.errors) < 20 else 1

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"PMax ad strength [{mode}] groups={result.asset_groups} gate={result.gate.get('pass')}")
    print(
        f"  text_created={result.text_created} text_linked={result.text_linked} "
        f"logos={result.logos_linked} business={result.business_linked} "
        f"videos={result.videos_linked} images={result.images_linked} errors={len(result.errors)}"
    )
    for g in (result.before or [])[:8]:
        c = g.get("counts") or {}
        print(
            f"  before {g.get('strength'):10} H={c.get('HEADLINE',0)} D={c.get('DESCRIPTION',0)} "
            f"L={c.get('LOGO',0)} BN={c.get('BUSINESS_NAME',0)} YT={c.get('YOUTUBE_VIDEO',0)} "
            f"| {g.get('asset_group')}"
        )
    if apply:
        for g in (result.after or [])[:8]:
            c = g.get("counts") or {}
            print(
                f"  after  {g.get('strength'):10} H={c.get('HEADLINE',0)} D={c.get('DESCRIPTION',0)} "
                f"L={c.get('LOGO',0)} BN={c.get('BUSINESS_NAME',0)} YT={c.get('YOUTUBE_VIDEO',0)} "
                f"| {g.get('asset_group')}"
            )
    if result.errors:
        for e in result.errors[:8]:
            print(f"    ! {e}")
    print(f"Wrote: {out}")
    return 0 if len(result.errors) < 25 else 1


def _platform_grant_city_bridge(*, apply: bool, as_json: bool, enable: bool) -> int:
    import json

    from ads_ai_agents.intelligence.grant_city_bridge_launcher import (
        GrantCityBridgeLauncher,
    )
    from ads_ai_agents.intelligence.report_store import ReportStore

    try:
        result = GrantCityBridgeLauncher().apply(dry_run=not apply, enable=enable)
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"Grant city bridge failed: {exc}"))
        return 1

    store = ReportStore()
    day_dir = store.day_dir(kind="audit")
    payload = result.to_dict()
    out = store.write_json(day_dir, "grant_city_bridge.json", payload)

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0 if not result.errors else 1

    mode = "APPLY" if apply else "DRY-RUN"
    status = "ENABLED" if enable else "PAUSED"
    print(f"Grant city bridge [{mode}] create_as={status} gate={result.gate.get('pass')}")
    print(
        f"  planned={len(result.planned)} created={len(result.created)} "
        f"skipped={len(result.skipped)} paused_legacy={len(result.paused_existing)} "
        f"budget_changes={len(result.budget_changes)} errors={len(result.errors)}"
    )
    if result.after_budget_total is not None:
        print(f"  after_budget_total=${result.after_budget_total}")
    for item in result.created[:10]:
        print(
            f"    + {item.get('name')} | groups={item.get('ad_groups')} "
            f"kw={item.get('keywords')} rsa={item.get('rsa')} sl={item.get('sitelinks')}"
        )
    for err in result.errors[:8]:
        print(f"    ! {err}")
    print(f"Wrote: {out}")
    return 0 if not result.errors else 1


def _platform_grant_semantic_expand(
    *,
    apply: bool,
    as_json: bool,
    locales: str,
    max_total: int | None,
    use_keyword_ideas: bool,
) -> int:
    import json

    from ads_ai_agents.intelligence.report_store import ReportStore
    from ads_ai_agents.intelligence.semantic_expansion import (
        MAX_NEW_TOTAL,
        GrantSemanticExpander,
    )

    selected = tuple(
        part.strip().upper() for part in (locales or "").split(",") if part.strip()
    )

    try:
        result = GrantSemanticExpander().apply(
            dry_run=not apply,
            locales=selected,
            max_total=max_total or MAX_NEW_TOTAL,
            use_keyword_ideas=use_keyword_ideas,
        )
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"Semantic expansion failed: {exc}"))
        return 1

    store = ReportStore()
    day_dir = store.day_dir(kind="audit")
    payload = result.to_dict()
    out = store.write_json(day_dir, "grant_semantic_expansion.json", payload)

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0 if not result.errors else 1

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"Semantic expansion [{mode}] locales={','.join(result.locales)}")
    print(
        f"  demand_signals={result.demand_signals} "
        f"planned={len(result.candidates_planned)} created={result.keywords_created} "
        f"warnings={len(result.warnings)} errors={len(result.errors)}"
    )
    for locale, stats in result.coverage.items():
        print(
            f"  {locale}: groups={stats['ad_groups']} live_kw={stats['live_keywords']} "
            f"planned=+{stats['planned_additions']} "
            f"starved_groups={stats['groups_without_live_keywords']}"
        )
    for item in result.candidates_planned[:20]:
        print(
            f"    + [{item['locale']}] \"{item['text']}\" -> {item['ad_group_name']} "
            f"(score={item['score']}, {item['source']})"
        )
    for warning in result.warnings[:10]:
        print(f"    ~ {warning.get('op')}: {warning.get('warning') or warning.get('error')}")
    for err in result.errors[:8]:
        print(f"    ! {err}")
    print(f"Wrote: {out}")
    return 0 if not result.errors else 1


def _platform_campaign_verticals(*, apply: bool, as_json: bool, enable: bool) -> int:
    import json

    from ads_ai_agents.intelligence.campaign_verticals import CampaignVerticalLauncher
    from ads_ai_agents.intelligence.report_store import ReportStore

    try:
        result = CampaignVerticalLauncher().apply(dry_run=not apply, enable=enable)
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"Campaign verticals failed: {exc}"))
        return 1

    store = ReportStore()
    day_dir = store.day_dir(kind="audit")
    payload = result.to_dict()
    out = store.write_json(day_dir, "campaign_verticals_launch.json", payload)

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0 if len(result.errors) < 5 else 1

    mode = "APPLY" if apply else "DRY-RUN"
    status = "ENABLED" if enable else "PAUSED"
    print(f"Campaign verticals [{mode}] create_as={status} gate={result.gate.get('pass')}")
    print(
        f"  created={len(result.created_campaigns)} enabled={result.enabled} "
        f"skipped={len(result.skipped)} rsa={result.rsa_created} "
        f"keywords={result.keywords_added} sitelinks={result.sitelinks_linked} "
        f"errors={len(result.errors)}"
    )
    for c in result.created_campaigns[:20]:
        print(
            f"  + {c.get('name')} → {c.get('final_url')} "
            f"${c.get('budget_usd')}/d enabled={c.get('enabled')}"
        )
    if result.skipped:
        print(f"  skipped: {len(result.skipped)}")
    if result.errors:
        for e in result.errors[:10]:
            print(f"    ! {e}")
    print(f"Wrote: {out}")
    return 0 if len(result.errors) < 8 else 1


def _platform_sitelink_fix(*, apply: bool, as_json: bool) -> int:
    import json

    from ads_ai_agents.intelligence.report_store import ReportStore
    from ads_ai_agents.intelligence.sitelink_url_fix import SitelinkUrlFixer

    try:
        result = SitelinkUrlFixer().apply(dry_run=not apply)
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"Sitelink fix failed: {exc}"))
        return 1

    store = ReportStore()
    day_dir = store.day_dir(kind="audit")
    payload = result.to_dict()
    out = store.write_json(day_dir, "sitelink_url_fix.json", payload)

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0 if not result.errors else 1

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"Sitelink URL fix [{mode}] gate={result.gate.get('pass')}")
    print(
        f"  bad={result.bad_found} unlinked={result.unlinked} "
        f"created={result.created} linked={result.linked} "
        f"campaigns={result.campaigns_touched} errors={len(result.errors)}"
    )
    if result.by_url:
        print("  bad URLs:")
        for u, n in sorted(result.by_url.items(), key=lambda kv: -kv[1])[:12]:
            print(f"    n={n} {u}")
    if result.after_sample and apply:
        blocked = [x for x in result.after_sample if x.get("blocked")]
        print(f"  after: {len(result.after_sample)} unique URLs, still_blocked={len(blocked)}")
        for x in result.after_sample[:10]:
            flag = "BAD" if x.get("blocked") else "ok"
            print(f"    [{flag}] n={x.get('count')} {x.get('url')}")
    if result.errors:
        for e in result.errors[:8]:
            print(f"    ! {e}")
    print(f"Wrote: {out}")
    return 0 if len(result.errors) < 15 else 1


def _platform_rsa_replace(*, apply: bool, as_json: bool, cities_only: bool) -> int:
    import json

    from ads_ai_agents.intelligence.report_store import ReportStore
    from ads_ai_agents.intelligence.rsa_prohibited_replace import ProhibitedRsaReplacer

    replacer = ProhibitedRsaReplacer()
    try:
        result = replacer.apply(dry_run=not apply, cities_only=cities_only)
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"RSA replace failed: {exc}"))
        return 1

    store = ReportStore()
    day_dir = store.day_dir(kind="audit")
    payload = result.to_dict()
    out = store.write_json(day_dir, "rsa_prohibited_replace.json", payload)

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0 if not result.errors else 1

    mode = "APPLY" if apply else "DRY-RUN"
    scope = "cities focus cities" if cities_only else "all ENABLED Search"
    print(f"RSA replace [{mode}] scope={scope} gate={result.gate.get('pass')}")
    print(
        f"  candidates={result.candidates} ad_groups={result.ad_groups} "
        f"created={result.created} paused={result.paused} errors={len(result.errors)}"
    )
    for d in (result.details or [])[:8]:
        print(f"  AG {d.get('ad_group_id')}: pause {len(d.get('pause_ads') or [])} → create {d.get('created')}")
    if result.errors:
        for e in result.errors[:8]:
            print(f"    ! {e}")
    print(f"Wrote: {out}")
    return 0 if len(result.errors) < 10 else 1


def _controlled_audit() -> int:
    from ads_ai_agents.intelligence.historical_audit import run_historical_audit

    try:
        result = run_historical_audit()
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"Historical audit failed: {exc}"))
        return 1
    summary = result["summary"]
    print(f"Historical audit written: {result['directory']}")
    for window, block in (summary.get("windows") or {}).items():
        totals = block.get("totals") or {}
        print(
            f"- {window}: spend=${totals.get('spend')} quality={totals.get('quality_conversions')} "
            f"CPQA={totals.get('cpqa')} campaigns={block.get('campaign_count')}"
        )
    print(summary.get("note"))
    return 0


def _platform_grant_delivery_recovery(*, apply: bool, as_json: bool) -> int:
    import json

    from ads_ai_agents.intelligence.grant_delivery_recovery import GrantDeliveryRecovery
    from ads_ai_agents.intelligence.report_store import ReportStore

    try:
        result = GrantDeliveryRecovery().apply(dry_run=not apply)
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"Grant delivery recovery failed: {exc}"))
        return 1

    payload = result.to_dict()
    store = ReportStore()
    day_dir = store.day_dir(kind="audit")
    out = store.write_json(day_dir, "grant_delivery_recovery.json", payload)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print(
            "Grant delivery recovery · "
            f"goals {result.conversion_actions_updated}/"
            f"{len(result.conversion_actions_planned)} · "
            f"campaigns {result.campaigns_updated}/{len(result.campaigns_planned)}"
        )
        for error in result.errors:
            print(f"  ! {error}")
        print(f"Wrote: {out}")
    return 0 if not result.errors else 1


def _controlled_cycle(*, apply: bool, as_json: bool) -> int:
    from ads_ai_agents.intelligence.controlled_live import run_controlled_live_cycle

    try:
        summary = run_controlled_live_cycle(apply=apply)
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"CONTROLLED_LIVE cycle failed: {exc}"))
        return 1

    if as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    else:
        mode = "APPLY" if apply else "DRY-RUN"
        print(f"CONTROLLED_LIVE ({mode}) → {summary.get('artifacts')}")
        print(f"Mode: {summary.get('mode')}")
        neg = summary.get("negatives") or {}
        bud = summary.get("budgets") or {}
        print(
            f"Negatives: proposed={neg.get('proposed')} auto={neg.get('auto')} "
            f"applied={neg.get('applied')} errors={neg.get('errors')}"
        )
        print(
            f"Budgets: proposed={bud.get('proposed')} auto={bud.get('auto')} "
            f"applied={bud.get('applied')} pause_approvals={bud.get('approval_pauses')} "
            f"errors={bud.get('errors')}"
        )
        print(f"Approval queue: {summary.get('approval_queue_count')}")
        print(f"Report: {Path(summary['artifacts']) / 'controlled_live.md'}")
    errors = (summary.get("negatives") or {}).get("errors", 0) + (summary.get("budgets") or {}).get("errors", 0)
    return 1 if errors else 0


def _goal_cleanup_markdown(plan: dict[str, Any]) -> str:
    summary = plan["summary"]
    lines = [
        "# Goal Cleanup Plan (no mutate)",
        "",
        f"- Demote: {summary['demote_count']}",
        f"- Keep: {summary['keep_count']}",
        f"- Verify/review: {summary['verify_or_review_count']}",
        f"- Micro conversions currently in mix: {summary['micro_conversions_currently_in_mix']}",
        f"- Projected quality reported conversions: {summary['projected_reported_conversions_after_cleanup']}",
        f"- Projected CPA after cleanup: {summary['projected_cpa_after_cleanup']}",
        "",
        "## UI checklist",
    ]
    for item in plan.get("ui_checklist") or []:
        lines.append(f"- [ ] {item}")
    lines.extend(["", "## Target primary stack"])
    for name in plan.get("target_primary_stack") or []:
        lines.append(f"- {name}")
    lines.extend(["", "## Actions"])
    for action in plan.get("actions") or []:
        lines.append(
            f"- `{action['decision']}` | {action['name']} | tier={action['tier']} | "
            f"conv={action['conversions_mtd']} | primary {action['current_primary']}→{action['target_primary']}"
        )
        lines.append(f"  - {action['rationale']}")
    return "\n".join(lines) + "\n"


def _budget_plan(*, as_json: bool) -> int:
    from ads_ai_agents.adapters.google_ads_readonly import GoogleAdsReadOnlyAdapter
    from ads_ai_agents.intelligence.conversion_audit import audit_conversion_actions
    from ads_ai_agents.intelligence.report_store import ReportStore
    from ads_ai_agents.intelligence.shadow_budget import plan_shadow_reallocation

    adapter = GoogleAdsReadOnlyAdapter()
    store = ReportStore()
    day_dir = store.day_dir(kind="daily")
    try:
        campaigns = adapter.list_campaigns()
        conversion_report = audit_conversion_actions(
            adapter.list_conversion_actions(),
            spend_mtd=adapter.performance_snapshot().spend_usd_mtd,
        ).to_dict()
        proposals = [
            item.to_dict()
            for item in plan_shadow_reallocation(
                campaigns,
                conversion_tracking_healthy=not conversion_report.get("micro_primaries"),
            )
        ]
    except Exception as exc:  # noqa: BLE001
        print(redact_secrets(f"Budget-plan failed: {exc}"))
        return 1

    store.write_json(day_dir, "shadow_budget.json", proposals)
    if as_json:
        print(json.dumps(proposals, ensure_ascii=False, indent=2))
    else:
        print(f"Shadow budget plan written: {day_dir / 'shadow_budget.json'}")
        if not proposals:
            print("No proposals generated.")
        for proposal in proposals:
            print(
                f"- {proposal['from_campaign']} → {proposal['to_campaign']} "
                f"${proposal['proposed_change_usd_per_day']}/day "
                f"confidence={proposal['confidence']} blocked={proposal['blocked_reasons']}"
            )
        print("Mode=shadow. No Google Ads budget mutations executed.")
    return 0


def _conversions_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Conversion Integrity Audit",
        "",
        f"- Reported conversions MTD: {report['reported_conversions_mtd']}",
        f"- Primary actions: {', '.join(report['primary_actions']) or 'none'}",
        f"- Micro primaries: {', '.join(report['micro_primaries']) or 'none'}",
        "",
        "## KPIs",
        f"- CPA reported: {report['kpis'].get('cpa_reported_conversions')}",
        f"- CPQA: {report['kpis'].get('cpqa')}",
        f"- CAC: {report['kpis'].get('cac')}",
        "",
        "## Actions",
    ]
    for action in report["actions"]:
        lines.append(
            f"- [{action['tier']}] {action['name']} primary={action['primary_for_goal']} "
            f"conv={action['conversions']} role={action['recommended_role']}"
        )
    lines.extend(["", "## Narrative"])
    for item in report["narrative"]:
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def _filter_stage(mission: MissionPlan, stage: str) -> MissionPlan:
    selected = WorkflowStage(stage)
    return MissionPlan(
        campaign=mission.campaign,
        gate=mission.gate,
        recommendations=tuple(rec for rec in mission.recommendations if rec.stage is selected),
        workflow=tuple(step for step in mission.workflow if step.stage is selected),
        daily_checklist=mission.daily_checklist,
        north_star=mission.north_star,
    )


def _format_mission(mission: MissionPlan) -> str:
    campaign = mission.campaign
    target = campaign.business_goal.effective_target_cpa_usd or campaign.grant.target_cpa_usd
    lines = [
        f"Campaign: {campaign.name}",
        f"Portfolio: {campaign.portfolio.value}",
        f"North star: {mission.north_star or f'${campaign.grant.monthly_budget_usd:,.0f}/mo @ ${target:.2f} CPA'}",
        f"Gate: {mission.gate.state.value} / risk={mission.gate.risk.value}",
    ]
    org = mission.organization or {}
    if org.get("main_offering"):
        lines.append(f"Main offering ({org.get('type', 'business')}): {org['main_offering']}")
    if org.get("competitor_search_seeds"):
        lines.append("Competitor search seeds: " + "; ".join(org["competitor_search_seeds"][:6]))
    lines += ["", "Agent recommendations:"]
    for recommendation in mission.recommendations:
        lines.append(f"- [{recommendation.stage.value}] {recommendation.agent}: {recommendation.title}")
        for action in recommendation.actions[:5]:
            lines.append(f"  * {action}")
    return "\n".join(lines)


def _emit(value: Any, *, as_json: bool) -> None:
    if as_json and not isinstance(value, str):
        print(json.dumps(_to_jsonable(value), ensure_ascii=False, indent=2))
    else:
        print(value if isinstance(value, str) else _to_jsonable(value))


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _to_jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, tuple | list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    return value


if __name__ == "__main__":
    sys.exit(main())
