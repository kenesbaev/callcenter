from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB

from teamora_api.audit import write_audit
from teamora_api.customer_service import (
    contacts_for_customers,
    duplicate_customer_fields,
    duplicate_error,
    field_definitions_for_project,
    normalize_contacts,
    replace_customer_contacts,
    serialize_customer,
    serialize_field_definition,
    validate_custom_field_value,
    validate_custom_fields,
    validate_responsible_operator,
)
from teamora_api.dependencies import Principal, SessionDep, require_permission
from teamora_api.errors import ApiError
from teamora_api.models import (
    Customer,
    CustomerContact,
    CustomerFieldDefinition,
    CustomerNote,
    Project,
)
from teamora_api.project_access import accessible_projects_statement, resolve_project
from teamora_api.schemas.common import Page
from teamora_api.schemas.crm import (
    CustomerContactsUpdate,
    CustomerCreate,
    CustomerFieldDefinitionCreate,
    CustomerFieldDefinitionRead,
    CustomerFieldDefinitionUpdate,
    CustomerRead,
    CustomerUpdate,
    normalize_external_reference,
)

router = APIRouter(prefix="/customers", tags=["customers"])


async def resolve_customer(
    session: SessionDep,
    principal: Principal,
    customer_id: UUID,
    *,
    for_update: bool = False,
) -> Customer:
    statement = select(Customer).where(
        Customer.tenant_id == principal.tenant_id,
        Customer.id == customer_id,
        Customer.is_anonymized.is_(False),
    )
    if for_update:
        statement = statement.with_for_update(of=Customer)
    customer = await session.scalar(statement)
    if customer is None:
        raise ApiError(404, "customer_not_found", "Клиент не найден")
    await resolve_project(session, principal, customer.project_id, active_only=False)
    return customer


async def serialized_customer(
    session: SessionDep,
    principal: Principal,
    customer: Customer,
) -> CustomerRead:
    grouped = await contacts_for_customers(session, principal.tenant_id, [customer.id])
    return serialize_customer(customer, grouped[customer.id])


