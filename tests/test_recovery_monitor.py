from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import importlib.util
import json
import subprocess
from ads_ai_agents.intelligence.recovery_monitor import evaluate, _metrics

NOW=datetime(2026,10,7,14,tzinfo=timezone.utc)
STATE={'customer_id':'YOUR_CUSTOMER_ID','episode_id':'new',
       'started_at':'2026-10-07T13:05:00+00:00','freeze_until':'2026-10-10T13:05:00+00:00',
       'campaigns':{'1':{'status':'ENABLED','bidding':'MAXIMIZE_CONVERSIONS'}},
       'portfolios':{'2':{'tcpa_micros':2000000000}},'unique_daily_budget':329,
       'primary_actions':['chat_message_sent'], 'negative_geo':[], 'metrics_baseline':{'impressions':0}}
SNAP={'campaigns':[{'id':'1','status':'ENABLED','bidding':'MAXIMIZE_CONVERSIONS','primary_status':'ELIGIBLE'}],
      'portfolios':[{'id':'2','tcpa_micros':2000000000}], 'approved_ads':{'1':2},
      'account':{'status':'ENABLED','auto_tagging':True}, 'unique_daily_budget':329,
      'negative_geo':[], 'primary_actions':['chat_message_sent'],'automation_mode':'READ_ONLY',
      'since_recovery':{'impressions':0,'clicks':0,'conversions':0}}


def codes(result):
    return {a['code'] for a in result['new_alerts']}


def test_old_episode_does_not_suppress_first_impressions():
    s=deepcopy(SNAP); s['since_recovery']['impressions']=1
    r=evaluate(s,STATE,{'episode_id':'old','milestones':{'impressions':True},'first_impressions_alerted':True},now=NOW)
    assert 'FIRST_IMPRESSIONS_ALERT' in codes(r)
    assert 'FIRST_IMPRESSIONS_ALERT' not in codes(evaluate(s,STATE,r,now=NOW))


def test_prior_same_day_impressions_do_not_count_as_recovery():
    s=deepcopy(SNAP); s['since_recovery']['impressions']=10
    state=deepcopy(STATE); state['metrics_baseline']['impressions']=10
    assert 'FIRST_IMPRESSIONS_ALERT' not in codes(evaluate(s,state,now=NOW))


def test_deadline_and_drift_are_deduplicated():
    s=deepcopy(SNAP); s['portfolios'][0]['tcpa_micros']=500000000
    now=datetime(2026,10,11,tzinfo=timezone.utc)
    r=evaluate(s,STATE,now=now)
    assert codes(r)=={'BIDDING_DRIFT','DEADLINE_REVIEW_REQUIRED'}
    assert not codes(evaluate(s,STATE,r,now=now))
    assert r['snapshot']['automation_mode']=='READ_ONLY'


def test_zero_ads_conversions_do_not_report_broken_site_tracking():
    assert not codes(evaluate(SNAP,STATE,now=NOW))


def test_budget_drift_and_lost_approved_ads_are_detected():
    s=deepcopy(SNAP); s['unique_daily_budget']=449; s['approved_ads']={}
    assert codes(evaluate(s,STATE,now=NOW))=={'BUDGET_DRIFT','NO_APPROVED_ADS'}


def test_rank_over_90_and_missing_zero_rank_are_labeled():
    from types import SimpleNamespace
    m=SimpleNamespace(search_rank_lost_impression_share=.9001,impressions=0,clicks=0,cost_micros=0,
                      conversions=0,all_conversions=0,search_budget_lost_impression_share=0)
    assert _metrics(SimpleNamespace(metrics=m))['rank_lost_label']=='>90%'
    m.search_rank_lost_impression_share=0
    assert _metrics(SimpleNamespace(metrics=m))['rank_lost'] is None


