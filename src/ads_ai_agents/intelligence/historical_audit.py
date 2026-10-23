from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ads_ai_agents.adapters.google_ads_readonly import GoogleAdsReadOnlyAdapter
from ads_ai_agents.intelligence.channel_cpqa import QUALITY_NAME_MARKERS
from ads_ai_agents.intelligence.report_store import ReportStore
from ads_ai_agents.logging_utils import configure_logging

logger = configure_logging()

WINDOWS = (7, 30, 180)


def _date_predicate(days: int) -> str:
    if days in (7, 14, 30):
        return f"segments.date DURING LAST_{days}_DAYS"
    end = date.today()
    start = end - timedelta(days=days - 1)
    return f"segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'"


def _micros(value: Any) -> float:
    try:
        return float(value or 0) / 1_000_000
    except (TypeError, ValueError):
        return 0.0


def _resolve_quality_ids(adapter: GoogleAdsReadOnlyAdapter) -> tuple[list[str], list[str]]:
    ids: list[str] = []
    names: list[str] = []
    for row in adapter.list_conversion_actions():
        if row.get("error"):
            continue
        name = str(row.get("conversion_action.name") or "")
        lower = name.lower()
        action_id = str(row.get("conversion_action.id") or "")
        primary = bool(row.get("conversion_action.primary_for_goal"))
        if any(m in lower for m in QUALITY_NAME_MARKERS):
            ids.append(action_id)
            names.append(name)
        elif primary and any(m in lower for m in ("chat", "form", "purchase")):
            ids.append(action_id)
            names.append(name)
    return ids, names


def _campaign_window(adapter: GoogleAdsReadOnlyAdapter, days: int) -> list[dict[str, Any]]:
    pred = _date_predicate(days)
    result = adapter.run_query(
        f"campaigns_{days}d",
        f"""
        SELECT
          campaign.id,
          campaign.name,
          campaign.status,
          campaign.advertising_channel_type,
          campaign.bidding_strategy_type,
          campaign_budget.resource_name,
          campaign_budget.amount_micros,
          campaign_budget.explicitly_shared,
          metrics.cost_micros,
          metrics.clicks,
          metrics.impressions,
          metrics.conversions,
          metrics.ctr,
          metrics.average_cpc,
          metrics.search_impression_share,
          metrics.search_budget_lost_impression_share,
          metrics.search_rank_lost_impression_share
        FROM campaign
        WHERE campaign.status != 'REMOVED'
          AND {pred}
        """,
    )
    if not result.ok:
        return [{"error": result.error, "window_days": days}]
    rows: list[dict[str, Any]] = []
    for row in result.rows:
        cost = _micros(row.get("metrics.cost_micros"))
        clicks = int(row.get("metrics.clicks") or 0)
        impressions = int(row.get("metrics.impressions") or 0)
        conversions = float(row.get("metrics.conversions") or 0)
        rows.append(
            {
                "id": str(row.get("campaign.id")),
                "name": row.get("campaign.name"),
                "status": row.get("campaign.status"),
                "channel": row.get("campaign.advertising_channel_type"),
                "bidding": row.get("campaign.bidding_strategy_type"),
                "budget_resource_name": str(row.get("campaign_budget.resource_name") or ""),
                "daily_budget": round(_micros(row.get("campaign_budget.amount_micros")), 2),
                "shared_budget": bool(row.get("campaign_budget.explicitly_shared")),
                "spend": round(cost, 2),
                "clicks": clicks,
                "impressions": impressions,
                "ctr": round(float(row.get("metrics.ctr") or 0), 4),
                "cpc": round(_micros(row.get("metrics.average_cpc")), 4),
                "conversions": round(conversions, 2),
                "cpa_reported": round(cost / conversions, 2) if conversions else None,
                "search_is": row.get("metrics.search_impression_share"),
                "lost_is_budget": row.get("metrics.search_budget_lost_impression_share"),
                "lost_is_rank": row.get("metrics.search_rank_lost_impression_share"),
                "window_days": days,
            }
        )
    return rows


