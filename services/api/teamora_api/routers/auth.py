from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from teamora_api.audit import write_audit
from teamora_api.call_result_service import seed_default_definitions
from teamora_api.config import get_settings
from teamora_api.db import set_tenant_context
from teamora_api.dependencies import PrincipalDep, SessionDep
from teamora_api.enums import LanguageCode, LanguageReadiness, RoleName, TenantStatus
from teamora_api.errors import ApiError
from teamora_api.models import (
    CallResultCatalog,
    CustomerFieldDefinition,
    LanguageConfiguration,
    Membership,
    Project,
    ProjectUser,
    RefreshToken,
    Tenant,
    TenantSettings,
    User,
)
from teamora_api.schemas.auth import AuthResponse, AuthTenant, AuthUser, LoginRequest, RegisterRequest
from teamora_api.security import (
    REFRESH_COOKIE,
    clear_auth_cookies,
    create_access_token,
    hash_password,
    hash_token,
    new_csrf_token,
    new_refresh_token,
    refresh_tenant_id,
    set_auth_cookies,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def auth_response(user: User, tenant: Tenant, membership: Membership, csrf: str) -> AuthResponse:
    return AuthResponse(
        user=AuthUser(id=user.id, email=user.email, display_name=user.display_name, role=membership.role),
        tenant=AuthTenant(id=tenant.id, name=tenant.name, slug=tenant.slug),
        csrf_token=csrf,
    )


async def issue_session(
    *, response: Response, session: SessionDep, user: User, tenant: Tenant, membership: Membership
) -> str:
    settings = get_settings()
    access = create_access_token(
        user_id=user.id,
        tenant_id=tenant.id,
        membership_id=membership.id,
        role=membership.role,
        settings=settings,
    )
    refresh = new_refresh_token(tenant.id)
    csrf = new_csrf_token()
    session.add(
        RefreshToken(
            tenant_id=tenant.id,
            user_id=user.id,
            membership_id=membership.id,
            token_hash=hash_token(refresh),
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_ttl_days),
        )
    )
    set_auth_cookies(response, access=access, refresh=refresh, csrf=csrf, settings=settings)
    return csrf


@router.post("/register", response_model=AuthResponse, status_code=201)
async def register(
    payload: RegisterRequest, request: Request, response: Response, session: SessionDep
) -> AuthResponse:
    email = payload.email.lower()
    async with session.begin():
        if await session.scalar(select(Tenant.id).where(Tenant.slug == payload.company_slug)):
            raise ApiError(409, "tenant_slug_taken", "This company workspace is already registered")
        if await session.scalar(select(User.id).where(User.email == email)):
            raise ApiError(409, "email_registered", "This email is already registered")

        tenant = Tenant(name=payload.company_name, slug=payload.company_slug, status=TenantStatus.ACTIVE)
        session.add(tenant)
        await session.flush()
        await set_tenant_context(session, tenant.id)

        user = User(
            email=email, display_name=payload.display_name, password_hash=hash_password(payload.password)
        )
        session.add(user)
        await session.flush()
        membership = Membership(tenant_id=tenant.id, user_id=user.id, role=RoleName.TENANT_OWNER)
        session.add(membership)
        project = Project(
            tenant_id=tenant.id,
            name="Основной проект",
            description="Основной проект компании",
            status="active",
            max_concurrent_calls=None,
            working_hours={},
            is_default=True,
        )
        session.add(project)
        await session.flush()
        catalog = CallResultCatalog(
            tenant_id=tenant.id,
            project_id=project.id,
            name="Результаты звонка",
            is_active=True,
        )
        session.add(catalog)
        await session.flush()
        await seed_default_definitions(
            session,
            tenant_id=tenant.id,
            project_id=project.id,
            catalog_id=catalog.id,
        )
        session.add(
            ProjectUser(
                tenant_id=tenant.id,
                project_id=project.id,
                user_id=user.id,
                is_active=True,
            )
        )
        session.add(
            CustomerFieldDefinition(
                tenant_id=tenant.id,
                project_id=project.id,
                name="Сегмент",
                key="segment",
                field_type="text",
                is_required=False,
                sort_order=0,
                options=[],
                is_active=True,
            )
        )
        session.add(
            TenantSettings(
                tenant_id=tenant.id,
                default_language=LanguageCode.RU,
                max_concurrent_calls=get_settings().default_max_concurrent_calls,
            )
        )
        language_rows = [
            (LanguageCode.RU, LanguageReadiness.PRODUCTION, True),
            (LanguageCode.EN, LanguageReadiness.PRODUCTION, True),
            (LanguageCode.UZ, LanguageReadiness.BETA, True),
            (LanguageCode.KAA, LanguageReadiness.EXPERIMENTAL, get_settings().karakalpak_experimental),
        ]
        session.add_all(
            [
                LanguageConfiguration(
                    tenant_id=tenant.id,
                    code=code,
                    readiness=readiness,
                    is_enabled=enabled,
                )
                for code, readiness, enabled in language_rows
            ]
        )
        await session.flush()
        csrf = await issue_session(
            response=response, session=session, user=user, tenant=tenant, membership=membership
        )
        await write_audit(
            session,
            tenant_id=tenant.id,
            actor_user_id=user.id,
            action="tenant.registered",
            resource_type="tenant",
            resource_id=tenant.id,
            correlation_id=request.state.correlation_id,
        )
    return auth_response(user, tenant, membership, csrf)


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest, request: Request, response: Response, session: SessionDep
) -> AuthResponse:
    tenant = await session.scalar(
        select(Tenant).where(Tenant.slug == payload.company_slug, Tenant.status == TenantStatus.ACTIVE)
    )
    if tenant is None:
        raise ApiError(401, "credentials_invalid", "Company, email, or password is invalid")
    await set_tenant_context(session, tenant.id)
    row = (
        await session.execute(
            select(User, Membership)
            .join(Membership, Membership.user_id == User.id)
            .where(
                User.email == payload.email.lower(),
                User.is_active.is_(True),
                Membership.tenant_id == tenant.id,
                Membership.is_active.is_(True),
            )
        )
    ).one_or_none()
    if row is None or not verify_password(payload.password, row.User.password_hash):
        raise ApiError(401, "credentials_invalid", "Company, email, or password is invalid")
    user, membership = row
    async with session.begin_nested():
        csrf = await issue_session(
            response=response, session=session, user=user, tenant=tenant, membership=membership
        )
        await write_audit(
            session,
            tenant_id=tenant.id,
            actor_user_id=user.id,
            action="auth.login",
            resource_type="session",
            resource_id=None,
            correlation_id=request.state.correlation_id,
        )
    await session.commit()
    return auth_response(user, tenant, membership, csrf)


