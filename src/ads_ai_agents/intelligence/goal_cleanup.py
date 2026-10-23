from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class GoalActionPlan:
    id: str
    name: str
    tier: str
    current_primary: bool
    include_in_conversions: bool | None
    conversions_mtd: float
    decision: str
    target_primary: bool
    target_include_in_conversions: bool
    rationale: str
    verification_checks: list[str] = field(default_factory=list)
    risk: str = "medium"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Stage north star: Cost per Product Activation (first meaningful chat).
KEEP_PRIMARY_NAME_MARKERS = (
    "chat_message_sent",
    "first_meaningful",
    "qualified_chat",
)

# Legacy proxies + micro noise — never bid on these.
DEMOTE_ALWAYS_MARKERS = (
    "user_engagement",
    "first_visit",
    "session_start",
    "page_view",
    "page view",
    "scroll",
    "scroll",
    "seconds_on_page",
    "click_messenger",
    "click_email",
    "follow-on views",
    "youtube follow",
    "form_submit",
    "chat_open",
    "go_to_chat",
    "go_to_paypal",
    "paypal",
    " (web) click",
    "file_download",
    "form_start",
    "form_submit",
)

# Useful diagnostics / mid-funnel — Secondary or Observe, never Primary.
SECONDARY_ONLY_MARKERS = (
    "youtube channel subscriptions",
    "android installs",
    "gotowebapp",
    "login_success",
    "login_start",
    "click_store_",
    "subscribe_click",
    "checkout_start",
    "purchase (1)",
    " (web) purchase",
    "purchase",
)


