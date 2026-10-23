"""Google Tags automation: paste → connect → plan → apply → verify.

* :func:`parse_tag_input` pulls GTM / GA4 / Google Ads IDs out of a pasted GTM container
  snippet, a Google tag (gtag.js) snippet, or plain text.
* :func:`build_tag_plan` turns those IDs plus the organization profile into a GTM plan:
  Consent Mode defaults, Google tag (GA4), Conversion Linker, Google Ads conversion tags and
  GA4 key events (lead / sign-up / purchase for businesses; lead / sign-up / donation for
  nonprofits), plus the app-store click events from ``docs/GTM_STORE_CLICKS.md``.
* :func:`diff_plan` compares the plan with what is already in the container.
* :func:`apply_tag_plan` is **dry-run** unless ``ADS_AUTOMATION_MODE`` allows writes *and*
  the user confirmed; then it creates a workspace, variables, triggers, tags, a version and
  publishes it, logging every call and keeping the previous live version for rollback.
* :func:`verify_site_snippet` checks (optionally) that the site already loads the container.
  The container snippet must be installed on the site once (or via a CMS plugin); after that
  all tag changes happen in GTM without touching the site.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

PREFIX = "[ads-agents] "
ALL_PAGES_TRIGGER_ID = "2147479553"  # GTM built-in "All Pages"
CONSENT_INIT_TRIGGER_ID = "2147479572"  # GTM built-in "Consent Initialization - All Pages"

GTM_RE = re.compile(r"\bGTM-[A-Z0-9]{4,10}\b")
GA4_RE = re.compile(r"\bG-[A-Z0-9]{4,12}\b")
ADS_RE = re.compile(r"\bAW-(\d{6,12})(?:/([A-Za-z0-9_-]{4,40}))?")


# --------------------------------------------------------------------------- parse


def parse_tag_input(text: str) -> dict[str, Any]:
    """Extract and validate tag IDs from a snippet or free text."""
    raw = str(text or "")
    upper_ids = raw.replace("\\u002d", "-")
    gtm = list(dict.fromkeys(GTM_RE.findall(upper_ids.upper())))
    ga4 = list(dict.fromkeys(m for m in GA4_RE.findall(upper_ids.upper()) if not m.startswith("GTM")))
    ads: list[str] = []
    labels: dict[str, list[str]] = {}
    for num, label in ADS_RE.findall(raw):
        aw = f"AW-{num}"
        if aw not in ads:
            ads.append(aw)
        if label:
            labels.setdefault(aw, [])
            if label not in labels[aw]:
                labels[aw].append(label)
    kind = "ids"
    if "googletagmanager.com/gtm.js" in raw or "googletagmanager.com/ns.html" in raw:
        kind = "gtm_container_snippet"
    elif "googletagmanager.com/gtag/js" in raw or "gtag(" in raw:
        kind = "google_tag_snippet"
    warnings: list[str] = []
    # Typos that look like IDs but do not validate.
    for token in re.findall(r"\b(?:GTM|G|AW)-[A-Za-z0-9/_-]+", raw):
        t = token.split("/")[0].upper()
        if t not in gtm and t not in ga4 and t not in ads:
            warnings.append(f"Ignored invalid ID: {token[:40]}")
    if not (gtm or ga4 or ads):
        warnings.append("No GTM-, G- or AW- IDs found.")
    if len(gtm) > 1:
        warnings.append("Several GTM containers found; the first one is used.")
    return {"kind": kind, "gtm": gtm, "ga4": ga4, "ads": ads, "ads_labels": labels, "warnings": warnings}


# --------------------------------------------------------------------------- plan


@dataclass
class PlanItem:
    kind: str  # variable | trigger | tag
    name: str
    body: dict[str, Any]
    purpose: str
    triggers: list[str] = field(default_factory=list)  # trigger names (or built-in ids)
    action: str = "create"  # create | update | unchanged

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TagPlan:
    container_public_id: str
    items: list[PlanItem]
    key_events: list[dict[str, str]]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "container_public_id": self.container_public_id,
            "items": [i.to_dict() for i in self.items],
            "key_events": self.key_events,
            "notes": self.notes,
            "summary": {
                a: sum(1 for i in self.items if i.action == a) for a in ("create", "update", "unchanged")
            },
        }


def _p(key: str, value: str, typ: str = "template") -> dict[str, str]:
    return {"type": typ, "key": key, "value": value}


def key_events_for(org_type: str) -> list[dict[str, str]]:
    if org_type == "nonprofit":
        return [
            {"event": "generate_lead", "label": "Contact / volunteer / help request form sent", "role": "primary"},
            {"event": "sign_up", "label": "Newsletter or account sign-up", "role": "secondary"},
            {"event": "donation", "label": "Donation completed", "role": "primary"},
        ]
    return [
        {"event": "generate_lead", "label": "Lead / contact form sent", "role": "primary"},
        {"event": "sign_up", "label": "Account sign-up", "role": "secondary"},
        {"event": "purchase", "label": "Purchase completed", "role": "primary"},
    ]


def tracking_from_facts(facts: dict[str, Any]) -> dict[str, Any]:
    t = facts.get("tracking") or {}
    return {
        "gtm": str(t.get("gtm") or ""),
        "ga4": str(t.get("ga4") or ""),
        "ads_conversion_id": str(t.get("ads_conversion_id") or ""),
        "ads_conversion_labels": dict(t.get("ads_conversion_labels") or {}),
        "gtm_api": dict(t.get("gtm_api") or {}),
    }


def build_tag_plan(facts: dict[str, Any], org: dict[str, Any] | None = None) -> TagPlan:
    from ads_ai_agents.platform.product_facts import organization_profile, store_click_events

    tracking = tracking_from_facts(facts)
    org = org or organization_profile(facts)
    org_type = org.get("type") or "business"
    events = key_events_for(org_type)
    gtm = tracking["gtm"]
    ga4 = tracking["ga4"] if not tracking["ga4"].startswith("G-XXXX") else ""
    aw = tracking["ads_conversion_id"]
    labels = tracking["ads_conversion_labels"]
    items: list[PlanItem] = []
    notes: list[str] = []

    items.append(
        PlanItem(
            "tag",
            PREFIX + "Consent Mode defaults",
            {
                "type": "html",
                "parameter": [
                    _p(
                        "html",
                        "<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}"
                        "gtag('consent','default',{ad_storage:'denied',ad_user_data:'denied',ad_personalization:'denied',"
                        "analytics_storage:'denied',wait_for_update:500});</script>",
                    )
                ],
            },
            "Consent Mode v2 defaults (denied until your consent banner updates them)",
            [CONSENT_INIT_TRIGGER_ID],
        )
    )
    if ga4:
        items.append(PlanItem("variable", PREFIX + "GA4 measurement ID", {"type": "c", "parameter": [_p("value", ga4)]}, "Reusable GA4 ID"))
        items.append(
            PlanItem("tag", PREFIX + "Google tag (GA4)", {"type": "googtag", "parameter": [_p("tagId", ga4)]}, "Loads GA4 on every page", [ALL_PAGES_TRIGGER_ID])
        )
    else:
        notes.append("No GA4 measurement ID (G-…) yet: GA4 tags are skipped.")
    if aw:
        items.append(PlanItem("tag", PREFIX + "Conversion Linker", {"type": "gclidw", "parameter": []}, "Keeps Google Ads click IDs for conversions", [ALL_PAGES_TRIGGER_ID]))
    else:
        notes.append("No Google Ads ID (AW-…) yet: conversion tags are skipped.")

    for ev in events:
        trig = PREFIX + f"Event {ev['event']}"
        items.append(
            PlanItem(
                "trigger",
                trig,
                {
                    "type": "customEvent",
                    "customEventFilter": [
                        {"type": "equals", "parameter": [_p("arg0", "{{_event}}"), _p("arg1", ev["event"])]}
                    ],
                },
                f"Fires when your site pushes dataLayer event '{ev['event']}' ({ev['label']})",
            )
        )
        if ga4:
            items.append(
                PlanItem(
                    "tag",
                    PREFIX + f"GA4 key event {ev['event']}",
                    {"type": "gaawe", "parameter": [_p("eventName", ev["event"]), _p("measurementIdOverride", ga4)]},
                    f"GA4 key event ({ev['role']})",
                    [trig],
                )
            )
        if aw:
            label = labels.get(ev["event"], "")
            if label:
                items.append(
                    PlanItem(
                        "tag",
                        PREFIX + f"Google Ads conversion {ev['event']}",
                        {"type": "awct", "parameter": [_p("conversionId", aw.removeprefix("AW-")), _p("conversionLabel", label)]},
                        f"Google Ads conversion ({ev['role']})",
                        [trig],
                    )
                )
            else:
                notes.append(f"Add a Google Ads conversion label for '{ev['event']}' to create its conversion tag.")

    for store in store_click_events(facts):
        if not store.get("url"):
            continue
        trig = PREFIX + f"Link click {store['match']}"
        items.append(
            PlanItem(
                "trigger",
                trig,
                {"type": "linkClick", "filter": [{"type": "contains", "parameter": [_p("arg0", "{{Click URL}}"), _p("arg1", store["match"])]}]},
                "App-store link click (see docs/GTM_STORE_CLICKS.md)",
            )
        )
        if ga4:
            items.append(
                PlanItem(
                    "tag",
                    PREFIX + f"GA4 event {store['event']}",
                    {"type": "gaawe", "parameter": [_p("eventName", store["event"]), _p("measurementIdOverride", ga4)]},
                    "Secondary conversion (observe only, never for bidding)",
                    [trig],
                )
            )
    if org_type == "nonprofit":
        notes.append("Nonprofit: 'donation' and 'generate_lead' are primary; keep page views and scrolls out of Ad Grants conversions.")
    return TagPlan(gtm, items, events, notes)


def _comparable(body: dict[str, Any]) -> str:
    keep = {k: body.get(k) for k in ("type", "parameter", "customEventFilter", "filter") if body.get(k) is not None}
    return json.dumps(keep, sort_keys=True)


def diff_plan(plan: TagPlan, existing: dict[str, list[dict[str, Any]]] | None) -> TagPlan:
    """Mark each plan item create / update / unchanged against existing container entities."""
    existing = existing or {}
    by_kind = {"tag": existing.get("tags", []), "trigger": existing.get("triggers", []), "variable": existing.get("variables", [])}
    for item in plan.items:
        match = next((e for e in by_kind[item.kind] if e.get("name") == item.name), None)
        if match is None:
            item.action = "create"
        elif _comparable(match) == _comparable(item.body):
            item.action = "unchanged"
        else:
            item.action = "update"
    return plan


# --------------------------------------------------------------------------- apply


def _log(log_path: Path, entry: dict[str, Any]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"at": datetime.now(timezone.utc).isoformat(), **entry}
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_log(log_path: Path, limit: int = 50) -> list[dict[str, Any]]:
    if not log_path.is_file():
        return []
    lines = log_path.read_text(encoding="utf-8").splitlines()[-limit:]
    return [json.loads(line) for line in lines if line.strip()]


def write_gate(confirm: bool) -> tuple[bool, str]:
    from ads_ai_agents.adapters.automation_mode import mutations_allowed, resolve_automation_mode

    mode = resolve_automation_mode()
    if not mutations_allowed(mode):
        return False, f"Dry run: ADS_AUTOMATION_MODE={mode.value}. Switch the mode (Safety & automation mode) to publish."
    if not confirm:
        return False, "Dry run: tick the confirmation and click Publish to apply."
    return True, f"Live: ADS_AUTOMATION_MODE={mode.value}"


def apply_tag_plan(
    api: Any,
    plan: TagPlan,
    *,
    container_path: str,
    confirm: bool,
    log_path: Path,
) -> dict[str, Any]:
    allowed, reason = write_gate(confirm)
    plan_dict = plan.to_dict()
    if not allowed:
        _log(log_path, {"action": "dry_run", "container": container_path, "reason": reason, "summary": plan_dict["summary"]})
        return {"ok": True, "applied": False, "dry_run": True, "reason": reason, "plan": plan_dict}
    if not container_path:
        return {"ok": False, "applied": False, "error": "Connect a GTM container first (step 2)."}

    previous = api.live_version(container_path) or {}
    rollback_id = str(previous.get("containerVersionId") or "")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    _log(log_path, {"action": "start", "container": container_path, "rollback_version_id": rollback_id})
    steps: list[dict[str, Any]] = []
    ws = api.create_workspace(container_path, f"ads-agents {stamp}", "Created by ads-agents dashboard")
    ws_path = ws["path"]
    steps.append({"call": "create_workspace", "path": ws_path})
    _log(log_path, {"action": "create_workspace", "path": ws_path})
    existing = api.list_entities(ws_path)
    diff_plan(plan, existing)
    by_name = {e.get("name"): e for k in ("tags", "triggers", "variables") for e in existing.get(k, [])}
    trigger_ids: dict[str, str] = {}
    for kind in ("variable", "trigger", "tag"):
        for item in [i for i in plan.items if i.kind == kind]:
            body = {"name": item.name, **item.body}
            if kind == "tag":
                body["firingTriggerId"] = [trigger_ids.get(t, t) for t in item.triggers]
            if item.action == "unchanged":
                if kind == "trigger":
                    trigger_ids[item.name] = str(by_name[item.name].get("triggerId"))
                continue
            if item.action == "update":
                res = api.update_entity(by_name[item.name]["path"], body)
                call = f"update_{kind}"
            else:
                res = getattr(api, f"create_{kind}")(ws_path, body)
                call = f"create_{kind}"
            if kind == "trigger":
                trigger_ids[item.name] = str(res.get("triggerId"))
            steps.append({"call": call, "name": item.name})
            _log(log_path, {"action": call, "name": item.name, "path": res.get("path")})
    version = api.create_version(ws_path, f"ads-agents {stamp}", "Tag plan applied from the ads-agents dashboard")
    cv = version.get("containerVersion") or version
    version_path = cv.get("path")
    steps.append({"call": "create_version", "version_id": cv.get("containerVersionId")})
    _log(log_path, {"action": "create_version", "version_id": cv.get("containerVersionId")})
    api.publish_version(version_path)
    steps.append({"call": "publish_version", "version_id": cv.get("containerVersionId")})
    _log(log_path, {"action": "publish_version", "version_id": cv.get("containerVersionId"), "rollback_version_id": rollback_id})
    return {
        "ok": True,
        "applied": True,
        "dry_run": False,
        "published_version_id": cv.get("containerVersionId"),
        "rollback_version_id": rollback_id,
        "steps": steps,
        "plan": plan.to_dict(),
    }


def rollback(api: Any, *, container_path: str, version_id: str, confirm: bool, log_path: Path) -> dict[str, Any]:
    allowed, reason = write_gate(confirm)
    if not allowed:
        return {"ok": True, "applied": False, "dry_run": True, "reason": reason}
    if not (container_path and re.fullmatch(r"\d+", str(version_id or ""))):
        return {"ok": False, "error": "container and a numeric previous version id are required"}
    api.publish_version(f"{container_path}/versions/{version_id}")
    _log(log_path, {"action": "rollback_publish", "container": container_path, "version_id": version_id})
    return {"ok": True, "applied": True, "published_version_id": version_id}


# --------------------------------------------------------------------------- verify


def install_snippet(gtm_id: str) -> dict[str, str]:
    gid = gtm_id or "GTM-XXXXXXX"
    head = (
        "<!-- Google Tag Manager -->\n"
        "<script>(function(w,d,s,l,i){w[l]=w[l]||[];w[l].push({'gtm.start':\n"
        "new Date().getTime(),event:'gtm.js'});var f=d.getElementsByTagName(s)[0],\n"
        "j=d.createElement(s),dl=l!='dataLayer'?'&l='+l:'';j.async=true;j.src=\n"
        "'https://www.googletagmanager.com/gtm.js?id='+i+dl;f.parentNode.insertBefore(j,f);\n"
        f"}})(window,document,'script','dataLayer','{gid}');</script>\n"
        "<!-- End Google Tag Manager -->"
    )
    body = (
        "<!-- Google Tag Manager (noscript) -->\n"
        f'<noscript><iframe src="https://www.googletagmanager.com/ns.html?id={gid}"\n'
        'height="0" width="0" style="display:none;visibility:hidden"></iframe></noscript>\n'
        "<!-- End Google Tag Manager (noscript) -->"
    )
    return {"head": head, "body": body}


def verify_site_snippet(url: str, gtm_id: str, *, fetch: bool = False, opener: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Check whether the page loads the GTM container. Network fetch only when ``fetch=True``."""
    result: dict[str, Any] = {"url": url, "gtm": gtm_id, "checked": False, "installed": None, "install_snippet": install_snippet(gtm_id)}
    if not gtm_id:
        result["message"] = "Paste your GTM container ID first (step 1)."
        return result
    if not url:
        result["message"] = "Add your website in 'Your organization or business' to verify."
        return result
    if not fetch:
        result["message"] = "Verification fetch is off. Tick 'Fetch my site' to check the live page."
        return result
    from urllib.request import Request, urlopen

    try:
        with (opener or urlopen)(Request(url, headers={"User-Agent": "ads-agents-dashboard/0.1"}), timeout=8) as resp:
            html = resp.read(500_000).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        result["message"] = f"Could not fetch the site ({type(exc).__name__})."
        return result
    result["checked"] = True
    result["installed"] = gtm_id in html and "googletagmanager.com" in html
    result["message"] = (
        "Pass: the container snippet is on the page. Tag changes are now fully automated in GTM."
        if result["installed"]
        else "Missing: install the snippet below once (or with your CMS's GTM plugin). After that everything is automated."
    )
    return result
