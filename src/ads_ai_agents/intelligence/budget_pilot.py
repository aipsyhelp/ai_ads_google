from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ads_ai_agents.intelligence.change_ledger import ChangeLedger
from ads_ai_agents.intelligence.controlled_policy import ControlledLivePolicy


@dataclass
class BudgetPilotChange:
    campaign_id: str
    campaign_name: str
    budget_resource_name: str
    current_daily_budget: float
    proposed_daily_budget: float
    change_percent: float
    direction: str  # increase | decrease | pause_approval
    auto_apply: bool
    requires_approval: bool
    major_variable: str
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)
    expected_impact: dict[str, Any] = field(default_factory=dict)
    rollback_value: dict[str, Any] = field(default_factory=dict)
    blocked_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Heuristic tilt targets from operating plan (name substrings).
# Prefer channel prefixes so PMax "Home" never matches Search increase rules.
INCREASE_HINTS = (
    ("ser / www.example.com / articles / en", 1.0),
    ("ser / www.example.com / articles / ua", 0.7),
    ("ser / www.example.com / home page / en", 0.8),
    ("ser / www.example.com / home page / ua / national", 0.6),
    ("ser / www.example.com / home page / ua / city_a", 0.7),
)

DECREASE_HINTS = (
    ("performance max-youtube", -1.0),  # pause approval
    ("dsa", -1.0),
    ("pmax / www.example.com / home page / en", -0.8),
    ("pmax / www.example.com / home page / ua", -0.5),
    ("de_berlin", -1.0),
    ("us test", -1.0),
    ("europe", -0.5),
)


def _match_hint(name: str, hints: tuple[tuple[str, float], ...]) -> float | None:
    lower = name.lower()
    for needle, weight in hints:
        if needle in lower:
            return weight
    return None


def plan_budget_pilot(
    campaigns: list[dict[str, Any]],
    *,
    policy: ControlledLivePolicy,
    ledger: ChangeLedger | None = None,
    account_daily_sum: float | None = None,
) -> list[BudgetPilotChange]:
    """Plan ±max% budget tilts using CPQA/classification; campaign pause → approval only."""
    ledger = ledger or ChangeLedger()
    max_pct = policy.max_budget_change_percent / 100.0
    changes: list[BudgetPilotChange] = []
    touched_campaigns: set[str] = set()

    enabled = []
    for c in campaigns:
        if c.get("error"):
            continue
        status = str(c.get("status") or "").upper()
        if status not in {"ENABLED", "2"} and "ENABLED" not in status:
            continue
        enabled.append(c)

    for camp in enabled:
        name = str(camp.get("name") or "")
        cid = str(camp.get("id") or camp.get("campaign_id") or "")
        if not cid or cid in touched_campaigns:
            continue
        # One major variable / cooldown
        if policy.one_major_variable_per_campaign:
            recent = ledger.recent_material_changes(
                campaign_id=cid, within_days=policy.cooldown_days
            )
            if recent:
                continue

        daily = float(camp.get("daily_budget") or 0)
        if daily <= 0:
            continue
        budget_rn = str(camp.get("budget_resource_name") or "")
        quality = float(camp.get("quality_conversions") or 0)
        cpqa = camp.get("cpqa")
        classification = str(camp.get("classification") or "")
        spend = float(camp.get("spend") or 0)

        pause_weight = _match_hint(name, DECREASE_HINTS)
        increase_weight = _match_hint(name, INCREASE_HINTS)

        # Explicit pause candidates → approval queue only
        if pause_weight is not None and pause_weight <= -1.0:
            if "youtube" in name.lower() or "dsa" in name.lower() or classification == "zero_quality_spender":
                changes.append(
                    BudgetPilotChange(
                        campaign_id=cid,
                        campaign_name=name,
                        budget_resource_name=budget_rn,
                        current_daily_budget=daily,
                        proposed_daily_budget=daily,
                        change_percent=0.0,
                        direction="pause_approval",
                        auto_apply=False,
                        requires_approval=True,
                        major_variable="campaign_status",
                        reason="Zero-quality / drain campaign — pause requires human approval.",
                        evidence={
                            "classification": classification,
                            "spend": spend,
                            "quality_conversions": quality,
                            "cpqa": cpqa,
                        },
                        expected_impact={"effect": "stop_spend", "risk": "medium"},
                        rollback_value={"status": "ENABLED", "daily_budget": daily},
                    )
                )
                touched_campaigns.add(cid)
                continue

        direction = 0.0
        if increase_weight and (quality > 0 or "articles" in name.lower()):
            direction = max_pct * min(1.0, abs(increase_weight))
        elif pause_weight is not None and pause_weight > -1.0:
            direction = -max_pct * min(1.0, abs(pause_weight))
        elif classification == "cheap_useless_conversions":
            direction = -max_pct
        elif classification == "stable_winner_candidate" and cpqa and quality >= 3:
            direction = max_pct * 0.8
        else:
            continue

        proposed = round(daily * (1.0 + direction), 2)
        change_pct = abs(proposed - daily) / daily * 100.0
        if change_pct < 0.5:
            continue
        if change_pct > policy.max_budget_change_percent + 0.01:
            # Cap hard at policy max for auto path
            proposed = round(daily * (1.0 + (max_pct if direction > 0 else -max_pct)), 2)
            change_pct = abs(proposed - daily) / daily * 100.0

        requires_approval = change_pct > policy.require_approval_above_percent
        auto_apply = (
            policy.auto_budget_under_cap
            and not requires_approval
            and change_pct <= policy.max_budget_change_percent
        )
        blocked: list[str] = []
        if account_daily_sum is not None and direction > 0:
            projected = account_daily_sum - daily + proposed
            if projected > policy.max_account_daily_budget * 1.02:
                blocked.append("account_daily_budget_cap")
                auto_apply = False
                requires_approval = True

        changes.append(
            BudgetPilotChange(
                campaign_id=cid,
                campaign_name=name,
                budget_resource_name=budget_rn,
                current_daily_budget=daily,
                proposed_daily_budget=proposed,
                change_percent=round(change_pct, 2),
                direction="increase" if proposed > daily else "decrease",
                auto_apply=auto_apply and not blocked,
                requires_approval=requires_approval or bool(blocked),
                major_variable="budget",
                reason=(
                    f"CONTROLLED_LIVE pilot: {('increase' if proposed > daily else 'decrease')} "
                    f"{change_pct:.1f}% based on CPQA/classification (cap {policy.max_budget_change_percent}%)."
                ),
                evidence={
                    "classification": classification,
                    "quality_conversions": quality,
                    "cpqa": cpqa,
                    "spend_window": spend,
                    "increase_hint": increase_weight,
                    "decrease_hint": pause_weight,
                },
                expected_impact={
                    "daily_delta_usd": round(proposed - daily, 2),
                    "expected": "more_volume_on_winners_or_less_waste_on_drains",
                },
                rollback_value={"daily_budget": daily, "budget_resource_name": budget_rn},
                blocked_reasons=blocked,
            )
        )
        touched_campaigns.add(cid)

    return changes
