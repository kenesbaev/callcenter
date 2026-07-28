"""add production operator workflow

Revision ID: b13f5e7d9a21
Revises: 8d6dea3e8a52
Create Date: 2026-07-28 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b13f5e7d9a21"
down_revision: str | None = "8d6dea3e8a52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "customers",
        sa.Column("status", sa.String(length=32), nullable=False, server_default="new"),
    )
    op.add_column(
        "customers",
        sa.Column(
            "custom_fields",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
    )
    op.add_column("customers", sa.Column("locked_by_user_id", sa.Uuid(), nullable=True))
    op.add_column("customers", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("customers", sa.Column("last_call_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("customers", sa.Column("next_call_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_customers_locked_by_user_id_users",
        "customers",
        "users",
        ["locked_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_customers_status", "customers", ["status"])
    op.create_index("ix_customers_locked_by_user_id", "customers", ["locked_by_user_id"])
    op.create_index("ix_customers_locked_until", "customers", ["locked_until"])
    op.create_index("ix_customers_next_call_at", "customers", ["next_call_at"])

    op.add_column("calls", sa.Column("operator_user_id", sa.Uuid(), nullable=True))
    op.add_column(
        "calls",
        sa.Column("provider", sa.String(length=40), nullable=False, server_default="mock"),
    )
    op.add_column("calls", sa.Column("from_number", sa.String(length=32), nullable=True))
    op.add_column("calls", sa.Column("to_number", sa.String(length=32), nullable=True))
    op.create_foreign_key(
        "fk_calls_operator_user_id_users",
        "calls",
        "users",
        ["operator_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_calls_operator_user_id", "calls", ["operator_user_id"])

    op.create_table(
        "callback_tasks",
        sa.Column("customer_id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=True),
        sa.Column("assigned_user_id", sa.Uuid(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["assigned_user_id"],
            ["users.id"],
            name="fk_callback_tasks_assigned_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["call_id"],
            ["calls.id"],
            name="fk_callback_tasks_call_id_calls",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name="fk_callback_tasks_customer_id_customers",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_callback_tasks_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_callback_tasks"),
    )
    op.create_index("ix_callback_tasks_customer_id", "callback_tasks", ["customer_id"])
    op.create_index("ix_callback_tasks_call_id", "callback_tasks", ["call_id"])
    op.create_index("ix_callback_tasks_assigned_user_id", "callback_tasks", ["assigned_user_id"])
    op.create_index("ix_callback_tasks_due_at", "callback_tasks", ["due_at"])
    op.create_index("ix_callback_tasks_status", "callback_tasks", ["status"])
    op.create_index("ix_callback_tasks_tenant_id", "callback_tasks", ["tenant_id"])
    op.create_index(
        "ix_callback_tasks_tenant_status_due",
        "callback_tasks",
        ["tenant_id", "status", "due_at"],
    )
    op.execute('ALTER TABLE "callback_tasks" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "callback_tasks" FORCE ROW LEVEL SECURITY')
    op.execute(
        'CREATE POLICY "callback_tasks_tenant_isolation" ON "callback_tasks" '
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.execute('DROP POLICY IF EXISTS "callback_tasks_tenant_isolation" ON "callback_tasks"')
    op.execute('ALTER TABLE "callback_tasks" DISABLE ROW LEVEL SECURITY')
    op.drop_index("ix_callback_tasks_tenant_status_due", table_name="callback_tasks")
    op.drop_index("ix_callback_tasks_tenant_id", table_name="callback_tasks")
    op.drop_index("ix_callback_tasks_status", table_name="callback_tasks")
    op.drop_index("ix_callback_tasks_due_at", table_name="callback_tasks")
    op.drop_index("ix_callback_tasks_assigned_user_id", table_name="callback_tasks")
    op.drop_index("ix_callback_tasks_call_id", table_name="callback_tasks")
    op.drop_index("ix_callback_tasks_customer_id", table_name="callback_tasks")
    op.drop_table("callback_tasks")

    op.drop_index("ix_calls_operator_user_id", table_name="calls")
    op.drop_constraint("fk_calls_operator_user_id_users", "calls", type_="foreignkey")
    op.drop_column("calls", "to_number")
    op.drop_column("calls", "from_number")
    op.drop_column("calls", "provider")
    op.drop_column("calls", "operator_user_id")

    op.drop_index("ix_customers_next_call_at", table_name="customers")
    op.drop_index("ix_customers_locked_until", table_name="customers")
    op.drop_index("ix_customers_locked_by_user_id", table_name="customers")
    op.drop_index("ix_customers_status", table_name="customers")
    op.drop_constraint("fk_customers_locked_by_user_id_users", "customers", type_="foreignkey")
    op.drop_column("customers", "next_call_at")
    op.drop_column("customers", "last_call_at")
    op.drop_column("customers", "locked_until")
    op.drop_column("customers", "locked_by_user_id")
    op.drop_column("customers", "custom_fields")
    op.drop_column("customers", "status")
