from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Request, Response
from sqlalchemy import select

from teamora_api.audit import write_audit
from teamora_api.call_flow_service import (
    broken_reference_issues,
    build_preview,
    create_draft_version,
    definition_from_storage,
    ensure_draft,
    flow_read,
    publish_version,
    referenced_node_ids,
    resolve_flow,
    resolve_version,
    validate_customer_fields,
    validate_definition,
    validation_details,
    version_read,
)
from teamora_api.dependencies import Principal, SessionDep, require_permission
from teamora_api.enums import OperatorVersionStatus, RoleName
from teamora_api.errors import ApiError
from teamora_api.models import Call, CallFlow, CallFlowVersion, Project
from teamora_api.project_access import resolve_project
from teamora_api.rbac import role_has_permission
from teamora_api.schemas.call_flows import (
    CallFlowCreate,
    CallFlowDefinition,
    CallFlowDraftCreate,
    CallFlowDraftSave,
    CallFlowNodeCreate,
    CallFlowNodeDelete,
    CallFlowNodeUpdate,
    CallFlowPreviewRequest,
    CallFlowPreviewResult,
    CallFlowRead,
    CallFlowUpdate,
    CallFlowValidationResult,
    CallFlowVersionRead,
)

router = APIRouter(prefix="/call-flows", tags=["call-flows"])


def can_manage(principal: Principal) -> bool:
    return role_has_permission(principal.role, "call_flows:manage")


async def readable_flow(
    session: SessionDep,
    principal: Principal,
    flow_id: UUID,
    *,
    for_update: bool = False,
) -> CallFlow:
    return await resolve_flow(
        session,
        principal,
        flow_id,
        for_update=for_update,
        allow_archived=can_manage(principal),
    )


async def readable_version(
    session: SessionDep,
    principal: Principal,
    flow_id: UUID,
    version_id: UUID,
    *,
    for_update: bool = False,
) -> tuple[CallFlow, CallFlowVersion]:
    flow, version = await resolve_version(
        session,
        principal,
        flow_id,
        version_id,
        for_update=for_update,
        allow_archived_flow=can_manage(principal),
    )
    if not can_manage(principal) and (
        flow.active_version_id != version.id or version.status != OperatorVersionStatus.PUBLISHED
    ):
        raise ApiError(404, "call_flow_version_not_found", "Версия сценария не найдена")
    return flow, version


def persist_definition(version: CallFlowVersion, definition: CallFlowDefinition) -> None:
    version.definition = definition.model_dump(mode="json")
    version.lock_version += 1


@router.get("", response_model=list[CallFlowRead])
async def list_call_flows(
    project_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("call_flows:read"),
    include_archived: bool = False,
) -> list[CallFlowRead]:
    project = await resolve_project(session, principal, project_id, active_only=False)
    statement = select(CallFlow).where(
        CallFlow.tenant_id == principal.tenant_id,
        CallFlow.project_id == project.id,
    )
    if not include_archived or not can_manage(principal):
        statement = statement.where(CallFlow.archived_at.is_(None), CallFlow.is_active.is_(True))
    if not can_manage(principal):
        statement = statement.where(CallFlow.active_version_id.is_not(None))
    flows = list(await session.scalars(statement.order_by(CallFlow.created_at, CallFlow.id)))
    result: list[CallFlowRead] = []
    for flow in flows:
        item = await flow_read(session, flow)
        if not can_manage(principal):
            item = item.model_copy(
                update={
                    "versions": [
                        version
                        for version in item.versions
                        if version.id == flow.active_version_id
                        and version.status == OperatorVersionStatus.PUBLISHED
                    ]
                }
            )
        result.append(item)
    return result


@router.post("", response_model=CallFlowRead, status_code=201)
async def create_call_flow(
    payload: CallFlowCreate,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("call_flows:manage"),
) -> CallFlowRead:
    project = await resolve_project(
        session,
        principal,
        payload.project_id,
        active_only=False,
        for_update=True,
    )
    if project.status == "archived":
        raise ApiError(409, "project_archived", "Нельзя создать сценарий в архивном проекте")
    flow = CallFlow(
        tenant_id=principal.tenant_id,
        project_id=project.id,
        name=payload.name,
        description=payload.description,
        default_language_code=payload.default_language_code,
        language_codes=payload.language_codes,
        is_active=True,
    )
    session.add(flow)
    await session.flush()
    await create_draft_version(session, flow, source=None)
    if project.call_flow_id is None:
        project.call_flow_id = flow.id
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="call_flow.created",
        resource_type="call_flow",
        resource_id=flow.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"project_id": str(project.id)},
    )
    await session.flush()
    await session.refresh(flow)
    result = await flow_read(session, flow)
    await session.commit()
    return result


