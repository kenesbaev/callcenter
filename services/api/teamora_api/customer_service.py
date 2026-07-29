from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from teamora_api.errors import ApiError
from teamora_api.models import (
    Customer,
    CustomerContact,
    CustomerFieldDefinition,
    Membership,
    ProjectUser,
    User,
)
from teamora_api.schemas.crm import (
    ContactKind,
    CustomerContactInput,
    CustomerContactRead,
    CustomerFieldDefinitionRead,
    CustomerRead,
    CustomFieldType,
    normalize_email,
    normalize_external_reference,
    normalize_phone,
)


@dataclass(frozen=True)
class NormalizedContact:
    id: UUID | None
    kind: ContactKind
    normalized_value: str
    display_value: str
    label: str | None
    is_primary: bool


def normalize_contacts(values: list[CustomerContactInput]) -> list[NormalizedContact]:
    normalized: list[NormalizedContact] = []
    seen: set[tuple[str, str]] = set()
    primary_counts: dict[str, int] = defaultdict(int)
    for contact in values:
        contact_value = (
            normalize_phone(contact.value) if contact.kind == "phone" else normalize_email(contact.value)
        )
        identity = (contact.kind, contact_value)
        if identity in seen:
            raise ApiError(422, "customer_contact_duplicate", "Один контакт указан несколько раз")
        seen.add(identity)
        if contact.is_primary:
            primary_counts[contact.kind] += 1
        normalized.append(
            NormalizedContact(
                id=contact.id,
                kind=contact.kind,
                normalized_value=contact_value,
                display_value=contact_value,
                label=contact.label,
                is_primary=contact.is_primary,
            )
        )
    if any(count > 1 for count in primary_counts.values()):
        raise ApiError(
            422,
            "customer_primary_contact_duplicate",
            "Для каждого типа контакта можно выбрать только одно основное значение",
        )
    for kind in ("phone", "email"):
        matching = [index for index, contact in enumerate(normalized) if contact.kind == kind]
        if matching and primary_counts[kind] == 0:
            index = matching[0]
            selected_contact = normalized[index]
            normalized[index] = NormalizedContact(
                id=selected_contact.id,
                kind=selected_contact.kind,
                normalized_value=selected_contact.normalized_value,
                display_value=selected_contact.display_value,
                label=selected_contact.label,
                is_primary=True,
            )
    return normalized


async def contacts_for_customers(
    session: AsyncSession,
    tenant_id: UUID,
    customer_ids: list[UUID],
) -> dict[UUID, list[CustomerContact]]:
    grouped: dict[UUID, list[CustomerContact]] = defaultdict(list)
    if not customer_ids:
        return grouped
    contacts = await session.scalars(
        select(CustomerContact).where(
            CustomerContact.tenant_id == tenant_id,
            CustomerContact.customer_id.in_(customer_ids),
        )
    )
    for contact in contacts:
        grouped[contact.customer_id].append(contact)
    return grouped


def serialize_customer(customer: Customer, contacts: list[CustomerContact]) -> CustomerRead:
    return CustomerRead(
        id=customer.id,
        project_id=customer.project_id,
        display_name=customer.display_name,
        external_reference=customer.external_reference,
        preferred_language=customer.preferred_language,
        status=customer.status,
        city=customer.city,
        region=customer.region,
        address=customer.address,
        job_title=customer.job_title,
        organization=customer.organization,
        tags=customer.tags,
        description=customer.description,
        source=customer.source,
        assigned_user_id=customer.assigned_user_id,
        custom_fields=customer.custom_fields,
        contacts=[
            CustomerContactRead(
                id=contact.id,
                kind=cast(ContactKind, contact.kind),
                value=contact.display_value,
                label=contact.label,
                is_primary=contact.is_primary,
            )
            for contact in sorted(
                contacts,
                key=lambda value: (value.kind, not value.is_primary, value.created_at),
            )
        ],
        locked_by_user_id=customer.locked_by_user_id,
        locked_until=customer.locked_until,
        last_call_at=customer.last_call_at,
        next_call_at=customer.next_call_at,
        next_contact_at=customer.next_call_at,
        archived_at=customer.archived_at,
        created_at=customer.created_at,
        updated_at=customer.updated_at,
    )


