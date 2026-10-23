"""Boost Performance Max asset groups toward Excellent ad strength.

Google guidance: 15 headlines, 5 descriptions, logos, business name, own videos,
plus campaign sitelinks (≥6). Links existing logos/videos; adds brand-gated text.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.adapters.write_guard import assert_write_service
from ads_ai_agents.intelligence.brand_gate import brand_gate_pack, brand_gate_text
from ads_ai_agents.intelligence.enterprise_asset_coverage import _clip, _rsa_hooks
from ads_ai_agents.logging_utils import configure_logging
from ads_ai_agents.platform.account_ids import asset_resource

logger = configure_logging()

# Brand library assets come from config/account_ids.json ("assets"); empty entries are skipped.
DEFAULT_LOGO = asset_resource("logo")  # square logo
DEFAULT_LANDSCAPE_LOGO = asset_resource("landscape_logo")
DEFAULT_BUSINESS_NAME = asset_resource("business_name")
DEFAULT_IMAGES = {
    key: rn
    for key, rn in {
        "MARKETING_IMAGE": asset_resource("marketing_image"),
        "SQUARE_MARKETING_IMAGE": asset_resource("square_marketing_image"),
        "PORTRAIT_MARKETING_IMAGE": asset_resource("portrait_marketing_image"),
    }.items()
    if rn
}

TARGET_HEADLINES = 15
TARGET_DESCRIPTIONS = 5
TARGET_LONG_HEADLINES = 5

PMAX_DESCRIPTIONS = {
    "EN": (
        "Anonymous AI chat for work stress and burnout. No medical claims.",
        "Calm check-in in a minute. Wellness support, not a clinic.",
        "Web and mobile app. Start a gentle conversation today.",
        "Privacy first. Steady daily support when thoughts won't rest.",
        "Built for after-shift stress. Soft steps, not clinical promises.",
    ),
    "UA": (
        "Анонімний AI-чат для стресу та вигорання. Без клінічних обіцянок.",
        "Спокійний check-in за хвилину. Велнес-підтримка, не клініка.",
        "Веб і мобільний app. Почніть м'який діалог сьогодні.",
        "Приватність first. Щоденна опора, коли думки не відпускають.",
        "Для стресу після зміни. М'які кроки без клінічних обіцянок.",
    ),
}

LONG_HEADLINES = {
    "EN": (
        "Anonymous daily support when work stress follows you home",
        "A calm AI companion for burnout-friendly check-ins anytime",
        "Wellness chat that stays private — no diagnosis, just steadier days",
        "Start a gentle conversation in under a minute with YOUR_BRAND",
        "After-shift wind-down support for people who carry too much",
    ),
    "UA": (
        "Анонімна підтримка, коли робочий стрес іде з вами додому",
        "Спокійний AI-супутник для check-in проти вигорання щодня",
        "Велнес-чат наодинці — без діагнозів, лише стабільніші дні",
        "Почніть м'яку розмову за хвилину з YOUR_BRAND",
        "Підтримка після зміни для тих, хто тримає занадто багато",
    ),
}


@dataclass
class PMaxStrengthResult:
    dry_run: bool
    asset_groups: int = 0
    text_created: int = 0
    text_linked: int = 0
    logos_linked: int = 0
    business_linked: int = 0
    videos_linked: int = 0
    images_linked: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)
    before: list[dict[str, Any]] = field(default_factory=list)
    after: list[dict[str, Any]] = field(default_factory=list)
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


def _locale_from_campaign(name: str) -> str:
    n = (name or "").upper()
    if "/ EN" in n or " EN /" in n:
        return "EN"
    return "UA"


class PMaxAdStrengthBooster:
    def __init__(self, settings: GoogleAdsSettings | None = None) -> None:
        self.settings = settings or GoogleAdsSettings.from_env()
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from google.ads.googleads.client import GoogleAdsClient

            raw = GoogleAdsClient.load_from_dict(self.settings.client_config())
            self._client = _ScopedWriteClientProxy(raw, scope="asset_publish")
        return self._client

    def _strength_name(self, raw: Any) -> str:
        try:
            return self.client.enums.AdStrengthEnum.AdStrength.Name(raw)
        except Exception:  # noqa: BLE001
            return str(raw)

    def snapshot(self) -> list[dict[str, Any]]:
        ga = self.client.get_service("GoogleAdsService")
        cid = self.settings.customer_id
        groups = []
        counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        names: dict[str, dict[str, Any]] = {}
        for r in ga.search(
            customer_id=cid,
            query="""
            SELECT campaign.name, campaign.status, campaign.advertising_channel_type,
                   asset_group.resource_name, asset_group.name, asset_group.status,
                   asset_group.ad_strength
            FROM asset_group
            WHERE campaign.status = 'ENABLED'
              AND campaign.advertising_channel_type = 'PERFORMANCE_MAX'
              AND asset_group.status != 'REMOVED'
            """,
        ):
            rn = r.asset_group.resource_name
            names[rn] = {
                "campaign": r.campaign.name,
                "asset_group": r.asset_group.name,
                "strength": self._strength_name(r.asset_group.ad_strength),
                "locale": _locale_from_campaign(r.campaign.name),
            }
        for r in ga.search(
            customer_id=cid,
            query="""
            SELECT asset_group.resource_name, campaign.status, campaign.advertising_channel_type,
                   asset_group_asset.field_type, asset_group_asset.status
            FROM asset_group_asset
            WHERE campaign.status = 'ENABLED'
              AND campaign.advertising_channel_type = 'PERFORMANCE_MAX'
              AND asset_group_asset.status != 'REMOVED'
            """,
        ):
            ft = int(r.asset_group_asset.field_type)
            try:
                fname = self.client.enums.AssetFieldTypeEnum.AssetFieldType.Name(ft)
            except Exception:  # noqa: BLE001
                fname = str(ft)
            counts[r.asset_group.resource_name][fname] += 1
        for rn, meta in names.items():
            c = dict(counts.get(rn, {}))
            groups.append({**meta, "resource_name": rn, "counts": c})
        return groups

    def _youtube_pool(self) -> list[str]:
        ga = self.client.get_service("GoogleAdsService")
        seen: list[str] = []
        for r in ga.search(
            customer_id=self.settings.customer_id,
            query="""
            SELECT asset.resource_name, campaign.status, campaign.advertising_channel_type,
                   asset_group_asset.field_type, asset_group_asset.status
            FROM asset_group_asset
            WHERE campaign.status = 'ENABLED'
              AND campaign.advertising_channel_type = 'PERFORMANCE_MAX'
              AND asset_group_asset.field_type = YOUTUBE_VIDEO
              AND asset_group_asset.status != 'REMOVED'
            """,
        ):
            rn = r.asset.resource_name
            if rn not in seen:
                seen.append(rn)
        return seen

    def _existing_links(self, ag_rn: str) -> set[tuple[str, str]]:
        """Set of (field_type_name, asset_resource_name)."""
        ga = self.client.get_service("GoogleAdsService")
        out: set[tuple[str, str]] = set()
        # Extract asset_group id from resource name for filter
        for r in ga.search(
            customer_id=self.settings.customer_id,
            query=f"""
            SELECT asset_group.resource_name, campaign.status, campaign.advertising_channel_type,
                   asset.resource_name, asset_group_asset.field_type, asset.text_asset.text,
                   asset_group_asset.status
            FROM asset_group_asset
            WHERE campaign.status = 'ENABLED'
              AND campaign.advertising_channel_type = 'PERFORMANCE_MAX'
              AND asset_group.resource_name = '{ag_rn}'
              AND asset_group_asset.status != 'REMOVED'
            """,
        ):
            try:
                fname = self.client.enums.AssetFieldTypeEnum.AssetFieldType.Name(
                    int(r.asset_group_asset.field_type)
                )
            except Exception:  # noqa: BLE001
                fname = str(int(r.asset_group_asset.field_type))
            out.add((fname, r.asset.resource_name))
            if getattr(r.asset.text_asset, "text", None):
                out.add((fname, f"TEXT::{r.asset.text_asset.text.strip().lower()}"))
        return out

    def _create_text_asset(self, text: str, *, dry_run: bool) -> str | None:
        if dry_run:
            return f"dry_run/text/{text[:20]}"
        svc = self.client.get_service("AssetService")
        op = self.client.get_type("AssetOperation")
        op.create.name = f"PMax · {_clip(text, 40)}"
        op.create.text_asset.text = text
        return svc.mutate_assets(
            customer_id=self.settings.customer_id, operations=[op]
        ).results[0].resource_name

    def _link(self, ag_rn: str, asset_rn: str, field_type: str, *, dry_run: bool) -> None:
        if dry_run:
            return
        aga = self.client.get_service("AssetGroupAssetService")
        op = self.client.get_type("AssetGroupAssetOperation")
        op.create.asset_group = ag_rn
        op.create.asset = asset_rn
        op.create.field_type = getattr(self.client.enums.AssetFieldTypeEnum, field_type)
        aga.mutate_asset_group_assets(
            customer_id=self.settings.customer_id, operations=[op]
        )

    def apply(self, *, dry_run: bool = True) -> PMaxStrengthResult:
        before = self.snapshot()
        texts: list[str] = []
        for loc in ("UA", "EN"):
            texts.extend(_rsa_hooks(loc)[:15])
            texts.extend(list(PMAX_DESCRIPTIONS[loc]))
            texts.extend(list(LONG_HEADLINES[loc]))
        gate = brand_gate_pack(texts)
        result = PMaxStrengthResult(dry_run=dry_run, asset_groups=len(before), before=before, gate=gate)
        if not gate.get("pass"):
            result.errors.append({"error": "brand_gate_failed", "failed": gate.get("failed")})
            return result
        if not dry_run:
            assert_mutations_allowed()

        yt_pool = self._youtube_pool()
        for ag in before:
            ag_rn = ag["resource_name"]
            locale = ag["locale"]
            counts = ag.get("counts") or {}
            existing = self._existing_links(ag_rn)

            # Logos + business name
            for ftype, asset_rn in (
                ("LOGO", DEFAULT_LOGO),
                ("LANDSCAPE_LOGO", DEFAULT_LANDSCAPE_LOGO),
                ("BUSINESS_NAME", DEFAULT_BUSINESS_NAME),
            ):
                if not asset_rn:
                    continue
                if (ftype, asset_rn) in existing or counts.get(ftype, 0) >= 1:
                    continue
                try:
                    self._link(ag_rn, asset_rn, ftype, dry_run=dry_run)
                    if ftype == "BUSINESS_NAME":
                        result.business_linked += 1
                    else:
                        result.logos_linked += 1
                    existing.add((ftype, asset_rn))
                except Exception as exc:  # noqa: BLE001
                    msg = str(exc).lower()
                    if "already" in msg or "duplicate" in msg or "exists" in msg:
                        continue
                    result.errors.append(
                        {"op": f"link_{ftype}", "ag": ag["asset_group"], "error": str(exc)[:180]}
                    )

            # Images (ensure YOUR_BRAND library present)
            for ftype, asset_rn in DEFAULT_IMAGES.items():
                if (ftype, asset_rn) in existing:
                    continue
                try:
                    self._link(ag_rn, asset_rn, ftype, dry_run=dry_run)
                    result.images_linked += 1
                    existing.add((ftype, asset_rn))
                except Exception as exc:  # noqa: BLE001
                    msg = str(exc).lower()
                    if "exceeded" in msg or "already" in msg or "duplicate" in msg:
                        continue
                    result.errors.append(
                        {"op": f"link_{ftype}", "ag": ag["asset_group"], "error": str(exc)[:160]}
                    )

            # Videos — need at least 1; for Excellent aim 3
            need_vid = max(0, 3 - counts.get("YOUTUBE_VIDEO", 0))
            for vrn in yt_pool:
                if need_vid <= 0:
                    break
                if ("YOUTUBE_VIDEO", vrn) in existing:
                    continue
                try:
                    self._link(ag_rn, vrn, "YOUTUBE_VIDEO", dry_run=dry_run)
                    result.videos_linked += 1
                    existing.add(("YOUTUBE_VIDEO", vrn))
                    need_vid -= 1
                except Exception as exc:  # noqa: BLE001
                    msg = str(exc).lower()
                    if "already" in msg or "duplicate" in msg or "exceeded" in msg:
                        continue
                    result.errors.append(
                        {"op": "link_video", "ag": ag["asset_group"], "error": str(exc)[:160]}
                    )

            # Text fill
            headlines = [
                t
                for t in _rsa_hooks(locale)
                if brand_gate_text(t).get("pass") and "{" not in t
            ][:TARGET_HEADLINES]
            # ensure short headline ≤15 chars
            if not any(len(h) <= 15 for h in headlines):
                headlines = ["YOUR_BRAND", *headlines][:TARGET_HEADLINES]
            descs = [_clip(d, 90) for d in PMAX_DESCRIPTIONS.get(locale, PMAX_DESCRIPTIONS["EN"])][
                :TARGET_DESCRIPTIONS
            ]
            longs = [_clip(d, 90) for d in LONG_HEADLINES.get(locale, LONG_HEADLINES["EN"])][
                :TARGET_LONG_HEADLINES
            ]

            for ftype, texts_list, target, current_key in (
                ("HEADLINE", headlines, TARGET_HEADLINES, "HEADLINE"),
                ("DESCRIPTION", descs, TARGET_DESCRIPTIONS, "DESCRIPTION"),
                ("LONG_HEADLINE", longs, TARGET_LONG_HEADLINES, "LONG_HEADLINE"),
            ):
                have = counts.get(current_key, 0)
                need = max(0, target - have)
                for text in texts_list:
                    if need <= 0:
                        break
                    key = (ftype, f"TEXT::{text.strip().lower()}")
                    if key in existing:
                        continue
                    try:
                        rn = self._create_text_asset(text, dry_run=dry_run)
                        if not rn:
                            continue
                        result.text_created += 1
                        self._link(ag_rn, rn, ftype, dry_run=dry_run)
                        result.text_linked += 1
                        existing.add(key)
                        need -= 1
                    except Exception as exc:  # noqa: BLE001
                        msg = str(exc).lower()
                        if "already" in msg or "duplicate" in msg or "exceeded" in msg:
                            need -= 1
                            continue
                        result.errors.append(
                            {
                                "op": f"text_{ftype}",
                                "ag": ag["asset_group"],
                                "text": text[:40],
                                "error": str(exc)[:160],
                            }
                        )

        if not dry_run:
            result.after = self.snapshot()
        else:
            result.after = before
        return result