@router.get("/calls/{call_id}", response_model=CallFlowVersionRead | None)
async def get_call_flow_for_call(
    call_id: UUID,
    response: Response,
    session: SessionDep,
    principal: Principal = require_permission("call_flows:read"),
) -> CallFlowVersionRead | None:
    statement = select(Call).where(
        Call.tenant_id == principal.tenant_id,
        Call.id == call_id,
    )
    if principal.role == RoleName.HUMAN_OPERATOR:
        statement = statement.where(Call.operator_user_id == principal.user_id)
    call = await session.scalar(statement)
    if call is None:
        raise ApiError(404, "call_not_found", "Звонок не найден")
    await resolve_project(session, principal, call.project_id, active_only=False)
    if call.call_flow_version_id is None:
        response.status_code = 200
        return None
    version = await session.scalar(
        select(CallFlowVersion).where(
            CallFlowVersion.tenant_id == principal.tenant_id,
            CallFlowVersion.project_id == call.project_id,
            CallFlowVersion.id == call.call_flow_version_id,
        )
    )
    if version is None:
        raise ApiError(409, "call_flow_snapshot_missing", "Версия сценария звонка недоступна")
    return version_read(version)


@router.get("/{flow_id}", response_model=CallFlowRead)
async def get_call_flow(
    flow_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("call_flows:read"),
) -> CallFlowRead:
    flow = await readable_flow(session, principal, flow_id)
    item = await flow_read(session, flow)
    if not can_manage(principal):
        item = item.model_copy(
            update={
                "versions": [
                    version
                    for version in item.versions
                    if version.id == flow.active_version_id
                    and version.status == OperatorVersionStatus.PUBLISHED
                ]
            }
        )
    return item


@router.patch("/{flow_id}", response_model=CallFlowRead)
async def update_call_flow(
    flow_id: UUID,
    payload: CallFlowUpdate,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("call_flows:manage"),
) -> CallFlowRead:
    flow = await resolve_flow(session, principal, flow_id, for_update=True)
    if flow.archived_at is not None:
        raise ApiError(409, "call_flow_archived", "Архивный сценарий нельзя изменять")
    values = payload.model_dump(exclude_unset=True)
    language_codes = values.get("language_codes", flow.language_codes)
    default_language = values.get("default_language_code", flow.default_language_code)
    if default_language not in language_codes:
        raise ApiError(
            422,
            "call_flow_default_language_invalid",
            "Язык по умолчанию должен входить в список языков сценария",
        )
    for field, value in values.items():
        setattr(flow, field, value)
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="call_flow.updated",
        resource_type="call_flow",
        resource_id=flow.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"updated_fields": sorted(values)},
    )
    await session.flush()
    await session.refresh(flow)
    result = await flow_read(session, flow)
    await session.commit()
    return result


@router.post("/{flow_id}/archive", response_model=CallFlowRead)
async def archive_call_flow(
    flow_id: UUID,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("call_flows:manage"),
) -> CallFlowRead:
    flow = await resolve_flow(session, principal, flow_id, for_update=True)
    if flow.archived_at is None:
        flow.archived_at = datetime.now(UTC)
        flow.is_active = False
        project = await session.scalar(
            select(Project)
            .where(
                Project.tenant_id == principal.tenant_id,
                Project.id == flow.project_id,
            )
            .with_for_update()
        )
        if project is not None and project.call_flow_id == flow.id:
            project.call_flow_id = None
        await write_audit(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="call_flow.archived",
            resource_type="call_flow",
            resource_id=flow.id,
            correlation_id=request.state.correlation_id,
            safe_metadata={"project_id": str(flow.project_id)},
        )
        await session.flush()
        await session.refresh(flow)
        result = await flow_read(session, flow)
        await session.commit()
        return result
    return await flow_read(session, flow)


