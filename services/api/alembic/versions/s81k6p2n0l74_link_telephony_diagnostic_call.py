"""link telephony diagnostics to the canonical call

Revision ID: s81k6p2n0l74
Revises: r70j5o1m9k63
Create Date: 2026-08-05 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "s81k6p2n0l74"
down_revision: str | None = "r70j5o1m9k63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("telephony_diagnostic_runs", sa.Column("call_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_telephony_diagnostics_tenant_call",
        "telephony_diagnostic_runs",
        "calls",
        ["tenant_id", "call_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_telephony_diagnostic_runs_call_id",
        "telephony_diagnostic_runs",
        ["call_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_telephony_diagnostic_runs_call_id", table_name="telephony_diagnostic_runs")
    op.drop_constraint(
        "fk_telephony_diagnostics_tenant_call",
        "telephony_diagnostic_runs",
        type_="foreignkey",
    )
    op.drop_column("telephony_diagnostic_runs", "call_id")
