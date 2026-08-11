from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from teamora_api.call_state import CAPACITY_CALL_STATES
from teamora_api.dependencies import Principal
from teamora_api.enums import CallStatus, QueueStatus, RoleName
from teamora_api.errors import ApiError
from teamora_api.models import (
    Call,
    HumanOperator,
    Membership,
    OperatorPresence,
    OperatorStatus,
    ProjectUser,
)

PRESENCE_TTL_SECONDS = 90
MANUAL_OPERATOR_STATUSES = {
    QueueStatus.AVAILABLE,
    QueueStatus.AWAY,
    QueueStatus.ON_BREAK,
    QueueStatus.OFFLINE,
}
DIALER_ROLES = {
    RoleName.TENANT_OWNER,
    RoleName.TENANT_MANAGER,
    RoleName.HUMAN_OPERATOR,
}
EffectiveOperatorStatus = Literal["available", "away", "on_break", "offline", "busy", "on_hold"]


@dataclass(frozen=True)
class OperatorState:
    manual_status: QueueStatus
    effective_status: EffectiveOperatorStatus
    last_heartbeat_at: datetime | None
    current_call_id: UUID | None


async def ensure_operator_profile(
    session: AsyncSession,
    membership: Membership,
    *,
    now: datetime | None = None,
) -> tuple[HumanOperator | None, OperatorStatus | None]:
    if membership.role not in DIALER_ROLES:
        return None, None
    current_time = now or datetime.now(UTC)
    operator = await session.scalar(
        select(HumanOperator).where(
            HumanOperator.tenant_id == membership.tenant_id,
            HumanOperator.membership_id == membership.id,
        )
    )
    if operator is None:
        operator = HumanOperator(
            tenant_id=membership.tenant_id,
            membership_id=membership.id,
            max_concurrent_calls=1,
            is_transfer_available=True,
        )
        session.add(operator)
        await session.flush()
    status = await session.scalar(
        select(OperatorStatus).where(
            OperatorStatus.tenant_id == membership.tenant_id,
            OperatorStatus.human_operator_id == operator.id,
        )
    )
    if status is None:
        status = OperatorStatus(
            tenant_id=membership.tenant_id,
            human_operator_id=operator.id,
            status=QueueStatus.AVAILABLE,
            changed_at=current_time,
        )
        session.add(status)
        await session.flush()
    elif status.status not in MANUAL_OPERATOR_STATUSES:
        status.status = QueueStatus.AVAILABLE
        status.changed_at = current_time
    return operator, status


async def touch_presence(
    session: AsyncSession,
    membership: Membership,
    *,
    session_key: str,
    now: datetime | None = None,
) -> OperatorPresence:
    current_time = now or datetime.now(UTC)
    presence = await session.scalar(
        select(OperatorPresence)
        .where(
            OperatorPresence.tenant_id == membership.tenant_id,
            OperatorPresence.membership_id == membership.id,
            OperatorPresence.session_key == session_key,
        )
        .with_for_update()
    )
    if presence is None:
        presence = OperatorPresence(
            tenant_id=membership.tenant_id,
            membership_id=membership.id,
            session_key=session_key,
            heartbeat_at=current_time,
        )
        session.add(presence)
    else:
        presence.heartbeat_at = current_time
        presence.ended_at = None
    membership.presence_last_seen_at = current_time
    return presence


async def end_all_presence(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    membership_id: UUID,
    now: datetime | None = None,
) -> None:
    current_time = now or datetime.now(UTC)
    presences = list(
        await session.scalars(
            select(OperatorPresence).where(
                OperatorPresence.tenant_id == tenant_id,
                OperatorPresence.membership_id == membership_id,
                OperatorPresence.ended_at.is_(None),
            )
        )
    )
    for presence in presences:
        presence.ended_at = current_time


async def get_operator_state(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    membership: Membership,
    operator: HumanOperator | None = None,
    status: OperatorStatus | None = None,
    now: datetime | None = None,
) -> OperatorState:
    current_time = now or datetime.now(UTC)
    manual = status.status if status and status.status in MANUAL_OPERATOR_STATUSES else QueueStatus.OFFLINE
    active_call = await session.scalar(
        select(Call)
        .where(
            Call.tenant_id == tenant_id,
            Call.operator_user_id == membership.user_id,
            Call.status.in_(CAPACITY_CALL_STATES),
        )
        .order_by(Call.started_at.desc().nullslast(), Call.created_at.desc())
        .limit(1)
    )
    if active_call is not None:
        effective: EffectiveOperatorStatus = "on_hold" if active_call.status == CallStatus.ON_HOLD else "busy"
        return OperatorState(manual, effective, membership.presence_last_seen_at, active_call.id)

    cutoff = current_time - timedelta(seconds=PRESENCE_TTL_SECONDS)
    last_heartbeat = await session.scalar(
        select(func.max(OperatorPresence.heartbeat_at)).where(
            OperatorPresence.tenant_id == tenant_id,
            OperatorPresence.membership_id == membership.id,
            OperatorPresence.ended_at.is_(None),
            OperatorPresence.heartbeat_at >= cutoff,
        )
    )
    if not membership.is_active or last_heartbeat is None or manual == QueueStatus.OFFLINE:
        effective = "offline"
    else:
        effective = cast(EffectiveOperatorStatus, manual.value)
    return OperatorState(manual, effective, last_heartbeat, None)


