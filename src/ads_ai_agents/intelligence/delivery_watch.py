"""Day-over-day delivery comparison for the Ad Grants account.

The delivery audit answers "what does the account look like right now". This
module answers the question that actually matters while the account waits for
Grant reactivation: "what changed since yesterday, and is traffic back?".

Everything here is pure: it consumes an audit report dict and a previously
stored snapshot, so the comparison logic is testable without API access.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

# Ad Grants program limits. Breaching either one is grounds for deactivation,
# so they are watched every run rather than only during a manual audit.
GRANT_DAILY_BUDGET_CAP_USD = 329.0
GRANT_MIN_ACCOUNT_CTR = 0.05

# Below this volume a CTR reading is noise, not a policy signal.
MIN_IMPRESSIONS_FOR_CTR_JUDGEMENT = 100

# A keyword core that shrinks faster than this is starving delivery again.
KEYWORD_SHRINK_ALERT_RATIO = 0.10

TREND_WINDOW_DAYS = 7


@dataclass(frozen=True)
class DeliverySnapshot:
    taken_at: str
    grant_status: str
    grant_delivery_status: str
    account_status: str
    auto_tagging_enabled: bool
    campaigns: int
    ad_groups: int
    enabled_keywords: int
    paused_keywords: int
    budget_usd: float
    groups_with_blockers: int
    last_spend_date: str | None
    days_without_spend: int
    impressions_7d: int
    clicks_7d: int
    spend_7d: float
    ctr_7d: float | None
    primary_conversion_actions: tuple[str, ...]
    # Today is excluded from the streak because partial-day reporting would fake
    # a stall every morning; it is reported separately as an early signal.
    spend_today: float = 0.0
    complete_through: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["primary_conversion_actions"] = list(self.primary_conversion_actions)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> DeliverySnapshot:
        data = dict(payload)
        data["primary_conversion_actions"] = tuple(data.get("primary_conversion_actions") or ())
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{key: value for key, value in data.items() if key in allowed})


@dataclass(frozen=True)
class Alert:
    code: str
    level: str  # info | warning | critical
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass
class DeliveryComparison:
    current: DeliverySnapshot
    previous: DeliverySnapshot | None
    deltas: dict[str, dict[str, Any]] = field(default_factory=dict)
    alerts: list[Alert] = field(default_factory=list)

    @property
    def has_blocking_alert(self) -> bool:
        return any(alert.level == "critical" for alert in self.alerts)

    @property
    def reactivated(self) -> bool:
        return any(alert.code == "delivery_restored" for alert in self.alerts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "current": self.current.to_dict(),
            "previous": self.previous.to_dict() if self.previous else None,
            "deltas": self.deltas,
            "alerts": [alert.to_dict() for alert in self.alerts],
            "reactivated": self.reactivated,
            "has_blocking_alert": self.has_blocking_alert,
        }


def _densify_trend(
    trend: dict[str, Any],
    through: date,
) -> tuple[list[tuple[date, dict[str, Any]]], float]:
    """Fill in the days Google omits entirely.

    The API returns no row at all for a day with no activity, so reading the
    streak straight off the response understates a stall: the account looks
    "last seen 6 days ago" when it has in fact been dark for two weeks.
    Days after `through` are excluded from the series and returned separately,
    since a partial day of reporting is not evidence of anything.
    """
    parsed: dict[date, dict[str, Any]] = {}
    for key, values in trend.items():
        try:
            day = date.fromisoformat(str(key)[:10])
        except ValueError:
            continue
        parsed[day] = values or {}

    spend_after_cutoff = round(
        sum(float(values.get("cost") or 0) for day, values in parsed.items() if day > through),
        2,
    )

    complete = {day: values for day, values in parsed.items() if day <= through}
    if not complete:
        return [], spend_after_cutoff

    series: list[tuple[date, dict[str, Any]]] = []
    cursor = min(complete)
    while cursor <= through:
        series.append((cursor, complete.get(cursor, {"impressions": 0, "clicks": 0, "cost": 0.0})))
        cursor += timedelta(days=1)
    return series, spend_after_cutoff


def unique_enabled_budget_usd(campaign_summary: list[dict[str, Any]] | None) -> float | None:
    """Sum each budget resource once.

    Shared Grant budgets are attached to several campaigns. Summing
    `campaign_budget.amount_micros` per campaign inflates the total (e.g. one
    $257 shared budget counted four times as $1028) and falsely trips the
    Grant-cap alert.
    """
    if not campaign_summary:
        return None
    by_budget: dict[str, float] = {}
    for item in campaign_summary:
        budget_id = str(item.get("budget_id") or item.get("budget_resource_name") or "")
        amount = float(item.get("budget_usd") or 0)
        if budget_id:
            by_budget[budget_id] = amount
        else:
            # Fall back to per-campaign amount only when the audit could not
            # expose a budget id — better a slight over-count than silence.
            by_budget[f"campaign:{item.get('campaign_id')}"] = amount
    if not by_budget:
        return None
    return round(sum(by_budget.values()), 2)


def build_snapshot(
    report: dict[str, Any],
    *,
    taken_at: str | None = None,
    complete_through: date | None = None,
) -> DeliverySnapshot:
    account = report.get("account") or {}
    totals = report.get("totals") or {}
    trend = report.get("daily_trend") or {}

    through = complete_through or (datetime.now(timezone.utc).date() - timedelta(days=1))
    ordered_days, spend_today = _densify_trend(trend, through)

    trailing_zero = 0
    for _, values in reversed(ordered_days):
        if float(values.get("cost") or 0) > 0:
            break
        trailing_zero += 1

    last_spend_date = next(
        (
            day.isoformat()
            for day, values in reversed(ordered_days)
            if float(values.get("cost") or 0) > 0
        ),
        None,
    )

    window = ordered_days[-TREND_WINDOW_DAYS:]
    impressions = sum(int(values.get("impressions") or 0) for _, values in window)
    clicks = sum(int(values.get("clicks") or 0) for _, values in window)
    spend = round(sum(float(values.get("cost") or 0) for _, values in window), 2)

    primaries = tuple(
        sorted(
            action["name"]
            for action in report.get("conversion_actions") or []
            if action.get("primary_for_goal")
        )
    )

    unique_budget = unique_enabled_budget_usd(report.get("campaign_summary") or [])
    budget_usd = (
        unique_budget
        if unique_budget is not None
        else round(float(totals.get("budget_usd") or 0), 2)
    )

    return DeliverySnapshot(
        taken_at=taken_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        grant_status=str(account.get("grant_status") or "unknown"),
        grant_delivery_status=str(account.get("grant_delivery_status") or "unknown"),
        account_status=str(account.get("status") or "unknown"),
        auto_tagging_enabled=bool(account.get("auto_tagging_enabled")),
        campaigns=int(totals.get("campaigns") or 0),
        ad_groups=int(totals.get("ad_groups") or 0),
        enabled_keywords=int(totals.get("enabled_keywords") or 0),
        paused_keywords=int(totals.get("paused_keywords") or 0),
        budget_usd=budget_usd,
        groups_with_blockers=int(totals.get("groups_with_blockers") or 0),
        last_spend_date=last_spend_date,
        days_without_spend=trailing_zero,
        impressions_7d=impressions,
        clicks_7d=clicks,
        spend_7d=spend,
        ctr_7d=(clicks / impressions) if impressions else None,
        primary_conversion_actions=primaries,
        spend_today=spend_today,
        complete_through=through.isoformat(),
    )


_TRACKED_FIELDS = (
    "enabled_keywords",
    "paused_keywords",
    "campaigns",
    "ad_groups",
    "budget_usd",
    "groups_with_blockers",
    "impressions_7d",
    "clicks_7d",
    "spend_7d",
    "spend_today",
    "days_without_spend",
    "grant_status",
    "grant_delivery_status",
    "account_status",
    "auto_tagging_enabled",
)


def compare(
    current: DeliverySnapshot,
    previous: DeliverySnapshot | None,
) -> DeliveryComparison:
    comparison = DeliveryComparison(current=current, previous=previous)

    if previous is not None:
        for name in _TRACKED_FIELDS:
            before = getattr(previous, name)
            after = getattr(current, name)
            if before == after:
                continue
            entry: dict[str, Any] = {"before": before, "after": after}
            if isinstance(before, (int, float)) and isinstance(after, (int, float)):
                entry["delta"] = round(after - before, 2)
            comparison.deltas[name] = entry

    comparison.alerts.extend(_state_alerts(current))
    if previous is not None:
        comparison.alerts.extend(_transition_alerts(current, previous))
    return comparison


def _state_alerts(current: DeliverySnapshot) -> list[Alert]:
    alerts: list[Alert] = []

    if current.budget_usd > GRANT_DAILY_BUDGET_CAP_USD + 0.01:
        alerts.append(
            Alert(
                code="budget_over_grant_cap",
                level="critical",
                message=(
                    f"Enabled daily budget ${current.budget_usd} exceeds the Ad Grants cap "
                    f"of ${GRANT_DAILY_BUDGET_CAP_USD}."
                ),
            )
        )

    if not current.auto_tagging_enabled:
        alerts.append(
            Alert(
                code="auto_tagging_disabled",
                level="critical",
                message="Auto-tagging is off, so clicks cannot be matched to conversions.",
            )
        )

    if not current.primary_conversion_actions:
        alerts.append(
            Alert(
                code="no_primary_conversion",
                level="critical",
                message="No conversion action is primary — Grant policy requires meaningful conversions.",
            )
        )

    if (
        current.ctr_7d is not None
        and current.impressions_7d >= MIN_IMPRESSIONS_FOR_CTR_JUDGEMENT
        and current.ctr_7d < GRANT_MIN_ACCOUNT_CTR
    ):
        alerts.append(
            Alert(
                code="ctr_below_grant_floor",
                level="warning",
                message=(
                    f"7-day CTR {current.ctr_7d:.2%} is below the {GRANT_MIN_ACCOUNT_CTR:.0%} "
                    "Ad Grants requirement."
                ),
            )
        )

    if current.groups_with_blockers:
        alerts.append(
            Alert(
                code="groups_with_blockers",
                level="warning",
                message=f"{current.groups_with_blockers} ad group(s) cannot serve.",
            )
        )

    # Partial-day spend is the earliest possible reactivation evidence, so it is
    # checked before declaring a stall and reported even on a first-ever run.
    if current.spend_today > 0 and current.days_without_spend > 0:
        alerts.append(
            Alert(
                code="delivery_restored",
                level="info",
                message=(
                    f"Spend today is ${current.spend_today} after "
                    f"{current.days_without_spend} dark day(s) — delivery is back."
                ),
            )
        )
    elif current.days_without_spend >= 1 and current.spend_7d <= 0:
        alerts.append(
            Alert(
                code="delivery_stalled",
                level="critical",
                message=(
                    f"No spend for {current.days_without_spend} consecutive day(s) "
                    f"through {current.complete_through}; the account is not entering auctions."
                ),
            )
        )

    return alerts


def _transition_alerts(
    current: DeliverySnapshot,
    previous: DeliverySnapshot,
) -> list[Alert]:
    alerts: list[Alert] = []

    # The signal worth waking up for: money moved again after a stall.
    if previous.days_without_spend > 0 and current.days_without_spend == 0:
        alerts.append(
            Alert(
                code="delivery_restored",
                level="info",
                message=(
                    f"Spend resumed on {current.last_spend_date} after "
                    f"{previous.days_without_spend} day(s) of zero delivery."
                ),
            )
        )
    elif current.spend_7d > 0 and previous.spend_7d <= 0:
        alerts.append(
            Alert(
                code="delivery_restored",
                level="info",
                message=f"First spend in the 7-day window: ${current.spend_7d}.",
            )
        )

    if current.days_without_spend > previous.days_without_spend > 0:
        alerts.append(
            Alert(
                code="stall_extended",
                level="warning",
                message=(
                    f"Zero-delivery streak grew from {previous.days_without_spend} to "
                    f"{current.days_without_spend} day(s)."
                ),
            )
        )

    if previous.enabled_keywords > 0:
        shrink = (previous.enabled_keywords - current.enabled_keywords) / previous.enabled_keywords
        if shrink > KEYWORD_SHRINK_ALERT_RATIO:
            alerts.append(
                Alert(
                    code="keyword_core_shrank",
                    level="critical",
                    message=(
                        f"Enabled keywords fell {shrink:.0%} "
                        f"({previous.enabled_keywords} → {current.enabled_keywords}); "
                        "delivery starvation risk."
                    ),
                )
            )

    if current.campaigns < previous.campaigns:
        alerts.append(
            Alert(
                code="campaigns_lost",
                level="warning",
                message=(
                    f"Enabled Search campaigns dropped from {previous.campaigns} "
                    f"to {current.campaigns}."
                ),
            )
        )

    if current.grant_delivery_status != previous.grant_delivery_status:
        alerts.append(
            Alert(
                code="grant_delivery_status_changed",
                level="critical",
                message=(
                    f"Grant delivery status changed: {previous.grant_delivery_status} → "
                    f"{current.grant_delivery_status}."
                ),
            )
        )

    if current.grant_status != previous.grant_status:
        alerts.append(
            Alert(
                code="grant_status_changed",
                level="critical",
                message=f"Grant status changed: {previous.grant_status} → {current.grant_status}.",
            )
        )

    return alerts


def render_text(comparison: DeliveryComparison) -> str:
    current = comparison.current
    lines = [
        "== Grant delivery watch ==",
        f"taken_at={current.taken_at}",
        (
            f"grant={current.grant_status}/{current.grant_delivery_status} "
            f"account={current.account_status} auto_tagging={current.auto_tagging_enabled}"
        ),
        (
            f"campaigns={current.campaigns} ad_groups={current.ad_groups} "
            f"kw_enabled={current.enabled_keywords} kw_paused={current.paused_keywords} "
            f"budget=${current.budget_usd} blockers={current.groups_with_blockers}"
        ),
        (
            f"7d: impressions={current.impressions_7d} clicks={current.clicks_7d} "
            f"spend=${current.spend_7d} "
            f"ctr={'n/a' if current.ctr_7d is None else format(current.ctr_7d, '.2%')}"
        ),
        (
            f"last_spend_date={current.last_spend_date or 'never in window'} "
            f"days_without_spend={current.days_without_spend} "
            f"(complete through {current.complete_through}) "
            f"spend_today=${current.spend_today}"
        ),
    ]

    if comparison.previous is None:
        lines.append("")
        lines.append("no previous snapshot — this run establishes the baseline")
    elif comparison.deltas:
        lines.append("")
        lines.append(f"-- changes since {comparison.previous.taken_at} --")
        for name, entry in comparison.deltas.items():
            delta = entry.get("delta")
            suffix = f" ({delta:+})" if isinstance(delta, (int, float)) else ""
            lines.append(f"  {name}: {entry['before']} -> {entry['after']}{suffix}")
    else:
        lines.append("")
        lines.append(f"no tracked change since {comparison.previous.taken_at}")

    if comparison.alerts:
        lines.append("")
        lines.append("-- alerts --")
        for alert in comparison.alerts:
            lines.append(f"  [{alert.level.upper()}] {alert.code}: {alert.message}")
    else:
        lines.append("")
        lines.append("no alerts")

    return "\n".join(lines)


def snapshot_filename(taken_at: str | None = None) -> str:
    stamp = (taken_at or date.today().isoformat())[:10]
    return f"delivery-{stamp}.json"
