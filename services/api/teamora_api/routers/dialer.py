from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from sqlalchemy import and_, or_, select
from sqlalchemy.sql.elements import ColumnElement

from teamora_api.audit import write_audit
from teamora_api.dependencies import Principal, SessionDep, require_permission
from teamora_api.enums import CallStatus
from teamora_api.errors import ApiError
from teamora_api.models import Call, CallbackTask, CallOutcome, Customer, TenantSettings
from teamora_api.project_access import accessible_project_ids, resolve_project
from teamora_api.routers.calls import serialize_call
from teamora_api.routers.customers import contacts_for_customers, serialize_customer
from teamora_api.schemas.calls import CallRead
from teamora_api.schemas.crm import DialerAssignment, DialerLeaseRequest

router = APIRouter(prefix="/dialer", tags=["dialer"])
LOCK_MINUTES = 15


async def end_of_tenant_day(session: SessionDep, tenant_id: UUID, now: datetime) -> datetime:
    settings = await session.scalar(select(TenantSettings).where(TenantSettings.tenant_id == tenant_id))
    zone = ZoneInfo(settings.timezone if settings else "Asia/Tashkent")
    local_now = now.astimezone(zone)
    return datetime.combine(local_now.date(), time.max, tzinfo=zone).astimezone(UTC)


def unresolved_operator_call(
    tenant_id: UUID,
    customer_id: UUID,
    user_id: UUID,
) -> ColumnElement[bool]:
    return (
        select(Call.id)
        .outerjoin(
            CallOutcome,
            and_(CallOutcome.call_id == Call.id, CallOutcome.tenant_id == Call.tenant_id),
        )
        .where(
            Call.tenant_id == tenant_id,
            Call.customer_id == customer_id,
            Call.operator_user_id == user_id,
            or_(
                Call.status.in_([CallStatus.RINGING, CallStatus.ACTIVE]),
                and_(Call.status == CallStatus.COMPLETED, CallOutcome.id.is_(None)),
            ),
        )
        .exists()
    )


def active_customer_call() -> ColumnElement[bool]:
    return (
        select(Call.id)
        .where(
            Call.tenant_id == Customer.tenant_id,
            Call.customer_id == Customer.id,
            Call.status.in_([CallStatus.RINGING, CallStatus.ACTIVE]),
        )
        .exists()
    )


async def assignment(
    session: SessionDep,
    tenant_id: UUID,
    customer: Customer,
    task: CallbackTask | None,
) -> DialerAssignment:
    if customer.lock_token is None:
        raise ApiError(500, "dialer_lease_missing", "Не удалось создать безопасную блокировку клиента")
    grouped = await contacts_for_customers(session, tenant_id, [customer.id])
    return DialerAssignment(
        customer=serialize_customer(customer, grouped[customer.id]),
        source="callback" if task else "new",
        callback_task_id=task.id if task else None,
        lock_token=customer.lock_token,
    )


async def current_task(
    session: SessionDep,
    tenant_id: UUID,
    project_id: UUID,
    customer_id: UUID,
    user_id: UUID,
) -> CallbackTask | None:
    return (
        await session.scalars(
            select(CallbackTask)
            .where(
                CallbackTask.tenant_id == tenant_id,
                CallbackTask.project_id == project_id,
                CallbackTask.customer_id == customer_id,
                or_(
                    and_(
                        CallbackTask.status == "pending",
                        or_(
                            CallbackTask.assigned_user_id.is_(None),
                            CallbackTask.assigned_user_id == user_id,
                        ),
                    ),
                    and_(
                        CallbackTask.status == "in_progress",
                        CallbackTask.assigned_user_id == user_id,
                    ),
                ),
            )
            .order_by(CallbackTask.due_at)
            .limit(1)
        )
    ).first()


