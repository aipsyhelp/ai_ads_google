from __future__ import annotations

from ads_ai_agents.core.domain import (
    AgentRecommendation,
    CampaignDraft,
    Portfolio,
    RiskLevel,
    WorkflowStage,
)


class AssetAgent:
    name = "asset"
    stage = WorkflowStage.CREATE

    def recommend(self, campaign: CampaignDraft) -> AgentRecommendation:
        actions: list[str] = [
            "Produce RSA matrix: ≥2 ads/ad group, aim Good/Excellent Ad Strength, do not churn winners weekly.",
            "Copy rules: wellbeing/self-help/anonymous support only — never diagnosis, therapy, cure claims.",
            "Creative brief pack per JTBD: headlines, descriptions, image orientations, LP message match.",
        ]

        if not campaign.asset_specs:
            actions.append("No asset_specs yet — generate first EN pack for each intent cluster before launch.")
        else:
            for spec in campaign.asset_specs:
                headline_n = len(spec.headlines)
                desc_n = len(spec.descriptions)
                actions.append(
                    f"Review '{spec.cluster}/{spec.locale}': {headline_n} headlines, {desc_n} descriptions, LP={spec.landing_page_url or 'missing'}."
                )
                if headline_n < 8:
                    actions.append(f"Expand headlines for '{spec.cluster}/{spec.locale}' toward 10–15 unique angles.")
                if desc_n < 2:
                    actions.append(f"Add ≥2 descriptions for '{spec.cluster}/{spec.locale}'.")

        for cluster in campaign.intent_clusters:
            safe = ", ".join(cluster.safe_language[:4])
            banned = ", ".join(cluster.prohibited_language[:4])
            actions.append(f"Cluster '{cluster.name}' lexicon OK=[{safe}] NO=[{banned}].")

        if campaign.portfolio is Portfolio.PERFORMANCE_MAX:
            actions.extend(
                [
                    "PMax asset groups by JTBD, not country-only.",
                    "Image set: 1.91:1, 1:1, 4:5 + square/horizontal logos; decide auto-video vs branded video.",
                ]
            )
        if campaign.portfolio is Portfolio.AI_MAX_SEARCH:
            actions.append("Ship text customization guidelines and final URL expansion exclusions.")

        from ads_ai_agents.platform.product_facts import organization_profile

        org = organization_profile()
        if org["main_offering"]:
            kind = "cause/program" if org["type"] == "nonprofit" else "product/service"
            actions.insert(0, f"Main offering to advertise ({kind}): {org['main_offering']}.")
        if org["description"]:
            actions.insert(1 if org["main_offering"] else 0, f"Message source (your description): {org['description'][:200]}")
        if org["seed_terms"]:
            actions.append("Seed headline themes from your offering: " + ", ".join(org["seed_terms"][:6]) + ".")

        actions.append("Localize UA/EN/DE/PL only when LP + legal/disclaimer parity exists.")
        actions.append("Feed competitor creative angles into counter-messaging without trademark misuse.")

        risk = RiskLevel.MEDIUM if not campaign.asset_specs else RiskLevel.LOW
        return AgentRecommendation(
            agent=self.name,
            stage=self.stage,
            title="Asset & creative production",
            rationale="Creative systems win when JTBD packs, policy-safe language, and Ad Strength discipline ship together.",
            actions=tuple(actions),
            risk=risk,
            requires_approval=True,
            priority=30,
        )
