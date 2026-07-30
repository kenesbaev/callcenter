from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, Request
from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from teamora_api.audit import write_audit
from teamora_api.dependencies import Principal, SessionDep, require_permission
from teamora_api.enums import (
    RoleName,
    TaskEventType,
    TaskPriority,
    TaskSource,
    TaskStatus,
    TaskType,
)
from teamora_api.errors import ApiError
from teamora_api.models import (
    CallbackTask,
    Customer,
    CustomerContact,
    Membership,
    Project,
    ProjectUser,
    TaskEvent,
    TenantSettings,
    User,
)
from teamora_api.project_access import accessible_project_ids, resolve_project
from teamora_api.schemas.common import Page
from teamora_api.schemas.tasks import (
    TaskCancelRequest,
    TaskCreate,
    TaskCustomerOption,
    TaskEventRead,
    TaskOperatorOption,
    TaskOptions,
    TaskPeriod,
    TaskRead,
    TaskReassignRequest,
    TaskRescheduleRequest,
    TaskUpdate,
)
from teamora_api.task_service import (
    acquire_task_command,
    append_task_event,
    cancel_task_record,
    create_task_record,
    store_task_command,
    sync_customer_callback_state,
    task_command_fingerprint,
    utc_due_at,
    validate_task_assignee,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])
MANAGER_ROLES = {RoleName.TENANT_OWNER, RoleName.TENANT_MANAGER}
ACTIVE_STATUSES = (TaskStatus.PENDING, TaskStatus.IN_PROGRESS)

AssignedUser = aliased(User)
CreatorUser = aliased(User)
EventActor = aliased(User)


def is_manager(principal: Principal) -> bool:
    return principal.role in MANAGER_ROLES


def task_visibility_filter(principal: Principal):  # type: ignore[no-untyped-def]
    if principal.role == RoleName.HUMAN_OPERATOR:
        return or_(
            CallbackTask.assigned_user_id == principal.user_id,
            CallbackTask.assigned_user_id.is_(None),
        )
    return True


def task_statement():  # type: ignore[no-untyped-def]
    return (
        select(
            CallbackTask,
            Project,
            Customer,
            CustomerContact,
            AssignedUser,
            CreatorUser,
            TenantSettings,
        )
        .join(
            Project,
            and_(
                Project.tenant_id == CallbackTask.tenant_id,
                Project.id == CallbackTask.project_id,
            ),
        )
        .join(
            Customer,
            and_(
                Customer.tenant_id == CallbackTask.tenant_id,
                Customer.project_id == CallbackTask.project_id,
                Customer.id == CallbackTask.customer_id,
            ),
        )
        .outerjoin(
            CustomerContact,
            and_(
                CustomerContact.tenant_id == CallbackTask.tenant_id,
                CustomerContact.project_id == CallbackTask.project_id,
                CustomerContact.customer_id == CallbackTask.customer_id,
                CustomerContact.kind == "phone",
                CustomerContact.is_primary.is_(True),
            ),
        )
        .outerjoin(AssignedUser, AssignedUser.id == CallbackTask.assigned_user_id)
        .join(CreatorUser, CreatorUser.id == CallbackTask.created_by_user_id)
        .outerjoin(TenantSettings, TenantSettings.tenant_id == CallbackTask.tenant_id)
    )