@router.get("", response_model=Page[CustomerRead])
async def list_customers(
    session: SessionDep,
    principal: Principal = require_permission("customers:manage"),
    search: str = "",
    status: str | None = None,
    language: str | None = None,
    project_id: UUID | None = None,
    assigned_user_id: UUID | None = None,
    tag: str | None = None,
    archived: Literal["exclude", "include", "only"] = "exclude",
    next_contact_from: datetime | None = None,
    next_contact_to: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Page[CustomerRead]:
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    project_ids = accessible_projects_statement(principal).with_only_columns(Project.id)
    filters = [
        Customer.tenant_id == principal.tenant_id,
        Customer.is_anonymized.is_(False),
        Customer.project_id.in_(project_ids),
    ]
    if project_id is not None:
        project = await resolve_project(session, principal, project_id, active_only=False)
        filters.append(Customer.project_id == project.id)
    if status:
        filters.append(Customer.status == status)
    if language:
        filters.append(Customer.preferred_language == language)
    if assigned_user_id:
        filters.append(Customer.assigned_user_id == assigned_user_id)
    if tag and tag.strip():
        filters.append(func.jsonb_exists(cast(Customer.tags, JSONB), tag.strip()))
    if archived == "exclude":
        filters.append(Customer.archived_at.is_(None))
    elif archived == "only":
        filters.append(Customer.archived_at.is_not(None))
    if next_contact_from is not None:
        filters.append(Customer.next_call_at >= next_contact_from)
    if next_contact_to is not None:
        filters.append(Customer.next_call_at <= next_contact_to)
    if search.strip():
        value = search.strip()
        pattern = f"%{value}%"
        normalized = value.casefold()
        contact_match = (
            select(CustomerContact.id)
            .where(
                CustomerContact.tenant_id == principal.tenant_id,
                CustomerContact.customer_id == Customer.id,
                CustomerContact.project_id == Customer.project_id,
                or_(
                    CustomerContact.display_value.ilike(pattern),
                    CustomerContact.normalized_value.ilike(f"%{normalized}%"),
                ),
            )
            .exists()
        )
        filters.append(
            or_(
                Customer.display_name.ilike(pattern),
                Customer.external_reference.ilike(pattern),
                Customer.external_reference_normalized.ilike(f"%{normalized}%"),
                contact_match,
            )
        )
    total = int(await session.scalar(select(func.count()).select_from(Customer).where(*filters)) or 0)
    customers = list(
        await session.scalars(
            select(Customer)
            .where(*filters)
            .order_by(Customer.created_at.desc(), Customer.id)
            .limit(limit)
            .offset(offset)
        )
    )
    grouped = await contacts_for_customers(
        session,
        principal.tenant_id,
        [customer.id for customer in customers],
    )
    return Page(
        items=[serialize_customer(customer, grouped[customer.id]) for customer in customers],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/fields", response_model=list[CustomerFieldDefinitionRead])
async def list_customer_fields(
    project_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("customers:manage"),
    include_inactive: bool = False,
) -> list[CustomerFieldDefinitionRead]:
    project = await resolve_project(session, principal, project_id, active_only=False)
    definitions = await field_definitions_for_project(
        session,
        principal.tenant_id,
        project.id,
        include_inactive=include_inactive,
    )
    return [serialize_field_definition(definition) for definition in definitions]


@router.post("/fields", response_model=CustomerFieldDefinitionRead, status_code=201)
async def create_customer_field(
    payload: CustomerFieldDefinitionCreate,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("customers:manage"),
) -> CustomerFieldDefinitionRead:
    project = await resolve_project(session, principal, payload.project_id)
    duplicate = await session.scalar(
        select(CustomerFieldDefinition.id).where(
            CustomerFieldDefinition.tenant_id == principal.tenant_id,
            CustomerFieldDefinition.project_id == project.id,
            CustomerFieldDefinition.key == payload.key,
        )
    )
    if duplicate is not None:
        raise ApiError(409, "customer_field_key_exists", "Поле с таким ключом уже существует")
    if payload.field_type in {"select", "multiselect"} and not payload.options:
        raise ApiError(422, "customer_field_options_required", "Добавьте варианты выбора")
    if payload.field_type not in {"select", "multiselect"} and payload.options:
        raise ApiError(
            422,
            "customer_field_options_not_allowed",
            "Варианты доступны только для select и multiselect",
        )
    definition = CustomerFieldDefinition(
        tenant_id=principal.tenant_id,
        project_id=project.id,
        name=payload.name,
        key=payload.key,
        field_type=payload.field_type,
        is_required=payload.is_required,
        sort_order=payload.sort_order,
        options=payload.options,
        default_value=payload.default_value,
        is_active=payload.is_active,
    )
    if payload.default_value is not None:
        try:
            definition.default_value = validate_custom_field_value(
                definition,
                payload.default_value,
            )
        except (TypeError, ValueError) as exc:
            raise ApiError(
                422,
                "customer_field_default_invalid",
                f"Некорректное значение по умолчанию: {exc}",
            ) from exc
    session.add(definition)
    await session.flush()
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="customer_field.created",
        resource_type="customer_field_definition",
        resource_id=definition.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"project_id": str(project.id), "field_type": definition.field_type},
    )
    await session.commit()
    return serialize_field_definition(definition)


