from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from app.backups import decrypt_backup
from app.config import get_settings
from app.database import close_database, init_database, session_scope
from app.lifecycle import scheduler_tick, set_setting
from app.models import SystemSetting


class FakeBot:
    def __init__(self) -> None:
        self.messages = []
        self.documents = []

    async def send_message(self, chat_id, text):
        self.messages.append((chat_id, text))

    async def send_document(self, chat_id, document, caption):
        self.documents.append((chat_id, document, caption))


@pytest.mark.asyncio
async def test_railway_warning_sends_one_backup_and_preserves_warning_status() -> None:
    await init_database()
    settings = get_settings()
    fake_bot = FakeBot()
    expiry = datetime.now(UTC) + timedelta(days=2)
    try:
        async with session_scope() as session:
            await session.execute(delete(SystemSetting).where(SystemSetting.key.like("railway_warning_sent:%")))
            await set_setting(session, "railway_expires_at", expiry.isoformat())
            await set_setting(session, "last_auto_backup_at", datetime.now(UTC).isoformat())

        await scheduler_tick(fake_bot, settings)
        assert len(fake_bot.messages) == 1
        assert len(fake_bot.documents) == 1
        _, document, _ = fake_bot.documents[0]
        payload = decrypt_backup(document.data, settings.backup_passphrase.get_secret_value())
        setting_rows = payload["tables"]["system_settings"]
        setting_keys = {row["key"] for row in setting_rows}
        assert "railway_expires_at" in setting_keys
        assert any(key.startswith("railway_warning_sent:") for key in setting_keys)

        await scheduler_tick(fake_bot, settings)
        assert len(fake_bot.messages) == 1
        assert len(fake_bot.documents) == 1
    finally:
        await close_database()
