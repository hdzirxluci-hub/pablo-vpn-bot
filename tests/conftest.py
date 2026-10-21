import os
from pathlib import Path

TEST_DB = Path("/tmp/pablo-vpn-test.db")
if TEST_DB.exists():
    TEST_DB.unlink()

os.environ.update(
    {
        "ENVIRONMENT": "test",
        "DEMO_MODE": "true",
        "BOT_USERNAME": "PablloVPN3_bot",
        "OWNER_TELEGRAM_ID": "900001",
        "DATABASE_URL": f"sqlite+aiosqlite:///{TEST_DB}",
        "BACKUP_PASSPHRASE": "test-only-backup-passphrase-1234",
    }
)
