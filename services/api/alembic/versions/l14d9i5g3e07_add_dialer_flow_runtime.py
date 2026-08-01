"""add dialer flow runtime

Revision ID: l14d9i5g3e07
Revises: k03c8h4f2d96
Create Date: 2026-08-01 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "l14d9i5g3e07"
down_revision: str | None = "k03c8h4f2d96"
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
    op.add_column(
        "customers",
        sa.Column("dialer_assignment_source", sa.String(length=16), nullable=True),
    )
    op.create_check_constraint(
        "dialer_assignment_source_valid",
        "customers",
        "dialer_assignment_source IS NULL OR dialer_assignment_source IN ('callback', 'retry', 'new')",
    )

    op.create_unique_constraint("uq_calls_tenant_call_id", "calls", ["tenant_id", "id"])

    op.create_table(
        "call_flow_executions",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("operator_user_id", sa.Uuid(), nullable=False),
        sa.Column("call_flow_version_id", sa.Uuid(), nullable=False),
        sa.Column("current_node_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=24), server_default="active", nullable=False),
        sa.Column("language_code", sa.String(length=32), server_default="ru", nullable=False),
        sa.Column("state_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("values", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'completed', 'cancelled')",
            name="status_valid",
        ),
        sa.CheckConstraint("state_version >= 1", name="state_version_positive"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["operator_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "call_id"],
            ["calls.tenant_id", "calls.id"],
            name="fk_call_flow_executions_tenant_call",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "call_flow_version_id"],
            ["call_flow_versions.tenant_id", "call_flow_versions.project_id", "call_flow_versions.id"],
            name="fk_call_flow_executions_tenant_project_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "call_id", name="uq_call_flow_executions_tenant_call"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_call_flow_executions_tenant_id"),
    )
    op.create_index("ix_call_flow_executions_tenant_id", "call_flow_executions", ["tenant_id"])
    op.create_index("ix_call_flow_executions_project_id", "call_flow_executions", ["project_id"])
    op.create_index("ix_call_flow_executions_call_id", "call_flow_executions", ["call_id"])
    op.create_index("ix_call_flow_executions_customer_id", "call_flow_executions", ["customer_id"])
    op.create_index("ix_call_flow_executions_operator_user_id", "call_flow_executions", ["operator_user_id"])
    op.create_index("ix_call_flow_executions_current_node_id", "call_flow_executions", ["current_node_id"])
    op.create_index(
        "ix_call_flow_executions_call_flow_version_id",
        "call_flow_executions",
        ["call_flow_version_id"],
    )
    op.create_index("ix_call_flow_executions_status", "call_flow_executions", ["status"])
    op.create_index(
        "ix_call_flow_executions_operator_status_updated",
        "call_flow_executions",
        ["tenant_id", "operator_user_id", "status", "updated_at"],
    )
    enable_tenant_rls("call_flow_executions")

    op.create_table(
        "call_flow_execution_steps",
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("system_key", sa.String(length=80), nullable=False),
        sa.Column("node_type", sa.String(length=40), nullable=False),
        sa.Column("language_code", sa.String(length=32), nullable=False),
        sa.Column("text_snapshot", sa.Text(), server_default="", nullable=False),
        sa.Column("hint_snapshot", sa.Text(), server_default="", nullable=False),
        sa.Column("selected_answer_key", sa.String(length=80), nullable=True),
        sa.Column("selected_answer_label", sa.String(length=240), nullable=True),
        sa.Column("input_value", sa.JSON(), nullable=True),
        sa.Column("next_node_id", sa.Uuid(), nullable=True),
        sa.Column("action_status", sa.String(length=24), nullable=True),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "execution_id"],
            ["call_flow_executions.tenant_id", "call_flow_executions.id"],
            name="fk_call_flow_execution_steps_tenant_execution",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("execution_id", "sequence", name="uq_call_flow_execution_steps_sequence"),
        sa.UniqueConstraint(
            "tenant_id",
            "execution_id",
            "idempotency_key",
            name="uq_call_flow_execution_steps_idempotency",
        ),
    )
    op.create_index("ix_call_flow_execution_steps_tenant_id", "call_flow_execution_steps", ["tenant_id"])
    op.create_index(
        "ix_call_flow_execution_steps_execution_id", "call_flow_execution_steps", ["execution_id"]
    )
    op.create_index("ix_call_flow_execution_steps_node_id", "call_flow_execution_steps", ["node_id"])
    op.create_index(
        "ix_call_flow_execution_steps_next_node_id", "call_flow_execution_steps", ["next_node_id"]
    )
    op.create_index(
        "ix_call_flow_execution_steps_actor_user_id", "call_flow_execution_steps", ["actor_user_id"]
    )
    op.create_index(
        "ix_call_flow_execution_steps_execution_created",
        "call_flow_execution_steps",
        ["execution_id", "created_at"],
    )
    enable_tenant_rls("call_flow_execution_steps")

    op.create_table(
        "dialer_completion_submissions",
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("response_payload", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "call_id"],
            ["calls.tenant_id", "calls.id"],
            name="fk_dialer_completion_submissions_tenant_call",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_dialer_completion_submissions_tenant_key",
        ),
    )
    op.create_index(
        "ix_dialer_completion_submissions_tenant_id", "dialer_completion_submissions", ["tenant_id"]
    )
    op.create_index("ix_dialer_completion_submissions_call_id", "dialer_completion_submissions", ["call_id"])
    enable_tenant_rls("dialer_completion_submissions")


def downgrade() -> None:
    op.drop_table("dialer_completion_submissions")
    op.drop_table("call_flow_execution_steps")
    op.drop_table("call_flow_executions")
    op.drop_constraint("uq_calls_tenant_call_id", "calls", type_="unique")
    # IF EXISTS keeps downgrade safe for local databases that briefly ran the
    # pre-release form of this revision before assignment-source persistence
    # was added during Stage 9 verification.
    op.execute("ALTER TABLE customers DROP CONSTRAINT IF EXISTS dialer_assignment_source_valid")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS dialer_assignment_source")
