"""add SIP, ARI, media resources, and durable CDR controls

Revision ID: r70j5o1m9k63
Revises: q69i4n0l8j52
Create Date: 2026-08-05 09:00:00.000000
"""

import os
import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "r70j5o1m9k63"
down_revision: str | None = "q69i4n0l8j52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = (
    "telephony_channel_reservations",
    "telephony_resources",
    "call_detail_records",
    "telephony_diagnostic_runs",
)


def _app_role() -> str:
    configured = os.environ.get("POSTGRES_APP_USER", "").strip()
    configured_by_alembic = op.get_context().config.get_main_option("teamora.app_role", "").strip()
    role = configured or configured_by_alembic or "teamora_app"
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", role) is None:
        raise RuntimeError("POSTGRES_APP_USER is not a valid PostgreSQL identifier")
    return role


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
    op.create_unique_constraint("uq_sip_trunks_tenant_id_id", "sip_trunks", ["tenant_id", "id"])
    for name, column in (
        ("auth_mode", sa.Column("auth_mode", sa.String(16), nullable=True)),
        ("codecs", sa.Column("codecs", sa.JSON(), nullable=True)),
        ("dtmf_mode", sa.Column("dtmf_mode", sa.String(16), nullable=True)),
        ("channel_pool_mode", sa.Column("channel_pool_mode", sa.String(16), nullable=True)),
        ("inbound_channel_limit", sa.Column("inbound_channel_limit", sa.Integer(), nullable=True)),
        ("outbound_channel_limit", sa.Column("outbound_channel_limit", sa.Integer(), nullable=True)),
        ("registration_status", sa.Column("registration_status", sa.String(32), nullable=True)),
        ("reachability_status", sa.Column("reachability_status", sa.String(32), nullable=True)),
        ("last_status_at", sa.Column("last_status_at", sa.DateTime(timezone=True), nullable=True)),
        ("last_safe_error", sa.Column("last_safe_error", sa.String(240), nullable=True)),
        ("lock_version", sa.Column("lock_version", sa.Integer(), nullable=True)),
    ):
        del name
        op.add_column("sip_trunks", column)
    op.execute(
        "UPDATE sip_trunks SET auth_mode='registration', codecs='[]'::json, dtmf_mode='auto', "
        "channel_pool_mode='shared', registration_status='not_configured', "
        "reachability_status='not_configured', lock_version=1"
    )
    for column, default in (
        ("auth_mode", "registration"),
        ("codecs", sa.text("'[]'::json")),
        ("dtmf_mode", "auto"),
        ("channel_pool_mode", "shared"),
        ("registration_status", "not_configured"),
        ("reachability_status", "not_configured"),
        ("lock_version", "1"),
    ):
        op.alter_column("sip_trunks", column, nullable=False, server_default=default)
    op.create_check_constraint(
        "sip_trunk_auth_mode_valid", "sip_trunks", "auth_mode IN ('registration','ip')"
    )
    op.create_check_constraint(
        "sip_trunk_dtmf_mode_valid", "sip_trunks", "dtmf_mode IN ('auto','rfc4733','inband','info')"
    )
    op.create_check_constraint(
        "sip_trunk_channel_pool_mode_valid", "sip_trunks", "channel_pool_mode IN ('shared','separate')"
    )
    op.create_check_constraint("sip_trunk_max_channels_positive", "sip_trunks", "max_channels > 0")
    op.create_check_constraint(
        "sip_trunk_inbound_limit_positive",
        "sip_trunks",
        "inbound_channel_limit IS NULL OR inbound_channel_limit > 0",
    )
    op.create_check_constraint(
        "sip_trunk_outbound_limit_positive",
        "sip_trunks",
        "outbound_channel_limit IS NULL OR outbound_channel_limit > 0",
    )
    op.create_check_constraint("sip_trunk_lock_version_positive", "sip_trunks", "lock_version >= 1")

    op.add_column("calls", sa.Column("sip_trunk_id", sa.Uuid(), nullable=True))
    op.create_index("ix_calls_sip_trunk_id", "calls", ["sip_trunk_id"])
    op.create_foreign_key(
        "fk_calls_tenant_sip_trunk",
        "calls",
        "sip_trunks",
        ["tenant_id", "sip_trunk_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "telephony_channel_reservations",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("sip_trunk_id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("pool_key", sa.String(24), server_default="shared", nullable=False),
        sa.Column("status", sa.String(16), server_default="reserved", nullable=False),
        sa.Column("provider_channel_id", sa.String(200), nullable=True),
        sa.Column("reserved_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_reason", sa.String(80), nullable=True),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        *_tenant_columns(),
        sa.CheckConstraint(
            "direction IN ('inbound','outbound')", name="telephony_reservation_direction_valid"
        ),
        sa.CheckConstraint(
            "status IN ('reserved','released','lost')", name="telephony_reservation_status_valid"
        ),
        sa.CheckConstraint("lock_version >= 1", name="telephony_reservation_lock_version_positive"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_telephony_reservations_tenant_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "sip_trunk_id"],
            ["sip_trunks.tenant_id", "sip_trunks.id"],
            name="fk_telephony_reservations_tenant_trunk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "call_id"],
            ["calls.tenant_id", "calls.id"],
            name="fk_telephony_reservations_tenant_call",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_telephony_reservations_tenant_id"),
        sa.UniqueConstraint("tenant_id", "call_id", name="uq_telephony_reservations_tenant_call"),
    )
    for column in (
        "tenant_id",
        "project_id",
        "sip_trunk_id",
        "call_id",
        "status",
        "provider_channel_id",
        "heartbeat_at",
        "released_at",
    ):
        op.create_index(
            f"ix_telephony_channel_reservations_{column}", "telephony_channel_reservations", [column]
        )
    op.create_index(
        "ix_telephony_reservations_trunk_active",
        "telephony_channel_reservations",
        ["tenant_id", "sip_trunk_id", "status", "direction"],
    )

    op.create_table(
        "telephony_resources",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("resource_type", sa.String(24), nullable=False),
        sa.Column("provider_resource_id", sa.String(200), nullable=False),
        sa.Column("parent_provider_resource_id", sa.String(200), nullable=True),
        sa.Column("status", sa.String(24), server_default="creating", nullable=False),
        sa.Column("safe_metadata", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        *_tenant_columns(),
        sa.CheckConstraint(
            "resource_type IN ('channel','bridge','external_media','recording')",
            name="telephony_resource_type_valid",
        ),
        sa.CheckConstraint(
            "status IN ('creating','active','stopping','released','orphaned','failed')",
            name="telephony_resource_status_valid",
        ),
        sa.CheckConstraint("lock_version >= 1", name="telephony_resource_lock_version_positive"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_telephony_resources_tenant_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "call_id"],
            ["calls.tenant_id", "calls.id"],
            name="fk_telephony_resources_tenant_call",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_telephony_resources_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "resource_type", "provider_resource_id", name="uq_telephony_resources_provider_id"
        ),
    )
    for column in ("tenant_id", "project_id", "call_id", "status", "last_seen_at", "released_at"):
        op.create_index(f"ix_telephony_resources_{column}", "telephony_resources", [column])
    op.create_index(
        "ix_telephony_resources_call_status", "telephony_resources", ["tenant_id", "call_id", "status"]
    )

    op.create_table(
        "call_detail_records",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("sip_trunk_id", sa.Uuid(), nullable=False),
        sa.Column("external_call_id", sa.String(200), nullable=True),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("did_masked", sa.String(40), nullable=True),
        sa.Column("destination_masked", sa.String(40), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disposition", sa.String(40), nullable=False),
        sa.Column("hangup_cause", sa.String(40), nullable=True),
        sa.Column("codec", sa.String(40), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), server_default="0", nullable=False),
        sa.Column("billable_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("channels_used", sa.Integer(), server_default="1", nullable=False),
        sa.Column("safe_provider_metadata", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        *_tenant_columns(),
        sa.CheckConstraint("direction IN ('inbound','outbound')", name="call_cdr_direction_valid"),
        sa.CheckConstraint("duration_seconds >= 0", name="call_cdr_duration_non_negative"),
        sa.CheckConstraint(
            "billable_duration_seconds IS NULL OR billable_duration_seconds >= 0",
            name="call_cdr_billable_duration_non_negative",
        ),
        sa.CheckConstraint("channels_used > 0", name="call_cdr_channels_used_positive"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_call_cdr_tenant_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "call_id"],
            ["calls.tenant_id", "calls.id"],
            name="fk_call_cdr_tenant_call",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "sip_trunk_id"],
            ["sip_trunks.tenant_id", "sip_trunks.id"],
            name="fk_call_cdr_tenant_trunk",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_call_cdr_tenant_id"),
        sa.UniqueConstraint("tenant_id", "call_id", name="uq_call_cdr_tenant_call"),
    )
    for column in ("tenant_id", "project_id", "call_id", "sip_trunk_id", "external_call_id"):
        op.create_index(f"ix_call_detail_records_{column}", "call_detail_records", [column])
    op.create_index("ix_call_cdr_tenant_started", "call_detail_records", ["tenant_id", "started_at"])

    op.create_table(
        "telephony_diagnostic_runs",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("sip_trunk_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("status", sa.String(40), server_default="pending", nullable=False),
        sa.Column("destination_hash", sa.String(64), nullable=True),
        sa.Column("destination_masked", sa.String(40), nullable=True),
        sa.Column("signaling_verified", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("inbound_audio_verified", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("outbound_audio_verified", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("dtmf_verified", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("codec", sa.String(40), nullable=True),
        sa.Column("media_statistics", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("safe_error_code", sa.String(80), nullable=True),
        sa.Column("correlation_id", sa.String(160), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        *_tenant_columns(),
        sa.CheckConstraint("mode IN ('local','live')", name="telephony_diagnostic_mode_valid"),
        sa.CheckConstraint(
            "status IN ('pending','running','local_test_passed','provider_unreachable',"
            "'live_signaling_verified','live_audio_verified','degraded','failed','cancelled')",
            name="telephony_diagnostic_status_valid",
        ),
        sa.CheckConstraint("lock_version >= 1", name="telephony_diagnostic_lock_version_positive"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_telephony_diagnostics_tenant_project",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "sip_trunk_id"],
            ["sip_trunks.tenant_id", "sip_trunks.id"],
            name="fk_telephony_diagnostics_tenant_trunk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_telephony_diagnostics_tenant_requester",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_telephony_diagnostics_tenant_id"),
        sa.UniqueConstraint(
            "tenant_id", "correlation_id", name="uq_telephony_diagnostics_tenant_correlation"
        ),
    )
    for column in ("tenant_id", "project_id", "sip_trunk_id", "requested_by_user_id", "status"):
        op.create_index(f"ix_telephony_diagnostic_runs_{column}", "telephony_diagnostic_runs", [column])
    op.create_index(
        "ix_telephony_diagnostics_tenant_started", "telephony_diagnostic_runs", ["tenant_id", "started_at"]
    )

    op.add_column("call_recordings", sa.Column("provider_recording_id", sa.String(200), nullable=True))
    op.add_column("call_recordings", sa.Column("checksum_sha256", sa.String(64), nullable=True))
    op.add_column("call_recordings", sa.Column("duration_seconds", sa.Integer(), nullable=True))
    op.add_column("call_recordings", sa.Column("status", sa.String(24), nullable=True))
    op.execute(
        "UPDATE call_recordings SET status = CASE WHEN deleted_at IS NULL THEN 'available' ELSE 'purged' END"
    )
    op.alter_column("call_recordings", "status", nullable=False, server_default="pending_upload")
    op.create_index("ix_call_recordings_provider_recording_id", "call_recordings", ["provider_recording_id"])
    op.create_unique_constraint(
        "uq_call_recordings_tenant_provider_recording",
        "call_recordings",
        ["tenant_id", "provider_recording_id"],
    )
    op.create_check_constraint(
        "call_recording_status_valid",
        "call_recordings",
        "status IN ('recording','pending_upload','uploading','available','failed','purged')",
    )

    for table in TENANT_TABLES:
        _enable_rls(table)

    # Exact DID resolver is deliberately narrow: it reveals only routing identifiers,
    # never customer data or credentials. The API still establishes tenant context before mutation.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION kline_resolve_inbound_did(input_e164 text)
        RETURNS TABLE(tenant_id uuid, project_id uuid, phone_number_id uuid, sip_trunk_id uuid)
        LANGUAGE sql SECURITY DEFINER STABLE
        SET search_path = public, pg_temp
        AS $$
          SELECT pn.tenant_id, pipn.project_id, pn.id, pn.sip_trunk_id
          FROM phone_numbers pn
          JOIN project_inbound_phone_numbers pipn
            ON pipn.tenant_id = pn.tenant_id AND pipn.phone_number_id = pn.id
          JOIN projects p ON p.tenant_id = pipn.tenant_id AND p.id = pipn.project_id
          WHERE pn.e164 = input_e164 AND pn.is_active AND p.archived_at IS NULL
          LIMIT 1
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION kline_resolve_inbound_did(text) FROM PUBLIC")
    op.execute(f'GRANT EXECUTE ON FUNCTION kline_resolve_inbound_did(text) TO "{_app_role()}"')


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS kline_resolve_inbound_did(text)")
    for table in reversed(TENANT_TABLES):
        _disable_rls(table)
    op.drop_constraint("call_recording_status_valid", "call_recordings", type_="check")
    op.drop_constraint("uq_call_recordings_tenant_provider_recording", "call_recordings", type_="unique")
    op.drop_index("ix_call_recordings_provider_recording_id", table_name="call_recordings")
    for column in ("status", "duration_seconds", "checksum_sha256", "provider_recording_id"):
        op.drop_column("call_recordings", column)
    for table in reversed(TENANT_TABLES):
        op.drop_table(table)
    op.drop_constraint("fk_calls_tenant_sip_trunk", "calls", type_="foreignkey")
    op.drop_index("ix_calls_sip_trunk_id", table_name="calls")
    op.drop_column("calls", "sip_trunk_id")
    for constraint in (
        "sip_trunk_lock_version_positive",
        "sip_trunk_outbound_limit_positive",
        "sip_trunk_inbound_limit_positive",
        "sip_trunk_max_channels_positive",
        "sip_trunk_channel_pool_mode_valid",
        "sip_trunk_dtmf_mode_valid",
        "sip_trunk_auth_mode_valid",
    ):
        op.drop_constraint(constraint, "sip_trunks", type_="check")
    for column in (
        "lock_version",
        "last_safe_error",
        "last_status_at",
        "reachability_status",
        "registration_status",
        "outbound_channel_limit",
        "inbound_channel_limit",
        "channel_pool_mode",
        "dtmf_mode",
        "codecs",
        "auth_mode",
    ):
        op.drop_column("sip_trunks", column)
    op.drop_constraint("uq_sip_trunks_tenant_id_id", "sip_trunks", type_="unique")
