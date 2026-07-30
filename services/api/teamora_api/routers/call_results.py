from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from teamora_api.audit import write_audit
from teamora_api.call_result_service import CATEGORY_LABELS, catalog_for_project, definition_read
from teamora_api.dependencies import Principal, SessionDep, require_permission
from teamora_api.enums import CallResultCategory
from teamora_api.errors import ApiError
from teamora_api.models import CallOutcome, CallResultDefinition
from teamora_api.project_access import resolve_project
from teamora_api.rbac import role_has_permission
from teamora_api.schemas.call_results import (
    CallResultActivation,
    CallResultAggregateItem,
    CallResultAnalyticsRead,
    CallResultCatalogRead,
    CallResultCategoryRead,
    CallResultDefinitionCreate,
    CallResultDefinitionRead,
    CallResultDefinitionUpdate,
    CallResultReorder,
)

router = APIRouter(prefix="/call-results", tags=["call-results"])


async def scoped_definition(
    session: SessionDep,
    principal: Principal,
    definition_id: UUID,
    *,
    active_only: bool = False,
) -> CallResultDefinition:
    definition = await session.scalar(
        select(CallResultDefinition).where(
            CallResultDefinition.tenant_id == principal.tenant_id,
            CallResultDefinition.id == definition_id,
        )
    )
    if definition is None:
        raise ApiError(404, "call_result_not_found", "Результат звонка не найден")
    await resolve_project(session, principal, definition.project_id, active_only=active_only)
    if active_only and (not definition.is_active or definition.archived_at is not None):
        raise ApiError(409, "call_result_unavailable", "Результат звонка недоступен")
    return definition


async def catalog_read(
    session: SessionDep,
    principal: Principal,
    project_id: UUID,
    *,
    include_archived: bool,
    available_only: bool,
) -> CallResultCatalogRead:
    await resolve_project(session, principal, project_id, active_only=available_only)
    catalog = await catalog_for_project(session, tenant_id=principal.tenant_id, project_id=project_id)
    filters = [
        CallResultDefinition.tenant_id == principal.tenant_id,
        CallResultDefinition.project_id == project_id,
    ]
    if available_only:
        filters.extend([CallResultDefinition.is_active.is_(True), CallResultDefinition.archived_at.is_(None)])
    elif not include_archived:
        filters.append(CallResultDefinition.archived_at.is_(None))
    definitions = list(
        await session.scalars(
            select(CallResultDefinition)
            .where(*filters)
            .order_by(
                CallResultDefinition.category,
                CallResultDefinition.sort_order,
                CallResultDefinition.created_at,
            )
        )
    )
    return CallResultCatalogRead(
        id=catalog.id,
        project_id=catalog.project_id,
        name=catalog.name,
        is_active=catalog.is_active,
        definitions=[await definition_read(session, item) for item in definitions],
    )


@router.get("/categories", response_model=list[CallResultCategoryRead])
async def list_call_result_categories(
    _principal: Principal = require_permission("call_results:read"),
) -> list[CallResultCategoryRead]:
    return [CallResultCategoryRead(code=code, label=label) for code, label in CATEGORY_LABELS.items()]


@router.get("", response_model=CallResultCatalogRead)
async def get_call_result_catalog(
    project_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("call_results:read"),
    include_archived: bool = False,
) -> CallResultCatalogRead:
    available_only = not role_has_permission(principal.role, "call_results:manage")
    return await catalog_read(
        session,
        principal,
        project_id,
        include_archived=include_archived and not available_only,
        available_only=available_only,
    )


@router.get("/available", response_model=CallResultCatalogRead)
async def get_available_call_results(
    project_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("call_results:read"),
) -> CallResultCatalogRead:
    return await catalog_read(
        session,
        principal,
        project_id,
        include_archived=False,
        available_only=True,
    )


