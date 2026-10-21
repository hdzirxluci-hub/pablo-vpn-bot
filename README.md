# PABLO VPN — Telegram Bot + Mini App

پروژهٔ مستقل `PABLO VPN` برای ربات رسمی **@PablloVPN3_bot**.

## امکانات فعلی

- Mini App موبایل‌محور برای مشاهدهٔ پلن‌ها، ثبت سفارش و دیدن سرویس‌ها
- احراز هویت واقعی Telegram Mini App با بررسی امضای `initData`
- پرداخت نسخهٔ اول با رسید: سفارش → ارسال عکس رسید در ربات → تأیید Owner → ساخت idempotent سرویس
- مقصد ثابت هر پلن روی PasarGuard، 3x-ui یا Marzban
- تست رایگان پیش‌فرض **100MB / 24 ساعت**، بدون پرداخت و با ساخت خودکار سرویس
- تنظیم حجم، مدت، سقف هر کاربر، بازهٔ مجاز و پنل Trial توسط Owner
- Reset عمومی Trial برای مجازشدن دوبارهٔ همه کاربران
- Adapter HTTP واقعی برای PasarGuard، 3x-ui و Marzban
- تست عمیق پنل: Login → Create کاربر موقت 1MB → Read-back → Delete
- بک‌آپ کامل و رمزگذاری‌شدهٔ داده‌های کسب‌وکار با AES-256-GCM و Scrypt
- Restore دومرحله‌ای از داخل ربات، همراه بک‌آپ ایمنی پیش از جایگزینی
- بک‌آپ خودکار به Telegram Owner؛ فایل خارج از فضای Railway باقی می‌ماند
- هشدار پایان دورهٔ Railway و ارسال بک‌آپ در بازهٔ هشدار (پیش‌فرض سه روز قبل)

> اتصال واقعی هیچ پنلی فقط با وجود کد یا health check تأیید نمی‌شود. وضعیت production زمانی معتبر است که Owner تست عمیق را با URL و Credential واقعی اجرا کند و هر چهار مرحله موفق باشند.

## ساختار

```text
app/main.py             FastAPI، Mini App API و webhook
app/telegram_bot.py     جریان مشتری و مدیریت Owner داخل ربات
app/panel_clients.py    HTTP adapters و deep test سه پنل
app/panels.py           provisioning idempotent
app/trials.py           policy، eligibility، claim و reset تست رایگان
app/backups.py          export، encryption، validation و restore کامل
app/lifecycle.py        backup خودکار و هشدار Railway
app/models.py           مدل‌های دیتابیس
app/frontend/           Telegram Mini App / PWA
tests/                  API، auth، trial، backup و adapter integration tests
```

## اجرای محلی

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

پیش‌نمایش محلی با `DEMO_MODE=true` و `PANEL_MODE=mock` کار می‌کند. برچسب Mock در گزارش پنل صریح است و به‌معنی اتصال واقعی نیست.

## متغیرهای Railway

همهٔ Secretها فقط در **Railway → Service → Variables** وارد شوند؛ آن‌ها را در فایل، Git یا پیام عمومی ذخیره نکنید.

### پایه و Telegram

```env
ENVIRONMENT=production
DEMO_MODE=false
PUBLIC_URL=https://YOUR-SERVICE.up.railway.app
MINI_APP_URL=https://YOUR-SERVICE.up.railway.app
DATABASE_URL=${{Postgres.DATABASE_URL}}
BOT_USERNAME=PablloVPN3_bot
BOT_TOKEN=...
OWNER_TELEGRAM_ID=...
TELEGRAM_WEBHOOK_SECRET=یک-رشته-تصادفی-طولانی
```

### Backup و هشدار

```env
BACKUP_PASSPHRASE=یک-عبارت-تصادفی-طولانی-و-ثابت
AUTO_BACKUP_ENABLED=true
AUTO_BACKUP_INTERVAL_HOURS=24
BACKUP_WARNING_DAYS=3
```

`BACKUP_PASSPHRASE` را در جای امن نگه دارید. بدون همان عبارت، فایل `.pablobak` عمداً قابل بازیابی نیست. Tokenها، Passwordها و `DATABASE_URL` داخل Backup ذخیره نمی‌شوند و پس از Restore باید همچنان در Railway Variables موجود باشند.

### اتصال واقعی پنل‌ها

