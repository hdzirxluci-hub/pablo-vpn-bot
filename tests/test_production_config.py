import pytest

from app.config import Settings


def test_production_refuses_missing_secrets_sqlite_and_mock_panels() -> None:
    settings = Settings(
        environment="production",
        demo_mode=False,
        bot_token=None,
        owner_telegram_id=None,
        telegram_webhook_secret=None,
        public_url=None,
        backup_passphrase=None,
        panel_mode="mock",
        database_url="sqlite+aiosqlite:///./unsafe.db",
    )
    with pytest.raises(RuntimeError) as exc:
        settings.validate_production()
    message = str(exc.value)
    assert "BOT_TOKEN" in message
    assert "OWNER_TELEGRAM_ID" in message
    assert "PostgreSQL" in message
    assert "BACKUP_PASSPHRASE" in message
    assert "PANEL_MODE=real" in message


def test_complete_production_security_settings_are_accepted() -> None:
    settings = Settings(
        environment="production",
        demo_mode=False,
        bot_token="123456:placeholder-not-a-real-token",
        owner_telegram_id=900001,
        telegram_webhook_secret="long-webhook-secret",
        public_url="https://example.up.railway.app",
        backup_passphrase="long-backup-passphrase-placeholder",
        panel_mode="real",
        database_url="postgresql+asyncpg://user:password@db.internal/app",
        pasarguard_url="https://pasar.example",
        pasarguard_username="admin",
        pasarguard_password="placeholder",
        pasarguard_group_id=1,
        xui_url="https://xui.example",
        xui_api_token="placeholder",
        xui_inbound_id=1,
        xui_subscription_base_url="https://sub.example/sub",
        marzban_url="https://marzban.example",
        marzban_username="admin",
        marzban_password="placeholder",
    )
    settings.validate_production()
