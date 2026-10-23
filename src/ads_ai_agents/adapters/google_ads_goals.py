from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from google.protobuf import field_mask_pb2

from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.adapters.write_guard import assert_write_service
from ads_ai_agents.logging_utils import configure_logging

logger = configure_logging()


@dataclass
class GoalMutation:
    action_id: str
    name: str
    field: str
    old_value: Any
    new_value: Any
    decision: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GoalApplyResult:
    dry_run: bool
    attempted: list[GoalMutation] = field(default_factory=list)
    applied: list[GoalMutation] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "attempted": [m.to_dict() for m in self.attempted],
            "applied": [m.to_dict() for m in self.applied],
            "skipped": self.skipped,
            "errors": self.errors,
            "mutate_surface": "ConversionActionService.primary_for_goal",
            "budgets_touched": False,
            "keywords_touched": False,
        }


class GoogleAdsGoalsWriter:
    """Enterprise Wave-1 writer: demote conversion actions from primary_for_goal only."""

    def __init__(self, settings: GoogleAdsSettings | None = None) -> None:
        self.settings = settings or GoogleAdsSettings.from_env()
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from google.ads.googleads.client import GoogleAdsClient

            raw = GoogleAdsClient.load_from_dict(self.settings.client_config())
            self._client = _ScopedWriteClientProxy(raw, scope="goal_cleanup")
        return self._client

    def apply_cleanup_plan(
        self,
        plan: dict[str, Any],
        *,
        dry_run: bool = True,
        only_decisions: tuple[str, ...] = ("demote_to_secondary",),
    ) -> GoalApplyResult:
        result = GoalApplyResult(dry_run=dry_run)
        customer_id = self.settings.customer_id
        if not dry_run:
            assert_mutations_allowed()

        for action in plan.get("actions") or []:
            decision = str(action.get("decision") or "")
            action_id = str(action.get("id") or "").strip()
            name = str(action.get("name") or "")
            if decision not in only_decisions:
                result.skipped.append(
                    {"id": action_id, "name": name, "reason": f"decision_not_in_wave:{decision}"}
                )
                continue
            if not action_id:
                result.skipped.append({"name": name, "reason": "missing_action_id"})
                continue
            if not bool(action.get("current_primary")):
                result.skipped.append(
                    {"id": action_id, "name": name, "reason": "already_not_primary"}
                )
                continue

            mutation = GoalMutation(
                action_id=action_id,
                name=name,
                field="primary_for_goal",
                old_value=True,
                new_value=False,
                decision=decision,
            )
            result.attempted.append(mutation)

            if dry_run:
                continue

            try:
                self._set_primary_for_goal(customer_id, action_id, primary=False)
                result.applied.append(mutation)
                logger.info("Demoted conversion action %s (%s) primary_for_goal=false", action_id, name)
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed demoting %s: %s", action_id, exc)
                result.errors.append({"id": action_id, "name": name, "error": str(exc)})

        return result

    def _set_primary_for_goal(self, customer_id: str, action_id: str, *, primary: bool) -> None:
        service = self.client.get_service("ConversionActionService")
        operation = self.client.get_type("ConversionActionOperation")
        action = operation.update
        action.resource_name = service.conversion_action_path(customer_id, action_id)
        action.primary_for_goal = primary
        operation.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["primary_for_goal"]))
        service.mutate_conversion_actions(customer_id=customer_id, operations=[operation])


class _ScopedWriteClientProxy:
    def __init__(self, client: Any, *, scope: str) -> None:
        self._client = client
        self._scope = scope

    def get_service(self, name: str):
        assert_write_service(name, scope=self._scope)
        return self._client.get_service(name)

    def get_type(self, name: str):
        return self._client.get_type(name)

    def __getattr__(self, item: str):
        return getattr(self._client, item)
