"""AUTONOMOUS_LIVE asset publisher — scrub clinical RSA, inject hooks, rotate PMax."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.adapters.write_guard import assert_write_service
from ads_ai_agents.intelligence.brand_gate import brand_gate_pack, brand_gate_text
from ads_ai_agents.logging_utils import configure_logging
from ads_ai_agents.platform.account_ids import asset_resource
from ads_ai_agents.platform.agents.creative_lab import CTA_SEEDS, HERO_SEEDS

logger = configure_logging()

# Library image assets come from config/account_ids.json ("assets"); empty entries are skipped.
DEFAULT_PMAX_ASSETS = {
    key: rn
    for key, rn in {
        "MARKETING_IMAGE": asset_resource("marketing_image"),
        "SQUARE_MARKETING_IMAGE": asset_resource("square_marketing_image"),
        "PORTRAIT_MARKETING_IMAGE": asset_resource("portrait_marketing_image"),
    }.items()
    if rn
}

MAX_RSA_HEADLINES = 15
# Hard remove from RSA (Grant + brand). Disclaimers like "no diagnosis" kept via brand_gate.
CLINICAL_SCRUB = (
    "психолог",
    "психіатр",
    "діагноз",
    "терап",
    "лікар",
    "suicide",
    "therapist",
    "psycholog",
    "psychiatr",
    "diagnos",
    "medication",
    "prescription",
    "clinical",
    "treatment",
    "disorder",
    "ptsd",
)


@dataclass
class AssetPublishResult:
    dry_run: bool
    gate: dict[str, Any] = field(default_factory=dict)
    rsa_attempted: int = 0
    rsa_patched: int = 0
    rsa_scrubbed_headlines: int = 0
    rsa_skipped: int = 0
    rsa_errors: list[dict[str, Any]] = field(default_factory=list)
    pmax_removed: int = 0
    pmax_linked: int = 0
    pmax_skipped: int = 0
    pmax_errors: list[dict[str, Any]] = field(default_factory=list)
    headlines_used: list[str] = field(default_factory=list)

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


def _is_clinical_headline(text: str) -> bool:
    low = (text or "").lower()
    # Keep wellness disclaimers
    if brand_gate_text(text).get("pass") and any(
        x in low for x in ("no diagnos", "ohne diagnos", "без діагн", "without diagnos")
    ):
        return False
    return any(m in low for m in CLINICAL_SCRUB)


def _clip(text: str, n: int = 30) -> str:
    t = (text or "").strip()
    if len(t) <= n:
        return t
    return t[: n - 1].rstrip() + "…"


def _locale_from_name(name: str) -> str:
    upper = (name or "").upper()
    return "UA" if "/ UA /" in upper else "EN"


class GoogleAdsAssetPublisher:
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

    def _hook_headlines(self, *, locale: str) -> list[str]:
        texts: list[str] = []
        texts.extend(list(HERO_SEEDS.get(locale, HERO_SEEDS.get("EN", ())))[:3])
        texts.extend(list(CTA_SEEDS.get(locale, CTA_SEEDS.get("EN", ())))[:2])
        out: list[str] = []
        for t in texts:
            t = _clip(t)
            if t and t not in out:
                out.append(t)
        return out[:10]

    def publish_gated_assets(
        self,
        *,
        dry_run: bool = True,
        max_rsa: int = 80,
        link_pmax: bool = True,
        scrub_clinical: bool = True,
        rotate_pmax: bool = True,
        locales: tuple[str, ...] = ("UA", "EN"),
    ) -> AssetPublishResult:
        hooks_by_locale = {locale: self._hook_headlines(locale=locale) for locale in locales}
        headlines = [text for pack in hooks_by_locale.values() for text in pack]
        gate = brand_gate_pack(headlines)
        result = AssetPublishResult(dry_run=dry_run, gate=gate, headlines_used=list(gate.get("passed_texts") or []))
        if not gate.get("pass"):
            logger.warning("Brand gate failed — asset publish aborted")
            return result
        if not gate.get("passed_texts"):
            return result
        if not dry_run:
            assert_mutations_allowed()

        a, p, scrub, sk, err = self._replace_rsa(
            hooks_by_locale,
            dry_run=dry_run,
            max_rsa=max_rsa,
            scrub_clinical=scrub_clinical,
        )
        result.rsa_attempted = a
        result.rsa_patched = p
        result.rsa_scrubbed_headlines = scrub
        result.rsa_skipped = sk
        result.rsa_errors = err

        if link_pmax:
            removed, linked, skipped, errors = self._rotate_pmax(dry_run=dry_run, rotate=rotate_pmax)
            result.pmax_removed = removed
            result.pmax_linked = linked
            result.pmax_skipped = skipped
            result.pmax_errors = errors
        return result

    def _replace_rsa(
        self,
        hooks_by_locale: dict[str, list[str]],
        *,
        dry_run: bool,
        max_rsa: int,
        scrub_clinical: bool,
    ) -> tuple[int, int, int, int, list[dict[str, Any]]]:
        from google.protobuf import field_mask_pb2

        ga = self.client.get_service("GoogleAdsService")
        cid = self.settings.customer_id
        rows = list(
            ga.search(
                customer_id=cid,
                query="""
                SELECT ad_group_ad.ad.id, ad_group_ad.ad.responsive_search_ad.headlines,
                       campaign.name, ad_group.name
                FROM ad_group_ad
                WHERE campaign.status = 'ENABLED' AND ad_group.status = 'ENABLED'
                  AND ad_group_ad.status = 'ENABLED'
                  AND ad_group_ad.ad.type = 'RESPONSIVE_SEARCH_AD'
                LIMIT 250
                """,
            )
        )
        attempted = patched = scrubbed = skipped = 0
        errors: list[dict[str, Any]] = []
        ad_service = None if dry_run else self.client.get_service("AdService")

        for row in rows:
            if attempted >= max_rsa:
                break
            hooks = hooks_by_locale.get(_locale_from_name(row.campaign.name)) or hooks_by_locale.get("EN") or []
            existing = [(h.text or "").strip() for h in (row.ad_group_ad.ad.responsive_search_ad.headlines or [])]
            customizers = [t for t in existing if "{" in t and "}" in t and t.count("{") == t.count("}")]
            kept = []
            for t in existing:
                if t in customizers:
                    continue  # re-add first below
                if scrub_clinical and _is_clinical_headline(t):
                    scrubbed += 1
                    continue
                if brand_gate_text(t).get("pass") and t not in kept:
                    kept.append(t)
            merged: list[str] = []
            # Customizers must stay intact (Google rejects unpaired braces)
            for t in customizers:
                if t not in merged:
                    merged.append(t)
            for t in hooks + kept:
                if "{" in t or "}" in t:
                    continue  # never invent / truncate customizer tags
                t = _clip(t)
                if t and t not in merged and not _is_clinical_headline(t):
                    merged.append(t)
                if len(merged) >= MAX_RSA_HEADLINES:
                    break
            # If clinical was inside a customizer keyword, drop that customizer
            cleaned_custom = []
            for t in merged:
                if "{" in t and _is_clinical_headline(t):
                    scrubbed += 1
                    continue
                cleaned_custom.append(t)
            merged = cleaned_custom
            if len(merged) < 3:
                skipped += 1
                continue
            if set(merged) == set(existing) and len(merged) == len(existing):
                skipped += 1
                continue
            attempted += 1
            if dry_run:
                patched += 1
                continue
            try:
                ad_op = self.client.get_type("AdOperation")
                ad_op.update.resource_name = f"customers/{cid}/ads/{row.ad_group_ad.ad.id}"
                rsa = ad_op.update.responsive_search_ad
                del rsa.headlines[:]
                for text in merged:
                    asset = self.client.get_type("AdTextAsset")
                    asset.text = text
                    rsa.headlines.append(asset)
                ad_op.update_mask.CopyFrom(
                    field_mask_pb2.FieldMask(paths=["responsive_search_ad.headlines"])
                )
                ad_service.mutate_ads(customer_id=cid, operations=[ad_op])
                patched += 1
            except Exception as exc:  # noqa: BLE001
                errors.append({"campaign": row.campaign.name, "error": str(exc)[:240]})
        return attempted, patched, scrubbed, skipped, errors

    def _rotate_pmax(self, *, dry_run: bool, rotate: bool) -> tuple[int, int, int, list[dict[str, Any]]]:
        ga = self.client.get_service("GoogleAdsService")
        cid = self.settings.customer_id
        aga = None if dry_run else self.client.get_service("AssetGroupAssetService")
        FT = self.client.enums.AssetFieldTypeEnum
        removed = linked = skipped = 0
        errors: list[dict[str, Any]] = []

        # Per asset group: count squares; remove non YOUR_BRAND if > 10
        if rotate:
            by_ag: dict[str, list[Any]] = {}
            for r in ga.search(
                customer_id=cid,
                query="""
                SELECT asset_group.resource_name, asset_group.name,
                       asset_group_asset.resource_name, asset.id, asset.name
                FROM asset_group_asset
                WHERE campaign.status = 'ENABLED' AND asset_group.status = 'ENABLED'
                  AND asset_group_asset.field_type = SQUARE_MARKETING_IMAGE
                  AND asset_group_asset.status != 'REMOVED'
                """,
            ):
                by_ag.setdefault(r.asset_group.resource_name, []).append(r)
            for ag, items in by_ag.items():
                victims = [x for x in items if not (x.asset.name or "").startswith("YOUR_BRAND")]
                excess = max(0, len(items) - 10)
                for r in victims[:excess]:
                    if dry_run:
                        removed += 1
                        continue
                    try:
                        op = self.client.get_type("AssetGroupAssetOperation")
                        op.remove = r.asset_group_asset.resource_name
                        aga.mutate_asset_group_assets(customer_id=cid, operations=[op])
                        removed += 1
                    except Exception as exc:  # noqa: BLE001
                        errors.append({"op": "remove", "error": str(exc)[:160]})

        ags = list(
            ga.search(
                customer_id=cid,
                query="""
                SELECT asset_group.resource_name, asset_group.name
                FROM asset_group
                WHERE campaign.status = 'ENABLED' AND asset_group.status = 'ENABLED'
                """,
            )
        )
        existing: set[tuple[str, str]] = set()
        for r in ga.search(
            customer_id=cid,
            query="""
            SELECT asset_group.resource_name, asset.resource_name, asset.name
            FROM asset_group_asset
            WHERE campaign.status = 'ENABLED' AND asset_group.status = 'ENABLED'
              AND asset_group_asset.status != 'REMOVED'
            """,
        ):
            if (r.asset.name or "").startswith("YOUR_BRAND"):
                existing.add((r.asset_group.resource_name, r.asset.resource_name))

        for row in ags:
            for ftype, asset_rn in DEFAULT_PMAX_ASSETS.items():
                key = (row.asset_group.resource_name, asset_rn)
                if key in existing:
                    skipped += 1
                    continue
                if dry_run:
                    linked += 1
                    continue
                try:
                    op = self.client.get_type("AssetGroupAssetOperation")
                    op.create.asset_group = row.asset_group.resource_name
                    op.create.asset = asset_rn
                    op.create.field_type = getattr(FT, ftype)
                    aga.mutate_asset_group_assets(customer_id=cid, operations=[op])
                    linked += 1
                except Exception as exc:  # noqa: BLE001
                    msg = str(exc).lower()
                    if "exceeded" in msg or "already" in msg:
                        skipped += 1
                    else:
                        errors.append({"asset_group": row.asset_group.name, "error": str(exc)[:160]})
        return removed, linked, skipped, errors
