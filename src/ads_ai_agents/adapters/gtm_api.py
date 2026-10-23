"""Google Tag Manager API v2 adapter (optional; ``pip install -e ".[gtm]"``).

Uses the same Google Cloud service account as the Google Ads API. Add the service
account's e-mail in GTM → Admin → User Management with **Publish** permission on the
container. Everything goes through the small :class:`GtmApi` protocol so planners and
tests can use an in-memory fake (no network in tests).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol

GTM_SCOPES = (
    "https://www.googleapis.com/auth/tagmanager.readonly",
    "https://www.googleapis.com/auth/tagmanager.edit.containers",
    "https://www.googleapis.com/auth/tagmanager.edit.containerversions",
    "https://www.googleapis.com/auth/tagmanager.publish",
)


class GtmApi(Protocol):
    def list_accounts(self) -> list[dict[str, Any]]: ...
    def list_containers(self, account_id: str) -> list[dict[str, Any]]: ...
    def live_version(self, container_path: str) -> dict[str, Any] | None: ...
    def list_entities(self, workspace_path: str) -> dict[str, list[dict[str, Any]]]: ...
    def default_workspace(self, container_path: str) -> dict[str, Any] | None: ...
    def create_workspace(self, container_path: str, name: str, description: str) -> dict[str, Any]: ...
    def create_variable(self, workspace_path: str, body: dict[str, Any]) -> dict[str, Any]: ...
    def create_trigger(self, workspace_path: str, body: dict[str, Any]) -> dict[str, Any]: ...
    def create_tag(self, workspace_path: str, body: dict[str, Any]) -> dict[str, Any]: ...
    def update_entity(self, path: str, body: dict[str, Any]) -> dict[str, Any]: ...
    def create_version(self, workspace_path: str, name: str, notes: str) -> dict[str, Any]: ...
    def publish_version(self, version_path: str) -> dict[str, Any]: ...


def service_account_email(json_path: str | os.PathLike[str] | None = None) -> str:
    """Return ``client_email`` from the service-account key (never any other field)."""
    raw = json_path or os.environ.get("GOOGLE_ADS_SERVICE_ACCOUNT_JSON", "")
    path = Path(str(raw)).expanduser() if raw else None
    if not path or not path.is_file():
        return ""
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("client_email") or "")
    except (OSError, ValueError):
        return ""


class GoogleTagManagerApi:
    """Thin wrapper over ``googleapiclient.discovery.build("tagmanager", "v2")``."""

    def __init__(self, service: Any) -> None:
        self._svc = service

    @classmethod
    def from_service_account(cls, json_path: str | os.PathLike[str] | None = None) -> "GoogleTagManagerApi":
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError('GTM API needs: pip install -e ".[gtm]" (google-api-python-client)') from exc
        path = str(json_path or os.environ.get("GOOGLE_ADS_SERVICE_ACCOUNT_JSON", "")).strip()
        if not path or not Path(path).expanduser().is_file():
            raise RuntimeError("Service-account JSON not found; set it in the dashboard (Credentials).")
        creds = service_account.Credentials.from_service_account_file(str(Path(path).expanduser()), scopes=list(GTM_SCOPES))
        return cls(build("tagmanager", "v2", credentials=creds, cache_discovery=False))

    # -- read
    def list_accounts(self) -> list[dict[str, Any]]:
        return self._svc.accounts().list().execute().get("account", [])

    def list_containers(self, account_id: str) -> list[dict[str, Any]]:
        return self._svc.accounts().containers().list(parent=f"accounts/{account_id}").execute().get("container", [])

    def live_version(self, container_path: str) -> dict[str, Any] | None:
        try:
            return self._svc.accounts().containers().versions().live(parent=container_path).execute()
        except Exception:  # noqa: BLE001 - no live version yet
            return None

    def default_workspace(self, container_path: str) -> dict[str, Any] | None:
        items = self._svc.accounts().containers().workspaces().list(parent=container_path).execute().get("workspace", [])
        return items[0] if items else None

    def list_entities(self, workspace_path: str) -> dict[str, list[dict[str, Any]]]:
        ws = self._svc.accounts().containers().workspaces()
        return {
            "tags": ws.tags().list(parent=workspace_path).execute().get("tag", []),
            "triggers": ws.triggers().list(parent=workspace_path).execute().get("trigger", []),
            "variables": ws.variables().list(parent=workspace_path).execute().get("variable", []),
        }

    # -- write (only called from gtm_tags.apply_tag_plan after mode + confirm gates)
    def create_workspace(self, container_path: str, name: str, description: str) -> dict[str, Any]:
        body = {"name": name, "description": description}
        return self._svc.accounts().containers().workspaces().create(parent=container_path, body=body).execute()

    def create_variable(self, workspace_path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._svc.accounts().containers().workspaces().variables().create(parent=workspace_path, body=body).execute()

    def create_trigger(self, workspace_path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._svc.accounts().containers().workspaces().triggers().create(parent=workspace_path, body=body).execute()

    def create_tag(self, workspace_path: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._svc.accounts().containers().workspaces().tags().create(parent=workspace_path, body=body).execute()

    def update_entity(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        ws = self._svc.accounts().containers().workspaces()
        kind = "tags" if "/tags/" in path else "triggers" if "/triggers/" in path else "variables"
        return getattr(ws, kind)().update(path=path, body=body).execute()

    def create_version(self, workspace_path: str, name: str, notes: str) -> dict[str, Any]:
        body = {"name": name, "notes": notes}
        return self._svc.accounts().containers().workspaces().create_version(path=workspace_path, body=body).execute()

    def publish_version(self, version_path: str) -> dict[str, Any]:
        return self._svc.accounts().containers().versions().publish(path=version_path).execute()
