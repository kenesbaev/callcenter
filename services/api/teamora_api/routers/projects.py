from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Request
from sqlalchemy import delete, func, select

from teamora_api.audit import write_audit
from teamora_api.call_result_service import seed_default_definitions
from teamora_api.config import get_settings
from teamora_api.dependencies import Principal, SessionDep, require_permission
from teamora_api.enums import LanguageCode, RoleName
from teamora_api.errors import ApiError
from teamora_api.models import (
    AiOperator,
    CallFlow,
    CallResultCatalog,
    KnowledgeSource,
    Membership,
    PhoneNumber,
    Project,
    ProjectInboundPhoneNumber,
    ProjectUser,
    TenantSettings,
    User,
)
from teamora_api.project_access import accessible_projects_statement, resolve_project
from teamora_api.schemas.common import Page
from teamora_api.schemas.projects import (
    CallbackRules,
    EffectiveProjectSettings,
    ProjectAiOperatorOption,
    ProjectCallFlowOption,
    ProjectCreate,
    ProjectKnowledgeSourceOption,
    ProjectOperatorOption,
    ProjectOptions,
    ProjectPhoneNumberOption,
    ProjectRead,
    ProjectStatus,
    ProjectUpdate,
    WorkingHours,
    validate_retry_policy,
)

router = APIRouter(prefix="/projects", tags=["projects"])

ASSIGNABLE_OPERATOR_ROLES = {
    RoleName.TENANT_OWNER,
    RoleName.TENANT_MANAGER,
    RoleName.HUMAN_OPERATOR,
}


def effective_settings(
    project: Project | None,
    tenant_settings: TenantSettings | None,
) -> EffectiveProjectSettings:
    tenant_language = tenant_settings.default_language if tenant_settings else LanguageCode.RU
    tenant_timezone = tenant_settings.timezone if tenant_settings else "Asia/Tashkent"
    tenant_limit = (
        tenant_settings.max_concurrent_calls
        if tenant_settings
        else get_settings().default_max_concurrent_calls
    )
    tenant_recording = tenant_settings.recording_enabled if tenant_settings else True
    tenant_disclosure = tenant_settings.recording_disclosure_required if tenant_settings else True
    return EffectiveProjectSettings(
        default_language=(project.default_language if project else None) or tenant_language,
        timezone=(project.timezone if project else None) or tenant_timezone,
        max_concurrent_calls=(project.max_concurrent_calls if project else None) or tenant_limit,
        recording_enabled=(
            project.recording_enabled
            if project is not None and project.recording_enabled is not None
            else tenant_recording
        ),
        recording_disclosure_required=(
            project.recording_disclosure_required
            if project is not None and project.recording_disclosure_required is not None
            else tenant_disclosure
        ),
    )


