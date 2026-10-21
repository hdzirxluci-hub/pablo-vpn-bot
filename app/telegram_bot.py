from __future__ import annotations

import html
import io
import logging
from datetime import UTC, datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from .backups import BackupError, decrypt_backup, restore_backup
from .config import get_settings
from .database import session_scope, upsert_user
from .lifecycle import get_railway_expiry, send_encrypted_backup, set_railway_expiry
from .models import AuditLog, Order, PanelInstance, Plan, Service, User
from .panel_clients import build_panel_client
from .panels import PanelAdapterError, provision_order
from .schemas import TelegramIdentity
from .trials import TrialEligibilityError, claim_trial, configure_trial, get_trial_policy, reset_trials, trial_status

logger = logging.getLogger("pablo-vpn.bot")
settings = get_settings()
router = Router(name="pablo-vpn")
dispatcher = Dispatcher()
dispatcher.include_router(router)
bot: Bot | None = None
_pending_restore: dict[int, bytes] = {}


def web_app_keyboard() -> InlineKeyboardMarkup:
    url = settings.effective_mini_app_url or "https://example.invalid"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛍 بازکردن فروشگاه PABLO VPN", web_app=WebAppInfo(url=url))],
            [InlineKeyboardButton(text="🎁 دریافت تست رایگان", callback_data="user:trial")],
            [
                InlineKeyboardButton(text="📦 سرویس‌های من", callback_data="user:services"),
                InlineKeyboardButton(text="🛟 پشتیبانی", callback_data="user:support"),
            ],
        ]
    )


def owner_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📊 آمار فروش", callback_data="owner:dashboard"),
                InlineKeyboardButton(text="🧾 سفارش‌ها", callback_data="owner:orders"),
            ],
            [
                InlineKeyboardButton(text="🖥 تست پنل‌ها", callback_data="owner:panels"),
                InlineKeyboardButton(text="🛍 پلن‌ها", callback_data="owner:plans"),
            ],
            [
                InlineKeyboardButton(text="🎁 تنظیمات تست رایگان", callback_data="owner:trial"),
                InlineKeyboardButton(text="🔐 دریافت بک‌آپ", callback_data="owner:backup"),
            ],
            [InlineKeyboardButton(text="⏳ وضعیت Railway", callback_data="owner:railway")],
        ]
    )


def order_actions(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ تأیید و ساخت سرویس", callback_data=f"order:approve:{order_id}"),
                InlineKeyboardButton(text="❌ رد رسید", callback_data=f"order:reject:{order_id}"),
            ]
        ]
    )


def identity_from_message(message: Message) -> TelegramIdentity:
    user = message.from_user
    if user is None:
        raise RuntimeError("Message has no Telegram user")
    return TelegramIdentity(
        id=user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        language_code=user.language_code,
    )


async def is_owner(telegram_id: int | None) -> bool:
    return bool(telegram_id and settings.owner_telegram_id and telegram_id == settings.owner_telegram_id)


async def deny_non_owner(message: Message) -> bool:
    if await is_owner(message.from_user.id if message.from_user else None):
        return False
    await message.answer("این دستور فقط برای Owner فعال است.")
    return True


def format_volume_mb(volume_mb: int) -> str:
    if volume_mb < 1024:
        return f"{volume_mb} MB"
    value = volume_mb / 1024
    return f"{value:g} GB"


@router.message(CommandStart())
async def start_handler(message: Message) -> None:
    identity = identity_from_message(message)
    async with session_scope() as session:
        user = await upsert_user(session, identity, settings.owner_telegram_id)
        trial = await trial_status(session, user)

    trial_line = (
        f"\n\n🎁 تست فعلی: <b>{trial['volumeMb']}MB / {trial['durationHours']} ساعت</b>" if trial["enabled"] else ""
    )
    text = (
        f"سلام <b>{html.escape(identity.first_name)}</b> 👋\n\n"
        "به <b>PABLO VPN</b> خوش اومدی. پلن را انتخاب کن و لینک اتصال را داخل همین ربات تحویل بگیر."
        f"{trial_line}"
    )
    await message.answer(text, reply_markup=web_app_keyboard())
    if await is_owner(identity.id):
        await message.answer("👑 حالت Owner برای این حساب فعال است.", reply_markup=owner_keyboard())


