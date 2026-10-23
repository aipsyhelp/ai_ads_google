from __future__ import annotations

import calendar
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from ads_ai_agents.platform.product_facts import focus_cities


GRANT_MONTHLY = 10_000.0


@dataclass
class BudgetChange:
    campaign_id: str
    campaign_name: str
    budget_resource_name: str
    action: str  # set_budget | pause | leave
    current_daily_budget: float
    proposed_daily_budget: float | None
    current_status: str
    proposed_status: str
    reason: str
    channel: str
    spend_mtd: float
    conversions_mtd: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def days_left_in_month(today: date | None = None) -> int:
    today = today or date.today()
    last_day = calendar.monthrange(today.year, today.month)[1]
    return max(1, last_day - today.day + 1)


def build_remaining_month_plan(
    campaigns: list[dict[str, Any]],
    *,
    spend_mtd: float,
    grant_monthly: float = GRANT_MONTHLY,
    today: date | None = None,
) -> dict[str, Any]:
    """
    Enterprise end-of-month pacing for Ad Grant remainder.

    Priority: Search Articles winners > efficient Search > throttled PMax >
    pause zero-conversion drains. Does not require 14-day CPQA wait when
    operator explicitly authorizes remaining-grant utilization wave.
    """
    today = today or date.today()
    remaining = max(0.0, grant_monthly - spend_mtd)
    days_left = days_left_in_month(today)
    target_daily = round(remaining / days_left, 2)

    # Explicit target daily budgets for ENABLED campaigns (USD).
    # Names matched by substring for resilience.
    targets: list[tuple[str, float | None, str, str]] = [
        # (name_substring, daily_budget or None if pause, action, reason)
        ("Performance Max-YouTube", None, "pause", "Zero conversions MTD — stop grant drain"),
        ("Articles / EN", 140.0, "set_budget", "Best Search efficiency — primary utilization vehicle"),
        ("Articles / UA", 65.0, "set_budget", "Solid Search UA — keep volume with quality guard"),
        ("PMax / www.example.com / Home Page / EN", 40.0, "set_budget", "Throttle expensive PMax while observing CPQA"),
        ("PMax / www.example.com / Home Page / UA", 20.0, "set_budget", "Weak CPA — keep minimal learning only"),
        ("Home Page / EN / Brand_Search", 20.0, "set_budget", "Search home EN weak vs articles — reduce"),
        ("DSA / www.example.com / Home Page / EN", None, "pause", "High budget / weak CPA — pause DSA EN"),
        ("DSA / www.example.com / Home Page / UA", None, "pause", "Zero MTD spend/value — pause"),
        ("Home Page / UA / National / Brand_Search", 15.0, "set_budget", "Keep light Search UA home presence"),
        ("Europe", None, "pause", "Zero MTD — pause until demand proven"),
        ("DE_BERLIN", None, "pause", "Near-zero grant utility this month"),
    ]
    # City micro-campaigns: labels from product_facts.json → geo.focus_cities.
    targets += [(city, 8.0, "set_budget", "City micro — minimal presence") for city in focus_cities()]

    changes: list[BudgetChange] = []
    matched: set[str] = set()

    enabled = [c for c in campaigns if _is_enabled(c)]
    for camp in enabled:
        name = str(camp.get("name") or "")
        rule = _match_rule(name, targets)
        if rule is None:
            # Default: leave paused already handled; enabled unmatched → small cap
            if (camp.get("cost") or 0) <= 0 and (camp.get("conversions") or 0) <= 0:
                changes.append(
                    _change(camp, action="pause", proposed_budget=None, reason="Unmatched zero-activity ENABLED campaign — pause")
                )
                matched.add(str(camp.get("id")))
            continue
        _, budget, action, reason = rule
        changes.append(_change(camp, action=action, proposed_budget=budget, reason=reason))
        matched.add(str(camp.get("id")))

    proposed_daily_sum = 0.0
    for ch in changes:
        if ch.action == "set_budget" and ch.proposed_daily_budget:
            proposed_daily_sum += ch.proposed_daily_budget
        elif ch.action == "leave" and ch.current_status == "ENABLED":
            proposed_daily_sum += ch.current_daily_budget

    # Scale set_budget rows if above target_daily * 1.15
    if proposed_daily_sum > target_daily * 1.15:
        scale = (target_daily * 1.05) / proposed_daily_sum
        for ch in changes:
            if ch.action == "set_budget" and ch.proposed_daily_budget:
                ch.proposed_daily_budget = max(5.0, round(ch.proposed_daily_budget * scale, 2))
                ch.reason += f" | scaled x{scale:.2f} to fit remaining-month pacing"
        proposed_daily_sum = sum(
            ch.proposed_daily_budget or 0 for ch in changes if ch.action == "set_budget"
        )

    return {
        "wave": "budget_pacing_remaining_month",
        "as_of": today.isoformat(),
        "grant_monthly": grant_monthly,
        "spend_mtd": spend_mtd,
        "remaining_usd": round(remaining, 2),
        "days_left": days_left,
        "target_daily_usd": target_daily,
        "proposed_daily_budget_sum": round(proposed_daily_sum, 2),
        "changes": [c.to_dict() for c in changes],
        "guardrails": {
            "budgets_only": True,
            "no_keyword_mutate": True,
            "no_new_campaigns": True,
            "youtube_pmax_paused": True,
            "operator_authorized_remaining_grant_wave": True,
        },
        "narrative": [
            f"Utilize remaining ${remaining:,.0f} over {days_left} days ≈ ${target_daily}/day.",
            "Shift spend to Search Articles; throttle/pause zero-conversion and weak DSA/PMax drains.",
            "CPQA observation continues in parallel — this wave is grant utilization under quality goals already cleaned.",
        ],
    }


def _match_rule(
    name: str, targets: list[tuple[str, float | None, str, str]]
) -> tuple[str, float | None, str, str] | None:
    # Prefer longer/more specific substrings first
    ordered = sorted(targets, key=lambda t: len(t[0]), reverse=True)
    for rule in ordered:
        if rule[0] in name:
            # Avoid matching PMax home rule against Search home incorrectly:
            if rule[0].startswith("Home Page / EN") and "PMax" in name:
                continue
            if rule[0].startswith("Home Page / UA / National") and ("PMax" in name or "DSA" in name or "Articles" in name):
                continue
            if rule[0].startswith("Home Page / EN") and ("DSA" in name or "Articles" in name):
                continue
            return rule
    return None


def _is_enabled(camp: dict[str, Any]) -> bool:
    status = camp.get("status")
    return status in (2, "2", "ENABLED", "ENABLED".lower()) or str(status).upper() == "ENABLED"


def _change(
    camp: dict[str, Any],
    *,
    action: str,
    proposed_budget: float | None,
    reason: str,
) -> BudgetChange:
    status = str(camp.get("status") or "")
    status_label = "ENABLED" if _is_enabled(camp) else str(status)
    return BudgetChange(
        campaign_id=str(camp.get("id") or camp.get("campaign.id") or ""),
        campaign_name=str(camp.get("name") or ""),
        budget_resource_name=str(camp.get("budget_resource_name") or ""),
        action=action,
        current_daily_budget=float(camp.get("daily_budget") or 0),
        proposed_daily_budget=proposed_budget,
        current_status=status_label,
        proposed_status="PAUSED" if action == "pause" else "ENABLED",
        reason=reason,
        channel=str(camp.get("channel") or ""),
        spend_mtd=float(camp.get("cost") or 0),
        conversions_mtd=float(camp.get("conversions") or 0),
    )
