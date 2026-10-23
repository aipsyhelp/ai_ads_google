from __future__ import annotations

"""
Data Manager conversion architecture (target path).

Google restricted UploadClickConversion for developer tokens that never used
offline conversions before 2026-06-15 and recommends Data Manager API for new
integrations. This module defines the event contract and intentionally does NOT
call ConversionUploadService / UploadClickConversion.
"""

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class UploadStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    DIAGNOSTIC_ERROR = "diagnostic_error"


@dataclass
class ConversionEventRecord:
    gclid: str | None = None
    gbraid: str | None = None
    wbraid: str | None = None
    event_id: str = ""
    user_id_pseudonymous: str = ""
    conversion_name: str = ""
    conversion_time: str = ""
    value: float | None = None
    currency: str = "USD"
    consent_state: str = "unknown"
    upload_status: UploadStatus = UploadStatus.PENDING
    google_job_id: str | None = None
    error_details: str | None = None
    hashed_email_sha256: str | None = None
    hashed_phone_sha256: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["upload_status"] = self.upload_status.value
        return payload


TARGET_PIPELINE = """
Website / App
   ↓
Backend event registry
   ↓
Consent validation
   ↓
Identity normalization + SHA-256 hashing
   ↓
Data Manager / supported conversion upload
   ↓
Google Ads diagnostics
"""


class DataManagerConversionClient:
    """Stub client — production wiring comes after conversion integrity audit."""

    def __init__(self) -> None:
        self.supported = False
        self.reason = (
            "Phase 2.1 intentionally uses Data Manager as the target architecture. "
            "UploadClickConversion-only pipelines are not implemented."
        )

    def enqueue(self, event: ConversionEventRecord) -> ConversionEventRecord:
        event.upload_status = UploadStatus.PENDING
        event.metadata["pipeline"] = "data_manager_target"
        event.metadata["blocked_reason"] = self.reason
        return event

    def architecture(self) -> dict[str, Any]:
        return {
            "target": "data_manager_api",
            "forbidden_primary_path": "UploadClickConversion",
            "pipeline": TARGET_PIPELINE.strip(),
            "required_fields": [
                "gclid|gbraid|wbraid",
                "event_id",
                "user_id_pseudonymous",
                "conversion_name",
                "conversion_time",
                "value",
                "currency",
                "consent_state",
                "upload_status",
                "google_job_id",
                "error_details",
            ],
            "preferred_events": [
                "qualified_onboarding_complete",
                "first_meaningful_conversation",
                "account_returned_within_7_days",
                "subscription_started",
                "payment_success",
            ],
        }
