from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ConversionTier(str, Enum):
    MICRO = "micro"
    ACQUISITION = "acquisition"
    ACTIVATION = "activation"
    RETENTION = "retention"
    REVENUE = "revenue"
    B2B = "b2b"
    UNKNOWN = "unknown"


MICRO_MARKERS = (
    # GA4 emits these with underscores; the spaced variants only appear in
    # Ads UI labels, so both spellings must be recognised or a primary
    # page_view slips through as "unknown" instead of a micro alert.
    "page view",
    "pageview",
    "page_view",
    "screen_view",
    "first_open",
    "scroll",
    "scroll",
    "session",
    "session_start",
    "first_visit",
    "user_engagement",
    "seconds_on_page",
    "cta",
    "button",
    " (web) click",
    "click_",
    "landing",
    "article",
    "screen opened",
    "chat opened",
    "chat_open",
    "engaged session",
    "follow-on views",
    "youtube follow",
)
ACQUISITION_MARKERS = (
    "registration",
    "sign up",
    "signup",
    "register",
    "lead form",
    "submit",
    "form_submit",
    "login_success",
    "login_start",
    "click_store_",
)
ACTIVATION_MARKERS = (
    "chat_message_sent",
    "onboarding",
    "qualified",
    "first meaningful",
    "meaningful conversation",
    "first chat",
    "activation",
    "chat_open",
    "go_to_chat",
    "chat_open",
)
RETENTION_MARKERS = ("return", "second session", "retained", "day 7", "7 days", "repeat")
REVENUE_MARKERS = ("subscription", "payment", "purchase", "checkout", "paid", "revenue")
B2B_MARKERS = ("meeting", "demo", "opportunity", "enterprise", "b2b", "qualified opportunity")

PREFERRED_PRIMARY_SEQUENCE = (
    "chat_message_sent",  # stage CPPA north star
    "login_success",  # secondary diagnostic
    "click_store_*",  # secondary diagnostic
    "subscribe_click",  # observe
    "checkout_start",  # observe
    "purchase",  # observe until volume; later CPPU
)


@dataclass
class ConversionActionAudit:
    id: str
    name: str
    origin: str
    category: str
    status: str
    primary_for_goal: bool
    include_in_conversions_metric: bool | None
    counting_type: str
    click_window_days: int | None
    view_window_days: int | None
    conversions: float
    all_conversions: float
    conversions_value: float
    cost_per_conversion: float | None
    tier: ConversionTier
    recommended_role: str
    risks: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["tier"] = self.tier.value
        return payload


@dataclass
class ConversionIntegrityReport:
    actions: list[ConversionActionAudit]
    reported_conversions_mtd: float
    primary_actions: list[str]
    micro_primaries: list[str]
    duplicate_suspects: list[str]
    funnel: dict[str, float]
    kpis: dict[str, float | None]
    narrative: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "actions": [a.to_dict() for a in self.actions],
            "reported_conversions_mtd": self.reported_conversions_mtd,
            "primary_actions": self.primary_actions,
            "micro_primaries": self.micro_primaries,
            "duplicate_suspects": self.duplicate_suspects,
            "funnel": self.funnel,
            "kpis": self.kpis,
            "narrative": self.narrative,
            "terminology": {
                "do_not_say": "qualified leads",
                "say_instead": "reported conversions",
                "reason": "Qualified/Converted lead stages are not validated until CRM/Data Manager quality imports exist.",
            },
        }


def classify_conversion_name(name: str) -> ConversionTier:
    text = name.lower()
    if any(marker in text for marker in REVENUE_MARKERS):
        return ConversionTier.REVENUE
    if any(marker in text for marker in B2B_MARKERS):
        return ConversionTier.B2B
    if any(marker in text for marker in ACTIVATION_MARKERS):
        return ConversionTier.ACTIVATION
    if any(marker in text for marker in RETENTION_MARKERS):
        return ConversionTier.RETENTION
    if any(marker in text for marker in ACQUISITION_MARKERS):
        return ConversionTier.ACQUISITION
    if any(marker in text for marker in MICRO_MARKERS):
        return ConversionTier.MICRO
    return ConversionTier.UNKNOWN


def recommended_role(tier: ConversionTier, primary: bool) -> str:
    if tier is ConversionTier.MICRO:
        return "secondary_observe_only"
    if tier is ConversionTier.ACQUISITION:
        return "temporary_primary_until_activation_volume"
    if tier is ConversionTier.ACTIVATION:
        return "preferred_primary"
    if tier is ConversionTier.RETENTION:
        return "primary_or_value_signal"
    if tier is ConversionTier.REVENUE:
        return "north_star_business_conversion"
    if tier is ConversionTier.B2B:
        return "separate_b2b_goal"
    return "needs_manual_classification"


