from __future__ import annotations

from typing import Any

from ads_ai_agents.agents.assets import AssetAgent
from ads_ai_agents.agents.competitors import CompetitorAgent
from ads_ai_agents.agents.keywords import DEFAULT_NEGATIVE_BANK, KeywordAgent
from ads_ai_agents.core.domain import (
    BusinessGoal,
    CampaignDraft,
    CompetitorWatchlist,
    GrantEconomics,
    IntentCluster,
    Portfolio,
)


LOCALE_SEEDS: dict[str, dict[str, Any]] = {
    "UA": {
        "market": "Ukraine",
        "locale": "ua",
        "intent_clusters": [
            IntentCluster(
                name="anxiety_stress_ua",
                jobs_to_be_done=("Потрібна емоційна підтримка при тривозі/стресі",),
                safe_language=("емоційна підтримка", "самодопомога", "спокійні рутини"),
                prohibited_language=("діагноз", "психіатр", "ліки"),
                locales=("ua",),
                seed_keywords=(
                    "тривога допомога",
                    "стрес як впоратися",
                    "вигорання підтримка",
                    "емоційна підтримка онлайн",
                ),
                negative_themes=("ліки", "психіатр", "суїцид", "робота вакансії"),
            ),
            IntentCluster(
                name="content_articles_ua",
                jobs_to_be_done=("Знайти корисні статті про стрес і вигорання",),
                safe_language=("статті про стрес", "самодопомога"),
                prohibited_language=("pdf відповіді", "реферат"),
                locales=("ua",),
                seed_keywords=(
                    "статті про стрес",
                    "як заспокоїти тривогу",
                    "самодопомога емоції",
                ),
                negative_themes=("pdf", "реферат", "тест відповіді"),
            ),
        ],
        "competitors": CompetitorWatchlist(
            brands=("BetterHelp", "Talkspace", "Wysa"),
            domains=("betterhelp.com", "talkspace.com"),
            notes="UA: local clinics + telegram wellbeing channels; compete on JTBD not clinical claims.",
        ),
    },
    "EN": {
        "market": "English soft-geo / diaspora",
        "locale": "en",
        "intent_clusters": [
            IntentCluster(
                name="anxiety_stress_en",
                jobs_to_be_done=("I need anonymous daily emotional support for stress/anxiety",),
                safe_language=("wellbeing support", "self-help routines", "anonymous chat"),
                prohibited_language=("psychiatrist", "medication", "diagnosis"),
                locales=("en",),
                seed_keywords=(
                    "anxiety self help",
                    "stress support chat",
                    "burnout emotional support",
                    "online wellbeing companion",
                    "YOUR_BRAND",
                ),
                negative_themes=("psychiatrist", "medication", "suicide hotline", "jobs"),
            ),
            IntentCluster(
                name="content_articles_en",
                jobs_to_be_done=("I want practical articles on anxiety and burnout",),
                safe_language=("how to manage anxiety", "burnout recovery tips"),
                prohibited_language=("definition", "pdf", "wikipedia"),
                locales=("en",),
                seed_keywords=(
                    "how to manage anxiety",
                    "burnout recovery tips",
                    "daily calm routine",
                ),
                negative_themes=("pdf", "definition", "wikipedia"),
            ),
        ],
        "competitors": CompetitorWatchlist(
            brands=("BetterHelp", "Calm", "Headspace", "Woebot", "Youper"),
            domains=("betterhelp.com", "calm.com", "headspace.com", "woebothealth.com"),
            notes="Compete on stress/burnout JTBD; never mirror clinical therapy claims.",
        ),
    },
    "DE": {
        "market": "DACH",
        "locale": "de",
        "intent_clusters": [
            IntentCluster(
                name="anxiety_stress_de",
                jobs_to_be_done=("Ich brauche emotionale Unterstützung bei Stress/Angst",),
                safe_language=("Selbsthilfe", "emotionale Unterstützung", "Burnout-Prävention"),
                prohibited_language=("Psychiater", "Medikamente", "Diagnose"),
                locales=("de",),
                seed_keywords=(
                    "angst selbsthilfe",
                    "stress bewältigen",
                    "burnout unterstützung",
                    "emotionale unterstützung online",
                ),
                negative_themes=("psychiater", "medikamente", "suizid", "jobs"),
            ),
        ],
        "competitors": CompetitorWatchlist(
            brands=("MindDoc", "Selfapy", "BetterHelp"),
            domains=("minddoc.com", "selfapy.de", "betterhelp.com"),
            notes="DE only with DE landing + conversion architecture; otherwise keep paused/limited.",
        ),
    },
}