@router.patch("/fields/{field_id}", response_model=CustomerFieldDefinitionRead)
async def update_customer_field(
    field_id: UUID,
    payload: CustomerFieldDefinitionUpdate,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("customers:manage"),
) -> CustomerFieldDefinitionRead:
    definition = await session.scalar(
        select(CustomerFieldDefinition)
        .where(
            CustomerFieldDefinition.tenant_id == principal.tenant_id,
            CustomerFieldDefinition.id == field_id,
        )
        .with_for_update()
    )
    if definition is None:
        raise ApiError(404, "customer_field_not_found", "Поле клиента не найдено")
    await resolve_project(session, principal, definition.project_id, active_only=False)
    fields = payload.model_fields_set
    for field in ("name", "is_required", "sort_order", "is_active"):
        if field in fields:
            value = getattr(payload, field)
            if value is not None:
                setattr(definition, field, value)
    if "options" in fields and payload.options is not None:
        if definition.field_type not in {"select", "multiselect"} and payload.options:
            raise ApiError(
                422,
                "customer_field_options_not_allowed",
                "Варианты доступны только для select и multiselect",
            )
        if definition.field_type in {"select", "multiselect"} and not payload.options:
            raise ApiError(422, "customer_field_options_required", "Добавьте варианты выбора")
        definition.options = payload.options
    if "default_value" in fields:
        if payload.default_value is None:
            definition.default_value = None
        else:
            try:
                definition.default_value = validate_custom_field_value(
                    definition,
                    payload.default_value,
                )
            except (TypeError, ValueError) as exc:
                raise ApiError(
                    422,
                    "customer_field_default_invalid",
                    f"Некорректное значение по умолчанию: {exc}",
                ) from exc
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="customer_field.updated",
        resource_type="customer_field_definition",
        resource_id=definition.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"fields": sorted(fields)},
    )
    await session.commit()
    return serialize_field_definition(definition)


@router.post("", response_model=CustomerRead, status_code=201)
async def create_customer(
    payload: CustomerCreate,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("customers:manage"),
) -> CustomerRead:
    project = await resolve_project(session, principal, payload.project_id)
    contacts = normalize_contacts(payload.contacts)
    external_reference, external_normalized = normalize_external_reference(payload.external_reference)
    duplicates = await duplicate_customer_fields(
        session,
        tenant_id=principal.tenant_id,
        project_id=project.id,
        contacts=contacts,
        external_reference=external_reference,
    )
    if duplicates:
        raise duplicate_error(duplicates)
    await validate_responsible_operator(
        session,
        tenant_id=principal.tenant_id,
        project_id=project.id,
        user_id=payload.assigned_user_id,
    )
    custom_fields = await validate_custom_fields(
        session,
        tenant_id=principal.tenant_id,
        project_id=project.id,
        submitted=payload.custom_fields,
        apply_defaults=True,
    )
    customer = Customer(
        tenant_id=principal.tenant_id,
        project_id=project.id,
        display_name=payload.display_name,
        external_reference=external_reference,
        external_reference_normalized=external_normalized,
        preferred_language=payload.preferred_language,
        status=payload.status,
        city=payload.city,
        region=payload.region,
        address=payload.address,
        job_title=payload.job_title,
        organization=payload.organization,
        tags=payload.tags,
        description=payload.description,
        source=payload.source,
        assigned_user_id=payload.assigned_user_id,
        next_call_at=payload.next_contact_at,
        custom_fields=custom_fields,
    )
    session.add(customer)
    await session.flush()
    stored_contacts = await replace_customer_contacts(
        session,
        customer=customer,
        contacts=contacts,
    )
    if payload.note and payload.note.strip():
        session.add(
            CustomerNote(
                tenant_id=principal.tenant_id,
                customer_id=customer.id,
                author_user_id=principal.user_id,
                content=payload.note.strip(),
            )
        )
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="customer.created",
        resource_type="customer",
        resource_id=customer.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"project_id": str(project.id), "contact_count": len(stored_contacts)},
    )
    await session.commit()
    return serialize_customer(customer, stored_contacts)


@router.get("/{customer_id}", response_model=CustomerRead)
async def get_customer(
    customer_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("customers:manage"),
) -> CustomerRead:
    customer = await resolve_customer(session, principal, customer_id)
    return await serialized_customer(session, principal, customer)


