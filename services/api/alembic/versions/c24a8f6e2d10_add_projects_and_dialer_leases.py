"""add projects and safe dialer leases

Revision ID: c24a8f6e2d10
Revises: b13f5e7d9a21
Create Date: 2026-07-28 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c24a8f6e2d10"
down_revision: str | None = "b13f5e7d9a21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PROJECT_SCOPED_TABLES = (
    "ai_operators",
    "call_flows",
    "customers",
    "customer_contacts",
    "calls",
    "callback_tasks",
)


def upgrade() -> None:
    # Existing tenant policies use transaction-local context. The migration runs as
    # the table owner and temporarily disables FORCE so every tenant can be backfilled
    # atomically without inventing a tenant context outside the application.
    for table in (*PROJECT_SCOPED_TABLES, "memberships"):
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')

    op.create_table(
        "projects",
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
        sa.Column("outbound_number", sa.String(length=32), nullable=True),
        sa.Column("max_concurrent_calls", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "working_hours",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "max_concurrent_calls > 0",
            name="ck_projects_max_concurrent_calls_positive",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'archived')",
            name="ck_projects_status_valid",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_projects_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_projects"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_projects_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_projects_tenant_name"),
    )
    op.create_index("ix_projects_tenant_id", "projects", ["tenant_id"])
    op.create_index("ix_projects_status", "projects", ["status"])
    op.create_index("ix_projects_tenant_status", "projects", ["tenant_id", "status"])
    op.create_index(
        "uq_projects_default_per_tenant",
        "projects",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )

    op.create_table(
        "project_users",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_project_users_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_project_users_tenant_project_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_project_users_tenant_user_memberships",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_project_users"),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_users_project_user"),
    )
    op.create_index("ix_project_users_tenant_id", "project_users", ["tenant_id"])
    op.create_index("ix_project_users_project_id", "project_users", ["project_id"])
    op.create_index("ix_project_users_user_id", "project_users", ["user_id"])

    for table in PROJECT_SCOPED_TABLES:
        op.add_column(table, sa.Column("project_id", sa.Uuid(), nullable=True))
        op.create_index(f"ix_{table}_project_id", table, ["project_id"])
    op.add_column("customers", sa.Column("lock_token", sa.Uuid(), nullable=True))
    op.create_index("ix_customers_lock_token", "customers", ["lock_token"])

    op.execute(
        """
        INSERT INTO projects (
          id, tenant_id, name, description, status, max_concurrent_calls,
          working_hours, is_default, created_at, updated_at
        )
        SELECT
          gen_random_uuid(), t.id, 'Основной проект',
          'Создан автоматически при переходе на проектную модель',
          'active', COALESCE(ts.max_concurrent_calls, 1), '{}'::json, true, now(), now()
        FROM tenants t
        LEFT JOIN tenant_settings ts ON ts.tenant_id = t.id
        """
    )
    op.execute(
        """
        INSERT INTO project_users (
          id, tenant_id, project_id, user_id, is_active, created_at, updated_at
        )
        SELECT gen_random_uuid(), m.tenant_id, p.id, m.user_id, m.is_active, now(), now()
        FROM memberships m
        JOIN projects p ON p.tenant_id = m.tenant_id AND p.is_default = true
        """
    )
    for table in ("ai_operators", "call_flows", "customers"):
        op.execute(
            f"""
            UPDATE {table} value
            SET project_id = project.id
            FROM projects project
            WHERE project.tenant_id = value.tenant_id AND project.is_default = true
            """
        )
    op.execute(
        """
        UPDATE customer_contacts contact
        SET project_id = customer.project_id
        FROM customers customer
        WHERE customer.id = contact.customer_id AND customer.tenant_id = contact.tenant_id
        """
    )
    op.execute(
        """
        UPDATE calls call
        SET project_id = COALESCE(
          (
            SELECT customer.project_id
            FROM customers customer
            WHERE customer.id = call.customer_id AND customer.tenant_id = call.tenant_id
          ),
          (
            SELECT operator.project_id
            FROM ai_operators operator
            WHERE operator.id = call.ai_operator_id AND operator.tenant_id = call.tenant_id
          ),
          project.id
        )
        FROM projects project
        WHERE project.tenant_id = call.tenant_id AND project.is_default = true
        """
    )
    op.execute(
        """
        UPDATE callback_tasks task
        SET project_id = customer.project_id
        FROM customers customer
        WHERE customer.id = task.customer_id AND customer.tenant_id = task.tenant_id
        """
    )

    for table in PROJECT_SCOPED_TABLES:
        op.alter_column(table, "project_id", nullable=False)

    op.create_unique_constraint(
        "uq_customers_tenant_project_id",
        "customers",
        ["tenant_id", "project_id", "id"],
    )
    for table in PROJECT_SCOPED_TABLES:
        op.create_foreign_key(
            f"fk_{table}_tenant_project_projects",
            table,
            "projects",
            ["tenant_id", "project_id"],
            ["tenant_id", "id"],
            ondelete="RESTRICT",
        )
    op.create_foreign_key(
        "fk_customer_contacts_tenant_project_customer",
        "customer_contacts",
        "customers",
        ["tenant_id", "project_id", "customer_id"],
        ["tenant_id", "project_id", "id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("uq_customer_contacts_tenant_id", "customer_contacts", type_="unique")
    op.create_index(
        "uq_customer_contacts_project_phone",
        "customer_contacts",
        ["project_id", "normalized_value"],
        unique=True,
        postgresql_where=sa.text("kind = 'phone'"),
    )
    op.drop_constraint("uq_ai_operators_tenant_id", "ai_operators", type_="unique")
    op.create_unique_constraint(
        "uq_ai_operators_project_name",
        "ai_operators",
        ["project_id", "name"],
    )
    op.create_index("ix_calls_project_status", "calls", ["project_id", "status"])
    op.create_index(
        "ix_callback_tasks_project_status_due",
        "callback_tasks",
        ["project_id", "status", "due_at"],
    )

    for table in ("projects", "project_users"):
        policy = f"{table}_tenant_isolation"
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY "{policy}" ON "{table}" '
            "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
            "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        )
    for table in (*PROJECT_SCOPED_TABLES, "memberships"):
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')

    op.execute(
        """
        INSERT INTO permissions (id, code, description, created_at, updated_at)
        VALUES
          (gen_random_uuid(), 'projects:read', 'Read permitted projects', now(), now()),
          (gen_random_uuid(), 'projects:manage', 'Manage tenant projects', now(), now())
        ON CONFLICT (code) DO NOTHING
        """
    )


def downgrade() -> None:
    for table in (*PROJECT_SCOPED_TABLES, "memberships"):
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
    for table in ("project_users", "projects"):
        op.execute(f'DROP POLICY IF EXISTS "{table}_tenant_isolation" ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')

    op.execute("DELETE FROM permissions WHERE code IN ('projects:read', 'projects:manage')")
    op.drop_index("ix_callback_tasks_project_status_due", table_name="callback_tasks")
    op.drop_index("ix_calls_project_status", table_name="calls")
    op.drop_constraint("uq_ai_operators_project_name", "ai_operators", type_="unique")
    op.create_unique_constraint(
        "uq_ai_operators_tenant_id",
        "ai_operators",
        ["tenant_id", "name"],
    )
    op.drop_index("uq_customer_contacts_project_phone", table_name="customer_contacts")
    op.create_unique_constraint(
        "uq_customer_contacts_tenant_id",
        "customer_contacts",
        ["tenant_id", "kind", "normalized_value"],
    )
    op.drop_constraint(
        "fk_customer_contacts_tenant_project_customer",
        "customer_contacts",
        type_="foreignkey",
    )
    for table in reversed(PROJECT_SCOPED_TABLES):
        op.drop_constraint(f"fk_{table}_tenant_project_projects", table, type_="foreignkey")
    op.drop_constraint("uq_customers_tenant_project_id", "customers", type_="unique")

    op.drop_index("ix_customers_lock_token", table_name="customers")
    op.drop_column("customers", "lock_token")
    for table in reversed(PROJECT_SCOPED_TABLES):
        op.drop_index(f"ix_{table}_project_id", table_name=table)
        op.drop_column(table, "project_id")

    op.drop_index("ix_project_users_user_id", table_name="project_users")
    op.drop_index("ix_project_users_project_id", table_name="project_users")
    op.drop_index("ix_project_users_tenant_id", table_name="project_users")
    op.drop_table("project_users")
    op.drop_index("uq_projects_default_per_tenant", table_name="projects")
    op.drop_index("ix_projects_tenant_status", table_name="projects")
    op.drop_index("ix_projects_status", table_name="projects")
    op.drop_index("ix_projects_tenant_id", table_name="projects")
    op.drop_table("projects")

    for table in (*PROJECT_SCOPED_TABLES, "memberships"):
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