async def tenant_settings_for(session: SessionDep, tenant_id: UUID) -> TenantSettings | None:
    return cast(
        TenantSettings | None,
        await session.scalar(select(TenantSettings).where(TenantSettings.tenant_id == tenant_id)),
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


async def inbound_numbers_by_project(
    session: SessionDep,
    tenant_id: UUID,
    project_ids: list[UUID],
) -> dict[UUID, list[UUID]]:
    grouped: dict[UUID, list[UUID]] = defaultdict(list)
    if not project_ids:
        return grouped
    rows = await session.execute(
        select(
            ProjectInboundPhoneNumber.project_id,
            ProjectInboundPhoneNumber.phone_number_id,
        ).where(
            ProjectInboundPhoneNumber.tenant_id == tenant_id,
            ProjectInboundPhoneNumber.project_id.in_(project_ids),
        )
    )
    for project_id, phone_number_id in rows:
        grouped[project_id].append(phone_number_id)
    return grouped


async def catalogs_by_project(
    session: SessionDep,
    tenant_id: UUID,
    project_ids: list[UUID],
) -> dict[UUID, UUID]:
    if not project_ids:
        return {}
    rows = await session.execute(
        select(CallResultCatalog.project_id, CallResultCatalog.id).where(
            CallResultCatalog.tenant_id == tenant_id,
            CallResultCatalog.project_id.in_(project_ids),
            CallResultCatalog.is_active.is_(True),
        )
    )
    return {project_id: catalog_id for project_id, catalog_id in rows}


def serialize_project(
    project: Project,
    *,
    operator_user_ids: list[UUID],
    inbound_phone_number_ids: list[UUID],
    call_result_catalog_id: UUID,
    tenant_settings: TenantSettings | None,
) -> ProjectRead:
    return ProjectRead(
        id=project.id,
        name=project.name,
        description=project.description,
        status=cast(ProjectStatus, project.status),
        default_language=project.default_language,
        timezone=project.timezone,
        outbound_number=project.outbound_number,
        outbound_phone_number_id=project.outbound_phone_number_id,
        inbound_phone_number_ids=inbound_phone_number_ids,
        ai_operator_id=project.ai_operator_id,
        knowledge_source_id=project.knowledge_source_id,
        call_flow_id=project.call_flow_id,
        call_result_catalog_id=call_result_catalog_id,
        max_concurrent_calls=project.max_concurrent_calls,
        effective_settings=effective_settings(project, tenant_settings),
        working_hours=WorkingHours.model_validate(project.working_hours),
        recording_enabled=project.recording_enabled,
        recording_disclosure_required=project.recording_disclosure_required,
        max_attempts=project.max_attempts,
        retry_intervals_minutes=project.retry_intervals_minutes,
        callback_rules=CallbackRules.model_validate(project.callback_rules),
        is_default=project.is_default,
        archived_at=project.archived_at,
        operator_user_ids=operator_user_ids,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


async def serialize_projects(
    session: SessionDep,
    principal: Principal,
    projects: list[Project],
) -> list[ProjectRead]:
    project_ids = [project.id for project in projects]
    users = await project_users_by_project(session, principal.tenant_id, project_ids)
    inbound_numbers = await inbound_numbers_by_project(session, principal.tenant_id, project_ids)
    catalogs = await catalogs_by_project(session, principal.tenant_id, project_ids)
    tenant_settings = await tenant_settings_for(session, principal.tenant_id)
    missing_catalog = next((project.id for project in projects if project.id not in catalogs), None)
    if missing_catalog is not None:
        raise ApiError(
            500,
            "project_catalog_missing",
            "Каталог результатов проекта не инициализирован",
        )
    return [
        serialize_project(
            project,
            operator_user_ids=users[project.id],
            inbound_phone_number_ids=inbound_numbers[project.id],
            call_result_catalog_id=catalogs[project.id],
            tenant_settings=tenant_settings,
        )
        for project in projects
    ]


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
                    Membership.role.in_(ASSIGNABLE_OPERATOR_ROLES),
                )
            )
        )
        if valid_ids != unique_user_ids:
            raise ApiError(
                422,
                "project_operator_invalid",
                "Один или несколько сотрудников недоступны для работы в проекте",
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
            for user_id in sorted(unique_user_ids, key=str)
        ]
    )


async def replace_inbound_numbers(
    session: SessionDep,
    *,
    tenant_id: UUID,
    project_id: UUID,
    phone_number_ids: list[UUID],
) -> None:
    unique_ids = set(phone_number_ids)
    if unique_ids:
        valid_ids = set(
            await session.scalars(
                select(PhoneNumber.id).where(
                    PhoneNumber.tenant_id == tenant_id,
                    PhoneNumber.id.in_(unique_ids),
                    PhoneNumber.is_active.is_(True),
                )
            )
        )
        if valid_ids != unique_ids:
            raise ApiError(
                422,
                "project_phone_number_invalid",
                "Один или несколько телефонных номеров недоступны компании",
            )
        occupied = await session.scalar(
            select(ProjectInboundPhoneNumber.id).where(
                ProjectInboundPhoneNumber.tenant_id == tenant_id,
                ProjectInboundPhoneNumber.phone_number_id.in_(unique_ids),
                ProjectInboundPhoneNumber.project_id != project_id,
            )
        )
        if occupied is not None:
            raise ApiError(
                409,
                "project_inbound_number_in_use",
                "Входящий номер уже назначен другому проекту",
            )
    await session.execute(
        delete(ProjectInboundPhoneNumber).where(
            ProjectInboundPhoneNumber.tenant_id == tenant_id,
            ProjectInboundPhoneNumber.project_id == project_id,
        )
    )
    session.add_all(
        [
            ProjectInboundPhoneNumber(
                tenant_id=tenant_id,
                project_id=project_id,
                phone_number_id=phone_number_id,
            )
            for phone_number_id in sorted(unique_ids, key=str)
        ]
    )


