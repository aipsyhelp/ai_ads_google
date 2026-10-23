#!/usr/bin/env python3
"""Repair hollow Search ad groups that cannot enter Google Ads auctions.

Root causes confirmed 2026-08-21:
1. ENABLED groups with zero PHRASE/EXACT keywords (Grants forbids Broad).
2. ENABLED groups with Broad-only keywords and zero RSAs (Lviv/Odesa clones).
3. EN Homepage RSAs pointing at /uk instead of /en → language mismatch QS death.
4. Purchase / YouTube marked primary_for_goal without being in Conversions.

Does NOT pause clinical/wellness keywords and does NOT touch legacy PMax/DSA
campaigns — only ENABLED Search structure on the live automated set.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import asdict, dataclass, field
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from google.protobuf import field_mask_pb2

from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.adapters.write_guard import assert_write_service
from ads_ai_agents.intelligence.brand_gate import brand_gate_text
from ads_ai_agents.intelligence.grant_keyword_normalizer import (
    BLOCKED_KEYWORD_MARKERS,
    DIRECT_OR_TREATMENT_MARKERS,
    DIRECT_SERVICE_GROUPS,
    GrantKeywordNormalizer,
)
from ads_ai_agents.intelligence.grant_reactivation_fix import SAFE_DESCS, SAFE_HEADLINES
from ads_ai_agents.intelligence import keyword_ops


DEMOTE_PRIMARY_ACTIONS = (
    "Purchase (1)",
    "YouTube channel subscriptions",
)

SEED_EXTRAS: dict[str, list[str]] = {
    "a self help guide": [
        "self help guide",
        "self help for stress",
        "daily self help tips",
    ],
    "activities for low self esteem": [
        "activities for self esteem",
        "improve self esteem activities",
        "self esteem exercises",
    ],
    "anger management": [
        "anger management tips",
        "how to manage anger",
        "calm anger techniques",
    ],
    "anxiety depression": [
        "anxiety and low mood",
        "coping with low mood",
        "anxiety support guide",
    ],
    "deal with low self esteem": [
        "deal with low self esteem",
        "cope with low self esteem",
        "build self esteem tips",
    ],
    "coping with panic": [
        "coping with panic",
        "panic feelings support",
        "calm panic tips",
    ],
    "family mental health support": [
        "family mental health support",
        "support family stress",
        "family wellness support",
    ],
    "how to tell if your burnt out": [
        "signs of burnout",
        "am i burnt out",
        "burnout warning signs",
    ],
    "why sleep is bad": [
        "why sleep is bad",
        "poor sleep stress",
        "sleep and stress tips",
    ],
    "ai for mental health": [
        "ai for mental health",
        "ai wellness support",
        "ai mental wellness",
    ],
}


@dataclass
class RepairResult:
    dry_run: bool
    normalize: dict[str, Any] = field(default_factory=dict)
    phrase_seeded: int = 0
    rsa_created: int = 0
    finals_rewritten: int = 0
    groups_paused: int = 0
    primaries_demoted: int = 0
    warnings: list[dict[str, str]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    after: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _Scoped:
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
    from ads_ai_agents.platform.product_facts import focus_cities

    upper = name.upper()
    if "/ DE /" in upper or upper.endswith(" DE"):
        return "DE"
    if "/ UA /" in upper or "/ NATIONAL" in upper:
        return "UA"
    if "/ EN /" in upper:
        return "EN"
    if any(city.upper() in upper for city in focus_cities()):  # city campaigns
        return "UA"
    return "EN"


def _safe_seed(text: str) -> bool:
    lowered = (text or "").strip().lower()
    if len(lowered.split()) < 2:
        return False
    if any(m in lowered for m in BLOCKED_KEYWORD_MARKERS):
        return False
    if any(m in lowered for m in DIRECT_OR_TREATMENT_MARKERS):
        return False
    return bool(brand_gate_text(text).get("pass"))


def _landing_for_campaign(name: str) -> str:
    locale = _locale_from_campaign(name).lower()
    if locale == "de":
        return "https://www.example.com/de"
    if locale == "ua":
        return "https://www.example.com/uk"
    return "https://www.example.com/en"


def repair(*, dry_run: bool = True) -> RepairResult:
    if not dry_run:
        assert_mutations_allowed()

    settings = GoogleAdsSettings.from_env()
    from google.ads.googleads.client import GoogleAdsClient

    raw = GoogleAdsClient.load_from_dict(settings.client_config())
    reactivate = _Scoped(raw, scope="grant_reactivate")
    recovery = _Scoped(raw, scope="grant_delivery_recovery")
    ga = reactivate.get_service("GoogleAdsService")
    cid = settings.customer_id
    result = RepairResult(dry_run=dry_run)

    # 1) Broad → Phrase + delivery floor for paused terms.
    normalizer = GrantKeywordNormalizer(settings=settings)
    norm = normalizer.apply(dry_run=dry_run)
    result.normalize = {
        k: norm.to_dict()[k]
        for k in (
            "dry_run",
            "broad_keywords_found",
            "phrase_keywords_created",
            "phrase_keywords_enabled",
            "broad_keywords_paused",
            "unsafe_keywords_paused",
            "recoverable_paused_found",
            "groups_paused",
            "errors",
            "warnings",
        )
    }

    # 2) Seed extra PHRASE keywords for still-thin EN hollow groups.
    groups: dict[int, dict[str, Any]] = {}
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.name, ad_group.id, ad_group.name, ad_group.resource_name,
               ad_group.status, ad_group.type
        FROM ad_group
        WHERE campaign.status = 'ENABLED'
          AND campaign.advertising_channel_type = 'SEARCH'
          AND ad_group.status = 'ENABLED'
        """,
    ):
        groups[row.ad_group.id] = {
            "id": row.ad_group.id,
            "name": row.ad_group.name,
            "rn": row.ad_group.resource_name,
            "campaign": row.campaign.name,
            "type": row.ad_group.type.name,
            "phrase": set(),
            "ads": 0,
        }

    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT ad_group.id, ad_group_criterion.keyword.text,
               ad_group_criterion.keyword.match_type, ad_group_criterion.status
        FROM keyword_view
        WHERE campaign.status = 'ENABLED' AND ad_group.status = 'ENABLED'
          AND ad_group_criterion.status = 'ENABLED'
          AND ad_group_criterion.keyword.match_type IN (PHRASE, EXACT)
        """,
    ):
        g = groups.get(row.ad_group.id)
        if g:
            g["phrase"].add(row.ad_group_criterion.keyword.text.strip().lower())

    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT ad_group.id, ad_group_ad.status
        FROM ad_group_ad
        WHERE campaign.status = 'ENABLED' AND ad_group.status = 'ENABLED'
          AND ad_group_ad.status = 'ENABLED'
        """,
    ):
        g = groups.get(row.ad_group.id)
        if g:
            g["ads"] += 1

    seed_ops = []
    for g in groups.values():
        if g["name"].strip().lower() in DIRECT_SERVICE_GROUPS:
            continue
        if g["type"] == "SEARCH_DYNAMIC_ADS" or g["name"].strip().upper() == "DSA":
            continue
        need = max(0, 5 - len(g["phrase"]))
        if need <= 0:
            continue
        candidates = list(SEED_EXTRAS.get(g["name"].strip().lower(), []))
        if " " in g["name"].strip():
            candidates.insert(0, g["name"].strip())
        for text in candidates:
            if need <= 0:
                break
            if not _safe_seed(text):
                continue
            if text.strip().lower() in g["phrase"]:
                continue
            op = reactivate.get_type("AdGroupCriterionOperation")
            op.create.ad_group = g["rn"]
            op.create.status = reactivate.enums.AdGroupCriterionStatusEnum.ENABLED
            op.create.keyword.text = text.strip()
            op.create.keyword.match_type = reactivate.enums.KeywordMatchTypeEnum.PHRASE
            seed_ops.append(op)
            g["phrase"].add(text.strip().lower())
            need -= 1
            result.phrase_seeded += 1

    if seed_ops and not dry_run:
        try:
            svc = reactivate.get_service("AdGroupCriterionService")
            created, _ = keyword_ops.commit(
                service=svc,
                client=reactivate,
                customer_id=cid,
                operations=seed_ops,
                warnings=result.warnings,
                rejected_indices_fn=lambda err: keyword_ops.rejected_indices(
                    reactivate, err
                ),
            )
            result.phrase_seeded = created
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"op": "seed_phrase", "error": str(exc)[:300]})

    # 3) Clone / create RSAs for groups with zero ENABLED ads.
    templates: dict[str, dict[str, Any]] = {}
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.name, ad_group.name,
               ad_group_ad.ad.responsive_search_ad.headlines,
               ad_group_ad.ad.responsive_search_ad.descriptions,
               ad_group_ad.ad.responsive_search_ad.path1,
               ad_group_ad.ad.responsive_search_ad.path2,
               ad_group_ad.ad.final_urls
        FROM ad_group_ad
        WHERE campaign.status = 'ENABLED'
          AND ad_group.status = 'ENABLED'
          AND ad_group_ad.status = 'ENABLED'
          AND ad_group_ad.ad.type = 'RESPONSIVE_SEARCH_AD'
          AND campaign.name LIKE '%Home Page / UA / National%'
        """,
    ):
        key = row.ad_group.name.strip().lower()
        if key in templates:
            continue
        rsa = row.ad_group_ad.ad.responsive_search_ad
        templates[key] = {
            "headlines": [h.text for h in rsa.headlines][:15],
            "descriptions": [d.text for d in rsa.descriptions][:4],
            "path1": rsa.path1 or "uk",
            "path2": rsa.path2 or "help",
        }

    for g in groups.values():
        if g["ads"] > 0:
            continue
        if g["type"] == "SEARCH_DYNAMIC_ADS" or g["name"].strip().upper() == "DSA":
            continue
        locale = _locale_from_campaign(g["campaign"])
        landing = _landing_for_campaign(g["campaign"])
        tmpl = templates.get(g["name"].strip().lower())
        if tmpl:
            headlines = tmpl["headlines"]
            descriptions = tmpl["descriptions"]
            path1 = tmpl["path1"]
            path2 = tmpl["path2"]
        else:
            headlines = [
                h
                for h in SAFE_HEADLINES.get(locale, SAFE_HEADLINES["EN"])
                if brand_gate_text(h).get("pass")
            ][:15]
            descriptions = list(SAFE_DESCS.get(locale, SAFE_DESCS["EN"]))[:4]
            path1 = "uk" if locale == "UA" else "en"
            path2 = "help"
        if len(headlines) < 3 or len(descriptions) < 2:
            result.warnings.append(
                {
                    "op": "rsa",
                    "ad_group": g["name"],
                    "warning": "insufficient headlines/descriptions",
                }
            )
            continue
        for variant in range(2):
            result.rsa_created += 1
            if dry_run:
                continue
            try:
                op = reactivate.get_type("AdGroupAdOperation")
                aga = op.create
                aga.ad_group = g["rn"]
                aga.status = reactivate.enums.AdGroupAdStatusEnum.ENABLED
                aga.ad.final_urls.append(landing)
                hs = headlines if variant == 0 else list(reversed(headlines))
                for text in hs[:15]:
                    asset = reactivate.get_type("AdTextAsset")
                    asset.text = text[:30]
                    aga.ad.responsive_search_ad.headlines.append(asset)
                for text in descriptions[:4]:
                    asset = reactivate.get_type("AdTextAsset")
                    asset.text = text[:90]
                    aga.ad.responsive_search_ad.descriptions.append(asset)
                aga.ad.responsive_search_ad.path1 = (path1 or "help")[:15]
                aga.ad.responsive_search_ad.path2 = (path2 or "care")[:15]
                reactivate.get_service("AdGroupAdService").mutate_ad_group_ads(
                    customer_id=cid, operations=[op]
                )
                g["ads"] += 1
            except Exception as exc:  # noqa: BLE001
                result.errors.append(
                    {
                        "op": "create_rsa",
                        "ad_group": g["name"][:40],
                        "error": str(exc)[:250],
                    }
                )

    # 4) Rewrite EN Homepage finals from /uk → /en.
    ad_ops = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.name, ad_group.name,
               ad_group_ad.ad.resource_name, ad_group_ad.ad.final_urls
        FROM ad_group_ad
        WHERE campaign.status = 'ENABLED'
          AND campaign.name LIKE '%Home Page / EN%'
          AND ad_group_ad.status = 'ENABLED'
          AND ad_group_ad.ad.type = 'RESPONSIVE_SEARCH_AD'
        """,
    ):
        urls = list(row.ad_group_ad.ad.final_urls or [])
        if not any("/uk" in u for u in urls):
            continue
        result.finals_rewritten += 1
        if dry_run:
            continue
        op = reactivate.get_type("AdOperation")
        op.update.resource_name = row.ad_group_ad.ad.resource_name
        op.update.final_urls.append("https://www.example.com/en")
        op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["final_urls"]))
        ad_ops.append(op)
    if ad_ops and not dry_run:
        try:
            reactivate.get_service("AdService").mutate_ads(
                customer_id=cid, operations=ad_ops
            )
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"op": "rewrite_finals", "error": str(exc)[:300]})

    # 5) Pause DSA shells on Search campaigns (no keywords, confuse inventory).
    pause_ops = []
    for g in groups.values():
        if g["type"] == "SEARCH_DYNAMIC_ADS" or g["name"].strip().upper() == "DSA":
            result.groups_paused += 1
            if dry_run:
                continue
            op = reactivate.get_type("AdGroupOperation")
            op.update.resource_name = g["rn"]
            op.update.status = reactivate.enums.AdGroupStatusEnum.PAUSED
            op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["status"]))
            pause_ops.append(op)
    if pause_ops and not dry_run:
        try:
            reactivate.get_service("AdGroupService").mutate_ad_groups(
                customer_id=cid, operations=pause_ops
            )
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"op": "pause_dsa", "error": str(exc)[:300]})

    # 6) Demote junk primary conversion actions.
    demote_ops = []
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT conversion_action.resource_name, conversion_action.name,
               conversion_action.primary_for_goal
        FROM conversion_action
        WHERE conversion_action.status != 'REMOVED'
        """,
    ):
        if row.conversion_action.name not in DEMOTE_PRIMARY_ACTIONS:
            continue
        if not row.conversion_action.primary_for_goal:
            continue
        result.primaries_demoted += 1
        if dry_run:
            continue
        op = recovery.get_type("ConversionActionOperation")
        op.update.resource_name = row.conversion_action.resource_name
        op.update.primary_for_goal = False
        op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["primary_for_goal"]))
        demote_ops.append(op)
    if demote_ops and not dry_run:
        try:
            recovery.get_service("ConversionActionService").mutate_conversion_actions(
                customer_id=cid, operations=demote_ops
            )
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"op": "demote_primary", "error": str(exc)[:300]})

    result.after = _audit_after(ga, cid)
    return result


def _audit_after(ga: Any, cid: str) -> dict[str, Any]:
    ag: dict[int, dict[str, Any]] = {}
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.name, ad_group.id, ad_group.name, ad_group.status
        FROM ad_group
        WHERE campaign.status = 'ENABLED' AND ad_group.status = 'ENABLED'
          AND campaign.advertising_channel_type = 'SEARCH'
        """,
    ):
        ag[row.ad_group.id] = {
            "campaign": row.campaign.name,
            "name": row.ad_group.name,
            "phrase": 0,
            "ads": 0,
        }
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT ad_group.id, ad_group_criterion.keyword.match_type
        FROM keyword_view
        WHERE campaign.status = 'ENABLED' AND ad_group.status = 'ENABLED'
          AND ad_group_criterion.status = 'ENABLED'
          AND ad_group_criterion.keyword.match_type IN (PHRASE, EXACT)
        """,
    ):
        if row.ad_group.id in ag:
            ag[row.ad_group.id]["phrase"] += 1
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT ad_group.id FROM ad_group_ad
        WHERE campaign.status = 'ENABLED' AND ad_group.status = 'ENABLED'
          AND ad_group_ad.status = 'ENABLED'
        """,
    ):
        if row.ad_group.id in ag:
            ag[row.ad_group.id]["ads"] += 1
    no_kw = [d for d in ag.values() if d["phrase"] == 0]
    no_ads = [d for d in ag.values() if d["ads"] == 0]
    ok = [d for d in ag.values() if d["phrase"] > 0 and d["ads"] > 0]
    return {
        "enabled_groups": len(ag),
        "servable_groups": len(ok),
        "no_phrase_keywords": [
            f"{d['campaign'][-35:]} | {d['name']}" for d in no_kw[:20]
        ],
        "no_ads": [f"{d['campaign'][-35:]} | {d['name']}" for d in no_ads[:20]],
    }