def build_goal_cleanup_plan(
    actions: list[dict[str, Any]],
    *,
    spend_mtd: float | None = None,
) -> dict[str, Any]:
    plans: list[GoalActionPlan] = []
    for action in actions:
        name = str(action.get("name") or "")
        lower = name.lower()
        tier = str(action.get("tier") or "unknown")
        primary = bool(action.get("primary_for_goal"))
        include = action.get("include_in_conversions_metric")
        conversions = float(action.get("conversions") or 0)

        if any(marker in lower for marker in DEMOTE_ALWAYS_MARKERS) or tier == "micro":
            decision = "demote_to_secondary"
            target_primary = False
            target_include = False
            rationale = (
                "Micro/engagement event inflates Conversions and trains Smart Bidding on noise. "
                "Keep for analytics only (secondary / observe)."
            )
            checks = [
                "Confirm event still visible in GA4/Ads as secondary.",
                "After demotion, watch account Conversions drop toward quality events only.",
            ]
            risk = "critical" if primary or include else "low"
        elif any(marker in lower for marker in SECONDARY_ONLY_MARKERS):
            decision = "demote_to_secondary"
            target_primary = False
            target_include = False
            rationale = "Not a durable YOUR_BRAND activation/revenue outcome for Grant Search optimization."
            checks = ["Confirm YouTube/app installs are not used as account-default bidding goals."]
            risk = "high" if primary else "medium"
        elif "paypal" in lower:
            decision = "verify_then_secondary_or_value"
            target_primary = False
            target_include = False
            rationale = (
                "PayPal click is purchase intent, not completed payment. "
                "Use as secondary or value signal until payment_success exists via Data Manager."
            )
            checks = [
                "Validate GTM/GA4 fires only on real PayPal handoff.",
                "Map completed payment to Purchase / payment_success via Data Manager.",
            ]
            risk = "high"
        elif any(marker in lower for marker in KEEP_PRIMARY_NAME_MARKERS) or tier in {
            "acquisition",
            "activation",
            "revenue",
        }:
            if conversions <= 0 and primary:
                decision = "keep_primary_after_tracking_repair"
                target_primary = True
                target_include = True
                rationale = (
                    "Quality-aligned goal, but MTD volume is 0 while marked primary — "
                    "likely broken tagging or not firing. Repair before relying on it for bidding."
                )
                checks = [
                    "Fire test conversion in staging/prod and confirm Ads Diagnostics.",
                    "Ensure gclid/gbraid preserved through redirect.",
                    "Confirm consent mode allows ads storage when applicable.",
                ]
                risk = "high"
            else:
                decision = "keep_primary"
                target_primary = True
                target_include = True
                rationale = (
                    "Quality funnel event suitable for Conversions / bidding after cleanup of micro primaries."
                )
                checks = [
                    "Deduplicate against sibling chat/form events.",
                    "Confirm counting method is One/Many intentionally.",
                ]
                risk = "medium"
        else:
            decision = "manual_review"
            target_primary = False
            target_include = bool(include)
            rationale = "Unclassified action — do not leave as primary without human review."
            checks = ["Map to micro/acquisition/activation/revenue/B2B."]
            risk = "medium"

        plans.append(
            GoalActionPlan(
                id=str(action.get("id") or ""),
                name=name,
                tier=tier,
                current_primary=primary,
                include_in_conversions=include if isinstance(include, bool) else None,
                conversions_mtd=conversions,
                decision=decision,
                target_primary=target_primary,
                target_include_in_conversions=target_include,
                rationale=rationale,
                verification_checks=checks,
                risk=risk,
            )
        )

    demote = [p for p in plans if p.decision.startswith("demote")]
    keep = [p for p in plans if p.decision.startswith("keep")]
    verify = [p for p in plans if "verify" in p.decision or p.decision == "manual_review"]

    micro_conv = sum(p.conversions_mtd for p in demote)
    quality_conv = sum(p.conversions_mtd for p in keep if p.target_primary)
    projected_reported = quality_conv
    projected_cpa = (spend_mtd / projected_reported) if spend_mtd and projected_reported else None

    ui_checklist = [
        "Google Ads → Goals → Conversions → Summary",
        "For each DEMOTE row: set as Secondary and exclude from 'Conversions' column / bidding.",
        "For KEEP rows: Primary + included in Conversions only after verification checks pass.",
        "Do not change campaign budgets in this wave.",
        "Observe 7–14 days, then compare Search vs PMax on CPPA (chat_message_sent), not reported CPA.",
        "Wire payment/onboarding quality uploads through Data Manager (not UploadClickConversion-only).",
        "UI-only demote if API MUTATE_NOT_ALLOWED: YouTube subscriptions, Purchase (1) webpage.",
    ]

    return {
        "mode": "plan_only_no_mutate",
        "generated_from": "conversions_integrity",
        "summary": {
            "actions_total": len(plans),
            "demote_count": len(demote),
            "keep_count": len(keep),
            "verify_or_review_count": len(verify),
            "micro_conversions_currently_in_mix": round(micro_conv, 2),
            "quality_conversions_keep_primary": round(quality_conv, 2),
            "projected_reported_conversions_after_cleanup": round(projected_reported, 2),
            "projected_cpa_after_cleanup": projected_cpa,
            "spend_mtd": spend_mtd,
        },
        "target_primary_stack": [
            "www.example.com (web) chat_message_sent  ← Primary / bidding (CPPA)",
            "login_success + click_store_*  ← Secondary (not bidding)",
            "subscribe_click / checkout_start / purchase  ← Observe only",
            "Purchase (1) webpage + YouTube*  ← demote to Secondary in UI if still primary",
        ],
        "ui_checklist": ui_checklist,
        "actions": [p.to_dict() for p in sorted(plans, key=lambda x: (x.decision, -x.conversions_mtd))],
        "approval_required": True,
        "mutate_executed": False,
    }


def load_integrity_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def plan_from_integrity_file(path: Path, *, spend_mtd: float | None = None) -> dict[str, Any]:
    report = load_integrity_report(path)
    spend = spend_mtd
    if spend is None:
        reported = report.get("reported_conversions_mtd")
        cpa = (report.get("kpis") or {}).get("cpa_reported_conversions")
        if reported and cpa:
            spend = float(reported) * float(cpa)
    return build_goal_cleanup_plan(report.get("actions") or [], spend_mtd=spend)
