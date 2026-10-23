"""Local dashboard + setup UI served with the standard library only.

Security model (it is a local tool that edits credentials):

* binds to loopback only (127.0.0.1 / ::1 / localhost); other hosts are refused;
* every ``/api`` request must carry the per-process token embedded in the page
  (``X-UI-Token``), which blocks cross-site requests from other browser tabs;
* the ``Host`` header must be a loopback name (DNS-rebinding protection);
* secrets are write-only: responses never include the developer token, webhook URL
  or service-account key contents;
* all Google Ads access goes through the read-only adapter; the UI has no write actions.
"""
from __future__ import annotations

import json
import secrets
import threading
from dataclasses import asdict, is_dataclass
from enum import Enum
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, Callable

from ads_ai_agents import config_store
from ads_ai_agents.logging_utils import redact_secrets

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
MAX_BODY = 1_000_000

AdapterFactory = Callable[[], Any]


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, Path):
        return str(value)
    return value


def _default_gtm_factory() -> Any:
    from ads_ai_agents.adapters.gtm_api import GoogleTagManagerApi

    return GoogleTagManagerApi.from_service_account()


def _default_adapter_factory() -> Any:
    from ads_ai_agents.adapters.google_ads_readonly import GoogleAdsReadOnlyAdapter

    return GoogleAdsReadOnlyAdapter()