def pause_remaining_broad(*, batch_size: int = 40, sleep_s: float = 1.5) -> dict[str, Any]:
    """Pause ENABLED Broad keywords left after a quota-interrupted normalize.

    Explorer-access developer tokens often hit 'Number of operations' limits;
    call this again after the Retry-After window (~2h).
    """
    import time

    assert_mutations_allowed()
    settings = GoogleAdsSettings.from_env()
    from google.ads.googleads.client import GoogleAdsClient

    raw = GoogleAdsClient.load_from_dict(settings.client_config())
    client = _Scoped(raw, scope="grant_reactivate")
    ga = client.get_service("GoogleAdsService")
    svc = client.get_service("AdGroupCriterionService")
    cid = settings.customer_id
    broad = [
        row.ad_group_criterion.resource_name
        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT ad_group_criterion.resource_name
            FROM keyword_view
            WHERE campaign.status = 'ENABLED'
              AND ad_group.status = 'ENABLED'
              AND ad_group_criterion.status = 'ENABLED'
              AND ad_group_criterion.keyword.match_type = BROAD
            """,
        )
    ]
    paused = 0
    error = None
    for i in range(0, len(broad), batch_size):
        chunk = broad[i : i + batch_size]
        ops = []
        for rn in chunk:
            op = client.get_type("AdGroupCriterionOperation")
            op.update.resource_name = rn
            op.update.status = client.enums.AdGroupCriterionStatusEnum.PAUSED
            op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["status"]))
            ops.append(op)
        try:
            svc.mutate_ad_group_criteria(customer_id=cid, operations=ops)
            paused += len(ops)
            time.sleep(sleep_s)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)[:300]
            break
    return {
        "broad_found": len(broad),
        "paused": paused,
        "remaining": max(0, len(broad) - paused),
        "error": error,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply mutations")
    parser.add_argument(
        "--pause-broad-only",
        action="store_true",
        help="Only pause remaining ENABLED Broad keywords (quota resume).",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.pause_broad_only:
        if not args.apply:
            print("Pass --apply with --pause-broad-only")
            return 2
        payload = pause_remaining_broad()
        if args.json:
            import json

            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(
                f"Pause Broad: found={payload['broad_found']} "
                f"paused={payload['paused']} remaining={payload['remaining']}"
            )
            if payload["error"]:
                print(f"  error: {payload['error']}")
        return 1 if payload.get("error") else 0

    result = repair(dry_run=not args.apply)
    payload = result.to_dict()
    if args.json:
        import json

        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Repair hollow ad groups [{'APPLY' if args.apply else 'DRY-RUN'}]")
        print(f"  normalize: {payload['normalize']}")
        print(f"  phrase_seeded={result.phrase_seeded}")
        print(f"  rsa_created={result.rsa_created}")
        print(f"  finals_rewritten={result.finals_rewritten}")
        print(f"  groups_paused={result.groups_paused}")
        print(f"  primaries_demoted={result.primaries_demoted}")
        print(f"  warnings={len(result.warnings)} errors={len(result.errors)}")
        for err in result.errors[:12]:
            print(f"    ERR {err}")
        print(f"  after: {result.after}")
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