def serialize_field_definition(
    definition: CustomerFieldDefinition,
) -> CustomerFieldDefinitionRead:
    return CustomerFieldDefinitionRead(
        id=definition.id,
        project_id=definition.project_id,
        name=definition.name,
        key=definition.key,
        field_type=cast(CustomFieldType, definition.field_type),
        is_required=definition.is_required,
        sort_order=definition.sort_order,
        options=definition.options,
        default_value=definition.default_value,
        is_active=definition.is_active,
        created_at=definition.created_at,
        updated_at=definition.updated_at,
    )


async def field_definitions_for_project(
    session: AsyncSession,
    tenant_id: UUID,
    project_id: UUID,
    *,
    include_inactive: bool = True,
) -> list[CustomerFieldDefinition]:
    statement = select(CustomerFieldDefinition).where(
        CustomerFieldDefinition.tenant_id == tenant_id,
        CustomerFieldDefinition.project_id == project_id,
    )
    if not include_inactive:
        statement = statement.where(CustomerFieldDefinition.is_active.is_(True))
    return list(
        await session.scalars(
            statement.order_by(CustomerFieldDefinition.sort_order, CustomerFieldDefinition.created_at)
        )
    )


def is_empty_custom_value(value: object) -> bool:
    return value is None or value == "" or value == []


def validate_custom_field_value(definition: CustomerFieldDefinition, value: object) -> object:
    field_type = definition.field_type
    if value is None:
        return value
    if field_type in {"text", "textarea"}:
        if not isinstance(value, str):
            raise ValueError("ожидается текст")
        return value.strip()
    if field_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            raise ValueError("ожидается число")
        return float(value) if isinstance(value, Decimal) else value
    if field_type == "boolean":
        if not isinstance(value, bool):
            raise ValueError("ожидается логическое значение")
        return value
    if field_type == "date":
        if not isinstance(value, str):
            raise ValueError("ожидается дата YYYY-MM-DD")
        date.fromisoformat(value)
        return value
    if field_type == "datetime":
        if not isinstance(value, str):
            raise ValueError("ожидается дата и время ISO 8601")
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("дата и время должны содержать часовой пояс")
        return value
    if field_type == "select":
        if not isinstance(value, str) or value not in definition.options:
            raise ValueError("значение отсутствует в вариантах выбора")
        return value
    if field_type == "multiselect":
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError("ожидается список значений")
        string_values = list(dict.fromkeys(value))
        if any(item not in definition.options for item in string_values):
            raise ValueError("одно из значений отсутствует в вариантах выбора")
        return string_values
    raise ValueError("неподдерживаемый тип поля")


async def validate_custom_fields(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    submitted: dict[str, object],
    existing: dict[str, object] | None = None,
    apply_defaults: bool = False,
) -> dict[str, object]:
    definitions = await field_definitions_for_project(
        session,
        tenant_id,
        project_id,
        include_inactive=True,
    )
    by_key = {definition.key: definition for definition in definitions}
    unknown = sorted(set(submitted) - set(by_key))
    if unknown:
        raise ApiError(
            422,
            "customer_custom_field_unknown",
            f"Неизвестные поля клиента: {', '.join(unknown)}",
        )
    result = dict(existing or {})
    for key, value in submitted.items():
        definition = by_key[key]
        if not definition.is_active and key not in result:
            raise ApiError(
                422,
                "customer_custom_field_inactive",
                f"Поле «{definition.name}» неактивно",
            )
        try:
            result[key] = validate_custom_field_value(definition, value)
        except (TypeError, ValueError) as exc:
            raise ApiError(
                422,
                "customer_custom_field_invalid",
                f"Поле «{definition.name}»: {exc}",
            ) from exc
    if apply_defaults:
        for definition in definitions:
            if definition.is_active and definition.key not in result and definition.default_value is not None:
                try:
                    result[definition.key] = validate_custom_field_value(
                        definition,
                        definition.default_value,
                    )
                except (TypeError, ValueError) as exc:
                    raise ApiError(
                        422,
                        "customer_custom_field_default_invalid",
                        f"Значение по умолчанию поля «{definition.name}» некорректно: {exc}",
                    ) from exc
    missing = [
        definition.name
        for definition in definitions
        if definition.is_active
        and definition.is_required
        and is_empty_custom_value(result.get(definition.key))
    ]
    if missing:
        raise ApiError(
            422,
            "customer_custom_field_required",
            f"Заполните обязательные поля: {', '.join(missing)}",
        )
    return result


