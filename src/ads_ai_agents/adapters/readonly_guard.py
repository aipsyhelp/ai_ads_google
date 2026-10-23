from __future__ import annotations

ALLOWED_SERVICES = frozenset(
    {
        "GoogleAdsService",
    }
)

FORBIDDEN_SERVICE_SUBSTRINGS = (
    "Mutation",
    "Upload",
    "ConversionUpload",
    "OfflineUserData",
    "CustomerMatch",
)


class ReadOnlyViolation(RuntimeError):
    """Raised when read-only mode would instantiate a mutating Google Ads surface."""


def assert_readonly_service(service_name: str) -> None:
    if service_name not in ALLOWED_SERVICES:
        raise ReadOnlyViolation(
            f"Read-only mode forbids service '{service_name}'. "
            f"Allowed: {sorted(ALLOWED_SERVICES)}"
        )
    for fragment in FORBIDDEN_SERVICE_SUBSTRINGS:
        if fragment.lower() in service_name.lower():
            raise ReadOnlyViolation(
                f"Read-only mode forbids mutating/upload service '{service_name}'."
            )