@router.post("", response_model=CallResultDefinitionRead, status_code=201)
async def create_call_result(
    payload: CallResultDefinitionCreate,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("call_results:manage"),
) -> CallResultDefinitionRead:
    await resolve_project(session, principal, payload.project_id)
    catalog = await catalog_for_project(session, tenant_id=principal.tenant_id, project_id=payload.project_id)
    duplicate = await session.scalar(
        select(CallResultDefinition.id).where(
            CallResultDefinition.tenant_id == principal.tenant_id,
            CallResultDefinition.project_id == payload.project_id,
            CallResultDefinition.system_code == payload.system_code,
        )
    )
    if duplicate is not None:
        raise ApiError(409, "call_result_code_taken", "Системный код уже используется")
    definition = CallResultDefinition(
        tenant_id=principal.tenant_id,
        project_id=payload.project_id,
        catalog_id=catalog.id,
        **payload.model_dump(exclude={"project_id"}),
    )
    session.add(definition)
    await session.flush()
    result = await definition_read(session, definition)
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="call_result.created",
        resource_type="call_result_definition",
        resource_id=definition.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"project_id": str(definition.project_id), "system_code": definition.system_code},
    )
    await session.commit()
    return result


@router.get("/analytics", response_model=CallResultAnalyticsRead)
async def call_result_analytics(
    project_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("analytics:read"),
) -> CallResultAnalyticsRead:
    await resolve_project(session, principal, project_id, active_only=False)
    rows = (
        await session.execute(
            select(
                CallOutcome.result_definition_id,
                CallOutcome.code,
                CallOutcome.label,
                CallOutcome.category,
                CallOutcome.color,
                func.count(CallOutcome.id),
            )
            .where(
                CallOutcome.tenant_id == principal.tenant_id,
                CallOutcome.project_id == project_id,
            )
            .group_by(
                CallOutcome.result_definition_id,
                CallOutcome.code,
                CallOutcome.label,
                CallOutcome.category,
                CallOutcome.color,
            )
            .order_by(func.count(CallOutcome.id).desc(), CallOutcome.code)
        )
    ).all()
    category_counts = {category: 0 for category in CallResultCategory}
    results: list[CallResultAggregateItem] = []
    for definition_id, code, label, category, color, count in rows:
        category_counts[category] += int(count)
        results.append(
            CallResultAggregateItem(
                result_definition_id=definition_id,
                code=code,
                label=label,
                category=category,
                color=color,
                count=int(count),
            )
        )
    return CallResultAnalyticsRead(
        project_id=project_id,
        category_counts=category_counts,
        results=results,
    )


@router.get("/{definition_id}", response_model=CallResultDefinitionRead)
async def get_call_result(
    definition_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("call_results:read"),
) -> CallResultDefinitionRead:
    definition = await scoped_definition(session, principal, definition_id)
    if not role_has_permission(principal.role, "call_results:manage") and (
        not definition.is_active or definition.archived_at is not None
    ):
        raise ApiError(404, "call_result_not_found", "Результат звонка не найден")
    return await definition_read(session, definition)


@router.patch("/{definition_id}", response_model=CallResultDefinitionRead)
async def update_call_result(
    definition_id: UUID,
    payload: CallResultDefinitionUpdate,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("call_results:manage"),
) -> CallResultDefinitionRead:
    definition = await scoped_definition(session, principal, definition_id)
    changes = payload.model_dump(exclude_unset=True)
    if "system_code" in changes:
        duplicate = await session.scalar(
            select(CallResultDefinition.id).where(
                CallResultDefinition.tenant_id == principal.tenant_id,
                CallResultDefinition.project_id == definition.project_id,
                CallResultDefinition.system_code == changes["system_code"],
                CallResultDefinition.id != definition.id,
            )
        )
        if duplicate is not None:
            raise ApiError(409, "call_result_code_taken", "Системный код уже используется")
    merged = CallResultDefinitionCreate(
        project_id=definition.project_id,
        system_code=str(changes.get("system_code", definition.system_code)),
        category=changes.get("category", definition.category),
        name=str(changes.get("name", definition.name)),
        name_translations=changes.get("name_translations", definition.name_translations),
        description=str(changes.get("description", definition.description)),
        color=str(changes.get("color", definition.color)),
        sort_order=int(changes.get("sort_order", definition.sort_order)),
        is_active=bool(changes.get("is_active", definition.is_active)),
        requires_comment=bool(changes.get("requires_comment", definition.requires_comment)),
        requires_callback=bool(changes.get("requires_callback", definition.requires_callback)),
        requires_callback_at=bool(changes.get("requires_callback_at", definition.requires_callback_at)),
        creates_task=bool(changes.get("creates_task", definition.creates_task)),
        next_customer_status=changes.get("next_customer_status", definition.next_customer_status),
        return_to_queue=bool(changes.get("return_to_queue", definition.return_to_queue)),
        completes_customer=bool(changes.get("completes_customer", definition.completes_customer)),
        do_not_call=bool(changes.get("do_not_call", definition.do_not_call)),
        counts_as_success=bool(changes.get("counts_as_success", definition.counts_as_success)),
    )
    for field, value in merged.model_dump(exclude={"project_id"}).items():
        setattr(definition, field, value)
    await session.flush()
    await session.refresh(definition)
    result = await definition_read(session, definition)
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="call_result.updated",
        resource_type="call_result_definition",
        resource_id=definition.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"fields": sorted(changes)},
    )
    await session.commit()
    return result


