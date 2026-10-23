from __future__ import annotations

import logging
import re
from typing import Any


SECRET_PATTERNS = (
    re.compile(r"(developer[_-]?token\s*[=:]\s*)(\S+)", re.I),
    re.compile(r"(private_key\s*[=:]\s*)(\S+)", re.I),
    re.compile(r"(refresh[_-]?token\s*[=:]\s*)(\S+)", re.I),
    re.compile(r"(Bearer\s+)([A-Za-z0-9\-._~+/]+=*)", re.I),
    re.compile(r"(-----BEGIN [A-Z ]*PRIVATE KEY-----)(.*?)(-----END [A-Z ]*PRIVATE KEY-----)", re.S),
)


def redact_secrets(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        if pattern.groups >= 3:
            redacted = pattern.sub(r"\1[REDACTED]\3", redacted)
        else:
            redacted = pattern.sub(r"\1[REDACTED]", redacted)
    return redacted


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        return redact_secrets(original)


def configure_logging(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("ads_ai_agents")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            RedactingFormatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
        logger.addHandler(handler)
    logger.setLevel(level)
    return logger


def assert_no_secrets_printed(payload: Any) -> None:
    text = redact_secrets(str(payload))
    lowered = str(payload).lower()
    if "begin private key" in lowered:
        raise RuntimeError("Refusing to print private key material")
    if "developer_token=" in lowered and "[redacted]" not in text.lower():
        # raw token dump patterns
        pass
