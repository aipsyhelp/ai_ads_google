import json
from datetime import datetime, timezone
import pytest
from ads_ai_agents.adapters.recovery_stability import assert_recovery_stable, load_recovery_state

STATE = {'customer_id':'1234567890', 'episode_id':'new',
         'started_at':'2026-10-07T13:05:00+00:00','freeze_until':'2026-10-10T13:05:00+00:00'}


def test_freeze_blocks_live_changes_and_allows_after_deadline(tmp_path, monkeypatch):
    monkeypatch.setenv('GOOGLE_ADS_CUSTOMER_ID','123-456-7890')
    path=tmp_path/'state.json'; path.write_text(json.dumps(STATE))
    with pytest.raises(PermissionError,match='stability window'):
        assert_recovery_stable(path=path,now=datetime(2026,10,8,tzinfo=timezone.utc))
    assert_recovery_stable(path=path,now=datetime(2026,10,11,tzinfo=timezone.utc))


@pytest.mark.parametrize('content',['{','{}',json.dumps({**STATE,'freeze_until':'2026-10-10T13:05:00'})])
def test_invalid_state_fails_closed(tmp_path,content):
    path=tmp_path/'state.json'; path.write_text(content)
    with pytest.raises(PermissionError):
        load_recovery_state(path)


def test_wrong_account_fails_closed(tmp_path,monkeypatch):
    path=tmp_path/'state.json'; path.write_text(json.dumps(STATE))
    monkeypatch.setenv('GOOGLE_ADS_CUSTOMER_ID','123')
    with pytest.raises(PermissionError,match='does not match'):
        assert_recovery_stable(path=path)


def test_freeze_is_checked_by_mode_and_scoped_write_guard(monkeypatch):
    from ads_ai_agents.adapters import recovery_stability
    from ads_ai_agents.adapters.automation_mode import assert_mutations_allowed
    from ads_ai_agents.adapters.write_guard import assert_write_service
    monkeypatch.setenv('ADS_AUTOMATION_MODE','CONTROLLED_LIVE')
    def blocked():
        raise PermissionError('test freeze')
    monkeypatch.setattr(recovery_stability,'assert_recovery_stable',blocked)
    with pytest.raises(PermissionError,match='test freeze'):
        assert_mutations_allowed()
    with pytest.raises(PermissionError,match='test freeze'):
        assert_write_service('BiddingStrategyService',scope='grant_delivery_recovery')
    assert_write_service('GoogleAdsService',scope='grant_delivery_recovery')
