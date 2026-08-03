from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel
from sqlalchemy import and_, delete, func, or_, select

from teamora_api.audit import write_audit
from teamora_api.call_state import CAPACITY_CALL_STATES
from teamora_api.config import get_settings
from teamora_api.db import set_tenant_context
from teamora_api.dependencies import Principal, PrincipalDep, SessionDep, require_permission
from teamora_api.enums import InvitationStatus, QueueStatus, RoleName
from teamora_api.errors import ApiError
from teamora_api.models import (
    AuditLog,
    Call,
    HumanOperator,
    Invitation,
    InvitationProject,
    Membership,
    OperatorPresence,
    OperatorStatus,
    Project,
    ProjectUser,
    TeamCommandSubmission,
    Tenant,
    User,
)
from teamora_api.project_access import resolve_project
from teamora_api.realtime import enqueue_realtime_event
from teamora_api.schemas.common import Page
from teamora_api.schemas.team import (
    InvitationAccept,
    InvitationAcceptResponse,
    InvitationCreate,
    InvitationRead,
    OperatorHeartbeat,
    OperatorPresenceRead,
    OperatorStatusUpdate,
    OwnershipTransferRequest,
    TeamAuditRead,
    TeamBlockRequest,
    TeamMemberRead,
    TeamProfileUpdate,
    TeamProjectRead,
    TeamProjectsUpdate,
    TeamRoleUpdate,
    TeamVersionRequest,
    TransferCandidateRead,
)
from teamora_api.security import hash_password, hash_token, verify_password
from teamora_api.team_service import (
    DIALER_ROLES,
    MANUAL_OPERATOR_STATUSES,
    PRESENCE_TTL_SECONDS,
    assert_expected_version,
    ensure_member_management_allowed,
    ensure_operator_profile,
    get_operator_state,
    lock_tenant_memberships,
    touch_presence,
)

router = APIRouter(prefix="/team", tags=["team"])
TEAM_ROLES = {
    RoleName.TENANT_OWNER,
    RoleName.TENANT_MANAGER,
    RoleName.HUMAN_OPERATOR,
    RoleName.ANALYST,
}


def request_fingerprint(payload: object) -> str:
    body = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


async def get_member(
    session: SessionDep,
    *,
    tenant_id: UUID,
    membership_id: UUID,
    for_update: bool = False,
) -> Membership:
    statement = select(Membership).where(
        Membership.tenant_id == tenant_id,
        Membership.id == membership_id,
    )
    if for_update:
        statement = statement.with_for_update()
    membership = await session.scalar(statement)
    if membership is None:
        raise ApiError(404, "team_member_not_found", "Сотрудник не найден")
    return membership


async def member_projects(session: SessionDep, tenant_id: UUID, user_id: UUID) -> list[TeamProjectRead]:
    rows = (
        await session.execute(
            select(Project.id, Project.name)
            .join(
                ProjectUser,
                and_(
                    ProjectUser.tenant_id == Project.tenant_id,
                    ProjectUser.project_id == Project.id,
                ),
            )
            .where(
                Project.tenant_id == tenant_id,
                ProjectUser.user_id == user_id,
                ProjectUser.is_active.is_(True),
            )
            .order_by(Project.name)
        )
    ).all()
    return [TeamProjectRead(id=project_id, name=name) for project_id, name in rows]


async def serialize_member(
    session: SessionDep,
    membership: Membership,
    user: User | None = None,
) -> TeamMemberRead:
    if user is None:
        user = await session.scalar(select(User).where(User.id == membership.user_id))
    if user is None:
        raise ApiError(500, "team_user_missing", "Профиль сотрудника повреждён")
    operator = await session.scalar(
        select(HumanOperator).where(
            HumanOperator.tenant_id == membership.tenant_id,
            HumanOperator.membership_id == membership.id,
        )
    )
    status = (
        await session.scalar(
            select(OperatorStatus).where(
                OperatorStatus.tenant_id == membership.tenant_id,
                OperatorStatus.human_operator_id == operator.id,
            )
        )
        if operator is not None
        else None
    )
    state = await get_operator_state(
        session,
        tenant_id=membership.tenant_id,
        membership=membership,
        operator=operator,
        status=status,
    )
    return TeamMemberRead(
        membership_id=membership.id,
        user_id=user.id,
        display_name=user.display_name,
        email=user.email,
        phone=membership.phone,
        job_title=membership.job_title,
        role=membership.role,
        is_active=membership.is_active,
        extension=operator.extension if operator else None,
        interface_language=membership.interface_language,
        timezone=membership.timezone,
        invited_at=membership.invited_at,
        activated_at=membership.activated_at,
        last_login_at=user.last_login_at,
        last_heartbeat_at=state.last_heartbeat_at,
        blocked_at=membership.blocked_at,
        blocked_reason=membership.blocked_reason,
        manual_status=state.manual_status,
        effective_status=state.effective_status,
        is_transfer_available=operator.is_transfer_available if operator else False,
        current_call_id=state.current_call_id,
        projects=await member_projects(session, membership.tenant_id, membership.user_id),
        state_version=membership.state_version,
    )


