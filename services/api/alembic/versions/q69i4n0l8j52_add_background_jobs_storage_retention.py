"""add durable background jobs, storage registry, and retention controls

Revision ID: q69i4n0l8j52
Revises: p58h3m9k7i41
Create Date: 2026-08-04 12:00:00.000000
"""

import os
import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "q69i4n0l8j52"
down_revision: str | None = "p58h3m9k7i41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


NEW_TENANT_TABLES = (
    "background_jobs",
    "background_job_attempts",
    "background_job_events",
    "scheduled_jobs",
    "job_command_submissions",
    "storage_objects",
    "storage_consistency_issues",
    "retention_policies",
    "retention_candidates",
    "legal_holds",
    "customer_import_staging_rows",
)


def _storage_bucket() -> str:
    configured = op.get_context().config.get_main_option("teamora.minio_bucket")
    bucket = (configured or os.environ.get("MINIO_BUCKET", "teamora-private")).strip()
    if (
        not 3 <= len(bucket) <= 63
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]", bucket) is None
        or ".." in bucket
    ):
        raise RuntimeError("MINIO_BUCKET is invalid for the Stage 14 storage registry backfill")
    return bucket


def _enable_rls(table: str) -> None:
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY "{table}_tenant_isolation" ON "{table}" '
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def _disable_rls(table: str) -> None:
    op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
    op.execute(f'DROP POLICY IF EXISTS "{table}_tenant_isolation" ON "{table}"')
    op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')


