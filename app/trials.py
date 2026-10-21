from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditLog, Order, Plan, TrialClaim, TrialPolicy, User
from .panels import provision_order


class TrialEligibilityError(RuntimeError):
    pass


async def get_trial_policy(session: AsyncSession, lock: bool = False) -> TrialPolicy:
    query = select(TrialPolicy).where(TrialPolicy.id == 1)
    if lock:
        query = query.with_for_update()
    policy = await session.scalar(query)
    if policy is None:
        policy = TrialPolicy(id=1)
        session.add(policy)
        await session.flush()
    return policy


async def count_user_claims(session: AsyncSession, user_id: int, policy: TrialPolicy) -> int:
    query = select(func.count(TrialClaim.id)).where(
        TrialClaim.user_id == user_id,
        TrialClaim.policy_generation == policy.generation,
    )
    if policy.window_days > 0:
        query = query.where(TrialClaim.claimed_at >= datetime.now(UTC) - timedelta(days=policy.window_days))
    return int(await session.scalar(query) or 0)


async def trial_status(session: AsyncSession, user: User) -> dict[str, object]:
    policy = await get_trial_policy(session)
    used = await count_user_claims(session, user.id, policy)
    return {
        "enabled": policy.enabled,
        "volumeMb": policy.volume_mb,
        "durationHours": policy.duration_hours,
        "maxClaims": policy.max_claims,
        "windowDays": policy.window_days,
        "generation": policy.generation,
        "usedClaims": used,
        "remainingClaims": max(0, policy.max_claims - used),
        "eligible": policy.enabled and used < policy.max_claims,
        "panelId": policy.panel_id,
    }


async def claim_trial(session: AsyncSession, user: User, actor_telegram_id: int = 0):
    policy = await get_trial_policy(session, lock=True)
    if not policy.enabled:
        raise TrialEligibilityError("Free trial is disabled")
    used = await count_user_claims(session, user.id, policy)
    if used >= policy.max_claims:
        if policy.window_days > 0:
            raise TrialEligibilityError(f"Trial limit reached for the last {policy.window_days} days")
        raise TrialEligibilityError("Free trial has already been used")

    trial_plan = await session.get(Plan, "free-trial")
    if trial_plan is None:
        raise TrialEligibilityError("Free trial plan is missing")

    order = Order(
        user_id=user.id,
        plan_id=trial_plan.id,
        panel_id=policy.panel_id,
        inbound_id=trial_plan.inbound_id,
        amount_toman=0,
        volume_mb=policy.volume_mb,
        duration_hours=policy.duration_hours,
        status="pending",
        receipt_file_id="free-trial-auto-approved",
        is_trial=True,
    )
    session.add(order)
    await session.flush()

    claim = TrialClaim(
        user_id=user.id,
        order_id=order.id,
        policy_generation=policy.generation,
        claimed_at=datetime.now(UTC),
    )
    session.add(claim)
    await session.flush()

    service = await provision_order(session, order.id, actor_telegram_id)
    claim.service_id = service.id
    session.add(
        AuditLog(
            actor_telegram_id=user.telegram_id,
            action="trial.claim",
            entity_type="trial_claim",
            entity_id=str(claim.id),
            details=(
                f"volume_mb={policy.volume_mb}; duration_hours={policy.duration_hours}; "
                f"generation={policy.generation}; claim={used + 1}/{policy.max_claims}"
            ),
        )
    )
    await session.flush()
    return service, policy


async def configure_trial(
    session: AsyncSession,
    *,
    actor_telegram_id: int,
    volume_mb: int,
    duration_hours: int,
    max_claims: int,
    window_days: int,
    panel_id: str | None = None,
) -> TrialPolicy:
    if not 1 <= volume_mb <= 1024 * 100:
        raise ValueError("Trial volume must be between 1MB and 100GB")
    if not 1 <= duration_hours <= 24 * 365:
        raise ValueError("Trial duration must be between 1 hour and 365 days")
    if not 1 <= max_claims <= 100:
        raise ValueError("Trial max claims must be between 1 and 100")
    if not 0 <= window_days <= 3650:
        raise ValueError("Trial window must be between 0 and 3650 days")

    policy = await get_trial_policy(session, lock=True)
    policy.volume_mb = volume_mb
    policy.duration_hours = duration_hours
    policy.max_claims = max_claims
    policy.window_days = window_days
    if panel_id:
        policy.panel_id = panel_id
    session.add(
        AuditLog(
            actor_telegram_id=actor_telegram_id,
            action="trial.configure",
            entity_type="trial_policy",
            entity_id="1",
            details=(
                f"volume_mb={volume_mb}; duration_hours={duration_hours}; "
                f"max_claims={max_claims}; window_days={window_days}; panel={policy.panel_id}"
            ),
        )
    )
    await session.flush()
    return policy


async def reset_trials(session: AsyncSession, actor_telegram_id: int) -> TrialPolicy:
    policy = await get_trial_policy(session, lock=True)
    policy.generation += 1
    session.add(
        AuditLog(
            actor_telegram_id=actor_telegram_id,
            action="trial.reset_all",
            entity_type="trial_policy",
            entity_id="1",
            details=f"new_generation={policy.generation}",
        )
    )
    await session.flush()
    return policy
