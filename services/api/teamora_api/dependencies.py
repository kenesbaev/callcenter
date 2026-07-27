from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from teamora_api.config import Settings, get_settings
from teamora_api.db import get_session, set_tenant_context
from teamora_api.enums import RoleName, TenantStatus
from teamora_api.errors import ApiError
from teamora_api.models import Membership, Tenant, User
from teamora_api.rbac import role_has_permission
from teamora_api.security import ACCESS_COOKIE, decode_access_token


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    tenant_id: UUID
    membership_id: UUID
    role: RoleName
    display_name: str
    email: str
    tenant_name: str
    tenant_slug: str


SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_current_principal(request: Request, session: SessionDep, settings: SettingsDep) -> Principal:
    token = request.cookies.get(ACCESS_COOKIE)
    claims = decode_access_token(token or "", settings)
    if claims is None:
        raise ApiError(401, "authentication_required", "Authentication is required")

    await set_tenant_context(session, claims.tenant_id)
    statement = (
        select(User, Membership, Tenant)
        .join(Membership, Membership.user_id == User.id)
        .join(Tenant, Tenant.id == Membership.tenant_id)
        .where(
            User.id == claims.user_id,
            User.is_active.is_(True),
            Membership.id == claims.membership_id,
            Membership.tenant_id == claims.tenant_id,
            Membership.is_active.is_(True),
            Membership.role == claims.role,
            Tenant.status == TenantStatus.ACTIVE,
        )
    )
    row = (await session.execute(statement)).one_or_none()
    if row is None:
        raise ApiError(401, "session_invalid", "The session is no longer valid")
    user, membership, tenant = row
    principal = Principal(
        user_id=user.id,
        tenant_id=tenant.id,
        membership_id=membership.id,
        role=membership.role,
        display_name=user.display_name,
        email=user.email,
        tenant_name=tenant.name,
        tenant_slug=tenant.slug,
    )
    request.state.principal = principal
    return principal


PrincipalDep = Annotated[Principal, Depends(get_current_principal)]


def require_permission(permission: str) -> Any:
    async def dependency(principal: PrincipalDep) -> Principal:
        if not role_has_permission(principal.role, permission):
            raise ApiError(403, "permission_denied", "You do not have permission for this action")
        return principal

    return Depends(dependency)
