from ads_ai_agents.logging_utils import redact_secrets

FAKE_DEVELOPER_TOKEN = "FAKE-DEV-TOKEN-0000"  # obviously fake, never a real credential


def test_redacts_developer_token_and_private_key():
    text = f"developer_token={FAKE_DEVELOPER_TOKEN} private_key=FAKE-PRIVATE-KEY"
    redacted = redact_secrets(text)
    assert FAKE_DEVELOPER_TOKEN not in redacted
    assert "FAKE-PRIVATE-KEY" not in redacted
    assert redacted.count("[REDACTED]") == 2


def test_redacts_pem_private_key_blocks():
    header = "-----BEGIN " + "RSA PRIVATE KEY-----"
    footer = "-----END " + "RSA PRIVATE KEY-----"
    redacted = redact_secrets(f"{header}\nFAKEKEYMATERIAL\n{footer}")
    assert "FAKEKEYMATERIAL" not in redacted