def test_installer_wrapper_handles_alert_exit_status(tmp_path):
    # Exercise the generated shell wrapper with a command that emits an alert then exits 1.
    installer=Path(__file__).parents[1]/'scripts/install_delivery_watch_hourly.sh'
    text=installer.read_text(); wrapper=text.split('<<WRAP\n')[1].split('\nWRAP')[0]
    wrapper=wrapper.replace('\\$','$').replace('${PROJECT_DIR}',str(tmp_path)).replace('${LOG_DIR}',str(tmp_path))
    fake=tmp_path/'fake.sh'; fake.write_text('#!/bin/bash\necho FIRST_IMPRESSIONS_ALERT\nexit 1\n'); fake.chmod(0o755)
    wrapper=wrapper.replace('${PYTHON}',str(fake)).replace('${SCRIPT_DIR}',str(tmp_path))
    wrapper=wrapper.replace('/usr/bin/osascript', '/usr/bin/true')
    r=subprocess.run(['/bin/bash','-c',wrapper],capture_output=True,text=True)
    assert r.returncode==0
    assert 'FIRST_IMPRESSIONS_ALERT' in (tmp_path/'delivery-watch-hourly-ALERTS.log').read_text()


def test_failure_persists_unknown_without_erasing_success_history(tmp_path,monkeypatch):
    from ads_ai_agents.intelligence import recovery_monitor
    script=Path(__file__).parents[1]/'scripts/delivery_watch_hourly.py'
    spec=importlib.util.spec_from_file_location('watch_test',script)
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    history=tmp_path/'history'; history.mkdir()
    old={'episode_id':'new','milestones':{'impressions':True}}
    (history/'state.json').write_text(json.dumps(old))
    baseline=tmp_path/'baseline.json'; baseline.write_text(json.dumps(STATE))
    monkeypatch.setattr('sys.argv',['watch','--env-file',str(tmp_path/'none'),
                                   '--recovery-state',str(baseline),'--history-dir',str(history)])
    monkeypatch.setattr('ads_ai_agents.adapters.google_ads_readonly.GoogleAdsReadOnlyAdapter',lambda:object())
    def fail(*args):
        raise RuntimeError('simulated API failure')
    monkeypatch.setattr(recovery_monitor,'collect_snapshot',fail)
    assert mod.main()==3
    assert json.loads((history/'latest.json').read_text())['status']=='UNKNOWN'
    assert json.loads((history/'state.json').read_text())==old


def test_goal_config_drift_is_detected():
    s=deepcopy(SNAP); s['goal_configs']=[{'campaign':'1','custom_goal':'changed'}]
    state=deepcopy(STATE); state['goal_configs']=[{'campaign':'1','custom_goal':'original'}]
    assert codes(evaluate(s,state,now=NOW))=={'GOAL_SELECTION_DRIFT'}


def test_legacy_clear_target_entrypoints_are_disabled():
    root=Path(__file__).parents[1]
    for name in ('clear_tcpa_pause_pmax.py','enforce_maximize_conversions.py'):
        spec=importlib.util.spec_from_file_location('legacy_'+name,root/'examples'/'ops'/name)
        mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        assert mod.main()==2


def test_custom_goal_plan_excludes_micro_events_even_if_secondary():
    from ads_ai_agents.intelligence.grant_delivery_recovery import MEANINGFUL_PRIMARY_ACTIONS
    from ads_ai_agents.intelligence.recovery_monitor import plan_goal_cleanup
    actions=[{'name':name,'resource_name':f'a/{i}','primary':True} for i,name in enumerate(MEANINGFUL_PRIMARY_ACTIONS)]
    actions.append({'name':'page_view','resource_name':'a/micro','primary':False})
    s={'conversion_actions':actions,'custom_goals':[{'resource_name':'g/1','actions':['a/0','a/micro']}]}
    plan=plan_goal_cleanup(s)
    assert plan['ready'] and plan['mode']=='PLAN_ONLY'
    assert 'a/micro' not in plan['goals'][0]['after']
    assert plan['goals'][0]['rollback']==['a/0','a/micro']
    assert plan['goals'][0]['removed_names']==['page_view']


def test_custom_goal_plan_is_not_ready_without_required_events():
    from ads_ai_agents.intelligence.recovery_monitor import plan_goal_cleanup
    assert not plan_goal_cleanup({'conversion_actions':[],'custom_goals':[]})['ready']