@router.post("/reorder", response_model=CallResultCatalogRead)
async def reorder_call_results(
    project_id: UUID,
    payload: CallResultReorder,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("call_results:manage"),
) -> CallResultCatalogRead:
    await resolve_project(session, principal, project_id, active_only=False)
    definitions = list(
        await session.scalars(
            select(CallResultDefinition).where(
                CallResultDefinition.tenant_id == principal.tenant_id,
                CallResultDefinition.project_id == project_id,
                CallResultDefinition.id.in_(payload.definition_ids),
            )
        )
    )
    if len(definitions) != len(payload.definition_ids):
        raise ApiError(422, "call_result_order_invalid", "Порядок содержит чужой результат")
    by_id = {definition.id: definition for definition in definitions}
    for index, definition_id in enumerate(payload.definition_ids):
        by_id[definition_id].sort_order = (index + 1) * 10
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="call_result.reordered",
        resource_type="project",
        resource_id=project_id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"count": len(definitions)},
    )
    await session.commit()
    return await catalog_read(session, principal, project_id, include_archived=True, available_only=False)


@router.post("/{definition_id}/activate", response_model=CallResultDefinitionRead)
async def activate_call_result(
    definition_id: UUID,
    payload: CallResultActivation,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("call_results:manage"),
) -> CallResultDefinitionRead:
    definition = await scoped_definition(session, principal, definition_id)
    if payload.is_active and definition.archived_at is not None:
        raise ApiError(409, "call_result_archived", "Сначала восстановите результат из архива")
    definition.is_active = payload.is_active
    await session.flush()
    await session.refresh(definition)
    result = await definition_read(session, definition)
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="call_result.activation_changed",
        resource_type="call_result_definition",
        resource_id=definition.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"is_active": definition.is_active},
    )
    await session.commit()
    return result


@router.post("/{definition_id}/archive", response_model=CallResultDefinitionRead)
async def archive_call_result(
    definition_id: UUID,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("call_results:manage"),
) -> CallResultDefinitionRead:
    definition = await scoped_definition(session, principal, definition_id)
    definition.is_active = False
    definition.archived_at = definition.archived_at or datetime.now(UTC)
    await session.flush()
    await session.refresh(definition)
    result = await definition_read(session, definition)
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="call_result.archived",
        resource_type="call_result_definition",
        resource_id=definition.id,
        correlation_id=request.state.correlation_id,
    )
    await session.commit()
    return result


@router.post("/{definition_id}/restore", response_model=CallResultDefinitionRead)
async def restore_call_result(
    definition_id: UUID,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("call_results:manage"),
) -> CallResultDefinitionRead:
    definition = await scoped_definition(session, principal, definition_id)
    definition.archived_at = None
    definition.is_active = True
    await session.flush()
    await session.refresh(definition)
    result = await definition_read(session, definition)
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="call_result.restored",
        resource_type="call_result_definition",
        resource_id=definition.id,
        correlation_id=request.state.correlation_id,
    )
    await session.commit()
    return result
