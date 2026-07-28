from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, select

from teamora_api.dependencies import Principal, SessionDep
from teamora_api.enums import RoleName
from teamora_api.errors import ApiError
from teamora_api.models import Project, ProjectUser

PROJECT_WIDE_ROLES = {
    RoleName.TENANT_OWNER,
    RoleName.TENANT_MANAGER,
    RoleName.ANALYST,
}


def accessible_projects_statement(
    principal: Principal,
    *,
    active_only: bool = False,
) -> Select[tuple[Project]]:
    statement = select(Project).where(Project.tenant_id == principal.tenant_id)
    if principal.role not in PROJECT_WIDE_ROLES:
        statement = statement.join(
            ProjectUser,
            (ProjectUser.project_id == Project.id) & (ProjectUser.tenant_id == principal.tenant_id),
        ).where(
            ProjectUser.user_id == principal.user_id,
            ProjectUser.is_active.is_(True),
        )
    if active_only:
        statement = statement.where(Project.status == "active")
    return statement


async def resolve_project(
    session: SessionDep,
    principal: Principal,
    project_id: UUID | None = None,
    *,
    active_only: bool = True,
    for_update: bool = False,
) -> Project:
    statement = accessible_projects_statement(principal, active_only=active_only)
    if project_id is not None:
        statement = statement.where(Project.id == project_id)
    else:
        statement = statement.order_by(Project.is_default.desc(), Project.created_at)
    if for_update:
        statement = statement.with_for_update(of=Project)
    project = await session.scalar(statement.limit(1))
    if project is None:
        code = "project_not_available" if active_only else "project_not_found"
        raise ApiError(404, code, "Проект не найден или недоступен пользователю")
    return project


async def accessible_project_ids(session: SessionDep, principal: Principal) -> list[UUID]:
    return list(await session.scalars(accessible_projects_statement(principal).with_only_columns(Project.id)))
