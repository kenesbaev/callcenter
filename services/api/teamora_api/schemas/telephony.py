from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from teamora_api.enums import (
    CallDirection,
    CallStatus,
    HangupCause,
    TelephonyCallState,
    TelephonyCommandName,
)


class TelephonyCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: str = "1"
    command: TelephonyCommandName
    tenant_id: UUID = Field(alias="tenantId")
    project_id: UUID = Field(alias="projectId")
    call_id: UUID = Field(alias="callId")
    provider_call_id: str | None = Field(default=None, alias="providerCallId", max_length=200)
    command_id: UUID = Field(alias="commandId")
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=160)
    timestamp: datetime
    correlation_id: str = Field(alias="correlationId", min_length=1, max_length=160)
    parameters: dict[str, str | int | float | bool] = Field(default_factory=dict)


class TelephonyCommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    command_id: UUID = Field(alias="commandId")
    provider: str
    accepted: bool
    state: TelephonyCallState
    provider_call_id: str | None = Field(default=None, alias="providerCallId")
    occurred_at: datetime = Field(alias="occurredAt")
    safe_metadata: dict[str, str | int | float | bool] = Field(default_factory=dict, alias="safeMetadata")


class TelephonyProviderEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: str = "1"
    provider: str = Field(min_length=1, max_length=40)
    provider_event_id: str = Field(alias="providerEventId", min_length=1, max_length=200)
    event_type: str = Field(alias="eventType", min_length=1, max_length=80)
    tenant_id: UUID = Field(alias="tenantId")
    project_id: UUID = Field(alias="projectId")
    call_id: UUID = Field(alias="callId")
    external_call_id: str | None = Field(default=None, alias="externalCallId", max_length=200)
    occurred_at: datetime = Field(alias="occurredAt")
    provider_timestamp: datetime | None = Field(default=None, alias="providerTimestamp")
    correlation_id: str = Field(alias="correlationId", min_length=1, max_length=160)
    safe_payload: dict[str, str | int | float | bool] = Field(default_factory=dict, alias="safePayload")


class CallTransferRequest(BaseModel):
    destination: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=500)


class CallLastEventRead(BaseModel):
    event_type: str
    sequence: int
    occurred_at: datetime


class CallStateRead(BaseModel):
    call_id: UUID
    status: CallStatus
    state_version: int
    direction: CallDirection
    provider: str
    provider_call_id: str | None
    state: CallStatus
    recording_state: str
    allowed_actions: list[str]
    transfer_state: CallStatus | None
    started_at: datetime | None
    ringing_at: datetime | None
    answered_at: datetime | None
    held_at: datetime | None
    ended_at: datetime | None
    hangup_cause: HangupCause | None
    terminal: bool
    last_event: CallLastEventRead | None
    updated_at: datetime


class CallReconciliationRead(BaseModel):
    call: CallStateRead
    provider_state: TelephonyCallState | None
    reconciled: bool
    ignored_reason: str | None = None
