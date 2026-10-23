#!/usr/bin/env python3
"""Unblock Search delivery on the live automated campaigns.

Root cause found 2026-08-20:
- Campaigns are ENABLED + MAXIMIZE_CONVERSIONS + SERVING, but almost all
  historically winning keywords/ad groups are PAUSED.
- Currently ENABLED keywords have zero impressions in Jul–Aug, so auctions
  have nothing competitive to bid on → TODAY/YESTERDAY impressions stay at 0.

This script only touches ENABLED Search campaigns. It does NOT unpause old
Performance Max / DSA / legacy campaigns. Direct-service groups (therapist,
psychologist, counseling) stay paused for Ad Grants policy.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from google.protobuf import field_mask_pb2

from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.adapters.write_guard import assert_write_service
from ads_ai_agents.intelligence.grant_keyword_normalizer import (
    BLOCKED_KEYWORD_MARKERS,
    DIRECT_OR_TREATMENT_MARKERS,
    DIRECT_SERVICE_GROUPS,
)


DIRECT_GROUP_MARKERS = (
    "counsel",
    "therapist",
    "psycholog",
    "психолог",
    "психотерап",
    "консультац",
)


@dataclass
class UnblockResult:
    dry_run: bool
    keywords_to_enable: list[dict[str, Any]] = field(default_factory=list)
    groups_to_enable: list[dict[str, Any]] = field(default_factory=list)
    keywords_updated: int = 0
    groups_updated: int = 0
    skipped: list[dict[str, str]] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _Scoped:
    def __init__(self, client: Any) -> None:
        self._client = client

    def get_service(self, name: str):
        assert_write_service(name, scope="grant_keyword_normalize")
        return self._client.get_service(name)

    def get_type(self, name: str):
        return self._client.get_type(name)

    @property
    def enums(self):
        return self._client.enums

    def __getattr__(self, item: str):
        return getattr(self._client, item)


def _safe_text(text: str) -> bool:
    lowered = (text or "").strip().lower()
    if len(lowered.split()) < 2:
        return False
    if any(m in lowered for m in BLOCKED_KEYWORD_MARKERS):
        return False
    if any(m in lowered for m in DIRECT_OR_TREATMENT_MARKERS):
        return False
    return True


def _safe_group(name: str) -> bool:
    lowered = (name or "").strip().lower()
    if lowered in DIRECT_SERVICE_GROUPS:
        return False
    return not any(m in lowered for m in DIRECT_GROUP_MARKERS)


def unblock(*, dry_run: bool = True) -> UnblockResult:
    if not dry_run:
        assert_mutations_allowed()

    settings = GoogleAdsSettings.from_env()
    from google.ads.googleads.client import GoogleAdsClient

    raw = GoogleAdsClient.load_from_dict(settings.client_config())
    client = _Scoped(raw)
    ga = client.get_service("GoogleAdsService")
    cid = settings.customer_id
    result = UnblockResult(dry_run=dry_run)

    window_end = date.today()
    window_start = window_end - timedelta(days=120)

    # Historical volume for paused keywords.
    hist: dict[str, dict[str, Any]] = {}
    for row in ga.search(
        customer_id=cid,
        query=f"""
        SELECT campaign.id, campaign.name, campaign.status,
               ad_group.id, ad_group.name, ad_group.status, ad_group.resource_name,
               ad_group_criterion.resource_name, ad_group_criterion.keyword.text,
               ad_group_criterion.status, metrics.impressions, metrics.clicks
        FROM keyword_view
        WHERE campaign.status = 'ENABLED'
          AND campaign.advertising_channel_type = 'SEARCH'
          AND segments.date BETWEEN '{window_start.isoformat()}'
                                AND '{window_end.isoformat()}'
        """,
    ):
        rn = row.ad_group_criterion.resource_name
        item = hist.setdefault(
            rn,
            {
                "resource_name": rn,
                "text": row.ad_group_criterion.keyword.text,
                "kw_status": row.ad_group_criterion.status.name,
                "ag_status": row.ad_group.status.name,
                "ag_rn": row.ad_group.resource_name,
                "ag_name": row.ad_group.name,
                "campaign": row.campaign.name,
                "impressions": 0,
                "clicks": 0,
            },
        )
        item["impressions"] += int(row.metrics.impressions)
        item["clicks"] += int(row.metrics.clicks)
        item["kw_status"] = row.ad_group_criterion.status.name
        item["ag_status"] = row.ad_group.status.name

    # Also inventory current paused groups with live keywords (even without hist).
    paused_groups: dict[str, dict[str, Any]] = {}
    for row in ga.search(
        customer_id=cid,
        query="""
        SELECT campaign.name, ad_group.resource_name, ad_group.name, ad_group.status,
               ad_group_criterion.status, ad_group_criterion.keyword.text
        FROM keyword_view
        WHERE campaign.status = 'ENABLED'
          AND campaign.advertising_channel_type = 'SEARCH'
          AND ad_group.status = 'PAUSED'
        """,
    ):
        ag = paused_groups.setdefault(
            row.ad_group.resource_name,
            {
                "resource_name": row.ad_group.resource_name,
                "name": row.ad_group.name,
                "campaign": row.campaign.name,
                "kw_enabled": 0,
                "hist_imp": 0,
            },
        )
        if row.ad_group_criterion.status.name == "ENABLED":
            ag["kw_enabled"] += 1

    for item in hist.values():
        if item["ag_rn"] in paused_groups:
            paused_groups[item["ag_rn"]]["hist_imp"] += item["impressions"]

    # 1) Enable paused keywords with evidence, in any group of ENABLED campaigns.
    for item in sorted(hist.values(), key=lambda x: -x["impressions"]):
        if item["kw_status"] != "PAUSED":
            continue
        if item["impressions"] < 5 and item["clicks"] < 1:
            continue
        if not _safe_text(item["text"]):
            result.skipped.append(
                {
                    "op": "keyword",
                    "reason": "unsafe",
                    "text": item["text"][:60],
                    "group": item["ag_name"][:40],
                }
            )
            continue
        if not _safe_group(item["ag_name"]):
            result.skipped.append(
                {
                    "op": "keyword",
                    "reason": "direct_service_group",
                    "text": item["text"][:60],
                    "group": item["ag_name"][:40],
                }
            )
            continue
        result.keywords_to_enable.append(
            {
                "resource_name": item["resource_name"],
                "text": item["text"],
                "group": item["ag_name"],
                "campaign": item["campaign"],
                "impressions": item["impressions"],
                "clicks": item["clicks"],
                "ag_rn": item["ag_rn"],
                "ag_status": item["ag_status"],
            }
        )

    # 2) Enable paused wellness groups that either have history or live keywords.
    groups_needed = {k["ag_rn"] for k in result.keywords_to_enable if k["ag_status"] == "PAUSED"}
    for ag in paused_groups.values():
        if not _safe_group(ag["name"]):
            result.skipped.append(
                {
                    "op": "group",
                    "reason": "direct_service_group",
                    "group": ag["name"][:50],
                    "campaign": ag["campaign"][:40],
                }
            )
            continue
        worth = (
            ag["resource_name"] in groups_needed
            or ag["hist_imp"] >= 20
            or ag["kw_enabled"] >= 5
        )
        if not worth:
            continue
        result.groups_to_enable.append(
            {
                "resource_name": ag["resource_name"],
                "name": ag["name"],
                "campaign": ag["campaign"],
                "hist_imp": ag["hist_imp"],
                "kw_enabled": ag["kw_enabled"],
            }
        )

    if dry_run:
        return result

    # Apply groups first so keywords land in live groups.
    if result.groups_to_enable:
        ops = []
        for item in result.groups_to_enable:
            op = client.get_type("AdGroupOperation")
            op.update.resource_name = item["resource_name"]
            op.update.status = client.enums.AdGroupStatusEnum.ENABLED
            op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["status"]))
            ops.append(op)
        try:
            resp = client.get_service("AdGroupService").mutate_ad_groups(
                customer_id=cid, operations=ops
            )
            result.groups_updated = len(resp.results)
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"op": "enable_groups", "error": str(exc)[:400]})

    if result.keywords_to_enable:
        ops = []
        for item in result.keywords_to_enable:
            op = client.get_type("AdGroupCriterionOperation")
            op.update.resource_name = item["resource_name"]
            op.update.status = client.enums.AdGroupCriterionStatusEnum.ENABLED
            op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["status"]))
            ops.append(op)
        try:
            resp = client.get_service("AdGroupCriterionService").mutate_ad_group_criteria(
                customer_id=cid, operations=ops
            )
            result.keywords_updated = len(resp.results)
        except Exception as exc:  # noqa: BLE001
            result.errors.append({"op": "enable_keywords", "error": str(exc)[:400]})

    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = unblock(dry_run=not args.apply)
    d = result.to_dict()
    print(f"dry_run={d['dry_run']}")
    print(f"groups_to_enable={len(d['groups_to_enable'])} keywords_to_enable={len(d['keywords_to_enable'])}")
    print(f"groups_updated={d['groups_updated']} keywords_updated={d['keywords_updated']}")
    print("\n--- GROUPS ---")
    for g in d["groups_to_enable"][:40]:
        print(
            f"  hist={g['hist_imp']:>5} kw_en={g['kw_enabled']:>3}  "
            f"{g['campaign'][:40]} | {g['name']}"
        )
    print("\n--- KEYWORDS (top by impressions) ---")
    for k in sorted(d["keywords_to_enable"], key=lambda x: -x["impressions"])[:40]:
        print(
            f"  imp={k['impressions']:>5} clk={k['clicks']:>3} ag={k['ag_status']:<7} "
            f"{k['campaign'][:28]} | {k['group'][:28]} | {k['text'][:40]}"
        )
    print(f"\nskipped={len(d['skipped'])} errors={d['errors']}")
    for e in d["errors"]:
        print(f"  ERROR {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
