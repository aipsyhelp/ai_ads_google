
from ads_ai_agents.adapters.automation_mode import (
    AutomationMode,
    assert_mutations_allowed,
    mutations_allowed,
    resolve_automation_mode,
)
from ads_ai_agents.intelligence.controlled_policy import load_controlled_policy


def test_default_mode_is_read_only(monkeypatch):
    monkeypatch.setenv("ADS_AUTOMATION_MODE", "")
    monkeypatch.setenv("ADS_AUTOPILOT_MODE", "")
    assert resolve_automation_mode() is AutomationMode.READ_ONLY


def test_invalid_mode_falls_back_to_read_only(monkeypatch):
    monkeypatch.setenv("ADS_AUTOMATION_MODE", "FULL_AUTOPILOT")
    assert resolve_automation_mode() is AutomationMode.READ_ONLY


def test_controlled_live_mode(monkeypatch):
    monkeypatch.setenv("ADS_AUTOMATION_MODE", "CONTROLLED_LIVE")
    assert resolve_automation_mode() is AutomationMode.CONTROLLED_LIVE
    assert mutations_allowed()


def test_autonomous_live_mode(monkeypatch):
    monkeypatch.setenv("ADS_AUTOMATION_MODE", "AUTONOMOUS_LIVE")
    assert resolve_automation_mode() is AutomationMode.AUTONOMOUS_LIVE
    assert mutations_allowed()
    assert assert_mutations_allowed() is AutomationMode.AUTONOMOUS_LIVE


def test_assert_blocks_read_only(monkeypatch):
    monkeypatch.setenv("ADS_AUTOMATION_MODE", "READ_ONLY")
    try:
        assert_mutations_allowed()
        raise AssertionError("expected PermissionError")
    except PermissionError:
        pass


def test_controlled_policy_defaults_budget_mutations_off(monkeypatch):
    monkeypatch.setenv("ADS_AUTOMATION_MODE", "CONTROLLED_LIVE")
    monkeypatch.delenv("ADS_AUTO_BUDGET", raising=False)
    policy = load_controlled_policy()
    assert not policy.auto_budget_under_cap


def test_controlled_policy_reads_safe_boolean_controls(monkeypatch):
    monkeypatch.setenv("ADS_AUTOMATION_MODE", "CONTROLLED_LIVE")
    monkeypatch.setenv("ADS_AUTO_ADD_NEGATIVES", "false")
    monkeypatch.setenv("ADS_AUTO_BUDGET", "true")
    policy = load_controlled_policy()
    assert not policy.auto_add_negatives
    assert policy.auto_budget_under_cap


def test_legacy_autopilot_mode_never_enables_live_writes(monkeypatch):
    monkeypatch.setenv("ADS_AUTOMATION_MODE", "")
    for legacy in ("live", "controlled_live", "controlled", "autonomous", "autonomous_live"):
        monkeypatch.setenv("ADS_AUTOPILOT_MODE", legacy)
        assert resolve_automation_mode() is AutomationMode.READ_ONLY
        assert not mutations_allowed()
