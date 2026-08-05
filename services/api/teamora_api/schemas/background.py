from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, ValidationInfo, field_validator

BackgroundJobStatus = Literal[
    "pending",
    "scheduled",
    "running",
    "retry_wait",
    "completed",
    "failed",
    "dead_letter",
    "cancel_requested",
    "cancelled",
]


class BackgroundJobRead(BaseModel):
    id: UUID
    project_id: UUID | None
    type: str
    queue: str
    priority: int
    status: BackgroundJobStatus
    payload_summary: dict[str, object] = Field(default_factory=dict)
    progress: int
    attempt_count: int
    max_attempts: int
    scheduled_at: datetime | None
    available_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    heartbeat_at: datetime | None
    safe_error_code: str | None
    safe_error_message: str | None
    result_metadata: dict[str, object] = Field(default_factory=dict)
    state_version: int
    correlation_id: str | None = None
    causation_id: UUID | None = None
    can_cancel: bool
    can_retry: bool
    created_at: datetime
    updated_at: datetime


class BackgroundJobAttemptRead(BaseModel):
    id: UUID
    job_id: UUID
    attempt: int
    worker_id: str | None
    status: str
    started_at: datetime
    completed_at: datetime | None
    duration_ms: int | None
    safe_error_code: str | None
    safe_error_message: str | None


class BackgroundJobEventRead(BaseModel):
    id: UUID
    job_id: UUID
    event_type: str
    progress: int | None
    safe_snapshot: dict[str, object]
    occurred_at: datetime


class BackgroundQueueStatistics(BaseModel):
    queued: int
    running: int
    retry_wait: int
    completed: int
    failed: int
    dead_letter: int
    cancel_requested: int
    oldest_pending_at: datetime | None
    can_manage: bool


class JobCommandRequest(BaseModel):
    expected_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=500)


class StorageCategorySummary(BaseModel):
    category: str
    object_count: int
    bytes: int


class StorageSummaryRead(BaseModel):
    object_count: int
    total_bytes: int
    issue_count: int
    pending_purge_count: int
    categories: list[StorageCategorySummary]
    last_scan_at: datetime | None
    can_scan: bool


class StorageConsistencyIssueRead(BaseModel):
    id: UUID
    project_id: UUID | None
    object_id: UUID | None
    issue_type: str
    status: str
    object_category: str
    detected_at: datetime
    last_verified_at: datetime | None
    grace_expires_at: datetime | None


class StorageScanRequest(BaseModel):
    project_id: UUID | None = None
    dry_run: bool = True


class RetentionPolicyRead(BaseModel):
    id: UUID
    automatic_purge_enabled: bool
    grace_period_days: int
    call_recording_days: int | None
    transcript_days: int | None
    temporary_import_days: int | None
    import_report_days: int | None
    archived_knowledge_days: int | None
    realtime_event_hours: int | None
    completed_job_days: int | None
    failed_job_days: int | None
    state_version: int
    updated_at: datetime
    can_manage: bool


class RetentionPolicyUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    automatic_purge_enabled: bool | None = None
    grace_period_days: int | None = Field(default=None, ge=1, le=90)
    call_recording_days: int | None = Field(default=None, ge=1, le=3650)
    transcript_days: int | None = Field(default=None, ge=1, le=3650)
    temporary_import_days: int | None = Field(default=None, ge=1, le=365)
    import_report_days: int | None = Field(default=None, ge=1, le=3650)
    archived_knowledge_days: int | None = Field(default=None, ge=1, le=3650)
    realtime_event_hours: int | None = Field(default=None, ge=1, le=24 * 365)
    completed_job_days: int | None = Field(default=None, ge=1, le=3650)
    failed_job_days: int | None = Field(default=None, ge=1, le=3650)


class RetentionPreviewRequest(BaseModel):
    policy_version: int = Field(ge=1)


class RetentionPreviewRead(BaseModel):
    id: UUID
    policy_version: int
    eligible_objects: int
    eligible_bytes: int
    excluded_by_legal_hold: int
    excluded_by_active_reference: int
    created_at: datetime


class RetentionRunRequest(BaseModel):
    preview_id: UUID
    policy_version: int = Field(ge=1)


class RetentionCandidateRead(BaseModel):
    id: UUID
    project_id: UUID | None
    object_category: str
    owner_aggregate_type: str
    owner_aggregate_id: UUID | None
    status: str
    eligible_at: datetime
    purge_after: datetime | None
    bytes: int
    legal_hold: bool
    can_cancel: bool
    state_version: int


LegalHoldScope = Literal["tenant", "project", "call", "customer", "document"]


class LegalHoldCreate(BaseModel):
    project_id: UUID | None = None
    scope_type: LegalHoldScope
    scope_id: UUID | None = None
    reason: str = Field(min_length=3, max_length=1000)

    @field_validator("scope_id")
    @classmethod
    def validate_scope_id(cls, value: UUID | None, info: ValidationInfo) -> UUID | None:
        if info.data.get("scope_type") != "tenant" and value is None:
            raise ValueError("scope_id is required for non-tenant legal holds")
        return value


class LegalHoldRelease(BaseModel):
    expected_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=1000)


class LegalHoldRead(BaseModel):
    id: UUID
    project_id: UUID | None
    scope_type: LegalHoldScope
    scope_id: UUID | None
    reason: str
    created_by_user_id: UUID
    created_at: datetime
    released_by_user_id: UUID | None
    released_at: datetime | None
    can_release: bool
    state_version: int


class BackgroundImportRead(BaseModel):
    id: UUID
    job_id: UUID | None
    project_id: UUID
    file_name: str
    file_type: str
    selected_sheet: str
    sheet_names: list[str]
    mapping: dict[str, str]
    update_rule: Literal["skip", "update"]
    status: str
    progress: int
    headers: list[str]
    preview_rows: list[dict[str, object]]
    total_rows: int
    valid_rows: int
    invalid_rows: int
    duplicate_rows: int
    created: int
    updated: int
    skipped: int
    error_count: int
    processing_duration_ms: int | None
    state_version: int
    can_commit: bool
    can_cancel: bool
    can_retry: bool
    report_available: bool
    safe_error_code: str | None
    created_at: datetime
    completed_at: datetime | None


class BackgroundImportCommitRequest(BaseModel):
    expected_version: int = Field(ge=1)


class BackgroundImportReportRead(BaseModel):
    id: UUID
    total_rows: int
    valid_rows: int
    invalid_rows: int
    duplicates: int
    created: int
    updated: int
    skipped: int
    cancelled: bool
    processing_duration_ms: int | None
    error_codes: dict[str, int]
    completed_at: datetime | None
