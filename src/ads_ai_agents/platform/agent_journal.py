"""Per-agent action journal — what / how / result for Growth OS dashboard."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from ads_ai_agents.intelligence.report_store import ReportStore


class AgentActionJournal:
    def __init__(self, store: ReportStore | None = None) -> None:
        self.store = store or ReportStore()
        self.day_dir = self.store.day_dir(kind="audit")
        self.jsonl = self.day_dir / "agent_actions.jsonl"
        self._buffer: list[dict[str, Any]] = []

    def record(
        self,
        *,
        agent_id: str,
        action: str,
        how: str,
        result: str,
        status: str = "ok",
        efficiency: dict[str, Any] | None = None,
        safety: dict[str, Any] | None = None,
        artifacts: list[str] | None = None,
        mutates: bool = False,
        applied: bool = False,
    ) -> dict[str, Any]:
        entry = {
            "id": str(uuid.uuid4()),
            "at": datetime.now(timezone.utc).isoformat(),
            "agent_id": agent_id,
            "action": action,
            "how": how,
            "result": result,
            "status": status,
            "efficiency": efficiency or {},
            "safety": safety or {},
            "artifacts": artifacts or [],
            "mutates": mutates,
            "applied": applied,
        }
        self._buffer.append(entry)
        with self.jsonl.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        return entry

    def flush_summary(self) -> dict[str, Any]:
        by_agent: dict[str, list[dict[str, Any]]] = {}
        for e in self._buffer:
            by_agent.setdefault(e["agent_id"], []).append(e)
        summary = {
            "day": self.day_dir.name,
            "total_actions": len(self._buffer),
            "agents_touched": len(by_agent),
            "mutate_actions": sum(1 for e in self._buffer if e.get("mutates")),
            "applied_actions": sum(1 for e in self._buffer if e.get("applied")),
            "by_agent": {
                aid: {
                    "count": len(items),
                    "last": items[-1],
                    "actions": [
                        {
                            "action": i["action"],
                            "how": i["how"],
                            "result": i["result"],
                            "status": i["status"],
                            "applied": i["applied"],
                        }
                        for i in items
                    ],
                }
                for aid, items in by_agent.items()
            },
            "recent": list(reversed(self._buffer[-50:])),
        }
        self.store.write_json(self.day_dir, "agent_actions.json", summary)
        return summary

    @staticmethod
    def load_latest(store: ReportStore | None = None) -> dict[str, Any] | None:
        store = store or ReportStore()
        root = store.root
        days = sorted(
            [p for p in root.iterdir() if p.is_dir() and p.name[:4].isdigit()],
            key=lambda p: p.name,
            reverse=True,
        )
        for day in days[:5]:
            path = day / "agent_actions.json"
            if path.is_file():
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
        return None
