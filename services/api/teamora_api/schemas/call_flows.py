from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from teamora_api.enums import OperatorVersionStatus

LANGUAGE_CODE_PATTERN = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$")
SYSTEM_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,79}$")

CallFlowNodeType = Literal[
    "start",
    "operator_text",
    "customer_question",
    "info_hint",
    "choice",
    "value_input",
    "update_customer_field",
    "create_task",
    "create_callback",
    "transfer_request",
    "end",
]


def normalize_language_code(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if not LANGUAGE_CODE_PATTERN.fullmatch(normalized):
        raise ValueError("Некорректный language code")
    return normalized


def normalize_language_codes(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = normalize_language_code(value)
        if normalized not in result:
            result.append(normalized)
    if not result:
        raise ValueError("Добавьте хотя бы один язык сценария")
    return result


class CallFlowAnswer(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    key: str = Field(min_length=1, max_length=80)
    label_by_language: dict[str, str] = Field(default_factory=dict)
    next_node_id: UUID | None = None
    is_required: bool = True

    @field_validator("key")
    @classmethod
    def clean_key(cls, value: str) -> str:
        normalized = value.strip().lower().replace(" ", "_")
        if not SYSTEM_KEY_PATTERN.fullmatch(normalized):
            raise ValueError("Ключ варианта должен содержать латинские буквы, цифры и _")
        return normalized

    @field_validator("label_by_language")
    @classmethod
    def clean_labels(cls, value: dict[str, str]) -> dict[str, str]:
        return {
            normalize_language_code(language): label.strip()
            for language, label in value.items()
            if label.strip()
        }


class CallFlowNode(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    system_key: str = Field(min_length=2, max_length=80)
    name: str = Field(min_length=1, max_length=160)
    node_type: CallFlowNodeType
    text_by_language: dict[str, str] = Field(default_factory=dict)
    hint_by_language: dict[str, str] = Field(default_factory=dict)
    order: int = Field(default=0, ge=0, le=10_000)
    is_required: bool = False
    customer_field_definition_id: UUID | None = None
    answers: list[CallFlowAnswer] = Field(default_factory=list, max_length=30)
    next_node_id: UUID | None = None
    fallback_node_id: UUID | None = None
    action_config: dict[str, object] = Field(default_factory=dict)

    @field_validator("system_key")
    @classmethod
    def clean_system_key(cls, value: str) -> str:
        normalized = value.strip().lower().replace(" ", "_")
        if not SYSTEM_KEY_PATTERN.fullmatch(normalized):
            raise ValueError("Системный ключ должен содержать латинские буквы, цифры и _")
        return normalized

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("text_by_language", "hint_by_language")
    @classmethod
    def clean_localized_text(cls, value: dict[str, str]) -> dict[str, str]:
        return {
            normalize_language_code(language): text.strip()
            for language, text in value.items()
            if text.strip()
        }

    @model_validator(mode="after")
    def validate_answer_keys(self) -> CallFlowNode:
        keys = [answer.key for answer in self.answers]
        if len(keys) != len(set(keys)):
            raise ValueError("Ключи вариантов ответа не должны повторяться")
        return self


class CallFlowDefinition(BaseModel):
    schema_version: Literal[1] = 1
    nodes: list[CallFlowNode] = Field(default_factory=list, max_length=250)

    @model_validator(mode="after")
    def validate_node_ids(self) -> CallFlowDefinition:
        ids = [node.id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("UUID узлов не должны повторяться")
        return self


class CallFlowCreate(BaseModel):
    project_id: UUID
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=1000)
    default_language_code: str = "ru"
    language_codes: list[str] = Field(default_factory=lambda: ["ru"], max_length=20)

    @field_validator("name", "description")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("default_language_code")
    @classmethod
    def clean_default_language(cls, value: str) -> str:
        return normalize_language_code(value)

    @field_validator("language_codes")
    @classmethod
    def clean_languages(cls, value: list[str]) -> list[str]:
        return normalize_language_codes(value)

    @model_validator(mode="after")
    def default_must_be_enabled(self) -> CallFlowCreate:
        if self.default_language_code not in self.language_codes:
            raise ValueError("Язык по умолчанию должен входить в список языков сценария")
        return self


class CallFlowUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    default_language_code: str | None = None
    language_codes: list[str] | None = Field(default=None, max_length=20)

    @field_validator("name", "description")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("default_language_code")
    @classmethod
    def clean_optional_default_language(cls, value: str | None) -> str | None:
        return normalize_language_code(value) if value is not None else None

    @field_validator("language_codes")
    @classmethod
    def clean_optional_languages(cls, value: list[str] | None) -> list[str] | None:
        return normalize_language_codes(value) if value is not None else None


class CallFlowVersionSummary(BaseModel):
    id: UUID
    version: int
    status: OperatorVersionStatus
    lock_version: int
    created_from_version_id: UUID | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CallFlowRead(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    description: str
    default_language_code: str
    language_codes: list[str]
    active_version_id: UUID | None
    is_active: bool
    archived_at: datetime | None
    versions: list[CallFlowVersionSummary]
    created_at: datetime
    updated_at: datetime


class CallFlowVersionRead(CallFlowVersionSummary):
    call_flow_id: UUID
    project_id: UUID
    definition: CallFlowDefinition


class CallFlowDraftSave(BaseModel):
    expected_lock_version: int = Field(ge=1)
    definition: CallFlowDefinition


class CallFlowDraftCreate(BaseModel):
    source_version_id: UUID | None = None


class CallFlowNodeCreate(BaseModel):
    expected_lock_version: int = Field(ge=1)
    node: CallFlowNode


class CallFlowNodeUpdate(BaseModel):
    expected_lock_version: int = Field(ge=1)
    node: CallFlowNode


class CallFlowNodeDelete(BaseModel):
    expected_lock_version: int = Field(ge=1)


class CallFlowValidationIssue(BaseModel):
    code: str
    message: str
    node_id: UUID | None = None
    node_name: str | None = None


class CallFlowValidationResult(BaseModel):
    valid: bool
    errors: list[CallFlowValidationIssue]


class CallFlowPreviewAnswer(BaseModel):
    node_id: UUID
    answer_key: str | None = Field(default=None, max_length=80)


class CallFlowPreviewRequest(BaseModel):
    language_code: str
    answers: list[CallFlowPreviewAnswer] = Field(default_factory=list, max_length=250)

    @field_validator("language_code")
    @classmethod
    def clean_language(cls, value: str) -> str:
        return normalize_language_code(value)


class CallFlowPreviewNode(BaseModel):
    id: UUID
    system_key: str
    name: str
    node_type: CallFlowNodeType
    text: str
    hint: str
    answers: list[dict[str, str]]
    action_is_inert: bool


class CallFlowPreviewResult(BaseModel):
    language_code: str
    path: list[CallFlowPreviewNode]
    current_node: CallFlowPreviewNode | None
    completed: bool
    inert_actions: list[str]