async def validate_responsible_operator(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    user_id: UUID | None,
) -> None:
    if user_id is None:
        return
    exists = await session.scalar(
        select(ProjectUser.id)
        .join(
            Membership,
            (Membership.tenant_id == ProjectUser.tenant_id) & (Membership.user_id == ProjectUser.user_id),
        )
        .join(User, User.id == ProjectUser.user_id)
        .where(
            ProjectUser.tenant_id == tenant_id,
            ProjectUser.project_id == project_id,
            ProjectUser.user_id == user_id,
            ProjectUser.is_active.is_(True),
            Membership.is_active.is_(True),
            User.is_active.is_(True),
        )
    )
    if exists is None:
        raise ApiError(
            422,
            "customer_assignee_invalid",
            "Ответственный сотрудник не назначен на этот проект",
        )


async def duplicate_customer_fields(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID,
    contacts: list[NormalizedContact],
    external_reference: str | None,
    exclude_customer_id: UUID | None = None,
) -> dict[UUID, set[str]]:
    matches: dict[UUID, set[str]] = defaultdict(set)
    external_display, external_normalized = normalize_external_reference(external_reference)
    if external_display and external_normalized:
        statement = select(Customer.id).where(
            Customer.tenant_id == tenant_id,
            Customer.project_id == project_id,
            Customer.external_reference_normalized == external_normalized,
        )
        if exclude_customer_id is not None:
            statement = statement.where(Customer.id != exclude_customer_id)
        customer_id = await session.scalar(statement.limit(1))
        if customer_id is not None:
            matches[customer_id].add("external_reference")
    for contact in contacts:
        statement = select(CustomerContact.customer_id).where(
            CustomerContact.tenant_id == tenant_id,
            CustomerContact.project_id == project_id,
            CustomerContact.kind == contact.kind,
            CustomerContact.normalized_value == contact.normalized_value,
        )
        if exclude_customer_id is not None:
            statement = statement.where(CustomerContact.customer_id != exclude_customer_id)
        customer_id = await session.scalar(statement.limit(1))
        if customer_id is not None:
            matches[customer_id].add(contact.kind)
    return matches


async def replace_customer_contacts(
    session: AsyncSession,
    *,
    customer: Customer,
    contacts: list[NormalizedContact],
) -> list[CustomerContact]:
    duplicates = await duplicate_customer_fields(
        session,
        tenant_id=customer.tenant_id,
        project_id=customer.project_id,
        contacts=contacts,
        external_reference=None,
        exclude_customer_id=customer.id,
    )
    if duplicates:
        fields = sorted({field for values in duplicates.values() for field in values})
        raise ApiError(
            409,
            "customer_contact_exists",
            f"Контакт уже используется другим клиентом: {', '.join(fields)}",
        )
    await session.execute(
        delete(CustomerContact).where(
            CustomerContact.tenant_id == customer.tenant_id,
            CustomerContact.project_id == customer.project_id,
            CustomerContact.customer_id == customer.id,
        )
    )
    await session.flush()
    stored = [
        CustomerContact(
            tenant_id=customer.tenant_id,
            project_id=customer.project_id,
            customer_id=customer.id,
            kind=contact.kind,
            normalized_value=contact.normalized_value,
            display_value=contact.display_value,
            label=contact.label,
            is_primary=contact.is_primary,
        )
        for contact in contacts
    ]
    session.add_all(stored)
    await session.flush()
    return stored


def customer_has_phone(contacts: list[CustomerContact]) -> bool:
    return any(contact.kind == "phone" for contact in contacts)


def primary_or_first_phone(contacts: list[CustomerContact]) -> CustomerContact | None:
    phones = [contact for contact in contacts if contact.kind == "phone"]
    return next((contact for contact in phones if contact.is_primary), phones[0] if phones else None)


def duplicate_error(matches: dict[UUID, set[str]]) -> ApiError:
    fields = sorted({field for values in matches.values() for field in values})
    return ApiError(
        409,
        "customer_duplicate",
        f"Клиент с совпадающими данными уже существует: {', '.join(fields)}",
    )
