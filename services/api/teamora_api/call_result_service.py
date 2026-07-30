from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select

from teamora_api.dependencies import SessionDep
from teamora_api.enums import CallResultCategory
from teamora_api.errors import ApiError
from teamora_api.models import CallOutcome, CallResultCatalog, CallResultDefinition
from teamora_api.schemas.call_results import CallResultDefinitionRead


@dataclass(frozen=True)
class DefaultResult:
    code: str
    category: CallResultCategory
    translations: dict[str, str]
    color: str
    sort_order: int
    requires_callback: bool = False
    requires_callback_at: bool = False
    do_not_call: bool = False
    counts_as_success: bool = False
    next_customer_status: str = "completed"


DEFAULT_RESULTS = (
    DefaultResult(
        "success",
        CallResultCategory.SUCCESSFUL,
        {"ru": "Успешно", "uz": "Muvaffaqiyatli", "en": "Success", "kaa": "Tabıslı"},
        "#16A34A",
        10,
        counts_as_success=True,
    ),
    DefaultResult(
        "callback",
        CallResultCategory.INTERMEDIATE,
        {"ru": "Перезвонить", "uz": "Qayta qo‘ng‘iroq", "en": "Callback", "kaa": "Qayta qońıraw"},
        "#F59E0B",
        20,
        requires_callback=True,
        requires_callback_at=True,
        next_customer_status="callback",
    ),
    DefaultResult(
        "other",
        CallResultCategory.INTERMEDIATE,
        {"ru": "Другое", "uz": "Boshqa", "en": "Other", "kaa": "Basqa"},
        "#64748B",
        30,
    ),
    DefaultResult(
        "no_answer",
        CallResultCategory.UNREACHABLE,
        {"ru": "Нет ответа", "uz": "Javob yo‘q", "en": "No answer", "kaa": "Juwap joq"},
        "#64748B",
        40,
    ),
    DefaultResult(
        "busy",
        CallResultCategory.UNREACHABLE,
        {"ru": "Занято", "uz": "Band", "en": "Busy", "kaa": "Bánt"},
        "#F97316",
        50,
    ),
    DefaultResult(
        "wrong_number",
        CallResultCategory.UNSUCCESSFUL,
        {"ru": "Неверный номер", "uz": "Noto‘g‘ri raqam", "en": "Wrong number", "kaa": "Qáte nomer"},
        "#DC2626",
        60,
    ),
    DefaultResult(
        "do_not_call",
        CallResultCategory.UNSUCCESSFUL,
        {"ru": "Не звонить", "uz": "Qo‘ng‘iroq qilmang", "en": "Do not call", "kaa": "Qońıraw etpeń"},
        "#991B1B",
        70,
        do_not_call=True,
        next_customer_status="do_not_call",
    ),
    DefaultResult(
        "not_interested",
        CallResultCategory.UNSUCCESSFUL,
        {"ru": "Не заинтересован", "uz": "Qiziqmaydi", "en": "Not interested", "kaa": "Qızıǵıwshılıq joq"},
        "#DC2626",
        80,
    ),
    DefaultResult(
        "failed",
        CallResultCategory.UNSUCCESSFUL,
        {"ru": "Ошибка звонка", "uz": "Qo‘ng‘iroq xatosi", "en": "Call failed", "kaa": "Qońıraw qáteligi"},
        "#B91C1C",
        90,
    ),
)

CATEGORY_LABELS = {
    CallResultCategory.SUCCESSFUL: "Успешные",
    CallResultCategory.INTERMEDIATE: "Промежуточные",
    CallResultCategory.UNREACHABLE: "Недозвон",
    CallResultCategory.UNSUCCESSFUL: "Неуспешные",
}


async def catalog_for_project(session: SessionDep, *, tenant_id: UUID, project_id: UUID) -> CallResultCatalog:
    catalog = await session.scalar(
        select(CallResultCatalog).where(
            CallResultCatalog.tenant_id == tenant_id,
            CallResultCatalog.project_id == project_id,
        )
    )
    if catalog is None:
        raise ApiError(404, "call_result_catalog_not_found", "Каталог результатов не найден")
    return catalog


def default_definitions(*, tenant_id: UUID, project_id: UUID, catalog_id: UUID) -> list[CallResultDefinition]:
    return [
        CallResultDefinition(
            tenant_id=tenant_id,
            project_id=project_id,
            catalog_id=catalog_id,
            system_code=item.code,
            category=item.category,
            name=item.translations["ru"],
            name_translations=item.translations,
            description="",
            color=item.color,
            sort_order=item.sort_order,
            is_active=True,
            requires_comment=False,
            requires_callback=item.requires_callback,
            requires_callback_at=item.requires_callback_at,
            creates_task=False,
            next_customer_status=item.next_customer_status,
            return_to_queue=False,
            completes_customer=not item.requires_callback,
            do_not_call=item.do_not_call,
            counts_as_success=item.counts_as_success,
        )
        for item in DEFAULT_RESULTS
    ]


async def seed_default_definitions(
    session: SessionDep, *, tenant_id: UUID, project_id: UUID, catalog_id: UUID
) -> None:
    session.add_all(default_definitions(tenant_id=tenant_id, project_id=project_id, catalog_id=catalog_id))


async def definition_read(session: SessionDep, definition: CallResultDefinition) -> CallResultDefinitionRead:
    used_count = int(
        await session.scalar(
            select(func.count())
            .select_from(CallOutcome)
            .where(
                CallOutcome.tenant_id == definition.tenant_id,
                CallOutcome.result_definition_id == definition.id,
            )
        )
        or 0
    )
    return CallResultDefinitionRead(
        id=definition.id,
        project_id=definition.project_id,
        catalog_id=definition.catalog_id,
        system_code=definition.system_code,
        category=definition.category,
        name=definition.name,
        name_translations=definition.name_translations,
        description=definition.description,
        color=definition.color,
        sort_order=definition.sort_order,
        is_active=definition.is_active,
        requires_comment=definition.requires_comment,
        requires_callback=definition.requires_callback,
        requires_callback_at=definition.requires_callback_at,
        creates_task=definition.creates_task,
        next_customer_status=definition.next_customer_status,
        return_to_queue=definition.return_to_queue,
        completes_customer=definition.completes_customer,
        do_not_call=definition.do_not_call,
        counts_as_success=definition.counts_as_success,
        archived_at=definition.archived_at,
        used_count=used_count,
        created_at=definition.created_at,
        updated_at=definition.updated_at,
    )


def localized_result_name(definition: CallResultDefinition, language_code: str | None) -> str:
    language = (language_code or "ru").strip().lower().replace("_", "-")
    base_language = language.split("-", 1)[0]
    return (
        definition.name_translations.get(language)
        or definition.name_translations.get(base_language)
        or definition.name_translations.get("ru")
        or definition.name
    )
