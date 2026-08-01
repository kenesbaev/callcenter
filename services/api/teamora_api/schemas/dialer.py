from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from teamora_api.schemas.call_flows import CallFlowNode, normalize_language_code
from teamora_api.schemas.calls import CallRead, CallResultRequest, CallResultResponse
from teamora_api.schemas.crm import DialerAssignment


class DialerFlowStepRead(BaseModel):
    id: UUID
    sequence: int
    node_id: UUID
    system_key: str
    node_type: str
    language_code: str
    text_snapshot: str
    hint_snapshot: str
    selected_answer_key: str | None
    selected_answer_label: str | None
    input_value: object | None
    next_node_id: UUID | None
    action_status: str | None
    occurred_at: datetime


class DialerFlowExecutionRead(BaseModel):
    id: UUID
    call_id: UUID
    call_flow_version_id: UUID
    current_node: CallFlowNode | None
    status: str
    language_code: str
    language_codes: list[str]
    state_version: int
    values: dict[str, object]
    steps: list[DialerFlowStepRead]
    started_at: datetime
    completed_at: datetime | None


class DialerFlowStepRequest(BaseModel):
    node_id: UUID
    expected_state_version: int = Field(ge=1)
    answer_key: str | None = Field(default=None, max_length=80)
    value: object | None = None
    confirm_action: bool = False
    language_code: str | None = None

    @field_validator("language_code")
    @classmethod
    def clean_language(cls, value: str | None) -> str | None:
        return normalize_language_code(value) if value is not None else None


class DialerFlowBackRequest(BaseModel):
    expected_state_version: int = Field(ge=1)


class DialerHistoryItem(BaseModel):
    call: CallRead
    result_code: str | None = None
    result_label: str | None = None
    result_category: str | None = None
    comment: str = ""
    operator_name: str | None = None
    transcript: list[dict[str, object]] = Field(default_factory=list)
    summary: str | None = None


class DialerCompleteAndNextRequest(BaseModel):
    call_id: UUID
    customer_id: UUID
    lock_token: UUID
    expected_state_version: int = Field(ge=0)
    flow_execution_state_version: int | None = Field(default=None, ge=1)
    result: CallResultRequest
    project_id: UUID | None = None


class DialerCompleteAndNextResponse(BaseModel):
    result: CallResultResponse
    next_assignment: DialerAssignment | None
    queue_complete: bool
    replayed: bool = False


class DialerWorkspaceRead(BaseModel):
    assignment: DialerAssignment
    call: CallRead | None
    flow: DialerFlowExecutionRead | None
    history: list[DialerHistoryItem]