def _quality_by_campaign(adapter: GoogleAdsReadOnlyAdapter, days: int, quality_ids: list[str]) -> dict[str, float]:
    cid = adapter.settings.customer_id
    pred = _date_predicate(days)
    totals: dict[str, float] = {}
    for action_id in quality_ids:
        resource = f"customers/{cid}/conversionActions/{action_id}"
        result = adapter.run_query(
            f"quality_{days}d_{action_id}",
            f"""
            SELECT
              campaign.name,
              segments.conversion_action,
              metrics.conversions
            FROM campaign
            WHERE campaign.status != 'REMOVED'
              AND {pred}
              AND segments.conversion_action = '{resource}'
            """,
        )
        if not result.ok:
            logger.warning("Quality query failed %sd %s: %s", days, action_id, result.error)
            continue
        for row in result.rows:
            name = str(row.get("campaign.name") or "")
            totals[name] = totals.get(name, 0.0) + float(row.get("metrics.conversions") or 0)
    return totals


def _search_terms(adapter: GoogleAdsReadOnlyAdapter, days: int) -> list[dict[str, Any]]:
    # Search term view commonly supports up to 30d cleanly; for 180d use BETWEEN.
    pred = _date_predicate(min(days, 30) if days <= 30 else days)
    result = adapter.run_query(
        f"search_terms_{days}d",
        f"""
        SELECT
          search_term_view.search_term,
          campaign.id,
          campaign.name,
          ad_group.name,
          metrics.impressions,
          metrics.clicks,
          metrics.cost_micros,
          metrics.conversions
        FROM search_term_view
        WHERE {pred}
        ORDER BY metrics.cost_micros DESC
        LIMIT 2000
        """,
    )
    if not result.ok:
        return [{"error": result.error, "window_days": days}]
    out = []
    for row in result.rows:
        cost = _micros(row.get("metrics.cost_micros"))
        conversions = float(row.get("metrics.conversions") or 0)
        out.append(
            {
                "search_term": row.get("search_term_view.search_term"),
                "campaign_id": str(row.get("campaign.id")),
                "campaign": row.get("campaign.name"),
                "ad_group": row.get("ad_group.name"),
                "impressions": int(row.get("metrics.impressions") or 0),
                "clicks": int(row.get("metrics.clicks") or 0),
                "spend": round(cost, 2),
                "conversions": round(conversions, 2),
                "window_days": days,
            }
        )
    return out


def _devices(adapter: GoogleAdsReadOnlyAdapter, days: int) -> list[dict[str, Any]]:
    pred = _date_predicate(days)
    result = adapter.run_query(
        f"devices_{days}d",
        f"""
        SELECT
          segments.device,
          metrics.cost_micros,
          metrics.clicks,
          metrics.conversions,
          metrics.impressions
        FROM customer
        WHERE {pred}
        """,
    )
    if not result.ok:
        return [{"error": result.error}]
    return [
        {
            "device": row.get("segments.device"),
            "spend": round(_micros(row.get("metrics.cost_micros")), 2),
            "clicks": int(row.get("metrics.clicks") or 0),
            "impressions": int(row.get("metrics.impressions") or 0),
            "conversions": round(float(row.get("metrics.conversions") or 0), 2),
            "window_days": days,
        }
        for row in result.rows
    ]


def _classify_campaign(row: dict[str, Any]) -> str:
    spend = float(row.get("spend") or 0)
    quality = float(row.get("quality_conversions") or 0)
    reported = float(row.get("conversions") or 0)
    if spend < 5 and reported == 0 and quality == 0:
        return "idle_or_zero"
    if spend >= 50 and quality == 0 and reported > 0:
        return "cheap_useless_conversions"
    if spend >= 50 and quality == 0 and reported == 0:
        return "zero_quality_spender"
    if quality >= 5 and (row.get("cpqa") or 10**9) < 250:
        return "stable_winner_candidate"
    if quality > 0 and spend > 0:
        return "needs_more_evidence"
    return "observe"


