"""Persistent freeze for a recovery experiment; expiry never enables live mode."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_STATE = Path(__file__).resolve().parents[3] / "var" / "recovery_stability.json"


def load_recovery_state(path: Path | None = None) -> dict[str, Any] | None:
    path = path or DEFAULT_STATE
    if not path.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        start = datetime.fromisoformat(state["started_at"])
        end = datetime.fromisoformat(state["freeze_until"])
        if not state["customer_id"] or not state["episode_id"]:
            raise ValueError("missing account/episode")
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError("invalid window")
        return state
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise PermissionError("Recovery state unreadable; writes blocked until repaired.") from exc


def assert_recovery_stable(*, path: Path | None = None, now: datetime | None = None) -> None:
    state = load_recovery_state(path)
    if state is None:
        return
    cid = os.environ.get("GOOGLE_ADS_CUSTOMER_ID", "").replace("-", "").strip()
    if cid and cid != str(state["customer_id"]):
        raise PermissionError("Recovery account does not match configured Google Ads customer.")
    if (now or datetime.now(timezone.utc)) < datetime.fromisoformat(state["freeze_until"]):
        raise PermissionError(
            f"Recovery stability window: writes blocked until {state['freeze_until']}. "
            "Keep the existing bidding, goals, targeting and budgets stable."
        )