async def validate_projects(session: SessionDep, tenant_id: UUID, project_ids: list[UUID]) -> set[UUID]:
    requested = set(project_ids)
    if not requested:
        return set()
    found = set(
        await session.scalars(
            select(Project.id).where(
                Project.tenant_id == tenant_id,
                Project.id.in_(requested),
                Project.status != "archived",
            )
        )
    )
    if found != requested:
        raise ApiError(422, "team_project_invalid", "Один или несколько проектов недоступны")
    return found


async def replace_assignments(
    session: SessionDep,
    *,
    tenant_id: UUID,
    user_id: UUID,
    project_ids: set[UUID],
) -> None:
    await session.execute(
        delete(ProjectUser).where(
            ProjectUser.tenant_id == tenant_id,
            ProjectUser.user_id == user_id,
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
            for project_id in sorted(project_ids, key=str)
        ]
    )


def ensure_team_reader(principal: Principal) -> None:
    if principal.role not in TEAM_ROLES:
        raise ApiError(403, "permission_denied", "Недостаточно прав для просмотра команды")


def ensure_team_manager(principal: Principal) -> None:
    if principal.role not in {RoleName.TENANT_OWNER, RoleName.TENANT_MANAGER}:
        raise ApiError(403, "permission_denied", "Недостаточно прав для управления командой")


@router.get("", response_model=Page[TeamMemberRead])
async def list_team(
    session: SessionDep,
    principal: PrincipalDep,
    search: str | None = None,
    role: RoleName | None = None,
    project_id: UUID | None = None,
    effective_status: str | None = None,
    active: bool | None = None,
    limit: int = 25,
    offset: int = 0,
) -> Page[TeamMemberRead]:
    ensure_team_reader(principal)
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    statement = (
        select(Membership, User)
        .join(User, User.id == Membership.user_id)
        .where(Membership.tenant_id == principal.tenant_id)
    )
    if principal.role == RoleName.HUMAN_OPERATOR:
        statement = statement.where(Membership.id == principal.membership_id)
    if search:
        pattern = f"%{search.strip()}%"
        statement = statement.where(
            or_(User.display_name.ilike(pattern), User.email.ilike(pattern), Membership.phone.ilike(pattern))
        )
    if role is not None:
        statement = statement.where(Membership.role == role)
    if active is not None:
        statement = statement.where(Membership.is_active.is_(active))
    if project_id is not None:
        await resolve_project(session, principal, project_id, active_only=False)
        statement = statement.join(
            ProjectUser,
            and_(
                ProjectUser.tenant_id == Membership.tenant_id,
                ProjectUser.user_id == Membership.user_id,
            ),
        ).where(ProjectUser.project_id == project_id, ProjectUser.is_active.is_(True))
    rows = (await session.execute(statement.order_by(User.display_name, Membership.id))).all()
    items = [await serialize_member(session, membership, user) for membership, user in rows]
    if effective_status:
        items = [item for item in items if item.effective_status == effective_status]
    total = len(items)
    await session.flush()
    return Page(items=items[offset : offset + limit], total=total, limit=limit, offset=offset)


@router.get("/me", response_model=TeamMemberRead)
async def own_team_profile(session: SessionDep, principal: PrincipalDep) -> TeamMemberRead:
    membership = await get_member(
        session, tenant_id=principal.tenant_id, membership_id=principal.membership_id
    )
    result = await serialize_member(session, membership)
    await session.flush()
    return result


@router.get("/presence", response_model=OperatorPresenceRead)
async def own_presence(session: SessionDep, principal: PrincipalDep) -> OperatorPresenceRead:
    membership = await get_member(
        session, tenant_id=principal.tenant_id, membership_id=principal.membership_id
    )
    operator, status = await ensure_operator_profile(session, membership)
    state = await get_operator_state(
        session,
        tenant_id=principal.tenant_id,
        membership=membership,
        operator=operator,
        status=status,
    )
    await session.flush()
    return OperatorPresenceRead(
        manual_status=state.manual_status,
        effective_status=state.effective_status,
        last_heartbeat_at=state.last_heartbeat_at,
        heartbeat_ttl_seconds=PRESENCE_TTL_SECONDS,
        current_call_id=state.current_call_id,
    )


