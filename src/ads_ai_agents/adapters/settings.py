from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _strip_customer_id(value: str) -> str:
    return value.replace("-", "").strip()


def load_dotenv(path: Path | None = None) -> None:
    """Load ads_ai_agents/.env if present without requiring python-dotenv."""
    env_path = path or Path(__file__).resolve().parents[3] / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class GoogleAdsSettings:
    developer_token: str
    login_customer_id: str
    customer_id: str
    service_account_json: Path
    impersonated_email: str | None = None
    use_service_account: bool = True

    @classmethod
    def from_env(cls) -> GoogleAdsSettings:
        load_dotenv()
        token = os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", "").strip()
        login = _strip_customer_id(os.environ.get("GOOGLE_ADS_LOGIN_CUSTOMER_ID", ""))
        customer = _strip_customer_id(os.environ.get("GOOGLE_ADS_CUSTOMER_ID", ""))
        json_path = Path(
            os.environ.get(
                "GOOGLE_ADS_SERVICE_ACCOUNT_JSON",
                "",
            ).strip()
        ).expanduser()
        impersonated = os.environ.get("GOOGLE_ADS_IMPERSONATED_EMAIL", "").strip() or None
        use_sa = os.environ.get("GOOGLE_ADS_USE_SERVICE_ACCOUNT", "true").lower() in {
            "1",
            "true",
            "yes",
        }

        missing = [
            name
            for name, value in (
                ("GOOGLE_ADS_DEVELOPER_TOKEN", token),
                ("GOOGLE_ADS_LOGIN_CUSTOMER_ID", login),
                ("GOOGLE_ADS_CUSTOMER_ID", customer),
                ("GOOGLE_ADS_SERVICE_ACCOUNT_JSON", str(json_path) if json_path else ""),
            )
            if not value
        ]
        if missing:
            raise RuntimeError("Missing Google Ads settings: " + ", ".join(missing))
        if not json_path.is_file():
            raise RuntimeError(f"Service account JSON not found: {json_path}")

        return cls(
            developer_token=token,
            login_customer_id=login,
            customer_id=customer,
            service_account_json=json_path,
            impersonated_email=impersonated,
            use_service_account=use_sa,
        )

    def client_config(self) -> dict:
        config: dict = {
            "developer_token": self.developer_token,
            "login_customer_id": self.login_customer_id,
            "use_proto_plus": True,
            "json_key_file_path": str(self.service_account_json),
        }
        if self.impersonated_email:
            config["impersonated_email"] = self.impersonated_email
        return config