@router.patch("/{customer_id}", response_model=CustomerRead)
async def update_customer(
    customer_id: UUID,
    payload: CustomerUpdate,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("customers:manage"),
) -> CustomerRead:
    customer = await resolve_customer(session, principal, customer_id, for_update=True)
    fields = payload.model_fields_set
    if "assigned_user_id" in fields:
        await validate_responsible_operator(
            session,
            tenant_id=principal.tenant_id,
            project_id=customer.project_id,
            user_id=payload.assigned_user_id,
        )
    if "external_reference" in fields:
        external_reference, external_normalized = normalize_external_reference(payload.external_reference)
        duplicates = await duplicate_customer_fields(
            session,
            tenant_id=principal.tenant_id,
            project_id=customer.project_id,
            contacts=[],
            external_reference=external_reference,
            exclude_customer_id=customer.id,
        )
        if duplicates:
            raise duplicate_error(duplicates)
        customer.external_reference = external_reference
        customer.external_reference_normalized = external_normalized
    for field in (
        "display_name",
        "preferred_language",
        "status",
        "city",
        "region",
        "address",
        "job_title",
        "organization",
        "tags",
        "description",
        "source",
        "assigned_user_id",
    ):
        if field in fields:
            value = getattr(payload, field)
            if field == "tags" and value is None:
                value = []
            if field == "description" and value is None:
                value = ""
            setattr(customer, field, value)
    if "next_contact_at" in fields:
        customer.next_call_at = payload.next_contact_at
    if "custom_fields" in fields and payload.custom_fields is not None:
        customer.custom_fields = await validate_custom_fields(
            session,
            tenant_id=principal.tenant_id,
            project_id=customer.project_id,
            submitted=payload.custom_fields,
            existing=customer.custom_fields,
        )
    contacts: list[CustomerContact]
    if "contacts" in fields and payload.contacts is not None:
        contacts = await replace_customer_contacts(
            session,
            customer=customer,
            contacts=normalize_contacts(payload.contacts),
        )
    else:
        contacts = (await contacts_for_customers(session, principal.tenant_id, [customer.id]))[customer.id]
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="customer.updated",
        resource_type="customer",
        resource_id=customer.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"fields": sorted(fields)},
    )
    await session.flush()
    await session.refresh(customer)
    await session.commit()
    return serialize_customer(customer, contacts)


@router.put("/{customer_id}/contacts", response_model=CustomerRead)
async def update_customer_contacts(
    customer_id: UUID,
    payload: CustomerContactsUpdate,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("customers:manage"),
) -> CustomerRead:
    customer = await resolve_customer(session, principal, customer_id, for_update=True)
    contacts = await replace_customer_contacts(
        session,
        customer=customer,
        contacts=normalize_contacts(payload.contacts),
    )
    await write_audit(
        session,
        tenant_id=principal.tenant_id,
        actor_user_id=principal.user_id,
        action="customer.contacts_updated",
        resource_type="customer",
        resource_id=customer.id,
        correlation_id=request.state.correlation_id,
        safe_metadata={"contact_count": len(contacts)},
    )
    await session.commit()
    return serialize_customer(customer, contacts)


@router.post("/{customer_id}/archive", response_model=CustomerRead)
async def archive_customer(
    customer_id: UUID,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("customers:manage"),
) -> CustomerRead:
    customer = await resolve_customer(session, principal, customer_id, for_update=True)
    if customer.archived_at is None:
        customer.archived_at = datetime.now(UTC)
        customer.locked_by_user_id = None
        customer.locked_until = None
        customer.lock_token = None
        await write_audit(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="customer.archived",
            resource_type="customer",
            resource_id=customer.id,
            correlation_id=request.state.correlation_id,
        )
    response = await serialized_customer(session, principal, customer)
    await session.commit()
    return response


@router.post("/{customer_id}/restore", response_model=CustomerRead)
async def restore_customer(
    customer_id: UUID,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("customers:manage"),
) -> CustomerRead:
    customer = await resolve_customer(session, principal, customer_id, for_update=True)
    if customer.archived_at is not None:
        customer.archived_at = None
        await write_audit(
            session,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            action="customer.restored",
            resource_type="customer",
            resource_id=customer.id,
            correlation_id=request.state.correlation_id,
        )
    response = await serialized_customer(session, principal, customer)
    await session.commit()
    return response
