from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Request
from sqlalchemy import and_, select

from teamora_api.audit import write_audit
from teamora_api.call_state import CAPACITY_CALL_STATES
from teamora_api.config import get_settings
from teamora_api.dependencies import Principal, SessionDep, require_permission
from teamora_api.enums import IntegrationStatus, LanguageCode
from teamora_api.errors import ApiError
from teamora_api.models import (
    Call,
    HumanOperator,
    Integration,
    Membership,
    OperatorStatus,
    TenantSettings,
    User,
)
from teamora_api.routers.calls import serialize_call
from teamora_api.schemas.calls import CallRead
from teamora_api.schemas.operations import (
    IntegrationRead,
    TeamMemberRead,
    TenantSettingsRead,
    TenantSettingsUpdate,
)

router = APIRouter(tags=["operations"])

INTEGRATION_CATALOG: tuple[tuple[str, str, list[str], str], ...] = (
    (
        "generic_webhook",
        "Generic Webhook CRM",
        ["lead_sync", "call_summary"],
        "Подключение webhook и секретов будет доступно после security-проверки.",
    ),
    ("bitrix24", "Bitrix24", ["crm_sync"], "Адаптер подготовлен, но ещё не подключён."),
    ("amocrm", "amoCRM", ["crm_sync"], "Адаптер подготовлен, но ещё не подключён."),
    (
        "google_sheets",
        "Google Sheets",
        ["row_export"],
        "Адаптер подготовлен, но ещё не подключён.",
    ),
)


@router.get("/live-calls", response_model=list[CallRead])
async def live_calls(
    session: SessionDep,
    principal: Principal = require_permission("calls:read"),
) -> list[CallRead]:
    calls = list(
        await session.scalars(
            select(Call)
            .where(
                Call.tenant_id == principal.tenant_id,
                Call.status.in_(CAPACITY_CALL_STATES),
            )
            .order_by(Call.started_at.desc())
            .limit(100)
        )
    )
    return [serialize_call(call) for call in calls]


@router.get("/team", response_model=list[TeamMemberRead])
async def team_members(
    session: SessionDep,
    principal: Principal = require_permission("team:read"),
) -> list[TeamMemberRead]:
    rows = (
        await session.execute(
            select(Membership, User, HumanOperator, OperatorStatus)
            .join(User, User.id == Membership.user_id)
            .outerjoin(
                HumanOperator,
                and_(
                    HumanOperator.membership_id == Membership.id,
                    HumanOperator.tenant_id == principal.tenant_id,
                ),
            )
            .outerjoin(
                OperatorStatus,
                and_(
                    OperatorStatus.human_operator_id == HumanOperator.id,
                    OperatorStatus.tenant_id == principal.tenant_id,
                ),
            )
            .where(Membership.tenant_id == principal.tenant_id)
            .order_by(Membership.created_at)
        )
    ).all()
    return [
        TeamMemberRead(
            membership_id=membership.id,
            user_id=user.id,
            display_name=user.display_name,
            email=user.email,
            role=membership.role,
            is_active=membership.is_active,
            operator_status=status.status if status else None,
            extension=operator.extension if operator else None,
        )
        for membership, user, operator, status in rows
    ]


@router.get("/integrations", response_model=list[IntegrationRead])
async def integrations(
    session: SessionDep,
    principal: Principal = require_permission("integrations:read"),
) -> list[IntegrationRead]:
    configured = {
        integration.provider: integration
        for integration in await session.scalars(
            select(Integration).where(Integration.tenant_id == principal.tenant_id)
        )
    }
    result: list[IntegrationRead] = []
    for provider, display_name, capabilities, note in INTEGRATION_CATALOG:
        integration = configured.pop(provider, None)
        result.append(
            IntegrationRead(
                integration_id=integration.id if integration else None,
                provider=provider,
                display_name=integration.display_name if integration else display_name,
                status=integration.status if integration else IntegrationStatus.UNAVAILABLE,
                capabilities=integration.capabilities if integration else capabilities,
                verified_at=integration.verified_at if integration else None,
                can_configure=False,
                note=note,
            )
        )
    for integration in configured.values():
        result.append(
            IntegrationRead(
                integration_id=integration.id,
                provider=integration.provider,
                display_name=integration.display_name,
                status=integration.status,
                capabilities=integration.capabilities,
                verified_at=integration.verified_at,
                can_configure=False,
                note="Интеграция зарегистрирована; изменение credentials пока недоступно в UI.",
            )
        )
    return result


def serialize_settings(settings: TenantSettings) -> TenantSettingsRead:
    return TenantSettingsRead(
        timezone=settings.timezone,
        default_language=settings.default_language,
        recording_enabled=settings.recording_enabled,
        recording_disclosure_required=settings.recording_disclosure_required,
        retention_days=settings.retention_days,
        max_concurrent_calls=settings.max_concurrent_calls,
        updated_at=settings.updated_at,
    )


@router.get("/settings", response_model=TenantSettingsRead)
async def read_settings(
    session: SessionDep,
    principal: Principal = require_permission("settings:read"),
) -> TenantSettingsRead:
    settings = await session.scalar(
        select(TenantSettings).where(TenantSettings.tenant_id == principal.tenant_id)
    )
    if settings is None:
        raise ApiError(404, "settings_not_found", "Настройки компании не найдены")
    return serialize_settings(settings)


@router.patch("/settings", response_model=TenantSettingsRead)
async def update_settings(
    payload: TenantSettingsUpdate,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("settings:manage"),
) -> TenantSettingsRead:
    settings = await session.scalar(
        select(TenantSettings).where(TenantSettings.tenant_id == principal.tenant_id)
    )
    if settings is None:
        raise ApiError(404, "settings_not_found", "Настройки компании не найдены")

    changes = payload.model_dump(exclude_unset=True)
    timezone = changes.get("timezone")
    if timezone is not None:
        try:
            ZoneInfo(str(timezone))
        except ZoneInfoNotFoundError as exc:
            raise ApiError(422, "timezone_invalid", "Укажите корректный часовой пояс IANA") from exc
    if changes.get("default_language") == LanguageCode.KAA and not get_settings().karakalpak_experimental:
        raise ApiError(422, "language_unavailable", "Каракалпакский доступен только экспериментально")

    recording_enabled = changes.get("recording_enabled", settings.recording_enabled)
    disclosure_required = changes.get("recording_disclosure_required", settings.recording_disclosure_required)
    if recording_enabled and not disclosure_required:
        raise ApiError(
            422,
            "recording_disclosure_required",
            "При записи уведомление клиента должно быть включено",
        )

    changed_fields = sorted(changes)
    for field, value in changes.items():
        setattr(settings, field, value)
    if changed_fields:
        settings.updated_at = datetime.now(UTC)
        await write_audit(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="tenant.settings.updated",
            resource_type="tenant_settings",
            resource_id=settings.id,
            correlation_id=request.state.correlation_id,
            safe_metadata={"changed_fields": changed_fields},
        )
        await session.commit()
    return serialize_settings(settings)
