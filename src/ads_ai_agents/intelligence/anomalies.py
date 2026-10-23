from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class Anomaly:
    code: str
    severity: str
    entity: str
    message: str
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def detect_anomalies(
    *,
    campaigns: list[dict[str, Any]],
    search_terms: list[dict[str, Any]],
    conversion_report: dict[str, Any],
    spend_mtd: float,
    grant_monthly: float = 10_000.0,
) -> list[Anomaly]:
    anomalies: list[Anomaly] = []

    if spend_mtd > grant_monthly * 1.05:
        anomalies.append(
            Anomaly(
                code="pacing.over_grant",
                severity="high",
                entity="account",
                message=f"MTD spend ${spend_mtd:.0f} exceeds monthly grant ${grant_monthly:.0f}.",
                evidence={"spend_mtd": spend_mtd, "grant_monthly": grant_monthly},
            )
        )

    day_of_month = max(1, __import__("datetime").date.today().day)
    expected_pace = grant_monthly * (day_of_month / 30)
    if spend_mtd < expected_pace * 0.55:
        anomalies.append(
            Anomaly(
                code="pacing.under_delivery",
                severity="medium",
                entity="account",
                message=f"MTD spend ${spend_mtd:.0f} is well under expected pace ~${expected_pace:.0f}.",
                evidence={"spend_mtd": spend_mtd, "expected_pace": expected_pace},
            )
        )

    for campaign in campaigns:
        if campaign.get("error"):
            continue
        name = str(campaign.get("name") or campaign.get("campaign.name") or "")
        cost = float(campaign.get("cost") or 0)
        if "metrics.cost_micros" in campaign and not cost:
            cost = float(campaign["metrics.cost_micros"]) / 1_000_000
        conversions = float(campaign.get("conversions") or campaign.get("metrics.conversions") or 0)
        cpa = campaign.get("cpa")
        if cpa is None and conversions > 0:
            cpa = cost / conversions
        channel = str(campaign.get("channel") or campaign.get("campaign.advertising_channel_type") or "")
        if cost > 200 and conversions == 0:
            anomalies.append(
                Anomaly(
                    code="campaign.zero_conversion_spend",
                    severity="high",
                    entity=name,
                    message=f"{channel or 'Campaign'} spent ${cost:.0f} with 0 reported conversions.",
                    evidence={"cost": cost, "conversions": conversions, "channel": channel},
                )
            )
        if cpa is not None and cpa > 6:
            anomalies.append(
                Anomaly(
                    code="campaign.high_reported_cpa",
                    severity="medium",
                    entity=name,
                    message=f"Reported CPA ${cpa:.2f} is elevated vs $2 north-star (quality still unverified).",
                    evidence={"cpa": cpa, "cost": cost, "conversions": conversions},
                )
            )

    risky_terms = ("suicide", "psychiatrist", "diagnosis", "therapy near me", "depression treatment")
    for term_row in search_terms:
        term = str(term_row.get("search_term_view.search_term") or term_row.get("search_term") or "").lower()
        for marker in risky_terms:
            if marker in term:
                anomalies.append(
                    Anomaly(
                        code="search_term.policy_risk",
                        severity="high",
                        entity=term,
                        message=f"Search term may conflict with wellness-safe positioning: '{term}'.",
                        evidence=term_row,
                    )
                )
                break

    if conversion_report.get("micro_primaries"):
        anomalies.append(
            Anomaly(
                code="conversion.micro_primary",
                severity="critical",
                entity="conversion_actions",
                message="Micro conversions are primary_for_goal — bidding may optimize to inflated events.",
                evidence={"micro_primaries": conversion_report.get("micro_primaries")},
            )
        )

    return anomalies
