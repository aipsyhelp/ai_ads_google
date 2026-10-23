#!/usr/bin/env python3
"""Historical example: explicit operator amendment of a delivery recovery.

Validates by default and is never run by daily agents. Live apply requires the
digest of a saved, validated plan and an explicit amendment of the active
freeze. Writes are one atomic batch with fresh preconditions. The background
READ_ONLY setting is never changed. Resource IDs come from
config/account_ids.json (see config/account_ids.example.json).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def make_operations(client, plan):
    from google.protobuf.field_mask_pb2 import FieldMask
    operations = []
    for change in plan['operations']:
        op = client.get_type('MutateOperation')
        if change['kind'] == 'custom_goal':
            target = op.custom_conversion_goal_operation
            target.update.resource_name = change['resource']
            target.update.conversion_actions.extend(change['after'])
            target.update_mask.CopyFrom(FieldMask(paths=['conversion_actions']))
        elif change['kind'] == 'campaign':
            target = op.campaign_operation
            target.update.resource_name = change['resource']
            target.update.bidding_strategy = f"customers/{plan['customer_id']}/biddingStrategies/{change['after']['portfolio_id']}"
            target.update.campaign_budget = f"customers/{plan['customer_id']}/campaignBudgets/{change['after']['budget_id']}"
            target.update_mask.CopyFrom(FieldMask(paths=['bidding_strategy', 'campaign_budget']))
        elif change['kind'] == 'goal_config':
            target = op.conversion_goal_campaign_config_operation
            target.update.resource_name = change['resource']
            target.update.goal_config_level = client.enums.GoalConfigLevelEnum.CAMPAIGN
            target.update.custom_conversion_goal = change['after']['custom_goal']
            target.update_mask.CopyFrom(FieldMask(paths=['goal_config_level', 'custom_conversion_goal']))
        elif change['kind'] == 'remove_keyword':
            op.ad_group_criterion_operation.remove = change['resource']
        elif change['kind'] == 'campaign_goal':
            target = op.campaign_conversion_goal_operation
            target.update.resource_name = change['resource']
            target.update.biddable = False
            target.update_mask.CopyFrom(FieldMask(paths=['biddable']))
        else:
            raise ValueError('Unapproved operation kind')
        operations.append(op)
    return operations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--recovery-state', type=Path, default=ROOT / 'var/recovery_stability.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'var/recovery_amendment_plan.json')
    parser.add_argument('--apply-sha256')
    parser.add_argument('--amend-active-freeze', action='store_true')
    args = parser.parse_args()
    from ads_ai_agents.adapters.settings import load_dotenv
    from ads_ai_agents.adapters.automation_mode import resolve_automation_mode
    from ads_ai_agents.adapters.recovery_stability import load_recovery_state
    from ads_ai_agents.adapters.google_ads_readonly import GoogleAdsReadOnlyAdapter
    from ads_ai_agents.intelligence.recovery_monitor import collect_snapshot
    from ads_ai_agents.intelligence.recovery_amendment import (
        EXPECTED_UNIQUE_DAILY_BUDGET, build_amendment, digest,
    )
    load_dotenv(args.env_file)
    logging.getLogger('google.ads.googleads.client').setLevel(logging.CRITICAL)
    recovery = load_recovery_state(args.recovery_state)
    if recovery is None or resolve_automation_mode().value != 'READ_ONLY':
        raise RuntimeError('Recovery baseline and READ_ONLY background automation required')
    adapter = GoogleAdsReadOnlyAdapter()
    snapshot = collect_snapshot(adapter, recovery)
    plan = build_amendment(snapshot, recovery)
    if args.apply_sha256:
        saved = json.loads(args.output.read_text())
        payload = {k: v for k, v in saved['plan'].items() if k != 'sha256'}
        if saved.get('validation') != 'PASSED' or digest(payload) != args.apply_sha256:
            raise RuntimeError('A validated, intact plan with matching digest is required')
        if plan['sha256'] != args.apply_sha256 or not args.amend_active_freeze:
            raise RuntimeError('Live preconditions changed or explicit freeze amendment missing')
    operations = make_operations(adapter.client, plan)
    request = adapter.client.get_type('MutateGoogleAdsRequest')
    request.customer_id = adapter.settings.customer_id
    request.mutate_operations.extend(operations)
    request.partial_failure = False
    request.validate_only = True
    service = adapter.client.get_service('GoogleAdsService')
    if operations:
        service.mutate(request=request)
    report = {'plan': plan, 'validation': 'PASSED', 'before': snapshot,
              'previous_recovery': recovery, 'mode': 'VALIDATED_ONLY'}
    if not args.apply_sha256:
        save(args.output, report)
        print(json.dumps({'mode': report['mode'], 'sha256': plan['sha256'],
                          'operations': len(operations)}, ensure_ascii=False))
        return 0
    if not operations:
        raise RuntimeError('Nothing to amend')
    # Durable journal precedes the remote commit. On a transport error a human
    # must read current state; there is no automatic retry or rollback.
    report['mode'] = 'APPLYING'
    save(args.output, report)
    request.validate_only = False
    service.mutate(request=request)
    now = datetime.now(timezone.utc)
    recovery.update({'episode_id': 'recovery-amendment-' + now.strftime('%Y%m%dT%H%M%SZ'),
        'started_at': now.isoformat(), 'freeze_until': (now + timedelta(hours=72)).isoformat(),
        'amendment_plan_sha256': plan['sha256']})
    save(args.recovery_state, recovery)  # re-arm the freeze immediately
    after = collect_snapshot(adapter, recovery)
    if build_amendment(after, recovery)['operations']:
        raise RuntimeError('Amendment verification failed; automation remains frozen')
    if after['unique_daily_budget'] != EXPECTED_UNIQUE_DAILY_BUDGET:
        raise RuntimeError('Budget verification failed; automation remains frozen')
    recovery['campaigns'] = {c['id']: {k: c[k] for k in ('status', 'bidding', 'portfolio_id', 'budget_id', 'budget')}
                              for c in after['campaigns'] if c['status'] == 'ENABLED'}
    recovery['unique_daily_budget'] = after['unique_daily_budget']
    for key in ('conversion_actions', 'goal_configs', 'campaign_goals', 'custom_goals', 'primary_actions', 'negative_geo'):
        recovery[key] = after[key]
    recovery['metrics_baseline'] = {k: after['since_recovery'][k] for k in ('impressions', 'clicks', 'conversions')}
    recovery['campaign_metrics_baseline'] = {c['id']: {k: c[k] for k in ('impressions', 'clicks', 'conversions')}
                                             for c in after['campaign_delivery']}
    save(args.recovery_state, recovery)
    report.update({'mode': 'APPLIED_VERIFIED', 'after': after, 'recovery': recovery})
    save(args.output, report)
    print(json.dumps({'mode': report['mode'], 'budget': after['unique_daily_budget'],
                      'campaigns': len(recovery['campaigns']), 'freeze_until': recovery['freeze_until']}))
    return 0


if __name__ == '__main__':
    from google.ads.googleads.errors import GoogleAdsException
    try:
        raise SystemExit(main())
    except GoogleAdsException as exc:
        print(json.dumps({'mode': 'API_REJECTED', 'request_id': exc.request_id,
                          'errors': [e.message for e in exc.failure.errors]}, ensure_ascii=False))
        raise SystemExit(2)
