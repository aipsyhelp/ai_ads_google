"""In-memory Google Tag Manager API used by tests (no network)."""
from __future__ import annotations

from typing import Any


class FakeGtmApi:
    def __init__(self, live_version_id: str = "7") -> None:
        self.calls: list[tuple[str, Any]] = []
        self.container = "accounts/100/containers/200"
        self.live_version_id = live_version_id
        self.entities: dict[str, list[dict[str, Any]]] = {"tags": [], "triggers": [], "variables": []}
        self._n = 0

    def _id(self) -> str:
        self._n += 1
        return str(self._n)

    def list_accounts(self):
        self.calls.append(("list_accounts", None))
        return [{"accountId": "100", "name": "Fake Org Account"}]

    def list_containers(self, account_id):
        self.calls.append(("list_containers", account_id))
        return [{"containerId": "200", "name": "www.example.org", "publicId": "GTM-FAKE123"}]

    def live_version(self, container_path):
        self.calls.append(("live_version", container_path))
        return {"containerVersionId": self.live_version_id, "path": f"{container_path}/versions/{self.live_version_id}"}

    def default_workspace(self, container_path):
        self.calls.append(("default_workspace", container_path))
        return {"path": f"{container_path}/workspaces/1"}

    def list_entities(self, workspace_path):
        self.calls.append(("list_entities", workspace_path))
        return self.entities

    def create_workspace(self, container_path, name, description):
        self.calls.append(("create_workspace", name))
        return {"path": f"{container_path}/workspaces/9", "workspaceId": "9"}

    def _create(self, kind, workspace_path, body):
        self.calls.append((f"create_{kind}", body["name"]))
        i = self._id()
        return {"path": f"{workspace_path}/{kind}s/{i}", f"{kind}Id": i, **body}

    def create_variable(self, workspace_path, body):
        return self._create("variable", workspace_path, body)

    def create_trigger(self, workspace_path, body):
        return self._create("trigger", workspace_path, body)

    def create_tag(self, workspace_path, body):
        return self._create("tag", workspace_path, body)

    def update_entity(self, path, body):
        self.calls.append(("update_entity", body["name"]))
        return {"path": path, **body}

    def create_version(self, workspace_path, name, notes):
        self.calls.append(("create_version", name))
        return {"containerVersion": {"containerVersionId": "8", "path": f"{self.container}/versions/8"}}

    def publish_version(self, version_path):
        self.calls.append(("publish_version", version_path))
        return {"containerVersion": {"path": version_path}}

    def call_names(self):
        return [c[0] for c in self.calls]


class FakeResponse:
    def __init__(self, html: str) -> None:
        self._b = html.encode("utf-8")

    def read(self, n: int = -1) -> bytes:
        return self._b if n < 0 else self._b[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_site_opener(html: str):
    calls: list[str] = []

    def _open(req, timeout=None):
        calls.append(req.full_url)
        return FakeResponse(html)

    _open.calls = calls  # type: ignore[attr-defined]
    return _open


SITE_WITH_GTM = (
    "<html><head><title>Example Org — coding classes</title>"
    '<meta name="description" content="Free weekend coding classes for teens">'
    "<script>(function(w,d,s,l,i){})(window,document,'script','dataLayer','GTM-FAKE123');</script>"
    '<script src="https://www.googletagmanager.com/gtm.js?id=GTM-FAKE123"></script></head><body></body></html>'
)
SITE_WITHOUT_GTM = "<html><head><title>Example Org</title></head><body>hello</body></html>"
