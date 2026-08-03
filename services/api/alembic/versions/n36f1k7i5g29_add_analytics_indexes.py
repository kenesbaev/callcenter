"""add analytics query indexes

Revision ID: n36f1k7i5g29
Revises: m25e0j6h4f18
Create Date: 2026-08-01 17:15:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "n36f1k7i5g29"
down_revision: str | None = "m25e0j6h4f18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_calls_tenant_project_started",
        "calls",
        ["tenant_id", "project_id", "started_at"],
    )
    op.create_index(
        "ix_calls_tenant_operator_started",
        "calls",
        ["tenant_id", "operator_user_id", "started_at"],
    )
    op.create_index(
        "ix_call_events_tenant_type_occurred",
        "call_events",
        ["tenant_id", "event_type", "occurred_at"],
    )
    op.create_index(
        "ix_callback_tasks_tenant_project_created",
        "callback_tasks",
        ["tenant_id", "project_id", "created_at"],
    )
    op.create_index(
        "ix_transfer_requests_tenant_status_requested",
        "transfer_requests",
        ["tenant_id", "status", "requested_at"],
    )
    op.create_index(
        "ix_usage_records_tenant_metric_occurred",
        "usage_records",
        ["tenant_id", "metric", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_usage_records_tenant_metric_occurred", table_name="usage_records")
    op.drop_index("ix_transfer_requests_tenant_status_requested", table_name="transfer_requests")
    op.drop_index("ix_callback_tasks_tenant_project_created", table_name="callback_tasks")
    op.drop_index("ix_call_events_tenant_type_occurred", table_name="call_events")
    op.drop_index("ix_calls_tenant_operator_started", table_name="calls")
    op.drop_index("ix_calls_tenant_project_started", table_name="calls")