@router.post("/{flow_id}/drafts", response_model=CallFlowVersionRead, status_code=201)
async def create_call_flow_draft(
    flow_id: UUID,
    payload: CallFlowDraftCreate,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("call_flows:manage"),
) -> CallFlowVersionRead:
    flow = await resolve_flow(session, principal, flow_id, for_update=True)
    if flow.archived_at is not None:
        raise ApiError(409, "call_flow_archived", "Архивный сценарий нельзя изменять")
    source: CallFlowVersion | None = None
    source_id = payload.source_version_id or flow.active_version_id
    if source_id is not None:
        source = await session.scalar(
            select(CallFlowVersion).where(
                CallFlowVersion.tenant_id == principal.tenant_id,
                CallFlowVersion.project_id == flow.project_id,
                CallFlowVersion.call_flow_id == flow.id,
                CallFlowVersion.id == source_id,
                CallFlowVersion.status.in_([OperatorVersionStatus.PUBLISHED, OperatorVersionStatus.ARCHIVED]),
            )
        )
        if source is None:
            raise ApiError(404, "call_flow_source_version_not_found", "Исходная версия не найдена")
    draft = await create_draft_version(session, flow, source=source)
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="call_flow.draft_created",
        resource_type="call_flow_version",
        resource_id=draft.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"call_flow_id": str(flow.id), "version": draft.version},
    )
    await session.flush()
    await session.refresh(draft)
    result = version_read(draft)
    await session.commit()
    return result


@router.get("/{flow_id}/versions/{version_id}", response_model=CallFlowVersionRead)
async def get_call_flow_version(
    flow_id: UUID,
    version_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("call_flows:read"),
) -> CallFlowVersionRead:
    _flow, version = await readable_version(session, principal, flow_id, version_id)
    return version_read(version)


@router.put("/{flow_id}/versions/{version_id}", response_model=CallFlowVersionRead)
async def save_call_flow_draft(
    flow_id: UUID,
    version_id: UUID,
    payload: CallFlowDraftSave,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("call_flows:manage"),
) -> CallFlowVersionRead:
    flow, version = await resolve_version(session, principal, flow_id, version_id, for_update=True)
    ensure_draft(version, payload.expected_lock_version)
    reference_errors = broken_reference_issues(payload.definition)
    field_errors = await validate_customer_fields(session, flow, payload.definition)
    if reference_errors or field_errors:
        raise ApiError(
            422,
            "call_flow_draft_invalid",
            "Черновик содержит недоступные ссылки",
            details=validation_details(
                CallFlowValidationResult(
                    valid=False,
                    errors=[*reference_errors, *field_errors],
                )
            ),
        )
    persist_definition(version, payload.definition)
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="call_flow.draft_saved",
        resource_type="call_flow_version",
        resource_id=version.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"lock_version": version.lock_version},
    )
    await session.flush()
    await session.refresh(version)
    result = version_read(version)
    await session.commit()
    return result


@router.post(
    "/{flow_id}/versions/{version_id}/nodes",
    response_model=CallFlowVersionRead,
    status_code=201,
)
async def add_call_flow_node(
    flow_id: UUID,
    version_id: UUID,
    payload: CallFlowNodeCreate,
    session: SessionDep,
    principal: Principal = require_permission("call_flows:manage"),
) -> CallFlowVersionRead:
    flow, version = await resolve_version(session, principal, flow_id, version_id, for_update=True)
    ensure_draft(version, payload.expected_lock_version)
    definition = definition_from_storage(version.definition)
    if any(node.id == payload.node.id for node in definition.nodes):
        raise ApiError(409, "call_flow_node_exists", "Узел с таким UUID уже существует")
    candidate = definition.model_copy(update={"nodes": [*definition.nodes, payload.node]})
    issues = broken_reference_issues(candidate)
    issues.extend(await validate_customer_fields(session, flow, candidate))
    if issues:
        raise ApiError(
            422,
            "call_flow_node_invalid",
            "Узел содержит недоступные ссылки",
            details=validation_details(CallFlowValidationResult(valid=False, errors=issues)),
        )
    persist_definition(version, candidate)
    await session.flush()
    await session.refresh(version)
    result = version_read(version)
    await session.commit()
    return result


@router.patch(
    "/{flow_id}/versions/{version_id}/nodes/{node_id}",
    response_model=CallFlowVersionRead,
)
async def update_call_flow_node(
    flow_id: UUID,
    version_id: UUID,
    node_id: UUID,
    payload: CallFlowNodeUpdate,
    session: SessionDep,
    principal: Principal = require_permission("call_flows:manage"),
) -> CallFlowVersionRead:
    flow, version = await resolve_version(session, principal, flow_id, version_id, for_update=True)
    ensure_draft(version, payload.expected_lock_version)
    if payload.node.id != node_id:
        raise ApiError(422, "call_flow_node_id_immutable", "UUID узла нельзя изменить")
    definition = definition_from_storage(version.definition)
    if not any(node.id == node_id for node in definition.nodes):
        raise ApiError(404, "call_flow_node_not_found", "Узел не найден")
    nodes = [payload.node if node.id == node_id else node for node in definition.nodes]
    candidate = definition.model_copy(update={"nodes": nodes})
    issues = broken_reference_issues(candidate)
    issues.extend(await validate_customer_fields(session, flow, candidate))
    if issues:
        raise ApiError(
            422,
            "call_flow_node_invalid",
            "Узел содержит недоступные ссылки",
            details=validation_details(CallFlowValidationResult(valid=False, errors=issues)),
        )
    persist_definition(version, candidate)
    await session.flush()
    await session.refresh(version)
    result = version_read(version)
    await session.commit()
    return result


