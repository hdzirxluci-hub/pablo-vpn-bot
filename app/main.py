from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from aiogram.types import Update
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from . import telegram_bot
from .auth import current_identity
from .backups import BackupError, create_backup
from .config import get_settings
from .database import close_database, get_session, init_database, session_scope, upsert_user
from .lifecycle import initialize_lifecycle_settings, start_scheduler, stop_scheduler
from .models import AuditLog, Order, PanelInstance, Plan, Service, User
from .panel_clients import build_panel_client
from .panels import PanelAdapterError, provision_order
from .schemas import (
    APIMessage,
    HealthResponse,
    OrderCreate,
    RejectOrderRequest,
    TelegramIdentity,
    TrialConfigureRequest,
)
from .trials import TrialEligibilityError, claim_trial, configure_trial, reset_trials, trial_status

logger = logging.getLogger("pablo-vpn")
settings = get_settings()
STATIC_DIR = Path(__file__).parent / "frontend"


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_database()
    async with session_scope() as session:
        await initialize_lifecycle_settings(session, settings)
    await telegram_bot.setup_bot()
    start_scheduler(telegram_bot.bot)
    try:
        yield
    finally:
        await stop_scheduler()
        await telegram_bot.shutdown_bot()
        await close_database()


app = FastAPI(
    title="PABLO VPN API",
    version="0.2.0",
    docs_url="/api/docs" if settings.environment != "production" else None,
    redoc_url=None,
    lifespan=lifespan,
)


async def current_user(
    identity: TelegramIdentity = Depends(current_identity),
    session: AsyncSession = Depends(get_session),
) -> User:
    user = await upsert_user(session, identity, settings.owner_telegram_id)
    await session.commit()
    return user


def plan_json(plan: Plan) -> dict[str, object]:
    return {
        "id": plan.id,
        "mark": plan.mark,
        "name": plan.name,
        "subtitle": plan.subtitle,
        "volume": round(plan.volume_mb / 1024, 3),
        "volumeMb": plan.volume_mb,
        "days": round(plan.duration_hours / 24, 2),
        "durationHours": plan.duration_hours,
        "price": plan.price_toman,
        "category": plan.category,
        "tag": plan.tag,
        "featured": plan.featured,
    }


def order_json(order: Order) -> dict[str, object]:
    return {
        "id": order.id,
        "planId": order.plan_id,
        "status": order.status,
        "receipt": "telegram-photo" if order.receipt_file_id else None,
        "amount": order.amount_toman,
        "volumeMb": order.volume_mb,
        "durationHours": order.duration_hours,
        "isTrial": order.is_trial,
        "createdAt": order.created_at.isoformat() if order.created_at else None,
    }


