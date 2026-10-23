from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ads_ai_agents.intelligence.goal_cleanup import plan_from_integrity_file
from ads_ai_agents.intelligence.report_store import ReportStore


TRACKING_SPEC = [
    {
        "kpi": "Grant pacing",
        "formula": "spend_mtd / 10000",
        "cadence": "daily",
        "owner": "BudgetPacingAgent",
        "good": "near full month utilization without junk query dump",
    },
    {
        "kpi": "CPA reported",
        "formula": "spend / Google Ads Conversions",
        "cadence": "daily",
        "owner": "MeasurementAgent",
        "good": "diagnostic only until micro removed from primary",
    },
    {
        "kpi": "CPQA",
        "formula": "spend / (chat activation + qualified form)",
        "cadence": "daily",
        "owner": "ConversionAuditAgent",
        "good": "north-star efficiency after goal cleanup; target trend to ~$2",
    },
    {
        "kpi": "CAC",
        "formula": "spend / paid users (Purchase/payment_success)",
        "cadence": "weekly",
        "owner": "MeasurementAgent",
        "good": "available after Data Manager payment upload",
    },
    {
        "kpi": "Micro share of Conversions",
        "formula": "micro_primary_conversions / reported_conversions",
        "cadence": "daily",
        "owner": "ConversionAuditAgent",
        "good": "→ 0% after cleanup",
    },
    {
        "kpi": "Search vs PMax CPQA",
        "formula": "CPQA by channel after cleanup",
        "cadence": "weekly",
        "owner": "CampaignPerformanceAgent",
        "good": "reallocation evidence; not used before cleanup",
    },
    {
        "kpi": "Policy / search-term risk count",
        "formula": "count of clinical/crisis query hits",
        "cadence": "daily",
        "owner": "PolicyRiskAgent",
        "good": "flat or declining",
    },
    {
        "kpi": "Change drift",
        "formula": "unexpected change_event vs ledger",
        "cadence": "daily",
        "owner": "ChangeAuditAgent",
        "good": "all material edits explained",
    },
]


def build_efficiency_dashboard(
    report_dir: Path | None = None,
    store: ReportStore | None = None,
) -> dict[str, Any]:
    store = store or ReportStore()
    day_dir = report_dir or store.day_dir(kind="audit")
    integrity_path = day_dir / "conversions_integrity.json"
    executive_path = day_dir / "executive_summary.json"
    campaigns_csv = day_dir / "campaigns.csv"

    executive = _read_json(executive_path) if executive_path.exists() else {}
    integrity = _read_json(integrity_path) if integrity_path.exists() else {}
    cleanup = plan_from_integrity_file(integrity_path) if integrity_path.exists() else {}

    funnel = integrity.get("funnel") or {}
    actions = integrity.get("actions") or []
    conversion_bars = sorted(
        [
            {
                "name": a["name"].replace("www.example.com (web) ", ""),
                "conversions": round(float(a.get("conversions") or 0), 1),
                "tier": a.get("tier"),
                "primary": bool(a.get("primary_for_goal")),
            }
            for a in actions
            if float(a.get("conversions") or 0) > 0
        ],
        key=lambda x: x["conversions"],
        reverse=True,
    )[:12]

    campaigns = _read_campaigns(day_dir)
    return {
        "title": "YOUR_BRAND Grant Efficiency Dashboard",
        "account": executive.get("account") or "YOUR_BRAND",
        "customer_id": executive.get("customer_id") or "YOUR_CUSTOMER_ID",
        "as_of": executive.get("generated_at") or store.stamp(),
        "north_star": {
            "grant_monthly_usd": 10000,
            "target_cpqa_usd": 2.0,
            "mode": "read_only_until_goal_cleanup",
        },
        "kpis": {
            "spend_mtd": executive.get("spend_mtd"),
            "reported_conversions_mtd": executive.get("reported_conversions_mtd"),
            "cpa_reported": executive.get("cpa_reported_conversions"),
            "cpqa": executive.get("cpqa"),
            "cac": executive.get("cac"),
            "micro_share": round(
                (funnel.get("micro") or 0) / max(funnel.get("reported_conversions") or 1, 1),
                4,
            ),
            "activation_conversions": funnel.get("activation"),
            "micro_conversions": funnel.get("micro"),
        },
        "funnel": funnel,
        "conversion_bars": conversion_bars,
        "campaigns": campaigns,
        "goal_cleanup_summary": cleanup.get("summary"),
        "target_primary_stack": cleanup.get("target_primary_stack"),
        "demote_actions": [
            a for a in cleanup.get("actions", []) if str(a.get("decision", "")).startswith("demote")
        ][:20],
        "keep_actions": [
            a for a in cleanup.get("actions", []) if str(a.get("decision", "")).startswith("keep")
        ],
        "tracking_spec": TRACKING_SPEC,
        "terminology": integrity.get("terminology")
        or {
            "say_instead": "reported conversions",
            "do_not_say": "qualified leads",
        },
        "sources": {
            "executive_summary": str(executive_path),
            "conversions_integrity": str(integrity_path),
            "campaigns": str(campaigns_csv),
        },
    }


def write_efficiency_dashboard(store: ReportStore | None = None) -> Path:
    store = store or ReportStore()
    day_dir = store.day_dir(kind="audit")
    payload = build_efficiency_dashboard(day_dir, store=store)
    path = store.write_json(day_dir, "efficiency_dashboard.json", payload)
    store.write_markdown(day_dir, "TRACKING.md", _tracking_markdown(payload))
    return path


def _tracking_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Efficiency Tracking Spec — YOUR_BRAND Grant",
        "",
        f"Account: {payload.get('account')} (`{payload.get('customer_id')}`)",
        "",
        "## North star",
        "- Utilize ~$10,000/mo Grant",
        "- Optimize to CPQA ≈ $2 after goal cleanup",
        "- No budget mutate until micro primaries removed",
        "",
        "## KPI board",
    ]
    for item in payload.get("tracking_spec") or []:
        lines.append(
            f"- **{item['kpi']}** (`{item['cadence']}`): {item['formula']} — good when {item['good']}"
        )
    lines.extend(["", "## Goal cleanup target primary stack"])
    for name in payload.get("target_primary_stack") or []:
        lines.append(f"- {name}")
    return "\n".join(lines) + "\n"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_campaigns(day_dir: Path) -> list[dict[str, Any]]:
    # Prefer normalized rows from executive winners/losers; else parse sync-like CSV keys if present.
    executive = day_dir / "executive_summary.json"
    if executive.exists():
        data = _read_json(executive)
        rows = []
        for item in (data.get("winners_preliminary") or []) + (data.get("losers_preliminary") or []):
            rows.append(item)
        if rows:
            # dedupe by name
            seen = set()
            out = []
            for row in rows:
                name = row.get("name")
                if name in seen:
                    continue
                seen.add(name)
                out.append(row)
            return out
    return []
