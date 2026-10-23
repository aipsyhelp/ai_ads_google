from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ShadowBudgetProposal:
    action: str
    mode: str
    from_campaign: str
    to_campaign: str
    proposed_change_usd_per_day: float
    reason: str
    confidence: float
    minimum_observation_days: int
    approval_required: bool
    evidence: dict[str, Any] = field(default_factory=dict)
    rollback_proposal: dict[str, Any] = field(default_factory=dict)
    blocked_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def plan_shadow_reallocation(
    campaigns: list[dict[str, Any]],
    *,
    conversion_tracking_healthy: bool = True,
    observation_days: int = 14,
    recent_material_change_days: int | None = None,
    learning_period: bool = False,
    goal_changed_recently: bool = False,
    data_fresh: bool = True,
    max_pct: float = 0.10,
    min_qualified_conversions: float = 30,
) -> list[ShadowBudgetProposal]:
    """Shadow-mode only: propose ±10% shifts; never mutate Google Ads."""
    usable = []
    for row in campaigns:
        if row.get("error"):
            continue
        status = str(row.get("status") or row.get("campaign.status") or "").upper()
        if status and status not in {"ENABLED", "2"}:
            # accept ENABLED name or raw
            if "ENABLED" not in status and status != "2":
                continue
        name = str(row.get("name") or row.get("campaign.name") or "")
        cost = float(row.get("cost") or 0)
        if "metrics.cost_micros" in row and not cost:
            cost = float(row["metrics.cost_micros"]) / 1_000_000
        conversions = float(row.get("conversions") or row.get("metrics.conversions") or 0)
        daily_budget = float(row.get("daily_budget") or 0)
        if "campaign_budget.amount_micros" in row and not daily_budget:
            daily_budget = float(row["campaign_budget.amount_micros"]) / 1_000_000
        cpa = float(row["cpa"]) if row.get("cpa") is not None else (cost / conversions if conversions else None)
        channel = str(row.get("channel") or row.get("campaign.advertising_channel_type") or "")
        usable.append(
            {
                "name": name,
                "cost": cost,
                "conversions": conversions,
                "cpa": cpa,
                "daily_budget": daily_budget or 50.0,
                "channel": channel,
            }
        )

    if len(usable) < 2:
        return []

    winners = sorted(
        [c for c in usable if c["cpa"] is not None and c["conversions"] >= min_qualified_conversions],
        key=lambda c: c["cpa"],
    )
    losers = sorted(
        [c for c in usable if c["cpa"] is not None],
        key=lambda c: c["cpa"],
        reverse=True,
    )
    if not winners or not losers:
        return []

    donor = losers[0]
    receiver = winners[0]
    if donor["name"] == receiver["name"]:
        return []

    blocked: list[str] = []
    if observation_days < 14:
        blocked.append("less_than_14_days_data")
    if receiver["conversions"] < min_qualified_conversions:
        blocked.append("receiver_below_30_qualified_conversions")
    if not conversion_tracking_healthy:
        blocked.append("conversion_tracking_unhealthy")
    if learning_period:
        blocked.append("campaign_in_learning_period")
    if recent_material_change_days is not None and recent_material_change_days < 7:
        blocked.append("material_edit_within_7_days")
    if not data_fresh:
        blocked.append("stale_data")
    if goal_changed_recently:
        blocked.append("conversion_goal_changed_recently")
    if donor["cpa"] is None or receiver["cpa"] is None or donor["cpa"] <= receiver["cpa"] * 1.15:
        blocked.append("efficiency_gap_not_stable")

    delta = round(min(donor["daily_budget"] * max_pct, receiver["daily_budget"] * max_pct, 20.0), 2)
    confidence = 0.5
    if receiver["conversions"] >= 100 and donor["cpa"] and receiver["cpa"]:
        confidence = min(0.9, 0.55 + (donor["cpa"] - receiver["cpa"]) / max(donor["cpa"], 1) * 0.4)

    reason = (
        f"Shadow hypothesis: move ${delta:.2f}/day from '{donor['name']}' (CPA ${donor['cpa']}) "
        f"toward '{receiver['name']}' (CPA ${receiver['cpa']}) after quality validation."
    )
    proposal = ShadowBudgetProposal(
        action="budget_reallocation",
        mode="shadow",
        from_campaign=donor["name"],
        to_campaign=receiver["name"],
        proposed_change_usd_per_day=delta,
        reason=reason,
        confidence=round(confidence, 2),
        minimum_observation_days=14,
        approval_required=True,
        evidence={
            "donor_cpa": donor["cpa"],
            "receiver_cpa": receiver["cpa"],
            "donor_conversions": donor["conversions"],
            "receiver_conversions": receiver["conversions"],
            "note": "Conversions are Google reported conversions until activation quality is proven.",
        },
        rollback_proposal={
            "action": "restore_budgets",
            "from_campaign": receiver["name"],
            "to_campaign": donor["name"],
            "amount_usd_per_day": delta,
        },
        blocked_reasons=blocked,
    )
    # If blocked, still return the proposal with blocked_reasons for transparency.
    return [proposal]