@router.message(Command("owner"))
async def owner_command_handler(message: Message) -> None:
    if await deny_non_owner(message):
        return
    await message.answer("مرکز مدیریت PABLO VPN آماده است:", reply_markup=owner_keyboard())


@router.message(Command("backup"))
async def backup_command_handler(message: Message) -> None:
    if await deny_non_owner(message):
        return
    if bot is None or settings.owner_telegram_id is None:
        await message.answer("ربات یا Owner ID تنظیم نشده است.")
        return
    try:
        rows, filename = await send_encrypted_backup(bot, settings.owner_telegram_id, "درخواست دستی Owner")
        await message.answer(f"✅ بک‌آپ ساخته شد: <code>{html.escape(filename)}</code> · {rows} رکورد")
    except BackupError as exc:
        await message.answer(f"❌ بک‌آپ ساخته نشد: {html.escape(str(exc))}")


@router.message(Command("restore"))
async def restore_command_handler(message: Message) -> None:
    if await deny_non_owner(message):
        return
    await message.answer(
        "فایل با پسوند <code>.pablobak</code> را همین‌جا ارسال کن. "
        "قبل از جایگزینی اطلاعات، تأیید دوباره می‌گیرم و یک بک‌آپ ایمنی هم می‌سازم."
    )


@router.message(Command("send"))
async def owner_send_command_handler(message: Message) -> None:
    if await deny_non_owner(message):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) != 3:
        await message.answer("فرمت پاسخ: <code>/send TELEGRAM_ID متن پیام</code>")
        return
    try:
        target_id = int(parts[1])
    except ValueError:
        await message.answer("Telegram ID معتبر نیست.")
        return
    if bot is None:
        await message.answer("ربات فعال نیست.")
        return
    try:
        await bot.send_message(target_id, f"💬 <b>پاسخ پشتیبانی PABLO VPN</b>\n\n{html.escape(parts[2])}")
        await message.answer(f"✅ پاسخ برای <code>{target_id}</code> ارسال شد.")
    except Exception:
        logger.exception("Could not send owner support reply to %s", target_id)
        await message.answer("❌ ارسال پاسخ انجام نشد؛ احتمالاً کاربر ربات را Block کرده است.")


@router.message(Command("railway"))
async def railway_command_handler(message: Message) -> None:
    if await deny_non_owner(message):
        return
    parts = (message.text or "").split()
    async with session_scope() as session:
        if len(parts) == 2:
            try:
                expiry = await set_railway_expiry(session, int(parts[1]))
            except ValueError as exc:
                await message.answer(str(exc))
                return
            await message.answer(
                f"✅ تاریخ هشدار Railway روی {expiry.strftime('%Y-%m-%d %H:%M UTC')} تنظیم شد. "
                f"{settings.backup_warning_days} روز قبل هشدار و بک‌آپ می‌فرستم."
            )
            return
        expiry = await get_railway_expiry(session)
    if expiry is None:
        await message.answer("تاریخ پایان تنظیم نشده. نمونه: <code>/railway 30</code>")
    else:
        remaining = max(0, (expiry - datetime.now(UTC)).days + 1)
        await message.answer(
            f"⏳ تاریخ پایان تنظیم‌شده: {expiry.strftime('%Y-%m-%d')} · حدود {remaining} روز باقی مانده"
        )


@router.message(Command("trial_status"))
async def trial_status_command(message: Message) -> None:
    if await deny_non_owner(message):
        return
    async with session_scope() as session:
        policy = await get_trial_policy(session)
    await message.answer(
        "🎁 <b>تنظیمات تست رایگان</b>\n"
        f"وضعیت: {'فعال' if policy.enabled else 'غیرفعال'}\n"
        f"حجم: {policy.volume_mb} MB\n"
        f"زمان: {policy.duration_hours} ساعت\n"
        f"سقف هر کاربر: {policy.max_claims} بار\n"
        f"بازه: {'همیشگی' if policy.window_days == 0 else str(policy.window_days) + ' روز'}\n"
        f"نسل ریست: {policy.generation}\n"
        f"پنل: {html.escape(policy.panel_id)}"
    )


