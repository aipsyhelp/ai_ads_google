from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ads_ai_agents.intelligence.report_store import ReportStore
from ads_ai_agents.platform.agents.competitor_intel import DEFAULT_WATCHLIST, build_competitor_brief


def evidence_root(store: ReportStore | None = None) -> Path:
    store = store or ReportStore()
    path = store.root / "competitor_evidence"
    path.mkdir(parents=True, exist_ok=True)
    return path


def record_competitor_snapshot(
    *,
    brand: str,
    changes: list[dict[str, Any]],
    captured_at: str | None = None,
    store: ReportStore | None = None,
) -> Path:
    """Persist a daily public observation pack for one brand (manual or connector-fed)."""
    root = evidence_root(store)
    day = date.today().isoformat()
    brand_dir = root / brand.replace(" ", "_")
    brand_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "brand": brand,
        "captured_at": captured_at or datetime.now(timezone.utc).isoformat(),
        "day": day,
        "changes": changes,
    }
    path = brand_dir / f"{day}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def diff_last_two(brand: str, store: ReportStore | None = None) -> dict[str, Any]:
    brand_dir = evidence_root(store) / brand.replace(" ", "_")
    if not brand_dir.is_dir():
        return {"brand": brand, "diff": [], "note": "no_snapshots"}
    files = sorted(brand_dir.glob("*.json"))
    if len(files) < 2:
        return {"brand": brand, "diff": [], "note": "need_two_days", "files": [f.name for f in files]}
    prev = json.loads(files[-2].read_text(encoding="utf-8"))
    curr = json.loads(files[-1].read_text(encoding="utf-8"))
    prev_sum = {str(c.get("type")) + "|" + str(c.get("summary")) for c in prev.get("changes") or []}
    new_items = [
        c
        for c in (curr.get("changes") or [])
        if (str(c.get("type")) + "|" + str(c.get("summary"))) not in prev_sum
    ]
    return {
        "brand": brand,
        "from": files[-2].name,
        "to": files[-1].name,
        "new_changes": new_items,
        "new_count": len(new_items),
    }


def build_daily_competitor_pack(
    *,
    known_changes: list[dict[str, Any]] | None = None,
    watchlist: tuple[str, ...] = DEFAULT_WATCHLIST,
    store: ReportStore | None = None,
) -> dict[str, Any]:
    from ads_ai_agents.platform.competitor_intel_pack import PUBLIC_COMPETITOR_INTEL, intel_to_changes

    store = store or ReportStore()
    # Always refresh today's evidence with structured public intel (not empty shells).
    seeded_changes: list[dict[str, Any]] = list(known_changes or [])
    for brand in watchlist:
        intel = PUBLIC_COMPETITOR_INTEL.get(brand)
        if not intel:
            continue
        changes = intel_to_changes(brand, intel)
        record_competitor_snapshot(brand=brand, changes=changes, store=store)
        for ch in changes:
            seeded_changes.append({"brand": brand, **ch})

    brief = build_competitor_brief(watchlist=watchlist, known_changes=seeded_changes)
    diffs = [diff_last_two(b, store=store) for b in watchlist[:10]]
    day_dir = store.day_dir(kind="audit")
    pack = {
        "brief": brief,
        "diffs": diffs,
        "evidence_root": str(evidence_root(store)),
        "brands_filled": len([b for b in watchlist if b in PUBLIC_COMPETITOR_INTEL]),
        "instruction": (
            "Daily packs are auto-filled from public_category_intel_v1. "
            "Operators may append Ads Transparency / SERP screenshots later."
        ),
    }
    store.write_json(day_dir, "competitor_daily_pack.json", pack)
    return pack
