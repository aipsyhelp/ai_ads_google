from __future__ import annotations

from typing import Any

from ads_ai_agents.adapters.google_ads_readonly import GoogleAdsReadOnlyAdapter, QueryResult
from ads_ai_agents.intelligence.anomalies import detect_anomalies
from ads_ai_agents.intelligence.conversion_audit import audit_conversion_actions
from ads_ai_agents.intelligence.recommendation_firewall import evaluate_recommendations
from ads_ai_agents.intelligence.report_store import ReportStore
from ads_ai_agents.intelligence.shadow_budget import plan_shadow_reallocation
from ads_ai_agents.logging_utils import configure_logging

logger = configure_logging()


POLICY_COPY_MARKERS = (
    "diagnose",
    "diagnosis",
    "therapy",
    "treatment",
    "cure",
    "suicidal",
    "mental illness",
)


def run_full_audit(
    adapter: GoogleAdsReadOnlyAdapter | None = None,
    store: ReportStore | None = None,
) -> dict[str, Any]:
    adapter = adapter or GoogleAdsReadOnlyAdapter()
    store = store or ReportStore()
    day_dir = store.day_dir(kind="audit")
    logger.info("Starting full account audit into %s", day_dir)

    meta = adapter.ping()
    bundle = adapter.fetch_audit_bundle()
    errors = {name: result.error for name, result in bundle.items() if not result.ok}

    campaigns = _normalize_campaigns(bundle.get("campaigns"))
    search_terms = bundle["search_terms"].rows if bundle.get("search_terms") and bundle["search_terms"].ok else []
    # Prefer merged conversion actions (metadata + MTD metrics).
    conversion_rows = adapter.list_conversion_actions()
    if conversion_rows and conversion_rows[0].get("error"):
        conversion_rows = (
            bundle["conversions"].rows if bundle.get("conversions") and bundle["conversions"].ok else []
        )
    recommendations = (
        bundle["recommendations"].rows
        if bundle.get("recommendations") and bundle["recommendations"].ok
        else []
    )
    change_events = (
        bundle["change_events"].rows if bundle.get("change_events") and bundle["change_events"].ok else []
    )

    spend_mtd = _spend_from_account(bundle.get("account"), campaigns)
    conversion_report = audit_conversion_actions(conversion_rows, spend_mtd=spend_mtd).to_dict()
    evaluated_recs = [item.to_dict() for item in evaluate_recommendations(recommendations)]
    anomalies = [
        item.to_dict()
        for item in detect_anomalies(
            campaigns=campaigns,
            search_terms=search_terms,
            conversion_report=conversion_report,
            spend_mtd=spend_mtd,
        )
    ]
    policy_risks = _policy_risks(search_terms, bundle.get("ads"))
    shadow = [
        item.to_dict()
        for item in plan_shadow_reallocation(
            campaigns,
            conversion_tracking_healthy=not conversion_report.get("micro_primaries"),
            observation_days=14,
            data_fresh=True,
        )
    ]

    account_payload = {
        "meta": meta,
        "generated_at": store.stamp(),
        "query_errors": errors,
        "account_rows": bundle["account"].rows if bundle.get("account") else [],
    }

    # Persist artifacts
    store.write_json(day_dir, "account.json", account_payload)
    store.write_csv(day_dir, "campaigns.csv", _rows(bundle.get("campaigns")))
    store.write_csv(day_dir, "ad_groups.csv", _rows(bundle.get("ad_groups")))
    store.write_csv(day_dir, "keywords.csv", _rows(bundle.get("keywords")))
    store.write_csv(day_dir, "search_terms.csv", _rows(bundle.get("search_terms")))
    store.write_csv(day_dir, "ads.csv", _rows(bundle.get("ads")))
    store.write_csv(day_dir, "assets.csv", _rows(bundle.get("assets")))
    store.write_csv(day_dir, "asset_groups.csv", _rows(bundle.get("asset_groups")))
    store.write_csv(day_dir, "conversions.csv", conversion_report["actions"])
    store.write_json(day_dir, "conversions_integrity.json", conversion_report)
    store.write_json(day_dir, "recommendations.json", evaluated_recs)
    store.write_json(day_dir, "change_events.json", change_events)
    store.write_json(day_dir, "risks.json", {"anomalies": anomalies, "policy_risks": policy_risks})
    store.write_json(day_dir, "shadow_budget.json", shadow)

    winners, losers = _winners_losers(campaigns)
    executive = {
        "account": meta.get("descriptive_name"),
        "customer_id": meta.get("customer_id"),
        "generated_at": store.stamp(),
        "terminology": conversion_report["terminology"],
        "reported_conversions_mtd": conversion_report["reported_conversions_mtd"],
        "spend_mtd": spend_mtd,
        "cpa_reported_conversions": conversion_report["kpis"].get("cpa_reported_conversions"),
        "cpqa": conversion_report["kpis"].get("cpqa"),
        "cac": conversion_report["kpis"].get("cac"),
        "primary_actions": conversion_report["primary_actions"],
        "micro_primaries": conversion_report["micro_primaries"],
        "campaign_count": len(campaigns),
        "winners_preliminary": winners,
        "losers_preliminary": losers,
        "anomaly_count": len(anomalies),
        "policy_risk_count": len(policy_risks),
        "shadow_budget_proposals": shadow,
        "query_errors": errors,
        "narrative": conversion_report["narrative"]
        + [
            "No Google Ads mutate operations were executed.",
            "Budget proposals are shadow-mode only and require human approval after conversion integrity proof.",
        ],
    }
    store.write_json(day_dir, "executive_summary.json", executive)
    store.write_markdown(day_dir, "executive_summary.md", _executive_markdown(executive))

    logger.info(
        "Audit complete: %s campaigns, %.0f reported conversions, %s anomalies",
        len(campaigns),
        conversion_report["reported_conversions_mtd"],
        len(anomalies),
    )
    return {"directory": str(day_dir), "executive": executive, "conversion_report": conversion_report}


