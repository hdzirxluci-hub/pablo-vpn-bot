from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .config import get_settings
from .models import Base, PanelInstance, Plan, TrialPolicy, User
from .schemas import TelegramIdentity


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://") and not url.startswith("postgresql+asyncpg://"):
        return "postgresql+asyncpg://" + url.removeprefix("postgresql://")
    return url


settings = get_settings()
engine: AsyncEngine = create_async_engine(normalize_database_url(settings.database_url), pool_pre_ping=True, echo=False)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


PANEL_SEEDS = [
    {
        "id": "pg-ams-1",
        "panel_type": "PasarGuard",
        "name": "PasarGuard · Primary",
        "base_url": "https://pg.example.invalid",
    },
    {
        "id": "xui-de-1",
        "panel_type": "3x-ui",
        "name": "3x-ui · Secondary",
        "base_url": "https://xui.example.invalid",
    },
    {
        "id": "mrz-fin-1",
        "panel_type": "Marzban",
        "name": "Marzban · Backup",
        "base_url": "https://marzban.example.invalid",
    },
]

PLAN_SEEDS = [
    {
        "id": "start15",
        "mark": "S",
        "name": "شروع",
        "subtitle": "برای مصرف سبک و تست",
        "volume_mb": 15 * 1024,
        "duration_hours": 15 * 24,
        "price_toman": 89000,
        "category": "short",
        "panel_id": "pg-ams-1",
        "featured": False,
        "tag": "اقتصادی",
    },
    {
        "id": "smart40",
        "mark": "P",
        "name": "هوشمند",
        "subtitle": "انتخاب محبوب کاربران",
        "volume_mb": 40 * 1024,
        "duration_hours": 30 * 24,
        "price_toman": 179000,
        "category": "monthly",
        "panel_id": "pg-ams-1",
        "featured": True,
        "tag": "پرفروش",
    },
    {
        "id": "pro80",
        "mark": "PRO",
        "name": "حرفه‌ای",
        "subtitle": "برای استفاده روزانه سنگین",
        "volume_mb": 80 * 1024,
        "duration_hours": 30 * 24,
        "price_toman": 289000,
        "category": "monthly",
        "panel_id": "xui-de-1",
        "featured": False,
        "tag": "سریع",
    },
    {
        "id": "max150",
        "mark": "M",
        "name": "مکس",
        "subtitle": "دو ماه بدون نگرانی",
        "volume_mb": 150 * 1024,
        "duration_hours": 60 * 24,
        "price_toman": 499000,
        "category": "heavy",
        "panel_id": "mrz-fin-1",
        "featured": False,
        "tag": "پرحجم",
    },
    {
        "id": "ultra300",
        "mark": "U",
        "name": "اولترا",
        "subtitle": "برای چند دستگاه و مصرف بالا",
        "volume_mb": 300 * 1024,
        "duration_hours": 90 * 24,
        "price_toman": 799000,
        "category": "heavy",
        "panel_id": "xui-de-1",
        "featured": False,
        "tag": "ویژه",
    },
    {
        "id": "free-trial",
        "mark": "T",
        "name": "تست رایگان",
        "subtitle": "پیش‌نمایش کیفیت سرویس",
        "volume_mb": 100,
        "duration_hours": 24,
        "price_toman": 0,
        "category": "trial",
        "panel_id": "pg-ams-1",
        "featured": False,
        "tag": "رایگان",
        "is_public": False,
    },
]


async def init_database() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with session_scope() as session:
        existing_panel = await session.scalar(select(PanelInstance.id).limit(1))
        if existing_panel is None:
            session.add_all([PanelInstance(**data) for data in PANEL_SEEDS])
            await session.flush()

        existing_plan = await session.scalar(select(Plan.id).limit(1))
        if existing_plan is None:
            session.add_all([Plan(**data) for data in PLAN_SEEDS])
            await session.flush()

        policy = await session.get(TrialPolicy, 1)
        if policy is None:
            session.add(
                TrialPolicy(
                    id=1,
                    enabled=True,
                    volume_mb=100,
                    duration_hours=24,
                    max_claims=1,
                    window_days=0,
                    generation=1,
                    panel_id="pg-ams-1",
                )
            )


async def close_database() -> None:
    await engine.dispose()


async def upsert_user(session: AsyncSession, identity: TelegramIdentity, owner_telegram_id: int | None) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == identity.id))
    is_owner = bool(owner_telegram_id and identity.id == owner_telegram_id)
    if user is None:
        user = User(
            telegram_id=identity.id,
            username=identity.username,
            first_name=identity.first_name,
            last_name=identity.last_name,
            language_code=identity.language_code,
            is_owner=is_owner,
        )
        session.add(user)
        await session.flush()
        return user

    user.username = identity.username
    user.first_name = identity.first_name
    user.last_name = identity.last_name
    user.language_code = identity.language_code
    user.is_owner = is_owner
    await session.flush()
    return user
