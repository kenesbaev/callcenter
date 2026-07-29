"""expand project configuration

Revision ID: d35b9f7a4c21
Revises: c24a8f6e2d10
Create Date: 2026-07-29 16:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d35b9f7a4c21"
down_revision: str | None = "c24a8f6e2d10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


REFERENCED_RLS_TABLES = (
    "projects",
    "phone_numbers",
    "ai_operators",
    "knowledge_sources",
    "call_flows",
)


def tenant_policy(table: str) -> None:
    policy = f"{table}_tenant_isolation"
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY "{policy}" ON "{table}" '
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def upgrade() -> None:
    # The migration role owns these FORCE-RLS tables. Temporarily disabling FORCE is
    # required for a cross-tenant backfill; application sessions never use this role.
    for table in REFERENCED_RLS_TABLES:
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')

    op.add_column(
        "projects",
        sa.Column(
            "default_language",
            sa.Enum("ru", "en", "uz", "kaa", name="languagecode", native_enum=False, length=40),
            nullable=True,
        ),
    )
    op.add_column("projects", sa.Column("timezone", sa.String(length=64), nullable=True))
    op.add_column("projects", sa.Column("outbound_phone_number_id", sa.Uuid(), nullable=True))
    op.add_column("projects", sa.Column("ai_operator_id", sa.Uuid(), nullable=True))
    op.add_column("projects", sa.Column("knowledge_source_id", sa.Uuid(), nullable=True))
    op.add_column("projects", sa.Column("call_flow_id", sa.Uuid(), nullable=True))
    op.add_column("projects", sa.Column("recording_enabled", sa.Boolean(), nullable=True))
    op.add_column(
        "projects",
        sa.Column("recording_disclosure_required", sa.Boolean(), nullable=True),
    )
    op.add_column("projects", sa.Column("max_attempts", sa.Integer(), nullable=True))
    op.add_column("projects", sa.Column("retry_intervals_minutes", sa.JSON(), nullable=True))
    op.add_column("projects", sa.Column("callback_rules", sa.JSON(), nullable=True))
    op.add_column(
        "projects",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.execute("UPDATE projects SET max_attempts = 3 WHERE max_attempts IS NULL")
    op.execute(
        "UPDATE projects SET retry_intervals_minutes = '[15, 60]'::json WHERE retry_intervals_minutes IS NULL"
    )
    op.execute("UPDATE projects SET callback_rules = '{}'::json WHERE callback_rules IS NULL")
    op.alter_column("projects", "max_attempts", nullable=False, server_default="3")
    op.alter_column(
        "projects",
        "retry_intervals_minutes",
        nullable=False,
        server_default=sa.text("'[15, 60]'::json"),
    )
    op.alter_column(
        "projects",
        "callback_rules",
        nullable=False,
        server_default=sa.text("'{}'::json"),
    )
    op.alter_column("projects", "max_concurrent_calls", nullable=True)
    op.create_check_constraint(
        "ck_projects_max_attempts_positive",
        "projects",
        "max_attempts > 0",
    )

    for column in (
        "outbound_phone_number_id",
        "ai_operator_id",
        "knowledge_source_id",
        "call_flow_id",
    ):
        op.create_index(f"ix_projects_{column}", "projects", [column])

    op.create_unique_constraint(
        "uq_phone_numbers_tenant_id_id",
        "phone_numbers",
        ["tenant_id", "id"],
    )
    op.create_unique_constraint(
        "uq_ai_operators_tenant_project_id",
        "ai_operators",
        ["tenant_id", "project_id", "id"],
    )
    op.create_unique_constraint(
        "uq_knowledge_sources_tenant_id_id",
        "knowledge_sources",
        ["tenant_id", "id"],
    )
    op.create_unique_constraint(
        "uq_call_flows_tenant_project_id",
        "call_flows",
        ["tenant_id", "project_id", "id"],
    )

    op.create_foreign_key(
        "fk_projects_tenant_outbound_phone_number_phone_numbers",
        "projects",
        "phone_numbers",
        ["tenant_id", "outbound_phone_number_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_projects_tenant_project_ai_operator_ai_operators",
        "projects",
        "ai_operators",
        ["tenant_id", "id", "ai_operator_id"],
        ["tenant_id", "project_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_projects_tenant_knowledge_source_knowledge_sources",
        "projects",
        "knowledge_sources",
        ["tenant_id", "knowledge_source_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_projects_tenant_project_call_flow_call_flows",
        "projects",
        "call_flows",
        ["tenant_id", "id", "call_flow_id"],
        ["tenant_id", "project_id", "id"],
        ondelete="RESTRICT",
    )

    op.execute(
        """
        UPDATE projects project
        SET outbound_phone_number_id = phone.id
        FROM phone_numbers phone
        WHERE phone.tenant_id = project.tenant_id
          AND phone.e164 = project.outbound_number
          AND project.outbound_phone_number_id IS NULL
        """
    )

    op.create_table(
        "project_inbound_phone_numbers",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("phone_number_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
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
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_project_inbound_phone_numbers_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_project_inbound_numbers_tenant_project_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "phone_number_id"],
            ["phone_numbers.tenant_id", "phone_numbers.id"],
            name="fk_project_inbound_numbers_tenant_phone_phone_numbers",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_project_inbound_phone_numbers"),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "phone_number_id",
            name="uq_project_inbound_numbers_project_phone",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "phone_number_id",
            name="uq_project_inbound_numbers_tenant_phone",
        ),
    )
    op.create_index(
        "ix_project_inbound_phone_numbers_tenant_id",
        "project_inbound_phone_numbers",
        ["tenant_id"],
    )
    op.create_index(
        "ix_project_inbound_phone_numbers_project_id",
        "project_inbound_phone_numbers",
        ["project_id"],
    )
    op.create_index(
        "ix_project_inbound_phone_numbers_phone_number_id",
        "project_inbound_phone_numbers",
        ["phone_number_id"],
    )

    op.create_table(
        "call_result_catalogs",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False, server_default="Результаты звонка"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("id", sa.Uuid(), nullable=False),
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
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_call_result_catalogs_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_call_result_catalogs_tenant_project_projects",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_call_result_catalogs"),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            name="uq_call_result_catalogs_tenant_project",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "id",
            name="uq_call_result_catalogs_tenant_project_id",
        ),
    )
    op.create_index(
        "ix_call_result_catalogs_tenant_id",
        "call_result_catalogs",
        ["tenant_id"],
    )
    op.create_index(
        "ix_call_result_catalogs_project_id",
        "call_result_catalogs",
        ["project_id"],
    )
    op.execute(
        """
        INSERT INTO call_result_catalogs (
          id, tenant_id, project_id, name, is_active, created_at, updated_at
        )
        SELECT gen_random_uuid(), tenant_id, id, 'Результаты звонка', true, now(), now()
        FROM projects
        """
    )

    tenant_policy("project_inbound_phone_numbers")
    tenant_policy("call_result_catalogs")
    for table in REFERENCED_RLS_TABLES:
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')


def downgrade() -> None:
    for table in REFERENCED_RLS_TABLES:
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
    for table in ("call_result_catalogs", "project_inbound_phone_numbers"):
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS "{table}_tenant_isolation" ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')

    op.drop_index("ix_call_result_catalogs_project_id", table_name="call_result_catalogs")
    op.drop_index("ix_call_result_catalogs_tenant_id", table_name="call_result_catalogs")
    op.drop_table("call_result_catalogs")
    op.drop_index(
        "ix_project_inbound_phone_numbers_phone_number_id",
        table_name="project_inbound_phone_numbers",
    )
    op.drop_index(
        "ix_project_inbound_phone_numbers_project_id",
        table_name="project_inbound_phone_numbers",
    )
    op.drop_index(
        "ix_project_inbound_phone_numbers_tenant_id",
        table_name="project_inbound_phone_numbers",
    )
    op.drop_table("project_inbound_phone_numbers")

    for constraint in (
        "fk_projects_tenant_project_call_flow_call_flows",
        "fk_projects_tenant_knowledge_source_knowledge_sources",
        "fk_projects_tenant_project_ai_operator_ai_operators",
        "fk_projects_tenant_outbound_phone_number_phone_numbers",
    ):
        op.drop_constraint(constraint, "projects", type_="foreignkey")

    op.drop_constraint("uq_call_flows_tenant_project_id", "call_flows", type_="unique")
    op.drop_constraint(
        "uq_knowledge_sources_tenant_id_id",
        "knowledge_sources",
        type_="unique",
    )
    op.drop_constraint(
        "uq_ai_operators_tenant_project_id",
        "ai_operators",
        type_="unique",
    )
    op.drop_constraint("uq_phone_numbers_tenant_id_id", "phone_numbers", type_="unique")

    op.execute(
        """
        UPDATE projects project
        SET max_concurrent_calls = COALESCE(settings.max_concurrent_calls, 1)
        FROM tenant_settings settings
        WHERE settings.tenant_id = project.tenant_id
          AND project.max_concurrent_calls IS NULL
        """
    )
    op.execute("UPDATE projects SET max_concurrent_calls = 1 WHERE max_concurrent_calls IS NULL")
    op.alter_column("projects", "max_concurrent_calls", nullable=False)
    op.drop_constraint("ck_projects_max_attempts_positive", "projects", type_="check")
    for column in (
        "call_flow_id",
        "knowledge_source_id",
        "ai_operator_id",
        "outbound_phone_number_id",
    ):
        op.drop_index(f"ix_projects_{column}", table_name="projects")

    for column in (
        "archived_at",
        "callback_rules",
        "retry_intervals_minutes",
        "max_attempts",
        "recording_disclosure_required",
        "recording_enabled",
        "call_flow_id",
        "knowledge_source_id",
        "ai_operator_id",
        "outbound_phone_number_id",
        "timezone",
        "default_language",
    ):
        op.drop_column("projects", column)

    for table in REFERENCED_RLS_TABLES:
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
