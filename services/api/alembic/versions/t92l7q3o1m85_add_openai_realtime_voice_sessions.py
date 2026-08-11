"""add durable OpenAI Realtime voice sessions

Revision ID: t92l7q3o1m85
Revises: s81k6p2n0l74
Create Date: 2026-08-05 20:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "t92l7q3o1m85"
down_revision: str | None = "s81k6p2n0l74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = ("ai_realtime_sessions", "ai_realtime_session_events")


def _tenant_columns() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


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


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_ai_operator_versions_tenant_id",
        "ai_operator_versions",
        ["tenant_id", "id"],
    )
    op.create_table(
        "ai_realtime_sessions",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("ai_operator_version_id", sa.Uuid(), nullable=False),
        sa.Column("call_flow_version_id", sa.Uuid(), nullable=True),
        sa.Column("knowledge_base_revision_id", sa.Uuid(), nullable=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("voice", sa.String(80), nullable=False),
        sa.Column("language_code", sa.String(32), nullable=False),
        sa.Column("state", sa.String(24), server_default="pending", nullable=False),
        sa.Column("state_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("provider_session_id", sa.String(200), nullable=True),
        sa.Column("enabled_tools", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False),
        sa.Column("recording_allowed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("disclosure_required", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("disclosure_played_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_type", sa.String(80), nullable=True),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("safe_error_code", sa.String(80), nullable=True),
        sa.Column("usage_snapshot", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("latency_snapshot", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("interruption_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("correlation_id", sa.String(160), nullable=False),
        *_tenant_columns(),
        sa.CheckConstraint(
            "state IN ('pending','connecting','active','reconnecting','degraded',"
            "'closing','closed','failed')",
            name="ai_realtime_session_state_valid",
        ),
        sa.CheckConstraint("state_version >= 1", name="ai_realtime_session_version_positive"),
        sa.CheckConstraint("interruption_count >= 0", name="ai_realtime_session_interruptions_non_negative"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_ai_realtime_sessions_tenant_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "call_id"],
            ["calls.tenant_id", "calls.id"],
            name="fk_ai_realtime_sessions_tenant_call",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "call_flow_version_id"],
            ["call_flow_versions.tenant_id", "call_flow_versions.project_id", "call_flow_versions.id"],
            name="fk_ai_realtime_sessions_tenant_flow_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "knowledge_base_revision_id"],
            [
                "knowledge_base_revisions.tenant_id",
                "knowledge_base_revisions.project_id",
                "knowledge_base_revisions.id",
            ],
            name="fk_ai_realtime_sessions_tenant_knowledge_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "ai_operator_version_id"],
            ["ai_operator_versions.tenant_id", "ai_operator_versions.id"],
            name="fk_ai_realtime_sessions_tenant_operator_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_ai_realtime_sessions_tenant_id"),
        sa.UniqueConstraint("tenant_id", "call_id", name="uq_ai_realtime_sessions_tenant_call"),
    )
    for column in (
        "tenant_id",
        "project_id",
        "call_id",
        "ai_operator_version_id",
        "call_flow_version_id",
        "knowledge_base_revision_id",
        "provider_session_id",
        "state",
        "last_event_at",
    ):
        op.create_index(f"ix_ai_realtime_sessions_{column}", "ai_realtime_sessions", [column])
    op.create_index(
        "ix_ai_realtime_sessions_tenant_project_state",
        "ai_realtime_sessions",
        ["tenant_id", "project_id", "state"],
    )

    op.create_table(
        "ai_realtime_session_events",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("provider_event_id", sa.String(200), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("safe_payload", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("correlation_id", sa.String(160), nullable=False),
        *_tenant_columns(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["ai_realtime_sessions.tenant_id", "ai_realtime_sessions.id"],
            name="fk_ai_realtime_events_tenant_session",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "call_id"],
            ["calls.tenant_id", "calls.id"],
            name="fk_ai_realtime_events_tenant_call",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "provider_event_id", name="uq_ai_realtime_events_provider_event"),
    )
    for column in ("tenant_id", "project_id", "session_id", "call_id"):
        op.create_index(f"ix_ai_realtime_session_events_{column}", "ai_realtime_session_events", [column])
    op.create_index(
        "ix_ai_realtime_events_session_occurred",
        "ai_realtime_session_events",
        ["tenant_id", "session_id", "occurred_at"],
    )
    op.create_index(
        "ix_ai_realtime_events_call_type",
        "ai_realtime_session_events",
        ["tenant_id", "call_id", "event_type"],
    )

    op.add_column("transcript_segments", sa.Column("language_code", sa.String(32), nullable=True))
    op.add_column("transcript_segments", sa.Column("provider_item_id", sa.String(200), nullable=True))
    op.add_column("transcript_segments", sa.Column("is_final", sa.Boolean(), nullable=True))
    op.add_column("transcript_segments", sa.Column("interrupted", sa.Boolean(), nullable=True))
    op.execute(
        "UPDATE transcript_segments SET language_code=language::text, is_final=true, interrupted=false"
    )
    op.alter_column("transcript_segments", "language_code", nullable=False, server_default="ru")
    op.alter_column("transcript_segments", "is_final", nullable=False, server_default=sa.true())
    op.alter_column("transcript_segments", "interrupted", nullable=False, server_default=sa.false())
    op.create_index("ix_transcript_segments_provider_item_id", "transcript_segments", ["provider_item_id"])

    op.add_column("usage_records", sa.Column("provider", sa.String(40), nullable=True))
    op.add_column("usage_records", sa.Column("model", sa.String(120), nullable=True))
    op.add_column("usage_records", sa.Column("provider_session_id", sa.String(200), nullable=True))
    op.add_column("usage_records", sa.Column("safe_metadata", sa.JSON(), nullable=True))
    op.add_column("usage_records", sa.Column("pricing_available", sa.Boolean(), nullable=True))
    op.execute("UPDATE usage_records SET safe_metadata='{}'::json, pricing_available=false")
    op.alter_column("usage_records", "safe_metadata", nullable=False, server_default=sa.text("'{}'::json"))
    op.alter_column("usage_records", "pricing_available", nullable=False, server_default=sa.false())
    op.create_index("ix_usage_records_provider", "usage_records", ["provider"])
    op.create_index("ix_usage_records_provider_session_id", "usage_records", ["provider_session_id"])

    for table in TENANT_TABLES:
        _enable_rls(table)


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        _disable_rls(table)
    for name in ("ix_usage_records_provider_session_id", "ix_usage_records_provider"):
        op.drop_index(name, table_name="usage_records")
    for column in ("pricing_available", "safe_metadata", "provider_session_id", "model", "provider"):
        op.drop_column("usage_records", column)
    op.drop_index("ix_transcript_segments_provider_item_id", table_name="transcript_segments")
    for column in ("interrupted", "is_final", "provider_item_id", "language_code"):
        op.drop_column("transcript_segments", column)
    op.drop_table("ai_realtime_session_events")
    op.drop_table("ai_realtime_sessions")
    # A development database may have applied an earlier, uncommitted form of
    # this revision before the tenant-aware operator FK was added.  The guard
    # keeps downgrade safe for that local state while remaining deterministic
    # for clean installations.
    op.execute(
        "ALTER TABLE ai_operator_versions "
        "DROP CONSTRAINT IF EXISTS uq_ai_operator_versions_tenant_id"
    )