def test_goal_comparison_ignores_record_and_dictionary_field_order():
    s=deepcopy(SNAP); state=deepcopy(STATE)
    state['conversion_actions']=[{'name':'A','resource_name':'a/2'},{'name':'B','resource_name':'a/1'}]
    s['conversion_actions']=[{'resource_name':'a/1','name':'B'},{'resource_name':'a/2','name':'A'}]
    assert not codes(evaluate(s,state,now=NOW))


def test_resolved_drift_is_reported_when_other_alerts_remain():
    s=deepcopy(SNAP); s['portfolios'][0]['tcpa_micros']=500000000
    now=datetime(2026,10,11,tzinfo=timezone.utc)
    old=evaluate(s,STATE,now=now)
    new=evaluate(SNAP,STATE,old,now=now)
    assert codes(new)=={'ALERT_RESOLVED'}
    assert {a['code'] for a in new['active_alerts']}=={'DEADLINE_REVIEW_REQUIRED'}


def test_one_campaign_delivery_does_not_hide_another_stalled_campaign():
    state=deepcopy(STATE); state['campaigns']['3']=state['campaigns']['1'].copy()
    s=deepcopy(SNAP); s['campaigns'].append({**s['campaigns'][0],'id':'3'}); s['approved_ads']['3']=2
    s['since_recovery']['impressions']=4
    s['campaign_delivery']=[{'id':'1','impressions':4}]
    now=datetime(2026,10,11,tzinfo=timezone.utc)
    r=evaluate(s,state,now=now)
    assert r['campaign_milestones']=={'1':True}
    assert [(a['code'],a['detail'].split(':')[0]) for a in r['active_alerts'] if a['code']=='CAMPAIGN_ZERO_DELIVERY']==[('CAMPAIGN_ZERO_DELIVERY','3')]
    assert 'ACCOUNT_WIDE_DELIVERY_STALL' not in codes(r)
    assert 'CAMPAIGN_FIRST_IMPRESSIONS' not in codes(evaluate(s,state,r,now=now))


def test_two_months_below_grant_ctr_raise_deactivation_risk():
    from ads_ai_agents.intelligence.recovery_monitor import grant_ctr_alerts
    monthly=[{'month':'2026-07-01','impressions':31850,'clicks':1373},
             {'month':'2026-08-01','impressions':2124,'clicks':61},
             {'month':'2026-09-01','impressions':6,'clicks':0}]
    alerts=grant_ctr_alerts(monthly,'2026-10-01')
    assert [a['code'] for a in alerts]==['GRANT_CTR_DEACTIVATION_RISK']
    assert '2026-08' in alerts[0]['detail'] and '2026-09' in alerts[0]['detail']


def test_one_low_month_or_small_current_sample_does_not_alert():
    from ads_ai_agents.intelligence.recovery_monitor import grant_ctr_alerts
    monthly=[{'month':'2026-08-01','impressions':1000,'clicks':80},
             {'month':'2026-09-01','impressions':1000,'clicks':30},
             {'month':'2026-10-01','impressions':50,'clicks':1}]
    assert grant_ctr_alerts(monthly,'2026-10-01')==[]


def test_current_month_below_safety_margin_warns():
    from ads_ai_agents.intelligence.recovery_monitor import grant_ctr_alerts
    monthly=[{'month':'2026-10-01','impressions':500,'clicks':27}]
    assert [a['code'] for a in grant_ctr_alerts(monthly,'2026-10-01')]==['GRANT_CTR_WARNING']


def test_account_stall_requires_grants_review_without_automatic_bid_switch():
    s=deepcopy(SNAP); s['campaign_delivery']=[]
    r=evaluate(s,STATE,now=datetime(2026,10,11,tzinfo=timezone.utc))
    assert 'ACCOUNT_WIDE_DELIVERY_STALL' in codes(r)
    assert r['snapshot']['automation_mode']=='READ_ONLY'