def service_json(service: Service) -> dict[str, object]:
    expires_at = service.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    seconds = max(0, (expires_at - datetime.now(UTC)).total_seconds())
    hours = max(0, int((seconds + 3599) // 3600))
    return {
        "id": f"svc-{service.id}",
        "planId": service.plan_id,
        "name": "تست رایگان" if service.is_trial else f"سرویس {service.username}",
        "username": service.username,
        "panelId": service.panel_id,
        "panel": service.panel_type,
        "used": round(service.used_mb / 1024, 3),
        "usedMb": service.used_mb,
        "total": round(service.volume_mb / 1024, 3),
        "totalMb": service.volume_mb,
        "days": max(1, int((hours + 23) // 24)) if hours else 0,
        "hoursRemaining": hours,
        "status": service.status,
        "isTrial": service.is_trial,
        "sub": service.subscription_url,
        "createdAt": service.created_at.isoformat() if service.created_at else None,
    }


@app.get("/health", response_model=HealthResponse)
async def health(session: AsyncSession = Depends(get_session)) -> HealthResponse:
    await session.execute(text("SELECT 1"))
    return HealthResponse(
        status="ok",
        database="ok",
        bot_configured=settings.is_bot_configured,
        bot_username=settings.bot_username,
        demo_mode=settings.demo_mode,
        panel_mode=settings.panel_mode,
        backup_configured=settings.is_backup_configured,
    )


@app.get("/api/config")
async def public_config() -> dict[str, object]:
    return {
        "appName": settings.app_name,
        "botUsername": settings.bot_username,
        "demoMode": settings.demo_mode,
        "panelMode": settings.panel_mode,
        "telegramAuthRequired": not settings.demo_mode,
    }


@app.get("/api/bootstrap")
async def bootstrap(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    plans = list(
        (
            await session.scalars(
                select(Plan).where(Plan.is_active.is_(True), Plan.is_public.is_(True)).order_by(Plan.price_toman)
            )
        ).all()
    )
    orders = list(
        (await session.scalars(select(Order).where(Order.user_id == user.id).order_by(Order.id.desc()))).all()
    )
    services = list(
        (await session.scalars(select(Service).where(Service.user_id == user.id).order_by(Service.id.desc()))).all()
    )
    trial = await trial_status(session, user)
    return {
        "user": {
            "id": user.telegram_id,
            "firstName": user.first_name,
            "lastName": user.last_name,
            "username": user.username,
            "isOwner": user.is_owner,
        },
        "plans": [plan_json(plan) for plan in plans],
        "orders": [order_json(order) for order in orders],
        "services": [service_json(service) for service in services],
        "trial": trial,
        "botUsername": settings.bot_username,
        "demoMode": settings.demo_mode,
    }


@app.post("/api/orders", status_code=status.HTTP_201_CREATED)
async def create_order(
    payload: OrderCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    plan = await session.scalar(
        select(Plan).where(Plan.id == payload.plan_id, Plan.is_active.is_(True), Plan.is_public.is_(True))
    )
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")

    existing = await session.scalar(
        select(Order)
        .where(
            Order.user_id == user.id,
            Order.plan_id == plan.id,
            Order.status.in_(["awaiting_receipt", "pending"]),
        )
        .order_by(Order.id.desc())
    )
    if existing is not None:
        return order_json(existing)

    order = Order(
        user_id=user.id,
        plan_id=plan.id,
        panel_id=plan.panel_id,
        inbound_id=plan.inbound_id,
        amount_toman=plan.price_toman,
        volume_mb=plan.volume_mb,
        duration_hours=plan.duration_hours,
        status="awaiting_receipt",
        is_trial=False,
    )
    session.add(order)
    await session.flush()
    session.add(
        AuditLog(
            actor_telegram_id=user.telegram_id,
            action="order.create",
            entity_type="order",
            entity_id=str(order.id),
            details=f"plan={plan.id}; volume_mb={plan.volume_mb}; hours={plan.duration_hours}",
        )
    )
    await session.commit()
    await session.refresh(order)

    try:
        await telegram_bot.notify_order_created(user.telegram_id, order.id, plan.name, plan.price_toman)
    except Exception:
        logger.exception("Could not notify Telegram user about order %s", order.id)
    return order_json(order)


@app.post("/api/trial", status_code=status.HTTP_201_CREATED)
async def create_free_trial(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    try:
        service, policy = await claim_trial(session, user, settings.owner_telegram_id or 0)
        await session.commit()
        await session.refresh(service)
        return {"service": service_json(service), "policy": await trial_status(session, user)}
    except (TrialEligibilityError, PanelAdapterError) as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@app.post("/api/demo/orders/{order_id}/receipt")
async def demo_receipt(
    order_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    if not settings.demo_mode:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    order = await session.scalar(select(Order).where(Order.id == order_id, Order.user_id == user.id))
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    if order.status == "awaiting_receipt":
        order.status = "pending"
        order.receipt_file_id = "demo-telegram-photo-file-id"
        await session.commit()
        await session.refresh(order)
    return order_json(order)


async def require_owner(user: User) -> None:
    if not user.is_owner:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Owner access required")


@app.get("/api/owner/orders")
async def owner_orders(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, object]]:
    await require_owner(user)
    orders = list(
        (
            await session.scalars(
                select(Order).order_by(Order.id.desc()).options(joinedload(Order.plan), joinedload(Order.user))
            )
        )
        .unique()
        .all()
    )
    return [
        {
            **order_json(order),
            "planName": order.plan.name,
            "customerName": order.user.first_name,
            "customerTelegramId": order.user.telegram_id,
        }
        for order in orders
    ]


@app.post("/api/owner/orders/{order_id}/approve")
async def owner_approve_order(
    order_id: int,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    await require_owner(user)
    try:
        service = await provision_order(session, order_id, user.telegram_id)
        await session.commit()
        await session.refresh(service)
        return service_json(service)
    except PanelAdapterError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@app.post("/api/owner/orders/{order_id}/reject", response_model=APIMessage)
async def owner_reject_order(
    order_id: int,
    payload: RejectOrderRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> APIMessage:
    await require_owner(user)
    order = await session.scalar(select(Order).where(Order.id == order_id))
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    if order.status != "pending":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Order is not pending")
    order.status = "rejected"
    order.rejection_reason = payload.reason
    session.add(
        AuditLog(
            actor_telegram_id=user.telegram_id,
            action="order.reject",
            entity_type="order",
            entity_id=str(order.id),
            details=payload.reason,
        )
    )
    await session.commit()
    return APIMessage(message="Order rejected")


@app.get("/api/owner/panels/health")
async def owner_panel_health(
    deep: bool = Query(default=False),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, object]]:
    await require_owner(user)
    panel_rows = list((await session.scalars(select(PanelInstance).order_by(PanelInstance.id))).all())
    results = []
    for panel in panel_rows:
        report = await build_panel_client(panel.panel_type).test_connection(deep=deep)
        panel.status = "mock" if report.mock else "online" if report.online else "offline"
        panel.last_tested_at = datetime.now(UTC)
        panel.last_latency_ms = report.latency_ms
        if deep:
            session.add(
                AuditLog(
                    actor_telegram_id=user.telegram_id,
                    action="panel.deep_test",
                    entity_type="panel",
                    entity_id=panel.id,
                    details=(
                        f"mode={'mock' if report.mock else 'real'}; create={report.create_ok}; "
                        f"read={report.read_ok}; delete={report.delete_ok}; online={report.online}"
                    ),
                )
            )
        results.append({"panelId": panel.id, "name": panel.name, **report.to_dict()})
    await session.commit()
    return results


@app.post("/api/owner/trial/configure")
async def owner_configure_trial(
    payload: TrialConfigureRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    await require_owner(user)
    try:
        policy = await configure_trial(
            session,
            actor_telegram_id=user.telegram_id,
            volume_mb=payload.volume_mb,
            duration_hours=payload.duration_hours,
            max_claims=payload.max_claims,
            window_days=payload.window_days,
            panel_id=payload.panel_id,
        )
        await session.commit()
        return {
            "enabled": policy.enabled,
            "volumeMb": policy.volume_mb,
            "durationHours": policy.duration_hours,
            "maxClaims": policy.max_claims,
            "windowDays": policy.window_days,
            "generation": policy.generation,
            "panelId": policy.panel_id,
        }
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@app.post("/api/owner/trial/reset")
async def owner_reset_trials(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    await require_owner(user)
    policy = await reset_trials(session, user.telegram_id)
    await session.commit()
    return {"ok": True, "generation": policy.generation}


@app.get("/api/owner/backup")
async def owner_download_backup(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await require_owner(user)
    if not settings.is_backup_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Backup passphrase is not configured"
        )
    try:
        data, _ = await create_backup(session, settings.backup_passphrase.get_secret_value())
    except BackupError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    filename = f"pablo-vpn-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.pablobak"
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, bool]:
    if telegram_bot.bot is None or not settings.telegram_webhook_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Telegram bot is not configured")
    expected = settings.telegram_webhook_secret.get_secret_value()
    if not x_telegram_bot_api_secret_token or not hmac.compare_digest(x_telegram_bot_api_secret_token, expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid webhook secret")
    payload = await request.json()
    update = Update.model_validate(payload, context={"bot": telegram_bot.bot})
    await telegram_bot.dispatcher.feed_update(telegram_bot.bot, update)
    return {"ok": True}


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
