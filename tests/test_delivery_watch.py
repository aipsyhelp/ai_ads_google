from datetime import date

from ads_ai_agents.intelligence.delivery_watch import (
    DeliverySnapshot,
    build_snapshot,
    compare,
    render_text,
)


def _report(
    *,
    trend: dict[str, dict],
    enabled_keywords: int = 160,
    budget_usd: float = 329.0,
    blockers: int = 0,
    auto_tagging: bool = True,
    primary: bool = True,
) -> dict:
    return {
        "account": {
            "status": "ENABLED",
            "auto_tagging_enabled": auto_tagging,
            "grant_status": "ENABLED",
            "grant_delivery_status": "SERVING",
        },
        "totals": {
            "campaigns": 8,
            "ad_groups": 24,
            "enabled_keywords": enabled_keywords,
            "paused_keywords": 341,
            "budget_usd": budget_usd,
            "groups_with_blockers": blockers,
        },
        "daily_trend": trend,
        "conversion_actions": [
            {"name": "www.example.com (web) chat_message_sent", "primary_for_goal": primary},
            {"name": "www.example.com (web) session_start", "primary_for_goal": False},
        ],
    }


def _snapshot(report: dict, *, through: str = "2026-08-31", taken_at: str = "2026-09-01T09:00:00+00:00"):
    return build_snapshot(
        report, taken_at=taken_at, complete_through=date.fromisoformat(through)
    )


def _stalled_trend(days: int = 10) -> dict[str, dict]:
    return {
        f"2026-08-{day:02d}": {"impressions": 0, "clicks": 0, "cost": 0.0}
        for day in range(10, 10 + days)
    }


def test_snapshot_counts_the_trailing_zero_spend_streak():
    trend = {
        "2026-08-04": {"impressions": 900, "clicks": 60, "cost": 118.4},
        "2026-08-05": {"impressions": 850, "clicks": 55, "cost": 120.1},
        "2026-08-06": {"impressions": 0, "clicks": 0, "cost": 0.0},
        "2026-08-07": {"impressions": 0, "clicks": 0, "cost": 0.0},
    }

    snapshot = _snapshot(_report(trend=trend), through="2026-08-07")

    assert snapshot.days_without_spend == 2
    assert snapshot.last_spend_date == "2026-08-05"
    assert snapshot.spend_7d == 238.5
    assert snapshot.primary_conversion_actions == (
        "www.example.com (web) chat_message_sent",
    )


def test_days_missing_from_the_api_response_still_count_as_dark():
    """Google returns no row at all for a day with zero activity."""
    trend = {
        "2026-08-05": {"impressions": 850, "clicks": 55, "cost": 120.1},
        "2026-08-06": {"impressions": 0, "clicks": 0, "cost": 0.0},
    }

    snapshot = _snapshot(_report(trend=trend), through="2026-08-18")

    # 08-06 through 08-18 inclusive, even though only 08-06 came back as a row.
    assert snapshot.days_without_spend == 13
    assert snapshot.last_spend_date == "2026-08-05"
    assert snapshot.spend_7d == 0.0
    assert snapshot.ctr_7d is None


def test_partial_day_is_excluded_from_the_streak_but_reported():
    trend = {
        "2026-08-18": {"impressions": 0, "clicks": 0, "cost": 0.0},
        "2026-08-19": {"impressions": 120, "clicks": 9, "cost": 11.4},
    }

    snapshot = _snapshot(_report(trend=trend), through="2026-08-18")

    assert snapshot.complete_through == "2026-08-18"
    assert snapshot.spend_today == 11.4
    assert snapshot.spend_7d == 0.0
    # Today's spend outranks the stall: it is the reactivation signal.
    codes = {alert.code for alert in compare(snapshot, None).alerts}
    assert "delivery_restored" in codes
    assert "delivery_stalled" not in codes


def test_stall_is_critical_and_extends_over_time():
    yesterday = _snapshot(_report(trend=_stalled_trend(13)), through="2026-08-22")
    today = _snapshot(_report(trend=_stalled_trend(14)), through="2026-08-23")

    comparison = compare(today, yesterday)
    codes = {alert.code: alert.level for alert in comparison.alerts}

    assert codes["delivery_stalled"] == "critical"
    assert codes["stall_extended"] == "warning"
    assert comparison.has_blocking_alert is True
    assert comparison.reactivated is False
    assert comparison.deltas["days_without_spend"] == {"before": 13, "after": 14, "delta": 1}


def test_first_full_day_of_spend_after_stall_raises_the_reactivation_signal():
    stalled = _snapshot(_report(trend=_stalled_trend(14)), through="2026-08-23")
    recovered_trend = _stalled_trend(14)
    recovered_trend["2026-08-24"] = {"impressions": 420, "clicks": 31, "cost": 42.7}
    recovered = _snapshot(_report(trend=recovered_trend), through="2026-08-24")

    comparison = compare(recovered, stalled)
    codes = {alert.code for alert in comparison.alerts}

    assert comparison.reactivated is True
    assert "delivery_stalled" not in codes
    assert recovered.days_without_spend == 0


def test_keyword_core_shrink_is_critical():
    before = _snapshot(_report(trend=_stalled_trend(), enabled_keywords=160))
    after = _snapshot(_report(trend=_stalled_trend(), enabled_keywords=120))

    comparison = compare(after, before)
    shrink = [alert for alert in comparison.alerts if alert.code == "keyword_core_shrank"]

    assert shrink and shrink[0].level == "critical"


def test_grant_policy_breaches_are_flagged_without_a_baseline():
    snapshot = _snapshot(
        _report(trend=_stalled_trend(), budget_usd=629.0, auto_tagging=False, primary=False)
    )

    comparison = compare(snapshot, None)
    codes = {alert.code for alert in comparison.alerts}

    assert {"budget_over_grant_cap", "auto_tagging_disabled", "no_primary_conversion"} <= codes
    assert comparison.previous is None
    assert "baseline" in render_text(comparison)


def test_ctr_below_grant_floor_only_fires_with_real_volume():
    thin = _snapshot(
        _report(trend={"2026-08-19": {"impressions": 40, "clicks": 1, "cost": 3.0}}),
        through="2026-08-19",
    )
    heavy = _snapshot(
        _report(trend={"2026-08-19": {"impressions": 4000, "clicks": 80, "cost": 90.0}}),
        through="2026-08-19",
    )

    assert not any(a.code == "ctr_below_grant_floor" for a in compare(thin, None).alerts)
    assert any(a.code == "ctr_below_grant_floor" for a in compare(heavy, None).alerts)


def test_snapshot_survives_a_json_round_trip():
    original = _snapshot(_report(trend=_stalled_trend()))

    restored = DeliverySnapshot.from_dict(original.to_dict())

    assert restored == original


def test_snapshot_from_dict_tolerates_older_and_newer_schemas():
    payload = _snapshot(_report(trend=_stalled_trend())).to_dict()
    payload["field_added_by_a_future_version"] = 1
    payload.pop("spend_today")

    restored = DeliverySnapshot.from_dict(payload)

    assert restored.enabled_keywords == 160
    assert restored.spend_today == 0.0


def test_defaults_to_yesterday_when_no_cutoff_is_supplied():
    snapshot = build_snapshot(_report(trend=_stalled_trend()))

    assert snapshot.complete_through is not None
    assert snapshot.complete_through < date.today().isoformat()
