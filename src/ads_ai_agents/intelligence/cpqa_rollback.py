from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
from ads_ai_agents.adapters.google_ads_budgets import GoogleAdsBudgetWriter
from ads_ai_agents.intelligence.change_ledger import ChangeLedger
from ads_ai_agents.intelligence.controlled_policy import ControlledLivePolicy, load_controlled_policy
from ads_ai_agents.logging_utils import configure_logging

logger = configure_logging()


def evaluate_cpqa_rollbacks(
    *,
    current_cpqa_by_campaign: dict[str, float | None],
    baseline_cpqa_by_campaign: dict[str, float | None],
    policy: ControlledLivePolicy | None = None,
    ledger: ChangeLedger | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    """If CPQA worsens by >threshold after delay window, restore rollback budgets."""
    policy = policy or load_controlled_policy()
    ledger = ledger or ChangeLedger()
    threshold = policy.cpqa_rollback_worsen_percent / 100.0
    delay_days = policy.conversion_delay_days

    candidates = []
    for entry in ledger.iter_entries():
        if not entry.applied or entry.major_variable != "budget":
            continue
        try:
            ts = datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00"))
        except ValueError:
            continue
        age_days = (datetime.now(timezone.utc) - ts).total_seconds() / 86400
        if age_days < delay_days:
            continue
        if entry.metadata.get("rolled_back"):
            continue
        name = str(entry.new_state.get("campaign_name") or entry.old_state.get("campaign_name") or "")
        # Prefer entry metadata cpqa_baseline when present, otherwise fall back to map
        key = entry.campaign_id or name
        entry_baseline = entry.metadata.get("cpqa_baseline")
        baseline = (
            float(entry_baseline)
            if isinstance(entry_baseline, (int, float))
            else (baseline_cpqa_by_campaign.get(key) or baseline_cpqa_by_campaign.get(name))
        )
        current = current_cpqa_by_campaign.get(key) or current_cpqa_by_campaign.get(name)
        if baseline is None or current is None or baseline <= 0:
            continue
        worsen = (current - baseline) / baseline
        if worsen > threshold:
            candidates.append(
                {
                    "change_id": entry.change_id,
                    "campaign_id": entry.campaign_id,
                    "campaign_name": name,
                    "baseline_cpqa": baseline,
                    "current_cpqa": current,
                    "worsen_percent": round(worsen * 100, 2),
                    "rollback_payload": entry.rollback_payload,
                }
            )

    applied = []
    errors = []
    if apply and candidates:
        assert_mutations_allowed()
        writer = GoogleAdsBudgetWriter()
        for item in candidates:
            payload = item["rollback_payload"] or {}
            daily = payload.get("daily_budget")
            budget_rn = payload.get("budget_resource_name")
            if daily is None or not budget_rn:
                errors.append({**item, "error": "missing_rollback_payload"})
                continue
            try:
                result = writer.apply_plan(
                    {
                        "changes": [
                            {
                                "action": "set_budget",
                                "campaign_id": item["campaign_id"],
                                "campaign_name": item["campaign_name"],
                                "budget_resource_name": budget_rn,
                                "proposed_daily_budget": float(daily),
                                "current_daily_budget": None,
                                "current_status": "ENABLED",
                                "proposed_status": "ENABLED",
                                "reason": "CPQA rollback",
                            }
                        ]
                    },
                    dry_run=False,
                )
                if result.errors:
                    errors.append({**item, "error": result.errors})
                else:
                    applied.append(item)
                    ledger.record_decision(
                        actor="cpqa_rollback",
                        agent_name="watchdog",
                        customer_id=writer.settings.customer_id,
                        campaign_id=item["campaign_id"],
                        resource_type="campaign_budget",
                        reason=f"Auto-rollback: CPQA worsened {item['worsen_percent']}%",
                        evidence=item,
                        expected_effect="restore_prior_budget",
                        approval_id="auto_cpqa_rollback",
                        old_state={"cpqa": item["current_cpqa"]},
                        new_state={"daily_budget": daily},
                        proposed_state={"daily_budget": daily},
                        rollback_payload={},
                        impact_estimate={"type": "rollback"},
                        major_variable="budget_rollback",
                        applied=True,
                        dry_run=False,
                        metadata={"rolled_back_change_id": item["change_id"]},
                    )
            except Exception as exc:  # noqa: BLE001
                logger.error("Rollback failed: %s", exc)
                errors.append({**item, "error": str(exc)})

    return {
        "threshold_percent": policy.cpqa_rollback_worsen_percent,
        "delay_days": delay_days,
        "candidates": candidates,
        "applied": applied,
        "errors": errors,
        "apply": apply,
    }
