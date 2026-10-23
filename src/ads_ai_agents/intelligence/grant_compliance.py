"""Google Ad Grants compliance auditor + destination policy.

Website policy + account rules + Ads certifications checklist.
Live HTTP probes (no Ads mutate). See docs/AD_GRANTS_COMPLIANCE.md.
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ads_ai_agents.logging_utils import configure_logging

logger = configure_logging()

ROOT = Path(__file__).resolve().parents[3]
FACTS_PATH = ROOT / "config" / "product_facts.json"

# Official references
POLICY_URLS = {
    "website": "https://support.google.com/grants/answer/1657899",
    "compliance_guide": "https://support.google.com/grants/answer/9314402",
    "account_management": "https://support.google.com/grants/answer/117827",
    "conversions": "https://support.google.com/grants/answer/9038650",
    "healthcare_ads": "https://support.google.com/adspolicy/answer/176031",
}


@dataclass
class Probe:
    url: str
    ok: bool
    status: int | None = None
    final_url: str | None = None
    http_to_https: bool | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class GrantComplianceReport:
    overall: str  # PASS | FAIL | WARN
    website_verdict: str
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    permissions_checklist: list[dict[str, Any]] = field(default_factory=list)
    probes: list[dict[str, Any]] = field(default_factory=list)
    grant_safe_destinations: dict[str, Any] = field(default_factory=dict)
    reactivation_steps: list[str] = field(default_factory=list)
    policy_urls: dict[str, str] = field(default_factory=dict)
    config_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_facts() -> dict[str, Any]:
    if FACTS_PATH.exists():
        return json.loads(FACTS_PATH.read_text(encoding="utf-8"))
    return {}


def grant_safe_mode_enabled(facts: dict[str, Any] | None = None) -> bool:
    env = (os.getenv("ADS_GRANT_SAFE_DESTINATIONS") or "").strip().lower()
    if env in {"1", "true", "yes", "on"}:
        return True
    if env in {"0", "false", "no", "off"}:
        return False
    facts = facts if facts is not None else load_facts()
    return bool((facts.get("grant_destinations") or {}).get("enforce_for_grant_account", True))


def grant_public_urls(facts: dict[str, Any] | None = None) -> dict[str, str]:
    """Nonprofit-safe finals for Ad Grants (mission site, not SaaS login)."""
    facts = facts or load_facts()
    gd = facts.get("grant_destinations") or {}
    return {
        "home": gd.get("home") or "https://www.example.com/",
        "help_request": gd.get("help_request")
        or "https://www.example.com/help-request/",
        "contact": gd.get("contact") or "https://www.example.com/contact/",
        "privacy": gd.get("privacy") or "https://www.example.com/privacy-policy/",
        "about_ngo": gd.get("about_ngo") or "",  # required by Website policy
        "donate": gd.get("donate") or "",  # optional for Ad Grants
    }


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


def _probe(url: str, *, follow: bool = True, timeout: float = 12.0) -> Probe:
    ctx = _ssl_context()
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "ads-ai-agents-grant-compliance/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            final = resp.geturl()
            status = getattr(resp, "status", None) or resp.getcode()
            notes: list[str] = []
            if url.startswith("http://"):
                notes.append(
                    "http_to_https" if final.startswith("https://") else "NO_HTTPS_REDIRECT"
                )
            return Probe(
                url=url,
                ok=200 <= int(status) < 400,
                status=int(status),
                final_url=final,
                http_to_https=(final.startswith("https://") if url.startswith("http://") else None),
                notes=notes,
            )
    except urllib.error.HTTPError as exc:
        return Probe(url=url, ok=False, status=int(exc.code), notes=[str(exc.reason)[:80]])
    except Exception as exc:  # noqa: BLE001
        # Fallback: curl often has a fuller CA store than the local Python build.
        try:
            import subprocess

            r = subprocess.run(
                ["curl", "-sI", "-L", "--max-time", str(int(timeout)), url],
                capture_output=True,
                text=True,
                check=False,
            )
            headers = r.stdout or ""
            status = None
            for line in headers.splitlines():
                if line.upper().startswith("HTTP/"):
                    parts = line.split()
                    if len(parts) >= 2 and parts[1].isdigit():
                        status = int(parts[1])
            final = url
            if "location:" in headers.lower():
                for line in headers.splitlines():
                    if line.lower().startswith("location:"):
                        loc = line.split(":", 1)[1].strip()
                        if loc:
                            final = loc
            notes = ["via_curl_fallback", str(exc)[:80]]
            http_to_https = None
            if url.startswith("http://"):
                http_to_https = (final or "").startswith("https://") or "location: https://" in headers.lower()
                # If last status is 200 on https effective URL from -L, treat as redirected.
                if status and 200 <= status < 400 and "https://" in headers.lower():
                    # Best-effort: curl -L ends on final scheme if redirected.
                    http_to_https = True if "https://" in (final or "") else http_to_https
                if status == 200 and url.startswith("http://") and final.startswith("http://"):
                    # Still on http — confirm with GET effective URL
                    r2 = subprocess.run(
                        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code} %{url_effective}",
                         "-L", "--max-time", str(int(timeout)), url],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    parts = (r2.stdout or "").strip().split()
                    if len(parts) >= 2:
                        status = int(parts[0]) if parts[0].isdigit() else status
                        final = parts[1]
                        http_to_https = final.startswith("https://")
                notes.append("http_to_https" if http_to_https else "NO_HTTPS_REDIRECT")
            ok = status is not None and 200 <= status < 400
            return Probe(
                url=url,
                ok=ok,
                status=status,
                final_url=final,
                http_to_https=http_to_https,
                notes=notes,
            )
        except Exception as exc2:  # noqa: BLE001
            return Probe(url=url, ok=False, notes=[str(exc)[:80], str(exc2)[:80]])


def _permissions_checklist(*, website_fail: bool) -> list[dict[str, Any]]:
    return [
        {
            "id": "website_policy",
            "priority": "P0",
            "status": "FAIL" if website_fail else "CHECK",
            "title": "Ad Grants Website policy",
            "action": "Fix nonprofit mission site + HTTPS redirects; keep Grant finals on the approved owned domain",
            "url": POLICY_URLS["website"],
            "owner": "website + Ads admin",
        },
        {
            "id": "healthcare_certification",
            "priority": "P1",
            "status": "CHECK_UI",
            "title": "Healthcare certification (only when explicitly required)",
            "action": (
                "Apply only if Google shows a category-specific certification request. "
                "HEALTH_IN_PERSONALIZED_ADS / Approved (limited) is a personalization "
                "restriction, not a certification requirement."
            ),
            "url": POLICY_URLS["healthcare_ads"],
            "owner": "Ads admin (ngo@example.com)",
        },
        {
            "id": "disapproved_assets",
            "priority": "P1",
            "status": "CHECK_UI",
            "title": "Check disapproved names/logos",
            "action": "Replace and relink assets only when Ads → Policy issues shows DISAPPROVED.",
            "owner": "Ads admin",
        },
        {
            "id": "conversion_tracking",
            "priority": "P0",
            "status": "CHECK",
            "title": "Meaningful conversion ≥1 / month",
            "action": "Primary: help request / login_success / chat_message_sent — not homepage views",
            "url": POLICY_URLS["conversions"],
            "owner": "Ads + GTM",
        },
        {
            "id": "ctr_5pct",
            "priority": "P1",
            "status": "MONITOR",
            "title": "Account CTR ≥ 5% monthly",
            "action": "Pause high-impr low-CTR keywords; 2 months below → deactivation",
            "url": POLICY_URLS["compliance_guide"],
            "owner": "Ads ops",
        },
        {
            "id": "qs_gte_3",
            "priority": "P1",
            "status": "MONITOR",
            "title": "No QS 1–2 keywords",
            "action": "Automated rule: pause Quality Score < 3 daily",
            "url": POLICY_URLS["compliance_guide"],
            "owner": "Ads ops",
        },
        {
            "id": "structure",
            "priority": "P1",
            "status": "MONITOR",
            "title": "≥2 ad groups/campaign + ≥2 sitelinks",
            "action": "Search structure; enterprise target 16 sitelinks",
            "owner": "Ads ops / CLI",
        },
        {
            "id": "smart_bidding",
            "priority": "P1",
            "status": "CHECK",
            "title": "Conversion-based Smart Bidding",
            "action": "Maximize conversions / tCPA / tROAS on Grant campaigns",
            "url": POLICY_URLS["account_management"],
            "owner": "Ads admin",
        },
        {
            "id": "annual_survey",
            "priority": "P2",
            "status": "CHECK_UI",
            "title": "Annual Ad Grants program survey",
            "action": "Complete survey from login email / in-product notification",
            "owner": "Ads admin",
        },
        {
            "id": "api_token",
            "priority": "P1",
            "status": "WARN",
            "title": "Developer token Basic/Standard (not Explorer)",
            "action": "Upgrade token to avoid RESOURCE_EXHAUSTED explorer ops cap",
            "owner": "API admin",
        },
        {
            "id": "nonprofit_verification",
            "priority": "P0",
            "status": "CHECK",
            "title": "Google for Nonprofits / Goodstack verification current",
            "action": "Confirm NGO status still verified for Ad Grants eligibility",
            "owner": "NGO admin",
        },
        {
            "id": "domain_approval",
            "priority": "P1",
            "status": "OPTIONAL",
            "title": "Additional website domain form (if using ai.*)",
            "action": "Only after ai.* meets Website policy; review 3–5 business days",
            "url": POLICY_URLS["website"],
            "owner": "NGO admin",
        },
    ]


def audit(*, include_product_domains: bool = True) -> GrantComplianceReport:
    facts = load_facts()
    safe = grant_public_urls(facts)
    blockers: list[str] = []
    warnings: list[str] = []
    probes: list[Probe] = []

    # Critical HTTP→HTTPS
    for host in ("www.example.com", "example.com", "app.example.com"):
        p = _probe(f"http://{host}/")
        probes.append(p)
        if p.http_to_https is False:
            blockers.append(f"HTTP does not redirect to HTTPS: http://{host}/")

    # Grant-safe pages
    for key in ("home", "help_request", "contact", "privacy"):
        url = safe.get(key) or ""
        if not url:
            continue
        p = _probe(url)
        probes.append(p)
        if not p.ok:
            blockers.append(f"Grant destination down ({key}): {url}")

    # Website policy requires a public mission/about page. A donation page is
    # not an Ad Grants requirement, so a missing one is reported, never blocking.
    url = (safe.get("about_ngo") or "").strip()
    if not url:
        blockers.append("Missing required Grant page config: grant_destinations.about_ngo")
    else:
        p = _probe(url)
        probes.append(p)
        if not p.ok:
            blockers.append(f"Grant page missing (about_ngo): {url}")

    donate_url = (safe.get("donate") or "").strip()
    if not donate_url:
        warnings.append(
            "No donate page configured (grant_destinations.donate) — optional for "
            "Ad Grants, but it strengthens nonprofit-mission signals"
        )
    else:
        p = _probe(donate_url)
        probes.append(p)
        if not p.ok:
            blockers.append(f"Grant page missing (donate): {donate_url}")

    approved_domains = {
        domain.strip().lower()
        for domain in (facts.get("grant_destinations") or {}).get("approved_domains", [])
        if domain and domain.strip()
    }
    if include_product_domains:
        for url in (
            "https://www.example.com/",
            "https://www.example.com/uk",
            "https://app.example.com/",
        ):
            p = _probe(url)
            probes.append(p)
            host = urlparse(url).netloc.lower()
            if host in approved_domains:
                continue
            warnings.append(
                f"Domain outside grant_destinations.approved_domains is reachable "
                f"from ads context: {host} — keep it out of Grant finals and sitelinks"
            )

    # Config: product destinations may remain for non-Grant surfaces, but live Ads
    # finals on this Grant account must use grant_destinations (enforce flag).
    preferred_domain = (
        (facts.get("grant_destinations") or {}).get("preferred_grant_domain")
        or "example.com"
    )
    ads = facts.get("ads_destinations") or {}
    unapproved_config = [
        value
        for value in (ads.get("site_root"), ads.get("chat_entry"))
        if value and urlparse(value).netloc.lower() not in approved_domains
    ]
    if unapproved_config:
        if grant_safe_mode_enabled(facts):
            warnings.append(
                "ads_destinations lists destinations outside approved_domains — OK while "
                "grant_destinations.enforce_for_grant_account=true remaps builders; "
                f"keep live Grant finals on {preferred_domain}"
            )
        else:
            blockers.append(
                "config ads_destinations points Grant traffic outside "
                "approved_domains — enable grant_destinations.enforce or set "
                "ADS_GRANT_SAFE_DESTINATIONS=1"
            )

    website_verdict = "FAIL" if blockers else "PASS"
    overall = "FAIL" if blockers else ("WARN" if warnings else "PASS")

    reactivation = [
        "Fix Website policy blockers (HTTPS redirect, About NGO page, mission-first LP).",
        f"Keep all Grant campaign finals/sitelinks on {preferred_domain} "
        "mission URLs (never app stores or login-gated chat).",
        "Apply for Healthcare certification only if Google explicitly requests a relevant category.",
        "Confirm Ads → Policy issues has no DISAPPROVED business name/logo assets.",
        "Confirm ≥1 meaningful conversion in last 30 days.",
        "Request Ad Grants account reactivation via in-product / Grants form.",
        "After API quota: enterprise-assets + pmax-strength; re-run platform grant-compliance.",
    ]

    report = GrantComplianceReport(
        overall=overall,
        website_verdict=website_verdict,
        blockers=blockers,
        warnings=warnings,
        permissions_checklist=_permissions_checklist(website_fail=bool(blockers)),
        probes=[asdict(p) for p in probes],
        grant_safe_destinations=safe,
        reactivation_steps=reactivation,
        policy_urls=POLICY_URLS,
        config_note=(
            f"ADS_GRANT_SAFE_DESTINATIONS env / grant_destinations.enforce="
            f"{grant_safe_mode_enabled(facts)}"
        ),
    )
    logger.info("grant_compliance overall=%s blockers=%s", overall, len(blockers))
    return report