@router.post("/presence/heartbeat", response_model=OperatorPresenceRead)
async def heartbeat_presence(
    payload: OperatorHeartbeat,
    session: SessionDep,
    principal: PrincipalDep,
) -> OperatorPresenceRead:
    membership = await get_member(
        session,
        tenant_id=principal.tenant_id,
        membership_id=principal.membership_id,
        for_update=True,
    )
    await ensure_operator_profile(session, membership)
    previous_seen_at = membership.presence_last_seen_at
    await touch_presence(session, membership, session_key=payload.session_key)
    if previous_seen_at is None or previous_seen_at < datetime.now(UTC) - timedelta(
        seconds=PRESENCE_TTL_SECONDS
    ):
        await enqueue_realtime_event(
            session,
            tenant_id=principal.tenant_id,
            target_membership_id=principal.membership_id,
            event_type="operator.presence_changed",
            aggregate_type="membership",
            aggregate_id=membership.id,
            aggregate_version=membership.state_version,
            payload={"presence": "online"},
        )
    result = await own_presence(session, principal)
    await session.commit()
    return result


@router.put("/presence/status", response_model=OperatorPresenceRead)
async def update_own_status(
    payload: OperatorStatusUpdate,
    request: Request,
    session: SessionDep,
    principal: PrincipalDep,
) -> OperatorPresenceRead:
    membership = await get_member(
        session,
        tenant_id=principal.tenant_id,
        membership_id=principal.membership_id,
        for_update=True,
    )
    operator, status = await ensure_operator_profile(session, membership)
    if operator is None or status is None:
        raise ApiError(403, "operator_status_forbidden", "Для этой роли рабочий статус недоступен")
    requested = QueueStatus(payload.status)
    if requested not in MANUAL_OPERATOR_STATUSES:
        raise ApiError(422, "operator_status_invalid", "Системный статус нельзя установить вручную")
    await touch_presence(session, membership, session_key=payload.session_key)
    changed = status.status != requested
    status.status = requested
    status.changed_at = datetime.now(UTC)
    if changed:
        await write_audit(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="operator.manual_status_changed",
            resource_type="membership",
            resource_id=membership.id,
            correlation_id=request.state.correlation_id,
            safe_metadata={"status": requested.value},
        )
        await enqueue_realtime_event(
            session,
            tenant_id=principal.tenant_id,
            event_type="operator.status_changed",
            aggregate_type="membership",
            aggregate_id=membership.id,
            aggregate_version=membership.state_version,
            payload={"manual_status": requested.value},
            correlation_id=request.state.correlation_id,
        )
    result = await own_presence(session, principal)
    await session.commit()
    return result


