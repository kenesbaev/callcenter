"""add live operator transfer orchestration

Revision ID: u03m8r4p2n96
Revises: t92l7q3o1m85
Create Date: 2026-08-11 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "u03m8r4p2n96"
down_revision: str | None = "t92l7q3o1m85"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_TENANT_TABLES = ("transfer_attempts", "operator_transfer_endpoints")


def _tenant_columns() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def _enable_rls(table: str) -> None:
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY "{table}_tenant_isolation" ON "{table}" '
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def _disable_rls(table: str) -> None:
    op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')
    op.execute(f'DROP POLICY IF EXISTS "{table}_tenant_isolation" ON "{table}"')
    op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')


def upgrade() -> None:
    op.add_column(
        "telephony_channel_reservations",
        sa.Column("purpose", sa.String(24), server_default="customer_leg", nullable=False),
    )
    op.drop_constraint(
        "uq_telephony_reservations_tenant_call",
        "telephony_channel_reservations",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_telephony_reservations_tenant_call_purpose",
        "telephony_channel_reservations",
        ["tenant_id", "call_id", "purpose"],
    )
    op.create_check_constraint(
        "telephony_reservation_purpose_valid",
        "telephony_channel_reservations",
        "purpose IN ('customer_leg','operator_transfer')",
    )
    op.add_column("transfer_requests", sa.Column("project_id", sa.Uuid(), nullable=True))
    op.add_column("transfer_requests", sa.Column("language_code", sa.String(32), nullable=True))
    op.add_column("transfer_requests", sa.Column("routing_strategy", sa.String(40), nullable=True))
    op.add_column("transfer_requests", sa.Column("destination_type", sa.String(16), nullable=True))
    op.add_column("transfer_requests", sa.Column("claimed_membership_id", sa.Uuid(), nullable=True))
    op.add_column("transfer_requests", sa.Column("context_snapshot", sa.JSON(), nullable=True))
    op.add_column("transfer_requests", sa.Column("attempt_count", sa.Integer(), nullable=True))
    op.add_column("transfer_requests", sa.Column("max_attempts", sa.Integer(), nullable=True))
    op.add_column("transfer_requests", sa.Column("idempotency_key", sa.String(160), nullable=True))
    op.add_column("transfer_requests", sa.Column("lock_version", sa.Integer(), nullable=True))
    op.add_column(
        "transfer_requests", sa.Column("offer_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("transfer_requests", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("transfer_requests", sa.Column("connected_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("transfer_requests", sa.Column("last_error_code", sa.String(80), nullable=True))
    op.add_column("transfer_requests", sa.Column("operator_channel_id", sa.String(200), nullable=True))
    op.execute(
        "UPDATE transfer_requests tr SET "
        "project_id=c.project_id, "
        "language_code=COALESCE(c.language::text, 'ru'), "
        "routing_strategy='longest_idle', destination_type='browser', "
        "context_snapshot='{}'::json, attempt_count=0, max_attempts=3, "
        "idempotency_key='legacy:' || tr.id::text, lock_version=1 "
        "FROM calls c WHERE c.id=tr.call_id AND c.tenant_id=tr.tenant_id"
    )
    for column in (
        "project_id",
        "language_code",
        "routing_strategy",
        "destination_type",
        "context_snapshot",
        "attempt_count",
        "max_attempts",
        "idempotency_key",
        "lock_version",
    ):
        op.alter_column("transfer_requests", column, nullable=False)
    op.drop_constraint(
        "fk_transfer_requests_call_id_calls",
        "transfer_requests",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_transfer_requests_tenant_project",
        "transfer_requests",
        "projects",
        ["tenant_id", "project_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_transfer_requests_tenant_call",
        "transfer_requests",
        "calls",
        ["tenant_id", "call_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_transfer_requests_tenant_claimed_membership",
        "transfer_requests",
        "memberships",
        ["tenant_id", "claimed_membership_id"],
        ["tenant_id", "id"],
        ondelete="SET NULL",
    )
    op.create_unique_constraint("uq_transfer_requests_tenant_id", "transfer_requests", ["tenant_id", "id"])
    op.create_unique_constraint(
        "uq_transfer_requests_tenant_idempotency",
        "transfer_requests",
        ["tenant_id", "idempotency_key"],
    )
    op.create_check_constraint(
        "transfer_request_lock_version_positive", "transfer_requests", "lock_version >= 1"
    )
    op.create_check_constraint(
        "attempt_count_non_negative", "transfer_requests", "attempt_count >= 0"
    )
    op.create_check_constraint(
        "transfer_request_max_attempts_positive", "transfer_requests", "max_attempts > 0"
    )
    op.create_check_constraint(
        "transfer_request_destination_type_valid",
        "transfer_requests",
        "destination_type IN ('browser','sip','mobile')",
    )
    op.create_index("ix_transfer_requests_project_id", "transfer_requests", ["project_id"])
    op.create_index(
        "ix_transfer_requests_claimed_membership_id", "transfer_requests", ["claimed_membership_id"]
    )
    op.create_index("ix_transfer_requests_offer_expires_at", "transfer_requests", ["offer_expires_at"])
    op.create_index(
        "ix_transfer_requests_project_status_expiry",
        "transfer_requests",
        ["tenant_id", "project_id", "status", "offer_expires_at"],
    )

    op.create_table(
        "transfer_attempts",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("transfer_request_id", sa.Uuid(), nullable=False),
        sa.Column("membership_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("destination_type", sa.String(16), nullable=False),
        sa.Column("destination_ref", sa.String(160), nullable=True),
        sa.Column("status", sa.String(24), server_default="offered", nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("offered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_channel_id", sa.String(200), nullable=True),
        sa.Column("safe_error_code", sa.String(80), nullable=True),
        *_tenant_columns(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_transfer_attempts_tenant_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "transfer_request_id"],
            ["transfer_requests.tenant_id", "transfer_requests.id"],
            name="fk_transfer_attempts_tenant_request",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_transfer_attempts_tenant_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "transfer_request_id", "attempt_number", name="uq_transfer_attempts_request_number"
        ),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_transfer_attempts_idempotency"),
        sa.CheckConstraint("attempt_number > 0", name="transfer_attempt_number_positive"),
    )
    for column in ("tenant_id", "project_id", "transfer_request_id", "membership_id", "status"):
        op.create_index(f"ix_transfer_attempts_{column}", "transfer_attempts", [column])
    op.create_index(
        "ix_transfer_attempts_member_status",
        "transfer_attempts",
        ["tenant_id", "membership_id", "status", "offered_at"],
    )

    op.create_table(
        "operator_transfer_endpoints",
        sa.Column("membership_id", sa.Uuid(), nullable=False),
        sa.Column("endpoint_type", sa.String(16), nullable=False),
        sa.Column("destination", sa.String(160), nullable=False),
        sa.Column("display_hint", sa.String(80), nullable=False),
        sa.Column("is_verified", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        *_tenant_columns(),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_operator_transfer_endpoints_tenant_membership",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "membership_id", "endpoint_type", name="uq_operator_transfer_endpoints_member_type"
        ),
        sa.CheckConstraint(
            "endpoint_type IN ('browser','sip','mobile')", name="endpoint_type_valid"
        ),
        sa.CheckConstraint("lock_version >= 1", name="lock_version_positive"),
    )
    op.create_index("ix_operator_transfer_endpoints_tenant_id", "operator_transfer_endpoints", ["tenant_id"])
    op.create_index(
        "ix_operator_transfer_endpoints_membership_id", "operator_transfer_endpoints", ["membership_id"]
    )
    for table in NEW_TENANT_TABLES:
        _enable_rls(table)


def downgrade() -> None:
    for table in reversed(NEW_TENANT_TABLES):
        _disable_rls(table)
    op.drop_table("operator_transfer_endpoints")
    op.drop_table("transfer_attempts")
    for name in (
        "ix_transfer_requests_project_status_expiry",
        "ix_transfer_requests_offer_expires_at",
        "ix_transfer_requests_claimed_membership_id",
        "ix_transfer_requests_project_id",
    ):
        op.drop_index(name, table_name="transfer_requests")
    for name in (
        "transfer_request_destination_type_valid",
        "transfer_request_max_attempts_positive",
        "attempt_count_non_negative",
        "transfer_request_lock_version_positive",
    ):
        op.drop_constraint(name, "transfer_requests", type_="check")
    op.drop_constraint("uq_transfer_requests_tenant_idempotency", "transfer_requests", type_="unique")
    op.drop_constraint("uq_transfer_requests_tenant_id", "transfer_requests", type_="unique")
    op.drop_constraint(
        "fk_transfer_requests_tenant_claimed_membership", "transfer_requests", type_="foreignkey"
    )
    op.drop_constraint("fk_transfer_requests_tenant_call", "transfer_requests", type_="foreignkey")
    op.drop_constraint("fk_transfer_requests_tenant_project", "transfer_requests", type_="foreignkey")
    op.create_foreign_key(
        "fk_transfer_requests_call_id_calls",
        "transfer_requests",
        "calls",
        ["call_id"],
        ["id"],
        ondelete="CASCADE",
    )
    for column in (
        "operator_channel_id",
        "last_error_code",
        "connected_at",
        "claimed_at",
        "offer_expires_at",
        "lock_version",
        "idempotency_key",
        "max_attempts",
        "attempt_count",
        "context_snapshot",
        "claimed_membership_id",
        "destination_type",
        "routing_strategy",
        "language_code",
        "project_id",
    ):
        op.drop_column("transfer_requests", column)
    op.drop_constraint(
        "telephony_reservation_purpose_valid",
        "telephony_channel_reservations",
        type_="check",
    )
    op.drop_constraint(
        "uq_telephony_reservations_tenant_call_purpose",
        "telephony_channel_reservations",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_telephony_reservations_tenant_call",
        "telephony_channel_reservations",
        ["tenant_id", "call_id"],
    )
    op.drop_column("telephony_channel_reservations", "purpose")
