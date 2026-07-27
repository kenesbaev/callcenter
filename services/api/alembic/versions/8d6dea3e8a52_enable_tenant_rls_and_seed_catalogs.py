"""enable tenant rls and seed catalogs

Revision ID: 8d6dea3e8a52
Revises: a0c9978a17f6
Create Date: 2026-07-26 15:07:32.547584
"""

from collections.abc import Sequence

from alembic import op


revision: str = "8d6dea3e8a52"
down_revision: str | None = "a0c9978a17f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TENANT_TABLES = (
    "ai_operator_versions",
    "ai_operators",
    "audit_logs",
    "call_events",
    "call_flow_versions",
    "call_flows",
    "call_outcomes",
    "call_participants",
    "call_recordings",
    "call_summaries",
    "call_tags",
    "calls",
    "crm_field_mappings",
    "customer_contacts",
    "customer_notes",
    "customers",
    "feature_flags",
    "human_operators",
    "inbound_routes",
    "integration_credential_references",
    "integrations",
    "invitations",
    "invoices",
    "knowledge_chunks",
    "knowledge_documents",
    "knowledge_sources",
    "knowledge_sync_jobs",
    "language_configurations",
    "memberships",
    "notifications",
    "operator_queues",
    "operator_statuses",
    "outbound_routes",
    "payments",
    "phone_numbers",
    "queue_members",
    "refresh_tokens",
    "sip_credential_references",
    "sip_trunks",
    "subscriptions",
    "tenant_settings",
    "tool_definitions",
    "tool_executions",
    "tool_permissions",
    "transcript_segments",
    "transfer_requests",
    "usage_limits",
    "usage_records",
    "voice_profiles",
    "webhook_deliveries",
    "webhook_endpoints",
)


def upgrade() -> None:
    op.create_foreign_key(
        "fk_ai_operators_active_version_id_ai_operator_versions",
        "ai_operators",
        "ai_operator_versions",
        ["active_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_call_flows_active_version_id_call_flow_versions",
        "call_flows",
        "call_flow_versions",
        ["active_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    for table in TENANT_TABLES:
        policy = f"{table}_tenant_isolation"
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY "{policy}" ON "{table}" '
            "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
            "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        )

    op.execute(
        """
        INSERT INTO roles (id, name, description, created_at, updated_at)
        VALUES
          (gen_random_uuid(), 'platform_admin', 'Platform operations without implicit recording access', now(), now()),
          (gen_random_uuid(), 'tenant_owner', 'Full tenant administration', now(), now()),
          (gen_random_uuid(), 'tenant_manager', 'Calls, AI operators, knowledge and analytics', now(), now()),
          (gen_random_uuid(), 'human_operator', 'Assigned conversations and customer work', now(), now()),
          (gen_random_uuid(), 'analyst', 'Read-only conversations and analytics', now(), now()),
          (gen_random_uuid(), 'billing_admin', 'Usage, plan and invoice access only', now(), now())
        ON CONFLICT (name) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO permissions (id, code, description, created_at, updated_at)
        VALUES
          (gen_random_uuid(), 'tenant:manage', 'Manage tenant settings', now(), now()),
          (gen_random_uuid(), 'operators:manage', 'Manage AI operators', now(), now()),
          (gen_random_uuid(), 'knowledge:manage', 'Manage tenant knowledge', now(), now()),
          (gen_random_uuid(), 'calls:read', 'Read permitted conversations', now(), now()),
          (gen_random_uuid(), 'recordings:read', 'Request audited recording playback', now(), now()),
          (gen_random_uuid(), 'analytics:read', 'Read tenant analytics', now(), now()),
          (gen_random_uuid(), 'billing:read', 'Read usage and billing', now(), now()),
          (gen_random_uuid(), 'simulator:use', 'Use development call simulator', now(), now())
        ON CONFLICT (code) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO plans (
          id, code, name, monthly_price_usd, annual_price_usd,
          included_ai_minutes, overage_per_minute_usd, is_custom, created_at, updated_at
        ) VALUES
          (gen_random_uuid(), 'start', 'Start', 99, 999, 250, 0.08, false, now(), now()),
          (gen_random_uuid(), 'business', 'Business', 149, 1500, 500, 0.08, false, now(), now()),
          (gen_random_uuid(), 'pro', 'Pro', 299, 2990, 2000, 0.08, false, now(), now()),
          (gen_random_uuid(), 'enterprise', 'Enterprise', NULL, NULL, NULL, 0.08, true, now(), now())
        ON CONFLICT (code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM plans WHERE code IN ('start', 'business', 'pro', 'enterprise')")
    op.execute(
        "DELETE FROM permissions WHERE code IN "
        "('tenant:manage', 'operators:manage', 'knowledge:manage', 'calls:read', "
        "'recordings:read', 'analytics:read', 'billing:read', 'simulator:use')"
    )
    op.execute(
        "DELETE FROM roles WHERE name IN "
        "('platform_admin', 'tenant_owner', 'tenant_manager', 'human_operator', 'analyst', 'billing_admin')"
    )
    for table in reversed(TENANT_TABLES):
        policy = f"{table}_tenant_isolation"
        op.execute(f'DROP POLICY IF EXISTS "{policy}" ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')
    op.drop_constraint(
        "fk_call_flows_active_version_id_call_flow_versions", "call_flows", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_ai_operators_active_version_id_ai_operator_versions",
        "ai_operators",
        type_="foreignkey",
    )
