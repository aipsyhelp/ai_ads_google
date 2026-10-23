from __future__ import annotations

from typing import Any


def plan_weekly_experiments(candidates: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Experiment Agent — never change everything; ≤5 / week; one variable each."""
    default = [
        {
            "id": "exp_lp_hero",
            "name": "LP Hero A/B",
            "variable": "landing_hero",
            "variants": ["hero_work_stress", "hero_sleep_anxiety"],
            "primary_metric": "signup_to_chat",
            "approval_required": True,
        },
        {
            "id": "exp_onboarding",
            "name": "Onboarding length",
            "variable": "onboarding_steps",
            "variants": ["3_steps", "6_steps"],
            "primary_metric": "first_chat",
            "approval_required": True,
        },
        {
            "id": "exp_trial",
            "name": "Trial 3 vs 7 days",
            "variable": "trial_days",
            "variants": ["3", "7"],
            "primary_metric": "paying_users",
            "approval_required": True,
        },
        {
            "id": "exp_rsa_burnout",
            "name": "RSA burnout intent",
            "variable": "ad_copy_theme",
            "variants": ["burnout_work", "calm_evening"],
            "primary_metric": "qualified_activation",
            "approval_required": True,
        },
        {
            "id": "exp_paywall_timing",
            "name": "Paywall after first meaningful chat",
            "variable": "paywall_trigger",
            "variants": ["before_chat", "after_meaningful_chat"],
            "primary_metric": "subscribe_to_paid",
            "approval_required": True,
        },
    ]
    selected = (candidates or default)[:5]
    return {
        "agent": "experiment",
        "max_per_week": 5,
        "rule": "one_major_variable_per_experiment",
        "auto_promote_winner": False,  # requires approval in v1
        "selected": selected,
        "status": "registry_skeleton",
    }
