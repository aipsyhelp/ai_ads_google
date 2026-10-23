from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from ads_ai_agents.platform.north_star import FunnelSnapshot


REQUIRED_COLUMNS = (
    "ad_clicks",
    "ad_spend_usd",
    "registrations",
    "first_chat",
    "paying_users",
)


def load_funnel_csv(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"Funnel CSV not found: {path}")
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = [dict(row) for row in reader if any((v or "").strip() for v in row.values())]
    if not rows:
        raise ValueError("Funnel CSV is empty")
    missing = [c for c in REQUIRED_COLUMNS if c not in (rows[0].keys())]
    if missing:
        raise ValueError(f"Funnel CSV missing columns: {missing}")
    return rows


def _f(row: dict[str, Any], key: str) -> float:
    raw = row.get(key)
    if raw is None or str(raw).strip() == "":
        return 0.0
    return float(str(raw).replace(",", "").strip())


def aggregate_funnel(
    rows: list[dict[str, Any]],
    *,
    source: str | None = None,
    locale: str | None = None,
    period_days: int | None = None,
) -> FunnelSnapshot:
    filtered = rows
    if source:
        filtered = [r for r in filtered if str(r.get("source") or "").lower() == source.lower()]
    if locale:
        filtered = [r for r in filtered if str(r.get("locale") or "").upper() == locale.upper()]

    dates = [str(r.get("date") or "") for r in filtered if r.get("date")]
    if period_days is None:
        period_days = max(len(set(dates)), 1)

    return FunnelSnapshot(
        period_days=period_days,
        ad_clicks=sum(_f(r, "ad_clicks") for r in filtered),
        ad_spend_usd=sum(_f(r, "ad_spend_usd") for r in filtered),
        landing_sessions=sum(_f(r, "landing_sessions") for r in filtered),
        registrations=sum(_f(r, "registrations") for r in filtered),
        first_chat=sum(_f(r, "first_chat") for r in filtered),
        returned_users=sum(_f(r, "returned_users") for r in filtered),
        subscriptions_started=sum(_f(r, "subscriptions_started") for r in filtered),
        paying_users=sum(_f(r, "paying_users") for r in filtered),
        revenue_usd=sum(_f(r, "revenue_usd") for r in filtered),
        source=f"csv:{source or 'all'}:{locale or 'all'}",
    )


def ingest_funnel_file(
    path: Path,
    *,
    source: str | None = None,
    locale: str | None = None,
) -> dict[str, Any]:
    rows = load_funnel_csv(path)
    funnel = aggregate_funnel(rows, source=source, locale=locale)
    by_locale: dict[str, dict[str, float]] = {}
    for row in rows:
        loc = str(row.get("locale") or "UNK").upper()
        bucket = by_locale.setdefault(
            loc,
            {
                "ad_spend_usd": 0.0,
                "ad_clicks": 0.0,
                "registrations": 0.0,
                "first_chat": 0.0,
                "paying_users": 0.0,
                "revenue_usd": 0.0,
            },
        )
        for key in bucket:
            bucket[key] += _f(row, key)

    return {
        "path": str(path),
        "rows": len(rows),
        "funnel": funnel.to_dict(),
        "by_locale": by_locale,
        "rates": funnel.rates(),
    }
