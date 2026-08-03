"""add durable realtime outbox

Revision ID: o47g2l8j6h30
Revises: n36f1k7i5g29
Create Date: 2026-08-03 11:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "o47g2l8j6h30"
down_revision: str | None = "n36f1k7i5g29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "realtime_events",
        sa.Column("cursor", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("target_membership_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("aggregate_type", sa.String(length=80), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=True),
        sa.Column("safe_payload", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("publish_status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("publish_attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.String(length=160), nullable=True),
        sa.Column("causation_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "publish_status IN ('pending', 'published')",
            name="publish_status_valid",
        ),
        sa.CheckConstraint("publish_attempts >= 0", name="publish_attempts_non_negative"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_realtime_events_tenant_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "target_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_realtime_events_tenant_target_membership",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cursor", name="uq_realtime_events_cursor"),
    )
    op.create_index("ix_realtime_events_tenant_id", "realtime_events", ["tenant_id"])
    op.create_index("ix_realtime_events_project_id", "realtime_events", ["project_id"])
    op.create_index(
        "ix_realtime_events_target_membership_id",
        "realtime_events",
        ["target_membership_id"],
    )
    op.create_index(
        "ix_realtime_events_tenant_cursor",
        "realtime_events",
        ["tenant_id", "cursor"],
    )
    op.create_index(
        "ix_realtime_events_tenant_project_cursor",
        "realtime_events",
        ["tenant_id", "project_id", "cursor"],
    )
    op.create_index(
        "ix_realtime_events_tenant_target_cursor",
        "realtime_events",
        ["tenant_id", "target_membership_id", "cursor"],
    )
    op.create_index(
        "ix_realtime_events_publish",
        "realtime_events",
        ["publish_status", "cursor"],
    )
    op.create_index("ix_realtime_events_expiry", "realtime_events", ["expires_at"])
    op.execute('ALTER TABLE "realtime_events" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "realtime_events" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY "realtime_events_tenant_isolation" ON "realtime_events" '
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.execute('ALTER TABLE "realtime_events" NO FORCE ROW LEVEL SECURITY')
    op.execute('DROP POLICY IF EXISTS "realtime_events_tenant_isolation" ON "realtime_events"')
    op.execute('ALTER TABLE "realtime_events" DISABLE ROW LEVEL SECURITY')
    op.drop_table("realtime_events")