def build_locale_strategy_pack(
    *,
    search_terms: list[dict[str, Any]] | None = None,
    locales: tuple[str, ...] = ("UA", "DE", "EN"),
) -> dict[str, Any]:
    """Competitor + keyword + asset strategy drafts per locale (publish requires approval)."""
    keyword_agent = KeywordAgent()
    competitor_agent = CompetitorAgent()
    asset_agent = AssetAgent()
    search_terms = search_terms or []

    locale_reports: dict[str, Any] = {}
    for locale in locales:
        cfg = LOCALE_SEEDS[locale]
        draft = CampaignDraft(
            name=f"GRANT_SEARCH_{locale}_CORE",
            portfolio=Portfolio.GRANT_SEARCH,
            market=cfg["market"],
            locale=cfg["locale"],
            business_goal=BusinessGoal(
                name="qualified_activation",
                primary_conversion="chat_activation_or_form",
                target_cpa_usd=2.0,
            ),
            intent_clusters=tuple(cfg["intent_clusters"]),
            daily_budget_eur=50.0,
            bidding_strategy="maximize_conversions",
            negative_keywords=tuple(DEFAULT_NEGATIVE_BANK),
            brand_exclusions=tuple(cfg["competitors"].brands),
            grant=GrantEconomics(),
            competitors=cfg["competitors"],
        )
        kw = keyword_agent.recommend(draft)
        comp = competitor_agent.recommend(draft)
        assets = asset_agent.recommend(draft)

        evidence_terms = []
        for row in search_terms:
            term = str(row.get("search_term") or "").lower()
            if not term:
                continue
            if locale == "UA" and any(ch in term for ch in "іїєґ"):
                evidence_terms.append(row)
            elif locale == "DE" and any(tok in term for tok in ("angst", "stress", "burnout", "hilfe")):
                evidence_terms.append(row)
            elif locale == "EN" and any(tok in term for tok in ("anxiety", "stress", "burnout", "help")):
                evidence_terms.append(row)
            if len(evidence_terms) >= 25:
                break

        locale_reports[locale] = {
            "locale": locale,
            "market": cfg["market"],
            "language": cfg["locale"],
            "seed_keywords": [
                {
                    "cluster": c.name,
                    "seeds": list(c.seed_keywords),
                    "negatives": list(c.negative_themes),
                }
                for c in cfg["intent_clusters"]
            ],
            "competitor_watchlist": {
                "brands": list(cfg["competitors"].brands),
                "domains": list(cfg["competitors"].domains),
                "notes": cfg["competitors"].notes,
            },
            "keyword_agent": {
                "title": kw.title,
                "actions": list(kw.actions),
                "auto_apply_safe": kw.auto_apply_safe,
                "requires_approval": kw.requires_approval,
            },
            "competitor_agent": {
                "title": comp.title,
                "actions": list(comp.actions),
                "requires_approval": True,
            },
            "asset_agent": {
                "title": assets.title,
                "actions": list(assets.actions),
                "generate": "auto_draft",
                "publish": "approval_required",
            },
            "live_search_term_evidence": evidence_terms[:25],
            "structure_recommendation": {
                "UA": "GRANT_SEARCH_UA_CORE + GRANT_SEARCH_UA_CONTENT",
                "EN": "GRANT_SEARCH_EN_CORE + GRANT_SEARCH_EN_CONTENT",
                "DE": "GRANT_SEARCH_DE_CORE (only with DE landing + conversion path)",
            }.get(locale),
        }

    return {
        "mode": "strategy_draft",
        "publish_assets": "approval_required",
        "add_positive_keywords": "approval_required",
        "locales": locale_reports,
        "north_star": (
            "Move from zero paying leads → qualified activations then Purchase via clean CPQA bidding."
        ),
        "note": (
            "Competitor/asset/keyword packs are drafts for human approval. "
            "CONTROLLED_LIVE auto-applies only high-confidence negatives and ≤10% budget tilts."
        ),
    }
