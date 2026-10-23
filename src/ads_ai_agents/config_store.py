"""Single source of truth for local project configuration.

Every local setting lives in one of these gitignored files:

* ``.env``                     credentials, account IDs, guard limits, automation mode
* ``config/product_facts.json`` brand, domains, landing pages, Ad Grants destinations, focus cities
* ``config/tenant.json``        tenant metadata (timezone, currency, locales, Ad Grants flag)
* ``config/account_ids.json``   Google Ads resource IDs used by planners and operator scripts
* ``secrets/``                  service-account key files

The setup wizard (``scripts/init_project.py``), the local dashboard (``ads-agents ui``)
and the runtime loaders (``GoogleAdsSettings.from_env``, ``load_controlled_policy``,
``load_product_facts``, ``load_account_ids``, ``load_tenant``) all use the paths and
helpers defined here, so a value saved anywhere is read back everywhere.

Secrets (developer token, Slack webhook, service-account key contents) are write-only:
:func:`config_view` reports only whether they are set, never their values.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

# --------------------------------------------------------------------------- paths

_REPO_ROOT = Path(__file__).resolve().parents[2]

LIVE_MODE_CONFIRMATION = "ENABLE LIVE WRITES"
VALID_MODES = ("READ_ONLY", "CONTROLLED_LIVE", "AUTONOMOUS_LIVE")
SECRET_ENV_KEYS = frozenset({"GOOGLE_ADS_DEVELOPER_TOKEN", "SLACK_WEBHOOK_URL"})
SERVICE_ACCOUNT_FILENAME = "google_ads_service_account.json"


def project_root() -> Path:
    """Repo root, or ``ADS_PROJECT_ROOT`` when set (used by tests and custom installs)."""
    env = os.environ.get("ADS_PROJECT_ROOT", "").strip()
    return Path(env).expanduser() if env else _REPO_ROOT


@dataclass(frozen=True)
class ProjectPaths:
    root: Path

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def env(self) -> Path:
        return self.root / ".env"

    @property
    def env_example(self) -> Path:
        return _template(self.root, ".env.example")

    @property
    def facts(self) -> Path:
        return self.config_dir / "product_facts.json"

    @property
    def facts_example(self) -> Path:
        return _template(self.root, "config/product_facts.example.json")

    @property
    def tenant(self) -> Path:
        return self.config_dir / "tenant.json"

    @property
    def tenant_example(self) -> Path:
        return _template(self.root, "config/tenant.example.json")

    @property
    def account_ids(self) -> Path:
        return self.config_dir / "account_ids.json"

    @property
    def account_ids_example(self) -> Path:
        return _template(self.root, "config/account_ids.example.json")

    @property
    def secrets_dir(self) -> Path:
        return self.root / "secrets"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def samples_dir(self) -> Path:
        return _template(self.root, "samples")


def _template(root: Path, rel: str) -> Path:
    """Templates come from the project root, falling back to the repo checkout."""
    own = root / rel
    return own if own.exists() else _REPO_ROOT / rel


def project_paths(root: Path | str | None = None) -> ProjectPaths:
    return ProjectPaths(Path(root) if root else project_root())


# --------------------------------------------------------------------------- errors


class ConfigValidationError(ValueError):
    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


@dataclass
class SaveResult:
    section: str
    written: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"section": self.section, "written": self.written, "warnings": self.warnings}


# --------------------------------------------------------------------------- file helpers


def _atomic_write(path: Path, text: str, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        if private:
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path: Path, fallback: Path | None = None) -> dict[str, Any]:
    for candidate in (path, fallback):
        if candidate and candidate.is_file():
            data = json.loads(candidate.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    return {}


def write_json(path: Path, data: dict[str, Any]) -> None:
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def parse_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'").strip('"')
    return values


def read_env(paths: ProjectPaths | None = None) -> dict[str, str]:
    paths = paths or project_paths()
    if paths.env.is_file():
        return parse_env_text(paths.env.read_text(encoding="utf-8"))
    return {}


def _env_value(value: str) -> str:
    if any(ch in value for ch in "\n\r"):
        raise ConfigValidationError({"env": "values must be single-line"})
    return value


def write_env(updates: dict[str, str], paths: ProjectPaths | None = None) -> None:
    """Update keys in ``.env`` (created from ``.env.example``), keeping comments and order."""
    paths = paths or project_paths()
    if paths.env.is_file():
        lines = paths.env.read_text(encoding="utf-8").splitlines()
    elif paths.env_example.is_file():
        lines = paths.env_example.read_text(encoding="utf-8").splitlines()
    else:
        lines = []
    pending = {k: _env_value(str(v)) for k, v in updates.items()}
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        body = stripped.lstrip("#").strip() if stripped.startswith("#") else stripped
        key = body.partition("=")[0].strip() if "=" in body else ""
        if key and key in pending and re.fullmatch(r"[A-Z0-9_]+", key):
            value = pending.pop(key)
            if stripped.startswith("#") and not value:
                out.append(line)  # keep optional commented keys commented while empty
            else:
                out.append(f"{key}={value}")
        else:
            out.append(line)
    for key, value in pending.items():
        out.append(f"{key}={value}")
    _atomic_write(paths.env, "\n".join(out) + "\n", private=True)


def deep_set(data: dict, path: tuple[str, ...], value: object) -> None:
    cur: dict = data
    for key in path[:-1]:
        nxt = cur.get(key)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[key] = nxt
        cur = nxt
    cur[path[-1]] = value


def load_facts(paths: ProjectPaths | None = None) -> dict[str, Any]:
    paths = paths or project_paths()
    return read_json(paths.facts, paths.facts_example)


def load_tenant(paths: ProjectPaths | None = None) -> dict[str, Any]:
    paths = paths or project_paths()
    return read_json(paths.tenant, paths.tenant_example)


def load_account_ids_doc(paths: ProjectPaths | None = None) -> dict[str, Any]:
    paths = paths or project_paths()
    return read_json(paths.account_ids, paths.account_ids_example)


def ensure_dirs(paths: ProjectPaths) -> None:
    for d in (paths.secrets_dir, paths.reports_dir, paths.root / "var", paths.config_dir):
        d.mkdir(parents=True, exist_ok=True)


def copy_templates(paths: ProjectPaths | None = None) -> list[str]:
    """Copy missing local files from the committed templates. Never overwrites."""
    paths = paths or project_paths()
    ensure_dirs(paths)
    copied: list[str] = []
    for src, dst in (
        (paths.env_example, paths.env),
        (paths.facts_example, paths.facts),
        (paths.tenant_example, paths.tenant),
        (paths.account_ids_example, paths.account_ids),
    ):
        if src.is_file() and not dst.exists():
            shutil.copy2(src, dst)
            if dst == paths.env:
                os.chmod(dst, 0o600)
            copied.append(str(dst))
    return copied


# --------------------------------------------------------------------------- validators

_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_customer_id(value: str) -> str:
    return re.sub(r"[\s-]", "", str(value or ""))


def _customer_id(errors: dict, key: str, value: Any, *, required: bool = True) -> str:
    cid = normalize_customer_id(value)
    if not cid:
        if required:
            errors[key] = "required"
        return ""
    if not re.fullmatch(r"\d{10}", cid):
        errors[key] = "must be 10 digits (dashes allowed, e.g. 123-456-7890)"
    return cid


def normalize_domain(value: str) -> str:
    v = str(value or "").strip().lower()
    v = v.removeprefix("https://").removeprefix("http://")
    return v.split("/", 1)[0]


def _domain(errors: dict, key: str, value: Any, *, required: bool = True) -> str:
    d = normalize_domain(value)
    if not d:
        if required:
            errors[key] = "required"
        return ""
    if not _DOMAIN_RE.match(d):
        errors[key] = f"not a valid domain: {d}"
    return d


def _list(value: Any) -> list[str]:
    if isinstance(value, str):
        items: Iterable[str] = re.split(r"[,\n]", value)
    elif isinstance(value, (list, tuple)):
        items = [str(v) for v in value]
    else:
        items = []
    seen: dict[str, None] = {}
    for item in items:
        item = item.strip()
        if item:
            seen.setdefault(item, None)
    return list(seen)


def _number(errors: dict, key: str, value: Any, lo: float, hi: float, *, integer: bool = False) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        errors[key] = "must be a number"
        return 0.0
    if integer and num != int(num):
        errors[key] = "must be a whole number"
    if not (lo <= num <= hi):
        errors[key] = f"must be between {lo:g} and {hi:g}"
    return int(num) if integer else num


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _fmt_num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


# --------------------------------------------------------------------------- wizard


@dataclass
class WizardAnswers:
    brand: str
    domain: str
    developer_token: str
    login_customer_id: str
    customer_id: str
    service_account_json: str
    app_domain: str = ""
    daily_cap: str = "329"
    is_grants: bool = True


def brand_facts(facts: dict[str, Any], *, brand: str, domain: str, app_domain: str = "") -> dict[str, Any]:
    """Derive landing pages and Ad Grants destinations from the brand and primary domain."""
    site = f"https://{domain}"
    app_domain = normalize_domain(app_domain) or f"app.{domain.split('.', 1)[-1]}"
    app_url = f"https://{app_domain}"
    facts["product"] = brand
    facts["landings"] = {"EN": f"{site}/en", "UA": f"{site}/uk", "DE": f"{site}/de"}
    ads = facts.setdefault("ads_destinations", {})
    ads["site_root"] = site + "/"
    ads["chat_entry"] = app_url + "/"
    for key in ("news", "corporate", "ngo"):
        ads[key] = {"EN": f"{site}/en/{key}", "UA": f"{site}/uk/{key}", "DE": f"{site}/de/{key}"}
    gd = facts.setdefault("grant_destinations", {})
    gd["approved_domains"] = list(dict.fromkeys([domain, domain.removeprefix("www.")]))
    gd["preferred_grant_domain"] = domain
    gd["home"] = f"{site}/en"
    gd["help_request"] = f"{site}/en"
    gd["contact"] = f"{site}/en/contact"
    gd["privacy"] = f"{site}/en/privacy"
    gd["about_ngo"] = f"{site}/en/about"
    web = facts.setdefault("web_app", {})
    web["login"] = app_url + "/"
    web["home"] = app_url + "/home"
    return facts


def tenant_id_for(brand: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", brand.lower()).strip("-") or "tenant"


def apply_wizard(answers: WizardAnswers, paths: ProjectPaths | None = None) -> SaveResult:
    """Write .env, product_facts.json and tenant.json from wizard answers (used by CLI + UI)."""
    paths = paths or project_paths()
    ensure_dirs(paths)
    errors: dict[str, str] = {}
    domain = _domain(errors, "domain", answers.domain)
    login = _customer_id(errors, "login_customer_id", answers.login_customer_id)
    customer = _customer_id(errors, "customer_id", answers.customer_id)
    cap = _number(errors, "daily_cap", answers.daily_cap, 1, 1_000_000)
    if not answers.brand.strip():
        errors["brand"] = "required"
    if not answers.developer_token.strip():
        errors["developer_token"] = "required"
    if errors:
        raise ConfigValidationError(errors)

    write_env(
        {
            "GOOGLE_ADS_DEVELOPER_TOKEN": answers.developer_token.strip(),
            "GOOGLE_ADS_LOGIN_CUSTOMER_ID": login,
            "GOOGLE_ADS_CUSTOMER_ID": customer,
            "GOOGLE_ADS_USE_SERVICE_ACCOUNT": "true",
            "GOOGLE_ADS_SERVICE_ACCOUNT_JSON": answers.service_account_json,
            "ADS_AUTOMATION_MODE": "READ_ONLY",
            "ADS_MAX_ACCOUNT_DAILY_BUDGET": _fmt_num(cap),
        },
        paths,
    )
    facts = brand_facts(load_facts(paths), brand=answers.brand.strip(), domain=domain, app_domain=answers.app_domain)
    deep_set(facts, ("grant_destinations", "enforce_for_grant_account"), bool(answers.is_grants))
    deep_set(facts, ("grant_policy", "max_daily_budget_usd"), cap)
    write_json(paths.facts, _clean_facts(facts))

    tenant = load_tenant(paths)
    tenant["tenant_id"] = tenant_id_for(answers.brand)
    tenant["display_name"] = answers.brand.strip()
    deep_set(tenant, ("google_ads", "login_customer_id"), login)
    deep_set(tenant, ("google_ads", "customer_id"), customer)
    deep_set(tenant, ("google_ads", "is_ad_grants"), bool(answers.is_grants))
    write_json(paths.tenant, tenant)
    _sync_account_ids_customer(paths, customer)
    return SaveResult("wizard", [str(paths.env), str(paths.facts), str(paths.tenant)])


def _clean_facts(facts: dict[str, Any]) -> dict[str, Any]:
    # load_product_facts() adds runtime keys; never persist them.
    return {k: v for k, v in facts.items() if k not in {"status", "path"}}


def _sync_account_ids_customer(paths: ProjectPaths, customer: str) -> None:
    if not customer:
        return
    doc = load_account_ids_doc(paths)
    if doc.get("customer_id") != customer:
        doc["customer_id"] = customer
        write_json(paths.account_ids, doc)


# --------------------------------------------------------------------------- sections


def save_credentials(payload: dict[str, Any], paths: ProjectPaths) -> SaveResult:
    result = SaveResult("credentials")
    errors: dict[str, str] = {}
    updates: dict[str, str] = {}
    token = str(payload.get("developer_token") or "").strip()
    if token:  # empty = keep the stored token
        if not re.fullmatch(r"[A-Za-z0-9_\-]{8,100}", token):
            errors["developer_token"] = "8-100 letters, digits, '-' or '_'"
        updates["GOOGLE_ADS_DEVELOPER_TOKEN"] = token
    sa_content = payload.get("service_account_json_content")
    sa_path = str(payload.get("service_account_json_path") or "").strip()
    if sa_content:
        try:
            parsed = json.loads(sa_content) if isinstance(sa_content, str) else sa_content
        except json.JSONDecodeError:
            parsed = None
        if not isinstance(parsed, dict) or parsed.get("type") != "service_account":
            errors["service_account_json_content"] = 'must be a service-account JSON key ("type": "service_account")'
        elif not errors:
            dest = paths.secrets_dir / SERVICE_ACCOUNT_FILENAME
            _atomic_write(dest, json.dumps(parsed, indent=2) + "\n", private=True)
            sa_path = str(dest)
            result.written.append(str(dest))
    if sa_path:
        updates["GOOGLE_ADS_SERVICE_ACCOUNT_JSON"] = sa_path
        if not Path(sa_path).expanduser().is_file():
            result.warnings.append("Service-account file not found yet; place it at the saved path before ping.")
    if "use_service_account" in payload:
        updates["GOOGLE_ADS_USE_SERVICE_ACCOUNT"] = "true" if _bool(payload["use_service_account"]) else "false"
    if "impersonated_email" in payload:
        email = str(payload.get("impersonated_email") or "").strip()
        if email and not _EMAIL_RE.match(email):
            errors["impersonated_email"] = "not a valid email"
        updates["GOOGLE_ADS_IMPERSONATED_EMAIL"] = email
    if errors:
        raise ConfigValidationError(errors)
    write_env(updates, paths)
    result.written.append(str(paths.env))
    return result


def save_account(payload: dict[str, Any], paths: ProjectPaths) -> SaveResult:
    errors: dict[str, str] = {}
    login = _customer_id(errors, "login_customer_id", payload.get("login_customer_id"))
    customer = _customer_id(errors, "customer_id", payload.get("customer_id"))
    if errors:
        raise ConfigValidationError(errors)
    write_env({"GOOGLE_ADS_LOGIN_CUSTOMER_ID": login, "GOOGLE_ADS_CUSTOMER_ID": customer}, paths)
    tenant = load_tenant(paths)
    deep_set(tenant, ("google_ads", "login_customer_id"), login)
    deep_set(tenant, ("google_ads", "customer_id"), customer)
    write_json(paths.tenant, tenant)
    _sync_account_ids_customer(paths, customer)
    return SaveResult("account", [str(paths.env), str(paths.tenant), str(paths.account_ids)])


def save_brand(payload: dict[str, Any], paths: ProjectPaths) -> SaveResult:
    errors: dict[str, str] = {}
    brand = str(payload.get("brand") or "").strip()
    if not brand:
        errors["brand"] = "required"
    elif len(brand) > 80:
        errors["brand"] = "max 80 characters"
    domain = _domain(errors, "domain", payload.get("domain"))
    app_domain = _domain(errors, "app_domain", payload.get("app_domain"), required=False)
    if errors:
        raise ConfigValidationError(errors)
    facts = brand_facts(load_facts(paths), brand=brand, domain=domain, app_domain=app_domain)
    write_json(paths.facts, _clean_facts(facts))
    tenant = load_tenant(paths)
    tenant["tenant_id"] = tenant_id_for(brand)
    tenant["display_name"] = brand
    write_json(paths.tenant, tenant)
    return SaveResult("brand", [str(paths.facts), str(paths.tenant)])


def save_domains(payload: dict[str, Any], paths: ProjectPaths) -> SaveResult:
    errors: dict[str, str] = {}
    approved = [normalize_domain(d) for d in _list(payload.get("approved_domains"))]
    unserved = [normalize_domain(d) for d in _list(payload.get("unserved_hosts"))]
    for label, items in (("approved_domains", approved), ("unserved_hosts", unserved)):
        bad = [d for d in items if not _DOMAIN_RE.match(d)]
        if bad:
            errors[label] = "invalid: " + ", ".join(bad)
    if not approved:
        errors["approved_domains"] = "at least one approved domain is required"
    preferred = _domain(errors, "preferred_grant_domain", payload.get("preferred_grant_domain") or (approved[0] if approved else ""))
    if preferred and approved and preferred not in approved:
        errors["preferred_grant_domain"] = "must be one of the approved domains"
    overlap = set(approved) & set(unserved)
    if overlap:
        errors["unserved_hosts"] = "cannot also be approved: " + ", ".join(sorted(overlap))
    if errors:
        raise ConfigValidationError(errors)
    facts = load_facts(paths)
    deep_set(facts, ("grant_destinations", "approved_domains"), approved)
    deep_set(facts, ("grant_destinations", "preferred_grant_domain"), preferred)
    deep_set(facts, ("grant_destinations", "unserved_hosts"), unserved)
    write_json(paths.facts, _clean_facts(facts))
    return SaveResult("domains", [str(paths.facts)])


def save_tenant(payload: dict[str, Any], paths: ProjectPaths) -> SaveResult:
    errors: dict[str, str] = {}
    tz = str(payload.get("timezone") or "").strip()
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(tz)
    except Exception:  # noqa: BLE001
        errors["timezone"] = "unknown IANA timezone (e.g. Europe/Berlin)"
    currency = str(payload.get("currency") or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        errors["currency"] = "3-letter ISO code, e.g. USD"
    locales = [loc.upper() for loc in _list(payload.get("locales"))]
    if not locales or any(not re.fullmatch(r"[A-Z]{2}", loc) for loc in locales):
        errors["locales"] = "one or more 2-letter codes, e.g. EN, DE"
    primary = str(payload.get("primary_locale") or (locales[0] if locales else "")).strip().upper()
    if primary not in locales:
        errors["primary_locale"] = "must be one of the locales"
    cities = _list(payload.get("focus_cities"))
    if len(cities) > 50 or any(len(c) > 60 for c in cities):
        errors["focus_cities"] = "max 50 cities, 60 characters each"
    alert_email = str(payload.get("alert_email") or "").strip()
    if alert_email and not _EMAIL_RE.match(alert_email):
        errors["alert_email"] = "not a valid email"
    slack = str(payload.get("slack_webhook_url") or "").strip()
    if slack and not slack.startswith("https://"):
        errors["slack_webhook_url"] = "must start with https://"
    if errors:
        raise ConfigValidationError(errors)
    tenant = load_tenant(paths)
    tenant.update({"timezone": tz, "currency": currency, "locales": locales, "primary_locale": primary})
    deep_set(tenant, ("alerts", "email"), alert_email)
    deep_set(tenant, ("alerts", "slack_webhook_url"), "")  # secret lives in .env only
    write_json(paths.tenant, tenant)
    facts = load_facts(paths)
    deep_set(facts, ("geo", "focus_cities"), cities)
    write_json(paths.facts, _clean_facts(facts))
    env_updates = {"ALERT_EMAIL": alert_email}
    if slack:
        env_updates["SLACK_WEBHOOK_URL"] = slack
    write_env(env_updates, paths)
    return SaveResult("tenant", [str(paths.tenant), str(paths.facts), str(paths.env)])


_ID_SECTIONS = {"portfolio_strategies", "assets", "core_campaigns", "city_campaigns", "youtube"}


def _validate_ids(node: Any, path: str, errors: dict[str, str], *, in_id_section: bool = False) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            sub = f"{path}.{key}" if path else key
            is_id_key = key == "id" or key.endswith("_id")
            if key in {"core_campaigns"} and isinstance(value, dict):
                bad = [k for k in value if not re.fullmatch(r"\d+", str(k))]
                if bad:
                    errors[sub] = "campaign IDs must be digits"
            if isinstance(value, (str, int)) and not isinstance(value, bool) and (is_id_key or in_id_section):
                if key in {"text"} or (in_id_section and path.endswith("core_campaigns")):
                    continue
                if str(value) and not re.fullmatch(r"\d+", str(value)):
                    errors[sub] = "must contain digits only"
            else:
                _validate_ids(value, sub, errors, in_id_section=in_id_section or key in _ID_SECTIONS)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            if in_id_section and isinstance(value, (str, int)) and str(value) and not re.fullmatch(r"\d+", str(value)):
                errors[f"{path}[{i}]"] = "must contain digits only"
            else:
                _validate_ids(value, f"{path}[{i}]", errors, in_id_section=in_id_section)


def save_account_ids(payload: dict[str, Any], paths: ProjectPaths) -> SaveResult:
    raw = payload.get("account_ids")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ConfigValidationError({"account_ids": f"invalid JSON: {exc.msg} (line {exc.lineno})"}) from None
    if not isinstance(raw, dict):
        raise ConfigValidationError({"account_ids": "must be a JSON object"})
    errors: dict[str, str] = {}
    if "customer_id" in raw:
        raw["customer_id"] = _customer_id(errors, "customer_id", raw.get("customer_id"))
    _validate_ids({k: v for k, v in raw.items() if k != "customer_id"}, "", errors)
    if errors:
        raise ConfigValidationError(errors)
    write_json(paths.account_ids, raw)
    return SaveResult("account_ids", [str(paths.account_ids)])


def save_guards(payload: dict[str, Any], paths: ProjectPaths) -> SaveResult:
    errors: dict[str, str] = {}
    change = _number(errors, "max_budget_change_percent", payload.get("max_budget_change_percent"), 0.5, 50)
    approval = _number(errors, "require_approval_above_percent", payload.get("require_approval_above_percent"), 0.5, 100)
    cooldown = _number(errors, "budget_cooldown_days", payload.get("budget_cooldown_days"), 0, 30, integer=True)
    daily = _number(errors, "max_account_daily_budget", payload.get("max_account_daily_budget"), 1, 1_000_000)
    if not errors and approval < change:
        errors["require_approval_above_percent"] = "must be at least the max budget change %"
    asset_publish = str(payload.get("asset_publish") or "approval").strip().lower()
    positive_kw = str(payload.get("positive_keywords") or "approval").strip().lower()
    for key, value in (("asset_publish", asset_publish), ("positive_keywords", positive_kw)):
        if value not in {"approval", "auto_gated"}:
            errors[key] = "approval or auto_gated"
    if errors:
        raise ConfigValidationError(errors)
    write_env(
        {
            "ADS_MAX_BUDGET_CHANGE_PERCENT": _fmt_num(change),
            "ADS_REQUIRE_APPROVAL_ABOVE_PERCENT": _fmt_num(approval),
            "ADS_BUDGET_COOLDOWN_DAYS": _fmt_num(cooldown),
            "ADS_MAX_ACCOUNT_DAILY_BUDGET": _fmt_num(daily),
            "ADS_ASSET_PUBLISH": asset_publish,
            "ADS_POSITIVE_KEYWORDS": positive_kw,
            "REQUIRE_HUMAN_APPROVAL": "true" if _bool(payload.get("require_human_approval", True)) else "false",
        },
        paths,
    )
    facts = load_facts(paths)
    deep_set(facts, ("grant_policy", "max_daily_budget_usd"), daily)
    write_json(paths.facts, _clean_facts(facts))
    return SaveResult("guards", [str(paths.env), str(paths.facts)])


def save_mode(payload: dict[str, Any], paths: ProjectPaths) -> SaveResult:
    mode = str(payload.get("mode") or "").strip().upper()
    if mode not in VALID_MODES:
        raise ConfigValidationError({"mode": "one of " + ", ".join(VALID_MODES)})
    if mode != "READ_ONLY" and str(payload.get("confirm") or "").strip() != LIVE_MODE_CONFIRMATION:
        raise ConfigValidationError({"confirm": f'type "{LIVE_MODE_CONFIRMATION}" to enable a live mode'})
    write_env({"ADS_AUTOMATION_MODE": mode}, paths)
    result = SaveResult("mode", [str(paths.env)])
    if mode != "READ_ONLY":
        result.warnings.append("Live writes are enabled. Set READ_ONLY to freeze all writes again.")
    return result


def save_grants(payload: dict[str, Any], paths: ProjectPaths) -> SaveResult:
    is_grants = _bool(payload.get("is_ad_grants"))
    enforce = _bool(payload.get("enforce_domain_gate", is_grants))
    tenant = load_tenant(paths)
    deep_set(tenant, ("google_ads", "is_ad_grants"), is_grants)
    write_json(paths.tenant, tenant)
    facts = load_facts(paths)
    deep_set(facts, ("grant_destinations", "enforce_for_grant_account"), enforce)
    write_json(paths.facts, _clean_facts(facts))
    return SaveResult("grants", [str(paths.tenant), str(paths.facts)])


ORG_TYPES = ("business", "nonprofit")


def validate_website_url(errors: dict[str, str], key: str, value: Any) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme.lower() not in {"http", "https"} or not _DOMAIN_RE.match(host) or parsed.username or parsed.password:
        errors[key] = "enter a website address like https://www.example.org"
        return ""
    if len(url) > 500:
        errors[key] = "max 500 characters"
    return url


def fetch_site_metadata(url: str, *, opener: Callable[..., Any] | None = None, timeout: float = 5.0) -> dict[str, str]:
    """Optional: read <title> and meta description of the user's own website.

    Off by default (the dashboard only calls it when "Fetch title/description" is ticked).
    ``opener`` is injectable so tests never touch the network.
    """
    from html.parser import HTMLParser
    from urllib.request import Request, urlopen

    class _Meta(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.title = ""
            self.description = ""
            self._in_title = False

        def handle_starttag(self, tag, attrs):  # type: ignore[no-untyped-def]
            a = {k.lower(): (v or "") for k, v in attrs}
            if tag == "title":
                self._in_title = True
            elif tag == "meta" and a.get("name", "").lower() in {"description", "og:description"}:
                self.description = self.description or a.get("content", "")

        def handle_endtag(self, tag):  # type: ignore[no-untyped-def]
            if tag == "title":
                self._in_title = False

        def handle_data(self, data):  # type: ignore[no-untyped-def]
            if self._in_title:
                self.title += data

    open_fn = opener or urlopen
    req = Request(url, headers={"User-Agent": "ads-agents-dashboard/0.1 (+local setup)"})
    with open_fn(req, timeout=timeout) as resp:
        raw = resp.read(200_000)
    parser = _Meta()
    parser.feed(raw.decode("utf-8", "replace"))
    return {"site_title": " ".join(parser.title.split())[:200], "site_meta_description": " ".join(parser.description.split())[:400]}


def save_organization(payload: dict[str, Any], paths: ProjectPaths, *, opener: Callable[..., Any] | None = None) -> SaveResult:
    """'Your organization or business': description and/or website, main offering, type."""
    result = SaveResult("organization")
    errors: dict[str, str] = {}
    org_type = str(payload.get("type") or "business").strip().lower()
    if org_type not in ORG_TYPES:
        errors["type"] = "business or nonprofit"
    description = str(payload.get("description") or "").strip()
    if len(description) > 2000:
        errors["description"] = "max 2000 characters"
    website = validate_website_url(errors, "website_url", payload.get("website_url"))
    offering = str(payload.get("main_offering") or "").strip()
    if len(offering) > 120:
        errors["main_offering"] = "max 120 characters"
    if not description and not website and "website_url" not in errors:
        errors["description"] = "describe your organization or enter your website (or both)"
    if errors:
        raise ConfigValidationError(errors)
    facts = load_facts(paths)
    org = dict(facts.get("organization") or {})
    org.update({"type": org_type, "description": description, "website_url": website})
    if website and _bool(payload.get("fetch_metadata", False)):
        try:
            org.update(fetch_site_metadata(website, opener=opener))
        except Exception as exc:  # noqa: BLE001
            result.warnings.append(f"Could not read the website title/description ({type(exc).__name__}); saved without it.")
    elif not website:
        org.pop("site_title", None)
        org.pop("site_meta_description", None)
    facts["organization"] = org
    facts["main_offering"] = offering
    # Business accounts cannot use Ad Grants; nonprofits opt in (default on).
    is_grants = org_type == "nonprofit" and _bool(payload.get("use_ad_grants", True))
    deep_set(facts, ("grant_destinations", "enforce_for_grant_account"), is_grants)
    write_json(paths.facts, _clean_facts(facts))
    tenant = load_tenant(paths)
    deep_set(tenant, ("google_ads", "is_ad_grants"), is_grants)
    tenant["organization_type"] = org_type
    write_json(paths.tenant, tenant)
    result.written += [str(paths.facts), str(paths.tenant)]
    if is_grants:
        result.warnings.append("Ad Grants mode is on: the Grant domain gate and compliance checks apply.")
    return result


def save_tags(payload: dict[str, Any], paths: ProjectPaths) -> SaveResult:
    """Google Tags step 1: IDs from a pasted snippet and/or typed IDs → product_facts.tracking."""
    from ads_ai_agents.intelligence.gtm_tags import GA4_RE, GTM_RE, key_events_for, parse_tag_input

    errors: dict[str, str] = {}
    parsed = parse_tag_input(str(payload.get("text") or ""))
    gtm = str(payload.get("gtm") or (parsed["gtm"][0] if parsed["gtm"] else "")).strip().upper()
    ga4 = str(payload.get("ga4") or (parsed["ga4"][0] if parsed["ga4"] else "")).strip().upper()
    aw = str(payload.get("ads_conversion_id") or (parsed["ads"][0] if parsed["ads"] else "")).strip().upper()
    if gtm and not GTM_RE.fullmatch(gtm):
        errors["gtm"] = "format GTM-XXXXXXX"
    if ga4 and not GA4_RE.fullmatch(ga4):
        errors["ga4"] = "format G-XXXXXXXXXX"
    if aw and not re.fullmatch(r"AW-\d{6,12}", aw):
        errors["ads_conversion_id"] = "format AW-123456789"
    labels_in = payload.get("ads_conversion_labels") or {}
    if not isinstance(labels_in, dict):
        errors["ads_conversion_labels"] = "must be an object {event: label}"
        labels_in = {}
    allowed_events = {e["event"] for t in ORG_TYPES for e in key_events_for(t)}
    labels: dict[str, str] = {}
    for event, label in labels_in.items():
        label = str(label or "").strip()
        if not label:
            continue
        if event not in allowed_events:
            errors[f"label_{event}"] = "unknown event"
        elif not re.fullmatch(r"[A-Za-z0-9_-]{4,40}", label):
            errors[f"label_{event}"] = "letters, digits, - or _ (4-40)"
        labels[event] = label
    if not (gtm or ga4 or aw):
        errors["text"] = "paste a GTM / Google tag snippet or type at least one ID (GTM-, G-, AW-)"
    if errors:
        raise ConfigValidationError(errors)
    facts = load_facts(paths)
    t = dict(facts.get("tracking") or {})
    t.update({"gtm": gtm, "ga4": ga4, "ads_conversion_id": aw, "ads_conversion_labels": labels})
    facts["tracking"] = t
    write_json(paths.facts, _clean_facts(facts))
    result = SaveResult("tags", [str(paths.facts)])
    result.warnings += parsed["warnings"] if payload.get("text") else []
    return result


def save_gtm_container(payload: dict[str, Any], paths: ProjectPaths) -> SaveResult:
    """Google Tags step 2: which GTM account/container the agent manages (IDs only, non-secret)."""
    errors: dict[str, str] = {}
    account_id = str(payload.get("account_id") or "").strip()
    container_id = str(payload.get("container_id") or "").strip()
    if not re.fullmatch(r"\d{1,20}", account_id):
        errors["account_id"] = "numeric GTM account id"
    if not re.fullmatch(r"\d{1,20}", container_id):
        errors["container_id"] = "numeric GTM container id"
    public_id = str(payload.get("container_public_id") or "").strip().upper()
    if public_id and not re.fullmatch(r"GTM-[A-Z0-9]{4,10}", public_id):
        errors["container_public_id"] = "format GTM-XXXXXXX"
    if errors:
        raise ConfigValidationError(errors)
    facts = load_facts(paths)
    t = dict(facts.get("tracking") or {})
    t["gtm_api"] = {
        "account_id": account_id,
        "container_id": container_id,
        "container_path": f"accounts/{account_id}/containers/{container_id}",
        "container_public_id": public_id or t.get("gtm", ""),
    }
    facts["tracking"] = t
    write_json(paths.facts, _clean_facts(facts))
    return SaveResult("gtm_container", [str(paths.facts)])


SECTIONS = {
    "organization": save_organization,
    "tags": save_tags,
    "gtm_container": save_gtm_container,
    "credentials": save_credentials,
    "account": save_account,
    "brand": save_brand,
    "domains": save_domains,
    "tenant": save_tenant,
    "account_ids": save_account_ids,
    "guards": save_guards,
    "mode": save_mode,
    "grants": save_grants,
}


def save_section(
    section: str,
    payload: dict[str, Any],
    paths: ProjectPaths | None = None,
    *,
    opener: Callable[..., Any] | None = None,
) -> SaveResult:
    paths = paths or project_paths()
    if section not in SECTIONS:
        raise ConfigValidationError({"section": f"unknown section: {section}"})
    if not isinstance(payload, dict):
        raise ConfigValidationError({"payload": "must be a JSON object"})
    ensure_dirs(paths)
    if section == "organization":
        result = save_organization(payload, paths, opener=opener)
    else:
        result = SECTIONS[section](payload, paths)
    reload_runtime(paths)
    return result


# --------------------------------------------------------------------------- runtime sync


def reload_runtime(paths: ProjectPaths | None = None) -> None:
    """Push saved values into this process: os.environ from .env and loader caches."""
    paths = paths or project_paths()
    for key, value in read_env(paths).items():
        os.environ[key] = value
    try:
        from ads_ai_agents.platform import account_ids, product_facts

        product_facts.load_product_facts.cache_clear()
        account_ids._load.cache_clear()
    except ImportError:  # pragma: no cover
        pass


def config_view(paths: ProjectPaths | None = None) -> dict[str, Any]:
    """Current configuration for display. Secrets are reported as set/not set only."""
    paths = paths or project_paths()
    env = read_env(paths)
    facts = load_facts(paths)
    tenant = load_tenant(paths)
    gd = facts.get("grant_destinations") or {}
    ga = tenant.get("google_ads") or {}
    sa_path = env.get("GOOGLE_ADS_SERVICE_ACCOUNT_JSON", "")
    org = facts.get("organization") or {}
    tracking = facts.get("tracking") or {}
    return {
        "tags": {
            "gtm": tracking.get("gtm", ""),
            "ga4": tracking.get("ga4", ""),
            "ads_conversion_id": tracking.get("ads_conversion_id", ""),
            "ads_conversion_labels": tracking.get("ads_conversion_labels", {}),
            "gtm_api": tracking.get("gtm_api", {}),
        },
        "organization": {
            "type": org.get("type", "business"),
            "description": org.get("description", ""),
            "website_url": org.get("website_url", ""),
            "main_offering": facts.get("main_offering", ""),
            "site_title": org.get("site_title", ""),
            "use_ad_grants": bool(ga.get("is_ad_grants", False)),
        },
        "credentials": {
            "developer_token_set": bool(env.get("GOOGLE_ADS_DEVELOPER_TOKEN")),
            "service_account_json_path": sa_path,
            "service_account_file_exists": bool(sa_path) and Path(sa_path).expanduser().is_file(),
            "use_service_account": env.get("GOOGLE_ADS_USE_SERVICE_ACCOUNT", "true").lower() in {"1", "true", "yes"},
            "impersonated_email": env.get("GOOGLE_ADS_IMPERSONATED_EMAIL", ""),
        },
        "account": {
            "login_customer_id": env.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID", ""),
            "customer_id": env.get("GOOGLE_ADS_CUSTOMER_ID", ""),
        },
        "brand": {
            "brand": facts.get("product", ""),
            "domain": gd.get("preferred_grant_domain", ""),
            "app_domain": normalize_domain((facts.get("web_app") or {}).get("login", "")),
        },
        "domains": {
            "approved_domains": gd.get("approved_domains", []),
            "preferred_grant_domain": gd.get("preferred_grant_domain", ""),
            "unserved_hosts": gd.get("unserved_hosts", []),
        },
        "tenant": {
            "timezone": tenant.get("timezone", ""),
            "currency": tenant.get("currency", ""),
            "locales": tenant.get("locales", []),
            "primary_locale": tenant.get("primary_locale", ""),
            "focus_cities": (facts.get("geo") or {}).get("focus_cities", []),
            "alert_email": env.get("ALERT_EMAIL", ""),
            "slack_webhook_set": bool(env.get("SLACK_WEBHOOK_URL")),
        },
        "account_ids": load_account_ids_doc(paths),
        "guards": {
            "max_budget_change_percent": env.get("ADS_MAX_BUDGET_CHANGE_PERCENT", "10"),
            "require_approval_above_percent": env.get("ADS_REQUIRE_APPROVAL_ABOVE_PERCENT", "15"),
            "budget_cooldown_days": env.get("ADS_BUDGET_COOLDOWN_DAYS", "3"),
            "max_account_daily_budget": env.get("ADS_MAX_ACCOUNT_DAILY_BUDGET", "329"),
            "asset_publish": env.get("ADS_ASSET_PUBLISH", "approval"),
            "positive_keywords": env.get("ADS_POSITIVE_KEYWORDS", "approval"),
            "require_human_approval": env.get("REQUIRE_HUMAN_APPROVAL", "true").lower() in {"1", "true", "yes"},
        },
        "mode": {"mode": env.get("ADS_AUTOMATION_MODE", "READ_ONLY") or "READ_ONLY", "confirmation_phrase": LIVE_MODE_CONFIRMATION},
        "grants": {
            "is_ad_grants": bool(ga.get("is_ad_grants", False)),
            "enforce_domain_gate": bool(gd.get("enforce_for_grant_account", False)),
        },
        "files": {
            name: {"path": str(p.relative_to(paths.root)) if p.is_relative_to(paths.root) else str(p), "exists": p.is_file()}
            for name, p in (
                ("env", paths.env),
                ("product_facts", paths.facts),
                ("tenant", paths.tenant),
                ("account_ids", paths.account_ids),
            )
        },
    }


def loaded_settings(paths: ProjectPaths | None = None) -> dict[str, Any]:
    """What the runtime loaders actually see right now (secrets masked)."""
    from ads_ai_agents.adapters.automation_mode import resolve_automation_mode
    from ads_ai_agents.adapters.settings import GoogleAdsSettings
    from ads_ai_agents.intelligence.controlled_policy import load_controlled_policy
    from ads_ai_agents.platform.account_ids import load_account_ids
    from ads_ai_agents.platform.product_facts import focus_cities, load_product_facts

    paths = paths or project_paths()
    reload_runtime(paths)
    out: dict[str, Any] = {}
    try:
        s = GoogleAdsSettings.from_env()
        out["google_ads"] = {
            "ok": True,
            "developer_token": "set" if s.developer_token else "missing",
            "login_customer_id": s.login_customer_id,
            "customer_id": s.customer_id,
            "service_account_json": str(s.service_account_json),
            "use_service_account": s.use_service_account,
        }
    except Exception as exc:  # noqa: BLE001
        from ads_ai_agents.logging_utils import redact_secrets

        out["google_ads"] = {"ok": False, "error": redact_secrets(str(exc))}
    mode = resolve_automation_mode()
    policy = load_controlled_policy()
    out["automation_mode"] = mode.value
    out["policy"] = {
        "mutations_enabled": policy.mutations_enabled,
        "max_budget_change_percent": policy.max_budget_change_percent,
        "require_approval_above_percent": policy.require_approval_above_percent,
        "cooldown_days": policy.cooldown_days,
        "max_account_daily_budget": policy.max_account_daily_budget,
        "asset_publish": policy.asset_publish,
        "positive_keywords": policy.positive_keywords,
    }
    facts = load_product_facts()
    gd = facts.get("grant_destinations") or {}
    out["product_facts"] = {
        "status": facts.get("status"),
        "product": facts.get("product"),
        "approved_domains": gd.get("approved_domains", []),
        "unserved_hosts": gd.get("unserved_hosts", []),
        "focus_cities": list(focus_cities(facts)),
        "max_daily_budget_usd": (facts.get("grant_policy") or {}).get("max_daily_budget_usd"),
    }
    from ads_ai_agents.platform.product_facts import organization_profile

    org = organization_profile(facts)
    out["organization"] = {k: org[k] for k in ("type", "main_offering", "description", "website_url", "seed_terms")}
    ids = load_account_ids()
    out["account_ids"] = {"customer_id": ids.get("customer_id"), "keys": sorted(ids)}
    tenant = load_tenant(paths)
    out["tenant"] = {
        "tenant_id": tenant.get("tenant_id"),
        "timezone": tenant.get("timezone"),
        "locales": tenant.get("locales"),
        "is_ad_grants": (tenant.get("google_ads") or {}).get("is_ad_grants"),
    }
    return out