@router.get("/transfer-candidates", response_model=list[TransferCandidateRead])
async def transfer_candidates(
    project_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("dialer:use"),
) -> list[TransferCandidateRead]:
    project = await resolve_project(session, principal, project_id)
    rows = (
        await session.execute(
            select(Membership, User, HumanOperator, OperatorStatus)
            .join(User, User.id == Membership.user_id)
            .join(
                ProjectUser,
                and_(
                    ProjectUser.tenant_id == Membership.tenant_id,
                    ProjectUser.user_id == Membership.user_id,
                    ProjectUser.project_id == project.id,
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
                Membership.tenant_id == principal.tenant_id,
                Membership.user_id != principal.user_id,
                Membership.is_active.is_(True),
                Membership.role.in_(DIALER_ROLES),
                HumanOperator.is_transfer_available.is_(True),
            )
            .order_by(User.display_name)
        )
    ).all()
    result: list[TransferCandidateRead] = []
    for membership, user, operator, status in rows:
        state = await get_operator_state(
            session,
            tenant_id=principal.tenant_id,
            membership=membership,
            operator=operator,
            status=status,
        )
        if state.effective_status != "available":
            continue
        result.append(
            TransferCandidateRead(
                membership_id=membership.id,
                user_id=user.id,
                display_name=user.display_name,
                extension=operator.extension,
                project_id=project.id,
                effective_status="available",
                is_transfer_available=True,
            )
        )
    return result


async def invitation_project_ids(session: SessionDep, invitation_id: UUID) -> list[UUID]:
    return list(
        await session.scalars(
            select(InvitationProject.project_id)
            .where(InvitationProject.invitation_id == invitation_id)
            .order_by(InvitationProject.project_id)
        )
    )


async def serialize_invitation(
    session: SessionDep,
    invitation: Invitation,
    *,
    token: str | None = None,
) -> InvitationRead:
    settings = get_settings()
    exposed = token if token and settings.app_env in {"development", "test"} else None
    tenant_slug = await session.scalar(select(Tenant.slug).where(Tenant.id == invitation.tenant_id))
    return InvitationRead(
        id=invitation.id,
        email=invitation.email,
        role=invitation.role,
        status=invitation.status,
        project_ids=await invitation_project_ids(session, invitation.id),
        expires_at=invitation.expires_at,
        issued_at=invitation.issued_at,
        accepted_at=invitation.accepted_at,
        cancelled_at=invitation.cancelled_at,
        state_version=invitation.state_version,
        created_at=invitation.created_at,
        acceptance_url=(
            f"{settings.web_origin}/accept-invitation?tenant={tenant_slug}&token={exposed}"
            if exposed and tenant_slug
            else None
        ),
        acceptance_token=exposed,
    )


@router.get("/invitations", response_model=Page[InvitationRead])
async def list_invitations(
    session: SessionDep,
    principal: Principal = require_permission("team:read"),
    status: InvitationStatus | None = None,
    limit: int = 25,
    offset: int = 0,
) -> Page[InvitationRead]:
    ensure_team_manager(principal)
    now = datetime.now(UTC)
    pending_expired = list(
        await session.scalars(
            select(Invitation).where(
                Invitation.tenant_id == principal.tenant_id,
                Invitation.status == InvitationStatus.PENDING,
                Invitation.expires_at <= now,
            )
        )
    )
    for invitation in pending_expired:
        invitation.status = InvitationStatus.EXPIRED
        invitation.state_version += 1
    filters = [Invitation.tenant_id == principal.tenant_id]
    if status is not None:
        filters.append(Invitation.status == status)
    total = int(await session.scalar(select(func.count()).select_from(Invitation).where(*filters)) or 0)
    rows = list(
        await session.scalars(
            select(Invitation)
            .where(*filters)
            .order_by(Invitation.created_at.desc(), Invitation.id)
            .limit(min(max(limit, 1), 100))
            .offset(max(offset, 0))
        )
    )
    items = [await serialize_invitation(session, invitation) for invitation in rows]
    if pending_expired:
        await session.commit()
    return Page(
        items=items,
        total=total,
        limit=min(max(limit, 1), 100),
        offset=max(offset, 0),
    )


def ensure_invite_role(actor: Principal, role: RoleName) -> None:
    if role not in {RoleName.TENANT_MANAGER, RoleName.HUMAN_OPERATOR, RoleName.ANALYST}:
        raise ApiError(422, "invitation_role_invalid", "Эта роль недоступна для приглашения")
    if actor.role not in {RoleName.TENANT_OWNER, RoleName.TENANT_MANAGER}:
        raise ApiError(403, "permission_denied", "Недостаточно прав для приглашения")


@router.post("/invitations", response_model=InvitationRead, status_code=201)
async def create_invitation(
    payload: InvitationCreate,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("team:manage"),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
) -> InvitationRead:
    role = RoleName(payload.role)
    ensure_invite_role(principal, role)
    fingerprint = request_fingerprint(payload)
    await session.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(f"team:{principal.tenant_id}:{idempotency_key}", 0)
            )
        )
    )
    replay = await session.scalar(
        select(TeamCommandSubmission).where(
            TeamCommandSubmission.tenant_id == principal.tenant_id,
            TeamCommandSubmission.idempotency_key == idempotency_key,
        )
    )
    if replay is not None:
        if replay.operation != "invitation.create" or replay.request_fingerprint != fingerprint:
            raise ApiError(409, "idempotency_key_reused", "Idempotency-Key уже использован")
        invitation = await session.scalar(
            select(Invitation).where(
                Invitation.tenant_id == principal.tenant_id,
                Invitation.id == replay.resource_id,
            )
        )
        if invitation is None:
            raise ApiError(409, "idempotency_replay_missing", "Результат операции недоступен")
        return await serialize_invitation(session, invitation)
    projects = await validate_projects(session, principal.tenant_id, payload.project_ids)
    email = str(payload.email).lower()
    existing = await session.scalar(
        select(Invitation).where(
            Invitation.tenant_id == principal.tenant_id,
            Invitation.email == email,
            Invitation.status == InvitationStatus.PENDING,
        )
    )
    if existing is not None:
        raise ApiError(409, "invitation_already_pending", "Для этого e-mail уже есть приглашение")
    token = secrets.token_urlsafe(48)
    now = datetime.now(UTC)
    invitation = Invitation(
        tenant_id=principal.tenant_id,
        email=email,
        role=role,
        token_hash=hash_token(token),
        invited_by_user_id=principal.user_id,
        invited_by_membership_id=principal.membership_id,
        status=InvitationStatus.PENDING,
        issued_at=now,
        expires_at=now + timedelta(days=payload.expires_in_days),
        state_version=1,
    )
    session.add(invitation)
    await session.flush()
    session.add_all(
        [
            InvitationProject(
                tenant_id=principal.tenant_id,
                invitation_id=invitation.id,
                project_id=project_id,
            )
            for project_id in sorted(projects, key=str)
        ]
    )
    session.add(
        TeamCommandSubmission(
            tenant_id=principal.tenant_id,
            operation="invitation.create",
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            resource_id=invitation.id,
            response_payload={"invitation_id": str(invitation.id)},
        )
    )
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="team.invitation_created",
        resource_type="invitation",
        resource_id=invitation.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"role": role.value, "project_count": len(projects)},
    )
    await session.flush()
    result = await serialize_invitation(session, invitation, token=token)
    await session.commit()
    return result


