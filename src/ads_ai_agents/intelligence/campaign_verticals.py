"""Create + ENABLE Search vertical campaigns (Corporate / NGO / App / News).

Public finals only (ads_public_urls). Enterprise seed budgets under grant envelope.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.adapters.write_guard import assert_write_service
from ads_ai_agents.intelligence.ads_public_urls import locale_path, public_urls
from ads_ai_agents.intelligence.brand_gate import brand_gate_pack, brand_gate_text
from ads_ai_agents.intelligence.enterprise_asset_coverage import (
    _build_sitelink_specs,
    _clip,
    _rsa_hooks,
)
from ads_ai_agents.intelligence.grant_compliance import grant_safe_mode_enabled
from ads_ai_agents.intelligence.grant_domain_gate import assert_grant_url
from ads_ai_agents.logging_utils import configure_logging
from ads_ai_agents.platform.product_facts import load_product_facts

logger = configure_logging()

# Match existing EN Search footprint (already live on account).
GEO = {
    "UA": ["geoTargetConstants/2804"],
    "DE": ["geoTargetConstants/2276"],
    "LT": ["geoTargetConstants/2440"],
    "EN": [
        "geoTargetConstants/2036",  # AU
        "geoTargetConstants/2124",  # CA
        "geoTargetConstants/2372",  # IE
        "geoTargetConstants/2826",  # UK
        "geoTargetConstants/2840",  # US
    ],
}
# Verified against language_constant: 1000=en, 1001=de, 1036=uk, 1029=lt.
LANG = {
    "EN": "languageConstants/1000",
    "DE": "languageConstants/1001",
    "UA": "languageConstants/1036",
    "LT": "languageConstants/1029",
}

KEYWORDS = {
    "corporate": {
        "UA": ["wellness для співробітників", "підтримка HR команди", "вигорання на роботі", "employee wellness"],
        "EN": ["employee wellness program", "workplace mental wellbeing", "HR wellness benefits", "team burnout support"],
        "DE": ["Employee Wellness", "Burnout am Arbeitsplatz", "HR Wellness Programm"],
        "LT": ["darbuotoju wellbeing", "HR wellness", "perdegimas darbe"],
    },
    "ngo": {
        "UA": ["підтримка громадських організацій", "wellness для НГО", "партнерська підтримка"],
        "EN": ["NGO wellness support", "community wellbeing program", "nonprofit employee support"],
        "DE": ["NGO Wellness", "Gemeinschaft Wohlbefinden"],
        "LT": ["NVO wellbeing", "bendruomenes parama"],
    },
    "app": {
        "UA": ["YOUR_BRAND app", "завантажити wellness app", "додаток підтримки"],
        "EN": ["YOUR_BRAND app", "download wellness app", "mental wellbeing app"],
        "DE": ["YOUR_BRAND App", "Wellness App Download"],
        "LT": ["YOUR_BRAND app", "wellness programele"],
    },
    "news": {
        "UA": ["YOUR_BRAND новини", "статті про стрес", "wellness статті"],
        "EN": ["YOUR_BRAND news", "wellness articles", "stress management blog"],
        "DE": ["YOUR_BRAND News", "Wellness Artikel"],
        "LT": ["YOUR_BRAND naujienos", "wellness straipsniai"],
    },
}

DESC = {
    "UA": (
        "Приватний AI-чат для стресу та вигорання. Без клінічних обіцянок.",
        "Спокійний check-in за хвилину. Велнес-підтримка, не клініка.",
        "Веб і мобільний app. Почніть м'який діалог сьогодні.",
        "Публічні матеріали та продукт YOUR_BRAND.",
    ),
    "EN": (
        "Private AI chat for work stress and burnout. No medical claims.",
        "Calm check-in in a minute. Wellness support, not a clinic.",
        "Web and mobile app. Start a gentle conversation today.",
        "Public resources and the YOUR_BRAND product.",
    ),
    "DE": (
        "Privater KI-Chat bei Stress. Keine Medizin-Claims.",
        "Ruhiger Check-in in einer Minute. Wellness, keine Klinik.",
        "Web und App. Sanften Dialog starten.",
        "Öffentliche Inhalte und YOUR_BRAND Produkt.",
    ),
    "LT": (
        "Privatus AI pokalbis del streso. Be medicininiu teiginiu.",
        "Ramus check-in per minute. Wellness, ne klinika.",
        "Web ir programėlė. Pradėkite švelnų pokalbį.",
        "Vieši resursai ir YOUR_BRAND produktas.",
    ),
}


@dataclass
class VerticalCreateResult:
    dry_run: bool
    created_campaigns: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    enabled: int = 0
    sitelinks_linked: int = 0
    keywords_added: int = 0
    rsa_created: int = 0
    gate: dict[str, Any] = field(default_factory=dict)

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


def _vertical_specs(facts: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten blueprint into concrete ENABLED Search campaigns."""
    specs: list[dict[str, Any]] = []
    # Compact seed budgets (USD micros later) — grant-aware.
    for loc in ("UA", "EN", "DE", "LT"):
        u = public_urls(facts, locale=loc)
        specs.append(
            {
                "vertical": "corporate",
                "locale": loc,
                "name": f"Ser / www.example.com / Corporate / {loc} / Agents",
                "final_url": u["corporate"],
                "budget_usd": 8.0 if loc in ("UA", "EN") else 5.0,
                "keywords": KEYWORDS["corporate"].get(loc) or KEYWORDS["corporate"]["EN"],
            }
        )
        specs.append(
            {
                "vertical": "ngo",
                "locale": loc,
                "name": f"Ser / www.example.com / NGO / {loc} / Agents",
                "final_url": u["ngo"],
                "budget_usd": 6.0 if loc in ("UA", "EN") else 4.0,
                "keywords": KEYWORDS["ngo"].get(loc) or KEYWORDS["ngo"]["EN"],
            }
        )
        specs.append(
            {
                "vertical": "news",
                "locale": loc,
                "name": f"Ser / www.example.com / News / {loc} / Agents",
                "final_url": u["news"],
                "budget_usd": 6.0 if loc in ("UA", "EN") else 4.0,
                "keywords": KEYWORDS["news"].get(loc) or KEYWORDS["news"]["EN"],
            }
        )
    for loc in ("UA", "EN"):
        u = public_urls(facts, locale=loc)
        for store, url_key, label in (
            ("Android", "android", "Play"),
            ("iOS", "ios", "AppStore"),
        ):
            specs.append(
                {
                    "vertical": "app",
                    "locale": loc,
                    "name": f"Ser / www.example.com / App / {store} / {loc} / Agents",
                    "final_url": u[url_key],
                    "budget_usd": 8.0,
                    "keywords": KEYWORDS["app"].get(loc) or KEYWORDS["app"]["EN"],
                    "store_label": label,
                }
            )
    return specs


