from __future__ import annotations

from datetime import datetime
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "PABLO VPN"
    environment: Literal["development", "test", "production"] = "development"
    demo_mode: bool = True

    bot_username: str = "PablloVPN3_bot"
    bot_token: SecretStr | None = None
    owner_telegram_id: int | None = None
    telegram_webhook_secret: SecretStr | None = None

    public_url: str | None = None
    mini_app_url: str | None = None
    database_url: str = "sqlite+aiosqlite:///./pablo-vpn.db"
    init_data_max_age_seconds: int = Field(default=3600, ge=60, le=86400)

    backup_passphrase: SecretStr | None = None
    auto_backup_enabled: bool = True
    auto_backup_interval_hours: int = Field(default=24, ge=1, le=24 * 30)
    backup_warning_days: int = Field(default=3, ge=1, le=30)
    railway_expires_at: datetime | None = None

    panel_mode: Literal["mock", "real"] = "mock"
    panel_timeout_seconds: float = Field(default=15.0, ge=1, le=120)
    panel_verify_tls: bool = True

    pasarguard_url: str | None = None
    pasarguard_username: str | None = None
    pasarguard_password: SecretStr | None = None
    pasarguard_group_id: int | None = None

    xui_url: str | None = None
    xui_api_token: SecretStr | None = None
    xui_username: str | None = None
    xui_password: SecretStr | None = None
    xui_inbound_id: int | None = None
    xui_subscription_base_url: str | None = None

    marzban_url: str | None = None
    marzban_username: str | None = None
    marzban_password: SecretStr | None = None
    marzban_proxies_json: str = '{"vless":{}}'
    marzban_inbounds_json: str = "{}"

    @field_validator("bot_username", mode="before")
    @classmethod
    def strip_at_sign(cls, value: str) -> str:
        return str(value).strip().lstrip("@")

    @field_validator(
        "public_url",
        "mini_app_url",
        "pasarguard_url",
        "xui_url",
        "xui_subscription_base_url",
        "marzban_url",
        mode="before",
    )
    @classmethod
    def normalize_optional_url(cls, value: str | None) -> str | None:
        if not value:
            return None
        return str(value).strip().rstrip("/")

    @property
    def effective_mini_app_url(self) -> str | None:
        return self.mini_app_url or self.public_url

    @property
    def is_bot_configured(self) -> bool:
        return self.bot_token is not None and bool(self.bot_token.get_secret_value())

    @property
    def is_backup_configured(self) -> bool:
        return self.backup_passphrase is not None and len(self.backup_passphrase.get_secret_value()) >= 12

    def validate_production(self) -> None:
        if self.environment != "production":
            return
        missing: list[str] = []
        if self.demo_mode:
            missing.append("DEMO_MODE=false")
        if not self.is_bot_configured:
            missing.append("BOT_TOKEN")
        if not self.owner_telegram_id:
            missing.append("OWNER_TELEGRAM_ID")
        if not self.telegram_webhook_secret or len(self.telegram_webhook_secret.get_secret_value()) < 16:
            missing.append("TELEGRAM_WEBHOOK_SECRET (minimum 16 characters)")
        if not self.public_url or not self.public_url.startswith("https://"):
            missing.append("PUBLIC_URL (HTTPS)")
        if self.database_url.startswith("sqlite"):
            missing.append("DATABASE_URL (PostgreSQL required in production)")
        if not self.is_backup_configured:
            missing.append("BACKUP_PASSPHRASE (minimum 12 characters)")
        if not self.auto_backup_enabled:
            missing.append("AUTO_BACKUP_ENABLED=true")
        if self.panel_mode != "real":
            missing.append("PANEL_MODE=real")
        has_pasarguard_password = bool(self.pasarguard_password and self.pasarguard_password.get_secret_value())
        if not (
            self.pasarguard_url
            and self.pasarguard_username
            and has_pasarguard_password
            and self.pasarguard_group_id is not None
        ):
            missing.append("PasarGuard URL/username/password/group ID")
        has_xui_token = bool(self.xui_api_token and self.xui_api_token.get_secret_value())
        has_xui_password = bool(self.xui_password and self.xui_password.get_secret_value())
        has_xui_auth = bool(has_xui_token or (self.xui_username and has_xui_password))
        if not (self.xui_url and has_xui_auth and self.xui_inbound_id is not None and self.xui_subscription_base_url):
            missing.append("3x-ui URL/auth/inbound/subscription URL")
        has_marzban_password = bool(self.marzban_password and self.marzban_password.get_secret_value())
        if not (self.marzban_url and self.marzban_username and has_marzban_password):
            missing.append("Marzban URL/username/password")
        if missing:
            raise RuntimeError("Missing production settings: " + ", ".join(missing))


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_production()
    return settings