@router.post("/invitations/{invitation_id}/reissue", response_model=InvitationRead)
async def reissue_invitation(
    invitation_id: UUID,
    payload: TeamVersionRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("team:manage"),
) -> InvitationRead:
    ensure_team_manager(principal)
    invitation = await session.scalar(
        select(Invitation)
        .where(Invitation.tenant_id == principal.tenant_id, Invitation.id == invitation_id)
        .with_for_update()
    )
    if invitation is None:
        raise ApiError(404, "invitation_not_found", "Приглашение не найдено")
    if invitation.state_version != payload.expected_version:
        raise ApiError(409, "invitation_version_conflict", "Приглашение уже изменилось")
    if invitation.status == InvitationStatus.ACCEPTED:
        raise ApiError(409, "invitation_already_accepted", "Приглашение уже принято")
    token = secrets.token_urlsafe(48)
    now = datetime.now(UTC)
    invitation.token_hash = hash_token(token)
    invitation.status = InvitationStatus.PENDING
    invitation.issued_at = now
    invitation.expires_at = now + timedelta(days=7)
    invitation.cancelled_at = None
    invitation.state_version += 1
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="team.invitation_reissued",
        resource_type="invitation",
        resource_id=invitation.id,
        correlation_id=request.state.correlation_id,
    )
    result = await serialize_invitation(session, invitation, token=token)
    await session.commit()
    return result


@router.post("/invitations/{invitation_id}/cancel", response_model=InvitationRead)
async def cancel_invitation(
    invitation_id: UUID,
    payload: TeamVersionRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("team:manage"),
) -> InvitationRead:
    ensure_team_manager(principal)
    invitation = await session.scalar(
        select(Invitation)
        .where(Invitation.tenant_id == principal.tenant_id, Invitation.id == invitation_id)
        .with_for_update()
    )
    if invitation is None:
        raise ApiError(404, "invitation_not_found", "Приглашение не найдено")
    if invitation.state_version != payload.expected_version:
        raise ApiError(409, "invitation_version_conflict", "Приглашение уже изменилось")
    if invitation.status == InvitationStatus.ACCEPTED:
        raise ApiError(409, "invitation_already_accepted", "Приглашение уже принято")
    if invitation.status != InvitationStatus.CANCELLED:
        invitation.status = InvitationStatus.CANCELLED
        invitation.cancelled_at = datetime.now(UTC)
        invitation.state_version += 1
        await write_audit(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="team.invitation_cancelled",
            resource_type="invitation",
            resource_id=invitation.id,
            correlation_id=request.state.correlation_id,
        )
    result = await serialize_invitation(session, invitation)
    if invitation.status == InvitationStatus.CANCELLED:
        await session.commit()
    return result