class CampaignVerticalLauncher:
    def __init__(self, settings: GoogleAdsSettings | None = None) -> None:
        self.settings = settings or GoogleAdsSettings.from_env()
        self.facts = load_product_facts()
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from google.ads.googleads.client import GoogleAdsClient

            raw = GoogleAdsClient.load_from_dict(self.settings.client_config())
            self._client = _ScopedWriteClientProxy(raw, scope="campaign_create")
        return self._client

    def existing_names(self) -> set[str]:
        ga = self.client.get_service("GoogleAdsService")
        names: set[str] = set()
        for r in ga.search(
            customer_id=self.settings.customer_id,
            query="SELECT campaign.name, campaign.status FROM campaign",
        ):
            names.add(r.campaign.name)
        return names

    def apply(self, *, dry_run: bool = True, enable: bool = True) -> VerticalCreateResult:
        if grant_safe_mode_enabled(self.facts):
            raise PermissionError(
                "Campaign vertical creation is disabled for the Ad Grants account "
                "until the builder enforces full Grant structure and policy preflight."
            )
        specs = _vertical_specs(self.facts)
        texts: list[str] = []
        for loc in ("UA", "EN", "DE", "LT"):
            texts.extend(_rsa_hooks(loc)[:15])
            texts.extend(list(DESC.get(loc) or DESC["EN"]))
        gate = brand_gate_pack(texts)
        result = VerticalCreateResult(dry_run=dry_run, gate=gate)
        if not gate.get("pass"):
            result.errors.append({"error": "brand_gate_failed", "failed": gate.get("failed")})
            return result

        existing = self.existing_names()
        if not dry_run:
            assert_mutations_allowed()

        for spec in specs:
            if spec["name"] in existing:
                result.skipped.append(f"exists:{spec['name']}")
                # Still try to ENABLE if paused
                if not dry_run and enable:
                    try:
                        self._enable_by_name(spec["name"])
                        result.enabled += 1
                    except Exception as exc:  # noqa: BLE001
                        result.errors.append({"op": "enable_existing", "name": spec["name"], "error": str(exc)[:160]})
                continue
            try:
                detail = self._create_one(spec, dry_run=dry_run, enable=enable)
                result.created_campaigns.append(detail)
                if detail.get("enabled"):
                    result.enabled += 1
                result.rsa_created += int(detail.get("rsa") or 0)
                result.keywords_added += int(detail.get("keywords") or 0)
                result.sitelinks_linked += int(detail.get("sitelinks") or 0)
            except Exception as exc:  # noqa: BLE001
                result.errors.append({"op": "create", "name": spec["name"], "error": str(exc)[:300]})
        return result

    def _enable_by_name(self, name: str) -> None:
        ga = self.client.get_service("GoogleAdsService")
        camp_svc = self.client.get_service("CampaignService")
        from google.protobuf import field_mask_pb2

        safe = name.replace("\\", "\\\\").replace("'", "\\'")
        query = (
            "SELECT campaign.id, campaign.name, campaign.status "
            f"FROM campaign WHERE campaign.name = '{safe}'"
        )
        for r in ga.search(customer_id=self.settings.customer_id, query=query):
            if str(r.campaign.status).endswith("ENABLED") or int(r.campaign.status) == 2:
                return
            op = self.client.get_type("CampaignOperation")
            op.update.resource_name = f"customers/{self.settings.customer_id}/campaigns/{r.campaign.id}"
            op.update.status = self.client.enums.CampaignStatusEnum.ENABLED
            op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["status"]))
            camp_svc.mutate_campaigns(customer_id=self.settings.customer_id, operations=[op])
            return

    def _create_one(self, spec: dict[str, Any], *, dry_run: bool, enable: bool) -> dict[str, Any]:
        detail: dict[str, Any] = {
            "name": spec["name"],
            "final_url": spec["final_url"],
            "budget_usd": spec["budget_usd"],
            "locale": spec["locale"],
            "vertical": spec["vertical"],
        }
        if dry_run:
            detail.update({"enabled": enable, "rsa": 1, "keywords": len(spec["keywords"]), "sitelinks": 8})
            return detail

        cid = self.settings.customer_id
        budget_svc = self.client.get_service("CampaignBudgetService")
        camp_svc = self.client.get_service("CampaignService")
        crit_svc = self.client.get_service("CampaignCriterionService")
        ag_svc = self.client.get_service("AdGroupService")
        agc_svc = self.client.get_service("AdGroupCriterionService")
        aga_svc = self.client.get_service("AdGroupAdService")
        asset_svc = self.client.get_service("AssetService")
        ca_svc = self.client.get_service("CampaignAssetService")

        # Budget
        bop = self.client.get_type("CampaignBudgetOperation")
        bop.create.name = f"Budget · {spec['name'][:40]}"
        bop.create.amount_micros = int(spec["budget_usd"] * 1_000_000)
        bop.create.delivery_method = self.client.enums.BudgetDeliveryMethodEnum.STANDARD
        bop.create.explicitly_shared = False
        budget_rn = budget_svc.mutate_campaign_budgets(
            customer_id=cid, operations=[bop]
        ).results[0].resource_name
        detail["budget"] = budget_rn

        # Campaign
        cop = self.client.get_type("CampaignOperation")
        camp = cop.create
        camp.name = spec["name"]
        camp.advertising_channel_type = self.client.enums.AdvertisingChannelTypeEnum.SEARCH
        camp.status = (
            self.client.enums.CampaignStatusEnum.ENABLED
            if enable
            else self.client.enums.CampaignStatusEnum.PAUSED
        )
        camp.campaign_budget = budget_rn
        camp.maximize_conversions.target_cpa_micros = 0
        camp.network_settings.target_google_search = True
        camp.network_settings.target_search_network = False
        camp.network_settings.target_content_network = False
        # EU political advertising declaration (required in recent API versions)
        try:
            camp.contains_eu_political_advertising = (
                self.client.enums.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
            )
        except Exception:  # noqa: BLE001
            pass
        camp_rn = camp_svc.mutate_campaigns(customer_id=cid, operations=[cop]).results[0].resource_name
        detail["campaign"] = camp_rn
        detail["enabled"] = bool(enable)

        # Geo (+ language best-effort)
        def _geo_ops() -> list[Any]:
            ops = []
            for geo in GEO.get(spec["locale"], GEO["EN"]):
                op = self.client.get_type("CampaignCriterionOperation")
                op.create.campaign = camp_rn
                op.create.location.geo_target_constant = geo
                ops.append(op)
            return ops

        crit_ops = _geo_ops()
        lang = LANG.get(spec["locale"])
        if lang:
            op = self.client.get_type("CampaignCriterionOperation")
            op.create.campaign = camp_rn
            op.create.language.language_constant = lang
            crit_ops.append(op)
        try:
            crit_svc.mutate_campaign_criteria(customer_id=cid, operations=crit_ops)
        except Exception:  # noqa: BLE001
            crit_svc.mutate_campaign_criteria(customer_id=cid, operations=_geo_ops())
            detail["language_skipped"] = True

        # Ad group
        ag_op = self.client.get_type("AdGroupOperation")
        ag_op.create.name = f"{spec['vertical'].title()} · {spec['locale']}"
        ag_op.create.campaign = camp_rn
        ag_op.create.status = self.client.enums.AdGroupStatusEnum.ENABLED
        ag_op.create.type_ = self.client.enums.AdGroupTypeEnum.SEARCH_STANDARD
        ag_rn = ag_svc.mutate_ad_groups(customer_id=cid, operations=[ag_op]).results[0].resource_name
        detail["ad_group"] = ag_rn

        # Keywords (BROAD)
        kw_ops = []
        for text in spec["keywords"]:
            # Ad Grants Support requires zero positive single-word keywords.
            if len((text or "").strip().split()) < 2:
                continue
            op = self.client.get_type("AdGroupCriterionOperation")
            op.create.ad_group = ag_rn
            op.create.status = self.client.enums.AdGroupCriterionStatusEnum.ENABLED
            op.create.keyword.text = text
            op.create.keyword.match_type = self.client.enums.KeywordMatchTypeEnum.BROAD
            kw_ops.append(op)
        if kw_ops:
            agc_svc.mutate_ad_group_criteria(customer_id=cid, operations=kw_ops)
            detail["keywords"] = len(kw_ops)

        # RSA
        locale = spec["locale"]
        headlines = [t for t in _rsa_hooks(locale) if brand_gate_text(t).get("pass") and "{" not in t][:15]
        while len(headlines) < 3:
            headlines.append("YOUR_BRAND")
        descs = [_clip(d, 90) for d in (DESC.get(locale) or DESC["EN"])][:4]
        aga_op = self.client.get_type("AdGroupAdOperation")
        aga = aga_op.create
        aga.ad_group = ag_rn
        aga.status = self.client.enums.AdGroupAdStatusEnum.ENABLED
        assert_grant_url(spec["final_url"], facts=self.facts)
        aga.ad.final_urls.append(spec["final_url"])
        for t in headlines:
            a = self.client.get_type("AdTextAsset")
            a.text = t
            aga.ad.responsive_search_ad.headlines.append(a)
        for t in descs:
            a = self.client.get_type("AdTextAsset")
            a.text = t
            aga.ad.responsive_search_ad.descriptions.append(a)
        # path hints
        lang = locale_path(locale)
        aga.ad.responsive_search_ad.path1 = lang[:15]
        aga.ad.responsive_search_ad.path2 = spec["vertical"][:15]
        aga_svc.mutate_ad_group_ads(customer_id=cid, operations=[aga_op])
        detail["rsa"] = 1

        # Sitelinks (public pack subset)
        linked = 0
        for sl in _build_sitelink_specs(self.facts, locale)[:10]:
            try:
                aop = self.client.get_type("AssetOperation")
                aop.create.name = f"SL · {sl['link_text']} · {spec['locale']}"
                aop.create.sitelink_asset.link_text = sl["link_text"]
                aop.create.sitelink_asset.description1 = sl["description1"]
                aop.create.sitelink_asset.description2 = sl["description2"]
                assert_grant_url(sl["final_url"], facts=self.facts)
                aop.create.final_urls.append(sl["final_url"])
                asset_rn = asset_svc.mutate_assets(customer_id=cid, operations=[aop]).results[0].resource_name
                lop = self.client.get_type("CampaignAssetOperation")
                lop.create.campaign = camp_rn
                lop.create.asset = asset_rn
                lop.create.field_type = self.client.enums.AssetFieldTypeEnum.SITELINK
                ca_svc.mutate_campaign_assets(customer_id=cid, operations=[lop])
                linked += 1
            except Exception as exc:  # noqa: BLE001
                detail.setdefault("sitelink_errors", []).append(str(exc)[:120])
        detail["sitelinks"] = linked
        return detail
