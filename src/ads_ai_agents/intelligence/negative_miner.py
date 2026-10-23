from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ads_ai_agents.intelligence.controlled_policy import HARMFUL_NEGATIVE_PHRASES, HARMFUL_TERM_MARKERS


@dataclass
class NegativeProposal:
    term: str
    match_type: str  # PHRASE or EXACT
    action: str  # add_negative | pause_keyword | approval_pause_campaign
    confidence: float
    auto_apply: bool
    campaign_id: str
    campaign_name: str
    evidence: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    rollback_value: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def mine_negative_proposals(
    search_terms: list[dict[str, Any]],
    *,
    existing_negatives: set[str] | None = None,
    min_clicks_irrelevant: int = 2,
) -> list[NegativeProposal]:
    """High-confidence negatives / harmful term pauses from search terms + policy bank."""
    existing = {t.lower() for t in (existing_negatives or set())}
    proposals: list[NegativeProposal] = []
    seen: set[str] = set()

    # Always propose bank phrases not already present (account-level intent).
    for phrase in HARMFUL_NEGATIVE_PHRASES:
        key = phrase.lower()
        if key in existing or key in seen:
            continue
        seen.add(key)
        proposals.append(
            NegativeProposal(
                term=phrase,
                match_type="PHRASE",
                action="add_negative",
                confidence=0.95,
                auto_apply=True,
                campaign_id="",
                campaign_name="ALL_SEARCH",
                evidence={"source": "policy_negative_bank"},
                reason="Wellness Grant policy bank: clinical/crisis/jobs/info-only intent.",
                rollback_value={"action": "remove_negative", "term": phrase, "match_type": "PHRASE"},
            )
        )

    for row in search_terms:
        if row.get("error"):
            continue
        term = str(row.get("search_term") or row.get("search_term_view.search_term") or "").strip()
        if not term:
            continue
        lower = term.lower()
        clicks = int(row.get("clicks") or row.get("metrics.clicks") or 0)
        conversions = float(row.get("conversions") or row.get("metrics.conversions") or 0)
        spend = float(row.get("spend") or 0)
        if "metrics.cost_micros" in row and not spend:
            spend = float(row["metrics.cost_micros"]) / 1_000_000
        campaign_id = str(row.get("campaign_id") or row.get("campaign.id") or "")
        campaign_name = str(row.get("campaign") or row.get("campaign.name") or "")

        harmful = any(marker in lower for marker in HARMFUL_TERM_MARKERS)
        irrelevant = conversions == 0 and clicks >= min_clicks_irrelevant and (
            any(
                tok in lower
                for tok in ("pdf", "definition", "what is", "wikipedia", "reddit", "salary", "job", "course free")
            )
        )
        if not harmful and not irrelevant:
            continue
        if lower in seen or lower in existing:
            continue
        seen.add(lower)

        confidence = 0.92 if harmful else 0.8
        proposals.append(
            NegativeProposal(
                term=term,
                match_type="PHRASE" if len(term.split()) > 1 else "EXACT",
                action="add_negative",
                confidence=confidence,
                auto_apply=confidence >= 0.85,
                campaign_id=campaign_id,
                campaign_name=campaign_name or "ALL_SEARCH",
                evidence={
                    "clicks": clicks,
                    "conversions": conversions,
                    "spend": spend,
                    "harmful": harmful,
                    "irrelevant": irrelevant,
                },
                reason=(
                    "Harmful/clinical/crisis search term — exclude from Grant bidding."
                    if harmful
                    else "Non-converting info/jobs intent — high-confidence negative."
                ),
                rollback_value={"action": "remove_negative", "term": term},
            )
        )
    return proposals
