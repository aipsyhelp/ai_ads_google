from __future__ import annotations

from pathlib import Path
from typing import Any

from ads_ai_agents.intelligence.report_store import ReportStore
from ads_ai_agents.platform.agents.experiment import plan_weekly_experiments
from ads_ai_agents.platform.ceo import run_ceo_brief
from ads_ai_agents.platform.competitor_store import build_daily_competitor_pack
from ads_ai_agents.platform.funnel_ingest import aggregate_funnel, load_funnel_csv
from ads_ai_agents.platform.measurement_bridge import build_measurement_plan
from ads_ai_agents.platform.north_star import FunnelSnapshot


def run_weekly_strategy_pack(
    *,
    funnel_csv: Path | None = None,
    store: ReportStore | None = None,
) -> dict[str, Any]:
    """Persist weekly executive pack: CEO + competitors + experiments + measurement."""
    store = store or ReportStore()
    day_dir = store.day_dir(kind="audit")

    funnel: FunnelSnapshot | None = None
    funnel_ingest = None
    if funnel_csv and funnel_csv.is_file():
        rows = load_funnel_csv(funnel_csv)
        funnel = aggregate_funnel(rows)
        funnel_ingest = {
            "path": str(funnel_csv),
            "rows": len(rows),
            "funnel": funnel.to_dict(),
            "rates": funnel.rates(),
        }

    brief = run_ceo_brief(funnel=funnel, store=store, include_ads_live=True)
    competitor = build_daily_competitor_pack(store=store)
    experiments = plan_weekly_experiments()
    measurement = build_measurement_plan(
        paying_users_known=bool((funnel.paying_users if funnel else 0) > 0)
    )

    pack = {
        "type": "weekly_strategy_pack",
        "ceo_headline": brief["executive"]["headline"],
        "priorities": brief["executive"]["top_priorities"],
        "funnel_ingest": funnel_ingest,
        "competitor_evidence_root": competitor.get("evidence_root"),
        "experiments": experiments,
        "measurement": measurement,
        "ads_executor_mode": brief["executive"]["ads_automation_mode"],
        "artifacts": str(day_dir),
    }
    store.write_json(day_dir, "weekly_strategy_pack.json", pack)
    store.write_markdown(day_dir, "weekly_strategy_pack.md", _md(pack))
    return pack


def _md(pack: dict[str, Any]) -> str:
    lines = [
        "# Weekly Strategy Pack",
        "",
        pack.get("ceo_headline") or "",
        "",
        "## Priorities",
    ]
    for p in pack.get("priorities") or []:
        lines.append(f"- **{p.get('theme')}**: {p.get('do')}")
    lines.extend(["", "## Experiments (max 5)", ""])
    for exp in (pack.get("experiments") or {}).get("selected") or []:
        lines.append(
            f"- `{exp.get('id')}` {exp.get('name')} — metric `{exp.get('primary_metric')}` "
            f"(approval={exp.get('approval_required')})"
        )
    lines.extend(
        [
            "",
            "## Measurement",
            f"- {((pack.get('measurement') or {}).get('priority'))}",
            "",
            f"Competitor evidence: `{pack.get('competitor_evidence_root')}`",
            "",
        ]
    )
    return "\n".join(lines)
