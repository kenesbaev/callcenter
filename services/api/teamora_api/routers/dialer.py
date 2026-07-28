from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from sqlalchemy import or_, select

from teamora_api.audit import write_audit
from teamora_api.dependencies import Principal, SessionDep, require_permission
from teamora_api.errors import ApiError
from teamora_api.models import Call, CallbackTask, Customer, TenantSettings
from teamora_api.routers.calls import serialize_call
from teamora_api.routers.customers import contacts_for_customers, serialize_customer
from teamora_api.schemas.calls import CallRead
from teamora_api.schemas.crm import DialerAssignment

router = APIRouter(prefix="/dialer", tags=["dialer"])
LOCK_MINUTES = 15


async def end_of_tenant_day(session: SessionDep, tenant_id: UUID, now: datetime) -> datetime:
    settings = await session.scalar(select(TenantSettings).where(TenantSettings.tenant_id == tenant_id))
    zone = ZoneInfo(settings.timezone if settings else "Asia/Tashkent")
    local_now = now.astimezone(zone)
    return datetime.combine(local_now.date(), time.max, tzinfo=zone).astimezone(UTC)


async def assignment(
    session: SessionDep,
    tenant_id: UUID,
    customer: Customer,
    task: CallbackTask | None,
) -> DialerAssignment:
    grouped = await contacts_for_customers(session, tenant_id, [customer.id])
    return DialerAssignment(
        customer=serialize_customer(customer, grouped[customer.id]),
        source="callback" if task else "new",
        callback_task_id=task.id if task else None,
    )


async def current_task(
    session: SessionDep, tenant_id: UUID, customer_id: UUID, user_id: UUID
) -> CallbackTask | None:
    return await session.scalar(
        select(CallbackTask)
        .where(
            CallbackTask.tenant_id == tenant_id,
            CallbackTask.customer_id == customer_id,
            CallbackTask.status == "pending",
            or_(CallbackTask.assigned_user_id.is_(None), CallbackTask.assigned_user_id == user_id),
        )
        .order_by(CallbackTask.due_at)
        .limit(1)
    )


@router.get("/current", response_model=DialerAssignment | None)
async def current_customer(
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
) -> DialerAssignment | None:
    now = datetime.now(UTC)
    customer = await session.scalar(
        select(Customer).where(
            Customer.tenant_id == principal.tenant_id,
            Customer.locked_by_user_id == principal.user_id,
            Customer.locked_until > now,
            Customer.is_anonymized.is_(False),
        )
    )
    if customer is None:
        return None
    task = await current_task(session, principal.tenant_id, customer.id, principal.user_id)
    return await assignment(session, principal.tenant_id, customer, task)


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
) -> DialerAssignment | None:
    now = datetime.now(UTC)
    current = await session.scalar(
        select(Customer).where(
            Customer.tenant_id == principal.tenant_id,
            Customer.locked_by_user_id == principal.user_id,
            Customer.locked_until > now,
            Customer.is_anonymized.is_(False),
        )
    )
    if current:
        current_callback = await current_task(session, principal.tenant_id, current.id, principal.user_id)
        return await assignment(session, principal.tenant_id, current, current_callback)

    available_lock = or_(Customer.locked_until.is_(None), Customer.locked_until <= now)
    due_before = await end_of_tenant_day(session, principal.tenant_id, now)
    callback_row = (
        await session.execute(
            select(Customer, CallbackTask)
            .join(CallbackTask, CallbackTask.customer_id == Customer.id)
            .where(
                Customer.tenant_id == principal.tenant_id,
                Customer.is_anonymized.is_(False),
                Customer.status != "do_not_call",
                available_lock,
                CallbackTask.tenant_id == principal.tenant_id,
                CallbackTask.status == "pending",
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
    else:
        customer = await session.scalar(
            select(Customer)
            .where(
                Customer.tenant_id == principal.tenant_id,
                Customer.is_anonymized.is_(False),
                Customer.status == "new",
                available_lock,
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
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="dialer.customer_assigned",
        resource_type="customer",
        resource_id=customer.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"source": "callback" if task else "new"},
    )
    await session.commit()
    return await assignment(session, principal.tenant_id, customer, task)


@router.post("/customers/{customer_id}/release", status_code=204)
async def release_customer(
    customer_id: UUID,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
) -> None:
    customer = await session.scalar(
        select(Customer).where(
            Customer.tenant_id == principal.tenant_id,
            Customer.id == customer_id,
            Customer.locked_by_user_id == principal.user_id,
        )
    )
    if customer is None:
        raise ApiError(404, "assignment_not_found", "Назначение клиента не найдено")
    task = await current_task(session, principal.tenant_id, customer.id, principal.user_id)
    customer.status = "callback" if task else "new"
    customer.locked_by_user_id = None
    customer.locked_until = None
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
