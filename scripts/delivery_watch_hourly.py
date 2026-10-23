#!/usr/bin/env python3
"""Read-only recovery agents. Exit 0=unchanged, 1=new alert, 3=unknown/API failure."""
from __future__ import annotations
import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--recovery-state', type=Path, default=ROOT / 'var/recovery_stability.json')
    parser.add_argument('--history-dir', type=Path, default=ROOT / 'var/delivery_watch_hourly')
    args = parser.parse_args()
    from ads_ai_agents.adapters.settings import load_dotenv
    from ads_ai_agents.adapters.automation_mode import resolve_automation_mode
    from ads_ai_agents.adapters.google_ads_readonly import GoogleAdsReadOnlyAdapter
    from ads_ai_agents.adapters.recovery_stability import load_recovery_state
    from ads_ai_agents.intelligence.recovery_monitor import collect_snapshot, evaluate
    load_dotenv(args.env_file)
    logging.getLogger('google.ads.googleads.client').setLevel(logging.CRITICAL)
    history = args.history_dir
    last_health = None
    if (history / 'latest.json').exists():
        try:
            last_health = json.loads((history / 'latest.json').read_text())
        except (OSError, ValueError):
            pass
    try:
        recovery = load_recovery_state(args.recovery_state)
        if recovery is None:
            raise RuntimeError('Recovery baseline missing')
        snapshot = collect_snapshot(GoogleAdsReadOnlyAdapter(), recovery)
        snapshot['automation_mode'] = resolve_automation_mode().value
        previous = None
        if (history / 'state.json').exists():
            try:
                previous = json.loads((history / 'state.json').read_text())
            except (OSError, ValueError):
                pass
        result = evaluate(snapshot, recovery, previous)
    except Exception as exc:
        failure = {'taken_at': datetime.now(timezone.utc).isoformat(),
                   'status': 'UNKNOWN', 'error_type': type(exc).__name__}
        write_json(history / 'latest.json', failure)
        print(f'HOURLY_WATCH_ERROR account_state=UNKNOWN error_type={type(exc).__name__}', flush=True)
        return 3
    if last_health and last_health.get('status') == 'UNKNOWN':
        result['new_alerts'].append({'code': 'MONITOR_RECOVERED', 'detail': 'API checks work again', 'level': 'info'})
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    history.mkdir(parents=True, exist_ok=True)
    for alert in result['new_alerts']:
        line = json.dumps({'taken_at': snapshot['taken_at'], 'episode_id': recovery['episode_id'], **alert}, ensure_ascii=False)
        with (history / 'alerts.jsonl').open('a', encoding='utf-8') as f:
            f.write(line + '\n')
        if alert['code'] == 'FIRST_IMPRESSIONS_ALERT':
            (history / 'FIRST_IMPRESSIONS_ALERT.txt').write_text(line + '\n', encoding='utf-8')
        print(alert['code'] + ' ' + alert['detail'], flush=True)
    # Persist alert milestones after the alert journal, so an interrupted write
    # can retry a notice instead of permanently suppressing its first delivery.
    write_json(history / f'hourly-{stamp}.json', result)
    write_json(history / 'state.json', result)
    write_json(history / 'latest.json', result)
    status = 'DELIVERY_LIVE' if result['new_delivery']['impressions'] else 'STILL_ZERO'
    print(f"{status} today_impr={snapshot['today']['impressions']} clicks={snapshot['today']['clicks']} "
          f"cost=${snapshot['today']['cost']} ads_conversions={snapshot['today']['conversions']} "
          f"freeze_until={recovery['freeze_until']} at={snapshot['taken_at']}", flush=True)
    return 1 if result['new_alerts'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
