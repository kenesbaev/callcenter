"""add canonical call state machine

Revision ID: k03c8h4f2d96
Revises: j92b7g3e1c85
Create Date: 2026-07-31 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "k03c8h4f2d96"
down_revision: str | None = "j92b7g3e1c85"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CANONICAL_STATUSES = (
    "queued",
    "initiated",
    "ringing",
    "active",
    "on_hold",
    "transfer_requested",
    "transferring",
    "transferred",
    "completed",
    "busy",
    "no_answer",
    "failed",
    "cancelled",
)


def upgrade() -> None:
    op.add_column("calls", sa.Column("caller_type", sa.String(length=24), nullable=True))
    op.add_column("calls", sa.Column("ringing_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("calls", sa.Column("held_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("calls", sa.Column("last_provider_event_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("calls", sa.Column("state_version", sa.Integer(), nullable=True))
    op.add_column("calls", sa.Column("hangup_cause", sa.String(length=40), nullable=True))
    op.add_column("calls", sa.Column("raw_provider_cause", sa.String(length=160), nullable=True))

    statuses = ", ".join(f"'{status}'" for status in CANONICAL_STATUSES)
    op.execute(
        "UPDATE calls SET status = 'failed' WHERE status NOT IN ("
        "'queued', 'initiated', 'ringing', 'active', 'on_hold', "
        "'transfer_requested', 'transferring', 'transferred', 'completed', "
        "'busy', 'no_answer', 'failed', 'cancelled')"
    )
    op.execute("UPDATE calls SET direction = 'inbound' WHERE direction NOT IN ('inbound', 'outbound')")
    op.execute(
        "UPDATE calls SET caller_type = CASE "
        "WHEN ai_operator_id IS NOT NULL AND operator_user_id IS NULL THEN 'ai_agent' "
        "ELSE 'human_operator' END"
    )
    op.execute(
        "UPDATE calls SET ringing_at = COALESCE(started_at, answered_at, ended_at) "
        "WHERE status IN ('ringing', 'active', 'transferring', 'completed', 'failed')"
    )
    op.execute("UPDATE calls SET state_version = 1")
    op.execute(
        "UPDATE calls AS c SET last_provider_event_at = events.last_event_at "
        "FROM (SELECT call_id, MAX(COALESCE(provider_timestamp, occurred_at)) AS last_event_at "
        "FROM call_events WHERE provider_event_id IS NOT NULL GROUP BY call_id) AS events "
        "WHERE c.id = events.call_id"
    )

    op.alter_column("calls", "caller_type", nullable=False, server_default="human_operator")
    op.alter_column("calls", "state_version", nullable=False, server_default="0")
    op.create_check_constraint(
        "call_status_valid",
        "calls",
        f"status IN ({statuses})",
    )
    op.create_check_constraint(
        "call_direction_valid",
        "calls",
        "direction IN ('inbound', 'outbound')",
    )
    op.create_check_constraint(
        "call_caller_type_valid",
        "calls",
        "caller_type IN ('human_operator', 'ai_agent')",
    )
    op.create_check_constraint(
        "call_state_version_non_negative",
        "calls",
        "state_version >= 0",
    )
    op.create_index(
        "ix_calls_tenant_status_last_event",
        "calls",
        ["tenant_id", "status", "last_provider_event_at"],
    )


def downgrade() -> None:
    op.execute(
        "UPDATE calls SET status = CASE "
        "WHEN status = 'initiated' THEN 'queued' "
        "WHEN status IN ('on_hold', 'transfer_requested', 'transferred') THEN 'active' "
        "WHEN status IN ('busy', 'no_answer', 'cancelled') THEN 'failed' "
        "ELSE status END"
    )
    op.drop_index("ix_calls_tenant_status_last_event", table_name="calls")
    for constraint in (
        "call_state_version_non_negative",
        "call_caller_type_valid",
        "call_direction_valid",
        "call_status_valid",
    ):
        op.drop_constraint(constraint, "calls", type_="check")
    for column in (
        "raw_provider_cause",
        "hangup_cause",
        "state_version",
        "last_provider_event_at",
        "held_at",
        "ringing_at",
        "caller_type",
    ):
        op.drop_column("calls", column)
