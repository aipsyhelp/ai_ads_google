"""Read-only recovery observer and episode-scoped alert evaluation."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

# Ad Grants deactivates accounts whose monthly CTR stays below 5% for two
# consecutive months. Warn early while the current month can still recover.
GRANT_MIN_MONTHLY_CTR = 0.05
GRANT_CTR_WARNING = 0.06
CTR_WARNING_MIN_IMPRESSIONS = 100


def grant_ctr_alerts(monthly: list[dict[str, Any]], current_month: str) -> list[dict[str, str]]:
    """Evaluate the Ad Grants monthly CTR rule from per-month impressions/clicks."""
    alerts = []
    served = sorted((m for m in monthly if m['impressions'] > 0), key=lambda m: m['month'])
    complete = [m for m in served if m['month'] < current_month]
    below = [m for m in complete[-2:] if m['clicks'] / m['impressions'] < GRANT_MIN_MONTHLY_CTR]
    if len(complete) >= 2 and len(below) == 2:
        months = ', '.join(f"{m['month'][:7]} {m['clicks'] / m['impressions']:.2%}" for m in below)
        alerts.append({'code': 'GRANT_CTR_DEACTIVATION_RISK',
                       'detail': f'Two consecutive months below 5% CTR ({months}); confirm Ad Grants status',
                       'level': 'critical'})
    current = next((m for m in served if m['month'] == current_month), None)
    if current and current['impressions'] >= CTR_WARNING_MIN_IMPRESSIONS:
        ctr = current['clicks'] / current['impressions']
        if ctr < GRANT_CTR_WARNING:
            alerts.append({'code': 'GRANT_CTR_WARNING',
                           'detail': f'Month-to-date CTR {ctr:.2%} is below the 6% safety margin',
                           'level': 'warning'})
    return alerts


def _normalized(items: list[dict[str, Any]]) -> list[str]:
    return sorted(json.dumps(v, sort_keys=True, ensure_ascii=False) for v in items)


def _metrics(row: Any) -> dict[str, Any]:
    m = row.metrics
    rank = float(m.search_rank_lost_impression_share or 0)
    return {'impressions': int(m.impressions), 'clicks': int(m.clicks),
            'cost': round(int(m.cost_micros) / 1e6, 2), 'conversions': float(m.conversions),
            'ads_all_conversions': float(m.all_conversions),
            'rank_lost': rank if rank else None,
            'rank_lost_label': '>90%' if rank > .9 else (f'{rank:.1%}' if rank else 'unavailable'),
            'budget_lost': float(m.search_budget_lost_impression_share or 0)}


def collect_snapshot(adapter: Any, recovery: dict[str, Any]) -> dict[str, Any]:
    ga = adapter.get_service('GoogleAdsService')
    cid = adapter.settings.customer_id
    def query(sql: str):
        return ga.search(customer_id=cid, query=sql)
    rows = list(query('''SELECT customer.id, customer.status, customer.currency_code,
                        customer.time_zone, customer.auto_tagging_enabled FROM customer'''))
    if len(rows) != 1 or str(rows[0].customer.id) != str(recovery['customer_id']):
        raise RuntimeError('Recovery customer mismatch or account metadata unavailable')
    c = rows[0].customer
    now = datetime.now(timezone.utc)
    start_day = datetime.fromisoformat(recovery['started_at']).astimezone(ZoneInfo(c.time_zone)).date()
    end_day = now.astimezone(ZoneInfo(c.time_zone)).date()
    s: dict[str, Any] = {'taken_at': now.isoformat(timespec='seconds'), 'customer_id': str(c.id),
        'account': {'status': c.status.name, 'currency': c.currency_code,
                    'time_zone': c.time_zone, 'auto_tagging': bool(c.auto_tagging_enabled)},
        'campaigns': [], 'portfolios': [], 'negative_geo': [], 'approved_ads': {},
        'primary_actions': [], 'last7_campaigns': [], 'conversion_actions': [],
        'goal_configs': [], 'campaign_goals': [], 'custom_goals': [],
        'campaign_delivery': [], 'single_word_keywords': [], 'device_exclusions': [],
        'unaddressable_auto_apply': 0}
    for r in query('''SELECT campaign.id, campaign.name, campaign.status, campaign.primary_status,
        campaign.bidding_strategy, campaign.bidding_strategy_type, campaign.advertising_channel_type,
        campaign.maximize_conversions.target_cpa_micros,
        campaign_budget.id, campaign_budget.amount_micros FROM campaign WHERE campaign.status != 'REMOVED' '''):
        s['campaigns'].append({'id': str(r.campaign.id), 'name': r.campaign.name,
            'status': r.campaign.status.name, 'primary_status': r.campaign.primary_status.name,
            'bidding': r.campaign.bidding_strategy_type.name,
            'portfolio_id': r.campaign.bidding_strategy.rsplit('/', 1)[-1],
            'channel': r.campaign.advertising_channel_type.name, 'budget_id': str(r.campaign_budget.id),
            'budget': round(r.campaign_budget.amount_micros / 1e6, 2),
            'standalone_tcpa_micros': int(r.campaign.maximize_conversions.target_cpa_micros)})
    enabled = [v for v in s['campaigns'] if v['status'] == 'ENABLED']
    s['unique_daily_budget'] = round(sum({v['budget_id']: v['budget'] for v in enabled}.values()), 2)
    for r in query('''SELECT bidding_strategy.id, bidding_strategy.status, bidding_strategy.type,
        bidding_strategy.maximize_conversions.target_cpa_micros FROM bidding_strategy
        WHERE bidding_strategy.status != 'REMOVED' '''):
        b = r.bidding_strategy
        s['portfolios'].append({'id': str(b.id), 'status': b.status.name, 'type': b.type_.name,
                               'tcpa_micros': int(b.maximize_conversions.target_cpa_micros)})
    fields = '''metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions,
        metrics.all_conversions, metrics.search_rank_lost_impression_share,
        metrics.search_budget_lost_impression_share'''
    for label, predicate in (('today', 'segments.date DURING TODAY'),
                            ('yesterday', 'segments.date DURING YESTERDAY'),
                            ('since_recovery', f"segments.date BETWEEN '{start_day}' AND '{end_day}'")):
        rows = list(query(f'SELECT {fields} FROM customer WHERE {predicate}'))
        s[label] = _metrics(rows[0]) if rows else {'impressions': 0, 'clicks': 0, 'cost': 0,
            'conversions': 0, 'ads_all_conversions': 0, 'rank_lost': None,
            'rank_lost_label': 'unavailable', 'budget_lost': 0}
    month_start = end_day.replace(day=1)
    months_back = month_start.year * 12 + month_start.month - 1 - 2
    window_start = month_start.replace(year=months_back // 12, month=months_back % 12 + 1)
    s['current_month'] = month_start.isoformat()
    s['monthly_ctr'] = [{'month': r.segments.month, 'impressions': int(r.metrics.impressions),
                         'clicks': int(r.metrics.clicks)}
                        for r in query(f'''SELECT segments.month, metrics.impressions, metrics.clicks
        FROM customer WHERE segments.date BETWEEN '{window_start}' AND '{end_day}' ''')]
    for r in query(f'''SELECT campaign.id, {fields} FROM campaign
        WHERE campaign.status = 'ENABLED' AND segments.date DURING LAST_7_DAYS'''):
        s['last7_campaigns'].append({'id': str(r.campaign.id), **_metrics(r)})
    for r in query(f'''SELECT campaign.id, {fields} FROM campaign
        WHERE campaign.status = 'ENABLED' AND segments.date BETWEEN '{start_day}' AND '{end_day}' '''):
        s['campaign_delivery'].append({'id': str(r.campaign.id), **_metrics(r)})
    # Inspect field presence: an unset optional modifier also reads as 0.0.
    for r in query('''SELECT campaign.id, campaign_criterion.device.type,
        campaign_criterion.bid_modifier FROM campaign_criterion
        WHERE campaign.status = 'ENABLED' AND campaign_criterion.type = 'DEVICE' '''):
        c = r.campaign_criterion
        if c._pb.HasField('bid_modifier') and c.bid_modifier == 0:
            s['device_exclusions'].append({'campaign_id': str(r.campaign.id), 'device': c.device.type.name})
    for r in query('''SELECT campaign.id, ad_group.id, ad_group_criterion.resource_name,
        ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type,
        ad_group_criterion.status FROM ad_group_criterion
        WHERE campaign.status != 'REMOVED' AND ad_group.status != 'REMOVED'
        AND ad_group_criterion.status != 'REMOVED' AND ad_group_criterion.type = 'KEYWORD'
        AND ad_group_criterion.negative = FALSE'''):
        c = r.ad_group_criterion
        if len(c.keyword.text.split()) == 1:
            s['single_word_keywords'].append({'resource_name': c.resource_name,
                'campaign_id': str(r.campaign.id), 'ad_group_id': str(r.ad_group.id),
                'text': c.keyword.text, 'match_type': c.keyword.match_type.name, 'status': c.status.name})
    for r in query('''SELECT recommendation_subscription.type, recommendation_subscription.status
        FROM recommendation_subscription'''):
        c = r.recommendation_subscription
        if c.status.name == 'ENABLED' and c.type_.name == 'UNKNOWN':
            s['unaddressable_auto_apply'] += 1
    for r in query('''SELECT campaign.id, campaign_criterion.location.geo_target_constant
        FROM campaign_criterion WHERE campaign.status = 'ENABLED'
        AND campaign_criterion.type = 'LOCATION' AND campaign_criterion.negative = TRUE'''):
        s['negative_geo'].append({'campaign_id': str(r.campaign.id),
                                 'geo': r.campaign_criterion.location.geo_target_constant})
    for r in query('''SELECT campaign.id, ad_group_ad.policy_summary.approval_status FROM ad_group_ad
        WHERE campaign.status = 'ENABLED' AND ad_group.status = 'ENABLED' AND ad_group_ad.status = 'ENABLED' '''):
        if r.ad_group_ad.policy_summary.approval_status.name in {'APPROVED', 'APPROVED_LIMITED'}:
            key = str(r.campaign.id)
            s['approved_ads'][key] = s['approved_ads'].get(key, 0) + 1
    for r in query('''SELECT conversion_action.resource_name, conversion_action.name, conversion_action.primary_for_goal,
        conversion_action.include_in_conversions_metric, conversion_action.category, conversion_action.origin
        FROM conversion_action
        WHERE conversion_action.status = 'ENABLED' '''):
        if r.conversion_action.primary_for_goal:
            s['primary_actions'].append(r.conversion_action.name)
        a = r.conversion_action
        s['conversion_actions'].append({'resource_name': a.resource_name, 'name': a.name, 'primary': bool(a.primary_for_goal),
            'included': bool(a.include_in_conversions_metric), 'category': a.category.name, 'origin': a.origin.name})
    enabled_resources = {f'customers/{cid}/campaigns/{v["id"]}' for v in enabled}
    for r in query('''SELECT conversion_goal_campaign_config.campaign,
        conversion_goal_campaign_config.goal_config_level,
        conversion_goal_campaign_config.custom_conversion_goal FROM conversion_goal_campaign_config'''):
        g = r.conversion_goal_campaign_config
        if g.campaign in enabled_resources:
            s['goal_configs'].append({'campaign': g.campaign, 'level': g.goal_config_level.name,
                                      'custom_goal': g.custom_conversion_goal})
    for r in query('''SELECT campaign_conversion_goal.resource_name, campaign_conversion_goal.campaign, campaign_conversion_goal.category,
        campaign_conversion_goal.origin, campaign_conversion_goal.biddable FROM campaign_conversion_goal'''):
        g = r.campaign_conversion_goal
        if g.campaign in enabled_resources:
            s['campaign_goals'].append({'resource_name': g.resource_name, 'campaign': g.campaign, 'category': g.category.name,
                                       'origin': g.origin.name, 'biddable': bool(g.biddable)})
    referenced_goals = {v['custom_goal'] for v in s['goal_configs'] if v['custom_goal']}
    for r in query('''SELECT custom_conversion_goal.resource_name, custom_conversion_goal.status,
        custom_conversion_goal.conversion_actions FROM custom_conversion_goal'''):
        g = r.custom_conversion_goal
        if g.resource_name in referenced_goals:
            s['custom_goals'].append({'resource_name': g.resource_name, 'status': g.status.name,
                                     'actions': sorted(g.conversion_actions)})
    s['measurement_note'] = ('Ads conversions are ad-attributed reporting, not site-wide GA4 events. '
                             'Zero conversions without ad clicks do not prove a broken tag.')
    s['goal_cleanup_plan'] = plan_goal_cleanup(s)
    return s


def plan_goal_cleanup(snapshot: dict[str, Any]) -> dict[str, Any]:
    from ads_ai_agents.intelligence.grant_delivery_recovery import MEANINGFUL_PRIMARY_ACTIONS

    actions = snapshot.get('conversion_actions', [])
    by_name = {v['name']: v for v in actions}
    by_resource = {v['resource_name']: v for v in actions}
    missing = [name for name in MEANINGFUL_PRIMARY_ACTIONS if name not in by_name]
    desired = sorted(by_name[name]['resource_name'] for name in MEANINGFUL_PRIMARY_ACTIONS if name in by_name)
    plans = []
    for goal in snapshot.get('custom_goals', []):
        if sorted(goal['actions']) == desired:
            continue
        plans.append({'resource_name': goal['resource_name'], 'before': goal['actions'], 'after': desired,
            'removed_names': [by_resource.get(r, {}).get('name', r) for r in goal['actions'] if r not in desired],
            'added_names': [by_resource[r]['name'] for r in desired if r not in goal['actions']],
            'rollback': goal['actions']})
    selected_campaigns = {g['campaign'] for g in snapshot.get('goal_configs', []) if g.get('custom_goal')}
    standard_overrides = [g for g in snapshot.get('campaign_goals', [])
                          if g['campaign'] in selected_campaigns and g['biddable']]
    return {'mode': 'PLAN_ONLY', 'ready': not missing, 'missing_actions': missing, 'goals': plans,
            'standard_goal_overrides': standard_overrides,
            'reason': 'Custom goals also bid on secondary actions. Use meaningful intent actions; report chat_message_sent separately as activation.'}


def evaluate(snapshot: dict[str, Any], recovery: dict[str, Any], previous: dict[str, Any] | None = None,
             *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    previous = previous if previous and previous.get('episode_id') == recovery['episode_id'] else {}
    alerts: list[dict[str, str]] = []
    def alert(code: str, detail: str, level: str = 'warning') -> None:
        alerts.append({'code': code, 'detail': detail, 'level': level})
    campaigns = {v['id']: v for v in snapshot['campaigns']}
    expected = recovery['campaigns']
    actual_enabled = {v['id'] for v in snapshot['campaigns'] if v['status'] == 'ENABLED'}
    if actual_enabled != set(expected):
        alert('CAMPAIGN_SET_DRIFT', 'Enabled campaigns differ from the recovery baseline', 'critical')
    for cid, baseline in expected.items():
        live = campaigns.get(cid, {})
        if any(live.get(k) != v for k, v in baseline.items()):
            alert('CAMPAIGN_DRIFT', cid + ': status/bidding/portfolio/budget changed', 'critical')
        if live.get('primary_status') not in {'ELIGIBLE', 'LEARNING', 'LIMITED'}:
            alert('CAMPAIGN_BLOCKED', cid + ': ' + str(live.get('primary_status')), 'critical')
        if not snapshot['approved_ads'].get(cid):
            alert('NO_APPROVED_ADS', cid + ': no active approved ad', 'critical')
    portfolios = {v['id']: v for v in snapshot['portfolios']}
    for pid, baseline in recovery['portfolios'].items():
        if any(portfolios.get(pid, {}).get(k) != v for k, v in baseline.items()):
            alert('BIDDING_DRIFT', pid + ': recovery tCPA/strategy changed', 'critical')
    if snapshot['account']['status'] != 'ENABLED':
        alert('ACCOUNT_DISABLED', 'Google Ads customer is not enabled', 'critical')
    if not snapshot['account']['auto_tagging']:
        alert('AUTO_TAGGING_OFF', 'Auto-tagging disabled', 'critical')
    if abs(snapshot['unique_daily_budget'] - recovery['unique_daily_budget']) > .01:
        alert('BUDGET_DRIFT', 'Unique enabled budget differs from the recovery baseline', 'critical')
    if _normalized(snapshot['negative_geo']) != _normalized(recovery.get('negative_geo', [])):
        alert('GEO_DRIFT', 'Negative geo targeting changed', 'critical')
    if set(snapshot['primary_actions']) != set(recovery['primary_actions']):
        alert('CONVERSION_GOALS_DRIFT', 'Primary conversion actions changed', 'critical')
    for key in ('conversion_actions', 'goal_configs', 'campaign_goals', 'custom_goals'):
        if key in recovery and _normalized(snapshot.get(key, [])) != _normalized(recovery[key]):
            alert('GOAL_SELECTION_DRIFT', key + ': bidding goal selection changed', 'critical')
    if snapshot.get('automation_mode') != 'READ_ONLY':
        alert('LIVE_MODE_DRIFT', 'Automation mode differs from READ_ONLY', 'critical')
    plan = snapshot.get('goal_cleanup_plan', {})
    if plan.get('goals') or plan.get('standard_goal_overrides'):
        alert('BIDDING_GOAL_QUALITY', 'Bidding differs from the selected intent actions: review custom-goal membership and overlapping standard goals')
    if snapshot.get('single_word_keywords'):
        alert('SINGLE_WORD_KEYWORD_REVIEW', 'Single-word positive keywords present; check Ad Grants exceptions before removal', 'critical')
    if snapshot.get('device_exclusions'):
        alert('DEVICE_EXCLUSION_REVIEW', 'Explicit device opt-outs present; review delivery coverage')
    if 'monthly_ctr' in snapshot:
        alerts.extend(grant_ctr_alerts(snapshot['monthly_ctr'], snapshot['current_month']))
    if snapshot.get('unaddressable_auto_apply'):
        alert('AUTO_APPLY_UI_REVIEW', 'Enabled UNKNOWN auto-apply subscriptions cannot be addressed through this API; verify in Google Ads UI')
    baseline = recovery.get('metrics_baseline', {})
    delta = {k: max(0, float(snapshot['since_recovery'].get(k, 0)) - float(baseline.get(k, 0)))
             for k in ('impressions', 'clicks', 'conversions')}
    milestones = dict(previous.get('milestones', {}))
    for metric, code in (('impressions', 'FIRST_IMPRESSIONS_ALERT'),
                         ('clicks', 'FIRST_CLICK_ALERT'), ('conversions', 'ADS_CONVERSIONS_RECORDED')):
        if delta[metric] > 0 and not milestones.get(metric):
            alert(code, f'First new {metric} in this recovery episode', 'info')
            milestones[metric] = True
    end = datetime.fromisoformat(recovery['freeze_until'])
    start = datetime.fromisoformat(recovery['started_at'])
    delivered = {v['id']: v for v in snapshot.get('campaign_delivery', [])}
    campaign_milestones = dict(previous.get('campaign_milestones', {}))
    for cid in expected:
        prior = recovery.get('campaign_metrics_baseline', {}).get(cid, {}).get('impressions', 0)
        impressions = max(0, delivered.get(cid, {}).get('impressions', 0) - prior)
        if impressions > 0 and not campaign_milestones.get(cid):
            alert('CAMPAIGN_FIRST_IMPRESSIONS', cid + ': first new impressions', 'info')
            campaign_milestones[cid] = True
        elif now >= end and not campaign_milestones.get(cid) and 'campaign_delivery' in snapshot:
            alert('CAMPAIGN_ZERO_DELIVERY', cid + ': no new impressions by the review deadline')
    if now >= end:
        alert('DEADLINE_REVIEW_REQUIRED', '72-hour experiment ended; review evidence before any further changes')
        if delta['impressions'] == 0 and 'campaign_delivery' in snapshot:
            alert('ACCOUNT_WIDE_DELIVERY_STALL', 'No new impressions after 72h: check Grants activation, policy notices and tracking before another bid change', 'critical')
    elif (now - start).total_seconds() >= 86400 and delta['impressions'] == 0:
        alert('ZERO_DELIVERY_24H', 'Still no new impressions after 24 hours; observe to the review deadline')
    active = [a for a in alerts if a['level'] != 'info']
    previous_active = previous.get('active_alerts', [])
    new_alerts = [a for a in alerts if a['level'] == 'info' or a not in previous_active]
    for old in previous_active:
        if old not in active:
            new_alerts.append({'code': 'ALERT_RESOLVED', 'detail': old['code'] + ': ' + old['detail'], 'level': 'info'})
    return {'episode_id': recovery['episode_id'], 'milestones': milestones, 'campaign_milestones': campaign_milestones,
            'active_alerts': active, 'new_alerts': new_alerts, 'freeze_active': now < end,
            'freeze_until': recovery['freeze_until'], 'new_delivery': delta, 'snapshot': snapshot}