def run_historical_audit(
    adapter: GoogleAdsReadOnlyAdapter | None = None,
    *,
    windows: tuple[int, ...] = WINDOWS,
    store: ReportStore | None = None,
) -> dict[str, Any]:
    """Full multi-window audit (7/30/180d) with CPQA where quality actions resolve."""
    adapter = adapter or GoogleAdsReadOnlyAdapter()
    store = store or ReportStore()
    day_dir = store.day_dir(kind="audit")
    quality_ids, quality_names = _resolve_quality_ids(adapter)

    windows_payload: dict[str, Any] = {}
    for days in windows:
        campaigns = _campaign_window(adapter, days)
        quality_map = _quality_by_campaign(adapter, days, quality_ids) if quality_ids else {}
        enriched = []
        for row in campaigns:
            if row.get("error"):
                enriched.append(row)
                continue
            name = str(row.get("name") or "")
            quality = float(quality_map.get(name, 0.0))
            spend = float(row.get("spend") or 0)
            cpqa = (spend / quality) if quality else None
            item = {
                **row,
                "quality_conversions": round(quality, 2),
                "cpqa": round(cpqa, 2) if cpqa is not None else None,
                "classification": _classify_campaign({**row, "quality_conversions": quality, "cpqa": cpqa}),
            }
            enriched.append(item)

        search_terms = _search_terms(adapter, days)
        devices = _devices(adapter, days)
        window_key = f"{days}d"
        payload = {
            "window_days": days,
            "campaigns": sorted(
                [r for r in enriched if not r.get("error")],
                key=lambda r: r.get("spend") or 0,
                reverse=True,
            ),
            "search_terms": search_terms,
            "devices": devices,
            "totals": {
                "spend": round(sum(float(r.get("spend") or 0) for r in enriched if not r.get("error")), 2),
                "reported_conversions": round(
                    sum(float(r.get("conversions") or 0) for r in enriched if not r.get("error")), 2
                ),
                "quality_conversions": round(sum(float(r.get("quality_conversions") or 0) for r in enriched if not r.get("error")), 2),
            },
        }
        spend = payload["totals"]["spend"]
        quality_total = payload["totals"]["quality_conversions"]
        payload["totals"]["cpqa"] = round(spend / quality_total, 2) if quality_total else None
        windows_payload[window_key] = payload
        store.write_json(day_dir, f"historical_audit_{window_key}.json", payload)

    summary = {
        "quality_action_ids": quality_ids,
        "quality_action_names": quality_names,
        "windows": {k: {"totals": v["totals"], "campaign_count": len(v["campaigns"])} for k, v in windows_payload.items()},
        "note": (
            "Historical CPA may be polluted by pre-cleanup micro-conversions. "
            "Use CPQA (quality actions) for efficiency; use search terms/geo/device/assets freely from 180d."
        ),
        "artifacts": str(day_dir),
    }
    store.write_json(day_dir, "historical_audit_summary.json", summary)
    store.write_markdown(day_dir, "historical_audit.md", _markdown(summary, windows_payload))
    return {"summary": summary, "windows": windows_payload, "directory": str(day_dir)}


def _markdown(summary: dict[str, Any], windows: dict[str, Any]) -> str:
    lines = [
        "# Historical Audit (7 / 30 / 180 days)",
        "",
        summary["note"],
        "",
        f"Quality actions: {', '.join(summary.get('quality_action_names') or []) or 'none resolved'}",
        "",
    ]
    for key in ("7d", "30d", "180d"):
        block = windows.get(key) or {}
        totals = block.get("totals") or {}
        lines.extend(
            [
                f"## Window {key}",
                f"- Spend: ${totals.get('spend')}",
                f"- Reported conversions: {totals.get('reported_conversions')}",
                f"- Quality conversions: {totals.get('quality_conversions')}",
                f"- CPQA: {totals.get('cpqa')}",
                "",
                "Top campaigns by spend:",
            ]
        )
        for camp in (block.get("campaigns") or [])[:12]:
            lines.append(
                f"- {camp.get('name')}: spend=${camp.get('spend')} qual={camp.get('quality_conversions')} "
                f"CPQA={camp.get('cpqa')} class={camp.get('classification')}"
            )
        lines.append("")
    return "\n".join(lines) + "\n"