async def set_outbound_number(
    session: SessionDep,
    project: Project,
    *,
    phone_number_id: UUID | None = None,
    legacy_number: str | None = None,
) -> None:
    if phone_number_id is None and legacy_number is None:
        project.outbound_phone_number_id = None
        project.outbound_number = None
        return
    statement = select(PhoneNumber).where(
        PhoneNumber.tenant_id == project.tenant_id,
        PhoneNumber.is_active.is_(True),
    )
    if phone_number_id is not None:
        statement = statement.where(PhoneNumber.id == phone_number_id)
    else:
        statement = statement.where(PhoneNumber.e164 == legacy_number)
    phone_number = await session.scalar(statement)
    if phone_number is None:
        raise ApiError(
            422,
            "project_phone_number_invalid",
            "Исходящий номер должен быть активным номером этой компании",
        )
    project.outbound_phone_number_id = phone_number.id
    project.outbound_number = phone_number.e164


async def validate_project_links(session: SessionDep, project: Project) -> None:
    if project.ai_operator_id is not None:
        operator = await session.scalar(
            select(AiOperator.id).where(
                AiOperator.tenant_id == project.tenant_id,
                AiOperator.project_id == project.id,
                AiOperator.id == project.ai_operator_id,
                AiOperator.is_active.is_(True),
            )
        )
        if operator is None:
            raise ApiError(
                422,
                "project_ai_operator_invalid",
                "AI-оператор должен принадлежать этому проекту и компании",
            )
    if project.knowledge_source_id is not None:
        source = await session.scalar(
            select(KnowledgeSource.id).where(
                KnowledgeSource.tenant_id == project.tenant_id,
                KnowledgeSource.id == project.knowledge_source_id,
            )
        )
        if source is None:
            raise ApiError(
                422,
                "project_knowledge_source_invalid",
                "База знаний должна принадлежать этой компании",
            )
    if project.call_flow_id is not None:
        call_flow = await session.scalar(
            select(CallFlow.id).where(
                CallFlow.tenant_id == project.tenant_id,
                CallFlow.project_id == project.id,
                CallFlow.id == project.call_flow_id,
                CallFlow.is_active.is_(True),
            )
        )
        if call_flow is None:
            raise ApiError(
                422,
                "project_call_flow_invalid",
                "Сценарий должен принадлежать этому проекту и компании",
            )