@router.message(Command("trial_set"))
async def trial_set_command(message: Message) -> None:
    if await deny_non_owner(message):
        return
    parts = (message.text or "").split()
    if len(parts) not in {5, 6}:
        await message.answer(
            "فرمت: <code>/trial_set حجمMB ساعت تعداد بازه‌روز [panel_id]</code>\n"
            "مثال پیش‌فرض: <code>/trial_set 100 24 1 0 pg-ams-1</code>"
        )
        return
    try:
        volume_mb, duration_hours, max_claims, window_days = map(int, parts[1:5])
        async with session_scope() as session:
            policy = await configure_trial(
                session,
                actor_telegram_id=message.from_user.id,
                volume_mb=volume_mb,
                duration_hours=duration_hours,
                max_claims=max_claims,
                window_days=window_days,
                panel_id=parts[5] if len(parts) == 6 else None,
            )
        await message.answer(
            f"✅ تست رایگان تنظیم شد: {policy.volume_mb}MB / {policy.duration_hours} ساعت / "
            f"حداکثر {policy.max_claims} بار در "
            f"{'کل دوره' if policy.window_days == 0 else str(policy.window_days) + ' روز'}"
        )
    except ValueError as exc:
        await message.answer(f"❌ {html.escape(str(exc))}")


@router.message(Command("trial_reset"))
async def trial_reset_command(message: Message) -> None:
    if await deny_non_owner(message):
        return
    async with session_scope() as session:
        policy = await reset_trials(session, message.from_user.id)
    await message.answer(
        f"✅ سهمیهٔ تست همه کاربران ریست شد. نسل جدید: {policy.generation}. همه دوباره می‌توانند طبق سقف فعلی تست بگیرند."
    )


@router.message(Command("trial_on"))
async def trial_on_command(message: Message) -> None:
    if await deny_non_owner(message):
        return
    async with session_scope() as session:
        policy = await get_trial_policy(session, lock=True)
        policy.enabled = True
    await message.answer("✅ تست رایگان فعال شد.")


@router.message(Command("trial_off"))
async def trial_off_command(message: Message) -> None:
    if await deny_non_owner(message):
        return
    async with session_scope() as session:
        policy = await get_trial_policy(session, lock=True)
        policy.enabled = False
    await message.answer("⛔ تست رایگان غیرفعال شد.")


@router.callback_query(F.data == "user:trial")
async def user_trial_callback(callback: CallbackQuery) -> None:
    identity = TelegramIdentity(
        id=callback.from_user.id,
        first_name=callback.from_user.first_name,
        last_name=callback.from_user.last_name,
        username=callback.from_user.username,
        language_code=callback.from_user.language_code,
    )
    try:
        async with session_scope() as session:
            user = await upsert_user(session, identity, settings.owner_telegram_id)
            service, policy = await claim_trial(session, user, settings.owner_telegram_id or 0)
        text = (
            "🎉 <b>تست رایگان آماده شد</b>\n\n"
            f"حجم: {policy.volume_mb} MB\n"
            f"اعتبار: {policy.duration_hours} ساعت\n"
            f"پنل: {html.escape(service.panel_type)}\n\n"
            f"لینک اشتراک:\n<code>{html.escape(service.subscription_url)}</code>"
        )
        if callback.message:
            await callback.message.answer(text, reply_markup=web_app_keyboard())
        await callback.answer("تست رایگان ساخته شد")
    except (TrialEligibilityError, PanelAdapterError) as exc:
        await callback.answer(str(exc), show_alert=True)


@router.callback_query(F.data == "user:services")
async def user_services_callback(callback: CallbackQuery) -> None:
    async with session_scope() as session:
        user = await session.scalar(select(User).where(User.telegram_id == callback.from_user.id))
        services = (
            []
            if user is None
            else list(
                (
                    await session.scalars(select(Service).where(Service.user_id == user.id).order_by(Service.id.desc()))
                ).all()
            )
        )
    if not services:
        text = "هنوز سرویس فعالی نداری. از فروشگاه یک پلن انتخاب کن."
    else:
        rows = ["📦 <b>سرویس‌های فعال تو</b>"]
        for service in services[:10]:
            expires = service.expires_at.strftime("%Y-%m-%d")
            trial_badge = " · 🎁 تست" if service.is_trial else ""
            rows.append(
                f"\n• <b>{html.escape(service.username)}</b>{trial_badge}\n"
                f"{format_volume_mb(service.volume_mb)} · تا {expires}\n"
                f"<code>{html.escape(service.subscription_url)}</code>"
            )
        text = "\n".join(rows)
    if callback.message:
        await callback.message.answer(text, reply_markup=web_app_keyboard())
    await callback.answer()


