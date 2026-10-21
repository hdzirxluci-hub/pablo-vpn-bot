from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str] = mapped_column(String(128), default="کاربر")
    last_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    language_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    orders: Mapped[list[Order]] = relationship(back_populates="user")
    services: Mapped[list[Service]] = relationship(back_populates="user")
    trial_claims: Mapped[list[TrialClaim]] = relationship(back_populates="user")


class PanelInstance(Base):
    __tablename__ = "panel_instances"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    panel_type: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(128))
    base_url: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    config_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    plans: Mapped[list[Plan]] = relationship(back_populates="panel")
    orders: Mapped[list[Order]] = relationship(back_populates="panel")


class Plan(Base):
    __tablename__ = "plans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    mark: Mapped[str] = mapped_column(String(8))
    name: Mapped[str] = mapped_column(String(64))
    subtitle: Mapped[str] = mapped_column(String(160))
    volume_mb: Mapped[int] = mapped_column(Integer)
    duration_hours: Mapped[int] = mapped_column(Integer)
    price_toman: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(24))
    tag: Mapped[str] = mapped_column(String(32))
    featured: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    is_public: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    panel_id: Mapped[str] = mapped_column(ForeignKey("panel_instances.id"))
    inbound_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    panel: Mapped[PanelInstance] = relationship(back_populates="plans")
    orders: Mapped[list[Order]] = relationship(back_populates="plan")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"), index=True)
    panel_id: Mapped[str] = mapped_column(ForeignKey("panel_instances.id"), index=True)
    inbound_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    amount_toman: Mapped[int] = mapped_column(Integer)
    volume_mb: Mapped[int] = mapped_column(Integer)
    duration_hours: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="awaiting_receipt", index=True)
    receipt_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_trial: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="orders")
    plan: Mapped[Plan] = relationship(back_populates="orders")
    panel: Mapped[PanelInstance] = relationship(back_populates="orders")
    service: Mapped[Service | None] = relationship(back_populates="order", uselist=False)
    trial_claim: Mapped[TrialClaim | None] = relationship(back_populates="order", uselist=False)


class Service(Base):
    __tablename__ = "services"
    __table_args__ = (UniqueConstraint("order_id", name="uq_service_order"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), unique=True, index=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"))
    panel_id: Mapped[str] = mapped_column(ForeignKey("panel_instances.id"))
    panel_type: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(128), unique=True)
    username: Mapped[str] = mapped_column(String(128), unique=True)
    subscription_url: Mapped[str] = mapped_column(Text)
    volume_mb: Mapped[int] = mapped_column(Integer)
    used_mb: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    is_trial: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship(back_populates="services")
    order: Mapped[Order] = relationship(back_populates="service")
    trial_claim: Mapped[TrialClaim | None] = relationship(back_populates="service", uselist=False)


class TrialPolicy(Base):
    __tablename__ = "trial_policy"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    volume_mb: Mapped[int] = mapped_column(Integer, default=100)
    duration_hours: Mapped[int] = mapped_column(Integer, default=24)
    max_claims: Mapped[int] = mapped_column(Integer, default=1)
    window_days: Mapped[int] = mapped_column(Integer, default=0)
    generation: Mapped[int] = mapped_column(Integer, default=1)
    panel_id: Mapped[str] = mapped_column(ForeignKey("panel_instances.id"), default="pg-ams-1")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TrialClaim(Base):
    __tablename__ = "trial_claims"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), unique=True)
    service_id: Mapped[int | None] = mapped_column(ForeignKey("services.id"), unique=True, nullable=True)
    policy_generation: Mapped[int] = mapped_column(Integer, index=True)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    user: Mapped[User] = relationship(back_populates="trial_claims")
    order: Mapped[Order] = relationship(back_populates="trial_claim")
    service: Mapped[Service | None] = relationship(back_populates="trial_claim")


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(96), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    actor_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64))
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