def _rows(result: QueryResult | None) -> list[dict[str, Any]]:
    if result is None or not result.ok:
        return []
    return result.rows


def _normalize_campaigns(result: QueryResult | None) -> list[dict[str, Any]]:
    rows = _rows(result)
    out = []
    for row in rows:
        cost = float(row.get("metrics.cost_micros") or 0) / 1_000_000
        conversions = float(row.get("metrics.conversions") or 0)
        out.append(
            {
                "id": str(row.get("campaign.id") or ""),
                "name": row.get("campaign.name"),
                "status": row.get("campaign.status"),
                "channel": row.get("campaign.advertising_channel_type"),
                "bidding": row.get("campaign.bidding_strategy_type"),
                "daily_budget": float(row.get("campaign_budget.amount_micros") or 0) / 1_000_000,
                "cost": round(cost, 2),
                "conversions": round(conversions, 2),
                "cpa": round(cost / conversions, 2) if conversions else None,
                "search_impression_share": row.get("metrics.search_impression_share"),
                "lost_is_budget": row.get("metrics.search_budget_lost_impression_share"),
                "lost_is_rank": row.get("metrics.search_rank_lost_impression_share"),
                "optimization_score": row.get("campaign.optimization_score"),
            }
        )
    return out


def _spend_from_account(account: QueryResult | None, campaigns: list[dict[str, Any]]) -> float:
    if account and account.ok and account.rows:
        micros = account.rows[0].get("metrics.cost_micros")
        if micros is not None:
            return round(float(micros) / 1_000_000, 2)
    return round(sum(float(c.get("cost") or 0) for c in campaigns), 2)


def _winners_losers(campaigns: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    ranked = [c for c in campaigns if c.get("cpa") is not None and (c.get("conversions") or 0) > 0]
    winners = sorted(ranked, key=lambda c: c["cpa"])[:5]
    losers = sorted(ranked, key=lambda c: c["cpa"], reverse=True)[:5]
    # also include zero-conversion spenders as losers
    zero = sorted(
        [c for c in campaigns if (c.get("conversions") or 0) == 0 and (c.get("cost") or 0) > 50],
        key=lambda c: c.get("cost") or 0,
        reverse=True,
    )[:5]
    return winners, (zero + losers)[:5]


def _policy_risks(search_terms: list[dict[str, Any]], ads: QueryResult | None) -> list[dict[str, Any]]:
    risks = []
    for row in search_terms:
        term = str(row.get("search_term_view.search_term") or "").lower()
        for marker in POLICY_COPY_MARKERS:
            if marker in term:
                risks.append({"type": "search_term", "marker": marker, "term": term})
                break
    for row in _rows(ads):
        blob = " ".join(
            str(x)
            for x in (
                row.get("ad_group_ad.ad.responsive_search_ad.headlines"),
                row.get("ad_group_ad.ad.responsive_search_ad.descriptions"),
            )
            if x
        ).lower()
        for marker in POLICY_COPY_MARKERS:
            if marker in blob:
                risks.append(
                    {
                        "type": "ad_copy",
                        "marker": marker,
                        "ad_id": row.get("ad_group_ad.ad.id"),
                    }
                )
                break
    return risks


def _executive_markdown(executive: dict[str, Any]) -> str:
    lines = [
        f"# Executive Audit — {executive.get('account')}",
        "",
        f"- Customer: `{executive.get('customer_id')}`",
        f"- Generated: {executive.get('generated_at')}",
        f"- Spend MTD: ${executive.get('spend_mtd')}",
        f"- Reported conversions MTD: {executive.get('reported_conversions_mtd')}",
        f"- CPA (reported conversions): {executive.get('cpa_reported_conversions')}",
        f"- CPQA: {executive.get('cpqa')}",
        f"- CAC: {executive.get('cac')}",
        "",
        "## Terminology",
        f"- Say: **{executive['terminology']['say_instead']}**",
        f"- Do not say: {executive['terminology']['do_not_say']}",
        "",
        "## Primary conversion actions",
    ]
    for name in executive.get("primary_actions") or []:
        lines.append(f"- {name}")
    if executive.get("micro_primaries"):
        lines.append("")
        lines.append("## CRITICAL micro primaries")
        for name in executive["micro_primaries"]:
            lines.append(f"- {name}")
    lines.extend(["", "## Preliminary winners (quality unverified)"])
    for item in executive.get("winners_preliminary") or []:
        lines.append(f"- {item.get('name')} CPA={item.get('cpa')} conv={item.get('conversions')}")
    lines.extend(["", "## Preliminary losers / diagnosis queue"])
    for item in executive.get("losers_preliminary") or []:
        lines.append(f"- {item.get('name')} CPA={item.get('cpa')} cost={item.get('cost')}")
    lines.extend(["", "## Narrative"])
    for item in executive.get("narrative") or []:
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"
