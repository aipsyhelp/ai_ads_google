from __future__ import annotations

from ads_ai_agents.core.domain import (
    AgentRecommendation,
    CampaignDraft,
    RiskLevel,
    WorkflowStage,
)


# Symptom-like demand that must be translated into safe ad language / negatives.
SYMPTOM_QUERY_BRIDGES = (
    ("depression", "daily emotional support", True),
    ("anxiety", "calm daily routine", True),
    ("panic attack", "grounding self-help", True),
    ("therapy online", "anonymous wellbeing support", True),
    ("suicide", None, True),
    ("psychiatrist", None, True),
)

DEFAULT_NEGATIVE_BANK = (
    "suicide",
    "suicidal",
    "hotline",
    "emergency",
    "psychiatrist",
    "diagnosis",
    "medication",
    "prescription",
    "clinical trial",
    "free therapy near me",
    "crisis intervention",
    "jobs",
    "salary",
    "hiring",
    "pdf",
    "reddit",
)


class KeywordAgent:
    name = "keywords"
    stage = WorkflowStage.STRUCTURE

    def recommend(self, campaign: CampaignDraft) -> AgentRecommendation:
        actions: list[str] = [
            "Daily search-terms review: promote converters to exact/phrase; add non-converters as negatives.",
            "Map symptom queries → safe wellness messaging; never mirror clinical wording in ads/LP claims.",
        ]

        for query, safe_bridge, as_negative in SYMPTOM_QUERY_BRIDGES:
            if as_negative and safe_bridge is None:
                actions.append(f"Negative theme '{query}*' (crisis/clinical) — shared list, all campaigns.")
            elif safe_bridge:
                actions.append(
                    f"Intent bridge: '{query}' demand → advertise '{safe_bridge}' + LP wellbeing framing; "
                    f"negative out pure clinical/crisis variants."
                )

        for cluster in campaign.intent_clusters:
            if cluster.seed_keywords:
                actions.append(
                    f"Cluster '{cluster.name}' seeds: " + ", ".join(f'"{kw}"' for kw in cluster.seed_keywords[:6])
                )
            for theme in cluster.negative_themes:
                actions.append(f"Cluster negative theme '{cluster.name}': {theme}")

        existing = {item.lower() for item in campaign.negative_keywords}
        missing = [term for term in DEFAULT_NEGATIVE_BANK if term not in existing]
        if missing:
            actions.append("Add shared negatives: " + ", ".join(missing[:12]) + ("…" if len(missing) > 12 else ""))
        else:
            actions.append("Shared negative bank looks complete; refresh from Search Terms Insights daily.")

        if campaign.brand_exclusions:
            actions.append(
                "Brand exclusions / competitor terms: " + ", ".join(campaign.brand_exclusions[:10]) + "."
            )

        risk = RiskLevel.HIGH if len(campaign.negative_keywords) < 5 else RiskLevel.MEDIUM
        return AgentRecommendation(
            agent=self.name,
            stage=self.stage,
            title="Search terms, keywords & negatives",
            rationale="Query hygiene is the main lever for Grant efficiency at ~$2 CPA.",
            actions=tuple(actions),
            risk=risk,
            auto_apply_safe=True,
            requires_approval=False,
            priority=25,
        )