async def require_available_for_new_assignment(
    session: AsyncSession,
    principal: Principal,
) -> OperatorState:
    membership = await session.scalar(
        select(Membership).where(
            Membership.tenant_id == principal.tenant_id,
            Membership.id == principal.membership_id,
            Membership.user_id == principal.user_id,
            Membership.is_active.is_(True),
        )
    )
    if membership is None or membership.role not in DIALER_ROLES:
        raise ApiError(403, "dialer_operator_unavailable", "Сотрудник не допущен к Dialer")
    operator, status = await ensure_operator_profile(session, membership)
    state = await get_operator_state(
        session,
        tenant_id=principal.tenant_id,
        membership=membership,
        operator=operator,
        status=status,
    )
    if state.effective_status != QueueStatus.AVAILABLE.value:
        raise ApiError(
            409,
            "operator_not_available",
            "Чтобы получить следующего клиента, установите статус «Доступен»",
        )
    return state


async def active_owner_count(session: AsyncSession, tenant_id: UUID) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(Membership)
            .where(
                Membership.tenant_id == tenant_id,
                Membership.role == RoleName.TENANT_OWNER,
                Membership.is_active.is_(True),
            )
        )
        or 0
    )


async def lock_tenant_memberships(session: AsyncSession, tenant_id: UUID) -> list[Membership]:
    return list(
        await session.scalars(
            select(Membership)
            .where(Membership.tenant_id == tenant_id)
            .order_by(Membership.id)
            .with_for_update()
        )
    )


def assert_expected_version(membership: Membership, expected_version: int) -> None:
    if membership.state_version != expected_version:
        raise ApiError(
            409,
            "team_member_version_conflict",
            "Карточка сотрудника изменилась. Обновите данные и повторите действие",
        )


def can_manage_member(actor: Principal, target: Membership) -> bool:
    if actor.role == RoleName.TENANT_OWNER:
        return True
    if actor.role == RoleName.TENANT_MANAGER:
        return target.role in {RoleName.HUMAN_OPERATOR, RoleName.ANALYST}
    return False


def ensure_member_management_allowed(actor: Principal, target: Membership) -> None:
    if not can_manage_member(actor, target):
        raise ApiError(403, "team_member_forbidden", "Недостаточно прав для изменения сотрудника")


async def require_transfer_candidate(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    destination_user_id: UUID,
) -> None:
    row = (
        await session.execute(
            select(Membership, HumanOperator, OperatorStatus)
            .join(
                ProjectUser,
                and_(
                    ProjectUser.tenant_id == Membership.tenant_id,
                    ProjectUser.user_id == Membership.user_id,
                    ProjectUser.project_id == project_id,
                    ProjectUser.is_active.is_(True),
                ),
            )
            .join(
                HumanOperator,
                and_(
                    HumanOperator.tenant_id == Membership.tenant_id,
                    HumanOperator.membership_id == Membership.id,
                ),
            )
            .join(
                OperatorStatus,
                and_(
                    OperatorStatus.tenant_id == Membership.tenant_id,
                    OperatorStatus.human_operator_id == HumanOperator.id,
                ),
            )
            .where(
                Membership.tenant_id == tenant_id,
                Membership.user_id == destination_user_id,
                Membership.is_active.is_(True),
                Membership.role.in_(DIALER_ROLES),
                HumanOperator.is_transfer_available.is_(True),
            )
        )
    ).one_or_none()
    if row is None:
        raise ApiError(422, "transfer_operator_invalid", "Оператор недоступен для перевода")
    membership, operator, status = row
    state = await get_operator_state(
        session,
        tenant_id=tenant_id,
        membership=membership,
        operator=operator,
        status=status,
    )
    if state.effective_status != QueueStatus.AVAILABLE.value:
        raise ApiError(409, "transfer_operator_unavailable", "Оператор сейчас недоступен")