@router.callback_query(F.data == "user:support")
async def user_support_callback(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.answer("🛟 پیام پشتیبانی را همین‌جا بفرست؛ مستقیم برای Owner ارسال می‌شود.")
    await callback.answer()


@router.message(F.photo)
async def receipt_photo_handler(message: Message) -> None:
    if message.from_user is None or not message.photo:
        return
    async with session_scope() as session:
        user = await session.scalar(select(User).where(User.telegram_id == message.from_user.id))
        if user is None:
            await message.answer("اول /start را بزن و از فروشگاه سفارش ثبت کن.")
            return
        order = await session.scalar(
            select(Order)
            .where(Order.user_id == user.id, Order.status == "awaiting_receipt")
            .order_by(Order.id.desc())
            .options(joinedload(Order.plan))
        )
        if order is None:
            await message.answer("سفارش منتظر رسیدی پیدا نشد. ابتدا از Mini App سفارش ثبت کن.")
            return
        order.receipt_file_id = message.photo[-1].file_id
        order.status = "pending"
        order_id = order.id
        plan_name = order.plan.name
        amount = order.amount_toman

    await message.answer(f"✅ رسید سفارش #{order_id} ثبت شد و برای Owner ارسال شد.")
    if bot and settings.owner_telegram_id:
        caption = (
            f"🧾 <b>رسید جدید #{order_id}</b>\n"
            f"کاربر: {html.escape(message.from_user.full_name)} · <code>{message.from_user.id}</code>\n"
            f"پلن: {html.escape(plan_name)}\n"
            f"مبلغ: {amount:,} تومان"
        )
        await bot.send_photo(
            chat_id=settings.owner_telegram_id,
            photo=message.photo[-1].file_id,
            caption=caption,
            reply_markup=order_actions(order_id),
        )


@router.message(F.text)
async def support_text_handler(message: Message) -> None:
    if message.from_user is None or not message.text or message.text.startswith("/"):
        return
    if await is_owner(message.from_user.id):
        return
    if bot is None or settings.owner_telegram_id is None:
        await message.answer("پشتیبانی موقتاً در دسترس نیست.")
        return
    identity = identity_from_message(message)
    async with session_scope() as session:
        await upsert_user(session, identity, settings.owner_telegram_id)
        session.add(
            AuditLog(
                actor_telegram_id=identity.id,
                action="support.forward",
                entity_type="user",
                entity_id=str(identity.id),
                details="Customer support message forwarded to Owner",
            )
        )
    username = f"@{identity.username}" if identity.username else "بدون username"
    await bot.send_message(
        settings.owner_telegram_id,
        "🛟 <b>پیام پشتیبانی جدید</b>\n"
        f"کاربر: {html.escape(message.from_user.full_name)} · {html.escape(username)}\n"
        f"Telegram ID: <code>{identity.id}</code>\n\n"
        f"{html.escape(message.text)}\n\n"
        f"پاسخ: <code>/send {identity.id} متن پاسخ</code>",
    )
    await message.answer("✅ پیامت برای Owner ارسال شد؛ پاسخ داخل همین ربات می‌آید.")


@router.message(F.document)
async def backup_document_handler(message: Message) -> None:
    if not await is_owner(message.from_user.id if message.from_user else None):
        return
    document = message.document
    if document is None or not (document.file_name or "").endswith(".pablobak"):
        return
    if document.file_size and document.file_size > 20 * 1024 * 1024:
        await message.answer("فایل بک‌آپ بزرگ‌تر از محدودیت ۲۰ مگابایت است.")
        return
    if bot is None or not settings.is_backup_configured:
        await message.answer("BACKUP_PASSPHRASE تنظیم نشده است.")
        return
    buffer = io.BytesIO()
    await bot.download(document, destination=buffer)
    data = buffer.getvalue()
    try:
        payload = decrypt_backup(data, settings.backup_passphrase.get_secret_value())
    except BackupError as exc:
        await message.answer(f"❌ فایل معتبر نیست: {html.escape(str(exc))}")
        return
    _pending_restore[message.from_user.id] = data
    total = sum(len(rows) for rows in payload["tables"].values())
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⚠️ تأیید بازیابی کامل", callback_data="backup:restore:confirm"),
                InlineKeyboardButton(text="انصراف", callback_data="backup:restore:cancel"),
            ]
        ]
    )
    await message.answer(
        f"فایل سالم و رمزگشایی شد. تاریخ: <code>{payload['created_at']}</code> · {total} رکورد.\n"
        "با تأیید، داده‌های فعلی کاملاً جایگزین می‌شوند.",
        reply_markup=keyboard,
    )


