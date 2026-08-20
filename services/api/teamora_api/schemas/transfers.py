from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from teamora_api.enums import TransferStatus

TransferDestinationType = Literal["browser", "sip", "mobile"]


class TransferRequestCreate(BaseModel):
    reason: str = Field(min_length=2, max_length=500)
    summary: str = Field(default="", max_length=4000)
    destination_type: TransferDestinationType = "browser"
    routing_strategy: Literal["longest_idle", "round_robin", "priority"] | None = None
    max_attempts: int | None = Field(default=None, ge=1, le=20)
    expected_call_version: int | None = Field(default=None, ge=0)


class TransferClaimRequest(BaseModel):
    expected_version: int = Field(ge=1)


class TransferDeclineRequest(BaseModel):
    expected_version: int = Field(ge=1)
    reason: str = Field(default="operator_declined", min_length=2, max_length=120)


class TransferAnswerRequest(BaseModel):
    expected_version: int = Field(ge=1)
    endpoint_type: TransferDestinationType = "browser"


class OperatorEndpointUpsert(BaseModel):
    endpoint_type: TransferDestinationType
    destination: str = Field(min_length=2, max_length=160)
    display_hint: str = Field(min_length=1, max_length=80)
    is_enabled: bool = True


class OperatorEndpointRead(BaseModel):
    id: UUID
    endpoint_type: TransferDestinationType
    display_hint: str
    is_verified: bool
    is_enabled: bool
    lock_version: int


class OperatorEndpointAuthorize(BaseModel):
    expected_version: int = Field(ge=1)
    confirm_controlled_destination: bool


class TransferAttemptRead(BaseModel):
    id: UUID
    membership_id: UUID
    attempt_number: int
    destination_type: TransferDestinationType
    status: str
    offered_at: datetime
    expires_at: datetime
    claimed_at: datetime | None
    answered_at: datetime | None
    safe_error_code: str | None


class TransferRequestRead(BaseModel):
    id: UUID
    call_id: UUID
    project_id: UUID
    status: TransferStatus
    reason: str
    summary: str
    language_code: str
    routing_strategy: str
    destination_type: TransferDestinationType
    claimed_membership_id: UUID | None
    context: dict[str, object]
    attempt_count: int
    max_attempts: int
    lock_version: int
    requested_at: datetime
    offer_expires_at: datetime | None
    claimed_at: datetime | None
    connected_at: datetime | None
    resolved_at: datetime | None
    last_error_code: str | None
    attempts: list[TransferAttemptRead] = Field(default_factory=list)


class WebRtcConfigurationRead(BaseModel):
    websocket_url: str
    sip_uri: str
    authorization: str
    expires_at: datetime
    ice_servers: list[dict[str, object]]
    dtls_srtp_required: bool = True
    register_required: bool = True
    live_verification: str