def audit_conversion_actions(
    rows: list[dict[str, Any]],
    *,
    spend_mtd: float | None = None,
) -> ConversionIntegrityReport:
    actions: list[ConversionActionAudit] = []
    for row in rows:
        if "error" in row and len(row) == 1:
            continue
        name = str(row.get("conversion_action.name") or row.get("name") or "")
        tier = classify_conversion_name(name)
        primary = _as_bool(row.get("conversion_action.primary_for_goal") or row.get("primary_for_goal"))
        conversions = float(row.get("metrics.conversions") or row.get("conversions") or 0)
        all_conversions = float(row.get("metrics.all_conversions") or row.get("all_conversions") or conversions)
        value = float(row.get("metrics.conversions_value") or row.get("conversions_value") or 0)
        cpa_raw = row.get("metrics.cost_per_conversion") or row.get("cost_per_conversion")
        cpa = float(cpa_raw) if cpa_raw not in (None, "") else None
        risks: list[str] = []
        if primary and tier is ConversionTier.MICRO:
            risks.append("micro_event_is_primary_for_goal")
        if primary and tier is ConversionTier.UNKNOWN:
            risks.append("unknown_primary_needs_review")
        if all_conversions > 0 and conversions > 0 and all_conversions > conversions * 1.5:
            risks.append("all_conversions_much_higher_than_conversions")
        actions.append(
            ConversionActionAudit(
                id=str(row.get("conversion_action.id") or row.get("id") or ""),
                name=name,
                origin=str(row.get("conversion_action.origin") or row.get("origin") or ""),
                category=str(row.get("conversion_action.category") or row.get("category") or ""),
                status=str(row.get("conversion_action.status") or row.get("status") or ""),
                primary_for_goal=primary,
                include_in_conversions_metric=_as_bool(
                    row.get("conversion_action.include_in_conversions_metric")
                ),
                counting_type=str(row.get("conversion_action.counting_type") or ""),
                click_window_days=_as_int(row.get("conversion_action.click_through_lookback_window_days")),
                view_window_days=_as_int(row.get("conversion_action.view_through_lookback_window_days")),
                conversions=conversions,
                all_conversions=all_conversions,
                conversions_value=value,
                cost_per_conversion=cpa,
                tier=tier,
                recommended_role=recommended_role(tier, primary),
                risks=risks,
            )
        )

    reported = sum(a.conversions for a in actions if a.primary_for_goal) or sum(a.conversions for a in actions)
    primaries = [a.name for a in actions if a.primary_for_goal]
    micro_primaries = [a.name for a in actions if a.primary_for_goal and a.tier is ConversionTier.MICRO]
    duplicate_suspects = _duplicate_suspects(actions)

    funnel = {
        "reported_conversions": reported,
        "acquisition": sum(a.conversions for a in actions if a.tier is ConversionTier.ACQUISITION),
        "activation": sum(a.conversions for a in actions if a.tier is ConversionTier.ACTIVATION),
        "retention": sum(a.conversions for a in actions if a.tier is ConversionTier.RETENTION),
        "revenue": sum(a.conversions for a in actions if a.tier is ConversionTier.REVENUE),
        "b2b": sum(a.conversions for a in actions if a.tier is ConversionTier.B2B),
        "micro": sum(a.conversions for a in actions if a.tier is ConversionTier.MICRO),
    }

    activation = funnel["activation"] or None
    revenue = funnel["revenue"] or None
    acquisition = funnel["acquisition"] or None
    kpis = {
        "cpa_reported_conversions": (spend_mtd / reported) if spend_mtd and reported else None,
        "cpqa": (spend_mtd / activation) if spend_mtd and activation else None,
        "cac": (spend_mtd / revenue) if spend_mtd and revenue else None,
        "activation_rate": (activation / acquisition) if activation and acquisition else None,
        "paid_conversion_rate": (revenue / activation) if revenue and activation else None,
    }

    narrative = [
        f"Google reports {reported:.0f} conversions MTD — treat as 'reported conversions', not qualified leads, until quality stages are proven.",
        f"Primary-for-goal actions: {', '.join(primaries) if primaries else 'none detected'}.",
        "Preferred primary sequence for YOUR_BRAND: " + " → ".join(PREFERRED_PRIMARY_SEQUENCE) + ".",
        "Offline/enhanced quality outcomes must use Data Manager architecture (not UploadClickConversion-only).",
    ]
    if micro_primaries:
        narrative.append(
            "CRITICAL: micro events marked primary: " + ", ".join(micro_primaries) + ". Remove from bidding."
        )
    if kpis["cpqa"] is None:
        narrative.append("CPQA unavailable — no activation-tier conversions detected in this snapshot.")
    if kpis["cac"] is None:
        narrative.append("CAC unavailable — no revenue-tier conversions detected in this snapshot.")

    return ConversionIntegrityReport(
        actions=actions,
        reported_conversions_mtd=reported,
        primary_actions=primaries,
        micro_primaries=micro_primaries,
        duplicate_suspects=duplicate_suspects,
        funnel=funnel,
        kpis=kpis,
        narrative=narrative,
    )


def _duplicate_suspects(actions: list[ConversionActionAudit]) -> list[str]:
    by_norm: dict[str, list[str]] = {}
    for action in actions:
        key = "".join(ch for ch in action.name.lower() if ch.isalnum())
        by_norm.setdefault(key, []).append(action.name)
    suspects = []
    for names in by_norm.values():
        if len(names) > 1:
            suspects.extend(names)
    # inflated: all_conversions >> conversions with primary
    for action in actions:
        if action.primary_for_goal and action.all_conversions > max(action.conversions, 1) * 2:
            suspects.append(f"{action.name} (all_conversions inflated)")
    return sorted(set(suspects))


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).lower() in {"1", "true", "yes"}


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
