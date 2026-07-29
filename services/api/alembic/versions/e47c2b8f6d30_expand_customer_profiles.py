"""expand customer profiles

Revision ID: e47c2b8f6d30
Revises: d35b9f7a4c21
Create Date: 2026-07-29 17:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e47c2b8f6d30"
down_revision: str | None = "d35b9f7a4c21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


NEW_TENANT_TABLES = (
    "customer_field_definitions",
    "customer_imports",
)


def enable_rls(table: str) -> None:
    policy = f"{table}_tenant_isolation"
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY "{policy}" ON "{table}" '
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def upgrade() -> None:
    for table in ("customers", "customer_contacts", "project_users"):
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')

    op.create_unique_constraint(
        "uq_project_users_tenant_project_user",
        "project_users",
        ["tenant_id", "project_id", "user_id"],
    )

    customer_columns = (
        sa.Column("external_reference_normalized", sa.String(length=160), nullable=True),
        sa.Column("city", sa.String(length=160), nullable=True),
        sa.Column("region", sa.String(length=160), nullable=True),
        sa.Column("address", sa.String(length=500), nullable=True),
        sa.Column("job_title", sa.String(length=160), nullable=True),
        sa.Column("organization", sa.String(length=200), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.String(length=120), nullable=True),
        sa.Column("assigned_user_id", sa.Uuid(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    for column in customer_columns:
        op.add_column("customers", column)

    op.execute(
        """
        WITH ranked AS (
          SELECT id, lower(btrim(external_reference)) AS normalized,
                 row_number() OVER (
                   PARTITION BY tenant_id, project_id, lower(btrim(external_reference))
                   ORDER BY created_at, id
                 ) AS duplicate_number
          FROM customers
          WHERE external_reference IS NOT NULL AND btrim(external_reference) <> ''
        )
        UPDATE customers customer
        SET external_reference_normalized = CASE
          WHEN ranked.duplicate_number = 1 THEN ranked.normalized
          ELSE ranked.normalized || '#legacy:' || customer.id::text
        END
        FROM ranked
        WHERE ranked.id = customer.id
        """
    )
    op.create_index(
        "uq_customers_tenant_project_external_reference",
        "customers",
        ["tenant_id", "project_id", "external_reference_normalized"],
        unique=True,
        postgresql_where=sa.text("external_reference_normalized IS NOT NULL"),
    )
    op.create_index(
        "ix_customers_tenant_project_status_archived",
        "customers",
        ["tenant_id", "project_id", "status", "archived_at"],
    )
    op.create_index(
        "ix_customers_tenant_assigned_user",
        "customers",
        ["tenant_id", "assigned_user_id"],
    )
    op.create_index("ix_customers_assigned_user_id", "customers", ["assigned_user_id"])
    op.create_index("ix_customers_archived_at", "customers", ["archived_at"])
    op.execute(
        """
        ALTER TABLE customers
        ADD CONSTRAINT fk_customers_tenant_project_assigned_user_project_users
        FOREIGN KEY (tenant_id, project_id, assigned_user_id)
        REFERENCES project_users (tenant_id, project_id, user_id)
        ON DELETE SET NULL (assigned_user_id)
        """
    )

    op.add_column("customer_contacts", sa.Column("label", sa.String(length=80), nullable=True))
    op.drop_index("uq_customer_contacts_project_phone", table_name="customer_contacts")
    op.execute(
        """
        WITH ranked AS (
          SELECT id, lower(btrim(normalized_value)) AS normalized,
                 row_number() OVER (
                   PARTITION BY tenant_id, project_id, kind, lower(btrim(normalized_value))
                   ORDER BY created_at, id
                 ) AS duplicate_number
          FROM customer_contacts
          WHERE kind = 'email'
        )
        UPDATE customer_contacts contact
        SET normalized_value = CASE
          WHEN ranked.duplicate_number = 1 THEN ranked.normalized
          ELSE ranked.normalized || '#legacy:' || contact.id::text
        END
        FROM ranked
        WHERE ranked.id = contact.id
        """
    )
    op.execute(
        """
        WITH ranked AS (
          SELECT id, row_number() OVER (
            PARTITION BY tenant_id, project_id, customer_id, kind
            ORDER BY created_at, id
          ) AS primary_number
          FROM customer_contacts
          WHERE is_primary = true
        )
        UPDATE customer_contacts contact
        SET is_primary = false
        FROM ranked
        WHERE ranked.id = contact.id AND ranked.primary_number > 1
        """
    )
    op.create_check_constraint(
        "ck_customer_contacts_kind_valid",
        "customer_contacts",
        "kind IN ('phone', 'email')",
    )
    op.create_index(
        "uq_customer_contacts_project_kind_value",
        "customer_contacts",
        ["tenant_id", "project_id", "kind", "normalized_value"],
        unique=True,
        postgresql_where=sa.text("kind IN ('phone', 'email')"),
    )
    op.create_index(
        "uq_customer_contacts_primary_kind",
        "customer_contacts",
        ["tenant_id", "project_id", "customer_id", "kind"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
    )

    op.create_table(
        "customer_field_definitions",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("key", sa.String(length=80), nullable=False),
        sa.Column("field_type", sa.String(length=24), nullable=False),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("options", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("default_value", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "field_type IN ('text', 'textarea', 'number', 'boolean', 'date', 'datetime', "
            "'select', 'multiselect')",
            name="ck_customer_field_definitions_field_type_valid",
        ),
        sa.CheckConstraint(
            "sort_order >= 0",
            name="ck_customer_field_definitions_sort_order_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_customer_field_definitions_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_customer_field_definitions_tenant_project_projects",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_customer_field_definitions"),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "key",
            name="uq_customer_field_definitions_project_key",
        ),
    )
    op.create_index(
        "ix_customer_field_definitions_tenant_id",
        "customer_field_definitions",
        ["tenant_id"],
    )
    op.create_index(
        "ix_customer_field_definitions_project_id",
        "customer_field_definitions",
        ["project_id"],
    )
    op.create_index(
        "ix_customer_field_definitions_project_active_order",
        "customer_field_definitions",
        ["tenant_id", "project_id", "is_active", "sort_order"],
    )
    op.execute(
        """
        INSERT INTO customer_field_definitions (
          id, tenant_id, project_id, name, key, field_type, is_required,
          sort_order, options, default_value, is_active, created_at, updated_at
        )
        SELECT gen_random_uuid(), legacy.tenant_id, legacy.project_id, legacy.key, legacy.key,
               'text', false, 0, '[]'::json, NULL, false, now(), now()
        FROM (
          SELECT DISTINCT customer.tenant_id, customer.project_id, field.key
          FROM customers customer
          CROSS JOIN LATERAL jsonb_object_keys(COALESCE(customer.custom_fields::jsonb, '{}'::jsonb)) field(key)
        ) legacy
        ON CONFLICT (tenant_id, project_id, key) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO customer_field_definitions (
          id, tenant_id, project_id, name, key, field_type, is_required,
          sort_order, options, default_value, is_active, created_at, updated_at
        )
        SELECT gen_random_uuid(), project.tenant_id, project.id, 'Сегмент', 'segment',
               'text', false, 0, '[]'::json, NULL, true, now(), now()
        FROM projects project
        ON CONFLICT (tenant_id, project_id, key)
        DO UPDATE SET is_active = true, updated_at = now()
        """
    )

    op.create_table(
        "customer_imports",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_type", sa.String(length=8), nullable=False),
        sa.Column("sheet_names", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("selected_sheet", sa.String(length=160), nullable=False),
        sa.Column("source_rows", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("mapping", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("update_rule", sa.String(length=16), nullable=False, server_default="skip"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="preview"),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("report", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("idempotency_key", sa.String(length=160), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "status IN ('preview', 'committed', 'expired')",
            name="ck_customer_imports_status_valid",
        ),
        sa.CheckConstraint(
            "update_rule IN ('skip', 'update')",
            name="ck_customer_imports_update_rule_valid",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_customer_imports_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_customer_imports_tenant_project_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_customer_imports_tenant_creator_memberships",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_customer_imports"),
    )
    op.create_index("ix_customer_imports_tenant_id", "customer_imports", ["tenant_id"])
    op.create_index("ix_customer_imports_project_id", "customer_imports", ["project_id"])
    op.create_index(
        "ix_customer_imports_created_by_user_id",
        "customer_imports",
        ["created_by_user_id"],
    )
    op.create_index("ix_customer_imports_expires_at", "customer_imports", ["expires_at"])
    op.create_index(
        "ix_customer_imports_tenant_project_status",
        "customer_imports",
        ["tenant_id", "project_id", "status"],
    )
    op.create_index(
        "uq_customer_imports_tenant_idempotency_key",
        "customer_imports",
        ["tenant_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    for table in NEW_TENANT_TABLES:
        enable_rls(table)
    for table in ("customers", "customer_contacts", "project_users"):
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')


def downgrade() -> None:
    for table in ("customers", "customer_contacts", "project_users"):
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
    for table in reversed(NEW_TENANT_TABLES):
        op.execute(f'DROP POLICY IF EXISTS "{table}_tenant_isolation" ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')

    op.drop_index("uq_customer_imports_tenant_idempotency_key", table_name="customer_imports")
    op.drop_index("ix_customer_imports_tenant_project_status", table_name="customer_imports")
    op.drop_index("ix_customer_imports_expires_at", table_name="customer_imports")
    op.drop_index("ix_customer_imports_created_by_user_id", table_name="customer_imports")
    op.drop_index("ix_customer_imports_project_id", table_name="customer_imports")
    op.drop_index("ix_customer_imports_tenant_id", table_name="customer_imports")
    op.drop_table("customer_imports")

    op.drop_index(
        "ix_customer_field_definitions_project_active_order",
        table_name="customer_field_definitions",
    )
    op.drop_index("ix_customer_field_definitions_project_id", table_name="customer_field_definitions")
    op.drop_index("ix_customer_field_definitions_tenant_id", table_name="customer_field_definitions")
    op.drop_table("customer_field_definitions")

    op.drop_index("uq_customer_contacts_primary_kind", table_name="customer_contacts")
    op.drop_index("uq_customer_contacts_project_kind_value", table_name="customer_contacts")
    op.drop_constraint("ck_customer_contacts_kind_valid", "customer_contacts", type_="check")
    op.create_index(
        "uq_customer_contacts_project_phone",
        "customer_contacts",
        ["project_id", "normalized_value"],
        unique=True,
        postgresql_where=sa.text("kind = 'phone'"),
    )
    op.drop_column("customer_contacts", "label")

    op.drop_constraint(
        "fk_customers_tenant_project_assigned_user_project_users",
        "customers",
        type_="foreignkey",
    )
    op.drop_index("ix_customers_archived_at", table_name="customers")
    op.drop_index("ix_customers_assigned_user_id", table_name="customers")
    op.drop_index("ix_customers_tenant_assigned_user", table_name="customers")
    op.drop_index("ix_customers_tenant_project_status_archived", table_name="customers")
    op.drop_index("uq_customers_tenant_project_external_reference", table_name="customers")
    for column in (
        "archived_at",
        "assigned_user_id",
        "source",
        "description",
        "tags",
        "organization",
        "job_title",
        "address",
        "region",
        "city",
        "external_reference_normalized",
    ):
        op.drop_column("customers", column)

    op.drop_constraint(
        "uq_project_users_tenant_project_user",
        "project_users",
        type_="unique",
    )
    for table in ("customers", "customer_contacts", "project_users"):
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
