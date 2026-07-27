from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from teamora_api.audit import write_audit
from teamora_api.config import get_settings
from teamora_api.dependencies import Principal, SessionDep, require_permission
from teamora_api.enums import LanguageCode, OperatorVersionStatus
from teamora_api.errors import ApiError
from teamora_api.models import AiOperator, AiOperatorVersion
from teamora_api.schemas.common import Page
from teamora_api.schemas.operators import AiOperatorCreate, AiOperatorRead, AiOperatorVersionRead

router = APIRouter(prefix="/ai-operators", tags=["ai-operators"])

ALLOWED_TOOL_NAMES = {
    "search_knowledge",
    "find_customer",
    "create_customer",
    "create_lead",
    "create_support_ticket",
    "get_order_status",
    "book_appointment",
    "reschedule_appointment",
    "cancel_appointment",
    "send_confirmation",
    "request_human_operator",
    "transfer_call",
    "end_call",
}


def serialize(operator: AiOperator, version: AiOperatorVersion) -> AiOperatorRead:
    return AiOperatorRead(
        id=operator.id,
        name=operator.name,
        description=operator.description,
        is_active=operator.is_active,
        active_version_id=operator.active_version_id,
        created_at=operator.created_at,
        version=AiOperatorVersionRead.model_validate(version),
    )


@router.get("", response_model=Page[AiOperatorRead])
async def list_operators(
    session: SessionDep,
    principal: Principal = require_permission("operators:manage"),
    limit: int = 50,
    offset: int = 0,
) -> Page[AiOperatorRead]:
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    total = int(
        await session.scalar(
            select(func.count()).select_from(AiOperator).where(AiOperator.tenant_id == principal.tenant_id)
        )
        or 0
    )
    rows = (
        await session.execute(
            select(AiOperator, AiOperatorVersion)
            .join(AiOperatorVersion, AiOperatorVersion.ai_operator_id == AiOperator.id)
            .where(
                AiOperator.tenant_id == principal.tenant_id,
                AiOperatorVersion.tenant_id == principal.tenant_id,
                AiOperatorVersion.version == 1,
            )
            .order_by(AiOperator.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return Page(
        items=[serialize(operator, version) for operator, version in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=AiOperatorRead, status_code=201)
async def create_operator(
    payload: AiOperatorCreate,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("operators:manage"),
) -> AiOperatorRead:
    if LanguageCode.KAA in payload.allowed_languages and not get_settings().karakalpak_experimental:
        raise ApiError(
            422, "kaa_feature_disabled", "Karakalpak is experimental and the feature flag is disabled"
        )
    unknown_tools = set(payload.allowed_tools) - ALLOWED_TOOL_NAMES
    if unknown_tools:
        raise ApiError(422, "tool_not_allowed", "One or more tools are not in the server registry")
    if await session.scalar(
        select(AiOperator.id).where(
            AiOperator.tenant_id == principal.tenant_id, AiOperator.name == payload.name
        )
    ):
        raise ApiError(409, "operator_name_taken", "An AI operator with this name already exists")
    async with session.begin_nested():
        operator = AiOperator(
            tenant_id=principal.tenant_id,
            name=payload.name,
            description=payload.description,
        )
        session.add(operator)
        await session.flush()
        version = AiOperatorVersion(
            tenant_id=principal.tenant_id,
            ai_operator_id=operator.id,
            version=1,
            status=OperatorVersionStatus.DRAFT,
            system_instructions=payload.system_instructions,
            greeting_by_language=payload.greeting_by_language,
            allowed_languages=[language.value for language in payload.allowed_languages],
            allowed_tools=payload.allowed_tools,
            transfer_policy={
                "on_explicit_request": True,
                "max_understanding_failures": 2,
                "on_knowledge_gap": True,
                "on_critical_tool_error": True,
            },
            business_hours={},
        )
        session.add(version)
        await session.flush()
        await write_audit(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="ai_operator.created",
            resource_type="ai_operator",
            resource_id=operator.id,
            correlation_id=request.state.correlation_id,
        )
    await session.commit()
    return serialize(operator, version)


@router.post("/{operator_id}/publish", response_model=AiOperatorRead)
async def publish_operator(
    operator_id: str,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("operators:manage"),
) -> AiOperatorRead:
    row = (
        await session.execute(
            select(AiOperator, AiOperatorVersion)
            .join(AiOperatorVersion, AiOperatorVersion.ai_operator_id == AiOperator.id)
            .where(
                AiOperator.tenant_id == principal.tenant_id,
                AiOperator.id == operator_id,
                AiOperatorVersion.tenant_id == principal.tenant_id,
                AiOperatorVersion.status == OperatorVersionStatus.DRAFT,
            )
            .order_by(AiOperatorVersion.version.desc())
        )
    ).first()
    if row is None:
        raise ApiError(404, "operator_not_found", "AI operator draft was not found")
    operator, version = row
    version.status = OperatorVersionStatus.PUBLISHED
    version.published_at = datetime.now(UTC)
    operator.active_version_id = version.id
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="ai_operator.published",
        resource_type="ai_operator_version",
        resource_id=version.id,
        correlation_id=request.state.correlation_id,
    )
    await session.commit()
    return serialize(operator, version)
