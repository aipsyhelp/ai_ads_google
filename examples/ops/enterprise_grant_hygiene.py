"""Enterprise Grant hygiene: pause clinical keywords, add safe synonyms, scrub RSA, fix goals.

Does NOT delete Google AI sitelinks. Does NOT pause mid-funnel symptom keywords
(depression/anxiety) wholesale — those remain as demand; we only neutralize
psychologist/therapy/treatment wording and clinical RSA copy.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

# Exact keyword text → safe PHRASE replacement (same ad group).
SAFE_KEYWORD_REPLACEMENTS: dict[str, str] = {
    "депресія психологія": "депресія самопідтримка",
    "допомога психолога при депресії": "онлайн підтримка при депресії",
    "професійне вигорання психолога": "професійне вигорання підтримка",
    "професійне вигорання психологія": "професійне вигорання велнес",
    "психологічне вигорання": "емоційне вигорання",
    "психологічне виснаження": "емоційне виснаження",
    "тривожність психологія": "тривожність самопідтримка",
    "апатія лікування": "апатія самопідтримка",
    "лікування депресії": "підтримка при депресії",
    "панічна атака лікування": "панічна атака заземлення",
    "синдром професійного вигорання у медичних працівників": "синдром вигорання у працівників",
}

# Pause if any of these stems appear (HIGH clinical / professional-help intent).
HIGH_STEMS = (
    "психолог",
    "психотерап",
    "психіатр",
    "лікуван",
    "медичн",
    "psycholog",
    "psychiatr",
    "psychother",
    "therapist",
    "counsellor",
    "counselor",
    "counseling",
    "counselling",
)


def _load_env() -> None:
    for raw in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _hits(text: str) -> list[str]:
    low = (text or "").lower()
    return [s for s in HIGH_STEMS if s in low]


def remediate_keywords(client: Any, customer_id: str, *, dry_run: bool) -> dict[str, Any]:
    ga = client.get_service("GoogleAdsService")
    to_pause: list[dict[str, str]] = []
    seen_rn: set[str] = set()
    for row in ga.search(
        customer_id=customer_id,
        query="""
        SELECT campaign.name, campaign.status, ad_group.name, ad_group.resource_name,
               ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type,
               ad_group_criterion.status, ad_group_criterion.resource_name
        FROM keyword_view
        WHERE campaign.status = 'ENABLED'
          AND ad_group.status = 'ENABLED'
          AND ad_group_criterion.status = 'ENABLED'
        """,
    ):
        text = row.ad_group_criterion.keyword.text
        rn = row.ad_group_criterion.resource_name
        if rn in seen_rn:
            continue
        hit = _hits(text)
        if not hit:
            continue
        seen_rn.add(rn)
        to_pause.append(
            {
                "rn": rn,
                "keyword": text,
                "hits": ",".join(hit),
                "campaign": row.campaign.name,
                "ad_group": row.ad_group.name,
                "ad_group_rn": row.ad_group.resource_name,
                "replacement": SAFE_KEYWORD_REPLACEMENTS.get(text.strip().lower())
                or SAFE_KEYWORD_REPLACEMENTS.get(text.strip()),
            }
        )

    paused = 0
    created = 0
    errors: list[dict[str, str]] = []
    if to_pause and not dry_run:
        crit_svc = client.get_service("AdGroupCriterionService")
        # Pause in batches
        for i in range(0, len(to_pause), 50):
            batch = to_pause[i : i + 50]
            ops = []
            from google.protobuf import field_mask_pb2

            for item in batch:
                op = client.get_type("AdGroupCriterionOperation")
                op.update.resource_name = item["rn"]
                op.update.status = client.enums.AdGroupCriterionStatusEnum.PAUSED
                op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["status"]))
                ops.append(op)
            try:
                crit_svc.mutate_ad_group_criteria(customer_id=customer_id, operations=ops)
                paused += len(batch)
            except Exception as exc:  # noqa: BLE001
                errors.append({"op": "pause_keywords", "error": str(exc)[:300]})

        # Create replacements (unique per ad group + text)
        existing: set[tuple[str, str]] = set()
        for row in ga.search(
            customer_id=customer_id,
            query="""
            SELECT ad_group.resource_name, ad_group_criterion.keyword.text,
                   ad_group_criterion.status
            FROM keyword_view
            WHERE campaign.status = 'ENABLED' AND ad_group.status = 'ENABLED'
              AND ad_group_criterion.status != 'REMOVED'
            """,
        ):
            existing.add(
                (
                    row.ad_group.resource_name,
                    (row.ad_group_criterion.keyword.text or "").strip().lower(),
                )
            )

        create_ops = []
        planned_create: list[dict[str, str]] = []
        for item in to_pause:
            repl = item.get("replacement")
            if not repl:
                continue
            key = (item["ad_group_rn"], repl.lower())
            if key in existing:
                continue
            existing.add(key)
            op = client.get_type("AdGroupCriterionOperation")
            crit = op.create
            crit.ad_group = item["ad_group_rn"]
            crit.status = client.enums.AdGroupCriterionStatusEnum.ENABLED
            crit.keyword.text = repl
            crit.keyword.match_type = client.enums.KeywordMatchTypeEnum.PHRASE
            create_ops.append(op)
            planned_create.append({"ad_group": item["ad_group"], "keyword": repl})
        for i in range(0, len(create_ops), 50):
            batch = create_ops[i : i + 50]
            try:
                crit_svc.mutate_ad_group_criteria(customer_id=customer_id, operations=batch)
                created += len(batch)
            except Exception as exc:  # noqa: BLE001
                errors.append({"op": "create_safe_keywords", "error": str(exc)[:300]})
    else:
        paused = len(to_pause) if dry_run else 0
        created = sum(1 for x in to_pause if x.get("replacement")) if dry_run else 0
        planned_create = [
            {"ad_group": x["ad_group"], "keyword": x["replacement"]}
            for x in to_pause
            if x.get("replacement")
        ]

    return {
        "found": len(to_pause),
        "paused": paused,
        "replacements_created": created,
        "samples": to_pause[:30],
        "planned_create": planned_create[:30] if dry_run else planned_create[:30],
        "errors": errors,
    }


def demote_vanity_goals(client: Any, customer_id: str, *, dry_run: bool) -> dict[str, Any]:
    from google.protobuf import field_mask_pb2

    markers = (
        "first_visit",
        "scroll",
        "session_start",
        "page_view",
        "Page view",
        "user_engagement",
        "checkout_start",
        "subscribe_click",
        "seconds_on_page",
    )
    force_secondary_names = {
        "YouTube channel subscriptions",
        "Purchase (1)",
        "Subscribe",
    }
    ga = client.get_service("GoogleAdsService")
    planned = []
    for row in ga.search(
        customer_id=customer_id,
        query="""
        SELECT conversion_action.resource_name, conversion_action.name,
               conversion_action.primary_for_goal,
               conversion_action.include_in_conversions_metric,
               conversion_action.status
        FROM conversion_action
        WHERE conversion_action.status != 'REMOVED'
        """,
    ):
        name = row.conversion_action.name
        primary = row.conversion_action.primary_for_goal
        included = row.conversion_action.include_in_conversions_metric
        if not primary and not included:
            continue
        if name in force_secondary_names or any(m in name for m in markers):
            planned.append(
                {
                    "name": name,
                    "resource_name": row.conversion_action.resource_name,
                    "was_primary": primary,
                    "was_included": included,
                }
            )

    updated = 0
    errors: list[str] = []
    if planned and not dry_run:
        svc = client.get_service("ConversionActionService")
        ops = []
        for item in planned:
            op = client.get_type("ConversionActionOperation")
            op.update.resource_name = item["resource_name"]
            op.update.primary_for_goal = False
            op.update.include_in_conversions_metric = False
            op.update_mask.CopyFrom(
                field_mask_pb2.FieldMask(
                    paths=["primary_for_goal", "include_in_conversions_metric"]
                )
            )
            ops.append(op)
        try:
            svc.mutate_conversion_actions(customer_id=customer_id, operations=ops)
            updated = len(ops)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc)[:400])
    return {"planned": planned, "updated": updated if not dry_run else len(planned), "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--skip-rsa", action="store_true")
    parser.add_argument("--skip-keywords", action="store_true")
    parser.add_argument("--skip-goals", action="store_true")
    args = parser.parse_args()
    dry_run = not args.apply

    _load_env()
    from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
    from ads_ai_agents.adapters.settings import GoogleAdsSettings
    from ads_ai_agents.adapters.write_guard import assert_write_service
    from google.ads.googleads.client import GoogleAdsClient

    if not dry_run:
        assert_mutations_allowed()
        assert_write_service("AdGroupCriterionService", scope="controlled_live_keywords")
        assert_write_service("ConversionActionService", scope="goal_cleanup")
        assert_write_service("AdGroupAdService", scope="asset_publish")
        assert_write_service("AdService", scope="asset_publish")

    settings = GoogleAdsSettings.from_env()
    client = GoogleAdsClient.load_from_dict(settings.client_config())
    cid = settings.customer_id

    report: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
    }

    if not args.skip_keywords:
        report["keywords"] = remediate_keywords(client, cid, dry_run=dry_run)

    if not args.skip_goals:
        report["goals"] = demote_vanity_goals(client, cid, dry_run=dry_run)

    if not args.skip_rsa:
        from ads_ai_agents.intelligence.rsa_prohibited_replace import ProhibitedRsaReplacer

        # Focus scrub on psychologist/therapy wording; symptom terms still match
        # inventory via existing CLINICAL_MARKERS (policy-safe RSA replacement).
        rsa = ProhibitedRsaReplacer().apply(dry_run=dry_run, cities_only=False)
        report["rsa"] = rsa.to_dict()

    out_dir = ROOT / "reports" / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "enterprise_grant_hygiene.json"
    # Compact rsa details
    if "rsa" in report and isinstance(report["rsa"], dict):
        details = report["rsa"].get("details") or []
        report["rsa"]["details"] = details[:40]
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    mode = "APPLY" if not dry_run else "DRY-RUN"
    print(f"Enterprise Grant hygiene [{mode}]")
    if "keywords" in report:
        k = report["keywords"]
        print(f"  keywords high-risk={k['found']} paused={k['paused']} replacements={k['replacements_created']} err={len(k['errors'])}")
    if "goals" in report:
        g = report["goals"]
        print(f"  goals demote planned={len(g['planned'])} updated={g['updated']} err={len(g['errors'])}")
    if "rsa" in report:
        r = report["rsa"]
        print(
            f"  rsa candidates={r.get('candidates')} groups={r.get('ad_groups')} "
            f"created={r.get('created')} paused={r.get('paused')} err={len(r.get('errors') or [])}"
        )
    print(f"Wrote: {out_path}")
    errs = 0
    for key in ("keywords", "goals"):
        errs += len((report.get(key) or {}).get("errors") or [])
    errs += len((report.get("rsa") or {}).get("errors") or [])
    return 1 if errs > 20 else 0


if __name__ == "__main__":
    raise SystemExit(main())
