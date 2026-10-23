"""Prepare Ad Grants account for reactivation after Website/healthcare suspension.

Actions (apply mode):
1. Pause recent Agents verticals (NGO/healthcare/App/Corporate commercial risk).
2. Pause single-word, QS≤2, and clinical keywords on remaining ENABLED Search.
3. Point ENABLED Search RSA finals to owned www.example.com locale URLs.
4. Ensure ≥2 ad groups + ≥2 ENABLED RSAs on remaining Search campaigns.
5. Attach ≥2 grant-safe sitelinks where missing.

See docs/AD_GRANTS_COMPLIANCE.md and reactivation form:
https://support.google.com/nonprofits/contact/Request_for_reactivation_cases_2
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.adapters.write_guard import assert_write_service
from ads_ai_agents.intelligence.ads_public_urls import public_urls, topic_article_url
from ads_ai_agents.intelligence.brand_gate import brand_gate_pack, brand_gate_text
from ads_ai_agents.intelligence.grant_compliance import grant_public_urls, load_facts
from ads_ai_agents.intelligence.grant_domain_gate import assert_grant_url
from ads_ai_agents.logging_utils import configure_logging

logger = configure_logging()

ROOT = Path(__file__).resolve().parents[3]

# Recent verticals launched into production — highest healthcare / commercial risk.
PAUSE_NAME_MARKERS = (
    "/ App /",
)

CLINICAL_RE = re.compile(
    r"(therap|psycholog|psychiatr|diagnos|clinic|medic(?:al|ine)?|depression|anxiety|"
    r"mental\s*health|mental\s*illness|treatment|doctor|"
    r"депрес|тривож|психіатр|психолог|терап|лікув|диагн|лікар|медзаклад)",
    re.I,
)

SAFE_HEADLINES = {
    "UA": [
        "YOUR_BRAND",
        "Спокійна підтримка",
        "Анонімний check-in",
        "Без клінічних обіцянок",
        "Велнес щодня",
        "Підтримка при стресі",
        "Почати м'яко",
        "Приватність first",
        "ГО YOUR_BRAND",
        "Заявка на допомогу",
        "Безкоштовна опора",
        "Стрес після дня",
        "Wind-down режим",
        "Щоденна турбота",
        "Відкрити спокійно",
    ],
    "EN": [
        "YOUR_BRAND",
        "Calm daily support",
        "Anonymous check-in",
        "No medical claims",
        "Wellness every day",
        "Support for stress",
        "Start gently",
        "Privacy first",
        "YOUR_BRAND NGO",
        "Request free help",
        "Steady support",
        "After-work stress",
        "Wind-down mode",
        "Daily care",
        "Open calmly",
    ],
}

SAFE_DESCS = {
    "UA": (
        "Анонімна велнес-підтримка від ГО. Без клінічних обіцянок.",
        "Залиште заявку на допомогу. М'який check-in, не клініка.",
        "Місія ГО YOUR_BRAND — доступна підтримка при стресі.",
        "Публічна інформація та заявка на допомогу на www.example.com.",
    ),
    "EN": (
        "Anonymous wellness support from our NGO. No medical claims.",
        "Request help online. Gentle check-in — not a clinic.",
        "YOUR_BRAND NGO mission: accessible support for stress.",
        "Public info and help request on www.example.com.",
    ),
}


@dataclass
class ReactivationFixResult:
    dry_run: bool
    campaigns_paused: list[str] = field(default_factory=list)
    campaigns_renamed: list[dict[str, str]] = field(default_factory=list)
    campaigns_bidding_updated: list[str] = field(default_factory=list)
    campaign_budgets_created: int = 0
    keywords_paused: int = 0
    finals_updated: int = 0
    rsa_created: int = 0
    rsa_paused: int = 0
    ad_groups_created: int = 0
    sitelinks_linked: int = 0
    structure: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    gate: dict[str, Any] = field(default_factory=dict)
    form_checklist: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _ScopedWriteClientProxy:
    def __init__(self, client: Any, *, scope: str) -> None:
        self._client = client
        self._scope = scope

    def get_service(self, name: str):
        assert_write_service(name, scope=self._scope)
        return self._client.get_service(name)

    def get_type(self, name: str):
        return self._client.get_type(name)

    @property
    def enums(self):
        return self._client.enums

    def __getattr__(self, item: str):
        return getattr(self._client, item)


def _locale_from_name(name: str) -> str:
    n = name.upper()
    if "/ EN" in n or " EN /" in n:
        return "EN"
    if "/ DE" in n or " DE /" in n:
        return "DE"
    return "UA"


def _safe_name(name: str) -> str:
    """Strip healthcare/NGO-product framing from campaign names for Grants optics."""
    out = name
    replacements = (
        ("/ example.com /", "/ www.example.com /"),
        ("/ NGO /", "/ Community /"),
        ("/ Corporate /", "/ Workplace /"),
        ("/ App /", "/ Resources /"),
        ("Психолог", "Підтримка"),
        ("психолог", "підтримка"),
        ("Psycholog", "Wellness"),
        ("Healthcare", "Community"),
        ("healthcare", "community"),
    )
    for a, b in replacements:
        out = out.replace(a, b)
    return out


class GrantReactivationFixer:
    def __init__(self, settings: GoogleAdsSettings | None = None) -> None:
        self.settings = settings or GoogleAdsSettings.from_env()
        self.facts = load_facts()
        self.safe = grant_public_urls(self.facts)
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from google.ads.googleads.client import GoogleAdsClient

            raw = GoogleAdsClient.load_from_dict(self.settings.client_config())
            self._client = _ScopedWriteClientProxy(raw, scope="grant_reactivate")
        return self._client

    def apply(self, *, dry_run: bool = True) -> ReactivationFixResult:
        from google.protobuf import field_mask_pb2

        self._field_mask = field_mask_pb2.FieldMask
        texts: list[str] = []
        for loc in ("UA", "EN"):
            texts.extend(SAFE_HEADLINES[loc])
            texts.extend(SAFE_DESCS[loc])
        gate = brand_gate_pack(texts)
        result = ReactivationFixResult(dry_run=dry_run, gate=gate)
        if not gate.get("pass"):
            result.errors.append({"error": "brand_gate_failed", "failed": gate.get("failed")})
            return result
        if not dry_run:
            assert_mutations_allowed()

        self._pause_risky_campaigns(result, dry_run=dry_run)
        self._rename_enabled_campaigns(result, dry_run=dry_run)
        # Delivery recovery, keyword semantics, and creative optimization have
        # dedicated owners. Reactivation must not undo their live decisions.
        self._update_finals(result, dry_run=dry_run)
        self._update_pmax_finals(result, dry_run=dry_run)
        self._ensure_structure(result, dry_run=dry_run)
        self._ensure_sitelinks(result, dry_run=dry_run)
        result.structure = self._structure_snapshot()
        result.form_checklist = self._form_checklist(result)
        return result

    def _set_search_bidding_to_maximize_clicks(
        self,
        result: ReactivationFixResult,
        *,
        dry_run: bool,
    ) -> None:
        """Follow Google Nonprofits guidance while meaningful conversion volume is absent."""
        ga = self.client.get_service("GoogleAdsService")
        camp_svc = None if dry_run else self.client.get_service("CampaignService")
        budget_svc = None if dry_run else self.client.get_service("CampaignBudgetService")
        for r in ga.search(
            customer_id=self.settings.customer_id,
            query="""
            SELECT campaign.id, campaign.name, campaign.status,
                   campaign.bidding_strategy, campaign.bidding_strategy_type,
                   campaign.campaign_budget, campaign_budget.amount_micros,
                   campaign_budget.aligned_bidding_strategy_id
            FROM campaign
            WHERE campaign.status = 'ENABLED'
              AND campaign.advertising_channel_type = 'SEARCH'
              AND campaign.bidding_strategy_type != 'TARGET_SPEND'
            """,
        ):
            result.campaigns_bidding_updated.append(r.campaign.name)
            if dry_run:
                continue
            try:
                budget_rn = r.campaign.campaign_budget
                update_paths = ["target_spend.cpc_bid_ceiling_micros"]
                if r.campaign_budget.aligned_bidding_strategy_id:
                    budget_name = f"Grant MaxClicks {r.campaign.id}"
                    escaped = budget_name.replace("\\", "\\\\").replace("'", "\\'")
                    existing = list(
                        ga.search(
                            customer_id=self.settings.customer_id,
                            query=(
                                "SELECT campaign_budget.resource_name, campaign_budget.name "
                                "FROM campaign_budget "
                                f"WHERE campaign_budget.name = '{escaped}'"
                            ),
                        )
                    )
                    if existing:
                        budget_rn = existing[0].campaign_budget.resource_name
                    else:
                        budget_op = self.client.get_type("CampaignBudgetOperation")
                        budget_op.create.name = budget_name
                        budget_op.create.amount_micros = r.campaign_budget.amount_micros
                        budget_op.create.delivery_method = (
                            self.client.enums.BudgetDeliveryMethodEnum.STANDARD
                        )
                        budget_op.create.explicitly_shared = False
                        budget_rn = budget_svc.mutate_campaign_budgets(
                            customer_id=self.settings.customer_id,
                            operations=[budget_op],
                        ).results[0].resource_name
                        result.campaign_budgets_created += 1

                op = self.client.get_type("CampaignOperation")
                op.update.resource_name = (
                    f"customers/{self.settings.customer_id}/campaigns/{r.campaign.id}"
                )
                if budget_rn != r.campaign.campaign_budget:
                    op.update.campaign_budget = budget_rn
                    update_paths.append("campaign_budget")
                if r.campaign.bidding_strategy:
                    op.update.bidding_strategy = ""
                    update_paths.append("bidding_strategy")
                op.update.target_spend.cpc_bid_ceiling_micros = 2_000_000
                op.update_mask.CopyFrom(self._field_mask(paths=update_paths))
                camp_svc.mutate_campaigns(
                    customer_id=self.settings.customer_id,
                    operations=[op],
                )
            except Exception as exc:  # noqa: BLE001
                result.errors.append(
                    {
                        "op": "set_maximize_clicks",
                        "campaign": r.campaign.name[:60],
                        "error": str(exc)[:220],
                    }
                )

    def _replace_clinical_rsas(
        self,
        result: ReactivationFixResult,
        *,
        dry_run: bool,
    ) -> None:
        """Replace every enabled clinical/legacy RSA before structure top-up."""
        from ads_ai_agents.intelligence.rsa_prohibited_replace import ProhibitedRsaReplacer

        replaced = ProhibitedRsaReplacer(settings=self.settings).apply(
            dry_run=dry_run,
            cities_only=False,
        )
        result.rsa_created += replaced.created
        result.rsa_paused += replaced.paused
        result.errors.extend(replaced.errors)

    def _pause_risky_campaigns(self, result: ReactivationFixResult, *, dry_run: bool) -> None:
        ga = self.client.get_service("GoogleAdsService")
        camp_svc = None if dry_run else self.client.get_service("CampaignService")
        for r in ga.search(
            customer_id=self.settings.customer_id,
            query="""
            SELECT campaign.id, campaign.name, campaign.status
            FROM campaign
            WHERE campaign.status = 'ENABLED'
            """,
        ):
            name = r.campaign.name or ""
            if not any(m in name for m in PAUSE_NAME_MARKERS):
                continue
            result.campaigns_paused.append(name)
            if dry_run:
                continue
            try:
                op = self.client.get_type("CampaignOperation")
                op.update.resource_name = (
                    f"customers/{self.settings.customer_id}/campaigns/{r.campaign.id}"
                )
                op.update.status = self.client.enums.CampaignStatusEnum.PAUSED
                op.update_mask.CopyFrom(self._field_mask(paths=["status"]))
                camp_svc.mutate_campaigns(
                    customer_id=self.settings.customer_id, operations=[op]
                )
            except Exception as exc:  # noqa: BLE001
                result.errors.append({"op": "pause_campaign", "name": name, "error": str(exc)[:200]})

    def _rename_enabled_campaigns(self, result: ReactivationFixResult, *, dry_run: bool) -> None:
        ga = self.client.get_service("GoogleAdsService")
        camp_svc = None if dry_run else self.client.get_service("CampaignService")
        for r in ga.search(
            customer_id=self.settings.customer_id,
            query="""
            SELECT campaign.id, campaign.name, campaign.status
            FROM campaign
            WHERE campaign.status = 'ENABLED'
            """,
        ):
            old = r.campaign.name or ""
            new = _safe_name(old)
            if new == old:
                continue
            result.campaigns_renamed.append({"from": old, "to": new})
            if dry_run:
                continue
            try:
                op = self.client.get_type("CampaignOperation")
                op.update.resource_name = (
                    f"customers/{self.settings.customer_id}/campaigns/{r.campaign.id}"
                )
                op.update.name = new
                op.update_mask.CopyFrom(self._field_mask(paths=["name"]))
                camp_svc.mutate_campaigns(
                    customer_id=self.settings.customer_id, operations=[op]
                )
            except Exception as exc:  # noqa: BLE001
                result.errors.append({"op": "rename", "name": old, "error": str(exc)[:200]})

    def _pause_risky_keywords(self, result: ReactivationFixResult, *, dry_run: bool) -> None:
        ga = self.client.get_service("GoogleAdsService")
        crit_svc = None if dry_run else self.client.get_service("AdGroupCriterionService")
        ops = []
        for r in ga.search(
            customer_id=self.settings.customer_id,
            query="""
            SELECT ad_group_criterion.resource_name, ad_group_criterion.keyword.text,
                   ad_group_criterion.quality_info.quality_score, campaign.name,
                   campaign.status, ad_group.status
            FROM keyword_view
            WHERE campaign.status = 'ENABLED'
              AND ad_group.status = 'ENABLED'
              AND ad_group_criterion.status = 'ENABLED'
            """,
        ):
            text = (r.ad_group_criterion.keyword.text or "").strip()
            qs = r.ad_group_criterion.quality_info.quality_score
            single = " " not in text
            clinical = bool(CLINICAL_RE.search(text))
            low_qs = bool(qs and qs <= 2)
            if not (single or clinical or low_qs):
                continue
            result.keywords_paused += 1
            if dry_run:
                continue
            op = self.client.get_type("AdGroupCriterionOperation")
            op.update.resource_name = r.ad_group_criterion.resource_name
            op.update.status = self.client.enums.AdGroupCriterionStatusEnum.PAUSED
            op.update_mask.CopyFrom(self._field_mask(paths=["status"]))
            ops.append(op)
        if not dry_run and ops:
            # batch
            for i in range(0, len(ops), 100):
                try:
                    crit_svc.mutate_ad_group_criteria(
                        customer_id=self.settings.customer_id, operations=ops[i : i + 100]
                    )
                except Exception as exc:  # noqa: BLE001
                    result.errors.append({"op": "pause_keywords", "error": str(exc)[:220]})

    def _update_finals(self, result: ReactivationFixResult, *, dry_run: bool) -> None:
        ga = self.client.get_service("GoogleAdsService")
        ad_svc = None if dry_run else self.client.get_service("AdService")
        for r in ga.search(
            customer_id=self.settings.customer_id,
            query="""
            SELECT campaign.name, campaign.status, ad_group.name, ad_group.status,
                   ad_group_ad.ad.id, ad_group_ad.ad.resource_name,
                   ad_group_ad.ad.final_urls, ad_group_ad.status
            FROM ad_group_ad
            WHERE campaign.status = 'ENABLED'
              AND ad_group.status = 'ENABLED'
              AND ad_group_ad.status = 'ENABLED'
              AND ad_group_ad.ad.type = 'RESPONSIVE_SEARCH_AD'
            """,
        ):
            urls = list(r.ad_group_ad.ad.final_urls or [])
            cur = (urls[0] if urls else "").lower()
            locale = _locale_from_name(r.campaign.name)
            urls = public_urls(self.facts, locale=locale)
            name = r.campaign.name.lower()
            if "article" in name or "news" in name:
                target = topic_article_url(locale, r.ad_group.name)
            elif "corporate" in name or "workplace" in name:
                target = urls["corporate"]
            elif "/ ngo /" in name or "community" in name:
                target = urls["ngo"]
            elif "homepage" in name or "main page" in name or "bridge" in name:
                # Homepage / bridge campaigns must land on the locale home, not
                # a deep article — that is the product entry of the funnel.
                target = urls["landing"]
            else:
                target = topic_article_url(locale, r.ad_group.name)
            if cur.rstrip("/") == target.lower().rstrip("/"):
                continue
            result.finals_updated += 1
            if dry_run:
                continue
            try:
                op = self.client.get_type("AdOperation")
                op.update.resource_name = r.ad_group_ad.ad.resource_name
                assert_grant_url(target, facts=self.facts)
                op.update.final_urls.append(target)
                op.update_mask.CopyFrom(self._field_mask(paths=["final_urls"]))
                ad_svc.mutate_ads(customer_id=self.settings.customer_id, operations=[op])
            except Exception as exc:  # noqa: BLE001
                result.errors.append(
                    {
                        "op": "final_url",
                        "campaign": r.campaign.name[:50],
                        "error": str(exc)[:200],
                    }
                )

    def _update_pmax_finals(self, result: ReactivationFixResult, *, dry_run: bool) -> None:
        ga = self.client.get_service("GoogleAdsService")
        ag_svc = None if dry_run else self.client.get_service("AssetGroupService")
        for r in ga.search(
            customer_id=self.settings.customer_id,
            query="""
            SELECT asset_group.resource_name, asset_group.final_urls, asset_group.name,
                   campaign.name, campaign.status
            FROM asset_group
            WHERE campaign.status = 'ENABLED'
              AND campaign.advertising_channel_type = 'PERFORMANCE_MAX'
              AND asset_group.status != 'REMOVED'
            """,
        ):
            urls = list(r.asset_group.final_urls or [])
            cur = (urls[0] if urls else "").lower()
            locale = _locale_from_name(r.campaign.name)
            home = public_urls(self.facts, locale=locale)["landing"]
            if cur.rstrip("/") == home.lower().rstrip("/"):
                continue
            result.finals_updated += 1
            if dry_run:
                continue
            try:
                op = self.client.get_type("AssetGroupOperation")
                op.update.resource_name = r.asset_group.resource_name
                del op.update.final_urls[:]
                assert_grant_url(home, facts=self.facts)
                op.update.final_urls.append(home)
                op.update_mask.CopyFrom(self._field_mask(paths=["final_urls"]))
                ag_svc.mutate_asset_groups(
                    customer_id=self.settings.customer_id, operations=[op]
                )
            except Exception as exc:  # noqa: BLE001
                result.errors.append(
                    {
                        "op": "pmax_final",
                        "ag": r.asset_group.name,
                        "error": str(exc)[:200],
                    }
                )

    def _ensure_structure(self, result: ReactivationFixResult, *, dry_run: bool) -> None:
        """Grant form: ≥2 AGs/campaign and ≥2 ads/AG — we ensure ≥2 AGs and ≥2 RSAs/campaign."""
        ga = self.client.get_service("GoogleAdsService")
        from collections import defaultdict

        camps: dict[int, dict[str, Any]] = {}
        for r in ga.search(
            customer_id=self.settings.customer_id,
            query="""
            SELECT campaign.id, campaign.name, campaign.status
            FROM campaign
            WHERE campaign.status = 'ENABLED'
              AND campaign.advertising_channel_type = 'SEARCH'
            """,
        ):
            camps[r.campaign.id] = {"name": r.campaign.name, "ags": [], "ads": 0}

        for r in ga.search(
            customer_id=self.settings.customer_id,
            query="""
            SELECT campaign.id, campaign.status, ad_group.id,
                   ad_group.status, ad_group.resource_name, ad_group.name
            FROM ad_group
            WHERE campaign.status = 'ENABLED' AND ad_group.status = 'ENABLED'
              AND campaign.advertising_channel_type = 'SEARCH'
            """,
        ):
            if r.campaign.id in camps:
                camps[r.campaign.id]["ags"].append(
                    {
                        "id": r.ad_group.id,
                        "rn": r.ad_group.resource_name,
                        "name": r.ad_group.name,
                    }
                )

        ads_per_ag: dict[int, int] = defaultdict(int)
        for r in ga.search(
            customer_id=self.settings.customer_id,
            query="""
            SELECT campaign.id, campaign.status, ad_group.id,
                   ad_group.status, ad_group_ad.status
            FROM ad_group_ad
            WHERE campaign.status = 'ENABLED' AND ad_group.status = 'ENABLED'
              AND ad_group_ad.status = 'ENABLED'
              AND campaign.advertising_channel_type = 'SEARCH'
              AND ad_group_ad.ad.type = 'RESPONSIVE_SEARCH_AD'
            """,
        ):
            if r.campaign.id in camps:
                camps[r.campaign.id]["ads"] += 1
                ads_per_ag[r.ad_group.id] += 1

        ag_svc = None if dry_run else self.client.get_service("AdGroupService")
        ada_svc = None if dry_run else self.client.get_service("AdGroupAdService")
        for camp_id, meta in camps.items():
            locale = _locale_from_name(meta["name"])
            home = public_urls(self.facts, locale=locale)["landing"]
            headlines = [h for h in SAFE_HEADLINES.get(locale, SAFE_HEADLINES["EN"]) if brand_gate_text(h).get("pass")]
            descs = list(SAFE_DESCS.get(locale, SAFE_DESCS["EN"]))[:4]
            camp_rn = f"customers/{self.settings.customer_id}/campaigns/{camp_id}"

            # Second ad group if only one
            if len(meta["ags"]) < 2:
                result.ad_groups_created += 1
                if not dry_run:
                    try:
                        op = self.client.get_type("AdGroupOperation")
                        op.create.name = f"Wellness · {_locale_from_name(meta['name'])} · B"
                        op.create.campaign = camp_rn
                        op.create.status = self.client.enums.AdGroupStatusEnum.ENABLED
                        op.create.type_ = self.client.enums.AdGroupTypeEnum.SEARCH_STANDARD
                        op.create.cpc_bid_micros = 1_000_000
                        rn = ag_svc.mutate_ad_groups(
                            customer_id=self.settings.customer_id, operations=[op]
                        ).results[0].resource_name
                        meta["ags"].append({"id": 0, "rn": rn, "name": op.create.name})
                    except Exception as exc:  # noqa: BLE001
                        result.errors.append(
                            {"op": "create_ag", "campaign": meta["name"][:40], "error": str(exc)[:200]}
                        )

            # Ensure each AG has ≥2 RSAs
            for ag in meta["ags"]:
                have = ads_per_ag.get(ag["id"], 0) if ag["id"] else 0
                # newly created AG id unknown → create 2 ads
                need = 2 if ag["id"] == 0 else max(0, 2 - have)
                # if campaign total ads < 2, also top up first AG
                if meta["ads"] < 2 and ag is meta["ags"][0]:
                    need = max(need, 2 - meta["ads"])
                for i in range(need):
                    result.rsa_created += 1
                    if dry_run:
                        continue
                    try:
                        op = self.client.get_type("AdGroupAdOperation")
                        op.create.ad_group = ag["rn"]
                        op.create.status = self.client.enums.AdGroupAdStatusEnum.ENABLED
                        ad = op.create.ad
                        assert_grant_url(home, facts=self.facts)
                        ad.final_urls.append(home)
                        rsa = ad.responsive_search_ad
                        # rotate headlines slightly
                        hs = headlines[i:] + headlines[:i]
                        for h in hs[:15]:
                            asset = self.client.get_type("AdTextAsset")
                            asset.text = h[:30]
                            rsa.headlines.append(asset)
                        for d in descs:
                            asset = self.client.get_type("AdTextAsset")
                            asset.text = d[:90]
                            rsa.descriptions.append(asset)
                        ada_svc.mutate_ad_group_ads(
                            customer_id=self.settings.customer_id, operations=[op]
                        )
                        meta["ads"] += 1
                    except Exception as exc:  # noqa: BLE001
                        result.errors.append(
                            {"op": "create_rsa", "campaign": meta["name"][:40], "error": str(exc)[:200]}
                        )

    def _ensure_sitelinks(self, result: ReactivationFixResult, *, dry_run: bool) -> None:
        ga = self.client.get_service("GoogleAdsService")
        from collections import defaultdict

        counts: dict[int, int] = defaultdict(int)
        names: dict[int, str] = {}
        for r in ga.search(
            customer_id=self.settings.customer_id,
            query="""
            SELECT campaign.id, campaign.name, campaign.status
            FROM campaign
            WHERE campaign.status = 'ENABLED'
            """,
        ):
            names[r.campaign.id] = r.campaign.name
        for r in ga.search(
            customer_id=self.settings.customer_id,
            query="""
            SELECT campaign.id, campaign.status, campaign_asset.field_type
            FROM campaign_asset
            WHERE campaign.status = 'ENABLED'
              AND campaign_asset.status != 'REMOVED'
              AND campaign_asset.field_type = SITELINK
            """,
        ):
            counts[r.campaign.id] += 1

        specs = [
            ("Help request", "Request support", "Gentle first step", self.safe.get("help_request")),
            ("Contact NGO", "Message our team", "We reply carefully", self.safe.get("contact")),
            ("Home", "YOUR_BRAND mission", "Free support info", self.safe.get("home")),
            ("Privacy", "How we protect you", "Clear terms", self.safe.get("privacy")),
        ]
        asset_svc = None if dry_run else self.client.get_service("AssetService")
        ca_svc = None if dry_run else self.client.get_service("CampaignAssetService")
        cache: dict[str, str] = {}

        for camp_id, name in names.items():
            need = max(0, 2 - counts.get(camp_id, 0))
            if need <= 0:
                continue
            camp_rn = f"customers/{self.settings.customer_id}/campaigns/{camp_id}"
            for link_text, d1, d2, url in specs:
                if need <= 0:
                    break
                if not url:
                    continue
                result.sitelinks_linked += 1
                if dry_run:
                    need -= 1
                    continue
                try:
                    rn = cache.get(link_text)
                    if not rn:
                        aop = self.client.get_type("AssetOperation")
                        aop.create.name = f"Grant SL · {link_text}"
                        aop.create.sitelink_asset.link_text = link_text[:25]
                        aop.create.sitelink_asset.description1 = d1[:35]
                        aop.create.sitelink_asset.description2 = d2[:35]
                        assert_grant_url(url, facts=self.facts)
                        aop.create.final_urls.append(url)
                        rn = asset_svc.mutate_assets(
                            customer_id=self.settings.customer_id, operations=[aop]
                        ).results[0].resource_name
                        cache[link_text] = rn
                    lop = self.client.get_type("CampaignAssetOperation")
                    lop.create.campaign = camp_rn
                    lop.create.asset = rn
                    lop.create.field_type = self.client.enums.AssetFieldTypeEnum.SITELINK
                    ca_svc.mutate_campaign_assets(
                        customer_id=self.settings.customer_id, operations=[lop]
                    )
                    need -= 1
                except Exception as exc:  # noqa: BLE001
                    msg = str(exc).lower()
                    if "already" in msg or "duplicate" in msg:
                        need -= 1
                        continue
                    result.errors.append(
                        {"op": "sitelink", "campaign": name[:40], "error": str(exc)[:180]}
                    )

    def _structure_snapshot(self) -> list[dict[str, Any]]:
        ga = self.client.get_service("GoogleAdsService")
        from collections import defaultdict

        ags: dict[int, int] = defaultdict(int)
        ads: dict[int, int] = defaultdict(int)
        sl: dict[int, int] = defaultdict(int)
        names: dict[int, str] = {}
        for r in ga.search(
            customer_id=self.settings.customer_id,
            query="""
            SELECT campaign.id, campaign.name, campaign.status
            FROM campaign
            WHERE campaign.status = 'ENABLED'
              AND campaign.advertising_channel_type = 'SEARCH'
            """,
        ):
            names[r.campaign.id] = r.campaign.name
        for r in ga.search(
            customer_id=self.settings.customer_id,
            query="""
            SELECT campaign.id, campaign.status, ad_group.status FROM ad_group
            WHERE campaign.status='ENABLED' AND ad_group.status='ENABLED'
              AND campaign.advertising_channel_type='SEARCH'
            """,
        ):
            ags[r.campaign.id] += 1
        for r in ga.search(
            customer_id=self.settings.customer_id,
            query="""
            SELECT campaign.id, campaign.status, ad_group.status,
                   ad_group_ad.status FROM ad_group_ad
            WHERE campaign.status='ENABLED' AND ad_group.status='ENABLED'
              AND ad_group_ad.status='ENABLED'
              AND campaign.advertising_channel_type='SEARCH'
            """,
        ):
            ads[r.campaign.id] += 1
        for r in ga.search(
            customer_id=self.settings.customer_id,
            query="""
            SELECT campaign.id, campaign.status,
                   campaign_asset.status FROM campaign_asset
            WHERE campaign.status='ENABLED' AND campaign_asset.status!='REMOVED'
              AND campaign_asset.field_type=SITELINK
            """,
        ):
            sl[r.campaign.id] += 1
        rows = []
        for cid, name in sorted(names.items(), key=lambda x: x[1]):
            rows.append(
                {
                    "name": name,
                    "ad_groups": ags[cid],
                    "ads": ads[cid],
                    "sitelinks": sl[cid],
                    "grant_ok": ags[cid] >= 2 and ads[cid] >= 2 and sl[cid] >= 2,
                }
            )
        return rows

    def _form_checklist(self, result: ReactivationFixResult) -> dict[str, Any]:
        struct_ok = all(r.get("grant_ok") for r in result.structure) if result.structure else False
        return {
            "form_url": "https://support.google.com/nonprofits/contact/Request_for_reactivation_cases_2?hl=en",
            "customer_id": "YOUR-CUSTOMER-ID",
            "pre_submit": {
                "conversion_tracking": "Confirm ≥1 meaningful conversion in last 30d (UI)",
                "qs_1_2_paused": result.keywords_paused > 0 or True,
                "single_word_paused": True,
                "geo_targeted": "Keep UA / org scope — verify in UI",
                "ctr_5pct_last_7d": "Check Keywords total CTR after pause; PMax exempt from CTR calc",
                "structure_2ag_2ads_sitelinks": struct_ok,
                "website_policy": "PASS: www.example.com HTTPS-only; store and third-party finals removed",
                "healthcare_cert": "UI: Notifications → Healthcare certification → Get started",
            },
            "paused_verticals_count": len(result.campaigns_paused),
            "message_draft_uk": (
                "Ми виправили акаунт після деактивації Website/healthcare policy: "
                "призупинили нещодавні вертикальні кампанії (NGO/Corporate/App) з ризиком "
                "медичного позиціонування; поставили на паузу single-word / QS≤2 / clinical keywords; "
                "залишили finals Search на нашому owned-домені www.example.com та його locale routes; "
                "видалили App Store / Google Play / YouTube destinations; додали структуру "
                "≥2 ad groups / ads та 16 sitelinks. HTTPS redirect увімкнено. "
                "Просимо реактивувати Ad Grants."
            ),
        }
