from __future__ import annotations

from typing import Any


# Pain-language intents beat generic clinical head terms for commercial potential + policy safety.
PAIN_INTENT_SEEDS: dict[str, tuple[str, ...]] = {
    "UA": (
        "не можу заснути через роботу",
        "вигорання на роботі що робити",
        "тривога перед сном",
        "дитина не хоче в школу",
        "немає сил після роботи",
    ),
    "EN": (
        "i cannot sleep because of work anxiety",
        "i hate my job but i can't quit",
        "burned out but still functioning",
        "my child refuses school",
        "Sunday scaries anxiety",
    ),
    "DE": (
        "kann vor stress nicht schlafen",
        "burnout job aber nicht kündigen",
        "angst vor der arbeit woche",
        "kind will nicht zur schule",
    ),
}


def mine_intent_opportunities(
    search_terms: list[dict[str, Any]] | None = None,
    *,
    locales: tuple[str, ...] = ("UA", "EN", "DE"),
) -> dict[str, Any]:
    """Intent Mining: what hurts people — not which keywords to buy blindly."""
    search_terms = search_terms or []
    live_hits: list[dict[str, Any]] = []
    pain_markers = (
        "can't",
        "cannot",
        "hate my",
        "burn",
        "sleep",
        "anxiety",
        "school",
        "job",
        "не можу",
        "вигоран",
        "тривог",
        "школ",
        "schlaf",
        "angst",
    )
    for row in search_terms:
        term = str(row.get("search_term") or row.get("search_term_view.search_term") or "")
        lower = term.lower()
        if any(m in lower for m in pain_markers):
            live_hits.append(
                {
                    "term": term,
                    "spend": row.get("spend") or row.get("metrics.cost_micros"),
                    "conversions": row.get("conversions") or row.get("metrics.conversions"),
                    "campaign": row.get("campaign") or row.get("campaign.name"),
                }
            )
        if len(live_hits) >= 40:
            break

    return {
        "agent": "intent_mining",
        "question": "What hurts people right now?",
        "not_the_question": "Which high-volume keywords should we buy?",
        "seed_pain_intents": {loc: list(PAIN_INTENT_SEEDS.get(loc, ())) for loc in locales},
        "live_search_term_pain_hits": live_hits,
        "product_scenarios": [
            "AI Burnout companion (work stress → daily return loop)",
            "AI Sleep anxiety wind-down (non-clinical)",
            "AI Parent support (school refusal / family stress) — careful policy framing",
            "AI Sunday scaries / week-start calm",
        ],
        "next_data": [
            "Reddit/forum corpora",
            "App review dumps",
            "Support tickets / chat transcripts (anonymized)",
            "180d search terms export",
        ],
        "status": "skeleton_plus_live_ads_term_overlap",
    }
