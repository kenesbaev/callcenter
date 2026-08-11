from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from teamora_api.schemas.call_flows import normalize_language_code


class AIRealtimeSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    tenant_id: UUID = Field(alias="tenantId")
    project_id: UUID = Field(alias="projectId")
    call_id: UUID = Field(alias="callId")
    requested_language: str | None = Field(default=None, alias="requestedLanguage", max_length=32)
    correlation_id: str = Field(alias="correlationId", min_length=1, max_length=160)

    @field_validator("requested_language")
    @classmethod
    def normalize_language(cls, value: str | None) -> str | None:
        return normalize_language_code(value) if value else None


class AIRealtimeToolDefinition(BaseModel):
    name: str
    description: str
    input_schema: dict[str, object]
    timeout_ms: int


class AIRealtimeSessionConfiguration(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    session_id: UUID = Field(alias="sessionId")
    call_id: UUID = Field(alias="callId")
    tenant_id: UUID = Field(alias="tenantId")
    project_id: UUID = Field(alias="projectId")
    state_version: int = Field(alias="stateVersion")
    provider: str
    model: str
    voice: str
    language: str
    instructions: str
    tools: list[AIRealtimeToolDefinition]
    recording_allowed: bool = Field(alias="recordingAllowed")
    disclosure_required: bool = Field(alias="disclosureRequired")
    disclosure_text: str | None = Field(alias="disclosureText")
    vad: dict[str, object]
    reasoning_effort: str | None = Field(alias="reasoningEffort")


class AIRealtimeProviderEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    tenant_id: UUID = Field(alias="tenantId")
    project_id: UUID = Field(alias="projectId")
    call_id: UUID = Field(alias="callId")
    session_id: UUID = Field(alias="sessionId")
    provider_event_id: str = Field(alias="providerEventId", min_length=1, max_length=200)
    event_type: str = Field(alias="eventType", min_length=1, max_length=80)
    occurred_at: datetime = Field(alias="occurredAt")
    provider_timestamp: datetime | None = Field(default=None, alias="providerTimestamp")
    correlation_id: str = Field(alias="correlationId", min_length=1, max_length=160)
    safe_payload: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict, alias="safePayload"
    )


class AIRealtimeToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    tenant_id: UUID = Field(alias="tenantId")
    project_id: UUID = Field(alias="projectId")
    call_id: UUID = Field(alias="callId")
    session_id: UUID = Field(alias="sessionId")
    tool_call_id: str = Field(alias="toolCallId", min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=120)
    arguments: dict[str, object] = Field(default_factory=dict)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=8, max_length=160)
    correlation_id: str = Field(alias="correlationId", min_length=1, max_length=160)


class AIRealtimeSessionRead(BaseModel):
    id: UUID
    call_id: UUID
    project_id: UUID
    state: str
    state_version: int
    provider: str
    model: str
    voice: str
    language_code: str
    provider_session_id: str | None
    interruption_count: int
    usage: dict[str, object]
    latency: dict[str, object]
    safe_error_code: str | None
    created_at: datetime
    connected_at: datetime | None
    closed_at: datetime | None


class AIRealtimeSessionEventRead(BaseModel):
    id: UUID
    event_type: str
    aggregate_version: int
    occurred_at: datetime


class AIRealtimeToolExecutionRead(BaseModel):
    id: UUID
    tool_name: str
    status: str
    safe_result: dict[str, object] | None
    duration_ms: int | None
    created_at: datetime


class AIRealtimeCitationRead(BaseModel):
    revision_id: UUID
    citations: list[dict[str, object]]
    no_match: bool
    latency_ms: int
    created_at: datetime


class AIRealtimeUsageRead(BaseModel):
    metric: str
    quantity: str
    unit: str
    provider: str | None
    model: str | None
    pricing_available: bool
    occurred_at: datetime


class AIRealtimeSessionDetailRead(BaseModel):
    session: AIRealtimeSessionRead
    events: list[AIRealtimeSessionEventRead]
    tools: list[AIRealtimeToolExecutionRead]
    citations: list[AIRealtimeCitationRead]
    usage: list[AIRealtimeUsageRead]


class AIRealtimeLatencyRead(BaseModel):
    metric: str = "first_audio_latency_ms"
    samples: int
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None
    is_available: bool


class AIRealtimeStatusRead(BaseModel):
    configured: bool
    enabled: bool
    provider: str
    model: str
    voice: str
    live_verification: str
    last_session_state: str | None
    last_safe_error: str | None
    active_sessions: int
    latency: AIRealtimeLatencyRead


class AIRealtimeDiagnosticRead(BaseModel):
    status: str
    provider: str
    input_bytes: int = Field(alias="inputBytes")
    output_bytes: int = Field(alias="outputBytes")
    event_types: int = Field(alias="eventTypes")
    vad_verified: bool = Field(alias="vadVerified")
    transcript_verified: bool = Field(alias="transcriptVerified")
    usage_verified: bool = Field(alias="usageVerified")
    live_openai_verified: bool = Field(alias="liveOpenAiVerified")
