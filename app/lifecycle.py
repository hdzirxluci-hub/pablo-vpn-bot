from __future__ import annotations

import asyncio
import logging
import math
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.types import BufferedInputFile
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from .backups import BackupError, create_backup
from .config import Settings, get_settings
from .database import session_scope
from .models import SystemSetting

CHECK_INTERVAL_SECONDS = 60 * 60
logger = logging.getLogger("pablo-vpn.lifecycle")
_scheduler_task: asyncio.Task | None = None


async def get_setting(session: AsyncSession, key: str) -> str | None:
    row = await session.get(SystemSetting, key)
    return row.value if row else None


async def set_setting(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(SystemSetting, key)
    if row is None:
        session.add(SystemSetting(key=key, value=value))
    else:
        row.value = value
    await session.flush()


async def delete_setting(session: AsyncSession, key: str) -> None:
    await session.execute(delete(SystemSetting).where(SystemSetting.key == key))
    await session.flush()


async def initialize_lifecycle_settings(session: AsyncSession, settings: Settings) -> None:
    if settings.railway_expires_at and await get_setting(session, "railway_expires_at") is None:
        expiry = settings.railway_expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        await set_setting(session, "railway_expires_at", expiry.astimezone(UTC).isoformat())


async def set_railway_expiry(session: AsyncSession, days: int) -> datetime:
    if not 1 <= days <= 3650:
        raise ValueError("Railway expiry days must be between 1 and 3650")
    expiry = datetime.now(UTC) + timedelta(days=days)
    await set_setting(session, "railway_expires_at", expiry.isoformat())
    return expiry


async def get_railway_expiry(session: AsyncSession) -> datetime | None:
    raw = await get_setting(session, "railway_expires_at")
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(raw)
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    except ValueError:
        return None


async def send_encrypted_backup(bot: Bot, owner_id: int, reason: str) -> tuple[int, str]:
    settings = get_settings()
    if not settings.is_backup_configured:
        raise BackupError("BACKUP_PASSPHRASE is not configured")
    async with session_scope() as session:
        backup, summary = await create_backup(session, settings.backup_passphrase.get_secret_value())
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    filename = f"pablo-vpn-{stamp}.pablobak"
    caption = (
        f"🔐 بک‌آپ رمزگذاری‌شده PABLO VPN\n"
        f"علت: {reason}\n"
        f"تعداد رکوردها: {summary.total_rows}\n"
        f"برای بازیابی، همین فایل را برای ربات ارسال کن."
    )
    await bot.send_document(owner_id, BufferedInputFile(backup, filename=filename), caption=caption)
    return summary.total_rows, filename


async def scheduler_tick(bot: Bot, settings: Settings) -> None:
    if not settings.owner_telegram_id or not settings.is_backup_configured:
        return
    now = datetime.now(UTC)
    async with session_scope() as session:
        await initialize_lifecycle_settings(session, settings)
        last_auto_raw = await get_setting(session, "last_auto_backup_at")
        last_auto = None
        if last_auto_raw:
            with suppress(ValueError):
                last_auto = datetime.fromisoformat(last_auto_raw)
                if last_auto.tzinfo is None:
                    last_auto = last_auto.replace(tzinfo=UTC)
        auto_due = settings.auto_backup_enabled and (
            last_auto is None or now - last_auto >= timedelta(hours=settings.auto_backup_interval_hours)
        )
        expiry = await get_railway_expiry(session)
        warning_due = False
        warning_key = None
        days_remaining = None
        if expiry:
            seconds_remaining = (expiry - now).total_seconds()
            days_remaining = math.ceil(seconds_remaining / 86400)
            if 0 <= days_remaining <= settings.backup_warning_days:
                warning_key = f"railway_warning_sent:{expiry.isoformat()}"
                warning_due = await get_setting(session, warning_key) is None

    if warning_due and warning_key is not None and days_remaining is not None:
        stamp = datetime.now(UTC).isoformat()
        async with session_scope() as session:
            # Write the expiry-warning status before export so the outgoing backup preserves it.
            await set_setting(session, warning_key, stamp)
            await set_setting(session, "last_auto_backup_at", stamp)
        try:
            await bot.send_message(
                settings.owner_telegram_id,
                f"⚠️ فقط {days_remaining} روز تا تاریخ پایان تنظیم‌شدهٔ Railway مانده. "
                "یک بک‌آپ تازه همراه این هشدار ارسال می‌شود.",
            )
            await send_encrypted_backup(
                bot,
                settings.owner_telegram_id,
                f"هشدار Railway؛ {days_remaining} روز باقی‌مانده",
            )
        except Exception:
            async with session_scope() as session:
                await delete_setting(session, warning_key)
                if last_auto_raw:
                    await set_setting(session, "last_auto_backup_at", last_auto_raw)
                else:
                    await delete_setting(session, "last_auto_backup_at")
            raise
        return

    if auto_due:
        stamp = datetime.now(UTC).isoformat()
        async with session_scope() as session:
            await set_setting(session, "last_auto_backup_at", stamp)
        try:
            await send_encrypted_backup(bot, settings.owner_telegram_id, "بک‌آپ خودکار دوره‌ای")
        except Exception:
            async with session_scope() as session:
                if last_auto_raw:
                    await set_setting(session, "last_auto_backup_at", last_auto_raw)
                else:
                    await delete_setting(session, "last_auto_backup_at")
            raise


async def _scheduler_loop(bot: Bot, settings: Settings) -> None:
    await asyncio.sleep(10)
    while True:
        try:
            await scheduler_tick(bot, settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduled backup or Railway expiry check failed; it will be retried")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


def start_scheduler(bot: Bot | None) -> None:
    global _scheduler_task
    settings = get_settings()
    if bot is None or not settings.owner_telegram_id or not settings.is_backup_configured:
        return
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(_scheduler_loop(bot, settings), name="pablo-backup-scheduler")


async def stop_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task is not None:
        _scheduler_task.cancel()
        with suppress(asyncio.CancelledError):
            await _scheduler_task
        _scheduler_task = None