@router.post("/invitations/accept", response_model=InvitationAcceptResponse)
async def accept_invitation(
    payload: InvitationAccept,
    request: Request,
    session: SessionDep,
) -> InvitationAcceptResponse:
    tenant = await session.scalar(select(Tenant).where(Tenant.slug == payload.tenant_slug))
    if tenant is None:
        raise ApiError(404, "invitation_not_found", "Приглашение не найдено")
    await set_tenant_context(session, tenant.id)
    invitation = await session.scalar(
        select(Invitation)
        .where(
            Invitation.tenant_id == tenant.id,
            Invitation.token_hash == hash_token(payload.token),
        )
        .with_for_update()
    )
    if invitation is None:
        raise ApiError(404, "invitation_not_found", "Приглашение не найдено")
    if invitation.status == InvitationStatus.ACCEPTED and invitation.accepted_membership_id:
        return InvitationAcceptResponse(
            membership_id=invitation.accepted_membership_id,
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            email=invitation.email,
            role=invitation.role,
            already_accepted=True,
        )
    now = datetime.now(UTC)
    if invitation.status != InvitationStatus.PENDING or invitation.expires_at <= now:
        if invitation.status == InvitationStatus.PENDING:
            invitation.status = InvitationStatus.EXPIRED
            invitation.state_version += 1
            await session.commit()
        raise ApiError(410, "invitation_unavailable", "Приглашение отменено или истекло")
    user = await session.scalar(select(User).where(User.email == invitation.email))
    if user is not None:
        if not verify_password(payload.password, user.password_hash):
            raise ApiError(401, "invitation_credentials_invalid", "Пароль существующего аккаунта неверен")
    else:
        if payload.display_name is None:
            raise ApiError(422, "display_name_required", "Укажите имя сотрудника")
        user = User(
            email=invitation.email,
            display_name=payload.display_name,
            password_hash=hash_password(payload.password),
            is_active=True,
        )
        session.add(user)
        await session.flush()
    membership = await session.scalar(
        select(Membership).where(
            Membership.tenant_id == tenant.id,
            Membership.user_id == user.id,
        )
    )
    if membership is None:
        membership = Membership(
            tenant_id=tenant.id,
            user_id=user.id,
            role=invitation.role,
            is_active=True,
            invited_at=invitation.created_at,
            activated_at=now,
            presence_last_seen_at=now,
            state_version=1,
        )
        session.add(membership)
        await session.flush()
    elif membership.role == RoleName.TENANT_OWNER and invitation.role != RoleName.TENANT_OWNER:
        raise ApiError(
            409, "invitation_owner_conflict", "Владелец не может принять приглашение с понижением роли"
        )
    else:
        membership.role = invitation.role
        membership.is_active = True
        membership.activated_at = membership.activated_at or now
        membership.blocked_at = None
        membership.blocked_reason = None
        membership.state_version += 1
    projects = set(await invitation_project_ids(session, invitation.id))
    await replace_assignments(
        session,
        tenant_id=tenant.id,
        user_id=user.id,
        project_ids=projects,
    )
    await ensure_operator_profile(session, membership, now=now)
    invitation.status = InvitationStatus.ACCEPTED
    invitation.accepted_at = now
    invitation.accepted_user_id = user.id
    invitation.accepted_membership_id = membership.id
    invitation.state_version += 1
    await write_audit(
        session,
        tenant_id=tenant.id,
        actor_user_id=user.id,
        action="team.invitation_accepted",
        resource_type="membership",
        resource_id=membership.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"invitation_id": str(invitation.id)},
    )
    await session.commit()
    return InvitationAcceptResponse(
        membership_id=membership.id,
        tenant_id=tenant.id,
        tenant_slug=tenant.slug,
        email=user.email,
        role=membership.role,
    )


@router.get("/{membership_id}", response_model=TeamMemberRead)
async def team_member_detail(
    membership_id: UUID,
    session: SessionDep,
    principal: PrincipalDep,
) -> TeamMemberRead:
    ensure_team_reader(principal)
    if principal.role == RoleName.HUMAN_OPERATOR and membership_id != principal.membership_id:
        raise ApiError(403, "team_member_forbidden", "Оператор может просматривать только свой профиль")
    membership = await get_member(session, tenant_id=principal.tenant_id, membership_id=membership_id)
    return await serialize_member(session, membership)


@router.patch("/{membership_id}/profile", response_model=TeamMemberRead)
async def update_member_profile(
    membership_id: UUID,
    payload: TeamProfileUpdate,
    request: Request,
    session: SessionDep,
    principal: PrincipalDep,
) -> TeamMemberRead:
    membership = await get_member(
        session, tenant_id=principal.tenant_id, membership_id=membership_id, for_update=True
    )
    self_update = membership.id == principal.membership_id
    if self_update and principal.role == RoleName.ANALYST:
        raise ApiError(
            403,
            "team_profile_read_only",
            "РђРЅР°Р»РёС‚РёРєСѓ РґРѕСЃС‚СѓРїРµРЅ С‚РѕР»СЊРєРѕ РїСЂРѕСЃРјРѕС‚СЂ РїСЂРѕС„РёР»СЏ",
        )
    if not self_update:
        ensure_team_manager(principal)
        ensure_member_management_allowed(principal, membership)
    assert_expected_version(membership, payload.expected_version)
    user = await session.scalar(select(User).where(User.id == membership.user_id).with_for_update())
    if user is None:
        raise ApiError(500, "team_user_missing", "Профиль сотрудника повреждён")
    operator, _ = await ensure_operator_profile(session, membership)
    changes = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
    if self_update and any(key in changes for key in {"extension", "is_transfer_available"}):
        raise ApiError(403, "team_profile_field_forbidden", "Это поле изменяет руководитель")
    if "extension" in changes and changes["extension"] is not None:
        duplicate = await session.scalar(
            select(HumanOperator.id).where(
                HumanOperator.tenant_id == principal.tenant_id,
                HumanOperator.extension == changes["extension"],
                HumanOperator.membership_id != membership.id,
            )
        )
        if duplicate is not None:
            raise ApiError(409, "operator_extension_taken", "Внутренний номер уже используется")
    if "display_name" in changes:
        user.display_name = changes.pop("display_name")
    if "extension" in changes:
        if operator is None:
            raise ApiError(422, "operator_profile_unavailable", "Для этой роли внутренний номер недоступен")
        operator.extension = changes.pop("extension")
    if "is_transfer_available" in changes:
        if operator is None:
            raise ApiError(422, "operator_profile_unavailable", "Для этой роли перевод недоступен")
        operator.is_transfer_available = changes.pop("is_transfer_available")
    for key, value in changes.items():
        setattr(membership, key, value)
    membership.state_version += 1
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="team.member_profile_updated",
        resource_type="membership",
        resource_id=membership.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={
            "changed_fields": sorted(payload.model_dump(exclude_unset=True, exclude={"expected_version"}))
        },
    )
    result = await serialize_member(session, membership, user)
    await session.commit()
    return result


