from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import delete, func, select

from teamora_api.audit import write_audit
from teamora_api.dependencies import Principal, SessionDep, require_permission
from teamora_api.errors import ApiError
from teamora_api.models import Membership, Project, ProjectUser
from teamora_api.project_access import accessible_projects_statement, resolve_project
from teamora_api.schemas.common import Page
from teamora_api.schemas.projects import ProjectCreate, ProjectRead, ProjectUpdate

router = APIRouter(prefix="/projects", tags=["projects"])


def serialize_project(project: Project, operator_user_ids: list[UUID]) -> ProjectRead:
    return ProjectRead(
        id=project.id,
        name=project.name,
        description=project.description,
        status=project.status,  # type: ignore[arg-type]
        outbound_number=project.outbound_number,
        max_concurrent_calls=project.max_concurrent_calls,
        working_hours=project.working_hours,
        is_default=project.is_default,
        operator_user_ids=operator_user_ids,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


async def project_users_by_project(
    session: SessionDep,
    tenant_id: UUID,
    project_ids: list[UUID],
) -> dict[UUID, list[UUID]]:
    grouped: dict[UUID, list[UUID]] = defaultdict(list)
    if not project_ids:
        return grouped
    rows = await session.execute(
        select(ProjectUser.project_id, ProjectUser.user_id).where(
            ProjectUser.tenant_id == tenant_id,
            ProjectUser.project_id.in_(project_ids),
            ProjectUser.is_active.is_(True),
        )
    )
    for project_id, user_id in rows:
        grouped[project_id].append(user_id)
    return grouped


async def replace_project_users(
    session: SessionDep,
    *,
    tenant_id: UUID,
    project_id: UUID,
    user_ids: list[UUID],
) -> None:
    unique_user_ids = set(user_ids)
    if unique_user_ids:
        valid_ids = set(
            await session.scalars(
                select(Membership.user_id).where(
                    Membership.tenant_id == tenant_id,
                    Membership.user_id.in_(unique_user_ids),
                    Membership.is_active.is_(True),
                )
            )
        )
        if valid_ids != unique_user_ids:
            raise ApiError(
                422,
                "project_operator_invalid",
                "Один или несколько пользователей не входят в активную команду компании",
            )
    await session.execute(
        delete(ProjectUser).where(
            ProjectUser.tenant_id == tenant_id,
            ProjectUser.project_id == project_id,
        )
    )
    session.add_all(
        [
            ProjectUser(
                tenant_id=tenant_id,
                project_id=project_id,
                user_id=user_id,
                is_active=True,
            )
            for user_id in unique_user_ids
        ]
    )


@router.get("", response_model=Page[ProjectRead])
async def list_projects(
    session: SessionDep,
    principal: Principal = require_permission("projects:read"),
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Page[ProjectRead]:
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    statement = accessible_projects_statement(principal)
    if status:
        statement = statement.where(Project.status == status)
    subquery = statement.with_only_columns(Project.id).order_by(None).subquery()
    total = int(await session.scalar(select(func.count()).select_from(subquery)) or 0)
    projects = list(
        await session.scalars(
            statement.order_by(Project.is_default.desc(), Project.created_at).limit(limit).offset(offset)
        )
    )
    users = await project_users_by_project(
        session,
        principal.tenant_id,
        [project.id for project in projects],
    )
    return Page(
        items=[serialize_project(project, users[project.id]) for project in projects],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=ProjectRead, status_code=201)
async def create_project(
    payload: ProjectCreate,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("projects:manage"),
) -> ProjectRead:
    name = payload.name.strip()
    if await session.scalar(
        select(Project.id).where(
            Project.tenant_id == principal.tenant_id,
            func.lower(Project.name) == name.lower(),
        )
    ):
        raise ApiError(409, "project_name_taken", "Проект с таким названием уже существует")
    project = Project(
        tenant_id=principal.tenant_id,
        name=name,
        description=payload.description.strip(),
        status=payload.status,
        outbound_number=payload.outbound_number,
        max_concurrent_calls=payload.max_concurrent_calls,
        working_hours=payload.working_hours,
        is_default=False,
    )
    session.add(project)
    await session.flush()
    await replace_project_users(
        session,
        tenant_id=principal.tenant_id,
        project_id=project.id,
        user_ids=payload.operator_user_ids,
    )
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="project.created",
        resource_type="project",
        resource_id=project.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"status": project.status},
    )
    await session.commit()
    return serialize_project(project, list(dict.fromkeys(payload.operator_user_ids)))


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project(
    project_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("projects:read"),
) -> ProjectRead:
    project = await resolve_project(session, principal, project_id, active_only=False)
    users = await project_users_by_project(session, principal.tenant_id, [project.id])
    return serialize_project(project, users[project.id])


@router.patch("/{project_id}", response_model=ProjectRead)
async def update_project(
    project_id: UUID,
    payload: ProjectUpdate,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("projects:manage"),
) -> ProjectRead:
    project = await resolve_project(session, principal, project_id, active_only=False, for_update=True)
    if payload.status == "archived" and project.is_default:
        raise ApiError(409, "default_project_required", "Основной проект нельзя архивировать")
    if payload.name is not None:
        name = payload.name.strip()
        duplicate = await session.scalar(
            select(Project.id).where(
                Project.tenant_id == principal.tenant_id,
                Project.id != project.id,
                func.lower(Project.name) == name.lower(),
            )
        )
        if duplicate:
            raise ApiError(409, "project_name_taken", "Проект с таким названием уже существует")
        project.name = name
    for field in (
        "description",
        "status",
        "outbound_number",
        "max_concurrent_calls",
        "working_hours",
    ):
        if field in payload.model_fields_set:
            value = getattr(payload, field)
            if field == "description" and value is not None:
                value = value.strip()
            setattr(project, field, value)
    if payload.operator_user_ids is not None:
        await replace_project_users(
            session,
            tenant_id=principal.tenant_id,
            project_id=project.id,
            user_ids=payload.operator_user_ids,
        )
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="project.updated",
        resource_type="project",
        resource_id=project.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"fields": sorted(payload.model_fields_set)},
    )
    users = await project_users_by_project(session, principal.tenant_id, [project.id])
    response = serialize_project(project, users[project.id])
    await session.commit()
    return response
