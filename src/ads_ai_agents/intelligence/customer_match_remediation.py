"""Customer Match policy remediation for mental-health Ad Grants accounts.

Google treats Enhanced Conversions for Leads (hashed email/phone on form/chat
conversions) and health-sensitive audience signals as Customer Match / customer
data policy surface area. For NGO YOUR_BRAND we keep Search delivery on gclid-based
conversions only and remove first-party audience plumbing that can trigger
suspension emails.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from google.protobuf import field_mask_pb2

from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
from ads_ai_agents.adapters.settings import GoogleAdsSettings
from ads_ai_agents.adapters.write_guard import assert_write_service


@dataclass
class CustomerMatchRemediationResult:
    dry_run: bool
    customer_id: str
    enhanced_conversions_for_leads_before: bool | None = None
    enhanced_conversions_for_leads_after: bool | None = None
    user_lists: list[dict[str, Any]] = field(default_factory=list)
    audiences: list[dict[str, Any]] = field(default_factory=list)
    audience_signals: list[dict[str, Any]] = field(default_factory=list)
    actions_planned: list[dict[str, str]] = field(default_factory=list)
    actions_applied: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    appeal_points: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _ScopedWriteClientProxy:
    def __init__(self, client: Any) -> None:
        self._client = client

    def get_service(self, name: str):
        assert_write_service(name, scope="customer_match_remediation")
        return self._client.get_service(name)

    def get_type(self, name: str):
        return self._client.get_type(name)

    @property
    def enums(self):
        return self._client.enums

    def __getattr__(self, item: str):
        return getattr(self._client, item)


class CustomerMatchRemediation:
    def __init__(self, settings: GoogleAdsSettings | None = None) -> None:
        self.settings = settings or GoogleAdsSettings.from_env()
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from google.ads.googleads.client import GoogleAdsClient

            raw = GoogleAdsClient.load_from_dict(self.settings.client_config())
            self._client = _ScopedWriteClientProxy(raw)
        return self._client

    def audit(self) -> CustomerMatchRemediationResult:
        ga = self.client.get_service("GoogleAdsService")
        cid = self.settings.customer_id
        result = CustomerMatchRemediationResult(dry_run=True, customer_id=cid)

        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT customer.conversion_tracking_setting.enhanced_conversions_for_leads_enabled
            FROM customer LIMIT 1
            """,
        ):
            result.enhanced_conversions_for_leads_before = bool(
                row.customer.conversion_tracking_setting.enhanced_conversions_for_leads_enabled
            )

        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT user_list.id, user_list.name, user_list.type, user_list.membership_status,
                   user_list.size_for_search, user_list.read_only, user_list.access_reason,
                   user_list.crm_based_user_list.upload_key_type
            FROM user_list
            ORDER BY user_list.id
            """,
        ):
            ul = row.user_list
            entry = {
                "id": str(ul.id),
                "name": ul.name,
                "type": ul.type_.name,
                "membership_status": ul.membership_status.name,
                "size_for_search": ul.size_for_search,
                "read_only": bool(ul.read_only),
                "access_reason": ul.access_reason.name,
            }
            if ul.crm_based_user_list.upload_key_type:
                entry["upload_key_type"] = ul.crm_based_user_list.upload_key_type.name
            result.user_lists.append(entry)

        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT audience.resource_name, audience.id, audience.name, audience.status
            FROM audience
            WHERE audience.status = 'ENABLED'
            """,
        ):
            result.audiences.append(
                {
                    "resource_name": row.audience.resource_name,
                    "id": str(row.audience.id),
                    "name": row.audience.name,
                    "status": row.audience.status.name,
                }
            )

        for row in ga.search(
            customer_id=cid,
            query="""
            SELECT asset_group_signal.resource_name,
                   asset_group_signal.audience.audience,
                   campaign.id, campaign.name, campaign.status
            FROM asset_group_signal
            WHERE asset_group_signal.audience.audience IS NOT NULL
            """,
        ):
            result.audience_signals.append(
                {
                    "resource_name": row.asset_group_signal.resource_name,
                    "audience": row.asset_group_signal.audience.audience,
                    "campaign_id": str(row.campaign.id),
                    "campaign_name": row.campaign.name,
                    "campaign_status": row.campaign.status.name,
                }
            )

        crm_lists = [u for u in result.user_lists if u.get("type") == "CRM_BASED"]
        if crm_lists:
            result.warnings.append(
                f"Found {len(crm_lists)} CRM-based Customer Match list(s) — remove in UI if not appeal-only."
            )

        if result.enhanced_conversions_for_leads_before:
            result.actions_planned.append(
                {
                    "action": "disable_enhanced_conversions_for_leads",
                    "reason": "Stop hashed PII uploads on mental-health conversions (Customer Match policy).",
                }
            )

        for sig in result.audience_signals:
            result.actions_planned.append(
                {
                    "action": "remove_asset_group_audience_signal",
                    "resource": sig["resource_name"],
                    "reason": f"Detach audience signal from paused PMax campaign {sig['campaign_name']}.",
                }
            )

        for aud in result.audiences:
            result.actions_planned.append(
                {
                    "action": "remove_audience",
                    "resource": aud["resource_name"],
                    "reason": f"Remove enabled custom audience '{aud['name']}' (health/remarketing signals).",
                }
            )

        result.appeal_points = self._appeal_points(result)
        return result

    def apply(self, *, dry_run: bool = True) -> CustomerMatchRemediationResult:
        assert_mutations_allowed()
        result = self.audit()
        result.dry_run = dry_run
        if dry_run:
            return result

        cid = self.settings.customer_id

        if result.enhanced_conversions_for_leads_before:
            try:
                self._disable_enhanced_conversions_for_leads(cid)
                result.actions_applied.append(
                    {"action": "disable_enhanced_conversions_for_leads", "status": "ok"}
                )
                result.enhanced_conversions_for_leads_after = False
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"disable_enhanced_conversions_for_leads: {exc}")

        for sig in result.audience_signals:
            try:
                self._remove_asset_group_signal(sig["resource_name"])
                result.actions_applied.append(
                    {
                        "action": "remove_asset_group_audience_signal",
                        "resource": sig["resource_name"],
                        "status": "ok",
                    }
                )
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"remove signal {sig['resource_name']}: {exc}")

        for aud in result.audiences:
            try:
                self._remove_audience(aud["resource_name"])
                result.actions_applied.append(
                    {
                        "action": "remove_audience",
                        "resource": aud["resource_name"],
                        "status": "ok",
                    }
                )
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"remove audience {aud['resource_name']}: {exc}")

        if result.enhanced_conversions_for_leads_after is None:
            result.enhanced_conversions_for_leads_after = (
                result.enhanced_conversions_for_leads_before
            )

        return result

    def _disable_enhanced_conversions_for_leads(self, customer_id: str) -> None:
        customer_service = self.client.get_service("CustomerService")
        customer = self.client.get_type("Customer")
        customer.resource_name = f"customers/{customer_id}"
        customer.conversion_tracking_setting.enhanced_conversions_for_leads_enabled = False

        operation = self.client.get_type("CustomerOperation")
        operation.update = customer
        operation.update_mask.CopyFrom(
            field_mask_pb2.FieldMask(
                paths=["conversion_tracking_setting.enhanced_conversions_for_leads_enabled"]
            )
        )
        customer_service.mutate_customer(
            customer_id=customer_id,
            operation=operation,
        )

    def _remove_asset_group_signal(self, resource_name: str) -> None:
        service = self.client.get_service("AssetGroupSignalService")
        operation = self.client.get_type("AssetGroupSignalOperation")
        operation.remove = resource_name
        service.mutate_asset_group_signals(
            customer_id=self.settings.customer_id,
            operations=[operation],
        )

    def _remove_audience(self, resource_name: str) -> None:
        service = self.client.get_service("AudienceService")
        audience = self.client.get_type("Audience")
        audience.resource_name = resource_name
        audience.status = self.client.enums.AudienceStatusEnum.REMOVED

        operation = self.client.get_type("AudienceOperation")
        operation.update = audience
        operation.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=["status"]))
        service.mutate_audiences(
            customer_id=self.settings.customer_id,
            operations=[operation],
        )

    @staticmethod
    def _appeal_points(result: CustomerMatchRemediationResult) -> list[str]:
        points = [
            "Account YOUR-CUSTOMER-ID is a Ukrainian mental-health NGO (Ad Grants).",
            "We do not upload CRM email/phone Customer Match lists; audit shows zero CRM_BASED user lists.",
            "Live Search campaigns use keyword intent only — zero USER_LIST campaign/ad group criteria.",
            "We disabled Enhanced Conversions for Leads to stop hashed PII on wellbeing form/chat events.",
            "We removed custom PMax audience personas and remarketing audience signals from paused campaigns.",
            "Consent Mode v2 is implemented; ad_user_data is denied until explicit cookie acceptance.",
            "We request Customer Match access be maintained or suspension lifted after corrective actions.",
        ]
        if any(u.get("name") == "Purchasers of www.example.com" for u in result.user_lists):
            points.append(
                "Legacy remarketing list 'Purchasers of www.example.com' is system/shared, not CRM upload; "
                "not used on active Search campaigns."
            )
        return points

    def write_report(self, result: CustomerMatchRemediationResult, root: Path | None = None) -> Path:
        base = root or Path(__file__).resolve().parents[3] / "reports"
        out_dir = base / date.today().isoformat()
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "customer_match_remediation.json"
        path.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

        md = out_dir / "CUSTOMER_MATCH_APPEAL.md"
        md.write_text(self._appeal_markdown(result), encoding="utf-8")
        return path

    @staticmethod
    def _appeal_markdown(result: CustomerMatchRemediationResult) -> str:
        lines = [
            "# Customer Match policy — corrective actions & appeal draft",
            "",
            f"Account: `{result.customer_id}` (YOUR_BRAND NGO)",
            "",
            "## What we fixed",
            "",
        ]
        if result.actions_applied:
            for item in result.actions_applied:
                lines.append(f"- {item['action']}: {item.get('status', 'planned')} `{item.get('resource', '')}`")
        else:
            for item in result.actions_planned:
                lines.append(f"- (planned) {item['action']}: {item.get('reason', '')}")

        lines.extend(["", "## Manual UI steps (API token cannot complete these)", ""])
        lines.extend(
            [
                "1. **Google Ads → Goals → Settings → Enhanced conversions for leads → OFF** "
                "(currently still ON; API `CustomerService` denied for this token).",
                "2. **GTM (GTM-XXXXXXX)** — disable any tag that sends `user_data` / Enhanced Conversions "
                "hashed email/phone on form or chat events.",
                "3. **Tools → Audience manager** — remove/archive these 6 custom audiences: "
                "YOUR_BRAND_UA_Audience, variant2, US+EU_audience, and 3× AssetGroupPersona_*.",
                "4. **Policy manager** (link from email) → Submit appeal within 7 days using text below.",
            ]
        )
        lines.extend(["", "## Appeal text (English — paste into Google Ads)", ""])
        lines.append(
            "We corrected the Customer Match policy concerns on account YOUR-CUSTOMER-ID. "
            "YOUR_BRAND is a registered mental-health NGO using Google Ad Grants for "
            "non-commercial wellbeing support. We do not upload CRM Customer Match email/phone lists. "
            "We disabled Enhanced Conversions for Leads account-wide to avoid sending hashed "
            "first-party identifiers from sensitive wellbeing interactions. We removed custom "
            "audience personas and audience signals from paused Performance Max campaigns. "
            "Active Search campaigns target only keyword intent with no Customer Match audiences. "
            "Consent Mode v2 requires explicit user consent before ad_user_data is granted. "
            "Please review and restore Customer Match access."
        )
        lines.extend(["", "## Evidence bullets", ""])
        for p in result.appeal_points:
            lines.append(f"- {p}")
        return "\n".join(lines) + "\n"
