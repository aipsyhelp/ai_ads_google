#!/usr/bin/env python3
"""Preview/validate custom-goal cleanup. Live apply is gated by mode + recovery freeze."""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from google.protobuf import field_mask_pb2
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))


def main() -> int:
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--env-file',type=Path,default=ROOT/'.env')
    parser.add_argument('--recovery-state',type=Path,default=ROOT/'var/recovery_stability.json')
    parser.add_argument('--output',type=Path,default=ROOT/'var/goal_cleanup_plan.json')
    args=parser.parse_args()
    from ads_ai_agents.adapters.settings import load_dotenv,GoogleAdsSettings
    from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
    from ads_ai_agents.adapters.recovery_stability import assert_recovery_stable,load_recovery_state
    from ads_ai_agents.adapters.google_ads_readonly import GoogleAdsReadOnlyAdapter
    from ads_ai_agents.adapters.write_guard import assert_write_service
    from ads_ai_agents.intelligence.recovery_monitor import collect_snapshot
    from google.ads.googleads.client import GoogleAdsClient
    load_dotenv(args.env_file)
    if args.apply:
        assert_mutations_allowed()
        assert_recovery_stable(path=args.recovery_state)
    recovery=load_recovery_state(args.recovery_state)
    if recovery is None:
        raise RuntimeError('Recovery baseline required')
    settings=GoogleAdsSettings.from_env()
    snapshot=collect_snapshot(GoogleAdsReadOnlyAdapter(settings),recovery)
    if args.apply:
        from ads_ai_agents.intelligence.recovery_monitor import evaluate
        snapshot['automation_mode']='READ_ONLY'
        critical=[a for a in evaluate(snapshot,recovery)['active_alerts'] if a['level']=='critical']
        if critical:
            raise RuntimeError('Recovery configuration drift detected; review before changing goals')
    plan=snapshot['goal_cleanup_plan']
    if not plan['ready']:
        raise RuntimeError('Required intent conversion actions unavailable; refusing partial cleanup')
    if plan.get('standard_goal_overrides'):
        raise RuntimeError('Additive standard goals still enabled; use a reviewed atomic amendment including campaign goals')
    assert_write_service('CustomConversionGoalService',scope='grant_delivery_recovery')
    client=GoogleAdsClient.load_from_dict(settings.client_config())
    operations=[]
    for goal in plan['goals']:
        operation=client.get_type('CustomConversionGoalOperation')
        operation.update.resource_name=goal['resource_name']
        operation.update.conversion_actions.extend(goal['after'])
        operation.update_mask.CopyFrom(field_mask_pb2.FieldMask(paths=['conversion_actions']))
        operations.append(operation)
    service=client.get_service('CustomConversionGoalService')
    if operations:
        request=client.get_type('MutateCustomConversionGoalsRequest')
        request.customer_id=settings.customer_id
        request.operations.extend(operations)
        request.validate_only=True
        service.mutate_custom_conversion_goals(request=request)
        plan['validation']='PASSED'
        if args.apply:
            request.validate_only=False
            result=service.mutate_custom_conversion_goals(request=request)
            plan['mode']='APPLIED'
            plan['applied']=[r.resource_name for r in result.results]
            start=datetime.now(timezone.utc)
            recovery.update({'episode_id':'goal-cleanup-'+start.strftime('%Y%m%dT%H%M%SZ'),
                'started_at':start.isoformat(),'freeze_until':(start+timedelta(hours=72)).isoformat()})
            def save_recovery():
                temp=args.recovery_state.with_suffix('.tmp')
                temp.write_text(json.dumps(recovery,ensure_ascii=False,indent=2))
                temp.replace(args.recovery_state)
            # Re-arm the write freeze immediately, including if verification later fails.
            save_recovery()
            after=collect_snapshot(GoogleAdsReadOnlyAdapter(settings),recovery)
            if after['goal_cleanup_plan']['goals']:
                raise RuntimeError('Goal cleanup verification failed; new freeze remains active')
            for key in ('conversion_actions','goal_configs','campaign_goals','custom_goals','primary_actions'):
                recovery[key]=after[key]
            recovery['metrics_baseline']={k:after['since_recovery'][k] for k in ('impressions','clicks','conversions')}
            save_recovery()
            plan['after']=after['custom_goals']
            plan['freeze_until']=recovery['freeze_until']
    else:
        plan['validation']='nothing_to_change'
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(plan,ensure_ascii=False,indent=2))
    print(json.dumps(plan,ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
