"""add telephony control plane

Revision ID: j92b7g3e1c85
Revises: i81a6f2d0b74
Create Date: 2026-07-30 14:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "j92b7g3e1c85"
down_revision: str | None = "i81a6f2d0b74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def enable_tenant_rls(table: str) -> None:
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY "{table}_tenant_isolation" ON "{table}" '
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def upgrade() -> None:
    op.add_column("calls", sa.Column("provider_state", sa.String(length=40), nullable=True))
    op.add_column("calls", sa.Column("recording_state", sa.String(length=40), nullable=True))
    op.add_column("calls", sa.Column("provider_metadata", sa.JSON(), nullable=True))
    op.execute(
        "UPDATE calls SET provider_state = status, recording_state = 'stopped', "
        "provider_metadata = '{}'::json"
    )
    op.alter_column("calls", "provider_state", nullable=False, server_default="queued")
    op.alter_column("calls", "recording_state", nullable=False, server_default="stopped")
    op.alter_column("calls", "provider_metadata", nullable=False, server_default=sa.text("'{}'::json"))

    op.add_column("call_events", sa.Column("provider", sa.String(length=40), nullable=True))
    op.add_column("call_events", sa.Column("provider_event_id", sa.String(length=200), nullable=True))
    op.add_column("call_events", sa.Column("external_call_id", sa.String(length=200), nullable=True))
    op.add_column("call_events", sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("call_events", sa.Column("provider_timestamp", sa.DateTime(timezone=True), nullable=True))
    op.add_column("call_events", sa.Column("correlation_id", sa.String(length=160), nullable=True))
    op.execute("UPDATE call_events SET occurred_at = created_at")
    op.alter_column("call_events", "occurred_at", nullable=False, server_default=sa.text("now()"))
    op.create_index(
        "uq_call_events_provider_event",
        "call_events",
        ["tenant_id", "provider", "provider_event_id"],
        unique=True,
        postgresql_where=sa.text("provider_event_id IS NOT NULL"),
    )

    op.create_table(
        "telephony_command_submissions",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("command_name", sa.String(length=40), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="pending", nullable=False),
        sa.Column("response_payload", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("external_call_id", sa.String(length=200), nullable=True),
        sa.Column("correlation_id", sa.String(length=160), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["call_id"], ["calls.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_telephony_commands_tenant_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("command_id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_telephony_commands_tenant_key"),
    )
    op.create_index(
        "ix_telephony_commands_call_created",
        "telephony_command_submissions",
        ["call_id", "created_at"],
    )
    op.create_index(
        op.f("ix_telephony_command_submissions_tenant_id"),
        "telephony_command_submissions",
        ["tenant_id"],
    )
    op.create_index(
        op.f("ix_telephony_command_submissions_project_id"),
        "telephony_command_submissions",
        ["project_id"],
    )
    op.create_index(
        op.f("ix_telephony_command_submissions_call_id"),
        "telephony_command_submissions",
        ["call_id"],
    )
    enable_tenant_rls("telephony_command_submissions")


def downgrade() -> None:
    op.execute(
        'DROP POLICY IF EXISTS "telephony_command_submissions_tenant_isolation" '
        'ON "telephony_command_submissions"'
    )
    op.drop_table("telephony_command_submissions")
    op.drop_index("uq_call_events_provider_event", table_name="call_events")
    for column in (
        "correlation_id",
        "provider_timestamp",
        "occurred_at",
        "external_call_id",
        "provider_event_id",
        "provider",
    ):
        op.drop_column("call_events", column)
    for column in ("provider_metadata", "recording_state", "provider_state"):
        op.drop_column("calls", column)
