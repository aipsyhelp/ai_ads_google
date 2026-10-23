#!/usr/bin/env python3
"""Add cheap, low-competition, NON-MEDICAL everyday-language long-tail keywords
(synonyms) to the two Ukraine-wide campaigns, mapped to each ad group's theme.

Why: historical winning CPC ~$7 means the clinical head terms clear far above the
Grant $2 cap, so Max Clicks wins nothing. Everyday self-help phrasings ("як
заспокоїтися", "як зняти стрес") are far less competitive -> lower first-page bid
-> a real chance to win under $2 and start traffic. All keywords are multi-word
(Grant-compliant), PHRASE match, and avoid medical terms (діагноз/симптоми/
лікування/розлад/депресія) to stay policy-safe.

Dry-run by default. --apply to add. Duplicates are skipped via partial_failure.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

# ad_group_resource_name -> list of safe everyday PHRASE keywords (themed)
MAPPING = {
    # === Home Page / UA / National ===
    "customers/YOUR_CUSTOMER_ID/adGroups/8000000001": [  # тривожність
        "як заспокоїтися", "як заспокоїти нерви", "як перестати нервувати",
        "як зняти тривогу", "вправи для заспокоєння", "як позбутися тривоги",
    ],
    "customers/YOUR_CUSTOMER_ID/adGroups/8000000002": [  # вигорання
        "як відновити сили", "немає сил що робити", "як відпочити від роботи",
        "постійна втома що робити", "як подолати втому",
    ],
    "customers/YOUR_CUSTOMER_ID/adGroups/8000000003": [  # професійне вигорання
        "втома від роботи", "як не вигоріти на роботі",
        "стрес на роботі що робити", "як зменшити стрес на роботі",
    ],
    "customers/YOUR_CUSTOMER_ID/adGroups/8000000004": [  # (mood — everyday, non-clinical)
        "як покращити настрій", "як підняти настрій", "постійно поганий настрій",
        "як повернути радість життя", "як знайти мотивацію",
    ],
    # === Articles / UA / National ===
    "customers/YOUR_CUSTOMER_ID/adGroups/8000000005": [  # тривожність
        "як перестати хвилюватися", "як заспокоїти нерви",
        "як зняти напругу", "вправи на розслаблення",
    ],
    "customers/YOUR_CUSTOMER_ID/adGroups/8000000006": [  # професійне вигорання
        "як відновити сили після роботи", "як впоратися зі стресом на роботі",
        "як не вигоріти",
    ],
    "customers/YOUR_CUSTOMER_ID/adGroups/8000000007": [  # паніка (everyday)
        "як швидко заспокоїтися", "дихальні вправи для заспокоєння",
        "як заспокоїтися під час паніки",
    ],
    "customers/YOUR_CUSTOMER_ID/adGroups/8000000008": [  # страхи (everyday)
        "як подолати страх", "як побороти невпевненість", "як подолати хвилювання",
    ],
    "customers/YOUR_CUSTOMER_ID/adGroups/8000000009": [  # (mood/meaning — everyday)
        "як покращити настрій", "як підняти собі настрій", "як знайти сенс життя",
    ],
}

# guard: reject anything single-word or containing medical terms
BANNED = {"депресія", "розлад", "симптоми", "діагноз", "лікування", "терапія",
          "птср", "клінічний", "хвороба", "препарати", "таблетки"}


def _load_env() -> None:
    for raw in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _validate_terms() -> list[str]:
    problems = []
    for ag, kws in MAPPING.items():
        for k in kws:
            if len(k.split()) < 2:
                problems.append(f"single-word: {k}")
            if any(b in k.lower() for b in BANNED):
                problems.append(f"medical term: {k}")
    return problems


def main() -> int:
    apply = "--apply" in sys.argv
    problems = _validate_terms()
    if problems:
        print("REFUSING — unsafe terms found:")
        for p in problems:
            print("  -", p)
        return 2

    _load_env()
    from ads_ai_agents.adapters.settings import GoogleAdsSettings
    from ads_ai_agents.adapters.write_guard import assert_write_service
    from google.ads.googleads.client import GoogleAdsClient
    if apply:
        from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
        assert_mutations_allowed()
    assert_write_service("AdGroupCriterionService", scope="grant_semantic_expand")

    settings = GoogleAdsSettings.from_env()
    client = GoogleAdsClient.load_from_dict(settings.client_config())
    cid = settings.customer_id
    svc = client.get_service("AdGroupCriterionService")
    PHRASE = client.enums.KeywordMatchTypeEnum.PHRASE

    total = sum(len(v) for v in MAPPING.values())
    result = {
        "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "customer_id": cid,
        "mode": "APPLY" if apply else "DRY_RUN",
        "total_keywords": total,
        "per_ad_group": {ag: kws for ag, kws in MAPPING.items()},
    }

    per_group_status = {}
    for ag_rn, kws in MAPPING.items():
        ops = []
        for text in kws:
            op = client.get_type("AdGroupCriterionOperation")
            op.create.ad_group = ag_rn
            op.create.status = client.enums.AdGroupCriterionStatusEnum.ENABLED
            op.create.keyword.text = text
            op.create.keyword.match_type = PHRASE
            ops.append(op)
        vreq = client.get_type("MutateAdGroupCriteriaRequest")
        vreq.customer_id = cid
        vreq.operations.extend(ops)
        vreq.validate_only = True
        vreq.partial_failure = True
        vresp = svc.mutate_ad_group_criteria(request=vreq)
        status = vresp.partial_failure_error.message[:200] if vresp.partial_failure_error.code else "PASSED"
        per_group_status[ag_rn] = status
        if apply:
            areq = client.get_type("MutateAdGroupCriteriaRequest")
            areq.customer_id = cid
            areq.operations.extend(ops)
            areq.partial_failure = True
            aresp = svc.mutate_ad_group_criteria(request=areq)
            per_group_status[ag_rn] = f"applied {sum(1 for r in aresp.results if r.resource_name)}/{len(ops)}"

    result["status_per_group"] = per_group_status
    out_dir = ROOT / "reports" / datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "add_safe_longtail_keywords.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"Wrote: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
