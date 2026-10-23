from copy import deepcopy
import importlib.util
from pathlib import Path
import pytest

from ads_ai_agents.intelligence.recovery_amendment import (
    build_amendment, CUSTOMER, CORE, CITIES, GOAL, WELLBEING, PRIMARY_CORE, CITY_WITH_ACCOUNT_GOAL,
    PRIMARY_PORTFOLIO, SECONDARY_PORTFOLIO, PRIMARY_BUDGET, SECONDARY_BUDGET,
    PRIMARY_BUDGET_AMOUNT, SECONDARY_BUDGET_AMOUNT, TARGET_CPA_MICROS,
)
from ads_ai_agents.intelligence.grant_delivery_recovery import MEANINGFUL_PRIMARY_ACTIONS


def fixture():
    campaigns=[]
    for cid in sorted(CORE | CITIES):
        ua=cid in PRIMARY_CORE
        campaigns.append({'id':cid,'status':'ENABLED','channel':'SEARCH','primary_status':'ELIGIBLE',
            'bidding':'MAXIMIZE_CONVERSIONS','portfolio_id':PRIMARY_PORTFOLIO if ua else SECONDARY_PORTFOLIO,
            'budget_id':PRIMARY_BUDGET if ua else SECONDARY_BUDGET,'budget':PRIMARY_BUDGET_AMOUNT if ua else SECONDARY_BUDGET_AMOUNT,
            'standalone_tcpa_micros':0})
        if cid in CITIES:campaigns[-1].update(portfolio_id='',budget_id='old'+cid,budget=15)
    actions=[{'name':name,'primary':True,'resource_name':f'customers/{CUSTOMER}/conversionActions/{i}'}
             for i,name in enumerate(MEANINGFUL_PRIMARY_ACTIONS)]
    snap={'customer_id':CUSTOMER,'campaigns':campaigns,'conversion_actions':actions,'unique_daily_budget':374,
          'negative_geo':[], 'campaign_goals':[{'resource_name':f'customers/{CUSTOMER}/campaignConversionGoals/{cid}~{category}~WEBSITE', 'campaign':f'customers/{CUSTOMER}/campaigns/{cid}',
              'category':category, 'origin':'WEBSITE','biddable':True} for cid in sorted(CORE|CITIES) for category in ('CONTACT','SIGNUP')],
          'portfolios':[{'id':pid,'type':'MAXIMIZE_CONVERSIONS','tcpa_micros':TARGET_CPA_MICROS} for pid in (PRIMARY_PORTFOLIO,SECONDARY_PORTFOLIO)],
          'goal_cleanup_plan':{'ready':True},'custom_goals':[{'resource_name':GOAL,'actions':['micro']}],
          'goal_configs':[{'campaign':f'customers/{CUSTOMER}/campaigns/{cid}','level':'CAMPAIGN','custom_goal':GOAL} for cid in sorted(CORE|CITIES)],
          'single_word_keywords':[{'resource_name':WELLBEING,'text':'wellbeing','campaign_id':sorted(CORE)[1],'ad_group_id':WELLBEING.rsplit('/',1)[-1].split('~')[0],'match_type':'PHRASE','status':'ENABLED'}]}
    for g in snap['goal_configs']:
        if g['campaign'].endswith('/'+CITY_WITH_ACCOUNT_GOAL):g.update(level='CUSTOMER',custom_goal='')
    return snap,{'customer_id':CUSTOMER,'episode_id':'test'}


def test_rejects_new_campaigns_and_unknown_single_word_keywords():
    s,r=fixture(); s['campaigns'].append({**s['campaigns'][0],'id':'unreviewed'})
    with pytest.raises(ValueError):build_amendment(s,r)
    s,r=fixture();s['single_word_keywords'].append({'resource_name':'new','text':'anxiety'})
    with pytest.raises(ValueError):build_amendment(s,r)


def test_digest_ignores_reporting_but_detects_unreviewed_control_changes():
    s,r=fixture(); p=build_amendment(s,r)
    t=deepcopy(s); t['campaigns'].reverse();t['goal_configs'].reverse();t['conversion_actions'].reverse()
    t['campaigns'][0]['primary_status']='LEARNING';t['today']={'impressions':5}
    assert build_amendment(t,r)['sha256']==p['sha256']
    t['negative_geo']=[{'campaign_id':sorted(PRIMARY_CORE)[0],'geo':'city'}]
    assert build_amendment(t,r)['sha256']!=p['sha256']
    t=deepcopy(s);t['campaigns'][0]['budget']=100
    with pytest.raises(ValueError):build_amendment(t,r)


def test_atomic_batch_never_contains_target_or_ad_or_budget_amount_changes():
    from google.auth.credentials import AnonymousCredentials
    from google.ads.googleads.client import GoogleAdsClient
    s,r=fixture();p=build_amendment(s,r)
    spec=importlib.util.spec_from_file_location('amend',Path(__file__).parents[1]/'examples/ops/amend_recovery.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    client=GoogleAdsClient(credentials=AnonymousCredentials(),developer_token='test',use_proto_plus=True)
    operations=m.make_operations(client,p)
    assert len(operations)==20
    for op in operations:
        kind=op._pb.WhichOneof('operation')
        assert kind in {'campaign_operation','custom_conversion_goal_operation','conversion_goal_campaign_config_operation','ad_group_criterion_operation','campaign_conversion_goal_operation'}
        if kind=='campaign_operation':
            assert set(op.campaign_operation.update_mask.paths)=={'bidding_strategy','campaign_budget'}
            assert op.campaign_operation.update.bidding_strategy.endswith('/'+PRIMARY_PORTFOLIO)


def test_amendment_is_idempotent_after_verified_commit():
    s,r=fixture()
    for c in s['campaigns']:
        if c['id'] in CITIES:c.update(portfolio_id=PRIMARY_PORTFOLIO,budget_id=PRIMARY_BUDGET,budget=PRIMARY_BUDGET_AMOUNT)
    for g in s['goal_configs']:g.update(level='CAMPAIGN',custom_goal=GOAL)
    s['custom_goals'][0]['actions']=[a['resource_name'] for a in s['conversion_actions']]
    s['single_word_keywords']=[];s['unique_daily_budget']=329
    for g in s['campaign_goals']:g['biddable']=False
    assert not build_amendment(s,r)['operations']


def test_custom_goal_membership_does_not_hide_extra_standard_goal_actions():
    s,r=fixture()
    s['custom_goals'][0]['actions']=[a['resource_name'] for a in s['conversion_actions']]
    p=build_amendment(s,r)
    assert sum(c['kind']=='campaign_goal' for c in p['operations'])==14
    s['campaign_goals'][0]['category']='PAGE_VIEW'
    with pytest.raises(ValueError):build_amendment(s,r)