def validate_project_configuration(
    project: Project,
    tenant_settings: TenantSettings | None,
) -> None:
    if project.timezone is not None:
        try:
            ZoneInfo(project.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ApiError(
                422,
                "project_timezone_invalid",
                "Укажите корректный часовой пояс IANA",
            ) from exc
    if project.default_language == LanguageCode.KAA and not get_settings().karakalpak_experimental:
        raise ApiError(
            422,
            "language_unavailable",
            "Каракалпакский язык доступен только в экспериментальном режиме",
        )
    WorkingHours.model_validate(project.working_hours)
    CallbackRules.model_validate(project.callback_rules)
    try:
        validate_retry_policy(project.max_attempts, project.retry_intervals_minutes)
    except ValueError as exc:
        raise ApiError(422, "project_retry_policy_invalid", str(exc)) from exc
    resolved = effective_settings(project, tenant_settings)
    tenant_limit = effective_settings(None, tenant_settings).max_concurrent_calls
    if resolved.max_concurrent_calls > tenant_limit:
        raise ApiError(
            422,
            "project_call_limit_invalid",
            "Лимит проекта не может превышать лимит одновременных звонков компании",
        )
    if resolved.recording_enabled and not resolved.recording_disclosure_required:
        raise ApiError(
            422,
            "project_recording_disclosure_required",
            "При записи разговора уведомление клиента обязательно",
        )


@router.get("", response_model=Page[ProjectRead])
async def list_projects(
    session: SessionDep,
    principal: Principal = require_permission("projects:read"),
    status: ProjectStatus | None = None,
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
    return Page(
        items=await serialize_projects(session, principal, projects),
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/options", response_model=ProjectOptions)
async def project_options(
    session: SessionDep,
    principal: Principal = require_permission("projects:manage"),
) -> ProjectOptions:
    tenant_settings = await tenant_settings_for(session, principal.tenant_id)
    members = (
        await session.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.tenant_id == principal.tenant_id,
                Membership.is_active.is_(True),
                Membership.role.in_(ASSIGNABLE_OPERATOR_ROLES),
                User.is_active.is_(True),
            )
            .order_by(User.display_name)
        )
    ).all()
    phone_numbers = list(
        await session.scalars(
            select(PhoneNumber)
            .where(PhoneNumber.tenant_id == principal.tenant_id)
            .order_by(PhoneNumber.label, PhoneNumber.e164)
        )
    )
    ai_operators = list(
        await session.scalars(
            select(AiOperator).where(AiOperator.tenant_id == principal.tenant_id).order_by(AiOperator.name)
        )
    )
    knowledge_sources = list(
        await session.scalars(
            select(KnowledgeSource)
            .where(KnowledgeSource.tenant_id == principal.tenant_id)
            .order_by(KnowledgeSource.name)
        )
    )
    call_flows = list(
        await session.scalars(
            select(CallFlow).where(CallFlow.tenant_id == principal.tenant_id).order_by(CallFlow.name)
        )
    )
    return ProjectOptions(
        tenant_defaults=effective_settings(None, tenant_settings),
        operators=[
            ProjectOperatorOption(
                user_id=user.id,
                display_name=user.display_name,
                email=user.email,
                role=membership.role,
            )
            for membership, user in members
        ],
        phone_numbers=[
            ProjectPhoneNumberOption(
                id=number.id,
                e164=number.e164,
                label=number.label,
                is_active=number.is_active,
            )
            for number in phone_numbers
        ],
        ai_operators=[
            ProjectAiOperatorOption(
                id=operator.id,
                project_id=operator.project_id,
                name=operator.name,
                is_active=operator.is_active,
            )
            for operator in ai_operators
        ],
        knowledge_sources=[
            ProjectKnowledgeSourceOption(
                id=source.id,
                name=source.name,
                source_type=source.source_type,
            )
            for source in knowledge_sources
        ],
        call_flows=[
            ProjectCallFlowOption(
                id=flow.id,
                project_id=flow.project_id,
                name=flow.name,
                is_active=flow.is_active,
            )
            for flow in call_flows
        ],
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
    now = datetime.now(UTC)
    project = Project(
        id=uuid4(),
        tenant_id=principal.tenant_id,
        name=name,
        description=payload.description.strip(),
        status=payload.status,
        default_language=payload.default_language,
        timezone=payload.timezone,
        ai_operator_id=payload.ai_operator_id,
        knowledge_source_id=payload.knowledge_source_id,
        call_flow_id=payload.call_flow_id,
        max_concurrent_calls=payload.max_concurrent_calls,
        working_hours=payload.working_hours.model_dump(mode="json", exclude_none=True),
        recording_enabled=payload.recording_enabled,
        recording_disclosure_required=payload.recording_disclosure_required,
        max_attempts=payload.max_attempts,
        retry_intervals_minutes=payload.retry_intervals_minutes,
        callback_rules=payload.callback_rules.model_dump(mode="json"),
        is_default=False,
        archived_at=now if payload.status == "archived" else None,
    )
    if payload.outbound_phone_number_id is not None or payload.outbound_number is not None:
        await set_outbound_number(
            session,
            project,
            phone_number_id=payload.outbound_phone_number_id,
            legacy_number=payload.outbound_number,
        )
    await validate_project_links(session, project)
    tenant_settings = await tenant_settings_for(session, principal.tenant_id)
    validate_project_configuration(project, tenant_settings)
    session.add(project)
    await session.flush()
    await replace_project_users(
        session,
        tenant_id=principal.tenant_id,
        project_id=project.id,
        user_ids=payload.operator_user_ids,
    )
    await replace_inbound_numbers(
        session,
        tenant_id=principal.tenant_id,
        project_id=project.id,
        phone_number_ids=payload.inbound_phone_number_ids,
    )
    catalog = CallResultCatalog(
        tenant_id=principal.tenant_id,
        project_id=project.id,
        name="Результаты звонка",
        is_active=True,
    )
    session.add(catalog)
    await session.flush()
    await seed_default_definitions(
        session,
        tenant_id=principal.tenant_id,
        project_id=project.id,
        catalog_id=catalog.id,
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
    return serialize_project(
        project,
        operator_user_ids=list(dict.fromkeys(payload.operator_user_ids)),
        inbound_phone_number_ids=list(dict.fromkeys(payload.inbound_phone_number_ids)),
        call_result_catalog_id=catalog.id,
        tenant_settings=tenant_settings,
    )


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project(
    project_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("projects:read"),
) -> ProjectRead:
    project = await resolve_project(session, principal, project_id, active_only=False)
    return (await serialize_projects(session, principal, [project]))[0]


@router.patch("/{project_id}", response_model=ProjectRead)
async def update_project(
    project_id: UUID,
    payload: ProjectUpdate,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("projects:manage"),
) -> ProjectRead:
    project = await resolve_project(session, principal, project_id, active_only=False, for_update=True)
    fields = payload.model_fields_set
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
            raise ApiError(
                409,
                "project_name_taken",
                "Проект с таким названием уже существует",
            )
        project.name = name
    for field in (
        "description",
        "status",
        "default_language",
        "timezone",
        "ai_operator_id",
        "knowledge_source_id",
        "call_flow_id",
        "max_concurrent_calls",
        "recording_enabled",
        "recording_disclosure_required",
        "max_attempts",
    ):
        if field in fields:
            value = getattr(payload, field)
            if field == "description" and value is not None:
                value = value.strip()
            setattr(project, field, value)
    if "working_hours" in fields and payload.working_hours is not None:
        project.working_hours = payload.working_hours.model_dump(mode="json", exclude_none=True)
    if "retry_intervals_minutes" in fields and payload.retry_intervals_minutes is not None:
        project.retry_intervals_minutes = payload.retry_intervals_minutes
    if "callback_rules" in fields and payload.callback_rules is not None:
        project.callback_rules = payload.callback_rules.model_dump(mode="json")
    if (
        "outbound_number" in fields
        and "outbound_phone_number_id" in fields
        and payload.outbound_number is not None
        and payload.outbound_phone_number_id is not None
    ):
        raise ApiError(
            422,
            "project_outbound_number_ambiguous",
            "Укажите исходящий номер либо его идентификатор, но не оба значения",
        )
    if "outbound_phone_number_id" in fields and payload.outbound_phone_number_id is not None:
        await set_outbound_number(
            session,
            project,
            phone_number_id=payload.outbound_phone_number_id,
        )
    elif "outbound_number" in fields and payload.outbound_number is not None:
        await set_outbound_number(session, project, legacy_number=payload.outbound_number)
    elif "outbound_phone_number_id" in fields or "outbound_number" in fields:
        await set_outbound_number(session, project)
    if payload.operator_user_ids is not None:
        await replace_project_users(
            session,
            tenant_id=principal.tenant_id,
            project_id=project.id,
            user_ids=payload.operator_user_ids,
        )
    if payload.inbound_phone_number_ids is not None:
        await replace_inbound_numbers(
            session,
            tenant_id=principal.tenant_id,
            project_id=project.id,
            phone_number_ids=payload.inbound_phone_number_ids,
        )
    if "status" in fields:
        project.archived_at = datetime.now(UTC) if project.status == "archived" else None
    await validate_project_links(session, project)
    tenant_settings = await tenant_settings_for(session, principal.tenant_id)
    validate_project_configuration(project, tenant_settings)
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="project.updated",
        resource_type="project",
        resource_id=project.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"fields": sorted(fields)},
    )
    await session.flush()
    await session.refresh(project)
    response = (await serialize_projects(session, principal, [project]))[0]
    await session.commit()
    return response


@router.post("/{project_id}/archive", response_model=ProjectRead)
async def archive_project(
    project_id: UUID,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("projects:manage"),
) -> ProjectRead:
    project = await resolve_project(session, principal, project_id, active_only=False, for_update=True)
    if project.is_default:
        raise ApiError(409, "default_project_required", "Основной проект нельзя архивировать")
    if project.status != "archived":
        project.status = "archived"
        project.archived_at = datetime.now(UTC)
        await write_audit(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="project.archived",
            resource_type="project",
            resource_id=project.id,
            correlation_id=request.state.correlation_id,
        )
        await session.flush()
        await session.refresh(project)
    response = (await serialize_projects(session, principal, [project]))[0]
    await session.commit()
    return response
