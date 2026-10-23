#!/usr/bin/env python3
"""Promote chat_open as a mid-funnel biddable conversion.

Do NOT promote homepage page_view or /login page views: those fire on almost
every session (and /login on www.example.com is a placeholder). chat_open means
the product UI actually opened — shallower than chat_message_sent, still intent.

Dry-run by default. --apply to mutate.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from google.protobuf import field_mask_pb2

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

TARGET_NAME = "www.example.com (web) chat_open"


def _load_env() -> None:
    for raw in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> int:
    from ads_ai_agents.intelligence.recovery_playbook import REFUSE_GOAL_DRIFT, refuse

    return refuse("promote_chat_open_goal.py", REFUSE_GOAL_DRIFT)


def _legacy_main_disabled() -> int:
    apply = "--apply" in sys.argv
    _load_env()

    from ads_ai_agents.adapters.settings import GoogleAdsSettings
    from ads_ai_agents.adapters.write_guard import assert_write_service
    from google.ads.googleads.client import GoogleAdsClient

    if apply:
        from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed

        assert_mutations_allowed()
    assert_write_service("ConversionActionService", scope="grant_delivery_recovery")

    settings = GoogleAdsSettings.from_env()
    client = GoogleAdsClient.load_from_dict(settings.client_config())
    ga = client.get_service("GoogleAdsService")
    cid = settings.customer_id

    rows = list(
        ga.search(
            customer_id=cid,
            query=f"""
            SELECT conversion_action.resource_name, conversion_action.name,
                   conversion_action.status, conversion_action.category,
                   conversion_action.primary_for_goal,
                   conversion_action.include_in_conversions_metric
            FROM conversion_action
            WHERE conversion_action.name = '{TARGET_NAME}'
            """,
        )
    )
    if not rows:
        print(json.dumps({"error": f"{TARGET_NAME} not found"}, ensure_ascii=False))
        return 2

    a = rows[0].conversion_action
    result = {
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "APPLY" if apply else "DRY_RUN",
        "before": {
            "name": a.name,
            "status": a.status.name,
            "category": a.category.name,
            "primary": a.primary_for_goal,
            "included": a.include_in_conversions_metric,
        },
        "desired": {"primary": True, "included": True, "category": "SIGNUP"},
        "note": "PAGE_VIEW stays non-biddable. login_success already primary.",
    }

    op = client.get_type("ConversionActionOperation")
    op.update.resource_name = a.resource_name
    # GA4-imported actions: include_in_conversions_metric and category are
    # immutable. primary_for_goal is what feeds SIGNUP (already biddable).
    op.update.primary_for_goal = True
    op.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["primary_for_goal"]))

    svc = client.get_service("ConversionActionService")
    vreq = client.get_type("MutateConversionActionsRequest")
    vreq.customer_id = cid
    vreq.operations.append(op)
    vreq.validate_only = True
    svc.mutate_conversion_actions(request=vreq)
    result["validation"] = "PASSED"

    if apply:
        areq = client.get_type("MutateConversionActionsRequest")
        areq.customer_id = cid
        areq.operations.append(op)
        svc.mutate_conversion_actions(request=areq)
        result["applied"] = True

    out_dir = ROOT / "reports" / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "promote_chat_open_goal.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Wrote: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
