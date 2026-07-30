from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from teamora_api.enums import CallResultCategory
from teamora_api.schemas.call_flows import normalize_language_code

SYSTEM_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,79}$")
COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")
CUSTOMER_STATUS_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
REQUIRED_TRANSLATIONS = frozenset({"ru", "uz", "en", "kaa"})


def normalize_translations(value: dict[str, str]) -> dict[str, str]:
    result = {
        normalize_language_code(language): label.strip() for language, label in value.items() if label.strip()
    }
    missing = sorted(REQUIRED_TRANSLATIONS - result.keys())
    if missing:
        raise ValueError(f"Добавьте переводы: {', '.join(missing)}")
    return result


class CallResultDefinitionBase(BaseModel):
    system_code: str = Field(min_length=2, max_length=80)
    category: CallResultCategory
    name: str = Field(min_length=1, max_length=160)
    name_translations: dict[str, str]
    description: str = Field(default="", max_length=1000)
    color: str = "#64748B"
    sort_order: int = Field(default=0, ge=0, le=10_000)
    is_active: bool = True
    requires_comment: bool = False
    requires_callback: bool = False
    requires_callback_at: bool = False
    creates_task: bool = False
    next_customer_status: str | None = Field(default=None, max_length=32)
    return_to_queue: bool = False
    completes_customer: bool = False
    do_not_call: bool = False
    counts_as_success: bool = False

    @field_validator("system_code")
    @classmethod
    def normalize_system_code(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if not SYSTEM_CODE_PATTERN.fullmatch(normalized):
            raise ValueError("Системный код должен содержать латинские буквы, цифры и _")
        return normalized

    @field_validator("name", "description")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("name_translations")
    @classmethod
    def clean_translations(cls, value: dict[str, str]) -> dict[str, str]:
        return normalize_translations(value)

    @field_validator("color")
    @classmethod
    def validate_color(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not COLOR_PATTERN.fullmatch(normalized):
            raise ValueError("Цвет должен быть в формате #RRGGBB")
        return normalized

    @field_validator("next_customer_status")
    @classmethod
    def clean_customer_status(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if not CUSTOMER_STATUS_PATTERN.fullmatch(normalized):
            raise ValueError("Некорректный следующий статус клиента")
        return normalized

    @model_validator(mode="after")
    def validate_callback_flags(self) -> CallResultDefinitionBase:
        if self.requires_callback_at and not self.requires_callback:
            raise ValueError("Обязательная дата доступна только для результата с перезвоном")
        return self


class CallResultDefinitionCreate(CallResultDefinitionBase):
    project_id: UUID


class CallResultDefinitionUpdate(BaseModel):
    system_code: str | None = Field(default=None, min_length=2, max_length=80)
    category: CallResultCategory | None = None
    name: str | None = Field(default=None, min_length=1, max_length=160)
    name_translations: dict[str, str] | None = None
    description: str | None = Field(default=None, max_length=1000)
    color: str | None = None
    sort_order: int | None = Field(default=None, ge=0, le=10_000)
    is_active: bool | None = None
    requires_comment: bool | None = None
    requires_callback: bool | None = None
    requires_callback_at: bool | None = None
    creates_task: bool | None = None
    next_customer_status: str | None = Field(default=None, max_length=32)
    return_to_queue: bool | None = None
    completes_customer: bool | None = None
    do_not_call: bool | None = None
    counts_as_success: bool | None = None

    @field_validator("system_code")
    @classmethod
    def normalize_optional_system_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if not SYSTEM_CODE_PATTERN.fullmatch(normalized):
            raise ValueError("Системный код должен содержать латинские буквы, цифры и _")
        return normalized

    @field_validator("name", "description")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("name_translations")
    @classmethod
    def clean_optional_translations(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return normalize_translations(value) if value is not None else None

    @field_validator("color")
    @classmethod
    def validate_optional_color(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if not COLOR_PATTERN.fullmatch(normalized):
            raise ValueError("Цвет должен быть в формате #RRGGBB")
        return normalized

    @field_validator("next_customer_status")
    @classmethod
    def clean_optional_customer_status(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if not CUSTOMER_STATUS_PATTERN.fullmatch(normalized):
            raise ValueError("Некорректный следующий статус клиента")
        return normalized


class CallResultDefinitionRead(CallResultDefinitionBase):
    id: UUID
    project_id: UUID
    catalog_id: UUID
    archived_at: datetime | None
    used_count: int = 0
    created_at: datetime
    updated_at: datetime


class CallResultCatalogRead(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    is_active: bool
    definitions: list[CallResultDefinitionRead]


class CallResultReorder(BaseModel):
    definition_ids: list[UUID] = Field(min_length=1, max_length=200)

    @field_validator("definition_ids")
    @classmethod
    def unique_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("Результат не может повторяться в порядке сортировки")
        return value


class CallResultActivation(BaseModel):
    is_active: bool


class CallResultCategoryRead(BaseModel):
    code: CallResultCategory
    label: str


class CallResultAggregateItem(BaseModel):
    result_definition_id: UUID
    code: str
    label: str
    category: CallResultCategory
    color: str
    count: int


class CallResultAnalyticsRead(BaseModel):
    project_id: UUID
    category_counts: dict[CallResultCategory, int]
    results: list[CallResultAggregateItem]