async def assigned_customer(
    session: SessionDep,
    principal: Principal,
    now: datetime,
) -> Customer | None:
    project_ids = await accessible_project_ids(session, principal)
    if not project_ids:
        return None
    unresolved_call = (
        select(Call.id)
        .outerjoin(
            CallOutcome,
            and_(CallOutcome.call_id == Call.id, CallOutcome.tenant_id == Call.tenant_id),
        )
        .where(
            Call.tenant_id == principal.tenant_id,
            Call.customer_id == Customer.id,
            Call.operator_user_id == principal.user_id,
            or_(
                Call.status.in_([CallStatus.RINGING, CallStatus.ACTIVE]),
                and_(Call.status == CallStatus.COMPLETED, CallOutcome.id.is_(None)),
            ),
        )
        .exists()
    )
    return (
        await session.scalars(
            select(Customer).where(
                Customer.tenant_id == principal.tenant_id,
                Customer.project_id.in_(project_ids),
                Customer.locked_by_user_id == principal.user_id,
                or_(
                    Customer.locked_until > now,
                    unresolved_call,
                ),
                Customer.is_anonymized.is_(False),
            )
        )
    ).first()


async def renew_assignment(
    session: SessionDep,
    principal: Principal,
    customer: Customer,
    now: datetime,
) -> DialerAssignment:
    if customer.lock_token is None:
        customer.lock_token = uuid4()
    customer.locked_until = now + timedelta(minutes=LOCK_MINUTES)
    task = await current_task(
        session,
        principal.tenant_id,
        customer.project_id,
        customer.id,
        principal.user_id,
    )
    response = await assignment(session, principal.tenant_id, customer, task)
    await session.commit()
    return response


@router.get("/current", response_model=DialerAssignment | None)
async def current_customer(
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
) -> DialerAssignment | None:
    now = datetime.now(UTC)
    customer = await assigned_customer(session, principal, now)
    if customer is None:
        return None
    return await renew_assignment(session, principal, customer, now)


@router.get("/history", response_model=list[CallRead])
async def assigned_customer_history(
    customer_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
) -> list[CallRead]:
    assigned = await session.scalar(
        select(Customer.id).where(
            Customer.tenant_id == principal.tenant_id,
            Customer.id == customer_id,
            Customer.locked_by_user_id == principal.user_id,
        )
    )
    if assigned is None:
        raise ApiError(403, "customer_not_assigned", "Клиент не назначен текущему оператору")
    calls = list(
        await session.scalars(
            select(Call)
            .where(
                Call.tenant_id == principal.tenant_id,
                Call.customer_id == customer_id,
                Call.operator_user_id == principal.user_id,
            )
            .order_by(Call.created_at.desc())
            .limit(10)
        )
    )
    return [serialize_call(call) for call in calls]


