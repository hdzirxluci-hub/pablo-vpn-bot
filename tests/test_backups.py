import pytest
from sqlalchemy import select

from app.backups import BackupError, create_backup, decrypt_backup, restore_backup
from app.database import close_database, init_database, session_scope
from app.models import PanelInstance, SystemSetting, User

PASSPHRASE = "test-only-backup-passphrase-1234"


@pytest.mark.asyncio
async def test_encrypted_backup_round_trip_restores_business_data() -> None:
    await init_database()
    try:
        async with session_scope() as session:
            user = User(
                telegram_id=889911,
                first_name="Backup Before",
                username="backup_test",
                language_code="fa",
            )
            session.add(user)
            session.add(SystemSetting(key="railway_expires_at", value="2030-01-02T03:04:05+00:00"))
            panel = await session.get(PanelInstance, "pg-ams-1")
            panel.config_ciphertext = b"encrypted-panel-config-placeholder"

        async with session_scope() as session:
            encrypted, summary = await create_backup(session, PASSPHRASE)
            assert summary.total_rows > 0
            assert b"Backup Before" not in encrypted
            payload = decrypt_backup(encrypted, PASSPHRASE)
            assert payload["contains_environment_secrets"] is False

        with pytest.raises(BackupError, match="decryption|integrity"):
            decrypt_backup(encrypted, "wrong-passphrase-but-long-enough")

        async with session_scope() as session:
            user = await session.scalar(select(User).where(User.telegram_id == 889911))
            user.first_name = "Backup After"
            setting = await session.get(SystemSetting, "railway_expires_at")
            setting.value = "changed"

        async with session_scope() as session:
            restored = await restore_backup(session, encrypted, PASSPHRASE, actor_telegram_id=900001)
            assert restored.total_rows == summary.total_rows

        async with session_scope() as session:
            user = await session.scalar(select(User).where(User.telegram_id == 889911))
            setting = await session.get(SystemSetting, "railway_expires_at")
            panel = await session.get(PanelInstance, "pg-ams-1")
            assert user.first_name == "Backup Before"
            assert setting.value == "2030-01-02T03:04:05+00:00"
            assert panel.config_ciphertext == b"encrypted-panel-config-placeholder"
    finally:
        await close_database()
