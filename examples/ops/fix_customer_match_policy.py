#!/usr/bin/env python3
"""Audit + remediate Customer Match policy risk for account YOUR_CUSTOMER_ID."""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from ads_ai_agents.intelligence.customer_match_remediation import CustomerMatchRemediation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Execute remediation (default: audit only)")
    args = parser.parse_args()

    rem = CustomerMatchRemediation()
    result = rem.apply(dry_run=not args.apply)
    report_path = rem.write_report(result)

    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    print(f"\nReport: {report_path}")
    print(f"Appeal draft: {report_path.parent / 'CUSTOMER_MATCH_APPEAL.md'}")
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
