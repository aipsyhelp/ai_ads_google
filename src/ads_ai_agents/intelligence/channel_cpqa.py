from __future__ import annotations

from typing import Any

from ads_ai_agents.adapters.google_ads_readonly import GoogleAdsReadOnlyAdapter
from ads_ai_agents.logging_utils import configure_logging

logger = configure_logging()

# Quality conversion action name markers for CPQA (chat/form).
QUALITY_NAME_MARKERS = (
    "chat_open",
    "go_to_chat",
    "form_submit",
    "qualified_onboarding",
    "first_meaningful",
)


def build_channel_cpqa_report(
    adapter: GoogleAdsReadOnlyAdapter | None = None,
    *,
    quality_action_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Compare Search vs PMax using quality conversions only (CPQA), not total Conversions."""
    adapter = adapter or GoogleAdsReadOnlyAdapter()
    cid = adapter.settings.customer_id

    actions = adapter.list_conversion_actions()
    quality_ids: list[str] = list(quality_action_ids or [])
    quality_names: list[str] = []
    if not quality_ids:
        for row in actions:
            if row.get("error"):
                continue
            name = str(row.get("conversion_action.name") or "")
            lower = name.lower()
            action_id = str(row.get("conversion_action.id") or "")
            primary = bool(row.get("conversion_action.primary_for_goal"))
            if any(m in lower for m in QUALITY_NAME_MARKERS):
                quality_ids.append(action_id)
                quality_names.append(name)
            elif primary and any(m in lower for m in ("chat", "form", "purchase")):
                quality_ids.append(action_id)
                quality_names.append(name)

    # Campaign totals (spend) + quality conversions via conversion_action segment when possible.
    campaigns = adapter.list_campaigns()
    by_channel: dict[str, dict[str, float]] = {}
    campaign_rows: list[dict[str, Any]] = []

    quality_by_campaign: dict[str, float] = {}
    for action_id in quality_ids:
        if not action_id:
            continue
        resource = f"customers/{cid}/conversionActions/{action_id}"
        result = adapter.run_query(
            f"cpqa_{action_id}",
            f"""
            SELECT
              campaign.id,
              campaign.name,
              campaign.advertising_channel_type,
              segments.conversion_action,
              metrics.conversions
            FROM campaign
            WHERE segments.date DURING THIS_MONTH
              AND campaign.status != 'REMOVED'
              AND segments.conversion_action = '{resource}'
            """,
        )
        if not result.ok:
            logger.warning("CPQA query failed for %s: %s", action_id, result.error)
            continue
        for row in result.rows:
            name = str(row.get("campaign.name") or "")
            quality_by_campaign[name] = quality_by_campaign.get(name, 0.0) + float(
                row.get("metrics.conversions") or 0
            )

    for camp in campaigns:
        if camp.get("error"):
            continue
        name = str(camp.get("name") or "")
        channel = _channel_label(camp.get("channel"))
        cost = float(camp.get("cost") or 0)
        reported = float(camp.get("conversions") or 0)
        quality = float(quality_by_campaign.get(name, 0.0))
        reported_cpa = (cost / reported) if reported else None
        cpqa = (cost / quality) if quality else None
        row = {
            "name": name,
            "channel": channel,
            "spend_mtd": round(cost, 2),
            "reported_conversions": round(reported, 2),
            "quality_conversions": round(quality, 2),
            "cpa_reported": round(reported_cpa, 2) if reported_cpa is not None else None,
            "cpqa": round(cpqa, 2) if cpqa is not None else None,
        }
        campaign_rows.append(row)
        bucket = by_channel.setdefault(
            channel,
            {"spend": 0.0, "reported": 0.0, "quality": 0.0},
        )
        bucket["spend"] += cost
        bucket["reported"] += reported
        bucket["quality"] += quality

    channels = []
    for channel, vals in sorted(by_channel.items()):
        channels.append(
            {
                "channel": channel,
                "spend_mtd": round(vals["spend"], 2),
                "reported_conversions": round(vals["reported"], 2),
                "quality_conversions": round(vals["quality"], 2),
                "cpa_reported": round(vals["spend"] / vals["reported"], 2) if vals["reported"] else None,
                "cpqa": round(vals["spend"] / vals["quality"], 2) if vals["quality"] else None,
            }
        )

    search = next((c for c in channels if c["channel"] == "SEARCH"), None)
    pmax = next((c for c in channels if c["channel"] == "PERFORMANCE_MAX"), None)
    narrative = [
        "CPQA uses chat/form quality conversion actions only — not total Google Conversions.",
        "Budget reallocation remains shadow-only until CPQA has 14 days post goal-cleanup.",
    ]
    if search and pmax:
        if (search.get("cpqa") or 10**9) < (pmax.get("cpqa") or 10**9):
            narrative.append(
                f"Hypothesis: SEARCH CPQA ${search.get('cpqa')} beats PMax ${pmax.get('cpqa')} — "
                "eligible for shadow budget tilt after observation window."
            )
        else:
            narrative.append(
                f"Hypothesis: PMax CPQA ${pmax.get('cpqa')} vs SEARCH ${search.get('cpqa')} — "
                "do not shift budget until quality volume is stable."
            )
    if not quality_ids:
        narrative.append("WARNING: no quality conversion action IDs resolved — CPQA incomplete.")

    return {
        "quality_action_ids": quality_ids,
        "quality_action_names": quality_names,
        "channels": channels,
        "campaigns": sorted(campaign_rows, key=lambda r: r["spend_mtd"], reverse=True),
        "narrative": narrative,
        "budget_mutate_allowed": False,
    }


def _channel_label(value: Any) -> str:
    if value in (2, "2", "SEARCH"):
        return "SEARCH"
    if value in (10, "10", "PERFORMANCE_MAX"):
        return "PERFORMANCE_MAX"
    text = str(value or "UNKNOWN")
    if text.isdigit():
        return {"2": "SEARCH", "10": "PERFORMANCE_MAX"}.get(text, f"CHANNEL_{text}")
    return text