def serialize_task(row: tuple[object, ...], *, now: datetime | None = None) -> TaskRead:
    task = row[0]
    project = row[1]
    customer = row[2]
    contact = row[3]
    assigned_user = row[4]
    creator = row[5]
    settings = row[6]
    if not isinstance(task, CallbackTask):
        raise TypeError("Expected CallbackTask row")
    if not isinstance(project, Project) or not isinstance(customer, Customer):
        raise TypeError("Expected project and customer row")
    current = now or datetime.now(UTC)
    timezone = project.timezone or (settings.timezone if isinstance(settings, TenantSettings) else None)
    return TaskRead(
        id=task.id,
        tenant_id=task.tenant_id,
        project_id=task.project_id,
        project_name=project.name,
        project_timezone=timezone or "Asia/Tashkent",
        task_type=task.task_type,
        title=task.title,
        description=task.description,
        priority=task.priority,
        status=task.status,
        customer_id=task.customer_id,
        customer_name=customer.display_name,
        customer_phone=(contact.display_value if isinstance(contact, CustomerContact) else None),
        call_id=task.call_id,
        call_outcome_id=task.call_outcome_id,
        assigned_user_id=task.assigned_user_id,
        assigned_user_name=(assigned_user.display_name if isinstance(assigned_user, User) else None),
        created_by_user_id=task.created_by_user_id,
        created_by_user_name=creator.display_name if isinstance(creator, User) else "Система",
        due_at=task.due_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        cancelled_at=task.cancelled_at,
        cancellation_reason=task.cancellation_reason,
        comment=task.comment,
        source=task.source,
        is_overdue=task.status in ACTIVE_STATUSES and task.due_at < current,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


async def load_task_row(
    session: SessionDep,
    principal: Principal,
    task_id: UUID,
    *,
    for_update: bool = False,
) -> tuple[object, ...]:
    project_ids = await accessible_project_ids(session, principal)
    statement = task_statement().where(
        CallbackTask.tenant_id == principal.tenant_id,
        CallbackTask.id == task_id,
        CallbackTask.project_id.in_(project_ids),
        task_visibility_filter(principal),
    )
    if for_update:
        statement = statement.with_for_update(of=CallbackTask)
    row = (await session.execute(statement)).one_or_none()
    if row is None:
        raise ApiError(404, "task_not_found", "Задача не найдена или недоступна")
    return tuple(row)


def require_operator_assignment(principal: Principal, task: CallbackTask) -> None:
    if principal.role == RoleName.HUMAN_OPERATOR and task.assigned_user_id != principal.user_id:
        raise ApiError(403, "task_not_assigned", "Задача не назначена текущему оператору")


async def replay_or_none(
    session: SessionDep,
    *,
    principal: Principal,
    operation: str,
    payload: object,
    idempotency_key: str | None,
) -> tuple[str, TaskRead | None]:
    fingerprint = task_command_fingerprint(operation, payload)
    submission = await acquire_task_command(
        session,
        tenant_id=principal.tenant_id,
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
    )
    replay = TaskRead.model_validate(submission.response_payload) if submission else None
    return fingerprint, replay


async def finish_mutation(
    session: SessionDep,
    *,
    principal: Principal,
    task: CallbackTask,
    operation: str,
    idempotency_key: str | None,
    fingerprint: str,
) -> TaskRead:
    await session.flush()
    row = await load_task_row(session, principal, task.id)
    response = serialize_task(row)
    store_task_command(
        session,
        task=task,
        operation=operation,
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
        response_payload=response.model_dump(mode="json"),
    )
    await session.commit()
    return response


def project_day_end(project: Project, settings: TenantSettings | None, now: datetime) -> datetime:
    timezone = project.timezone or (settings.timezone if settings else "Asia/Tashkent")
    zone = ZoneInfo(timezone)
    local_now = now.astimezone(zone)
    start = datetime.combine(local_now.date(), time.min, tzinfo=zone).astimezone(UTC)
    return start + timedelta(days=1)


async def project_period_clause(
    session: SessionDep,
    principal: Principal,
    project_id: UUID | None,
    project_ids: list[UUID],
    period: TaskPeriod,
    now: datetime,
) -> ColumnElement[bool]:
    settings = await session.scalar(
        select(TenantSettings).where(TenantSettings.tenant_id == principal.tenant_id)
    )
    if project_id is not None:
        project = await resolve_project(session, principal, project_id, active_only=False)
        end = project_day_end(project, settings, now)
        if period == "today":
            return and_(CallbackTask.due_at >= now, CallbackTask.due_at < end)
        return CallbackTask.due_at >= end
    projects = list(
        await session.scalars(
            select(Project).where(
                Project.tenant_id == principal.tenant_id,
                Project.id.in_(project_ids),
            )
        )
    )
    clauses: list[ColumnElement[bool]] = []
    for project in projects:
        end = project_day_end(project, settings, now)
        due_clause = (
            and_(CallbackTask.due_at >= now, CallbackTask.due_at < end)
            if period == "today"
            else CallbackTask.due_at >= end
        )
        clauses.append(and_(CallbackTask.project_id == project.id, due_clause))
    return or_(*clauses) if clauses else false()


@router.get("", response_model=Page[TaskRead])
async def list_tasks(
    session: SessionDep,
    principal: Principal = require_permission("tasks:read"),
    project_id: UUID | None = None,
    task_type: TaskType | None = None,
    status: TaskStatus | None = None,
    priority: TaskPriority | None = None,
    assigned_user_id: UUID | None = None,
    customer_id: UUID | None = None,
    period: TaskPeriod | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Page[TaskRead]:
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    project_ids = await accessible_project_ids(session, principal)
    filters = [
        CallbackTask.tenant_id == principal.tenant_id,
        CallbackTask.project_id.in_(project_ids),
        task_visibility_filter(principal),
    ]
    if project_id is not None:
        project = await resolve_project(session, principal, project_id, active_only=False)
        filters.append(CallbackTask.project_id == project.id)
    if task_type is not None:
        filters.append(CallbackTask.task_type == task_type)
    if status is not None:
        filters.append(CallbackTask.status == status)
    if priority is not None:
        filters.append(CallbackTask.priority == priority)
    if assigned_user_id is not None:
        filters.append(CallbackTask.assigned_user_id == assigned_user_id)
    if customer_id is not None:
        filters.append(CallbackTask.customer_id == customer_id)
    now = datetime.now(UTC)
    if period is not None:
        if period == "overdue":
            filters.extend([CallbackTask.status.in_(ACTIVE_STATUSES), CallbackTask.due_at < now])
        elif period in ("today", "future"):
            filters.extend(
                [
                    CallbackTask.status.in_(ACTIVE_STATUSES),
                    await project_period_clause(
                        session,
                        principal,
                        project_id,
                        project_ids,
                        period,
                        now,
                    ),
                ]
            )
        else:
            filters.append(CallbackTask.status == TaskStatus.COMPLETED)
    if date_from is not None:
        if date_from.tzinfo is None:
            raise ApiError(422, "task_date_from_timezone_required", "date_from требует часовой пояс")
        filters.append(CallbackTask.due_at >= date_from.astimezone(UTC))
    if date_to is not None:
        if date_to.tzinfo is None:
            raise ApiError(422, "task_date_to_timezone_required", "date_to требует часовой пояс")
        filters.append(CallbackTask.due_at <= date_to.astimezone(UTC))
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        filters.append(
            or_(
                CallbackTask.title.ilike(pattern),
                CallbackTask.description.ilike(pattern),
                CallbackTask.comment.ilike(pattern),
                Customer.display_name.ilike(pattern),
                Customer.external_reference.ilike(pattern),
                CustomerContact.normalized_value.ilike(pattern),
            )
        )
    base = task_statement().where(*filters)
    total = int(await session.scalar(select(func.count()).select_from(base.order_by(None).subquery())) or 0)
    rows = (
        await session.execute(
            base.order_by(CallbackTask.due_at, CallbackTask.created_at, CallbackTask.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return Page(
        items=[serialize_task(tuple(row), now=now) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=TaskRead, status_code=201)
async def create_task(
    payload: TaskCreate,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("tasks:create"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=160),
) -> TaskRead:
    project = await resolve_project(session, principal, payload.project_id)
    assigned_user_id = payload.assigned_user_id
    if principal.role == RoleName.HUMAN_OPERATOR:
        if payload.task_type == TaskType.SYSTEM:
            raise ApiError(403, "task_type_forbidden", "Оператор не может создавать системные задачи")
        if assigned_user_id not in (None, principal.user_id):
            raise ApiError(403, "task_reassign_forbidden", "Оператор может назначить задачу только себе")
        assigned_user_id = principal.user_id
    fingerprint, replay = await replay_or_none(
        session,
        principal=principal,
        operation="create",
        payload=payload.model_dump(mode="json"),
        idempotency_key=idempotency_key,
    )
    if replay is not None:
        return replay
    task = await create_task_record(
        session,
        tenant_id=principal.tenant_id,
        project_id=project.id,
        customer_id=payload.customer_id,
        created_by_user_id=principal.user_id,
        task_type=payload.task_type,
        title=payload.title,
        description=payload.description,
        priority=payload.priority,
        due_at=payload.due_at,
        assigned_user_id=assigned_user_id,
        source=TaskSource.MANUAL,
        correlation_id=request.state.correlation_id,
        comment=payload.comment,
        call_id=payload.call_id,
        call_outcome_id=payload.call_outcome_id,
        idempotency_key=idempotency_key,
    )
    if task.task_type == TaskType.CALLBACK:
        customer = await session.get(Customer, task.customer_id)
        if customer is not None:
            await sync_customer_callback_state(session, tenant_id=principal.tenant_id, customer=customer)
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="task.created",
        resource_type="task",
        resource_id=task.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"project_id": str(project.id), "task_type": task.task_type.value},
    )
    return await finish_mutation(
        session,
        principal=principal,
        task=task,
        operation="create",
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
    )


@router.get("/options", response_model=TaskOptions)
async def task_options(
    project_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("tasks:read"),
    search: str | None = None,
) -> TaskOptions:
    project = await resolve_project(session, principal, project_id, active_only=False)
    operator_statement = (
        select(User)
        .join(
            ProjectUser,
            and_(
                ProjectUser.tenant_id == principal.tenant_id,
                ProjectUser.project_id == project.id,
                ProjectUser.user_id == User.id,
            ),
        )
        .join(
            Membership,
            and_(
                Membership.tenant_id == ProjectUser.tenant_id,
                Membership.user_id == ProjectUser.user_id,
            ),
        )
        .where(
            ProjectUser.is_active.is_(True),
            Membership.is_active.is_(True),
            User.is_active.is_(True),
        )
        .order_by(User.display_name, User.id)
    )
    if principal.role == RoleName.HUMAN_OPERATOR:
        operator_statement = operator_statement.where(User.id == principal.user_id)
    operators = list(await session.scalars(operator_statement))

    customer_filters = [
        Customer.tenant_id == principal.tenant_id,
        Customer.project_id == project.id,
        Customer.archived_at.is_(None),
        Customer.is_anonymized.is_(False),
    ]
    if principal.role == RoleName.HUMAN_OPERATOR:
        customer_filters.append(
            or_(
                Customer.assigned_user_id.is_(None),
                Customer.assigned_user_id == principal.user_id,
                Customer.locked_by_user_id == principal.user_id,
            )
        )
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        customer_filters.append(
            or_(
                Customer.display_name.ilike(pattern),
                Customer.external_reference.ilike(pattern),
                CustomerContact.normalized_value.ilike(pattern),
            )
        )
    customer_rows = (
        await session.execute(
            select(Customer, CustomerContact)
            .outerjoin(
                CustomerContact,
                and_(
                    CustomerContact.tenant_id == principal.tenant_id,
                    CustomerContact.project_id == project.id,
                    CustomerContact.customer_id == Customer.id,
                    CustomerContact.kind == "phone",
                    CustomerContact.is_primary.is_(True),
                ),
            )
            .where(*customer_filters)
            .order_by(Customer.display_name, Customer.id)
            .limit(100)
        )
    ).all()
    return TaskOptions(
        operators=[TaskOperatorOption(user_id=user.id, display_name=user.display_name) for user in operators],
        customers=[
            TaskCustomerOption(
                id=customer.id,
                display_name=customer.display_name,
                phone=contact.display_value if isinstance(contact, CustomerContact) else None,
            )
            for customer, contact in customer_rows
        ],
    )


@router.get("/{task_id}", response_model=TaskRead)
async def get_task(
    task_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("tasks:read"),
) -> TaskRead:
    return serialize_task(await load_task_row(session, principal, task_id))


@router.patch("/{task_id}", response_model=TaskRead)
async def update_task(
    task_id: UUID,
    payload: TaskUpdate,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("tasks:manage"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=160),
) -> TaskRead:
    fingerprint, replay = await replay_or_none(
        session,
        principal=principal,
        operation=f"update:{task_id}",
        payload=payload.model_dump(mode="json", exclude_unset=True),
        idempotency_key=idempotency_key,
    )
    if replay is not None:
        return replay
    row = await load_task_row(session, principal, task_id, for_update=True)
    task = row[0]
    if not isinstance(task, CallbackTask):
        raise TypeError("Expected CallbackTask")
    if task.status not in ACTIVE_STATUSES:
        raise ApiError(409, "task_terminal", "Завершённую или отменённую задачу нельзя изменить")
    require_operator_assignment(principal, task)
    changes = payload.model_dump(exclude_unset=True)
    if "priority" in changes and changes["priority"] != task.priority:
        previous = task.priority.value
        task.priority = changes["priority"]
        await append_task_event(
            session,
            task=task,
            event_type=TaskEventType.PRIORITY_CHANGED,
            actor_user_id=principal.user_id,
            correlation_id=request.state.correlation_id,
            safe_snapshot={"from": previous, "to": task.priority.value},
        )
    if "comment" in changes and changes["comment"] != task.comment:
        task.comment = changes["comment"].strip()
        task.note = task.comment
        await append_task_event(
            session,
            task=task,
            event_type=TaskEventType.COMMENT_ADDED,
            actor_user_id=principal.user_id,
            correlation_id=request.state.correlation_id,
            safe_snapshot={"has_comment": bool(task.comment)},
        )
    metadata_changed = False
    for field in ("title", "description"):
        if field in changes and getattr(task, field) != changes[field].strip():
            setattr(task, field, changes[field].strip())
            metadata_changed = True
    if metadata_changed:
        await append_task_event(
            session,
            task=task,
            event_type=TaskEventType.UPDATED,
            actor_user_id=principal.user_id,
            correlation_id=request.state.correlation_id,
            safe_snapshot={"metadata_changed": True},
        )
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="task.updated",
        resource_type="task",
        resource_id=task.id,
        correlation_id=request.state.correlation_id,
    )
    return await finish_mutation(
        session,
        principal=principal,
        task=task,
        operation="update",
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
    )


async def action_context(
    *,
    session: SessionDep,
    principal: Principal,
    task_id: UUID,
    operation: str,
    payload: object,
    idempotency_key: str | None,
) -> tuple[str, TaskRead | None, tuple[object, ...] | None]:
    fingerprint, replay = await replay_or_none(
        session,
        principal=principal,
        operation=f"{operation}:{task_id}",
        payload=payload,
        idempotency_key=idempotency_key,
    )
    if replay is not None:
        return fingerprint, replay, None
    return fingerprint, None, await load_task_row(session, principal, task_id, for_update=True)


async def commit_action(
    *,
    session: SessionDep,
    principal: Principal,
    task: CallbackTask,
    request: Request,
    operation: str,
    idempotency_key: str | None,
    fingerprint: str,
) -> TaskRead:
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action=f"task.{operation}",
        resource_type="task",
        resource_id=task.id,
        correlation_id=request.state.correlation_id,
    )
    return await finish_mutation(
        session,
        principal=principal,
        task=task,
        operation=operation,
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
    )


@router.post("/{task_id}/start", response_model=TaskRead)
async def start_task(
    task_id: UUID,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("tasks:manage"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=160),
) -> TaskRead:
    fingerprint, replay, row = await action_context(
        session=session,
        principal=principal,
        task_id=task_id,
        operation="start",
        payload={},
        idempotency_key=idempotency_key,
    )
    if replay is not None:
        return replay
    assert row is not None
    task = row[0]
    assert isinstance(task, CallbackTask)
    if task.status != TaskStatus.PENDING:
        raise ApiError(409, "task_not_pending", "Начать можно только ожидающую задачу")
    if principal.role == RoleName.HUMAN_OPERATOR:
        if task.assigned_user_id not in (None, principal.user_id):
            raise ApiError(403, "task_not_assigned", "Задача назначена другому оператору")
        if task.assigned_user_id is None:
            task.assigned_user_id = principal.user_id
            await append_task_event(
                session,
                task=task,
                event_type=TaskEventType.ASSIGNED,
                actor_user_id=principal.user_id,
                correlation_id=request.state.correlation_id,
                safe_snapshot={"assigned_user_id": str(principal.user_id)},
            )
    task.status = TaskStatus.IN_PROGRESS
    task.started_at = datetime.now(UTC)
    await append_task_event(
        session,
        task=task,
        event_type=TaskEventType.STARTED,
        actor_user_id=principal.user_id,
        correlation_id=request.state.correlation_id,
    )
    return await commit_action(
        session=session,
        principal=principal,
        task=task,
        request=request,
        operation="started",
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
    )


@router.post("/{task_id}/complete", response_model=TaskRead)
async def complete_task(
    task_id: UUID,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("tasks:manage"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=160),
) -> TaskRead:
    fingerprint, replay, row = await action_context(
        session=session,
        principal=principal,
        task_id=task_id,
        operation="complete",
        payload={},
        idempotency_key=idempotency_key,
    )
    if replay is not None:
        return replay
    assert row is not None
    task = row[0]
    customer = row[2]
    assert isinstance(task, CallbackTask) and isinstance(customer, Customer)
    if task.status != TaskStatus.IN_PROGRESS:
        raise ApiError(409, "task_not_in_progress", "Завершить можно только начатую задачу")
    require_operator_assignment(principal, task)
    task.status = TaskStatus.COMPLETED
    task.completed_at = datetime.now(UTC)
    task.cancelled_at = None
    task.cancellation_reason = None
    await append_task_event(
        session,
        task=task,
        event_type=TaskEventType.COMPLETED,
        actor_user_id=principal.user_id,
        correlation_id=request.state.correlation_id,
    )
    if task.task_type == TaskType.CALLBACK:
        await sync_customer_callback_state(session, tenant_id=principal.tenant_id, customer=customer)
    return await commit_action(
        session=session,
        principal=principal,
        task=task,
        request=request,
        operation="completed",
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
    )


@router.post("/{task_id}/cancel", response_model=TaskRead)
async def cancel_task(
    task_id: UUID,
    payload: TaskCancelRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("tasks:manage"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=160),
) -> TaskRead:
    if not is_manager(principal):
        raise ApiError(403, "task_cancel_forbidden", "Отменять задачи может руководитель")
    fingerprint, replay, row = await action_context(
        session=session,
        principal=principal,
        task_id=task_id,
        operation="cancel",
        payload=payload.model_dump(mode="json"),
        idempotency_key=idempotency_key,
    )
    if replay is not None:
        return replay
    assert row is not None
    task = row[0]
    customer = row[2]
    assert isinstance(task, CallbackTask) and isinstance(customer, Customer)
    if task.status not in ACTIVE_STATUSES:
        raise ApiError(409, "task_terminal", "Задача уже завершена или отменена")
    await cancel_task_record(
        session,
        task=task,
        actor_user_id=principal.user_id,
        reason=payload.reason,
        correlation_id=request.state.correlation_id,
    )
    if task.task_type == TaskType.CALLBACK:
        await sync_customer_callback_state(session, tenant_id=principal.tenant_id, customer=customer)
    return await commit_action(
        session=session,
        principal=principal,
        task=task,
        request=request,
        operation="cancelled",
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
    )


@router.post("/{task_id}/restore", response_model=TaskRead)
async def restore_task(
    task_id: UUID,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("tasks:manage"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=160),
) -> TaskRead:
    if not is_manager(principal):
        raise ApiError(403, "task_restore_forbidden", "Восстанавливать задачи может руководитель")
    fingerprint, replay, row = await action_context(
        session=session,
        principal=principal,
        task_id=task_id,
        operation="restore",
        payload={},
        idempotency_key=idempotency_key,
    )
    if replay is not None:
        return replay
    assert row is not None
    task = row[0]
    customer = row[2]
    assert isinstance(task, CallbackTask) and isinstance(customer, Customer)
    if task.status != TaskStatus.CANCELLED:
        raise ApiError(409, "task_not_cancelled", "Восстановить можно только отменённую задачу")
    task.status = TaskStatus.PENDING
    task.started_at = None
    task.cancelled_at = None
    task.cancellation_reason = None
    await append_task_event(
        session,
        task=task,
        event_type=TaskEventType.RESTORED,
        actor_user_id=principal.user_id,
        correlation_id=request.state.correlation_id,
    )
    if task.task_type == TaskType.CALLBACK:
        await sync_customer_callback_state(session, tenant_id=principal.tenant_id, customer=customer)
    return await commit_action(
        session=session,
        principal=principal,
        task=task,
        request=request,
        operation="restored",
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
    )


@router.post("/{task_id}/reschedule", response_model=TaskRead)
async def reschedule_task(
    task_id: UUID,
    payload: TaskRescheduleRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("tasks:manage"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=160),
) -> TaskRead:
    fingerprint, replay, row = await action_context(
        session=session,
        principal=principal,
        task_id=task_id,
        operation="reschedule",
        payload=payload.model_dump(mode="json"),
        idempotency_key=idempotency_key,
    )
    if replay is not None:
        return replay
    assert row is not None
    task = row[0]
    customer = row[2]
    assert isinstance(task, CallbackTask) and isinstance(customer, Customer)
    if task.status not in ACTIVE_STATUSES:
        raise ApiError(409, "task_terminal", "Завершённую или отменённую задачу нельзя перенести")
    if principal.role == RoleName.HUMAN_OPERATOR:
        require_operator_assignment(principal, task)
        if task.task_type != TaskType.CALLBACK:
            raise ApiError(403, "task_reschedule_forbidden", "Оператор может переносить только перезвоны")
    previous_due = task.due_at
    task.due_at = utc_due_at(payload.due_at)
    task.status = TaskStatus.PENDING
    task.started_at = None
    await append_task_event(
        session,
        task=task,
        event_type=TaskEventType.RESCHEDULED,
        actor_user_id=principal.user_id,
        correlation_id=request.state.correlation_id,
        safe_snapshot={"from": previous_due.isoformat(), "to": task.due_at.isoformat()},
    )
    if task.task_type == TaskType.CALLBACK:
        await sync_customer_callback_state(session, tenant_id=principal.tenant_id, customer=customer)
    return await commit_action(
        session=session,
        principal=principal,
        task=task,
        request=request,
        operation="rescheduled",
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
    )


@router.post("/{task_id}/reassign", response_model=TaskRead)
async def reassign_task(
    task_id: UUID,
    payload: TaskReassignRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("tasks:manage"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", min_length=8, max_length=160),
) -> TaskRead:
    if not is_manager(principal):
        raise ApiError(403, "task_reassign_forbidden", "Переназначать задачи может руководитель")
    fingerprint, replay, row = await action_context(
        session=session,
        principal=principal,
        task_id=task_id,
        operation="reassign",
        payload=payload.model_dump(mode="json"),
        idempotency_key=idempotency_key,
    )
    if replay is not None:
        return replay
    assert row is not None
    task = row[0]
    assert isinstance(task, CallbackTask)
    if task.status not in ACTIVE_STATUSES:
        raise ApiError(409, "task_terminal", "Завершённую или отменённую задачу нельзя переназначить")
    await validate_task_assignee(
        session,
        tenant_id=principal.tenant_id,
        project_id=task.project_id,
        assigned_user_id=payload.assigned_user_id,
    )
    previous = task.assigned_user_id
    task.assigned_user_id = payload.assigned_user_id
    await append_task_event(
        session,
        task=task,
        event_type=(TaskEventType.ASSIGNED if previous is None else TaskEventType.REASSIGNED),
        actor_user_id=principal.user_id,
        correlation_id=request.state.correlation_id,
        safe_snapshot={
            "from": str(previous) if previous else None,
            "to": str(payload.assigned_user_id) if payload.assigned_user_id else None,
        },
    )
    return await commit_action(
        session=session,
        principal=principal,
        task=task,
        request=request,
        operation="reassigned",
        idempotency_key=idempotency_key,
        fingerprint=fingerprint,
    )


@router.get("/{task_id}/events", response_model=list[TaskEventRead])
async def list_task_events(
    task_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("tasks:read"),
) -> list[TaskEventRead]:
    await load_task_row(session, principal, task_id)
    rows = (
        await session.execute(
            select(TaskEvent, EventActor)
            .outerjoin(EventActor, EventActor.id == TaskEvent.actor_user_id)
            .where(
                TaskEvent.tenant_id == principal.tenant_id,
                TaskEvent.task_id == task_id,
            )
            .order_by(TaskEvent.created_at, TaskEvent.id)
        )
    ).all()
    return [
        TaskEventRead(
            id=event.id,
            task_id=event.task_id,
            event_type=event.event_type,
            actor_user_id=event.actor_user_id,
            actor_name=actor.display_name if isinstance(actor, User) else None,
            safe_snapshot=event.safe_snapshot,
            created_at=event.created_at,
        )
        for event, actor in rows
    ]