@router.delete(
    "/{flow_id}/versions/{version_id}/nodes/{node_id}",
    response_model=CallFlowVersionRead,
)
async def delete_call_flow_node(
    flow_id: UUID,
    version_id: UUID,
    node_id: UUID,
    payload: CallFlowNodeDelete,
    session: SessionDep,
    principal: Principal = require_permission("call_flows:manage"),
) -> CallFlowVersionRead:
    _flow, version = await resolve_version(session, principal, flow_id, version_id, for_update=True)
    ensure_draft(version, payload.expected_lock_version)
    definition = definition_from_storage(version.definition)
    if not any(node.id == node_id for node in definition.nodes):
        raise ApiError(404, "call_flow_node_not_found", "Узел не найден")
    if node_id in referenced_node_ids(definition):
        raise ApiError(
            409,
            "call_flow_node_referenced",
            "Сначала удалите переходы, которые ведут на этот узел",
        )
    persist_definition(
        version,
        definition.model_copy(update={"nodes": [node for node in definition.nodes if node.id != node_id]}),
    )
    await session.flush()
    await session.refresh(version)
    result = version_read(version)
    await session.commit()
    return result


@router.post(
    "/{flow_id}/versions/{version_id}/validate",
    response_model=CallFlowValidationResult,
)
async def validate_call_flow_version(
    flow_id: UUID,
    version_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("call_flows:manage"),
) -> CallFlowValidationResult:
    flow, version = await resolve_version(session, principal, flow_id, version_id)
    return await validate_definition(session, flow, definition_from_storage(version.definition))


@router.post(
    "/{flow_id}/versions/{version_id}/preview",
    response_model=CallFlowPreviewResult,
)
async def preview_call_flow_version(
    flow_id: UUID,
    version_id: UUID,
    payload: CallFlowPreviewRequest,
    session: SessionDep,
    principal: Principal = require_permission("call_flows:read"),
) -> CallFlowPreviewResult:
    flow, version = await readable_version(session, principal, flow_id, version_id)
    return await build_preview(session, flow, version, payload)


@router.post(
    "/{flow_id}/versions/{version_id}/publish",
    response_model=CallFlowVersionRead,
)
async def publish_call_flow_version(
    flow_id: UUID,
    version_id: UUID,
    payload: CallFlowNodeDelete,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("call_flows:manage"),
) -> CallFlowVersionRead:
    flow, version = await resolve_version(session, principal, flow_id, version_id, for_update=True)
    ensure_draft(version, payload.expected_lock_version)
    validation = await validate_definition(session, flow, definition_from_storage(version.definition))
    if not validation.valid:
        raise ApiError(
            422,
            "call_flow_publish_invalid",
            "Публикация заблокирована ошибками сценария",
            details=validation_details(validation),
        )
    previous: CallFlowVersion | None = None
    if flow.active_version_id is not None:
        previous = await session.scalar(
            select(CallFlowVersion)
            .where(
                CallFlowVersion.tenant_id == principal.tenant_id,
                CallFlowVersion.project_id == flow.project_id,
                CallFlowVersion.call_flow_id == flow.id,
                CallFlowVersion.id == flow.active_version_id,
            )
            .with_for_update()
        )
    if previous is not None and previous.id != version.id:
        previous.status = OperatorVersionStatus.ARCHIVED
    publish_version(flow, version)
    project = await session.scalar(
        select(Project)
        .where(
            Project.tenant_id == principal.tenant_id,
            Project.id == flow.project_id,
        )
        .with_for_update()
    )
    if project is None:
        raise ApiError(404, "project_not_found", "Проект сценария не найден")
    project.call_flow_id = flow.id
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="call_flow.published",
        resource_type="call_flow_version",
        resource_id=version.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"call_flow_id": str(flow.id), "version": version.version},
    )
    await session.flush()
    await session.refresh(version)
    result = version_read(version)
    await session.commit()
    return result
