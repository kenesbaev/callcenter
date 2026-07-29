"""expand call flow editor

Revision ID: f58d3c9a7e41
Revises: e47c2b8f6d30
Create Date: 2026-07-29 18:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f58d3c9a7e41"
down_revision: str | None = "e47c2b8f6d30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("call_flows", "call_flow_versions", "calls"):
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')

    op.add_column(
        "call_flows",
        sa.Column("description", sa.String(length=1000), nullable=False, server_default=""),
    )
    op.add_column(
        "call_flows",
        sa.Column(
            "default_language_code",
            sa.String(length=32),
            nullable=False,
            server_default="ru",
        ),
    )
    op.add_column(
        "call_flows",
        sa.Column(
            "language_codes",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[\"ru\"]'::json"),
        ),
    )
    op.add_column(
        "call_flows",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_call_flows_archived_at", "call_flows", ["archived_at"])
    op.create_index("ix_call_flows_active_version_id", "call_flows", ["active_version_id"])
    op.create_index(
        "ix_call_flows_tenant_project_archived",
        "call_flows",
        ["tenant_id", "project_id", "archived_at"],
    )

    op.add_column(
        "call_flow_versions",
        sa.Column("project_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "call_flow_versions",
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "call_flow_versions",
        sa.Column("created_from_version_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        """
        UPDATE call_flow_versions version
        SET project_id = flow.project_id
        FROM call_flows flow
        WHERE flow.id = version.call_flow_id
          AND flow.tenant_id = version.tenant_id
        """
    )
    op.alter_column("call_flow_versions", "project_id", nullable=False)
    op.create_index("ix_call_flow_versions_project_id", "call_flow_versions", ["project_id"])
    op.create_index("ix_call_flow_versions_call_flow_id", "call_flow_versions", ["call_flow_id"])
    op.create_index(
        "ix_call_flow_versions_created_from_version_id",
        "call_flow_versions",
        ["created_from_version_id"],
    )
    op.create_unique_constraint(
        "uq_call_flow_versions_tenant_project_id",
        "call_flow_versions",
        ["tenant_id", "project_id", "id"],
    )
    op.create_unique_constraint(
        "uq_call_flow_versions_tenant_project_flow_id",
        "call_flow_versions",
        ["tenant_id", "project_id", "call_flow_id", "id"],
    )
    op.create_check_constraint(
        "ck_call_flow_versions_lock_version_positive",
        "call_flow_versions",
        "lock_version >= 1",
    )
    op.execute(
        """
        WITH ranked AS (
          SELECT id,
                 row_number() OVER (
                   PARTITION BY tenant_id, call_flow_id
                   ORDER BY version DESC, created_at DESC, id
                 ) AS draft_number
          FROM call_flow_versions
          WHERE status = 'draft'
        )
        UPDATE call_flow_versions version
        SET status = 'archived'
        FROM ranked
        WHERE ranked.id = version.id AND ranked.draft_number > 1
        """
    )
    op.create_index(
        "uq_call_flow_versions_single_draft",
        "call_flow_versions",
        ["tenant_id", "call_flow_id"],
        unique=True,
        postgresql_where=sa.text("status = 'draft'"),
    )
    op.create_index(
        "ix_call_flow_versions_flow_status_version",
        "call_flow_versions",
        ["tenant_id", "call_flow_id", "status", "version"],
    )
    op.create_foreign_key(
        "fk_call_flow_versions_created_from_version",
        "call_flow_versions",
        "call_flow_versions",
        ["created_from_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.drop_constraint(
        "fk_call_flows_active_version_id_call_flow_versions",
        "call_flows",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_call_flow_versions_call_flow_id_call_flows",
        "call_flow_versions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_call_flow_versions_tenant_project_flow",
        "call_flow_versions",
        "call_flows",
        ["tenant_id", "project_id", "call_flow_id"],
        ["tenant_id", "project_id", "id"],
        ondelete="CASCADE",
    )
    op.execute(
        """
        ALTER TABLE call_flows
        ADD CONSTRAINT fk_call_flows_active_version_same_flow
        FOREIGN KEY (tenant_id, project_id, id, active_version_id)
        REFERENCES call_flow_versions (tenant_id, project_id, call_flow_id, id)
        ON DELETE SET NULL (active_version_id)
        """
    )

    op.add_column(
        "calls",
        sa.Column("call_flow_version_id", sa.Uuid(), nullable=True),
    )
    op.create_index("ix_calls_call_flow_version_id", "calls", ["call_flow_version_id"])
    op.create_foreign_key(
        "fk_calls_tenant_project_call_flow_version",
        "calls",
        "call_flow_versions",
        ["tenant_id", "project_id", "call_flow_version_id"],
        ["tenant_id", "project_id", "id"],
        ondelete="RESTRICT",
    )

    op.execute(
        """
        INSERT INTO permissions (id, code, description, created_at, updated_at)
        VALUES
          (gen_random_uuid(), 'call_flows:read', 'Read project call flows', now(), now()),
          (gen_random_uuid(), 'call_flows:manage', 'Manage project call flows', now(), now())
        ON CONFLICT (code) DO NOTHING
        """
    )

    for table in ("call_flows", "call_flow_versions", "calls"):
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')


def downgrade() -> None:
    for table in ("call_flows", "call_flow_versions", "calls"):
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')

    op.execute("DELETE FROM permissions WHERE code IN ('call_flows:read', 'call_flows:manage')")

    op.drop_constraint(
        "fk_calls_tenant_project_call_flow_version",
        "calls",
        type_="foreignkey",
    )
    op.drop_index("ix_calls_call_flow_version_id", table_name="calls")
    op.drop_column("calls", "call_flow_version_id")

    op.drop_constraint(
        "fk_call_flows_active_version_same_flow",
        "call_flows",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_call_flow_versions_tenant_project_flow",
        "call_flow_versions",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_call_flow_versions_call_flow_id_call_flows",
        "call_flow_versions",
        "call_flows",
        ["call_flow_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_call_flows_active_version_id_call_flow_versions",
        "call_flows",
        "call_flow_versions",
        ["active_version_id"],
        ["id"],
        ondelete="SET NULL",
        use_alter=True,
    )

    op.drop_constraint(
        "fk_call_flow_versions_created_from_version",
        "call_flow_versions",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_call_flow_versions_flow_status_version",
        table_name="call_flow_versions",
    )
    op.drop_index("uq_call_flow_versions_single_draft", table_name="call_flow_versions")
    op.drop_constraint(
        "ck_call_flow_versions_lock_version_positive",
        "call_flow_versions",
        type_="check",
    )
    op.drop_constraint(
        "uq_call_flow_versions_tenant_project_flow_id",
        "call_flow_versions",
        type_="unique",
    )
    op.drop_constraint(
        "uq_call_flow_versions_tenant_project_id",
        "call_flow_versions",
        type_="unique",
    )
    op.drop_index(
        "ix_call_flow_versions_created_from_version_id",
        table_name="call_flow_versions",
    )
    op.drop_index("ix_call_flow_versions_call_flow_id", table_name="call_flow_versions")
    op.drop_index("ix_call_flow_versions_project_id", table_name="call_flow_versions")
    op.drop_column("call_flow_versions", "created_from_version_id")
    op.drop_column("call_flow_versions", "lock_version")
    op.drop_column("call_flow_versions", "project_id")

    op.drop_index("ix_call_flows_tenant_project_archived", table_name="call_flows")
    op.drop_index("ix_call_flows_active_version_id", table_name="call_flows")
    op.drop_index("ix_call_flows_archived_at", table_name="call_flows")
    op.drop_column("call_flows", "archived_at")
    op.drop_column("call_flows", "language_codes")
    op.drop_column("call_flows", "default_language_code")
    op.drop_column("call_flows", "description")

    for table in ("call_flows", "call_flow_versions", "calls"):
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