class DashboardApp:
    """Request routing and actions. Transport-independent so it is easy to test."""

    def __init__(
        self,
        paths: config_store.ProjectPaths | None = None,
        *,
        adapter_factory: AdapterFactory | None = None,
        gtm_api_factory: AdapterFactory | None = None,
        site_opener: Callable[..., Any] | None = None,
        token: str | None = None,
    ) -> None:
        self.paths = paths or config_store.project_paths()
        self.adapter_factory = adapter_factory or _default_adapter_factory
        self.gtm_api_factory = gtm_api_factory or _default_gtm_factory
        self.site_opener = site_opener  # None → urllib (only used when the user ticks "fetch")
        self.token = token or secrets.token_urlsafe(24)
        self.last_ping: dict[str, Any] | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ static
    def index_html(self) -> bytes:
        html = resources.files("ads_ai_agents.ui").joinpath("static/index.html").read_text(encoding="utf-8")
        return html.replace("__UI_TOKEN__", self.token).encode("utf-8")

    def static_file(self, name: str) -> tuple[bytes, str] | None:
        types = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
        suffix = Path(name).suffix
        if "/" in name or name.startswith(".") or suffix not in types:
            return None
        res = resources.files("ads_ai_agents.ui").joinpath(f"static/{name}")
        if not res.is_file():
            return None
        return res.read_bytes(), types[suffix]

    # ------------------------------------------------------------------ api
    def api(self, method: str, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        with self._lock:
            if method == "GET" and path == "/api/config":
                return 200, {"config": config_store.config_view(self.paths)}
            if method == "GET" and path == "/api/settings":
                return 200, {"settings": config_store.loaded_settings(self.paths)}
            if method == "GET" and path == "/api/status":
                return 200, self.status()
            if method == "GET" and path == "/api/agents":
                return 200, self.agents()
            if method == "GET" and path == "/api/reports":
                return 200, self.reports()
            if path.startswith("/api/gtm/"):
                return self.gtm(method, path.removeprefix("/api/gtm/"), body)
            if method == "POST" and path.startswith("/api/config/"):
                return self.save(path.removeprefix("/api/config/"), body)
            if method == "POST" and path == "/api/init":
                copied = config_store.copy_templates(self.paths)
                config_store.reload_runtime(self.paths)
                return 200, {"ok": True, "copied": copied, "config": config_store.config_view(self.paths)}
            if method == "POST" and path == "/api/ping":
                return 200, self.ping()
            if method == "POST" and path == "/api/run/plan":
                return self.run_plan(body)
            if method == "POST" and path == "/api/run/audit":
                return 200, self.run_audit()
        return 404, {"ok": False, "error": "not found"}

    def save(self, section: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        try:
            result = config_store.save_section(section, body, self.paths, opener=self.site_opener)
        except config_store.ConfigValidationError as exc:
            return 400, {"ok": False, "errors": exc.errors}
        return 200, {
            "ok": True,
            "result": result.to_dict(),
            "config": config_store.config_view(self.paths),
            "settings": config_store.loaded_settings(self.paths),
        }

    # ------------------------------------------------------------------ Google Tags
    @property
    def gtm_log(self) -> Path:
        return self.paths.reports_dir / "gtm" / "changes.jsonl"

    def _gtm_ctx(self) -> tuple[dict[str, Any], dict[str, Any]]:
        from ads_ai_agents.intelligence.gtm_tags import tracking_from_facts

        config_store.reload_runtime(self.paths)
        facts = config_store.load_facts(self.paths)
        return facts, tracking_from_facts(facts)

    def gtm(self, method: str, action: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        from ads_ai_agents.adapters.gtm_api import service_account_email
        from ads_ai_agents.intelligence import gtm_tags

        if method == "POST" and action == "parse":
            return 200, {"ok": True, "parsed": gtm_tags.parse_tag_input(str(body.get("text") or ""))}
        facts, tracking = self._gtm_ctx()
        container_path = tracking["gtm_api"].get("container_path", "")
        if method == "GET" and action == "connect-info":
            env = config_store.read_env(self.paths)
            return 200, {
                "ok": True,
                "service_account_email": service_account_email(env.get("GOOGLE_ADS_SERVICE_ACCOUNT_JSON")),
                "permission": "Publish",
                "where": "Google Tag Manager → Admin → User Management → add the e-mail with Publish permission on your container",
                "saved": tracking["gtm_api"],
            }
        if method == "POST" and action == "accounts":
            try:
                api = self.gtm_api_factory()
                accounts = api.list_accounts()
                out = []
                for acc in accounts:
                    containers = api.list_containers(str(acc.get("accountId")))
                    out.append({
                        "account_id": str(acc.get("accountId")),
                        "name": acc.get("name"),
                        "containers": [
                            {"container_id": str(c.get("containerId")), "name": c.get("name"), "public_id": c.get("publicId")}
                            for c in containers
                        ],
                    })
            except Exception as exc:  # noqa: BLE001
                return 200, {"ok": False, "error": redact_secrets(str(exc))}
            return 200, {"ok": True, "accounts": out}
        if method == "POST" and action == "plan":
            plan = gtm_tags.build_tag_plan(facts)
            connected = False
            if container_path:
                try:
                    api = self.gtm_api_factory()
                    ws = api.default_workspace(container_path)
                    gtm_tags.diff_plan(plan, api.list_entities(ws["path"]) if ws else None)
                    connected = True
                except Exception as exc:  # noqa: BLE001
                    return 200, {"ok": False, "error": redact_secrets(str(exc))}
            else:
                gtm_tags.diff_plan(plan, None)
            return 200, {"ok": True, "connected": connected, "plan": plan.to_dict()}
        if method == "POST" and action == "apply":
            plan = gtm_tags.build_tag_plan(facts)
            confirm = body.get("confirm") is True
            allowed, _ = gtm_tags.write_gate(confirm)
            api = self.gtm_api_factory() if allowed else None
            result = gtm_tags.apply_tag_plan(api, plan, container_path=container_path, confirm=confirm, log_path=self.gtm_log)
            return 200, result
        if method == "POST" and action == "rollback":
            allowed, _ = gtm_tags.write_gate(body.get("confirm") is True)
            api = self.gtm_api_factory() if allowed else None
            return 200, gtm_tags.rollback(
                api, container_path=container_path, version_id=str(body.get("version_id") or ""),
                confirm=body.get("confirm") is True, log_path=self.gtm_log,
            )
        if method == "POST" and action == "verify":
            from ads_ai_agents.platform.product_facts import organization_profile

            org = organization_profile(facts)
            url = org["website_url"] or ("https://" + (facts.get("grant_destinations") or {}).get("preferred_grant_domain", "")).rstrip("/")
            url = "" if url == "https:/" else url
            return 200, {"ok": True, **gtm_tags.verify_site_snippet(url, tracking["gtm"], fetch=body.get("fetch") is True, opener=self.site_opener)}
        if method == "GET" and action == "log":
            return 200, {"ok": True, "entries": gtm_tags.read_log(self.gtm_log)}
        return 404, {"ok": False, "error": "not found"}

    def status(self) -> dict[str, Any]:
        from ads_ai_agents.adapters.automation_mode import resolve_automation_mode
        from ads_ai_agents.intelligence.controlled_policy import load_controlled_policy

        config_store.reload_runtime(self.paths)
        mode = resolve_automation_mode()
        policy = load_controlled_policy()
        view = config_store.config_view(self.paths)
        return {
            "mode": mode.value,
            "mutations_enabled": policy.mutations_enabled,
            "limits": {
                "max_budget_change_percent": policy.max_budget_change_percent,
                "require_approval_above_percent": policy.require_approval_above_percent,
                "cooldown_days": policy.cooldown_days,
                "max_account_daily_budget": policy.max_account_daily_budget,
            },
            "files": view["files"],
            "credentials_ready": view["credentials"]["developer_token_set"]
            and view["credentials"]["service_account_file_exists"]
            and bool(view["account"]["customer_id"]),
            "last_ping": self.last_ping,
            "samples": self.samples(),
        }

    def agents(self) -> dict[str, Any]:
        from ads_ai_agents.agents import ENTERPRISE_AGENTS
        from ads_ai_agents.platform.registry import PLATFORM_AGENTS

        platform = [
            {"id": a.id, "name": a.name, "layer": a.layer.value, "phase": a.phase, "mutates": a.mutates, "description": a.description}
            for a in PLATFORM_AGENTS
        ]
        ops = [{"name": a.name, "stage": a.stage.value, "class": type(a).__name__} for a in ENTERPRISE_AGENTS]
        return {"platform": platform, "ops": ops, "counts": {"platform": len(platform), "ops": len(ops), "total": len(platform) + len(ops)}}

    def reports(self) -> dict[str, Any]:
        root = self.paths.reports_dir
        days = sorted((p for p in root.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]") if p.is_dir()), reverse=True) if root.is_dir() else []
        latest: dict[str, Any] = {"dashboard": None, "executive": None, "directory": None}
        for day in days:
            dash = day / "efficiency_dashboard.json"
            exe = day / "executive_summary.json"
            if dash.is_file() or exe.is_file():
                latest["directory"] = str(day.relative_to(self.paths.root))
                latest["dashboard"] = json.loads(dash.read_text(encoding="utf-8")) if dash.is_file() else None
                latest["executive"] = json.loads(exe.read_text(encoding="utf-8")) if exe.is_file() else None
                break
        return {"days": [d.name for d in days[:30]], "latest": latest}

    def ping(self) -> dict[str, Any]:
        config_store.reload_runtime(self.paths)
        try:
            meta = self.adapter_factory().ping()
            self.last_ping = {"ok": True, "meta": meta}
        except Exception as exc:  # noqa: BLE001
            self.last_ping = {"ok": False, "error": redact_secrets(str(exc))}
        return self.last_ping

    def samples(self) -> list[str]:
        d = self.paths.samples_dir
        return sorted(p.name for p in d.glob("*.json")) if d.is_dir() else []

    def run_plan(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        from ads_ai_agents.core.loader import load_campaign_draft
        from ads_ai_agents.core.orchestrator import AdsOrchestrator

        name = str(body.get("sample") or "grant_search_campaign.example.json")
        if name not in self.samples():
            return 400, {"ok": False, "errors": {"sample": "unknown sample", "available": ", ".join(self.samples())}}
        config_store.reload_runtime(self.paths)
        mission = AdsOrchestrator().daily_cycle(load_campaign_draft(self.paths.samples_dir / name))
        return 200, {"ok": True, "read_only": True, "sample": name, "mission": _jsonable(mission)}

    def run_audit(self) -> dict[str, Any]:
        from ads_ai_agents.intelligence.account_audit import run_full_audit
        from ads_ai_agents.intelligence.dashboard import write_efficiency_dashboard
        from ads_ai_agents.intelligence.report_store import ReportStore

        config_store.reload_runtime(self.paths)
        store = ReportStore(self.paths.reports_dir)
        try:
            result = run_full_audit(adapter=self.adapter_factory(), store=store)
            dash_path = write_efficiency_dashboard(store)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": redact_secrets(str(exc))}
        return {
            "ok": True,
            "read_only": True,
            "directory": str(Path(result["directory"]).relative_to(self.paths.root)) if Path(result["directory"]).is_relative_to(self.paths.root) else result["directory"],
            "executive": _jsonable(result["executive"]),
            "dashboard": json.loads(dash_path.read_text(encoding="utf-8")),
        }


def _make_handler(app: DashboardApp, port_ref: dict[str, int]):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ads-agents-ui"
        sys_version = ""

        def log_message(self, fmt: str, *args: Any) -> None:  # quiet by default
            return

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").strip()
            if host.startswith("["):
                name = host[1:].split("]", 1)[0]
            else:
                name = host.rsplit(":", 1)[0] if ":" in host else host
            return name in LOOPBACK_HOSTS

        def _send(self, status: int, payload: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(payload)

        def _json(self, status: int, data: dict[str, Any]) -> None:
            self._send(status, json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8")

        def _dispatch(self, method: str) -> None:
            if not self._host_ok():
                self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "loopback host required"})
                return
            path = self.path.split("?", 1)[0]
            if method == "GET" and path in {"/", "/index.html"}:
                self._send(200, app.index_html(), "text/html; charset=utf-8")
                return
            if method == "GET" and path.startswith("/static/"):
                found = app.static_file(path.removeprefix("/static/"))
                if found:
                    self._send(200, found[0], found[1])
                else:
                    self._json(404, {"ok": False, "error": "not found"})
                return
            if not path.startswith("/api/"):
                self._json(404, {"ok": False, "error": "not found"})
                return
            if not secrets.compare_digest(self.headers.get("X-UI-Token", ""), app.token):
                self._json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "missing or invalid UI token"})
                return
            body: dict[str, Any] = {}
            if method == "POST":
                if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                    self._json(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"ok": False, "error": "application/json required"})
                    return
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": "body too large"})
                    return
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except json.JSONDecodeError:
                    self._json(400, {"ok": False, "error": "invalid JSON"})
                    return
                if not isinstance(body, dict):
                    self._json(400, {"ok": False, "error": "JSON object required"})
                    return
            try:
                status, data = app.api(method, path, body)
            except Exception as exc:  # noqa: BLE001
                status, data = 500, {"ok": False, "error": redact_secrets(f"{type(exc).__name__}: {exc}")}
            self._json(status, data)

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch("POST")

    return Handler


class DashboardServer:
    def __init__(self, app: DashboardApp, host: str = "127.0.0.1", port: int = 8765) -> None:
        if host not in LOOPBACK_HOSTS:
            raise ValueError(f"The dashboard only binds to loopback (127.0.0.1); refusing host {host!r}.")
        self.app = app
        port_ref: dict[str, int] = {}
        server_cls = ThreadingHTTPServer
        if host == "::1":
            import socket

            class _V6(ThreadingHTTPServer):
                address_family = socket.AF_INET6

            server_cls = _V6
        self.httpd = server_cls((host, port), _make_handler(app, port_ref))
        self.httpd.daemon_threads = True
        self.host = host
        self.port = self.httpd.server_address[1]
        port_ref["port"] = self.port
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}/"

    def start(self) -> "DashboardServer":
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def serve_forever(self) -> None:
        self.httpd.serve_forever()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def run(host: str = "127.0.0.1", port: int = 8765, *, open_browser: bool = True) -> int:
    app = DashboardApp()
    server = DashboardServer(app, host=host, port=port)
    print(f"Google Ads AI Automation dashboard: {server.url}")
    print("Bound to loopback only. Press Ctrl+C to stop.")
    if open_browser:
        import webbrowser

        threading.Timer(0.5, lambda: webbrowser.open(server.url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.httpd.server_close()
    return 0