def _tenant_columns() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def upgrade() -> None:
    storage_bucket = _storage_bucket()
    op.create_table(
        "background_jobs",
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("type", sa.String(80), nullable=False),
        sa.Column("queue", sa.String(40), server_default="default", nullable=False),
        sa.Column("priority", sa.Integer(), server_default="50", nullable=False),
        sa.Column("status", sa.String(24), server_default="pending", nullable=False),
        sa.Column("safe_payload", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="4", nullable=False),
        sa.Column("lease_owner", sa.String(160), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("progress", sa.Integer(), server_default="0", nullable=False),
        sa.Column("correlation_id", sa.String(160), nullable=False),
        sa.Column("causation_id", sa.Uuid(), nullable=True),
        sa.Column("result_metadata", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("safe_error_code", sa.String(80), nullable=True),
        sa.Column("safe_error_message", sa.String(500), nullable=True),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        *_tenant_columns(),
        sa.CheckConstraint(
            "status IN ('pending','scheduled','running','retry_wait','completed','failed',"
            "'dead_letter','cancel_requested','cancelled')",
            name="background_job_status_valid",
        ),
        sa.CheckConstraint("priority >= 0 AND priority <= 100", name="background_job_priority_valid"),
        sa.CheckConstraint("attempt_count >= 0", name="background_job_attempt_count_non_negative"),
        sa.CheckConstraint("max_attempts > 0", name="background_job_max_attempts_positive"),
        sa.CheckConstraint("progress >= 0 AND progress <= 100", name="background_job_progress_valid"),
        sa.CheckConstraint("lock_version >= 1", name="background_job_lock_version_positive"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_background_jobs_tenant_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_background_jobs_tenant_creator",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_background_jobs_tenant_id_id"),
    )
    for column in (
        "tenant_id",
        "project_id",
        "created_by_user_id",
        "lease_owner",
        "lease_token",
        "lease_expires_at",
        "causation_id",
    ):
        op.create_index(f"ix_background_jobs_{column}", "background_jobs", [column])
    op.create_index(
        "uq_background_jobs_tenant_type_idempotency",
        "background_jobs",
        ["tenant_id", "type", "idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_background_jobs_claim",
        "background_jobs",
        ["queue", "status", "available_at", "priority", "created_at"],
    )
    op.create_index(
        "ix_background_jobs_tenant_status_type",
        "background_jobs",
        ["tenant_id", "status", "type"],
    )
    op.create_index(
        "ix_background_jobs_lease_expiry",
        "background_jobs",
        ["status", "lease_expires_at"],
    )
    op.create_index(
        "ix_background_jobs_creator",
        "background_jobs",
        ["tenant_id", "created_by_user_id", "created_at"],
    )

    op.create_table(
        "background_job_attempts",
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(160), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(24), server_default="running", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("safe_error_code", sa.String(80), nullable=True),
        sa.Column("safe_error_message", sa.String(500), nullable=True),
        sa.Column("result_metadata", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        *_tenant_columns(),
        sa.CheckConstraint("attempt_number > 0", name="background_job_attempt_number_positive"),
        sa.CheckConstraint(
            "status IN ('running','completed','failed','cancelled','lease_lost')",
            name="background_job_attempt_status_valid",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_background_job_attempts_tenant_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["background_jobs.tenant_id", "background_jobs.id"],
            name="fk_background_job_attempts_tenant_job",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "job_id", "attempt_number", name="uq_background_job_attempts_job_number"
        ),
    )
    for column in ("tenant_id", "project_id", "job_id", "lease_token"):
        op.create_index(f"ix_background_job_attempts_{column}", "background_job_attempts", [column])
    op.create_index(
        "ix_background_job_attempts_job_started",
        "background_job_attempts",
        ["tenant_id", "job_id", "started_at"],
    )

    op.create_table(
        "background_job_events",
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("safe_snapshot", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("correlation_id", sa.String(160), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        *_tenant_columns(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_background_job_events_tenant_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["background_jobs.tenant_id", "background_jobs.id"],
            name="fk_background_job_events_tenant_job",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_background_job_events_tenant_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("tenant_id", "project_id", "job_id", "actor_user_id"):
        op.create_index(f"ix_background_job_events_{column}", "background_job_events", [column])
    op.create_index(
        "ix_background_job_events_job_occurred",
        "background_job_events",
        ["tenant_id", "job_id", "occurred_at"],
    )
    op.create_index(
        "ix_background_job_events_type_occurred",
        "background_job_events",
        ["tenant_id", "event_type", "occurred_at"],
    )

    op.create_table(
        "scheduled_jobs",
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("job_type", sa.String(80), nullable=False),
        sa.Column("queue", sa.String(40), server_default="system", nullable=False),
        sa.Column("priority", sa.Integer(), server_default="50", nullable=False),
        sa.Column("safe_payload", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("schedule_key", sa.String(160), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_enqueued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        *_tenant_columns(),
        sa.CheckConstraint("priority >= 0 AND priority <= 100", name="scheduled_job_priority_valid"),
        sa.CheckConstraint("interval_seconds > 0", name="scheduled_job_interval_positive"),
        sa.CheckConstraint("lock_version >= 1", name="scheduled_job_lock_version_positive"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_scheduled_jobs_tenant_project",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("tenant_id", "project_id", "next_run_at"):
        op.create_index(f"ix_scheduled_jobs_{column}", "scheduled_jobs", [column])
    op.create_index(
        "uq_scheduled_jobs_tenant_global_key",
        "scheduled_jobs",
        ["tenant_id", "schedule_key"],
        unique=True,
        postgresql_where=sa.text("project_id IS NULL"),
    )
    op.create_index(
        "uq_scheduled_jobs_tenant_project_key",
        "scheduled_jobs",
        ["tenant_id", "project_id", "schedule_key"],
        unique=True,
        postgresql_where=sa.text("project_id IS NOT NULL"),
    )
    op.create_index("ix_scheduled_jobs_due", "scheduled_jobs", ["enabled", "next_run_at"])

    op.create_table(
        "job_command_submissions",
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("submitted_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("command", sa.String(40), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), server_default="accepted", nullable=False),
        sa.Column("response_metadata", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        *_tenant_columns(),
        sa.CheckConstraint(
            "status IN ('accepted','completed','rejected')",
            name="job_command_submission_status_valid",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_job_command_submissions_tenant_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["background_jobs.tenant_id", "background_jobs.id"],
            name="fk_job_command_submissions_tenant_job",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "submitted_by_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_job_command_submissions_tenant_submitter",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_job_command_submissions_tenant_idempotency"
        ),
    )
    for column in ("tenant_id", "project_id", "job_id", "submitted_by_user_id"):
        op.create_index(f"ix_job_command_submissions_{column}", "job_command_submissions", [column])
    op.create_index(
        "ix_job_command_submissions_job_created",
        "job_command_submissions",
        ["tenant_id", "job_id", "created_at"],
    )

    op.create_table(
        "storage_objects",
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("bucket", sa.String(63), nullable=False),
        sa.Column("object_key", sa.String(1024), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("owner_aggregate_type", sa.String(80), nullable=True),
        sa.Column("owner_aggregate_id", sa.Uuid(), nullable=True),
        sa.Column("checksum_sha256", sa.String(64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("content_type", sa.String(160), server_default="application/octet-stream", nullable=False),
        sa.Column("status", sa.String(24), server_default="uploading", nullable=False),
        sa.Column("retention_state", sa.String(24), server_default="retained", nullable=False),
        sa.Column("legal_hold", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("missing_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pending_purge_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        *_tenant_columns(),
        sa.CheckConstraint(
            "category IN ('knowledge_original','import_source','import_report','temporary_preview',"
            "'call_recording','generated_report','other')",
            name="storage_object_category_valid",
        ),
        sa.CheckConstraint(
            "status IN ('uploading','active','archived','missing','orphan_candidate',"
            "'pending_purge','purged')",
            name="storage_object_status_valid",
        ),
        sa.CheckConstraint(
            "retention_state IN ('retained','eligible','pending_purge','purged')",
            name="storage_object_retention_state_valid",
        ),
        sa.CheckConstraint("size_bytes >= 0", name="storage_object_size_non_negative"),
        sa.CheckConstraint("lock_version >= 1", name="storage_object_lock_version_positive"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_storage_objects_tenant_project",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_storage_objects_tenant_id_id"),
        sa.UniqueConstraint("bucket", "object_key", name="uq_storage_objects_bucket_object_key"),
    )
    for column in (
        "tenant_id",
        "project_id",
        "owner_aggregate_id",
        "expires_at",
        "last_verified_at",
        "missing_at",
        "pending_purge_at",
    ):
        op.create_index(f"ix_storage_objects_{column}", "storage_objects", [column])
    op.create_index(
        "ix_storage_objects_owner",
        "storage_objects",
        ["tenant_id", "owner_aggregate_type", "owner_aggregate_id"],
    )
    op.create_index(
        "ix_storage_objects_consistency",
        "storage_objects",
        ["status", "last_verified_at", "missing_at"],
    )
    op.create_index(
        "ix_storage_objects_retention",
        "storage_objects",
        ["tenant_id", "retention_state", "pending_purge_at"],
    )
    op.create_index("ix_storage_objects_expiry", "storage_objects", ["expires_at", "status"])

    op.create_table(
        "storage_consistency_issues",
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("storage_object_id", sa.Uuid(), nullable=True),
        sa.Column("issue_type", sa.String(40), nullable=False),
        sa.Column("status", sa.String(24), server_default="open", nullable=False),
        sa.Column("bucket", sa.String(63), nullable=False),
        sa.Column("object_key", sa.String(1024), nullable=False),
        sa.Column("safe_metadata", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column(
            "first_detected_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "last_detected_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grace_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        *_tenant_columns(),
        sa.CheckConstraint(
            "issue_type IN ('missing_object','orphan_object','checksum_mismatch','size_mismatch',"
            "'incomplete_multipart','expired_temporary')",
            name="storage_consistency_issue_type_valid",
        ),
        sa.CheckConstraint(
            "status IN ('open','confirmed','resolved','ignored')",
            name="storage_consistency_issue_status_valid",
        ),
        sa.CheckConstraint("lock_version >= 1", name="storage_consistency_issue_lock_version_positive"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_storage_consistency_issues_tenant_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "storage_object_id"],
            ["storage_objects.tenant_id", "storage_objects.id"],
            name="fk_storage_consistency_issues_tenant_object",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("tenant_id", "project_id", "storage_object_id", "grace_until"):
        op.create_index(f"ix_storage_consistency_issues_{column}", "storage_consistency_issues", [column])
    op.create_index(
        "uq_storage_consistency_issues_active",
        "storage_consistency_issues",
        ["tenant_id", "issue_type", "bucket", "object_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('open','confirmed')"),
    )
    op.create_index(
        "ix_storage_consistency_issues_status_grace",
        "storage_consistency_issues",
        ["status", "grace_until"],
    )
    op.create_index(
        "ix_storage_consistency_issues_object",
        "storage_consistency_issues",
        ["tenant_id", "storage_object_id"],
    )

    op.create_table(
        "retention_policies",
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("policy_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("grace_period_days", sa.Integer(), server_default="7", nullable=False),
        sa.Column("recording_days", sa.Integer(), nullable=True),
        sa.Column("transcript_days", sa.Integer(), nullable=True),
        sa.Column("temporary_import_days", sa.Integer(), nullable=True),
        sa.Column("import_report_days", sa.Integer(), nullable=True),
        sa.Column("archived_knowledge_days", sa.Integer(), nullable=True),
        sa.Column("realtime_event_days", sa.Integer(), nullable=True),
        sa.Column("completed_job_days", sa.Integer(), nullable=True),
        sa.Column("failed_job_days", sa.Integer(), nullable=True),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        *_tenant_columns(),
        sa.CheckConstraint("policy_version >= 1", name="retention_policy_version_positive"),
        sa.CheckConstraint("grace_period_days > 0", name="retention_policy_grace_positive"),
        sa.CheckConstraint(
            "recording_days IS NULL OR recording_days > 0",
            name="retention_policy_recording_days_positive",
        ),
        sa.CheckConstraint(
            "transcript_days IS NULL OR transcript_days > 0",
            name="retention_policy_transcript_days_positive",
        ),
        sa.CheckConstraint(
            "temporary_import_days IS NULL OR temporary_import_days > 0",
            name="retention_policy_temporary_import_days_positive",
        ),
        sa.CheckConstraint(
            "import_report_days IS NULL OR import_report_days > 0",
            name="retention_policy_import_report_days_positive",
        ),
        sa.CheckConstraint(
            "archived_knowledge_days IS NULL OR archived_knowledge_days > 0",
            name="retention_policy_archived_knowledge_days_positive",
        ),
        sa.CheckConstraint(
            "realtime_event_days IS NULL OR realtime_event_days > 0",
            name="retention_policy_realtime_event_days_positive",
        ),
        sa.CheckConstraint(
            "completed_job_days IS NULL OR completed_job_days > 0",
            name="retention_policy_completed_job_days_positive",
        ),
        sa.CheckConstraint(
            "failed_job_days IS NULL OR failed_job_days > 0",
            name="retention_policy_failed_job_days_positive",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_retention_policies_tenant_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "updated_by_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_retention_policies_tenant_updater",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_retention_policies_tenant_id_id"),
    )
    for column in ("tenant_id", "project_id", "updated_by_user_id"):
        op.create_index(f"ix_retention_policies_{column}", "retention_policies", [column])
    op.create_index(
        "uq_retention_policies_tenant_default",
        "retention_policies",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("project_id IS NULL"),
    )
    op.create_index(
        "uq_retention_policies_tenant_project",
        "retention_policies",
        ["tenant_id", "project_id"],
        unique=True,
        postgresql_where=sa.text("project_id IS NOT NULL"),
    )

    op.create_table(
        "retention_candidates",
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("storage_object_id", sa.Uuid(), nullable=True),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("resource_type", sa.String(80), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), server_default="eligible", nullable=False),
        sa.Column("eligible_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pending_purge_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("grace_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.String(500), nullable=True),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("safe_metadata", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        *_tenant_columns(),
        sa.CheckConstraint(
            "status IN ('eligible','pending_purge','blocked','purged','cancelled')",
            name="retention_candidate_status_valid",
        ),
        sa.CheckConstraint("policy_version >= 1", name="retention_candidate_policy_version_positive"),
        sa.CheckConstraint("lock_version >= 1", name="retention_candidate_lock_version_positive"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_retention_candidates_tenant_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            ["retention_policies.tenant_id", "retention_policies.id"],
            name="fk_retention_candidates_tenant_policy",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "storage_object_id"],
            ["storage_objects.tenant_id", "storage_objects.id"],
            name="fk_retention_candidates_tenant_object",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_retention_candidates_tenant_idempotency"
        ),
    )
    for column in (
        "tenant_id",
        "project_id",
        "policy_id",
        "storage_object_id",
        "resource_id",
        "grace_until",
    ):
        op.create_index(f"ix_retention_candidates_{column}", "retention_candidates", [column])
    op.create_index(
        "ix_retention_candidates_due",
        "retention_candidates",
        ["status", "grace_until"],
    )
    op.create_index(
        "ix_retention_candidates_resource",
        "retention_candidates",
        ["tenant_id", "resource_type", "resource_id"],
    )

    op.create_table(
        "legal_holds",
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("scope_type", sa.String(24), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.String(1000), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("released_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        *_tenant_columns(),
        sa.CheckConstraint(
            "scope_type IN ('tenant','project','call','customer','document')",
            name="legal_hold_scope_type_valid",
        ),
        sa.CheckConstraint(
            "(scope_type = 'tenant' AND scope_id IS NULL) OR "
            "(scope_type <> 'tenant' AND scope_id IS NOT NULL)",
            name="legal_hold_scope_id_valid",
        ),
        sa.CheckConstraint("lock_version >= 1", name="legal_hold_lock_version_positive"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_legal_holds_tenant_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_legal_holds_tenant_creator",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "released_by_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_legal_holds_tenant_releaser",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "tenant_id",
        "project_id",
        "scope_id",
        "created_by_user_id",
        "released_by_user_id",
        "released_at",
    ):
        op.create_index(f"ix_legal_holds_{column}", "legal_holds", [column])
    op.create_index(
        "uq_legal_holds_active_tenant_scope",
        "legal_holds",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL AND scope_type = 'tenant'"),
    )
    op.create_index(
        "uq_legal_holds_active_resource_scope",
        "legal_holds",
        ["tenant_id", "scope_type", "scope_id"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL AND scope_type <> 'tenant'"),
    )
    op.create_index(
        "ix_legal_holds_project_active",
        "legal_holds",
        ["tenant_id", "project_id", "released_at"],
    )

    for table in NEW_TENANT_TABLES[:-1]:
        _enable_rls(table)

    op.add_column("knowledge_document_versions", sa.Column("storage_object_id", sa.Uuid(), nullable=True))
    op.create_index(
        "ix_knowledge_document_versions_storage_object_id",
        "knowledge_document_versions",
        ["storage_object_id"],
    )
    op.add_column("document_ingestion_jobs", sa.Column("background_job_id", sa.Uuid(), nullable=True))
    op.create_index(
        "ix_document_ingestion_jobs_background_job_id",
        "document_ingestion_jobs",
        ["background_job_id"],
    )

    op.get_bind().execute(
        sa.text(
            """
        INSERT INTO storage_objects (
          id, tenant_id, project_id, bucket, object_key, category,
          owner_aggregate_type, owner_aggregate_id, checksum_sha256, size_bytes,
          content_type, status, retention_state, legal_hold, archived_at, lock_version,
          created_at, updated_at
        )
        SELECT DISTINCT ON (version.object_key)
          gen_random_uuid(), version.tenant_id, version.project_id, :storage_bucket,
          version.object_key, 'knowledge_original', 'knowledge_document_version', version.id,
          version.checksum_sha256, version.content_length, version.content_type,
          CASE WHEN version.status <> 'archived'
                     AND (revision.status = 'draft'
                          OR source.active_revision_id = version.revision_id)
               THEN 'active' ELSE 'archived' END,
          'retained', false,
          CASE WHEN version.status <> 'archived'
                     AND (revision.status = 'draft'
                          OR source.active_revision_id = version.revision_id)
               THEN NULL ELSE version.updated_at END,
          1, version.created_at, version.updated_at
        FROM knowledge_document_versions AS version
        JOIN knowledge_base_revisions AS revision
          ON revision.tenant_id = version.tenant_id
         AND revision.id = version.revision_id
        JOIN knowledge_sources AS source
          ON source.tenant_id = revision.tenant_id
         AND source.id = revision.knowledge_source_id
        WHERE version.object_key IS NOT NULL
        ORDER BY version.object_key,
          (version.status <> 'archived'
           AND (revision.status = 'draft'
                OR COALESCE(source.active_revision_id = version.revision_id, false))) DESC,
          version.updated_at DESC, version.created_at, version.id
        """
        ),
        {"storage_bucket": storage_bucket},
    )
    op.get_bind().execute(
        sa.text(
            """
        UPDATE knowledge_document_versions AS version
        SET storage_object_id = object.id
        FROM storage_objects AS object
        WHERE object.bucket = :storage_bucket
          AND object.object_key = version.object_key
          AND object.tenant_id = version.tenant_id
        """
        ),
        {"storage_bucket": storage_bucket},
    )
    op.create_foreign_key(
        "fk_knowledge_document_versions_tenant_storage_object",
        "knowledge_document_versions",
        "storage_objects",
        ["tenant_id", "storage_object_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )

    op.execute(
        """
        INSERT INTO background_jobs (
          id, tenant_id, project_id, created_by_user_id, type, queue, priority, status,
          safe_payload, idempotency_key, scheduled_at, available_at, started_at,
          completed_at, cancelled_at, attempt_count, max_attempts, lease_owner,
          lease_token, lease_expires_at, heartbeat_at, progress, correlation_id,
          result_metadata, safe_error_code, safe_error_message, lock_version,
          created_at, updated_at
        )
        SELECT job.id, job.tenant_id, job.project_id, NULL, 'knowledge.ingest_document', 'knowledge', 50,
          CASE
            WHEN job.status = 'processing' THEN 'running'
            WHEN job.status = 'succeeded' THEN 'completed'
            WHEN job.status = 'cancelled' THEN 'cancelled'
            WHEN job.status = 'failed' THEN 'failed'
            WHEN job.status = 'pending' AND job.attempts > 0 THEN 'retry_wait'
            ELSE 'pending'
          END,
          jsonb_build_object('document_version_id', job.document_version_id),
          job.idempotency_key, job.created_at, job.next_attempt_at,
          CASE WHEN job.attempts > 0 THEN job.created_at ELSE NULL END,
          CASE WHEN job.status IN ('succeeded','failed') THEN COALESCE(job.completed_at, job.updated_at)
               ELSE NULL END,
          CASE WHEN job.status = 'cancelled' THEN job.updated_at ELSE NULL END,
          job.attempts, job.max_attempts, NULL, job.lease_token, job.lease_expires_at,
          job.heartbeat_at,
          CASE job.stage WHEN 'extracting' THEN 25 WHEN 'chunking' THEN 50
                         WHEN 'embedding' THEN 75 WHEN 'ready' THEN 100 ELSE 0 END,
          'legacy-ingestion:' || job.id::text,
          jsonb_build_object('stage', job.stage, 'document_version_id', job.document_version_id),
          job.safe_error_code, NULL, 1, job.created_at, job.updated_at
        FROM document_ingestion_jobs AS job
        """
    )
    op.execute(
        """
        INSERT INTO background_job_attempts (
          id, tenant_id, project_id, job_id, attempt_number, worker_id, lease_token,
          status, started_at, heartbeat_at, completed_at, safe_error_code,
          safe_error_message, result_metadata, created_at, updated_at
        )
        SELECT gen_random_uuid(), job.tenant_id, job.project_id, job.id, job.attempts,
          'legacy-knowledge-worker', COALESCE(job.lease_token, gen_random_uuid()),
          CASE WHEN job.status = 'processing' THEN 'running'
               WHEN job.status = 'succeeded' THEN 'completed'
               WHEN job.status = 'cancelled' THEN 'cancelled'
               ELSE 'failed' END,
          job.created_at, job.heartbeat_at,
          CASE WHEN job.status = 'processing' THEN NULL ELSE COALESCE(job.completed_at, job.updated_at) END,
          job.safe_error_code, NULL, jsonb_build_object('legacy_backfill', true, 'stage', job.stage),
          job.created_at, job.updated_at
        FROM document_ingestion_jobs AS job
        WHERE job.attempts > 0
        """
    )
    op.execute(
        """
        INSERT INTO background_job_events (
          id, tenant_id, project_id, job_id, event_type, safe_snapshot,
          actor_user_id, correlation_id, occurred_at, created_at, updated_at
        )
        SELECT gen_random_uuid(), job.tenant_id, job.project_id, job.id, 'job.migrated',
          jsonb_build_object('legacy_status', job.status, 'legacy_stage', job.stage),
          NULL, 'legacy-ingestion:' || job.id::text, job.updated_at, now(), now()
        FROM document_ingestion_jobs AS job
        """
    )
    op.execute("UPDATE document_ingestion_jobs SET background_job_id = id")
    op.create_foreign_key(
        "fk_document_ingestion_jobs_tenant_background_job",
        "document_ingestion_jobs",
        "background_jobs",
        ["tenant_id", "background_job_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )

    op.add_column("customer_imports", sa.Column("execution_mode", sa.String(16), nullable=True))
    op.add_column("customer_imports", sa.Column("background_job_id", sa.Uuid(), nullable=True))
    op.add_column("customer_imports", sa.Column("source_storage_object_id", sa.Uuid(), nullable=True))
    op.add_column("customer_imports", sa.Column("report_storage_object_id", sa.Uuid(), nullable=True))
    op.add_column("customer_imports", sa.Column("progress", sa.Integer(), nullable=True))
    for column in (
        "total_rows",
        "valid_rows",
        "invalid_rows",
        "duplicate_rows",
        "created_count",
        "updated_count",
        "skipped_count",
    ):
        op.add_column("customer_imports", sa.Column(column, sa.Integer(), nullable=True))
    op.add_column("customer_imports", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "customer_imports", sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "customer_imports", sa.Column("processing_completed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("customer_imports", sa.Column("mapping_snapshot", sa.JSON(), nullable=True))
    op.add_column("customer_imports", sa.Column("update_policy_snapshot", sa.String(16), nullable=True))
    op.alter_column("customer_imports", "status", type_=sa.String(24), existing_type=sa.String(16))
    op.drop_constraint("ck_customer_imports_status_valid", "customer_imports", type_="check")
    op.create_check_constraint(
        "ck_customer_imports_status_valid",
        "customer_imports",
        "status IN ('preview','committed','expired','processing_preview','ready','queued','running',"
        "'finalizing','completed','failed','cancel_requested','cancelled')",
    )
    op.create_check_constraint(
        "ck_customer_imports_progress_valid",
        "customer_imports",
        "progress IS NULL OR (progress >= 0 AND progress <= 100)",
    )
    op.create_unique_constraint(
        "uq_customer_imports_tenant_project_id",
        "customer_imports",
        ["tenant_id", "project_id", "id"],
    )
    for column in ("background_job_id", "source_storage_object_id", "report_storage_object_id"):
        op.create_index(f"ix_customer_imports_{column}", "customer_imports", [column])
    op.create_foreign_key(
        "fk_customer_imports_tenant_background_job",
        "customer_imports",
        "background_jobs",
        ["tenant_id", "background_job_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_customer_imports_tenant_source_storage_object",
        "customer_imports",
        "storage_objects",
        ["tenant_id", "source_storage_object_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_customer_imports_tenant_report_storage_object",
        "customer_imports",
        "storage_objects",
        ["tenant_id", "report_storage_object_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.execute(
        """
        UPDATE customer_imports
        SET execution_mode = 'synchronous',
            progress = CASE WHEN status = 'committed' THEN 100 ELSE 0 END,
            total_rows = row_count,
            valid_rows = GREATEST(0, row_count - COALESCE(json_array_length(report->'errors'), 0)),
            invalid_rows = COALESCE(json_array_length(report->'errors'), 0),
            duplicate_rows = CASE WHEN COALESCE(report->>'duplicates', '') ~ '^[0-9]+$'
                                  THEN (report->>'duplicates')::integer ELSE 0 END,
            created_count = CASE WHEN COALESCE(report->>'created', '') ~ '^[0-9]+$'
                                 THEN (report->>'created')::integer ELSE 0 END,
            updated_count = CASE WHEN COALESCE(report->>'updated', '') ~ '^[0-9]+$'
                                 THEN (report->>'updated')::integer ELSE 0 END,
            skipped_count = CASE WHEN COALESCE(report->>'skipped', '') ~ '^[0-9]+$'
                                 THEN (report->>'skipped')::integer ELSE 0 END,
            processing_completed_at = committed_at,
            mapping_snapshot = mapping,
            update_policy_snapshot = update_rule
        """
    )

    op.create_table(
        "customer_import_staging_rows",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("import_id", sa.Uuid(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), server_default="staged", nullable=False),
        sa.Column("normalized_payload", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("identity_hashes", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False),
        sa.Column("safe_errors", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False),
        sa.Column("target_customer_id", sa.Uuid(), nullable=True),
        sa.Column("result_action", sa.String(24), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        *_tenant_columns(),
        sa.CheckConstraint("row_number > 0", name="customer_import_staging_row_number_positive"),
        sa.CheckConstraint(
            "status IN ('staged','valid','invalid','duplicate','merged','skipped')",
            name="customer_import_staging_status_valid",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_customer_import_staging_rows_tenant_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "import_id"],
            ["customer_imports.tenant_id", "customer_imports.project_id", "customer_imports.id"],
            name="fk_customer_import_staging_rows_tenant_import",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "target_customer_id"],
            ["customers.tenant_id", "customers.project_id", "customers.id"],
            name="fk_customer_import_staging_rows_tenant_customer",
            ondelete="SET NULL (target_customer_id)",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "import_id", "row_number", name="uq_customer_import_staging_rows_import_row"
        ),
    )
    for column in ("tenant_id", "project_id", "import_id", "target_customer_id"):
        op.create_index(f"ix_customer_import_staging_rows_{column}", "customer_import_staging_rows", [column])
    op.create_index(
        "ix_customer_import_staging_rows_import_status",
        "customer_import_staging_rows",
        ["tenant_id", "import_id", "status", "row_number"],
    )
    _enable_rls("customer_import_staging_rows")

    op.add_column("call_recordings", sa.Column("storage_object_id", sa.Uuid(), nullable=True))
    op.add_column(
        "call_recordings",
        sa.Column("retention_state", sa.String(24), server_default="retained", nullable=False),
    )
    op.add_column("call_recordings", sa.Column("retention_eligible_at", sa.DateTime(timezone=True)))
    op.add_column("call_recordings", sa.Column("pending_purge_at", sa.DateTime(timezone=True)))
    op.add_column("call_recordings", sa.Column("purged_at", sa.DateTime(timezone=True)))
    op.create_unique_constraint("uq_call_recordings_tenant_id_id", "call_recordings", ["tenant_id", "id"])
    op.create_check_constraint(
        "ck_call_recordings_retention_state_valid",
        "call_recordings",
        "retention_state IN ('retained','eligible','pending_purge','purged')",
    )
    for column in (
        "storage_object_id",
        "retention_eligible_at",
        "pending_purge_at",
        "purged_at",
    ):
        op.create_index(f"ix_call_recordings_{column}", "call_recordings", [column])
    op.create_index(
        "ix_call_recordings_tenant_retention",
        "call_recordings",
        ["tenant_id", "retention_state", "delete_after"],
    )
    op.create_foreign_key(
        "fk_call_recordings_tenant_call",
        "call_recordings",
        "calls",
        ["tenant_id", "call_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    op.get_bind().execute(
        sa.text(
            """
        INSERT INTO storage_objects (
          id, tenant_id, project_id, bucket, object_key, category,
          owner_aggregate_type, owner_aggregate_id, size_bytes, content_type,
          status, retention_state, legal_hold, purged_at, lock_version, created_at, updated_at
        )
        SELECT DISTINCT ON (recording.storage_key)
          gen_random_uuid(), recording.tenant_id, call.project_id, :storage_bucket,
          recording.storage_key, 'call_recording', 'call_recording', recording.id,
          recording.size_bytes, recording.content_type,
          CASE WHEN recording.deleted_at IS NULL THEN 'active' ELSE 'purged' END,
          CASE WHEN recording.deleted_at IS NULL THEN 'retained' ELSE 'purged' END,
          false, recording.deleted_at, 1, recording.created_at, recording.updated_at
        FROM call_recordings AS recording
        JOIN calls AS call ON call.tenant_id = recording.tenant_id AND call.id = recording.call_id
        ORDER BY recording.storage_key, recording.created_at, recording.id
        """
        ),
        {"storage_bucket": storage_bucket},
    )
    op.get_bind().execute(
        sa.text(
            """
        UPDATE call_recordings AS recording
        SET storage_object_id = object.id,
            retention_state = CASE WHEN recording.deleted_at IS NULL THEN 'retained' ELSE 'purged' END,
            purged_at = recording.deleted_at
        FROM storage_objects AS object
        WHERE object.bucket = :storage_bucket
          AND object.object_key = recording.storage_key
          AND object.tenant_id = recording.tenant_id
        """
        ),
        {"storage_bucket": storage_bucket},
    )
    op.create_foreign_key(
        "fk_call_recordings_tenant_storage_object",
        "call_recordings",
        "storage_objects",
        ["tenant_id", "storage_object_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )

    op.add_column("transcript_segments", sa.Column("retention_redacted_at", sa.DateTime(timezone=True)))
    op.create_index(
        "ix_transcript_segments_retention_redacted_at",
        "transcript_segments",
        ["retention_redacted_at"],
    )
    op.create_foreign_key(
        "fk_transcript_segments_tenant_call",
        "transcript_segments",
        "calls",
        ["tenant_id", "call_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )

    op.execute(
        """
        INSERT INTO retention_policies (
          id, tenant_id, project_id, enabled, policy_version, grace_period_days,
          recording_days, transcript_days, temporary_import_days, import_report_days,
          archived_knowledge_days, realtime_event_days, completed_job_days, failed_job_days,
          updated_by_user_id, created_at, updated_at
        )
        SELECT gen_random_uuid(), tenant.id, NULL, false, 1, 7,
          settings.retention_days, NULL, 1, 30, NULL, 1, 90, 180,
          NULL, now(), now()
        FROM tenants AS tenant
        LEFT JOIN tenant_settings AS settings ON settings.tenant_id = tenant.id
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE customer_imports
        SET status = CASE
          WHEN status IN ('completed','committed') THEN 'committed'
          WHEN status = 'expired' THEN 'expired'
          WHEN status IN ('preview','ready') AND source_rows::jsonb <> '{}'::jsonb THEN 'preview'
          ELSE 'expired'
        END
        """
    )

    op.drop_constraint("fk_transcript_segments_tenant_call", "transcript_segments", type_="foreignkey")
    op.drop_index("ix_transcript_segments_retention_redacted_at", table_name="transcript_segments")
    op.drop_column("transcript_segments", "retention_redacted_at")

    op.drop_constraint("fk_call_recordings_tenant_storage_object", "call_recordings", type_="foreignkey")
    op.drop_constraint("fk_call_recordings_tenant_call", "call_recordings", type_="foreignkey")
    op.drop_index("ix_call_recordings_tenant_retention", table_name="call_recordings")
    for column in (
        "purged_at",
        "pending_purge_at",
        "retention_eligible_at",
        "storage_object_id",
    ):
        op.drop_index(f"ix_call_recordings_{column}", table_name="call_recordings")
    op.drop_constraint("ck_call_recordings_retention_state_valid", "call_recordings", type_="check")
    op.drop_constraint("uq_call_recordings_tenant_id_id", "call_recordings", type_="unique")
    for column in (
        "purged_at",
        "pending_purge_at",
        "retention_eligible_at",
        "retention_state",
        "storage_object_id",
    ):
        op.drop_column("call_recordings", column)

    _disable_rls("customer_import_staging_rows")
    op.drop_table("customer_import_staging_rows")

    for constraint in (
        "fk_customer_imports_tenant_report_storage_object",
        "fk_customer_imports_tenant_source_storage_object",
        "fk_customer_imports_tenant_background_job",
    ):
        op.drop_constraint(constraint, "customer_imports", type_="foreignkey")
    for column in ("report_storage_object_id", "source_storage_object_id", "background_job_id"):
        op.drop_index(f"ix_customer_imports_{column}", table_name="customer_imports")
    op.drop_constraint("uq_customer_imports_tenant_project_id", "customer_imports", type_="unique")
    op.drop_constraint("ck_customer_imports_progress_valid", "customer_imports", type_="check")
    op.drop_constraint("ck_customer_imports_status_valid", "customer_imports", type_="check")
    op.alter_column("customer_imports", "status", type_=sa.String(16), existing_type=sa.String(24))
    op.create_check_constraint(
        "ck_customer_imports_status_valid",
        "customer_imports",
        "status IN ('preview','committed','expired')",
    )
    for column in (
        "update_policy_snapshot",
        "mapping_snapshot",
        "processing_completed_at",
        "processing_started_at",
        "cancelled_at",
        "skipped_count",
        "updated_count",
        "created_count",
        "duplicate_rows",
        "invalid_rows",
        "valid_rows",
        "total_rows",
        "progress",
        "report_storage_object_id",
        "source_storage_object_id",
        "background_job_id",
        "execution_mode",
    ):
        op.drop_column("customer_imports", column)

    op.drop_constraint(
        "fk_document_ingestion_jobs_tenant_background_job",
        "document_ingestion_jobs",
        type_="foreignkey",
    )
    op.drop_index("ix_document_ingestion_jobs_background_job_id", table_name="document_ingestion_jobs")
    op.drop_column("document_ingestion_jobs", "background_job_id")

    op.drop_constraint(
        "fk_knowledge_document_versions_tenant_storage_object",
        "knowledge_document_versions",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_knowledge_document_versions_storage_object_id",
        table_name="knowledge_document_versions",
    )
    op.drop_column("knowledge_document_versions", "storage_object_id")

    for table in reversed(NEW_TENANT_TABLES[:-1]):
        _disable_rls(table)

    for table in (
        "legal_holds",
        "retention_candidates",
        "retention_policies",
        "storage_consistency_issues",
        "storage_objects",
        "job_command_submissions",
        "scheduled_jobs",
        "background_job_events",
        "background_job_attempts",
        "background_jobs",
    ):
        op.drop_table(table)
