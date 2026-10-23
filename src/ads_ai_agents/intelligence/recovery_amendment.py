"""Build a narrowly scoped, operator-invoked correction to a delivery recovery.

This is not an autonomous bidding policy. It preserves the two existing targets
and national targeting, fixes the custom goal and aligns newly enabled city
campaigns. All resource IDs come from ``config/account_ids.json`` (see
``config/account_ids.example.json`` for the fake placeholder layout).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from ads_ai_agents.platform.account_ids import load_account_ids

_IDS = load_account_ids()
_REC = _IDS.get('recovery') or {}
CUSTOMER = str(_IDS.get('customer_id') or '0000000000')
CORE_GROUPS: dict[str, str] = {str(k): str(v) for k, v in (_REC.get('core_campaigns') or {}).items()}
CORE = set(CORE_GROUPS)
PRIMARY_CORE = {cid for cid, group in CORE_GROUPS.items() if group == 'primary'}
CITIES = {str(c) for c in _REC.get('city_campaigns') or []}
CITY_WITH_ACCOUNT_GOAL = str(_REC.get('city_campaign_with_account_goal') or '')
PRIMARY_PORTFOLIO = str((_IDS.get('portfolio_strategies') or {}).get('primary') or '')
SECONDARY_PORTFOLIO = str((_IDS.get('portfolio_strategies') or {}).get('secondary') or '')
_BUDGETS = _IDS.get('budgets') or {}
PRIMARY_BUDGET = str((_BUDGETS.get('primary') or {}).get('id') or '')
SECONDARY_BUDGET = str((_BUDGETS.get('secondary') or {}).get('id') or '')
PRIMARY_BUDGET_AMOUNT = float((_BUDGETS.get('primary') or {}).get('amount') or 0)
SECONDARY_BUDGET_AMOUNT = float((_BUDGETS.get('secondary') or {}).get('amount') or 0)
TARGET_CPA_MICROS = int(_IDS.get('target_cpa_micros') or 0)
EXPECTED_UNIQUE_DAILY_BUDGET = float(_IDS.get('expected_unique_daily_budget') or 0)
GOAL = f"customers/{CUSTOMER}/customConversionGoals/{_REC.get('custom_goal_id') or '0'}"
_KW = _REC.get('single_word_keyword') or {}
SINGLE_WORD_TEXT = str(_KW.get('text') or 'wellbeing')
WELLBEING = f"customers/{CUSTOMER}/adGroupCriteria/{_KW.get('ad_group_id') or '0'}~{_KW.get('criterion_id') or '0'}"
# Backwards-compatible aliases.
UA_PORTFOLIO = PRIMARY_PORTFOLIO
UA_BUDGET = PRIMARY_BUDGET


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def build_amendment(snapshot: dict, recovery: dict) -> dict:
    if str(snapshot['customer_id']) != CUSTOMER or str(recovery['customer_id']) != CUSTOMER:
        raise ValueError('Account mismatch')
    enabled = {c['id']: c for c in snapshot['campaigns'] if c['status'] == 'ENABLED'}
    if set(enabled) != CORE | CITIES or any(c['channel'] != 'SEARCH' for c in enabled.values()):
        raise ValueError('Expected exactly the reviewed Search campaigns')
    portfolios = {p['id']: p for p in snapshot['portfolios']}
    for pid in (PRIMARY_PORTFOLIO, SECONDARY_PORTFOLIO):
        p = portfolios.get(pid, {})
        if p.get('type') != 'MAXIMIZE_CONVERSIONS' or p.get('tcpa_micros') != TARGET_CPA_MICROS:
            raise ValueError('Existing target/strategy changed; diagnosis required')
    for cid, group in sorted(CORE_GROUPS.items()):
        budget_id, amount = ((PRIMARY_BUDGET, PRIMARY_BUDGET_AMOUNT) if group == 'primary'
                             else (SECONDARY_BUDGET, SECONDARY_BUDGET_AMOUNT))
        if enabled[cid]['budget_id'] != budget_id or enabled[cid]['budget'] != amount:
            raise ValueError(f'{group} shared budget changed')
    for cid in CORE:
        ua = cid in PRIMARY_CORE
        c = enabled[cid]
        if (c['bidding'] != 'MAXIMIZE_CONVERSIONS'
            or c['portfolio_id'] != (PRIMARY_PORTFOLIO if ua else SECONDARY_PORTFOLIO)
            or c['budget_id'] != (PRIMARY_BUDGET if ua else SECONDARY_BUDGET)
            or c['budget'] != (PRIMARY_BUDGET_AMOUNT if ua else SECONDARY_BUDGET_AMOUNT)):
            raise ValueError('Core campaign control changed')
    goal_plan = snapshot['goal_cleanup_plan']
    if not goal_plan['ready']:
        raise ValueError('Required conversion actions missing')
    goals = {g['resource_name']: g for g in snapshot['custom_goals']}
    if set(goals) != {GOAL}:
        raise ValueError('Custom goal scope changed')
    from ads_ai_agents.intelligence.grant_delivery_recovery import MEANINGFUL_PRIMARY_ACTIONS
    actions = {a['name']: a for a in snapshot['conversion_actions']}
    desired = sorted(actions[n]['resource_name'] for n in MEANINGFUL_PRIMARY_ACTIONS)
    if any(not actions[n]['primary'] for n in MEANINGFUL_PRIMARY_ACTIONS):
        raise ValueError('Intent actions are no longer primary')
    operations = []
    if sorted(goals[GOAL]['actions']) != desired:
        operations.append({'kind': 'custom_goal', 'resource': GOAL,
                           'before': sorted(goals[GOAL]['actions']), 'after': desired})
    for cid in sorted(CITIES):
        c = enabled[cid]
        if c['bidding'] != 'MAXIMIZE_CONVERSIONS':
            raise ValueError('City strategy changed')
        if c['portfolio_id'] != PRIMARY_PORTFOLIO or c['budget_id'] != PRIMARY_BUDGET:
            operations.append({'kind': 'campaign', 'resource': f'customers/{CUSTOMER}/campaigns/{cid}',
                'before': {k: c[k] for k in ('portfolio_id', 'budget_id', 'standalone_tcpa_micros')},
                'after': {'portfolio_id': PRIMARY_PORTFOLIO, 'budget_id': PRIMARY_BUDGET}})
    configs = {g['campaign'].rsplit('/', 1)[-1]: g for g in snapshot['goal_configs']}
    for cid in sorted(enabled):
        config = configs[cid]
        if config['custom_goal'] != GOAL:
            if cid != CITY_WITH_ACCOUNT_GOAL or config['level'] != 'CUSTOMER':
                raise ValueError('Goal selection changed outside reviewed city scope')
            operations.append({'kind': 'goal_config',
                'resource': f'customers/{CUSTOMER}/conversionGoalCampaignConfigs/{cid}',
                'before': {'level': config['level'], 'custom_goal': config['custom_goal']},
                'after': {'level': 'CAMPAIGN', 'custom_goal': GOAL}})
    singles = snapshot.get('single_word_keywords', [])
    if any(k['resource_name'] != WELLBEING or k['text'].lower() != SINGLE_WORD_TEXT for k in singles):
        raise ValueError('Additional single-word keywords require individual policy review')
    for keyword in singles:
        operations.append({'kind': 'remove_keyword', 'resource': WELLBEING,
                           'before': keyword, 'after': 'REMOVED'})
    # Standard goals remain additive to a custom goal. False here affects
    # only these campaigns; custom-goal actions remain biddable independently.
    for goal in sorted(snapshot['campaign_goals'], key=lambda g: (g['campaign'], g['category'], g['origin'])):
        if not goal['biddable']:
            continue
        if (goal['campaign'].rsplit('/', 1)[-1] not in enabled
            or goal['category'] not in {'CONTACT', 'SIGNUP'} or goal['origin'] != 'WEBSITE'):
            raise ValueError('Unexpected standard goal needs individual review')
        cid = goal['campaign'].rsplit('/', 1)[-1]
        expected_resource = f"customers/{CUSTOMER}/campaignConversionGoals/{cid}~{goal['category']}~{goal['origin']}"
        if goal['resource_name'] != expected_resource:
            raise ValueError('Campaign goal resource mismatch')
        operations.append({'kind': 'campaign_goal', 'resource': goal['resource_name'],
                           'before': True, 'after': False})
    # Only control facts enter the precondition hash; delivery and learning state
    # can change normally between validation and application.
    before = {k: snapshot[k] for k in ('customer_id', 'unique_daily_budget', 'negative_geo',
              'goal_configs', 'campaign_goals', 'custom_goals', 'conversion_actions', 'single_word_keywords')}
    before['campaigns'] = [{k: v for k, v in c.items() if k not in ('name', 'primary_status')}
                           for c in enabled.values()]
    before['portfolios'] = [portfolios[pid] for pid in (PRIMARY_PORTFOLIO, SECONDARY_PORTFOLIO)]
    # API row order is unspecified.
    for key, value in before.items():
        if isinstance(value, list):
            before[key] = sorted(value, key=digest)
    payload = {'customer_id': CUSTOMER, 'episode_id': recovery['episode_id'],
        'precondition_sha256': digest(before), 'operations': operations,
        'expected_unique_daily_budget': EXPECTED_UNIQUE_DAILY_BUDGET,
        'preserved_targets_micros': {PRIMARY_PORTFOLIO: TARGET_CPA_MICROS, SECONDARY_PORTFOLIO: TARGET_CPA_MICROS}}
    return {**payload, 'sha256': digest(payload)}
