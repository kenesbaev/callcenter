from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import func, or_, select

from teamora_api.audit import write_audit
from teamora_api.dependencies import Principal, SessionDep, require_permission
from teamora_api.errors import ApiError
from teamora_api.models import Customer, CustomerContact, CustomerNote
from teamora_api.schemas.common import Page
from teamora_api.schemas.crm import CustomerContactRead, CustomerCreate, CustomerRead

router = APIRouter(prefix="/customers", tags=["customers"])


def serialize_customer(customer: Customer, contacts: list[CustomerContact]) -> CustomerRead:
    return CustomerRead(
        id=customer.id,
        display_name=customer.display_name,
        external_reference=customer.external_reference,
        preferred_language=customer.preferred_language,
        status=customer.status,
        custom_fields=customer.custom_fields,
        contacts=[
            CustomerContactRead(
                kind=contact.kind,
                value=contact.display_value,
                is_primary=contact.is_primary,
            )
            for contact in sorted(contacts, key=lambda value: (not value.is_primary, value.kind))
        ],
        locked_by_user_id=customer.locked_by_user_id,
        locked_until=customer.locked_until,
        last_call_at=customer.last_call_at,
        next_call_at=customer.next_call_at,
        created_at=customer.created_at,
    )


async def contacts_for_customers(
    session: SessionDep, tenant_id: UUID, customer_ids: list[UUID]
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


@router.get("", response_model=Page[CustomerRead])
async def list_customers(
    session: SessionDep,
    principal: Principal = require_permission("customers:manage"),
    search: str = "",
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> Page[CustomerRead]:
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    filters = [Customer.tenant_id == principal.tenant_id, Customer.is_anonymized.is_(False)]
    if status:
        filters.append(Customer.status == status)
    if search.strip():
        pattern = f"%{search.strip()}%"
        contact_match = (
            select(CustomerContact.id)
            .where(
                CustomerContact.tenant_id == principal.tenant_id,
                CustomerContact.customer_id == Customer.id,
                CustomerContact.display_value.ilike(pattern),
            )
            .exists()
        )
        filters.append(
            or_(
                Customer.display_name.ilike(pattern),
                Customer.external_reference.ilike(pattern),
                contact_match,
            )
        )
    total = int(await session.scalar(select(func.count()).select_from(Customer).where(*filters)) or 0)
    customers = list(
        await session.scalars(
            select(Customer).where(*filters).order_by(Customer.created_at.desc()).limit(limit).offset(offset)
        )
    )
    grouped = await contacts_for_customers(
        session, principal.tenant_id, [customer.id for customer in customers]
    )
    return Page(
        items=[serialize_customer(customer, grouped[customer.id]) for customer in customers],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=CustomerRead, status_code=201)
async def create_customer(
    payload: CustomerCreate,
    request: Request,
    session: SessionDep,
    principal: Principal = require_permission("customers:manage"),
) -> CustomerRead:
    contact_values = [payload.phone]
    if payload.alternate_phone:
        contact_values.append(payload.alternate_phone)
    if payload.email:
        contact_values.append(str(payload.email).lower())
    duplicate = await session.scalar(
        select(CustomerContact.id).where(
            CustomerContact.tenant_id == principal.tenant_id,
            CustomerContact.normalized_value.in_(contact_values),
        )
    )
    if duplicate:
        raise ApiError(409, "customer_contact_exists", "Клиент с таким контактом уже существует")

    customer = Customer(
        tenant_id=principal.tenant_id,
        display_name=payload.display_name.strip(),
        external_reference=payload.external_reference,
        preferred_language=payload.preferred_language,
        status="new",
        custom_fields=payload.custom_fields,
    )
    session.add(customer)
    await session.flush()
    contacts = [
        CustomerContact(
            tenant_id=principal.tenant_id,
            customer_id=customer.id,
            kind="phone",
            normalized_value=payload.phone,
            display_value=payload.phone,
            is_primary=True,
        )
    ]
    if payload.alternate_phone:
        contacts.append(
            CustomerContact(
                tenant_id=principal.tenant_id,
                customer_id=customer.id,
                kind="phone",
                normalized_value=payload.alternate_phone,
                display_value=payload.alternate_phone,
                is_primary=False,
            )
        )
    if payload.email:
        email = str(payload.email).lower()
        contacts.append(
            CustomerContact(
                tenant_id=principal.tenant_id,
                customer_id=customer.id,
                kind="email",
                normalized_value=email,
                display_value=email,
                is_primary=False,
            )
        )
    session.add_all(contacts)
    if payload.note:
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
    )
    await session.commit()
    return serialize_customer(customer, contacts)


@router.get("/{customer_id}", response_model=CustomerRead)
async def get_customer(
    customer_id: UUID,
    session: SessionDep,
    principal: Principal = require_permission("customers:manage"),
) -> CustomerRead:
    customer = await session.scalar(
        select(Customer).where(
            Customer.tenant_id == principal.tenant_id,
            Customer.id == customer_id,
            Customer.is_anonymized.is_(False),
        )
    )
    if customer is None:
        raise ApiError(404, "customer_not_found", "Клиент не найден")
    contacts = list(
        await session.scalars(
            select(CustomerContact).where(
                CustomerContact.tenant_id == principal.tenant_id,
                CustomerContact.customer_id == customer.id,
            )
        )
    )
    return serialize_customer(customer, contacts)