```env
PANEL_MODE=real
PANEL_TIMEOUT_SECONDS=15
PANEL_VERIFY_TLS=true

PASARGUARD_URL=https://...
PASARGUARD_USERNAME=...
PASARGUARD_PASSWORD=...
PASARGUARD_GROUP_ID=...

XUI_URL=https://...
XUI_API_TOKEN=...
# اگر Token ندارید، به‌جای آن XUI_USERNAME و XUI_PASSWORD را تنظیم کنید.
XUI_INBOUND_ID=...
XUI_SUBSCRIPTION_BASE_URL=https://.../sub

MARZBAN_URL=https://...
MARZBAN_USERNAME=...
MARZBAN_PASSWORD=...
MARZBAN_PROXIES_JSON={"vless":{}}
MARZBAN_INBOUNDS_JSON={}
```

در production، برنامه عمداً با SQLite، Demo، Mock panel، Backup خاموش یا Credential ناقص بالا نمی‌آید.

## مراحل Railway با موبایل

1. یک پروژهٔ جدید Railway بسازید و این کد را به‌عنوان Service مستقل اضافه کنید.
2. PostgreSQL را به همان پروژه اضافه کنید.
3. Variables بالا را وارد کنید؛ ابتدا URL عمومی Railway را بگیرید و سپس `PUBLIC_URL` و `MINI_APP_URL` را تکمیل کنید.
4. Deploy کنید و `/health` را بررسی کنید.
5. در Telegram دستور `/start` را برای @PablloVPN3_bot بزنید.
6. از منوی Owner، «تست پنل‌ها» را باز و برای هر پنل تست کامل ساخت/خواندن/حذف را اجرا کنید.
7. فقط نتیجه‌ای که `mode=real` و Create/Read/Delete همگی `True` دارد اتصال واقعی تأییدشده است.

`Dockerfile` و `railway.toml` آماده‌اند. برنامه روی `${PORT}` و `0.0.0.0` اجرا می‌شود.

## دستورهای Owner

```text
/owner
/backup
/restore
/railway 30
/send TELEGRAM_ID متن پاسخ
/trial_status
/trial_set 100 24 1 0 pg-ams-1
/trial_reset
/trial_on
/trial_off
```

فرمت `/trial_set`:

```text
/trial_set حجمMB مدت‌ساعت سقف‌تعداد بازه‌روز [panel_id]
```

`بازه‌روز=0` یعنی سقف در کل نسل فعلی اعمال شود. `/trial_reset` شمارهٔ نسل را افزایش می‌دهد و بدون پاک‌کردن Audit یا تاریخچه، همه کاربران را دوباره مجاز می‌کند.

## Backup / Restore

- `/backup` یک فایل رمزگذاری‌شدهٔ `.pablobak` به Owner می‌فرستد.
- فایل شامل کاربران، پنل‌ها، پلن‌ها، سفارش‌ها، سرویس‌ها، Trial policy/claims، تنظیمات Railway و Auditها است.
- برای Restore، `/restore` را بزنید و فایل را برای ربات ارسال کنید.
- ربات ابتدا رمز و integrity فایل را بررسی می‌کند، خلاصه را نشان می‌دهد و تأیید دوم می‌گیرد.
- پیش از جایگزینی دیتابیس، یک Safety Backup تازه به Owner ارسال می‌شود.
- وضعیت هشدار Railway و زمان آخرین بک‌آپ نیز در جدول تنظیمات و در Backup حفظ می‌شود.

## تست توسعه

```bash
python -m ruff check app tests
python -m ruff format --check app tests
python -m pytest -q
python -m playwright install chromium
python tests/browser_audit.py
```

تست‌ها شامل امضای Telegram، جریان کامل سفارش، Trial و Reset، round-trip واقعی Backup/Restore، الزام‌های production و HTTP MockTransport برای Create/Read/Delete هر سه Adapter هستند.

## نکات عملیاتی

- پلن Trial عمومی نیست و پیش‌فرض روی `pg-ams-1` قرار دارد.
- پلن شروع/هوشمند → PasarGuard، حرفه‌ای/اولترا → 3x-ui، مکس → Marzban.
- سفارش حجم، زمان، مبلغ، پنل و inbound را snapshot می‌کند تا تغییر بعدی پلن روی سفارش قبلی اثر نگذارد.
- provisioning مجدد سفارش تأییدشده همان سرویس قبلی را برمی‌گرداند و کاربر تکراری نمی‌سازد.
- این مخزن هیچ Token، Password یا URL خصوصی واقعی ندارد.