@router.post("/refresh", response_model=AuthResponse)
async def refresh(request: Request, response: Response, session: SessionDep) -> AuthResponse:
    raw_refresh = request.cookies.get(REFRESH_COOKIE, "")
    tenant_id = refresh_tenant_id(raw_refresh)
    if tenant_id is None:
        raise ApiError(401, "refresh_invalid", "Refresh token is invalid")
    await set_tenant_context(session, tenant_id)
    now = datetime.now(UTC)
    token = await session.scalar(
        select(RefreshToken).where(
            RefreshToken.tenant_id == tenant_id,
            RefreshToken.token_hash == hash_token(raw_refresh),
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > now,
        )
    )
    if token is None:
        raise ApiError(401, "refresh_invalid", "Refresh token is invalid")
    row = (
        await session.execute(
            select(User, Membership, Tenant)
            .join(Membership, Membership.user_id == User.id)
            .join(Tenant, Tenant.id == Membership.tenant_id)
            .where(
                Membership.id == token.membership_id,
                Membership.tenant_id == tenant_id,
                Membership.is_active.is_(True),
                User.id == token.user_id,
                User.is_active.is_(True),
                Tenant.status == TenantStatus.ACTIVE,
            )
        )
    ).one_or_none()
    if row is None:
        raise ApiError(401, "refresh_invalid", "Refresh token is invalid")
    user, membership, tenant = row
    async with session.begin_nested():
        token.revoked_at = now
        csrf = await issue_session(
            response=response, session=session, user=user, tenant=tenant, membership=membership
        )
        await write_audit(
            session,
            tenant_id=tenant.id,
            actor_user_id=user.id,
            action="auth.refresh_rotated",
            resource_type="refresh_token",
            resource_id=token.id,
            correlation_id=request.state.correlation_id,
        )
    await session.commit()
    return auth_response(user, tenant, membership, csrf)


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, session: SessionDep) -> None:
    raw_refresh = request.cookies.get(REFRESH_COOKIE, "")
    tenant_id = refresh_tenant_id(raw_refresh)
    if tenant_id is not None:
        await set_tenant_context(session, tenant_id)
        token = await session.scalar(
            select(RefreshToken).where(
                RefreshToken.tenant_id == tenant_id,
                RefreshToken.token_hash == hash_token(raw_refresh),
                RefreshToken.revoked_at.is_(None),
            )
        )
        if token is not None:
            token.revoked_at = datetime.now(UTC)
            await session.commit()
    clear_auth_cookies(response, get_settings())


@router.get("/me", response_model=AuthResponse)
async def me(principal: PrincipalDep) -> AuthResponse:
    return AuthResponse(
        user=AuthUser(
            id=principal.user_id,
            email=principal.email,
            display_name=principal.display_name,
            role=principal.role,
        ),
        tenant=AuthTenant(id=principal.tenant_id, name=principal.tenant_name, slug=principal.tenant_slug),
        csrf_token="",
    )
