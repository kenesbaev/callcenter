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


class InboundTelephonyEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: str = "1"
    provider: str = Field(default="asterisk-ari", min_length=1, max_length=40)
    provider_event_id: str = Field(alias="providerEventId", min_length=1, max_length=200)
    channel_id: str = Field(alias="channelId", min_length=1, max_length=200)
    did: str = Field(pattern=r"^\+[1-9][0-9]{7,14}$")
    caller_number: str | None = Field(default=None, alias="callerNumber", max_length=32)
    source_ip: str = Field(alias="sourceIp", min_length=1, max_length=64)
    occurred_at: datetime = Field(alias="occurredAt")
    correlation_id: str = Field(alias="correlationId", min_length=1, max_length=160)
    safe_payload: dict[str, str | int | float | bool] = Field(default_factory=dict, alias="safePayload")


class InboundTelephonyAccepted(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    call_id: UUID = Field(alias="callId")
    tenant_id: UUID = Field(alias="tenantId")
    project_id: UUID = Field(alias="projectId")
    state_version: int = Field(alias="stateVersion")
    recording_allowed: bool = Field(alias="recordingAllowed")
    disclosure_required: bool = Field(alias="disclosureRequired")
    ai_session_available: bool = Field(default=False, alias="aiSessionAvailable")
    duplicate: bool = False


class TelephonyChannelUsageRead(BaseModel):
    trunk_id: UUID
    name: str
    pool_mode: str
    limit: int
    inbound_limit: int | None
    outbound_limit: int | None
    occupied: int
    inbound_occupied: int
    outbound_occupied: int
    available: int


class TelephonyStatusRead(BaseModel):
    status: str
    asterisk: str
    ari: str
    sip_trunk: str
    registration: str
    reachability: str
    external_media: str
    recording: str
    dids: list[str]
    transport: str | None
    codecs: list[str]
    channel_usage: list[TelephonyChannelUsageRead]
    last_checked_at: datetime | None
    last_safe_error: str | None
    live_signaling_verified: bool
    live_audio_verified: bool


class DidRoutingTestRequest(BaseModel):
    did: str = Field(pattern=r"^\+[1-9][0-9]{7,14}$")


class DidRoutingTestRead(BaseModel):
    matched: bool
    project_id: UUID | None = None
    phone_number_id: UUID | None = None
    trunk_id: UUID | None = None
    did_masked: str


class LocalMediaTestRequest(BaseModel):
    project_id: UUID
    codec: str = Field(default="ulaw", pattern=r"^[A-Za-z0-9_-]{2,24}$")
    packets: int = Field(default=50, ge=10, le=500)


class LiveTelephonyTestRequest(BaseModel):
    project_id: UUID
    destination: str = Field(pattern=r"^\+[1-9][0-9]{7,14}$")
    confirmation: str = Field(pattern=r"^CALL_ALLOWED_TEST_NUMBER$")


class TelephonyDiagnosticRead(BaseModel):
    id: UUID
    mode: str
    status: str
    destination_masked: str | None
    signaling_verified: bool
    inbound_audio_verified: bool
    outbound_audio_verified: bool
    dtmf_verified: bool
    codec: str | None
    media_statistics: dict[str, object]
    safe_error_code: str | None
    started_at: datetime
    completed_at: datetime | None


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
