from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from typing import Any

from ads_ai_agents.intelligence.report_store import ReportStore


@dataclass
class ChangeLedgerEntry:
    change_id: str
    timestamp: str
    actor: str
    agent_name: str
    source: str
    customer_id: str
    campaign_id: str
    resource_type: str
    old_state: dict[str, Any]
    new_state: dict[str, Any]
    reason: str
    evidence: dict[str, Any]
    expected_effect: str
    approval_id: str | None
    rollback_payload: dict[str, Any]
    result_after_24h: dict[str, Any] | None = None
    result_after_7d: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    proposed_state: dict[str, Any] = field(default_factory=dict)
    impact_estimate: dict[str, Any] = field(default_factory=dict)
    major_variable: str = ""
    applied: bool = False
    dry_run: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ChangeLedger:
    def __init__(self, store: ReportStore | None = None) -> None:
        self.store = store or ReportStore()
        self.path = self.store.root / "ledger" / "change_ledger.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entry: ChangeLedgerEntry) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry.to_dict(), ensure_ascii=False, default=str) + "\n")

    def sync_from_google_events(
        self,
        events: list[dict[str, Any]],
        *,
        customer_id: str,
    ) -> list[ChangeLedgerEntry]:
        created: list[ChangeLedgerEntry] = []
        for event in events:
            if event.get("error"):
                continue
            entry = ChangeLedgerEntry(
                change_id=str(uuid.uuid4()),
                timestamp=str(
                    event.get("change_event.change_date_time")
                    or event.get("at")
                    or datetime.now(timezone.utc).isoformat()
                ),
                actor=str(event.get("change_event.user_email") or event.get("user") or "unknown"),
                agent_name="change_audit",
                source=str(event.get("change_event.client_type") or event.get("client") or "google_ads"),
                customer_id=customer_id,
                campaign_id="",
                resource_type=str(
                    event.get("change_event.change_resource_type") or event.get("resource") or ""
                ),
                old_state={},
                new_state={
                    "operation": event.get("change_event.resource_change_operation")
                    or event.get("operation"),
                    "resource_name": event.get("change_event.change_resource_name"),
                    "changed_fields": event.get("change_event.changed_fields"),
                },
                reason="Imported from Google Ads change_event (may be incomplete vs UI).",
                evidence=event,
                expected_effect="audit_only",
                approval_id=None,
                rollback_payload={},
                metadata={"import": "change_event", "incomplete_warning": True},
            )
            self.append(entry)
            created.append(entry)
        # also persist daily JSON snapshot of imports
        day_dir = self.store.day_dir(kind="changes")
        self.store.write_json(day_dir, "change_events.json", [e.to_dict() for e in created])
        return created

    def record_decision(
        self,
        *,
        actor: str,
        agent_name: str,
        customer_id: str,
        reason: str,
        evidence: dict[str, Any],
        expected_effect: str,
        approval_id: str | None,
        old_state: dict[str, Any],
        new_state: dict[str, Any],
        rollback_payload: dict[str, Any],
        campaign_id: str = "",
        resource_type: str = "decision",
        source: str = "ads_ai_agents",
        proposed_state: dict[str, Any] | None = None,
        impact_estimate: dict[str, Any] | None = None,
        major_variable: str = "",
        applied: bool = False,
        dry_run: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> ChangeLedgerEntry:
        entry = ChangeLedgerEntry(
            change_id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat(),
            actor=actor,
            agent_name=agent_name,
            source=source,
            customer_id=customer_id,
            campaign_id=campaign_id,
            resource_type=resource_type,
            old_state=old_state,
            new_state=new_state,
            reason=reason,
            evidence=evidence,
            expected_effect=expected_effect,
            approval_id=approval_id,
            rollback_payload=rollback_payload,
            proposed_state=proposed_state or dict(new_state),
            impact_estimate=impact_estimate or {},
            major_variable=major_variable,
            applied=applied,
            dry_run=dry_run,
            metadata=metadata or {},
        )
        self.append(entry)
        return entry

    def iter_entries(self) -> list[ChangeLedgerEntry]:
        if not self.path.is_file():
            return []
        entries: list[ChangeLedgerEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            known = {f.name for f in fields(ChangeLedgerEntry)}
            payload = {k: v for k, v in raw.items() if k in known}
            entries.append(ChangeLedgerEntry(**payload))
        return entries

    def recent_material_changes(
        self,
        *,
        campaign_id: str = "",
        major_variable: str = "",
        within_days: int = 3,
    ) -> list[ChangeLedgerEntry]:
        cutoff = datetime.now(timezone.utc).timestamp() - within_days * 86400
        out: list[ChangeLedgerEntry] = []
        for entry in self.iter_entries():
            if not entry.applied:
                continue
            try:
                ts = datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            if ts < cutoff:
                continue
            if campaign_id and entry.campaign_id and entry.campaign_id != campaign_id:
                continue
            if major_variable and entry.major_variable and entry.major_variable != major_variable:
                continue
            out.append(entry)
        return out
