"""Pause clinical/PROHIBITED RSA and create clean replacements (Google Ads best practice).

For city Search campaigns (focus cities) — and optionally all ENABLED Search —
ads that still carry clinical KeyWord customizers or clinical copy cannot be safely
mutated in-place when policy-locked. Flow per ad group:
  1) create one brand-gated RSA (15H / 4D) with product_facts final URL
  2) pause the clinical ENABLED RSAs in that ad group
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.adapters.write_guard import assert_write_service
from ads_ai_agents.intelligence.brand_gate import brand_gate_pack
from ads_ai_agents.intelligence.enterprise_asset_coverage import _clip, _rsa_hooks
from ads_ai_agents.intelligence.grant_domain_gate import assert_grant_url
from ads_ai_agents.logging_utils import configure_logging
from ads_ai_agents.platform.product_facts import focus_cities, load_product_facts

logger = configure_logging()

# City labels from product_facts.json → geo.focus_cities (example: City_A/B/C).
CITY_MARKERS = focus_cities()
MAX_HEADLINES = 15
MAX_DESCRIPTIONS = 4
MAX_DESC_LEN = 90

CLINICAL_MARKERS = (
    "психолог",
    "психотерап",
    "психіатр",
    "діагноз",
    "терап",
    "лікар",
    "лікуван",
    "медичн",
    "psycholog",
    "psychiatr",
    "therap",
    "counsel",
    "diagnos",
    "medication",
    "treatment",
    "disorder",
    "ptsd",
    "mental disorder",
    "безкоштовно",  # free clinical-help claim — Grant risk
)

# Safe UA descriptions (≤90), brand-gated wellness only — no psychologist/medical claims.
DESC_UA = (
    "Анонімний AI-чат після важкого дня. Підтримка без осуду, у вашому темпі.",
    "Спокійний check-in за хвилину. М'яка підтримка для щоденних моментів.",
    "Щоденник, тести й аналітика в одному місці. Приватність first.",
    "Ресурси ГО YOUR_BRAND онлайн. Почніть м'який діалог сьогодні.",
)
DESC_EN = (
    "Anonymous AI chat after a hard day. Private support at your own pace.",
    "Calm check-in in a minute. Gentle support for everyday moments.",
    "Diary, self-checks and analytics in one place. Privacy first.",
    "NGO resources online. Start a gentle conversation today.",
)


@dataclass
class ReplaceResult:
    dry_run: bool
    candidates: int = 0
    ad_groups: int = 0
    created: int = 0
    paused: int = 0
    skipped: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    details: list[dict[str, Any]] = field(default_factory=list)
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


def _is_clinical_blob(texts: list[str]) -> bool:
    blob = " ".join(texts or []).lower()
    return any(m in blob for m in CLINICAL_MARKERS)


def _has_keyword_customizer(headlines: list[str]) -> bool:
    return any("{" in (h or "") and "keyword" in (h or "").lower() for h in headlines)


def _locale_from_campaign(name: str) -> str:
    n = (name or "").upper()
    if "/ EN" in n or " EN /" in n:
        return "EN"
    if "/ DE" in n or " DE /" in n:
        return "DE"
    return "UA"


def _default_url(facts: dict[str, Any], locale: str) -> str:
    landings = facts.get("landings") or {}
    return landings.get(locale) or landings.get("UA") or landings.get("EN") or "https://www.example.com/uk"


class ProhibitedRsaReplacer:
    def __init__(self, settings: GoogleAdsSettings | None = None) -> None:
        self.settings = settings or GoogleAdsSettings.from_env()
        self.facts = load_product_facts()
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from google.ads.googleads.client import GoogleAdsClient

            raw = GoogleAdsClient.load_from_dict(self.settings.client_config())
            self._client = _ScopedWriteClientProxy(raw, scope="asset_publish")
        return self._client

    def inventory(self, *, cities_only: bool = True) -> list[dict[str, Any]]:
        ga = self.client.get_service("GoogleAdsService")
        cid = self.settings.customer_id
        out: list[dict[str, Any]] = []
        for r in ga.search(
            customer_id=cid,
            query="""
            SELECT campaign.id, campaign.name, campaign.status,
                   ad_group.id, ad_group.name, ad_group.resource_name,
                   ad_group_ad.resource_name, ad_group_ad.status,
                   ad_group_ad.ad.id, ad_group_ad.ad.final_urls,
                   ad_group_ad.ad.responsive_search_ad.headlines,
                   ad_group_ad.ad.responsive_search_ad.descriptions,
                   ad_group_ad.policy_summary.approval_status
            FROM ad_group_ad
            WHERE campaign.status = 'ENABLED'
              AND ad_group.status = 'ENABLED'
              AND ad_group_ad.status = 'ENABLED'
              AND ad_group_ad.ad.type = 'RESPONSIVE_SEARCH_AD'
              AND campaign.advertising_channel_type = 'SEARCH'
            """,
        ):
            name = r.campaign.name or ""
            if cities_only and not any(m in name for m in CITY_MARKERS):
                continue
            headlines = [(h.text or "").strip() for h in (r.ad_group_ad.ad.responsive_search_ad.headlines or [])]
            descriptions = [
                (d.text or "").strip() for d in (r.ad_group_ad.ad.responsive_search_ad.descriptions or [])
            ]
            try:
                approval = self.client.enums.PolicyApprovalStatusEnum.PolicyApprovalStatus.Name(
                    r.ad_group_ad.policy_summary.approval_status
                )
            except Exception:  # noqa: BLE001
                approval = str(r.ad_group_ad.policy_summary.approval_status)
            clinical = _is_clinical_blob(headlines + descriptions)
            has_kw = _has_keyword_customizer(headlines)
            short = len(headlines) < MAX_HEADLINES
            disapproved = approval in ("DISAPPROVED",)
            if not (clinical or has_kw or short or disapproved):
                continue
            urls = list(r.ad_group_ad.ad.final_urls or [])
            out.append(
                {
                    "campaign": name,
                    "campaign_id": r.campaign.id,
                    "ad_group_id": r.ad_group.id,
                    "ad_group_name": r.ad_group.name,
                    "ad_group_rn": r.ad_group.resource_name,
                    "aga_rn": r.ad_group_ad.resource_name,
                    "ad_id": r.ad_group_ad.ad.id,
                    "final_urls": urls,
                    "headlines": headlines,
                    "descriptions": descriptions,
                    "approval": approval,
                    "clinical": clinical,
                    "has_kw": has_kw,
                    "short": short,
                    "locale": _locale_from_campaign(name),
                }
            )
        return out

    def apply(self, *, dry_run: bool = True, cities_only: bool = True) -> ReplaceResult:
        candidates = self.inventory(cities_only=cities_only)
        by_ag: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in candidates:
            by_ag[row["ad_group_id"]].append(row)

        headlines_ua = _rsa_hooks("UA")[:MAX_HEADLINES]
        headlines_en = _rsa_hooks("EN")[:MAX_HEADLINES]
        descs_ua = [_clip(d, MAX_DESC_LEN) for d in DESC_UA][:MAX_DESCRIPTIONS]
        descs_en = [_clip(d, MAX_DESC_LEN) for d in DESC_EN][:MAX_DESCRIPTIONS]
        gate = brand_gate_pack(headlines_ua + headlines_en + descs_ua + descs_en)
        result = ReplaceResult(
            dry_run=dry_run,
            candidates=len(candidates),
            ad_groups=len(by_ag),
            gate=gate,
        )
        if not gate.get("pass"):
            result.errors.append({"error": "brand_gate_failed", "failed": gate.get("failed")})
            return result
        if not dry_run:
            assert_mutations_allowed()

        aga_svc = None if dry_run else self.client.get_service("AdGroupAdService")
        status_paused = self.client.enums.AdGroupAdStatusEnum.PAUSED
        status_enabled = self.client.enums.AdGroupAdStatusEnum.ENABLED

        for ag_id, rows in by_ag.items():
            sample = rows[0]
            locale = sample["locale"]
            headlines = headlines_ua if locale == "UA" else headlines_en
            descriptions = descs_ua if locale == "UA" else descs_en
            # Prefer existing final URL; fall back to product_facts landing
            final_url = (sample.get("final_urls") or [None])[0] or _default_url(self.facts, locale)
            detail: dict[str, Any] = {
                "ad_group_id": ag_id,
                "campaign": sample["campaign"][:55],
                "pause_ads": [r["ad_id"] for r in rows],
                "final_url": final_url,
            }
            # 1) Create replacement first (never leave AG without ENABLED ad)
            try:
                if dry_run:
                    result.created += 1
                    detail["created"] = "dry_run"
                else:
                    op = self.client.get_type("AdGroupAdOperation")
                    aga = op.create
                    aga.ad_group = sample["ad_group_rn"]
                    aga.status = status_enabled
                    ad = aga.ad
                    assert_grant_url(final_url, facts=self.facts)
                    ad.final_urls.append(final_url)
                    rsa = ad.responsive_search_ad
                    for text in headlines:
                        asset = self.client.get_type("AdTextAsset")
                        asset.text = text
                        rsa.headlines.append(asset)
                    for text in descriptions:
                        asset = self.client.get_type("AdTextAsset")
                        asset.text = text
                        rsa.descriptions.append(asset)
                    resp = aga_svc.mutate_ad_group_ads(
                        customer_id=self.settings.customer_id, operations=[op]
                    )
                    detail["created"] = resp.results[0].resource_name
                    result.created += 1
            except Exception as exc:  # noqa: BLE001
                result.errors.append(
                    {
                        "op": "create_rsa",
                        "ad_group_id": ag_id,
                        "campaign": sample["campaign"][:40],
                        "error": str(exc)[:240],
                    }
                )
                result.skipped.append(f"create_failed:{ag_id}")
                result.details.append(detail)
                continue

            # 2) Pause clinical/legacy ads
            for row in rows:
                try:
                    if dry_run:
                        result.paused += 1
                        continue
                    from google.protobuf import field_mask_pb2

                    op = self.client.get_type("AdGroupAdOperation")
                    op.update.resource_name = row["aga_rn"]
                    op.update.status = status_paused
                    op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["status"]))
                    aga_svc.mutate_ad_group_ads(
                        customer_id=self.settings.customer_id, operations=[op]
                    )
                    result.paused += 1
                except Exception as exc:  # noqa: BLE001
                    result.errors.append(
                        {
                            "op": "pause_rsa",
                            "ad_id": row["ad_id"],
                            "error": str(exc)[:200],
                        }
                    )
            result.details.append(detail)
        return result
