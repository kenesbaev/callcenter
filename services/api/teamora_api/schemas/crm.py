from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from teamora_api.enums import LanguageCode

CustomerStatus = Literal["new", "assigned", "callback", "completed", "do_not_call"]
ContactKind = Literal["phone", "email"]
CustomFieldType = Literal[
    "text",
    "textarea",
    "number",
    "boolean",
    "date",
    "datetime",
    "select",
    "multiselect",
]
ImportUpdateRule = Literal["skip", "update"]


def normalize_phone(value: str) -> str:
    compact = re.sub(r"[\s()\-.]", "", value.strip())
    if compact.startswith("00"):
        compact = f"+{compact[2:]}"
    if not re.fullmatch(r"\+[1-9][0-9]{7,14}", compact):
        raise ValueError("Укажите телефон в международном формате, например +998901234567")
    return compact


def normalize_email(value: str) -> str:
    normalized = value.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
        raise ValueError("Укажите корректный e-mail")
    return normalized


def normalize_external_reference(value: str | None) -> tuple[str | None, str | None]:
    if value is None or not value.strip():
        return None, None
    display = value.strip()
    return display, display.casefold()


def normalized_tags(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = raw.strip()
        if not value:
            continue
        normalized = value.casefold()
        if normalized in seen:
            continue
        if len(value) > 60:
            raise ValueError("Тег не может быть длиннее 60 символов")
        seen.add(normalized)
        result.append(value)
    if len(result) > 30:
        raise ValueError("Для клиента разрешено не более 30 тегов")
    return result


class CustomerContactInput(BaseModel):
    id: UUID | None = None
    kind: ContactKind
    value: str = Field(min_length=1, max_length=320)
    label: str | None = Field(default=None, max_length=80)
    is_primary: bool = False

    @field_validator("value")
    @classmethod
    def normalize_value(cls, value: str, info: object) -> str:
        # Kind-aware normalization is repeated in the service after Pydantic has
        # constructed the complete object. Here we only reject empty values.
        normalized = value.strip()
        if not normalized:
            raise ValueError("Контакт не может быть пустым")
        return normalized

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class CustomerBase(BaseModel):
    display_name: str = Field(min_length=2, max_length=160)
    preferred_language: LanguageCode = LanguageCode.RU
    external_reference: str | None = Field(default=None, max_length=160)
    status: CustomerStatus = "new"
    city: str | None = Field(default=None, max_length=160)
    region: str | None = Field(default=None, max_length=160)
    address: str | None = Field(default=None, max_length=500)
    job_title: str | None = Field(default=None, max_length=160)
    organization: str | None = Field(default=None, max_length=200)
    tags: list[str] = Field(default_factory=list)
    description: str = Field(default="", max_length=4000)
    source: str | None = Field(default=None, max_length=120)
    assigned_user_id: UUID | None = None
    next_contact_at: datetime | None = None
    custom_fields: dict[str, object] = Field(default_factory=dict)

    @field_validator("display_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator(
        "city",
        "region",
        "address",
        "job_title",
        "organization",
        "source",
        mode="before",
    )
    @classmethod
    def clean_optional_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("description")
    @classmethod
    def clean_description(cls, value: str) -> str:
        return value.strip()

    @field_validator("external_reference")
    @classmethod
    def clean_external_reference(cls, value: str | None) -> str | None:
        return normalize_external_reference(value)[0]

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, value: list[str]) -> list[str]:
        return normalized_tags(value)

    @field_validator("next_contact_at")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Дата следующего контакта должна содержать часовой пояс")
        return value


class CustomerCreate(CustomerBase):
    project_id: UUID | None = None
    contacts: list[CustomerContactInput] = Field(default_factory=list, max_length=20)
    note: str | None = Field(default=None, max_length=4000)

    # Compatibility fields used by the existing Dialer/E2E clients.
    phone: str | None = None
    alternate_phone: str | None = None
    email: str | None = None

    @model_validator(mode="after")
    def include_legacy_contacts(self) -> CustomerCreate:
        contacts = list(self.contacts)
        if self.phone and self.phone.strip():
            contacts.append(CustomerContactInput(kind="phone", value=self.phone, is_primary=True))
        if self.alternate_phone and self.alternate_phone.strip():
            contacts.append(CustomerContactInput(kind="phone", value=self.alternate_phone))
        if self.email and self.email.strip():
            contacts.append(CustomerContactInput(kind="email", value=self.email, is_primary=True))
        if len(contacts) > 20:
            raise ValueError("Для клиента разрешено не более 20 контактов")
        self.contacts = contacts
        return self


class CustomerUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=2, max_length=160)
    preferred_language: LanguageCode | None = None
    external_reference: str | None = Field(default=None, max_length=160)
    status: CustomerStatus | None = None
    city: str | None = Field(default=None, max_length=160)
    region: str | None = Field(default=None, max_length=160)
    address: str | None = Field(default=None, max_length=500)
    job_title: str | None = Field(default=None, max_length=160)
    organization: str | None = Field(default=None, max_length=200)
    tags: list[str] | None = Field(default=None, max_length=30)
    description: str | None = Field(default=None, max_length=4000)
    source: str | None = Field(default=None, max_length=120)
    assigned_user_id: UUID | None = None
    next_contact_at: datetime | None = None
    custom_fields: dict[str, object] | None = None
    contacts: list[CustomerContactInput] | None = Field(default=None, max_length=20)

    @field_validator("display_name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        return " ".join(value.split()) if value is not None else None

    @field_validator(
        "city",
        "region",
        "address",
        "job_title",
        "organization",
        "source",
        mode="before",
    )
    @classmethod
    def clean_optional_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @field_validator("external_reference")
    @classmethod
    def clean_external_reference(cls, value: str | None) -> str | None:
        return normalize_external_reference(value)[0]

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, value: list[str] | None) -> list[str] | None:
        return normalized_tags(value) if value is not None else None

    @field_validator("next_contact_at")
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Дата следующего контакта должна содержать часовой пояс")
        return value


class CustomerContactsUpdate(BaseModel):
    contacts: list[CustomerContactInput] = Field(max_length=20)


class CustomerContactRead(BaseModel):
    id: UUID
    kind: ContactKind
    value: str
    label: str | None
    is_primary: bool


class CustomerRead(BaseModel):
    id: UUID
    project_id: UUID
    display_name: str | None
    external_reference: str | None
    preferred_language: LanguageCode | None
    status: str
    city: str | None
    region: str | None
    address: str | None
    job_title: str | None
    organization: str | None
    tags: list[str]
    description: str
    source: str | None
    assigned_user_id: UUID | None
    custom_fields: dict[str, object]
    contacts: list[CustomerContactRead]
    locked_by_user_id: UUID | None
    locked_until: datetime | None
    last_call_at: datetime | None
    next_call_at: datetime | None
    next_contact_at: datetime | None
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CustomerFieldDefinitionCreate(BaseModel):
    project_id: UUID
    name: str = Field(min_length=1, max_length=160)
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,79}$")
    field_type: CustomFieldType
    is_required: bool = False
    sort_order: int = Field(default=0, ge=0, le=10_000)
    options: list[str] = Field(default_factory=list, max_length=100)
    default_value: object | None = None
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("key")
    @classmethod
    def normalize_key(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("options")
    @classmethod
    def clean_options(cls, value: list[str]) -> list[str]:
        result = normalized_tags(value)
        if any(len(option) > 120 for option in result):
            raise ValueError("Вариант выбора не может быть длиннее 120 символов")
        return result


class CustomerFieldDefinitionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    is_required: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=10_000)
    options: list[str] | None = Field(default=None, max_length=100)
    default_value: object | None = None
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        return " ".join(value.split()) if value is not None else None

    @field_validator("options")
    @classmethod
    def clean_options(cls, value: list[str] | None) -> list[str] | None:
        return normalized_tags(value) if value is not None else None


class CustomerFieldDefinitionRead(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    key: str
    field_type: CustomFieldType
    is_required: bool
    sort_order: int
    options: list[str]
    default_value: object | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CustomerImportMappingUpdate(BaseModel):
    sheet_name: str | None = Field(default=None, max_length=160)
    mapping: dict[str, str]
    update_rule: ImportUpdateRule = "skip"


class CustomerImportRow(BaseModel):
    row_number: int
    values: dict[str, object]
    duplicate_fields: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class CustomerImportPreviewRead(BaseModel):
    id: UUID
    project_id: UUID
    file_name: str
    file_type: str
    sheet_names: list[str]
    selected_sheet: str
    headers: list[str]
    mapping: dict[str, str]
    update_rule: ImportUpdateRule
    total_rows: int
    valid_rows: int
    duplicate_rows: int
    error_rows: int
    rows: list[CustomerImportRow]
    expires_at: datetime


class CustomerImportError(BaseModel):
    row_number: int
    messages: list[str]


class CustomerImportReport(BaseModel):
    import_id: UUID
    created: int
    updated: int
    skipped: int
    duplicates: int
    errors: list[CustomerImportError]
    committed_at: datetime


class CallbackCreate(BaseModel):
    customer_id: UUID
    due_at: datetime
    note: str = Field(default="", max_length=4000)


class CallbackRead(BaseModel):
    id: UUID
    project_id: UUID
    customer_id: UUID
    customer_name: str | None
    customer_phone: str | None
    call_id: UUID | None
    assigned_user_id: UUID | None
    due_at: datetime
    status: str
    note: str
    completed_at: datetime | None
    created_at: datetime


class DialerTaskSummary(BaseModel):
    id: UUID
    task_type: str
    title: str
    priority: str
    status: str
    due_at: datetime
    comment: str
    assigned_user_id: UUID | None


class DialerAssignment(BaseModel):
    customer: CustomerRead
    source: Literal["callback", "retry", "new"]
    callback_task_id: UUID | None = None
    task: DialerTaskSummary | None = None
    pending_tasks: list[DialerTaskSummary] = Field(default_factory=list)
    lock_token: UUID


class DialerLeaseRequest(BaseModel):
    lock_token: UUID
