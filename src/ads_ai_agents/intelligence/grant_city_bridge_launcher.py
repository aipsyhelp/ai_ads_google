"""Create Grant-safe city Search bridge campaigns on Smart Bidding.

Purpose: serve geo-local traffic to owned www.example.com content.

Bidding: Ad Grants caps CPC at $2 on every strategy except Smart Bidding, and
these auctions clear at $6-12. Bridges launched on Maximize Clicks recorded zero
impressions for their whole lifetime, so they are created on Maximize Conversions.

Hard constraints (Ad Grants NGO):
- Search only, Google Search network only
- MAXIMIZE_CONVERSIONS, no CPC ceiling
- Phrase keywords only (no Broad, no single-word)
- ≥2 ad groups, ≥2 RSAs, ≥2 sitelinks (we ship 16)
- Finals / sitelinks only on approved www.example.com URLs
- Brand-gated wellness copy; no clinical / commercial claims
- City-level geo + matching language
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from google.protobuf import field_mask_pb2

from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
from ads_ai_agents.adapters.google_ads_budgets import GoogleAdsBudgetWriter
from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.adapters.write_guard import assert_write_service
from ads_ai_agents.intelligence.ads_public_urls import locale_path, topic_article_url
from ads_ai_agents.intelligence.brand_gate import brand_gate_pack, brand_gate_text
from ads_ai_agents.intelligence.controlled_policy import HARMFUL_NEGATIVE_PHRASES
from ads_ai_agents.intelligence.enterprise_asset_coverage import (
    _build_sitelink_specs,
    _clip,
    _rsa_hooks,
    _rsa_topic_hooks,
)
from ads_ai_agents.intelligence.grant_domain_gate import assert_grant_url
from ads_ai_agents.logging_utils import configure_logging
from ads_ai_agents.platform.product_facts import load_product_facts

logger = configure_logging()

GRANT_DAILY_CAP = 329.0

CITY_BRIDGES: tuple[dict[str, Any], ...] = (
    {
        "key": "city_a",
        "name": "Ser / www.example.com / Bridge MaxClicks / UA / City_A / Agents",
        "locale": "UA",
        "city_label": "City_A",
        "budget_usd": 15.0,
        "geo_ids": ("1012852",),  # example city geo target constant — set your own
        "language": "languageConstants/1036",
        "path2": "city",
        "pause_existing": (
            "Ser / www.example.com / Home Page / UA / City_A / Brand_Search",
        ),
        "ad_groups": (
            {
                "name": "вигорання на роботі",
                "keywords": (
                    "вигорання на роботі",
                    "ознаки вигорання",
                    "професійне вигорання що робити",
                    "вигорання після зміни",
                    "втома після роботи що робити",
                ),
            },
            {
                "name": "стрес після роботи",
                "keywords": (
                    "стрес після роботи",
                    "як впоратися зі стресом",
                    "стрес на роботі що робити",
                    "напруга після робочого дня",
                    "спокій після важкого дня",
                ),
            },
            {
                "name": "спокійні практики щодня",
                "keywords": (
                    "спокійні практики щодня",
                    "тривога перед сном",
                    "як заспокоїтись ввечері",
                    "дихальні практики для спокою",
                    "м'який вечірній ритуал",
                ),
            },
        ),
        "extra_negatives": (
            "психолог",
            "психіатр",
            "психотерапія",
            "ліки",
            "діагноз",
            "суїцид",
            "безкоштовний психолог",
            "вакансія",
            "зарплата",
            "курси",
            "pdf",
        ),
        "descriptions": (
            "Щоденна велнес-підтримка від ГО YOUR_BRAND. Без клінічних обіцянок.",
            "Анонімні матеріали про стрес і вигорання у вашому місті.",
            "Спокійні практики, check-in і корисні статті на www.example.com.",
            "Місія ГО: доступна підтримка при стресі. Не клініка.",
        ),
    },
    {
        "key": "berlin",
        "name": "Ser / www.example.com / Bridge MaxClicks / DE / Berlin / Agents",
        "locale": "DE",
        "city_label": "Berlin",
        "budget_usd": 15.0,
        "geo_ids": ("1003854",),  # Berlin city
        "language": "languageConstants/1001",
        "path2": "berlin",
        "pause_existing": (),
        "ad_groups": (
            {
                "name": "Balance nach der Arbeit",
                "keywords": (
                    "balance nach der arbeit",
                    "pause nach dem arbeitstag",
                    "erholung nach der arbeit",
                    "feierabend entspannung tipps",
                    "ruhe nach einem schweren tag",
                ),
            },
            {
                "name": "Stress nach der Arbeit",
                "keywords": (
                    "stress nach der arbeit",
                    "mit stress umgehen alltag",
                    "arbeitsstress was tun",
                    "anspannung nach dem arbeitstag",
                    "stress am feierabend",
                ),
            },
            {
                "name": "Ruhige Alltagspraktiken",
                "keywords": (
                    "ruhige alltagspraktiken",
                    "abendliche ruhe finden",
                    "abendliche entspannung tipps",
                    "atemuebungen fuer ruhe",
                    "sanfte abendroutine stress",
                ),
            },
        ),
        "extra_negatives": (
            "psychologe",
            "psychiater",
            "psychotherapie",
            "medikamente",
            "diagnose",
            "suizid",
            "notfall",
            "stellenanzeige",
            "gehalt",
            "kurs",
            "pdf",
            "angst",
            "burnout",
            "depression",
        ),
        "descriptions": (
            "Tägliche Wellness-Unterstützung von YOUR_BRAND NGO. Keine Medizin-Claims.",
            "Anonyme Materialien zu Stress und Alltag für Berlin.",
            "Ruhige Praktiken, Check-in und Artikel auf www.example.com.",
            "NGO-Mission: zugängliche Unterstützung bei Stress. Keine Klinik.",
        ),
    },
)

# Free room inside the $329 Grant envelope for the two bridge campaigns.
BUDGET_REBALANCE: tuple[dict[str, Any], ...] = (
    {
        "campaign_name": "Ser / www.example.com / Articles / EN / Brand_Search",
        "proposed_daily_budget": 104.0,
    },
    {
        "campaign_name": "Ser / www.example.com / Articles / UA / National / Brand_Search",
        "proposed_daily_budget": 89.5,
    },
)


@dataclass
class CityBridgeResult:
    dry_run: bool
    planned: list[dict[str, Any]] = field(default_factory=list)
    created: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    paused_existing: list[str] = field(default_factory=list)
    budget_changes: list[dict[str, Any]] = field(default_factory=list)
    gate: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, str]] = field(default_factory=list)
    after_budget_total: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _ScopedWriteClientProxy:
    def __init__(self, client: Any) -> None:
        self._client = client

    def get_service(self, name: str):
        assert_write_service(name, scope="campaign_create")
        return self._client.get_service(name)

    def get_type(self, name: str):
        return self._client.get_type(name)

    @property
    def enums(self):
        return self._client.enums

    def __getattr__(self, item: str):
        return getattr(self._client, item)


class GrantCityBridgeLauncher:
    def __init__(self, settings: GoogleAdsSettings | None = None) -> None:
        self.settings = settings or GoogleAdsSettings.from_env()
        self.facts = load_product_facts()
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from google.ads.googleads.client import GoogleAdsClient

            raw = GoogleAdsClient.load_from_dict(self.settings.client_config())
            self._client = _ScopedWriteClientProxy(raw)
        return self._client

    def existing_campaigns(self) -> dict[str, dict[str, Any]]:
        ga = self.client.get_service("GoogleAdsService")
        out: dict[str, dict[str, Any]] = {}
        for row in ga.search(
            customer_id=self.settings.customer_id,
            query="""
            SELECT campaign.id, campaign.name, campaign.status,
                   campaign.campaign_budget, campaign_budget.amount_micros
            FROM campaign
            WHERE campaign.status != 'REMOVED'
            """,
        ):
            out[row.campaign.name] = {
                "id": str(row.campaign.id),
                "status": row.campaign.status.name,
                "budget_resource_name": row.campaign.campaign_budget,
                "daily_budget": row.campaign_budget.amount_micros / 1_000_000,
            }
        return out

    def apply(self, *, dry_run: bool = True, enable: bool = True) -> CityBridgeResult:
        gate = self._brand_gate()
        result = CityBridgeResult(dry_run=dry_run, gate=gate)
        if not gate.get("pass"):
            result.errors.append(
                {"op": "brand_gate", "error": f"failed={gate.get('failed')}"}
            )
            return result

        existing = self.existing_campaigns()
        for bridge in CITY_BRIDGES:
            plan = {
                "name": bridge["name"],
                "locale": bridge["locale"],
                "city": bridge["city_label"],
                "budget_usd": bridge["budget_usd"],
                "geo_ids": list(bridge["geo_ids"]),
                "ad_groups": [
                    {
                        "name": group["name"],
                        "keywords": list(group["keywords"]),
                        "final_url": topic_article_url(bridge["locale"], group["name"]),
                    }
                    for group in bridge["ad_groups"]
                ],
                "sitelinks": 16,
                "rsa_per_group": 2,
                "bidding": "MAXIMIZE_CONVERSIONS",
                "cpc_ceiling": None,
            }
            result.planned.append(plan)

        if dry_run:
            result.budget_changes = [dict(item) for item in BUDGET_REBALANCE]
            result.paused_existing = [
                name
                for bridge in CITY_BRIDGES
                for name in bridge["pause_existing"]
                if name in existing and existing[name]["status"] == "ENABLED"
            ]
            return result

        assert_mutations_allowed()
        self._rebalance_budgets(existing, result)
        self._pause_legacy(existing, result)
        for bridge in CITY_BRIDGES:
            current = existing.get(bridge["name"])
            if current and current["status"] != "REMOVED":
                try:
                    repaired = self._ensure_complete(bridge)
                    if repaired.get("changed"):
                        result.created.append({**repaired, "repaired": True})
                    else:
                        result.skipped.append(f"exists:{bridge['name']}")
                except Exception as exc:  # noqa: BLE001
                    result.errors.append(
                        {
                            "op": "repair",
                            "name": bridge["name"],
                            "error": str(exc)[:400],
                        }
                    )
                continue
            try:
                created = self._create_bridge(bridge, enable=enable)
                result.created.append(created)
                existing[bridge["name"]] = {
                    "id": created.get("campaign", "").rsplit("/", 1)[-1],
                    "status": "ENABLED" if enable else "PAUSED",
                    "budget_resource_name": created.get("budget", ""),
                    "daily_budget": bridge["budget_usd"],
                }
            except Exception as exc:  # noqa: BLE001
                result.errors.append(
                    {
                        "op": "create",
                        "name": bridge["name"],
                        "error": str(exc)[:400],
                    }
                )
        result.after_budget_total = self._active_budget_total()
        return result

    def _brand_gate(self) -> dict[str, Any]:
        texts: list[str] = []
        for bridge in CITY_BRIDGES:
            texts.extend(bridge["descriptions"])
            texts.extend(_rsa_hooks(bridge["locale"]))
            for group in bridge["ad_groups"]:
                texts.extend(_rsa_topic_hooks(bridge["locale"], group["name"]))
                texts.extend(group["keywords"])
        return brand_gate_pack(texts)

    def _rebalance_budgets(
        self,
        existing: dict[str, dict[str, Any]],
        result: CityBridgeResult,
    ) -> None:
        writer = GoogleAdsBudgetWriter(settings=self.settings)
        changes = []
        for item in BUDGET_REBALANCE:
            current = existing.get(item["campaign_name"])
            if not current:
                continue
            if abs(current["daily_budget"] - item["proposed_daily_budget"]) < 0.01:
                continue
            changes.append(
                {
                    "action": "set_budget",
                    "campaign_id": current["id"],
                    "campaign_name": item["campaign_name"],
                    "budget_resource_name": current["budget_resource_name"],
                    "proposed_daily_budget": item["proposed_daily_budget"],
                    "current_daily_budget": current["daily_budget"],
                    "current_status": current["status"],
                    "proposed_status": current["status"],
                    "reason": "Free Grant envelope for Kyiv/Berlin MaxClicks bridges",
                }
            )
        if not changes:
            return
        applied = writer.apply_plan({"changes": changes}, dry_run=False)
        result.budget_changes = applied.to_dict().get("applied") or []
        result.errors.extend(
            {"op": "budget", "error": err.get("error", str(err))[:300]}
            for err in (applied.to_dict().get("errors") or [])
        )

    def _pause_legacy(
        self,
        existing: dict[str, dict[str, Any]],
        result: CityBridgeResult,
    ) -> None:
        camp_svc = self.client.get_service("CampaignService")
        for bridge in CITY_BRIDGES:
            for name in bridge["pause_existing"]:
                row = existing.get(name)
                if not row or row["status"] != "ENABLED":
                    continue
                op = self.client.get_type("CampaignOperation")
                op.update.resource_name = (
                    f"customers/{self.settings.customer_id}/campaigns/{row['id']}"
                )
                op.update.status = self.client.enums.CampaignStatusEnum.PAUSED
                op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["status"]))
                camp_svc.mutate_campaigns(
                    customer_id=self.settings.customer_id, operations=[op]
                )
                result.paused_existing.append(name)

    def _ensure_complete(self, bridge: dict[str, Any]) -> dict[str, Any]:
        """Fill missing ad groups / keywords / RSAs / sitelinks on a partial bridge."""
        cid = self.settings.customer_id
        ga = self.client.get_service("GoogleAdsService")
        ag_svc = self.client.get_service("AdGroupService")
        agc_svc = self.client.get_service("AdGroupCriterionService")
        aga_svc = self.client.get_service("AdGroupAdService")
        asset_svc = self.client.get_service("AssetService")
        ca_svc = self.client.get_service("CampaignAssetService")

        safe = bridge["name"].replace("\\", "\\\\").replace("'", "\\'")
        camp_rows = list(
            ga.search(
                customer_id=cid,
                query=(
                    "SELECT campaign.id, campaign.resource_name FROM campaign "
                    f"WHERE campaign.name = '{safe}'"
                ),
            )
        )
        if not camp_rows:
            raise RuntimeError(f"missing campaign {bridge['name']}")
        camp_rn = camp_rows[0].campaign.resource_name
        locale = bridge["locale"]

        groups: dict[str, str] = {}
        for row in ga.search(
            customer_id=cid,
            query=(
                "SELECT campaign.name, ad_group.id, ad_group.name, ad_group.resource_name "
                f"FROM ad_group WHERE campaign.name = '{safe}' AND ad_group.status != 'REMOVED'"
            ),
        ):
            groups[row.ad_group.name] = row.ad_group.resource_name

        existing_kw: dict[str, set[str]] = {name: set() for name in groups}
        for row in ga.search(
            customer_id=cid,
            query=(
                "SELECT campaign.name, ad_group.name, ad_group_criterion.keyword.text "
                "FROM ad_group_criterion "
                f"WHERE campaign.name = '{safe}' AND ad_group_criterion.type = 'KEYWORD' "
                "AND ad_group_criterion.negative = FALSE "
                "AND ad_group_criterion.status != 'REMOVED'"
            ),
        ):
            existing_kw.setdefault(row.ad_group.name, set()).add(
                row.ad_group_criterion.keyword.text.strip().lower()
            )

        ads_by_group: dict[str, int] = {name: 0 for name in groups}
        for row in ga.search(
            customer_id=cid,
            query=(
                "SELECT campaign.name, ad_group.name FROM ad_group_ad "
                f"WHERE campaign.name = '{safe}' AND ad_group_ad.status != 'REMOVED'"
            ),
        ):
            ads_by_group[row.ad_group.name] = ads_by_group.get(row.ad_group.name, 0) + 1

        sitelinks = sum(
            1
            for _ in ga.search(
                customer_id=cid,
                query=(
                    "SELECT campaign.name FROM campaign_asset "
                    f"WHERE campaign.name = '{safe}' "
                    "AND campaign_asset.field_type = 'SITELINK' "
                    "AND campaign_asset.status != 'REMOVED'"
                ),
            )
        )

        changed = False
        keywords_added = 0
        rsa_created = 0
        groups_created = 0

        # Pause incomplete legacy DE burnout shell if present.
        if "Burnout am Arbeitsplatz" in groups and "Burnout am Arbeitsplatz" not in {
            g["name"] for g in bridge["ad_groups"]
        }:
            op = self.client.get_type("AdGroupOperation")
            op.update.resource_name = groups["Burnout am Arbeitsplatz"]
            op.update.status = self.client.enums.AdGroupStatusEnum.PAUSED
            op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["status"]))
            ag_svc.mutate_ad_groups(customer_id=cid, operations=[op])
            changed = True

        for group in bridge["ad_groups"]:
            final_url = topic_article_url(locale, group["name"])
            assert_grant_url(final_url, facts=self.facts)
            ag_rn = groups.get(group["name"])
            if not ag_rn:
                ag_op = self.client.get_type("AdGroupOperation")
                ag_op.create.name = group["name"]
                ag_op.create.campaign = camp_rn
                ag_op.create.status = self.client.enums.AdGroupStatusEnum.ENABLED
                ag_op.create.type_ = self.client.enums.AdGroupTypeEnum.SEARCH_STANDARD
                ag_rn = ag_svc.mutate_ad_groups(
                    customer_id=cid, operations=[ag_op]
                ).results[0].resource_name
                groups[group["name"]] = ag_rn
                existing_kw[group["name"]] = set()
                ads_by_group[group["name"]] = 0
                groups_created += 1
                changed = True

            kw_ops = []
            for text in group["keywords"]:
                if " " not in text.strip():
                    continue
                if text.strip().lower() in existing_kw.get(group["name"], set()):
                    continue
                if not brand_gate_text(text).get("pass"):
                    continue
                op = self.client.get_type("AdGroupCriterionOperation")
                op.create.ad_group = ag_rn
                op.create.status = self.client.enums.AdGroupCriterionStatusEnum.ENABLED
                op.create.keyword.text = text
                op.create.keyword.match_type = self.client.enums.KeywordMatchTypeEnum.PHRASE
                kw_ops.append(op)
            if kw_ops:
                request = self.client.get_type("MutateAdGroupCriteriaRequest")
                request.customer_id = cid
                request.operations.extend(kw_ops)
                request.partial_failure = True
                response = agc_svc.mutate_ad_group_criteria(request=request)
                keywords_added += len(
                    [item for item in response.results if item.resource_name]
                )
                changed = True

            need_ads = max(0, 2 - int(ads_by_group.get(group["name"], 0)))
            if need_ads:
                headlines = self._headlines(locale, group["name"])
                descriptions = [
                    _clip(text, 90)
                    for text in bridge["descriptions"]
                    if brand_gate_text(text).get("pass")
                ][:4]
                while len(descriptions) < 2:
                    descriptions.append(
                        _clip("YOUR_BRAND wellness support from our NGO.", 90)
                    )
                for variant in range(need_ads):
                    aga_op = self.client.get_type("AdGroupAdOperation")
                    aga = aga_op.create
                    aga.ad_group = ag_rn
                    aga.status = self.client.enums.AdGroupAdStatusEnum.ENABLED
                    aga.ad.final_urls.append(final_url)
                    variant_headlines = (
                        headlines if variant == 0 else list(reversed(headlines))
                    )
                    for text in variant_headlines[:15]:
                        asset = self.client.get_type("AdTextAsset")
                        asset.text = text
                        aga.ad.responsive_search_ad.headlines.append(asset)
                    for text in descriptions:
                        asset = self.client.get_type("AdTextAsset")
                        asset.text = text
                        aga.ad.responsive_search_ad.descriptions.append(asset)
                    aga.ad.responsive_search_ad.path1 = locale_path(locale)[:15]
                    aga.ad.responsive_search_ad.path2 = bridge["path2"][:15]
                    aga_svc.mutate_ad_group_ads(customer_id=cid, operations=[aga_op])
                    rsa_created += 1
                    changed = True

        linked = 0
        if sitelinks < 2:
            for sitelink in _build_sitelink_specs(self.facts, locale)[:16]:
                aop = self.client.get_type("AssetOperation")
                aop.create.name = (
                    f"SL · Bridge · {bridge['city_label']} · {sitelink['link_text']}"
                )
                aop.create.sitelink_asset.link_text = sitelink["link_text"]
                aop.create.sitelink_asset.description1 = sitelink["description1"]
                aop.create.sitelink_asset.description2 = sitelink["description2"]
                assert_grant_url(sitelink["final_url"], facts=self.facts)
                aop.create.final_urls.append(sitelink["final_url"])
                asset_rn = asset_svc.mutate_assets(
                    customer_id=cid, operations=[aop]
                ).results[0].resource_name
                lop = self.client.get_type("CampaignAssetOperation")
                lop.create.campaign = camp_rn
                lop.create.asset = asset_rn
                lop.create.field_type = self.client.enums.AssetFieldTypeEnum.SITELINK
                ca_svc.mutate_campaign_assets(customer_id=cid, operations=[lop])
                linked += 1
                changed = True

        return {
            "name": bridge["name"],
            "locale": locale,
            "city": bridge["city_label"],
            "campaign": camp_rn,
            "changed": changed,
            "ad_groups": groups_created,
            "keywords": keywords_added,
            "rsa": rsa_created,
            "sitelinks": linked,
        }

    def _create_bridge(self, bridge: dict[str, Any], *, enable: bool) -> dict[str, Any]:
        cid = self.settings.customer_id
        budget_svc = self.client.get_service("CampaignBudgetService")
        camp_svc = self.client.get_service("CampaignService")
        crit_svc = self.client.get_service("CampaignCriterionService")
        ag_svc = self.client.get_service("AdGroupService")
        agc_svc = self.client.get_service("AdGroupCriterionService")
        aga_svc = self.client.get_service("AdGroupAdService")
        asset_svc = self.client.get_service("AssetService")
        ca_svc = self.client.get_service("CampaignAssetService")

        detail: dict[str, Any] = {
            "name": bridge["name"],
            "locale": bridge["locale"],
            "city": bridge["city_label"],
            "budget_usd": bridge["budget_usd"],
        }

        bop = self.client.get_type("CampaignBudgetOperation")
        bop.create.name = f"Budget · Bridge · {bridge['city_label']}"
        bop.create.amount_micros = int(round(bridge["budget_usd"] * 1_000_000))
        bop.create.delivery_method = self.client.enums.BudgetDeliveryMethodEnum.STANDARD
        bop.create.explicitly_shared = False
        budget_rn = budget_svc.mutate_campaign_budgets(
            customer_id=cid, operations=[bop]
        ).results[0].resource_name
        detail["budget"] = budget_rn

        cop = self.client.get_type("CampaignOperation")
        camp = cop.create
        camp.name = bridge["name"]
        camp.advertising_channel_type = self.client.enums.AdvertisingChannelTypeEnum.SEARCH
        camp.status = (
            self.client.enums.CampaignStatusEnum.ENABLED
            if enable
            else self.client.enums.CampaignStatusEnum.PAUSED
        )
        camp.campaign_budget = budget_rn
        # Maximize Clicks is capped at $2 by Ad Grants while these auctions clear at
        # $6-12, so a bridge launched on it never wins an impression. Smart Bidding
        # is the only strategy that lifts the cap.
        camp.maximize_conversions.target_cpa_micros = 0
        camp.network_settings.target_google_search = True
        camp.network_settings.target_search_network = False
        camp.network_settings.target_content_network = False
        try:
            camp.contains_eu_political_advertising = (
                self.client.enums.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
            )
        except Exception:  # noqa: BLE001
            pass
        camp_rn = camp_svc.mutate_campaigns(
            customer_id=cid, operations=[cop]
        ).results[0].resource_name
        detail["campaign"] = camp_rn
        detail["enabled"] = bool(enable)

        crit_ops = []
        for geo_id in bridge["geo_ids"]:
            op = self.client.get_type("CampaignCriterionOperation")
            op.create.campaign = camp_rn
            op.create.location.geo_target_constant = f"geoTargetConstants/{geo_id}"
            crit_ops.append(op)
        lang_op = self.client.get_type("CampaignCriterionOperation")
        lang_op.create.campaign = camp_rn
        lang_op.create.language.language_constant = bridge["language"]
        crit_ops.append(lang_op)
        for phrase in (*HARMFUL_NEGATIVE_PHRASES, *bridge["extra_negatives"]):
            op = self.client.get_type("CampaignCriterionOperation")
            op.create.campaign = camp_rn
            op.create.negative = True
            op.create.keyword.text = phrase
            op.create.keyword.match_type = self.client.enums.KeywordMatchTypeEnum.PHRASE
            crit_ops.append(op)
        crit_svc.mutate_campaign_criteria(customer_id=cid, operations=crit_ops)
        detail["negatives"] = len(HARMFUL_NEGATIVE_PHRASES) + len(bridge["extra_negatives"])

        locale = bridge["locale"]
        keywords_added = 0
        rsa_created = 0
        groups_created = 0
        for group in bridge["ad_groups"]:
            final_url = topic_article_url(locale, group["name"])
            assert_grant_url(final_url, facts=self.facts)
            ag_op = self.client.get_type("AdGroupOperation")
            ag_op.create.name = group["name"]
            ag_op.create.campaign = camp_rn
            ag_op.create.status = self.client.enums.AdGroupStatusEnum.ENABLED
            ag_op.create.type_ = self.client.enums.AdGroupTypeEnum.SEARCH_STANDARD
            ag_rn = ag_svc.mutate_ad_groups(
                customer_id=cid, operations=[ag_op]
            ).results[0].resource_name
            groups_created += 1

            kw_ops = []
            for text in group["keywords"]:
                if " " not in text.strip():
                    continue
                if not brand_gate_text(text).get("pass"):
                    continue
                op = self.client.get_type("AdGroupCriterionOperation")
                op.create.ad_group = ag_rn
                op.create.status = self.client.enums.AdGroupCriterionStatusEnum.ENABLED
                op.create.keyword.text = text
                op.create.keyword.match_type = self.client.enums.KeywordMatchTypeEnum.PHRASE
                kw_ops.append(op)
            if kw_ops:
                request = self.client.get_type("MutateAdGroupCriteriaRequest")
                request.customer_id = cid
                request.operations.extend(kw_ops)
                request.partial_failure = True
                response = agc_svc.mutate_ad_group_criteria(request=request)
                keywords_added += len(
                    [item for item in response.results if item.resource_name]
                )
                if response.partial_failure_error.code:
                    detail.setdefault("keyword_warnings", []).append(
                        response.partial_failure_error.message[:240]
                    )

            headlines = self._headlines(locale, group["name"])
            descriptions = [
                _clip(text, 90)
                for text in bridge["descriptions"]
                if brand_gate_text(text).get("pass")
            ][:4]
            while len(descriptions) < 2:
                descriptions.append(_clip("YOUR_BRAND wellness support from our NGO.", 90))

            for variant in range(2):
                aga_op = self.client.get_type("AdGroupAdOperation")
                aga = aga_op.create
                aga.ad_group = ag_rn
                aga.status = self.client.enums.AdGroupAdStatusEnum.ENABLED
                aga.ad.final_urls.append(final_url)
                variant_headlines = headlines if variant == 0 else list(reversed(headlines))
                for text in variant_headlines[:15]:
                    asset = self.client.get_type("AdTextAsset")
                    asset.text = text
                    aga.ad.responsive_search_ad.headlines.append(asset)
                for text in descriptions:
                    asset = self.client.get_type("AdTextAsset")
                    asset.text = text
                    aga.ad.responsive_search_ad.descriptions.append(asset)
                aga.ad.responsive_search_ad.path1 = locale_path(locale)[:15]
                aga.ad.responsive_search_ad.path2 = bridge["path2"][:15]
                aga_svc.mutate_ad_group_ads(customer_id=cid, operations=[aga_op])
                rsa_created += 1

        linked = 0
        for sitelink in _build_sitelink_specs(self.facts, locale)[:16]:
            aop = self.client.get_type("AssetOperation")
            aop.create.name = f"SL · Bridge · {bridge['city_label']} · {sitelink['link_text']}"
            aop.create.sitelink_asset.link_text = sitelink["link_text"]
            aop.create.sitelink_asset.description1 = sitelink["description1"]
            aop.create.sitelink_asset.description2 = sitelink["description2"]
            assert_grant_url(sitelink["final_url"], facts=self.facts)
            aop.create.final_urls.append(sitelink["final_url"])
            asset_rn = asset_svc.mutate_assets(
                customer_id=cid, operations=[aop]
            ).results[0].resource_name
            lop = self.client.get_type("CampaignAssetOperation")
            lop.create.campaign = camp_rn
            lop.create.asset = asset_rn
            lop.create.field_type = self.client.enums.AssetFieldTypeEnum.SITELINK
            ca_svc.mutate_campaign_assets(customer_id=cid, operations=[lop])
            linked += 1

        detail.update(
            {
                "ad_groups": groups_created,
                "keywords": keywords_added,
                "rsa": rsa_created,
                "sitelinks": linked,
            }
        )
        return detail

    def _headlines(self, locale: str, ad_group_name: str) -> list[str]:
        texts = [
            *(_rsa_topic_hooks(locale, ad_group_name) or []),
            *(_rsa_hooks(locale) or []),
            "YOUR_BRAND",
            "YOUR_BRAND NGO" if locale != "UA" else "ГО YOUR_BRAND",
        ]
        out: list[str] = []
        for text in texts:
            clipped = _clip(text, 30)
            if (
                clipped
                and clipped not in out
                and brand_gate_text(clipped).get("pass")
                and "{" not in clipped
            ):
                out.append(clipped)
            if len(out) >= 15:
                break
        while len(out) < 3:
            out.append("YOUR_BRAND")
        return out

    def _active_budget_total(self) -> float:
        ga = self.client.get_service("GoogleAdsService")
        seen: dict[str, float] = {}
        for row in ga.search(
            customer_id=self.settings.customer_id,
            query="""
            SELECT campaign.campaign_budget, campaign_budget.amount_micros
            FROM campaign
            WHERE campaign.status = 'ENABLED'
            """,
        ):
            seen[row.campaign.campaign_budget] = row.campaign_budget.amount_micros / 1_000_000
        return round(sum(seen.values()), 2)
