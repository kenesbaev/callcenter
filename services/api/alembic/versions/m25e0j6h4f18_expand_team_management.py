"""expand team management and operator presence

Revision ID: m25e0j6h4f18
Revises: l14d9i5g3e07
Create Date: 2026-08-01 15:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "m25e0j6h4f18"
down_revision: str | None = "l14d9i5g3e07"
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
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("memberships", sa.Column("phone", sa.String(length=32), nullable=True))
    op.add_column("memberships", sa.Column("job_title", sa.String(length=120), nullable=True))
    op.add_column(
        "memberships",
        sa.Column("interface_language", sa.String(length=32), server_default="ru", nullable=False),
    )
    op.add_column("memberships", sa.Column("timezone", sa.String(length=64), nullable=True))
    op.add_column("memberships", sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("memberships", sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "memberships", sa.Column("presence_last_seen_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("memberships", sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("memberships", sa.Column("blocked_reason", sa.String(length=500), nullable=True))
    op.add_column("memberships", sa.Column("state_version", sa.Integer(), server_default="1", nullable=False))
    op.execute(
        "UPDATE memberships SET invited_at = created_at, activated_at = created_at, "
        "presence_last_seen_at = now() WHERE is_active"
    )
    op.create_check_constraint("state_version_positive", "memberships", "state_version >= 1")
    op.create_unique_constraint("uq_memberships_tenant_id_id", "memberships", ["tenant_id", "id"])
    op.create_index("ix_memberships_presence_last_seen_at", "memberships", ["presence_last_seen_at"])

    op.add_column(
        "human_operators",
        sa.Column("is_transfer_available", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.create_unique_constraint("uq_human_operators_tenant_id_id", "human_operators", ["tenant_id", "id"])
    op.create_foreign_key(
        "fk_human_operators_tenant_membership",
        "human_operators",
        "memberships",
        ["tenant_id", "membership_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_operator_statuses_tenant_human_operator",
        "operator_statuses",
        "human_operators",
        ["tenant_id", "human_operator_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )

    op.add_column(
        "invitations",
        sa.Column("status", sa.String(length=40), server_default="pending", nullable=False),
    )
    op.add_column("invitations", sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("invitations", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("invitations", sa.Column("invited_by_membership_id", sa.Uuid(), nullable=True))
    op.add_column("invitations", sa.Column("accepted_user_id", sa.Uuid(), nullable=True))
    op.add_column("invitations", sa.Column("accepted_membership_id", sa.Uuid(), nullable=True))
    op.add_column("invitations", sa.Column("state_version", sa.Integer(), server_default="1", nullable=False))
    op.execute(
        "UPDATE invitations SET issued_at = created_at, "
        "status = CASE WHEN accepted_at IS NOT NULL THEN 'accepted' "
        "WHEN expires_at <= now() THEN 'expired' ELSE 'pending' END"
    )
    op.execute(
        "UPDATE invitations i SET invited_by_membership_id = m.id FROM memberships m "
        "WHERE m.tenant_id = i.tenant_id AND m.user_id = i.invited_by_user_id"
    )
    op.alter_column("invitations", "issued_at", nullable=False)
    op.create_check_constraint(
        "status_valid",
        "invitations",
        "status IN ('pending', 'accepted', 'cancelled', 'expired')",
    )
    op.create_check_constraint("state_version_positive", "invitations", "state_version >= 1")
    op.create_unique_constraint("uq_invitations_tenant_id_id", "invitations", ["tenant_id", "id"])
    op.create_foreign_key(
        "fk_invitations_accepted_user_id_users",
        "invitations",
        "users",
        ["accepted_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_invitations_tenant_invited_by_membership",
        "invitations",
        "memberships",
        ["tenant_id", "invited_by_membership_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_invitations_tenant_accepted_membership",
        "invitations",
        "memberships",
        ["tenant_id", "accepted_membership_id"],
        ["tenant_id", "id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_invitations_invited_by_membership_id", "invitations", ["invited_by_membership_id"]
    )
    op.create_index(
        "ix_invitations_accepted_membership_id", "invitations", ["accepted_membership_id"]
    )
    op.create_index("ix_invitations_status", "invitations", ["status"])
    op.create_index(
        "uq_invitations_active_email",
        "invitations",
        ["tenant_id", "email"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "invitation_projects",
        sa.Column("invitation_id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "invitation_id"],
            ["invitations.tenant_id", "invitations.id"],
            name="fk_invitation_projects_tenant_invitation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_invitation_projects_tenant_project",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "invitation_id", "project_id", name="uq_invitation_projects_scope"),
    )
    op.create_index("ix_invitation_projects_tenant_id", "invitation_projects", ["tenant_id"])
    op.create_index("ix_invitation_projects_invitation_id", "invitation_projects", ["invitation_id"])
    op.create_index("ix_invitation_projects_project_id", "invitation_projects", ["project_id"])

    op.create_table(
        "operator_presences",
        sa.Column("membership_id", sa.Uuid(), nullable=False),
        sa.Column("session_key", sa.String(length=80), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_operator_presences_tenant_membership",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "membership_id", "session_key", name="uq_operator_presences_session"
        ),
    )
    op.create_index("ix_operator_presences_tenant_id", "operator_presences", ["tenant_id"])
    op.create_index("ix_operator_presences_membership_id", "operator_presences", ["membership_id"])
    op.create_index(
        "ix_operator_presences_active_heartbeat",
        "operator_presences",
        ["tenant_id", "membership_id", "heartbeat_at"],
    )

    op.create_table(
        "team_command_submissions",
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column("response_payload", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_team_commands_tenant_idempotency_key"),
    )
    op.create_index("ix_team_command_submissions_tenant_id", "team_command_submissions", ["tenant_id"])

    for table in ("invitation_projects", "operator_presences", "team_command_submissions"):
        enable_tenant_rls(table)
    op.execute("UPDATE operator_statuses SET status = 'available' WHERE status IN ('busy', 'wrap_up')")


def downgrade() -> None:
    for table in ("team_command_submissions", "operator_presences", "invitation_projects"):
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
        op.execute(f'DROP POLICY IF EXISTS "{table}_tenant_isolation" ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
        op.drop_table(table)

    op.drop_index("uq_invitations_active_email", table_name="invitations")
    op.drop_index("ix_invitations_status", table_name="invitations")
    op.execute("DROP INDEX IF EXISTS ix_invitations_accepted_membership_id")
    op.execute("DROP INDEX IF EXISTS ix_invitations_invited_by_membership_id")
    op.execute(
        "ALTER TABLE invitations DROP CONSTRAINT IF EXISTS "
        "fk_invitations_tenant_accepted_membership"
    )
    op.execute(
        "ALTER TABLE invitations DROP CONSTRAINT IF EXISTS "
        "fk_invitations_tenant_invited_by_membership"
    )
    op.drop_constraint("fk_invitations_accepted_user_id_users", "invitations", type_="foreignkey")
    op.drop_constraint("uq_invitations_tenant_id_id", "invitations", type_="unique")
    op.execute(
        "ALTER TABLE invitations DROP CONSTRAINT IF EXISTS ck_invitations_state_version_positive"
    )
    op.execute("ALTER TABLE invitations DROP CONSTRAINT IF EXISTS ck_invitations_status_valid")
    for column in (
        "state_version",
        "accepted_membership_id",
        "accepted_user_id",
        "cancelled_at",
        "issued_at",
        "status",
    ):
        op.drop_column("invitations", column)
    op.execute("ALTER TABLE invitations DROP COLUMN IF EXISTS invited_by_membership_id")

    op.execute(
        "ALTER TABLE operator_statuses DROP CONSTRAINT IF EXISTS "
        "fk_operator_statuses_tenant_human_operator"
    )
    op.execute(
        "ALTER TABLE human_operators DROP CONSTRAINT IF EXISTS "
        "fk_human_operators_tenant_membership"
    )
    op.drop_constraint("uq_human_operators_tenant_id_id", "human_operators", type_="unique")
    op.drop_column("human_operators", "is_transfer_available")

    op.drop_index("ix_memberships_presence_last_seen_at", table_name="memberships")
    op.drop_constraint("uq_memberships_tenant_id_id", "memberships", type_="unique")
    op.execute(
        "ALTER TABLE memberships DROP CONSTRAINT IF EXISTS ck_memberships_state_version_positive"
    )
    for column in (
        "state_version",
        "blocked_reason",
        "blocked_at",
        "presence_last_seen_at",
        "activated_at",
        "invited_at",
        "timezone",
        "interface_language",
        "job_title",
        "phone",
    ):
        op.drop_column("memberships", column)
    op.drop_column("users", "last_login_at")
