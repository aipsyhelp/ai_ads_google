from __future__ import annotations

from typing import Any

from ads_ai_agents.adapters.data_manager import (
    TARGET_PIPELINE,
    ConversionEventRecord,
    DataManagerConversionClient,
    UploadStatus,
)


QUALITY_EVENT_CONTRACT = (
    {
        "name": "chat_message_sent",
        "maps_to": ["chat_message_sent", "www.example.com (web) chat_message_sent"],
        "role": "activation_primary",
        "upload": "ga4_import_primary",
    },
    {
        "name": "login_success",
        "maps_to": ["login_success"],
        "role": "acquisition_secondary",
        "upload": "ga4_import_secondary",
    },
    {
        "name": "click_store",
        "maps_to": ["click_store_ios", "click_store_android"],
        "role": "acquisition_secondary",
        "upload": "ga4_import_secondary",
    },
    {
        "name": "subscribe_checkout_observe",
        "maps_to": ["subscribe_click", "checkout_start"],
        "role": "revenue_observe",
        "upload": "ga4_import_observe",
    },
    {
        "name": "purchase",
        "maps_to": ["Purchase", "purchase", "payment_success"],
        "role": "revenue_observe_until_volume",
        "upload": "data_manager_required_for_cppu",
    },
)


def build_measurement_plan(*, paying_users_known: bool = False) -> dict[str, Any]:
    client = DataManagerConversionClient()
    return {
        "agent": "measurement",
        "north_star_event": "chat_message_sent",
        "observe_events": ["subscribe_click", "checkout_start", "purchase"],
        "pipeline": TARGET_PIPELINE.strip(),
        "data_manager": {
            "supported_now": client.supported,
            "reason": client.reason,
            "forbidden": ["UploadClickConversion_only_for_new_tokens"],
        },
        "event_contract": list(QUALITY_EVENT_CONTRACT),
        "priority": (
            "Stage: optimize to chat_message_sent (CPPA); purchase observe-only until volume."
            if not paying_users_known
            else "Graduate north star to purchase/CPPU; keep activation as quality mid-funnel."
        ),
        "checklist": [
            "Backend event registry with event_id + consent",
            "Hash email/phone SHA-256 where enhanced conversions apply",
            "Map gclid/gbraid/wbraid through registration → pay",
            "Validate in Google Ads diagnostics within 24–72h",
            "Never let micro events become primary_for_goal again",
        ],
        "status": "bridge_ready_awaiting_backend_events",
    }


def enqueue_sample_purchase_diagnostic(
    *,
    gclid: str | None = None,
    value: float = 9.99,
) -> dict[str, Any]:
    """Dry diagnostic enqueue — does not call Google upload APIs."""
    client = DataManagerConversionClient()
    event = ConversionEventRecord(
        gclid=gclid,
        event_id="diagnostic_sample",
        conversion_name="purchase",
        conversion_time="",
        value=value,
        currency="USD",
        consent_state="unknown",
    )
    queued = client.enqueue(event)
    return {
        "queued": queued.to_dict(),
        "note": "Stub only — production upload after integrity + consent wiring.",
        "upload_status": UploadStatus.PENDING.value,
    }