@router.put("/{membership_id}/role", response_model=TeamMemberRead)
async def update_member_role(
    membership_id: UUID,
    payload: TeamRoleUpdate,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("team:manage"),
) -> TeamMemberRead:
    await lock_tenant_memberships(session, principal.tenant_id)
    membership = await get_member(session, tenant_id=principal.tenant_id, membership_id=membership_id)
    ensure_member_management_allowed(principal, membership)
    assert_expected_version(membership, payload.expected_version)
    new_role = RoleName(payload.role)
    if new_role == RoleName.TENANT_OWNER:
        raise ApiError(422, "owner_transfer_required", "Используйте атомарную передачу роли владельца")
    if membership.role == RoleName.TENANT_OWNER:
        owners = [
            value
            for value in await lock_tenant_memberships(session, principal.tenant_id)
            if value.role == RoleName.TENANT_OWNER and value.is_active
        ]
        if len(owners) <= 1:
            raise ApiError(409, "last_owner_protected", "Нельзя понизить роль последнего активного владельца")
    membership.role = new_role
    membership.state_version += 1
    await ensure_operator_profile(session, membership)
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="team.member_role_changed",
        resource_type="membership",
        resource_id=membership.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"role": new_role.value},
    )
    result = await serialize_member(session, membership)
    await session.commit()
    return result


@router.put("/{membership_id}/projects", response_model=TeamMemberRead)
async def update_member_projects(
    membership_id: UUID,
    payload: TeamProjectsUpdate,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("team:manage"),
) -> TeamMemberRead:
    membership = await get_member(
        session, tenant_id=principal.tenant_id, membership_id=membership_id, for_update=True
    )
    ensure_member_management_allowed(principal, membership)
    assert_expected_version(membership, payload.expected_version)
    projects = await validate_projects(session, principal.tenant_id, payload.project_ids)
    await replace_assignments(
        session,
        tenant_id=principal.tenant_id,
        user_id=membership.user_id,
        project_ids=projects,
    )
    membership.state_version += 1
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="team.member_projects_changed",
        resource_type="membership",
        resource_id=membership.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"project_ids": [str(value) for value in sorted(projects, key=str)]},
    )
    await session.flush()
    result = await serialize_member(session, membership)
    await session.commit()
    return result


@router.post("/{membership_id}/block", response_model=TeamMemberRead)
async def block_member(
    membership_id: UUID,
    payload: TeamBlockRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("team:manage"),
) -> TeamMemberRead:
    memberships = await lock_tenant_memberships(session, principal.tenant_id)
    membership = next((value for value in memberships if value.id == membership_id), None)
    if membership is None:
        raise ApiError(404, "team_member_not_found", "Сотрудник не найден")
    ensure_member_management_allowed(principal, membership)
    assert_expected_version(membership, payload.expected_version)
    if membership.id == principal.membership_id:
        raise ApiError(409, "self_block_forbidden", "Нельзя заблокировать собственную учётную запись")
    if membership.role == RoleName.TENANT_OWNER:
        active_owners = [m for m in memberships if m.role == RoleName.TENANT_OWNER and m.is_active]
        if len(active_owners) <= 1:
            raise ApiError(409, "last_owner_protected", "Нельзя заблокировать последнего активного владельца")
    active_call = await session.scalar(
        select(Call.id).where(
            Call.tenant_id == principal.tenant_id,
            Call.operator_user_id == membership.user_id,
            Call.status.in_(CAPACITY_CALL_STATES),
        )
    )
    if active_call is not None:
        raise ApiError(
            409, "operator_has_active_call", "Сначала безопасно завершите или передайте активный звонок"
        )
    membership.is_active = False
    membership.blocked_at = datetime.now(UTC)
    membership.blocked_reason = payload.reason
    membership.state_version += 1
    presences = list(
        await session.scalars(
            select(OperatorPresence).where(
                OperatorPresence.tenant_id == principal.tenant_id,
                OperatorPresence.membership_id == membership.id,
                OperatorPresence.ended_at.is_(None),
            )
        )
    )
    for presence in presences:
        presence.ended_at = membership.blocked_at
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="team.member_blocked",
        resource_type="membership",
        resource_id=membership.id,
        correlation_id=request.state.correlation_id,
        reason=payload.reason,
    )
    await enqueue_realtime_event(
        session,
        tenant_id=principal.tenant_id,
        event_type="team.member_blocked",
        aggregate_type="membership",
        aggregate_id=membership.id,
        aggregate_version=membership.state_version,
        payload={"active": False},
        correlation_id=request.state.correlation_id,
    )
    result = await serialize_member(session, membership)
    await session.commit()
    return result


