"""Shared write path for ad group keyword operations.

Google rejects restricted terms (for example HEALTH_IN_PERSONALIZED_ADS) per
operation, and it validates a batch in phases: once operations fail an early
phase, later phases are not run for the whole batch. A single validate_only
pass therefore reports only part of the rejections, and the commit that follows
still partially fails. Re-validating the survivors converges on a batch that
commits cleanly.

Both the keyword normalizer and the semantic expander write keywords, so this
logic lives in one place instead of being copied per agent.
"""
from __future__ import annotations

import re
from typing import Any, Callable

MAX_VALIDATION_ROUNDS = 5


def is_positive_single_word_create(operation: Any) -> bool:
    """Block positive one-word keywords required to be absent for Ad Grants.

    One-word campaign/ad-group negatives remain allowed because they narrow
    targeting and are not positive keywords shown in the compliance table.
    """
    if not is_create(operation) or bool(getattr(operation.create, "negative", False)):
        return False
    text = (operation.create.keyword.text or "").strip()
    return len(re.findall(r"[\w’'\-]+", text, flags=re.UNICODE)) == 1


def drop_positive_single_word_creates(
    operations: list[Any], warnings: list[dict[str, str]]
) -> list[Any]:
    safe: list[Any] = []
    for operation in operations:
        if is_positive_single_word_create(operation):
            warnings.append(
                {
                    "op": "keyword_blocked",
                    "keyword": operation_label(operation),
                    "warning": "Ad Grants: positive single-word keyword creation blocked",
                }
            )
            continue
        safe.append(operation)
    return safe


def is_create(operation: Any) -> bool:
    return bool(operation.create.keyword.text)


def operation_label(operation: Any) -> str:
    return operation.create.keyword.text or operation.update.resource_name


def rejected_indices(client: Any, failure_error: Any) -> dict[int, str]:
    """Map operation index → rejection reason from a partial failure error."""
    rejected: dict[int, str] = {}
    failure = client.get_type("GoogleAdsFailure")
    for detail in failure_error.details:
        try:
            failure._pb.ParseFromString(detail.value)
        except Exception:  # noqa: BLE001
            continue
        for error in failure.errors:
            index = next(
                (
                    element.index
                    for element in error.location.field_path_elements
                    if element.field_name == "operations"
                ),
                None,
            )
            if index is None:
                continue
            policy = error.details.policy_violation_details.key.policy_name
            rejected[index] = policy or (error.message or "policy_violation")[:160]
    return rejected


def prune_rejected(
    *,
    service: Any,
    client: Any,
    customer_id: str,
    operations: list[Any],
    warnings: list[dict[str, str]],
    rejected_indices_fn: Callable[[Any], dict[int, str]] | None = None,
) -> list[Any]:
    """Validate repeatedly and return only the operations Google accepts."""
    resolve = rejected_indices_fn or (lambda failure: rejected_indices(client, failure))
    surviving = drop_positive_single_word_creates(list(operations), warnings)

    for _ in range(MAX_VALIDATION_ROUNDS):
        if not surviving:
            return []

        request = client.get_type("MutateAdGroupCriteriaRequest")
        request.customer_id = customer_id
        request.operations.extend(surviving)
        request.partial_failure = True
        request.validate_only = True
        try:
            response = service.mutate_ad_group_criteria(request=request)
        except Exception as exc:  # noqa: BLE001
            warnings.append({"op": "validate_keywords", "error": str(exc)[:300]})
            return surviving

        failure_error = response.partial_failure_error
        if not failure_error.code:
            return surviving

        rejected = resolve(failure_error)
        if not rejected:
            # Batch-level failure with no operation index: pruning by index is
            # impossible, so hand the batch to the partial-failure commit.
            warnings.append(
                {
                    "op": "validate_keywords",
                    "warning": f"Unattributable validation failure: {failure_error.message[:240]}",
                }
            )
            return surviving

        for index, reason in sorted(rejected.items()):
            if index < len(surviving):
                warnings.append(
                    {
                        "op": "keyword_rejected",
                        "keyword": operation_label(surviving[index]),
                        "warning": f"Rejected by Google policy: {reason}",
                    }
                )
        surviving = [op for index, op in enumerate(surviving) if index not in rejected]

    warnings.append(
        {
            "op": "validate_keywords",
            "warning": (
                f"Validation still reported rejections after {MAX_VALIDATION_ROUNDS} rounds; "
                "committing survivors with partial failure enabled."
            ),
        }
    )
    return surviving


def commit(
    *,
    service: Any,
    client: Any,
    customer_id: str,
    operations: list[Any],
    warnings: list[dict[str, str]],
    rejected_indices_fn: Callable[[Any], dict[int, str]] | None = None,
) -> tuple[int, int]:
    """Commit keyword operations and return (created, updated) counts."""
    operations = drop_positive_single_word_creates(list(operations), warnings)
    if not operations:
        return 0, 0

    resolve = rejected_indices_fn or (lambda failure: rejected_indices(client, failure))

    request = client.get_type("MutateAdGroupCriteriaRequest")
    request.customer_id = customer_id
    request.operations.extend(operations)
    request.partial_failure = True
    response = service.mutate_ad_group_criteria(request=request)

    # Results are index-aligned with operations and a failed operation yields an
    # empty result, so counts must come from the response, not the request size.
    created = 0
    updated = 0
    for operation, item in zip(operations, response.results):
        if not item.resource_name:
            continue
        if is_create(operation):
            created += 1
        else:
            updated += 1

    if response.partial_failure_error.code:
        for index, reason in sorted(resolve(response.partial_failure_error).items()):
            if index < len(operations):
                warnings.append(
                    {
                        "op": "keyword_rejected_on_commit",
                        "keyword": operation_label(operations[index]),
                        "warning": reason,
                    }
                )
        warnings.append(
            {
                "op": "phrase_partial_failure",
                "error": response.partial_failure_error.message[:300],
            }
        )

    return created, updated