@router.callback_query(F.data == "backup:restore:cancel")
async def cancel_restore_callback(callback: CallbackQuery) -> None:
    _pending_restore.pop(callback.from_user.id, None)
    await callback.answer("بازیابی لغو شد")
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data == "backup:restore:confirm")
async def confirm_restore_callback(callback: CallbackQuery) -> None:
    if not await is_owner(callback.from_user.id):
        await callback.answer("دسترسی Owner نداری", show_alert=True)
        return
    data = _pending_restore.pop(callback.from_user.id, None)
    if data is None:
        await callback.answer("فایل منتظر بازیابی پیدا نشد", show_alert=True)
        return
    if bot is None or settings.owner_telegram_id is None or not settings.is_backup_configured:
        await callback.answer("تنظیمات بک‌آپ کامل نیست", show_alert=True)
        return
    try:
        await send_encrypted_backup(bot, settings.owner_telegram_id, "بک‌آپ ایمنی قبل از Restore")
        async with session_scope() as session:
            summary = await restore_backup(
                session,
                data,
                settings.backup_passphrase.get_secret_value(),
                callback.from_user.id,
            )
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.answer(f"✅ بازیابی کامل شد: {summary.total_rows} رکورد برگردانده شد.")
        await callback.answer("بازیابی موفق بود")
    except BackupError as exc:
        await callback.answer(str(exc), show_alert=True)


