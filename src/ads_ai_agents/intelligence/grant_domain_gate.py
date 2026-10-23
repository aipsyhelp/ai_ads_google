"""Ad Grants domain allowlist — only owned/approved nonprofit domains may be advertised.

Google: "you are allowed to advertise only the approved website domains."
Third-party stores (apple.com, play.google.com) and YouTube are NOT approved.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[3]
FACTS_PATH = ROOT / "config" / "product_facts.json"

# Fail-closed default: only the established owned advertising website.
DEFAULT_APPROVED = ("www.example.com",)

# Owned hostnames that no longer serve the site over valid HTTPS (for example a
# legacy www host with an expired certificate while the apex sends HSTS
# includeSubDomains). An ad landing there is a guaranteed lost click.
# Configure them in product_facts.json → grant_destinations.unserved_hosts.
UNSERVED_HOSTS: tuple[str, ...] = ()

# Hard defaults — product stores / social are NEVER Grant-safe.
THIRD_PARTY_BLOCKED = (
    "apple.com",
    "apps.apple.com",
    "play.google.com",
    "youtu.be",
    "youtube.com",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "t.me",
    "telegram.me",
    "stripe.com",
)
ALWAYS_BLOCKED_HOST_SUFFIXES = THIRD_PARTY_BLOCKED


def _load_facts() -> dict[str, Any]:
    if FACTS_PATH.exists():
        return json.loads(FACTS_PATH.read_text(encoding="utf-8"))
    return {}


def approved_hosts(facts: dict[str, Any] | None = None) -> set[str]:
    facts = facts if facts is not None else _load_facts()
    gd = facts.get("grant_destinations") or {}
    hosts = set(DEFAULT_APPROVED)
    for h in gd.get("approved_domains") or []:
        hosts.add(str(h).lower().removeprefix("www."))
        hosts.add("www." + str(h).lower().removeprefix("www."))
    # Normalize: always keep bare + www
    out: set[str] = set()
    for h in hosts:
        h = h.lower().removeprefix("www.")
        out.add(h)
        out.add("www." + h)
    return out


def blocked_hosts(facts: dict[str, Any] | None = None) -> set[str]:
    """Hosts explicitly quarantined from Ad Grants until policy approval."""
    facts = facts if facts is not None else _load_facts()
    gd = facts.get("grant_destinations") or {}
    return {
        host_of(str(url)).removeprefix("www.")
        for url in gd.get("blocked_for_grant") or []
        if host_of(str(url))
    }


def host_of(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower().split(":")[0]
    except Exception:  # noqa: BLE001
        return ""


def is_third_party_host(host: str) -> bool:
    h = (host or "").lower().removeprefix("www.")
    if not h:
        return True
    for suf in THIRD_PARTY_BLOCKED:
        if h == suf or h.endswith("." + suf):
            return True
    return False


def unserved_hosts(facts: dict[str, Any] | None = None) -> set[str]:
    facts = facts if facts is not None else _load_facts()
    gd = facts.get("grant_destinations") or {}
    return {str(h).lower() for h in (gd.get("unserved_hosts") or [])} | set(UNSERVED_HOSTS)


def is_unserved_host(host: str, facts: dict[str, Any] | None = None) -> bool:
    """True for owned hostnames that no longer serve the site over valid HTTPS."""
    return (host or "").lower().split(":")[0] in unserved_hosts(facts)


def is_blocked_host(
    host: str, *, approved: set[str] | None = None, facts: dict[str, Any] | None = None
) -> bool:
    """True if unserved, third-party, OR not on approved owned-domain list."""
    if is_unserved_host(host, facts):
        return True
    h = (host or "").lower().removeprefix("www.")
    if not h:
        return True
    if is_third_party_host(h):
        return True
    appr = approved if approved is not None else approved_hosts()
    bare_approved = {a.removeprefix("www.") for a in appr}
    return h not in bare_approved


def is_grant_allowed_url(
    url: str,
    *,
    facts: dict[str, Any] | None = None,
    third_party_only: bool = False,
) -> bool:
    """True if URL may remain in Grant ads.

    third_party_only=True → only apple/play/youtube/… are rejected (Google email scope).
    third_party_only=False → must be on approved_domains allowlist.
    """
    raw = (url or "").strip()
    if not raw.startswith("http"):
        return False
    h = host_of(raw)
    if is_unserved_host(h, facts):
        return False
    if third_party_only:
        return not is_third_party_host(h)
    if h.removeprefix("www.") in blocked_hosts(facts):
        return False
    if is_blocked_host(h, approved=approved_hosts(facts), facts=facts):
        return False
    return True


def assert_grant_url(url: str, *, facts: dict[str, Any] | None = None) -> None:
    if not is_grant_allowed_url(url, facts=facts):
        raise ValueError(
            f"Ad Grants domain gate blocked URL (not approved owned domain): {url!r}"
        )


@dataclass
class DomainGateReport:
    allowed: list[str] = field(default_factory=list)
    blocked: list[dict[str, str]] = field(default_factory=list)
    approved_domains: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def gate_urls(urls: list[str], *, facts: dict[str, Any] | None = None) -> DomainGateReport:
    facts = facts or _load_facts()
    appr = sorted({a.removeprefix("www.") for a in approved_hosts(facts)})
    report = DomainGateReport(approved_domains=appr)
    for u in urls:
        if is_grant_allowed_url(u, facts=facts):
            report.allowed.append(u)
        else:
            report.blocked.append({"url": u, "host": host_of(u), "reason": "not_approved_domain"})
    return report