@router.post("/next-client", response_model=DialerAssignment | None)
async def next_client(
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
    project_id: UUID | None = None,
) -> DialerAssignment | None:
    now = datetime.now(UTC)
    current = await assigned_customer(session, principal, now)
    if current is not None:
        return await renew_assignment(session, principal, current, now)

    project = await resolve_project(session, principal, project_id)
    available_lock = or_(Customer.locked_until.is_(None), Customer.locked_until <= now)
    due_before = await end_of_tenant_day(session, principal.tenant_id, now)
    callback_row = (
        await session.execute(
            select(Customer, CallbackTask)
            .join(
                CallbackTask,
                and_(
                    CallbackTask.customer_id == Customer.id,
                    CallbackTask.project_id == Customer.project_id,
                ),
            )
            .where(
                Customer.tenant_id == principal.tenant_id,
                Customer.project_id == project.id,
                Customer.is_anonymized.is_(False),
                Customer.status != "do_not_call",
                available_lock,
                ~active_customer_call(),
                CallbackTask.tenant_id == principal.tenant_id,
                CallbackTask.project_id == project.id,
                or_(
                    and_(
                        CallbackTask.status == "pending",
                        or_(
                            CallbackTask.assigned_user_id.is_(None),
                            CallbackTask.assigned_user_id == principal.user_id,
                        ),
                    ),
                    and_(
                        CallbackTask.status == "in_progress",
                        CallbackTask.assigned_user_id == principal.user_id,
                    ),
                ),
                CallbackTask.due_at <= due_before,
            )
            .order_by(CallbackTask.due_at, CallbackTask.created_at)
            .with_for_update(of=Customer, skip_locked=True)
            .limit(1)
        )
    ).one_or_none()
    task: CallbackTask | None = None
    if callback_row:
        customer, task = callback_row
        task.assigned_user_id = principal.user_id
        task.status = "in_progress"
    else:
        customer = await session.scalar(
            select(Customer)
            .where(
                Customer.tenant_id == principal.tenant_id,
                Customer.project_id == project.id,
                Customer.is_anonymized.is_(False),
                or_(
                    Customer.status == "new",
                    and_(
                        Customer.status == "assigned",
                        Customer.locked_until <= now,
                    ),
                ),
                available_lock,
                ~active_customer_call(),
                ~select(CallbackTask.id)
                .where(
                    CallbackTask.tenant_id == principal.tenant_id,
                    CallbackTask.project_id == project.id,
                    CallbackTask.customer_id == Customer.id,
                    CallbackTask.status.in_(["pending", "in_progress"]),
                )
                .exists(),
            )
            .order_by(Customer.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
    if customer is None:
        return None

    customer.status = "assigned"
    customer.locked_by_user_id = principal.user_id
    customer.locked_until = now + timedelta(minutes=LOCK_MINUTES)
    customer.lock_token = uuid4()
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="dialer.customer_assigned",
        resource_type="customer",
        resource_id=customer.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={
            "source": "callback" if task else "new",
            "project_id": str(project.id),
        },
    )
    response = await assignment(session, principal.tenant_id, customer, task)
    await session.commit()
    return response


@router.post("/customers/{customer_id}/heartbeat", status_code=204)
async def heartbeat_customer(
    customer_id: UUID,
    payload: DialerLeaseRequest,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
) -> None:
    customer = await session.scalar(
        select(Customer)
        .where(
            Customer.tenant_id == principal.tenant_id,
            Customer.id == customer_id,
            Customer.locked_by_user_id == principal.user_id,
            Customer.lock_token == payload.lock_token,
            Customer.is_anonymized.is_(False),
        )
        .with_for_update()
    )
    if customer is None:
        raise ApiError(409, "dialer_lease_lost", "Блокировка клиента уже недействительна")
    customer.locked_until = datetime.now(UTC) + timedelta(minutes=LOCK_MINUTES)
    await session.commit()


@router.post("/customers/{customer_id}/release", status_code=204)
async def release_customer(
    customer_id: UUID,
    payload: DialerLeaseRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
) -> None:
    customer = await session.scalar(
        select(Customer)
        .where(
            Customer.tenant_id == principal.tenant_id,
            Customer.id == customer_id,
            Customer.locked_by_user_id == principal.user_id,
            Customer.lock_token == payload.lock_token,
        )
        .with_for_update()
    )
    if customer is None:
        raise ApiError(404, "assignment_not_found", "Назначение клиента не найдено")
    if await session.scalar(
        select(unresolved_operator_call(customer.tenant_id, customer.id, principal.user_id))
    ):
        raise ApiError(
            409,
            "call_result_required",
            "Сначала завершите звонок и сохраните его результат",
        )
    task = await current_task(
        session,
        principal.tenant_id,
        customer.project_id,
        customer.id,
        principal.user_id,
    )
    if task is not None and task.status == "in_progress":
        task.status = "pending"
    customer.status = "callback" if task else "new"
    customer.locked_by_user_id = None
    customer.locked_until = None
    customer.lock_token = None
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="dialer.customer_released",
        resource_type="customer",
        resource_id=customer.id,
        correlation_id=request.state.correlation_id,
    )
    await session.commit()