@router.post("/{membership_id}/restore", response_model=TeamMemberRead)
async def restore_member(
    membership_id: UUID,
    payload: TeamVersionRequest,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("team:manage"),
) -> TeamMemberRead:
    membership = await get_member(
        session, tenant_id=principal.tenant_id, membership_id=membership_id, for_update=True
    )
    ensure_member_management_allowed(principal, membership)
    assert_expected_version(membership, payload.expected_version)
    membership.is_active = True
    membership.blocked_at = None
    membership.blocked_reason = None
    membership.state_version += 1
    await ensure_operator_profile(session, membership)
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="team.member_restored",
        resource_type="membership",
        resource_id=membership.id,
        correlation_id=request.state.correlation_id,
    )
    await enqueue_realtime_event(
        session,
        tenant_id=principal.tenant_id,
        event_type="team.member_restored",
        aggregate_type="membership",
        aggregate_id=membership.id,
        aggregate_version=membership.state_version,
        payload={"active": True},
        correlation_id=request.state.correlation_id,
    )
    result = await serialize_member(session, membership)
    await session.commit()
    return result


@router.post("/{membership_id}/transfer-ownership", response_model=TeamMemberRead)
async def transfer_ownership(
    membership_id: UUID,
    payload: OwnershipTransferRequest,
    request: Request,
    session: SessionDep,
    principal: PrincipalDep,
) -> TeamMemberRead:
    if principal.role != RoleName.TENANT_OWNER:
        raise ApiError(403, "permission_denied", "Только владелец может передать роль владельца")
    memberships = await lock_tenant_memberships(session, principal.tenant_id)
    current = next((value for value in memberships if value.id == principal.membership_id), None)
    target = next((value for value in memberships if value.id == membership_id), None)
    if current is None or target is None:
        raise ApiError(404, "team_member_not_found", "Сотрудник не найден")
    if current.id == target.id or not target.is_active:
        raise ApiError(422, "owner_transfer_invalid", "Выберите другого активного сотрудника")
    assert_expected_version(current, payload.current_owner_expected_version)
    assert_expected_version(target, payload.target_expected_version)
    if target.role == RoleName.TENANT_OWNER:
        raise ApiError(409, "owner_transfer_invalid", "Сотрудник уже является владельцем")
    target.role = RoleName.TENANT_OWNER
    target.state_version += 1
    current.role = RoleName.TENANT_MANAGER
    current.state_version += 1
    await ensure_operator_profile(session, target)
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="team.owner_transferred",
        resource_type="membership",
        resource_id=target.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"previous_owner_membership_id": str(current.id)},
    )
    result = await serialize_member(session, target)
    await session.commit()
    return result


@router.get("/{membership_id}/history", response_model=Page[TeamAuditRead])
async def member_history(
    membership_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("audit:read"),
    limit: int = 50,
    offset: int = 0,
) -> Page[TeamAuditRead]:
    await get_member(session, tenant_id=principal.tenant_id, membership_id=membership_id)
    filters = [
        AuditLog.tenant_id == principal.tenant_id,
        AuditLog.resource_type == "membership",
        AuditLog.resource_id == membership_id,
    ]
    total = int(await session.scalar(select(func.count()).select_from(AuditLog).where(*filters)) or 0)
    rows = list(
        await session.scalars(
            select(AuditLog)
            .where(*filters)
            .order_by(AuditLog.created_at.desc())
            .limit(min(max(limit, 1), 100))
            .offset(max(offset, 0))
        )
    )
    return Page(
        items=[
            TeamAuditRead(
                id=row.id,
                actor_user_id=row.actor_user_id,
                action=row.action,
                reason=row.reason,
                safe_metadata=row.safe_metadata,
                created_at=row.created_at,
            )
            for row in rows
        ],
        total=total,
        limit=min(max(limit, 1), 100),
        offset=max(offset, 0),
    )
