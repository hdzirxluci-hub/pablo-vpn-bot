from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from .config import get_settings
from .models import AuditLog, Order, Plan, Service
from .panel_clients import PanelConnectionError, build_panel_client


class PanelAdapterError(RuntimeError):
    pass


async def provision_order(session: AsyncSession, order_id: int, owner_telegram_id: int) -> Service:
    query = (
        select(Order)
        .where(Order.id == order_id)
        .options(
            joinedload(Order.plan).joinedload(Plan.panel),
            joinedload(Order.panel),
            joinedload(Order.user),
            joinedload(Order.service).joinedload(Service.user),
        )
        .with_for_update()
    )
    order = (await session.execute(query)).unique().scalar_one_or_none()
    if order is None:
        raise PanelAdapterError("Order not found")
    if order.service is not None:
        return order.service
    if order.status != "pending":
        raise PanelAdapterError(f"Order cannot be provisioned from status: {order.status}")

    panel = order.panel
    try:
        adapter = build_panel_client(panel.panel_type, get_settings())
        expires_at = datetime.now(UTC) + timedelta(hours=order.duration_hours)
        username = f"tg_{order.user.telegram_id}_{order.id}"
        account = await adapter.create_user(
            username=username,
            volume_mb=order.volume_mb,
            expires_at=expires_at,
            inbound_id=order.inbound_id,
        )
    except PanelConnectionError as exc:
        raise PanelAdapterError(str(exc)) from exc

    service = Service(
        user=order.user,
        order=order,
        plan_id=order.plan_id,
        panel_id=panel.id,
        panel_type=panel.panel_type,
        external_id=account.external_id,
        username=account.username,
        subscription_url=account.subscription_url,
        volume_mb=order.volume_mb,
        used_mb=0,
        expires_at=expires_at,
        status="active",
        is_trial=order.is_trial,
    )
    order.status = "approved"
    order.approved_by_telegram_id = owner_telegram_id
    order.approved_at = datetime.now(UTC)
    session.add(service)
    session.add(
        AuditLog(
            actor_telegram_id=owner_telegram_id,
            action="trial.provision" if order.is_trial else "order.approve_and_provision",
            entity_type="order",
            entity_id=str(order.id),
            details=f"adapter={panel.panel_type}; mode={get_settings().panel_mode}; plan={order.plan_id}",
        )
    )
    await session.flush()
    return service
