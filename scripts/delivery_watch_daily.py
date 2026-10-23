"""Daily read-only delivery watch with before/after comparison.

Runs the delivery starvation audit, stores a snapshot, and diffs it against the
previous run. Designed for unattended execution: it never mutates the account
and communicates through exit codes plus an append-only history directory.

Exit codes:
    0  ran fine, nothing critical
    2  at least one critical alert (use --fail-on-alert to enable)
    3  the audit itself failed (credentials, network, API)

Usage:
    python scripts/delivery_watch_daily.py
    python scripts/delivery_watch_daily.py --json --fail-on-alert
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

DEFAULT_HISTORY_DIR = ROOT / "var" / "delivery_watch"


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _latest_snapshot(history_dir: Path) -> dict[str, Any] | None:
    if not history_dir.exists():
        return None
    candidates = sorted(history_dir.glob("delivery-*.json"))
    for path in reversed(candidates):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    parser.add_argument(
        "--history-dir",
        type=Path,
        default=DEFAULT_HISTORY_DIR,
        help="where daily snapshots are stored",
    )
    parser.add_argument(
        "--fail-on-alert",
        action="store_true",
        help="exit 2 when a critical alert is raised",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="compare without persisting a new snapshot",
    )
    args = parser.parse_args()

    _load_env(ROOT / ".env")

    from ads_ai_agents.intelligence.delivery_watch import (
        DeliverySnapshot,
        build_snapshot,
        compare,
        render_text,
        snapshot_filename,
    )

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from delivery_starvation_audit import audit

    # The audit deliberately probes API fields that only exist in some versions.
    # The client logs each probe as a fault, which would make a healthy
    # unattended run look broken in the launchd error log.
    logging.getLogger("google.ads.googleads.client").setLevel(logging.CRITICAL)

    try:
        report = audit()
    except Exception:  # noqa: BLE001
        detail = traceback.format_exc(limit=3)
        if args.json:
            print(json.dumps({"error": "audit_failed", "detail": detail}, ensure_ascii=False))
        else:
            print("== Grant delivery watch ==")
            print("audit failed — account state unknown")
            print(detail)
        return 3

    taken_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    current = build_snapshot(report, taken_at=taken_at)

    raw_previous = _latest_snapshot(args.history_dir)
    previous = DeliverySnapshot.from_dict(raw_previous) if raw_previous else None

    comparison = compare(current, previous)

    if not args.no_write:
        args.history_dir.mkdir(parents=True, exist_ok=True)
        target = args.history_dir / snapshot_filename(taken_at)
        target.write_text(
            json.dumps(current.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (args.history_dir / "latest.json").write_text(
            json.dumps(comparison.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if args.json:
        print(json.dumps(comparison.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(render_text(comparison))
        if comparison.reactivated:
            print()
            print("REACTIVATION SIGNAL: spend is flowing again — re-enable the growth plan.")

    if args.fail_on_alert and comparison.has_blocking_alert:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
