#!/usr/bin/env python3
"""Read-only 30-day Google Ads change-history export for delivery forensics."""
from __future__ import annotations

import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from google.protobuf.json_format import MessageToDict

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def _load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _to_dict(message: Any) -> dict[str, Any]:
    return MessageToDict(
        message._pb,
        preserving_proto_field_name=True,
        use_integers_for_enums=False,
    )


def main() -> int:
    _load_env()
    from ads_ai_agents.adapters.settings import GoogleAdsSettings
    from google.ads.googleads.client import GoogleAdsClient

    settings = GoogleAdsSettings.from_env()
    client = GoogleAdsClient.load_from_dict(settings.client_config())
    ga = client.get_service("GoogleAdsService")
    customer_id = settings.customer_id

    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=29)
    query = f"""
        SELECT change_event.change_date_time,
               change_event.change_resource_type,
               change_event.change_resource_name,
               change_event.resource_change_operation,
               change_event.user_email,
               change_event.client_type,
               change_event.changed_fields,
               change_event.old_resource,
               change_event.new_resource
        FROM change_event
        WHERE change_event.change_date_time BETWEEN '{start_date.isoformat()}'
              AND '{today.isoformat()} 23:59:59'
        ORDER BY change_event.change_date_time DESC
        LIMIT 10000
    """
    events = [_to_dict(row.change_event) for row in ga.search(customer_id=customer_id, query=query)]

    by_day: Counter[str] = Counter()
    by_user: Counter[str] = Counter()
    by_client: Counter[str] = Counter()
    by_resource: Counter[str] = Counter()
    by_changed_fields: Counter[str] = Counter()
    by_day_resource: dict[str, Counter[str]] = defaultdict(Counter)
    by_user_resource: dict[str, Counter[str]] = defaultdict(Counter)
    for event in events:
        day = str(event.get("change_date_time", ""))[:10]
        user = str(event.get("user_email") or "unknown")
        resource = str(event.get("change_resource_type") or "unknown")
        by_day[day] += 1
        by_user[user] += 1
        by_client[str(event.get("client_type") or "unknown")] += 1
        by_resource[resource] += 1
        by_changed_fields[str(event.get("changed_fields") or "none")] += 1
        by_day_resource[day][resource] += 1
        by_user_resource[user][resource] += 1

    material_types = {"CAMPAIGN", "CAMPAIGN_BUDGET", "BIDDING_STRATEGY", "AD_GROUP"}
    material_events = [
        event
        for event in events
        if str(event.get("change_resource_type")) in material_types
    ]

    report = {
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "customer_id": customer_id,
        "window": {
            "start": start_date.isoformat(),
            "end": today.isoformat(),
        },
        "count": len(events),
        "counts": {
            "by_day": dict(sorted(by_day.items())),
            "by_user": dict(by_user.most_common()),
            "by_client": dict(by_client.most_common()),
            "by_resource": dict(by_resource.most_common()),
            "by_changed_fields": dict(by_changed_fields.most_common()),
            "by_day_resource": {
                day: dict(counter.most_common())
                for day, counter in sorted(by_day_resource.items())
            },
            "by_user_resource": {
                user: dict(counter.most_common())
                for user, counter in by_user_resource.items()
            },
        },
        "material_events": material_events,
        "events": events,
    }
    out_dir = ROOT / "reports" / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "change_history_forensics_30d.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                k: v
                for k, v in report.items()
                if k not in {"events", "material_events"}
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"Wrote {len(events)} events: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
