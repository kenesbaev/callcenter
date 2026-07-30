"""add call result submission log

Revision ID: h70f5e1c9a63
Revises: g69e4d0b8f52
Create Date: 2026-07-29 21:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "h70f5e1c9a63"
down_revision: str | None = "g69e4d0b8f52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_call_outcomes_tenant_id",
        "call_outcomes",
        ["tenant_id", "id"],
    )
    op.create_table(
        "call_result_submissions",
        sa.Column("outcome_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("response_payload", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_call_result_submissions_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "outcome_id"],
            ["call_outcomes.tenant_id", "call_outcomes.id"],
            name="fk_call_result_submissions_tenant_outcome",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_call_result_submissions")),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_call_result_submissions_tenant_idempotency_key",
        ),
    )
    op.create_index(
        op.f("ix_call_result_submissions_tenant_id"),
        "call_result_submissions",
        ["tenant_id"],
    )
    op.create_index(
        op.f("ix_call_result_submissions_outcome_id"),
        "call_result_submissions",
        ["outcome_id"],
    )
    op.execute('ALTER TABLE "call_result_submissions" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "call_result_submissions" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY "call_result_submissions_tenant_isolation" '
        'ON "call_result_submissions" '
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.execute(
        'DROP POLICY IF EXISTS "call_result_submissions_tenant_isolation" '
        'ON "call_result_submissions"'
    )
    op.drop_index(
        op.f("ix_call_result_submissions_outcome_id"),
        table_name="call_result_submissions",
    )
    op.drop_index(
        op.f("ix_call_result_submissions_tenant_id"),
        table_name="call_result_submissions",
    )
    op.drop_table("call_result_submissions")
    op.drop_constraint(
        op.f("uq_call_outcomes_tenant_id"),
        "call_outcomes",
        type_="unique",
    )