@router.callback_query(F.data.startswith("owner:"))
async def owner_menu_callback(callback: CallbackQuery) -> None:
    if not await is_owner(callback.from_user.id):
        await callback.answer("دسترسی Owner نداری.", show_alert=True)
        return
    action = (callback.data or "owner:dashboard").split(":", 1)[1]
    if action == "backup":
        if bot is None or settings.owner_telegram_id is None:
            await callback.answer("ربات تنظیم نشده", show_alert=True)
            return
        try:
            await send_encrypted_backup(bot, settings.owner_telegram_id, "درخواست از منوی Owner")
            await callback.answer("فایل بک‌آپ ارسال شد")
        except BackupError as exc:
            await callback.answer(str(exc), show_alert=True)
        return

    async with session_scope() as session:
        if action == "dashboard":
            pending = await session.scalar(select(func.count(Order.id)).where(Order.status == "pending")) or 0
            approved = await session.scalar(select(func.count(Order.id)).where(Order.status == "approved")) or 0
            active = await session.scalar(select(func.count(Service.id)).where(Service.status == "active")) or 0
            users = await session.scalar(select(func.count(User.id))) or 0
            text = (
                f"📊 <b>آمار PABLO VPN</b>\n\n⏳ در انتظار: {pending}\n✅ سفارش موفق: {approved}\n"
                f"📦 سرویس فعال: {active}\n👥 کاربران: {users}"
            )
            keyboard = owner_keyboard()
        elif action == "orders":
            orders = list(
                (
                    await session.scalars(
                        select(Order)
                        .order_by(Order.id.desc())
                        .limit(10)
                        .options(joinedload(Order.plan), joinedload(Order.user))
                    )
                )
                .unique()
                .all()
            )
            if not orders:
                text = "🧾 هنوز سفارشی ثبت نشده است."
            else:
                lines = ["🧾 <b>آخرین سفارش‌ها</b>"]
                for order in orders:
                    badge = "🎁" if order.is_trial else "#"
                    lines.append(
                        f"\n{badge}{order.id} · {html.escape(order.plan.name)} · {html.escape(order.status)}\n"
                        f"{html.escape(order.user.first_name)} · {order.amount_toman:,} تومان"
                    )
                text = "\n".join(lines)
            keyboard = owner_keyboard()
        elif action == "panels":
            panel_rows = list((await session.scalars(select(PanelInstance).order_by(PanelInstance.id))).all())
            lines = [f"🖥 <b>تست پایه پنل‌ها</b> · mode={settings.panel_mode}"]
            buttons = []
            for panel in panel_rows:
                report = await build_panel_client(panel.panel_type).test_connection(deep=False)
                panel.status = "mock" if report.mock else "online" if report.online else "offline"
                panel.last_tested_at = datetime.now(UTC)
                panel.last_latency_ms = report.latency_ms
                icon = "🟢" if report.online and not report.mock else "🟡" if report.mock else "🔴"
                lines.append(
                    f"\n{icon} {html.escape(panel.name)}\n"
                    f"configured={report.configured} · auth={report.authenticated} · {report.latency_ms or '-'}ms\n"
                    f"{html.escape(report.detail)}"
                )
                buttons.append(
                    [
                        InlineKeyboardButton(
                            text=f"تست کامل ساخت/حذف {panel.panel_type}", callback_data=f"panel:deep:{panel.panel_type}"
                        )
                    ]
                )
            buttons.append([InlineKeyboardButton(text="بازگشت", callback_data="owner:dashboard")])
            text = "\n".join(lines)
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        elif action == "plans":
            plan_rows = list(
                (
                    await session.scalars(
                        select(Plan)
                        .where(Plan.is_public.is_(True))
                        .order_by(Plan.price_toman)
                        .options(joinedload(Plan.panel))
                    )
                )
                .unique()
                .all()
            )
            lines = ["🛍 <b>پلن‌ها و مقصد ثابت</b>"]
            for plan in plan_rows:
                lines.append(
                    f"\n• {html.escape(plan.name)} · {format_volume_mb(plan.volume_mb)}/"
                    f"{plan.duration_hours // 24}روز\n↳ {html.escape(plan.panel.panel_type)}"
                )
            text = "\n".join(lines)
            keyboard = owner_keyboard()
        elif action == "trial":
            policy = await get_trial_policy(session)
            text = (
                "🎁 <b>تنظیمات تست رایگان</b>\n\n"
                f"{'🟢 فعال' if policy.enabled else '🔴 غیرفعال'}\n"
                f"{policy.volume_mb}MB · {policy.duration_hours} ساعت\n"
                f"هر کاربر {policy.max_claims} بار / "
                f"{'بدون بازه' if policy.window_days == 0 else str(policy.window_days) + ' روز'}\n"
                f"پنل: {html.escape(policy.panel_id)}\n\n"
                "تنظیم: <code>/trial_set 100 24 1 0 pg-ams-1</code>\n"
                "ریست همه: /trial_reset · روشن: /trial_on · خاموش: /trial_off"
            )
            keyboard = owner_keyboard()
        elif action == "railway":
            expiry = await get_railway_expiry(session)
            text = (
                "⏳ تاریخ پایان تنظیم نشده. دستور <code>/railway 30</code> را بزن."
                if expiry is None
                else f"⏳ پایان تنظیم‌شده: {expiry.strftime('%Y-%m-%d')}\nدستور تغییر: <code>/railway 30</code>"
            )
            keyboard = owner_keyboard()
        else:
            text = "دستور شناخته نشد."
            keyboard = owner_keyboard()
    if callback.message:
        await callback.message.answer(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("panel:deep:"))
async def deep_panel_test_callback(callback: CallbackQuery) -> None:
    if not await is_owner(callback.from_user.id):
        await callback.answer("دسترسی Owner نداری", show_alert=True)
        return
    panel_type = (callback.data or "").split(":", 2)[2]
    await callback.answer("تست ساخت، خواندن و حذف شروع شد")
    report = await build_panel_client(panel_type).test_connection(deep=True)
    async with session_scope() as session:
        panel_rows = list(
            (await session.scalars(select(PanelInstance).where(PanelInstance.panel_type == panel_type))).all()
        )
        for panel in panel_rows:
            panel.status = "mock" if report.mock else "online" if report.online else "offline"
            panel.last_tested_at = datetime.now(UTC)
            panel.last_latency_ms = report.latency_ms
            session.add(
                AuditLog(
                    actor_telegram_id=callback.from_user.id,
                    action="panel.deep_test",
                    entity_type="panel",
                    entity_id=panel.id,
                    details=(
                        f"mode={'mock' if report.mock else 'real'}; create={report.create_ok}; "
                        f"read={report.read_ok}; delete={report.delete_ok}; online={report.online}"
                    ),
                )
            )
    icon = "✅" if report.online and not report.mock else "⚠️" if report.mock else "❌"
    text = (
        f"{icon} <b>نتیجه تست کامل {html.escape(panel_type)}</b>\n"
        f"mode: {'mock' if report.mock else 'real'}\n"
        f"ساخت: {report.create_ok} · خواندن: {report.read_ok} · حذف: {report.delete_ok}\n"
        f"زمان: {report.latency_ms or '-'}ms\n{html.escape(report.detail)}"
    )
    if callback.message:
        await callback.message.answer(text, reply_markup=owner_keyboard())


