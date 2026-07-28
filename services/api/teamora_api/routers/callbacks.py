from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import and_, func, select

from teamora_api.audit import write_audit
from teamora_api.dependencies import Principal, SessionDep, require_permission
from teamora_api.enums import RoleName
from teamora_api.errors import ApiError
from teamora_api.models import CallbackTask, Customer, CustomerContact
from teamora_api.project_access import resolve_project
from teamora_api.schemas.common import Page
from teamora_api.schemas.crm import CallbackCreate, CallbackRead

router = APIRouter(prefix="/callbacks", tags=["callbacks"])


def serialize_callback(
    task: CallbackTask, customer: Customer, contact: CustomerContact | None
) -> CallbackRead:
    return CallbackRead(
        id=task.id,
        project_id=task.project_id,
        customer_id=task.customer_id,
        customer_name=customer.display_name,
        customer_phone=contact.display_value if contact else None,
        call_id=task.call_id,
        assigned_user_id=task.assigned_user_id,
        due_at=task.due_at,
        status=task.status,
        note=task.note,
        completed_at=task.completed_at,
        created_at=task.created_at,
    )


async def callback_row(
    session: SessionDep,
    tenant_id: UUID,
    task_id: UUID,
    assigned_user_id: UUID | None = None,
) -> tuple[CallbackTask, Customer, CustomerContact | None] | None:
    row = (
        await session.execute(
            select(CallbackTask, Customer, CustomerContact)
            .join(Customer, Customer.id == CallbackTask.customer_id)
            .outerjoin(
                CustomerContact,
                and_(
                    CustomerContact.customer_id == Customer.id,
                    CustomerContact.tenant_id == tenant_id,
                    CustomerContact.project_id == Customer.project_id,
                    CustomerContact.kind == "phone",
                    CustomerContact.is_primary.is_(True),
                ),
            )
            .where(
                CallbackTask.tenant_id == tenant_id,
                CallbackTask.id == task_id,
                *(
                    [CallbackTask.assigned_user_id == assigned_user_id]
                    if assigned_user_id is not None
                    else []
                ),
            )
        )
    ).one_or_none()
    if row is None:
        return None
    return row[0], row[1], row[2]


@router.get("", response_model=Page[CallbackRead])
async def list_callbacks(
    session: SessionDep,
    principal: Principal = require_permission("callbacks:manage"),
    status: str | None = None,
    project_id: UUID | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Page[CallbackRead]:
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    filters = [CallbackTask.tenant_id == principal.tenant_id]
    if project_id is not None:
        project = await resolve_project(session, principal, project_id, active_only=False)
        filters.append(CallbackTask.project_id == project.id)
    if principal.role == RoleName.HUMAN_OPERATOR:
        filters.append(CallbackTask.assigned_user_id == principal.user_id)
    if status:
        filters.append(CallbackTask.status == status)
    total = int(await session.scalar(select(func.count()).select_from(CallbackTask).where(*filters)) or 0)
    rows = (
        await session.execute(
            select(CallbackTask, Customer, CustomerContact)
            .join(Customer, Customer.id == CallbackTask.customer_id)
            .outerjoin(
                CustomerContact,
                and_(
                    CustomerContact.customer_id == Customer.id,
                    CustomerContact.tenant_id == principal.tenant_id,
                    CustomerContact.project_id == Customer.project_id,
                    CustomerContact.kind == "phone",
                    CustomerContact.is_primary.is_(True),
                ),
            )
            .where(*filters)
            .order_by(CallbackTask.due_at, CallbackTask.created_at)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return Page(
        items=[serialize_callback(task, customer, contact) for task, customer, contact in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=CallbackRead, status_code=201)
async def create_callback(
    payload: CallbackCreate,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("callbacks:manage"),
) -> CallbackRead:
    if payload.due_at.tzinfo is None:
        raise ApiError(422, "callback_timezone_required", "Дата перезвона должна содержать часовой пояс")
    due_at = payload.due_at.astimezone(UTC)
    if due_at <= datetime.now(UTC):
        raise ApiError(422, "callback_date_past", "Дата перезвона должна быть в будущем")
    customer = await session.scalar(
        select(Customer).where(
            Customer.tenant_id == principal.tenant_id,
            Customer.id == payload.customer_id,
            Customer.is_anonymized.is_(False),
        )
    )
    if customer is None:
        raise ApiError(404, "customer_not_found", "Клиент не найден")
    await resolve_project(session, principal, customer.project_id)
    if principal.role == RoleName.HUMAN_OPERATOR and (
        customer.locked_by_user_id != principal.user_id
        or customer.locked_until is None
        or customer.locked_until <= datetime.now(UTC)
    ):
        raise ApiError(
            403,
            "customer_not_assigned",
            "Оператор может создать перезвон только для назначенного клиента",
        )
    task = CallbackTask(
        tenant_id=principal.tenant_id,
        project_id=customer.project_id,
        customer_id=customer.id,
        assigned_user_id=principal.user_id,
        due_at=due_at,
        status="pending",
        note=payload.note.strip(),
    )
    customer.status = "callback"
    customer.next_call_at = task.due_at
    session.add(task)
    await session.flush()
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="callback.created",
        resource_type="callback_task",
        resource_id=task.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"customer_id": str(customer.id), "due_at": task.due_at.isoformat()},
    )
    row = await callback_row(session, principal.tenant_id, task.id)
    if row is None:
        raise ApiError(500, "callback_read_failed", "Не удалось прочитать созданную задачу")
    await session.commit()
    return serialize_callback(*row)


@router.post("/{task_id}/complete", response_model=CallbackRead)
async def complete_callback(
    task_id: UUID,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("callbacks:manage"),
) -> CallbackRead:
    row = await callback_row(
        session,
        principal.tenant_id,
        task_id,
        principal.user_id if principal.role == RoleName.HUMAN_OPERATOR else None,
    )
    if row is None:
        raise ApiError(404, "callback_not_found", "Задача на перезвон не найдена")
    task, customer, contact = row
    if task.status != "completed":
        task.status = "completed"
        task.completed_at = datetime.now(UTC)
        customer.next_call_at = None
        if customer.status == "callback":
            customer.status = "completed"
        await write_audit(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="callback.completed",
            resource_type="callback_task",
            resource_id=task.id,
            correlation_id=request.state.correlation_id,
        )
        await session.commit()
    return serialize_callback(task, customer, contact)
