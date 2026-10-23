from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any

from ads_ai_agents.adapters.automation_mode import AutomationMode, resolve_automation_mode
from ads_ai_agents.adapters.settings import load_dotenv


@dataclass(frozen=True)
class ControlledLivePolicy:
    """Ads operating envelope for YOUR_BRAND Grant (CONTROLLED + AUTONOMOUS)."""

    mode: AutomationMode = AutomationMode.READ_ONLY
    max_budget_change_percent: float = 10.0
    require_approval_above_percent: float = 15.0
    cooldown_days: int = 3
    max_account_daily_budget: float = 329.0
    cpqa_rollback_worsen_percent: float = 25.0
    conversion_delay_days: int = 3
    one_major_variable_per_campaign: bool = True
    auto_add_negatives: bool = True
    auto_pause_harmful_keywords: bool = True
    auto_budget_under_cap: bool = False
    pause_campaign_requires_approval: bool = True
    goals_mutate: str = "prohibited"  # prohibited | approval
    bidding_mutate: str = "approval"
    geo_expansion: str = "approval"
    geo_exclusions_auto: bool = True
    positive_keywords: str = "approval"  # approval | auto_gated
    match_type_change: str = "approval"
    asset_generate: str = "auto_draft"
    asset_publish: str = "approval"  # approval | auto_gated
    locales: tuple[str, ...] = ("UA", "DE", "EN")
    windows_days: tuple[int, ...] = (7, 30, 180)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["mode"] = self.mode.value
        return data

    @property
    def mutations_enabled(self) -> bool:
        return self.mode in {
            AutomationMode.CONTROLLED_LIVE,
            AutomationMode.AUTONOMOUS_LIVE,
        }

    @property
    def is_autonomous(self) -> bool:
        return self.mode is AutomationMode.AUTONOMOUS_LIVE

    @property
    def assets_auto_publish(self) -> bool:
        return self.asset_publish == "auto_gated" and self.is_autonomous


def load_controlled_policy() -> ControlledLivePolicy:
    load_dotenv()
    mode = resolve_automation_mode()

    def _float(name: str, default: float) -> float:
        raw = os.environ.get(name, "").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            return default

    def _int(name: str, default: int) -> int:
        raw = os.environ.get(name, "").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            return default

    def _bool(name: str, default: bool) -> bool:
        raw = os.environ.get(name, "").strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
        return default

    # Autonomous defaults (env can still override).
    if mode is AutomationMode.AUTONOMOUS_LIVE:
        default_tilt = 20.0
        default_cooldown = 1
        default_approval_above = 25.0
        default_asset_publish = "auto_gated"
        default_positive_kw = "auto_gated"
        default_daily_cap = 329.0
    else:
        default_tilt = 10.0
        default_cooldown = 3
        default_approval_above = 15.0
        default_asset_publish = "approval"
        default_positive_kw = "approval"
        default_daily_cap = 329.0

    asset_publish = (os.environ.get("ADS_ASSET_PUBLISH") or default_asset_publish).strip().lower()
    if asset_publish not in {"approval", "auto_gated"}:
        asset_publish = default_asset_publish
    positive_kw = (os.environ.get("ADS_POSITIVE_KEYWORDS") or default_positive_kw).strip().lower()
    if positive_kw not in {"approval", "auto_gated"}:
        positive_kw = default_positive_kw

    return ControlledLivePolicy(
        mode=mode,
        max_budget_change_percent=_float("ADS_MAX_BUDGET_CHANGE_PERCENT", default_tilt),
        require_approval_above_percent=_float("ADS_REQUIRE_APPROVAL_ABOVE_PERCENT", default_approval_above),
        cooldown_days=_int("ADS_BUDGET_COOLDOWN_DAYS", default_cooldown),
        max_account_daily_budget=_float("ADS_MAX_ACCOUNT_DAILY_BUDGET", default_daily_cap),
        cpqa_rollback_worsen_percent=_float("ADS_CPQA_ROLLBACK_WORSEN_PERCENT", 25.0),
        conversion_delay_days=_int("ADS_CONVERSION_DELAY_DAYS", 3),
        auto_add_negatives=_bool("ADS_AUTO_ADD_NEGATIVES", True),
        auto_pause_harmful_keywords=_bool("ADS_AUTO_PAUSE_HARMFUL_KEYWORDS", True),
        auto_budget_under_cap=_bool("ADS_AUTO_BUDGET", False),
        positive_keywords=positive_kw,
        asset_publish=asset_publish,
    )


# High-confidence negative themes for wellness Grant (auto-apply candidates).
HARMFUL_NEGATIVE_PHRASES: tuple[str, ...] = (
    "free diagnosis",
    "medical diagnosis",
    "psychiatric medication",
    "antidepressant prescription",
    "emergency suicide help",
    "hospital treatment",
    "suicide hotline",
    "psychiatrist near me",
    "clinical trial",
    "jobs",
    "salary",
    "hiring",
    "courses pdf",
    "pdf",
    "definition",
    "test answers",
    "reddit",
    "wikipedia",
)

HARMFUL_TERM_MARKERS: tuple[str, ...] = (
    "suicide",
    "suicidal",
    "kill myself",
    "psychiatrist",
    "psychiatric",
    "diagnosis",
    "medication",
    "prescription",
    "antidepressant",
    "hospital treatment",
    "emergency",
    "hotline",
    "jobs",
    "salary",
    "hiring",
    "pdf",
    "definition",
    "test answers",
    "free diagnosis",
)

# Brand / policy gate — block clinical & crisis claims in creatives.
CLINICAL_BLOCKLIST: tuple[str, ...] = (
    "diagnos",
    "disorder",
    "psychiatr",
    "psycholog",  # psychologist / psychological help claims
    "психолог",
    "психотерап",
    "психіатр",
    "therap",
    "treatment",
    "лікуван",
    "medication",
    "prescription",
    "antidepress",
    "suicid",
    "crisis",
    "clinical",
    "cure",
    "disease",
    "ptsd",
    "schizophren",
)

# Ad Grants creatives must present the NGO mission, not direct commercial sales.
GRANT_COMMERCIAL_BLOCKLIST: tuple[str, ...] = (
    "€",
    "pricing",
    "price",
    "subscription",
    "subscribe",
    "purchase",
    "checkout",
    "buy now",
    "sale",
    "payment",
    "paypal",
    "app store",
    "google play",
    "download app",
    "mobile app",
    " ltd",
    "підписк",
    "ціна",
    "купити",
    "оплат",
    "продаж",
    "завантажити",
    "мобільний app",
)