@router.callback_query(F.data.startswith("order:approve:"))
async def approve_order_callback(callback: CallbackQuery) -> None:
    if not await is_owner(callback.from_user.id):
        await callback.answer("دسترسی Owner نداری.", show_alert=True)
        return
    order_id = int((callback.data or "").rsplit(":", 1)[1])
    try:
        async with session_scope() as session:
            service = await provision_order(session, order_id, callback.from_user.id)
            user_telegram_id = service.user.telegram_id
            subscription_url = service.subscription_url
            username = service.username
    except PanelAdapterError as exc:
        await callback.answer(str(exc), show_alert=True)
        return

    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            f"✅ سفارش #{order_id} تأیید شد و کاربر <code>{html.escape(username)}</code> ساخته شد."
        )
    if bot:
        await bot.send_message(
            user_telegram_id,
            f"🎉 سرویس سفارش #{order_id} آماده شد.\n\nلینک اشتراک:\n<code>{html.escape(subscription_url)}</code>",
            reply_markup=web_app_keyboard(),
        )
    await callback.answer("سرویس ساخته شد")


@router.callback_query(F.data.startswith("order:reject:"))
async def reject_order_callback(callback: CallbackQuery) -> None:
    if not await is_owner(callback.from_user.id):
        await callback.answer("دسترسی Owner نداری.", show_alert=True)
        return
    order_id = int((callback.data or "").rsplit(":", 1)[1])
    async with session_scope() as session:
        order = await session.scalar(select(Order).where(Order.id == order_id).options(joinedload(Order.user)))
        if order is None or order.status != "pending":
            await callback.answer("این سفارش قابل رد نیست.", show_alert=True)
            return
        order.status = "rejected"
        order.rejection_reason = "رسید توسط Owner تأیید نشد"
        user_telegram_id = order.user.telegram_id
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(f"❌ سفارش #{order_id} رد شد.")
    if bot:
        await bot.send_message(user_telegram_id, f"رسید سفارش #{order_id} تأیید نشد. لطفاً سفارش جدید ثبت کن.")
    await callback.answer("سفارش رد شد")


async def notify_order_created(telegram_id: int, order_id: int, plan_name: str, amount_toman: int) -> None:
    if bot is None:
        return
    await bot.send_message(
        telegram_id,
        f"🧾 سفارش #{order_id} برای پلن <b>{html.escape(plan_name)}</b> ثبت شد.\n"
        f"مبلغ: <b>{amount_toman:,} تومان</b>\n\n"
        "حالا تصویر رسید پرداخت را همین‌جا برای ربات ارسال کن.",
    )


async def setup_bot() -> None:
    global bot
    if not settings.is_bot_configured:
        return
    bot = Bot(token=settings.bot_token.get_secret_value(), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="شروع و بازکردن فروشگاه"),
            BotCommand(command="owner", description="مرکز مدیریت Owner"),
            BotCommand(command="backup", description="دریافت بک‌آپ رمزگذاری‌شده"),
            BotCommand(command="restore", description="راهنمای بازیابی بک‌آپ"),
            BotCommand(command="railway", description="تنظیم روزهای باقی‌مانده Railway"),
            BotCommand(command="trial_status", description="وضعیت تست رایگان"),
        ]
    )
    mini_app_url = settings.effective_mini_app_url
    if mini_app_url:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="فروشگاه", web_app=WebAppInfo(url=mini_app_url))
        )
    if settings.public_url and settings.telegram_webhook_secret:
        await bot.set_webhook(
            url=f"{settings.public_url}/telegram/webhook",
            secret_token=settings.telegram_webhook_secret.get_secret_value(),
            allowed_updates=dispatcher.resolve_used_update_types(),
            drop_pending_updates=False,
        )


async def shutdown_bot() -> None:
    global bot
    if bot is not None:
        await bot.session.close()
        bot = None
