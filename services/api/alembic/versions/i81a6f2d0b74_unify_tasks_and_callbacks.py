"""unify tasks and callbacks

Revision ID: i81a6f2d0b74
Revises: h70f5e1c9a63
Create Date: 2026-07-30 11:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "i81a6f2d0b74"
down_revision: str | None = "h70f5e1c9a63"
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
    # Expand the existing callback table in place so UUIDs and public callback links survive.
    op.add_column(
        "callback_tasks",
        sa.Column("call_outcome_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "callback_tasks",
        sa.Column("task_type", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "callback_tasks",
        sa.Column("title", sa.String(length=240), nullable=True),
    )
    op.add_column(
        "callback_tasks",
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.add_column(
        "callback_tasks",
        sa.Column("priority", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "callback_tasks",
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "callback_tasks",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "callback_tasks",
        sa.Column("comment", sa.Text(), nullable=True),
    )
    op.add_column(
        "callback_tasks",
        sa.Column("source", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "callback_tasks",
        sa.Column("idempotency_key", sa.String(length=160), nullable=True),
    )
    op.add_column(
        "callback_tasks",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "callback_tasks",
        sa.Column("cancellation_reason", sa.String(length=1000), nullable=True),
    )

    op.execute(
        """
        UPDATE callback_tasks AS task
        SET created_by_user_id = COALESCE(
              task.assigned_user_id,
              (
                SELECT membership.user_id
                FROM memberships AS membership
                WHERE membership.tenant_id = task.tenant_id
                  AND membership.role = 'tenant_owner'
                ORDER BY membership.created_at, membership.id
                LIMIT 1
              ),
              (
                SELECT membership.user_id
                FROM memberships AS membership
                WHERE membership.tenant_id = task.tenant_id
                ORDER BY membership.created_at, membership.id
                LIMIT 1
              )
            ),
            call_outcome_id = (
              SELECT outcome.id
              FROM call_outcomes AS outcome
              WHERE outcome.tenant_id = task.tenant_id
                AND outcome.call_id = task.call_id
              LIMIT 1
            ),
            task_type = 'callback',
            title = 'Перезвон клиенту',
            description = COALESCE(task.note, ''),
            priority = 'normal',
            comment = COALESCE(task.note, ''),
            source = 'legacy_callback',
            completed_at = CASE
              WHEN task.status = 'completed' THEN COALESCE(task.completed_at, task.updated_at, now())
              ELSE NULL
            END,
            cancelled_at = CASE
              WHEN task.status = 'cancelled' THEN COALESCE(task.completed_at, task.updated_at, now())
              ELSE NULL
            END,
            status = CASE
              WHEN task.status IN ('pending', 'in_progress', 'completed', 'cancelled') THEN task.status
              ELSE 'pending'
            END
        """
    )
    op.alter_column("callback_tasks", "task_type", nullable=False, server_default="callback")
    op.alter_column("callback_tasks", "title", nullable=False, server_default="Перезвон клиенту")
    op.alter_column("callback_tasks", "description", nullable=False, server_default="")
    op.alter_column("callback_tasks", "priority", nullable=False, server_default="normal")
    op.alter_column("callback_tasks", "created_by_user_id", nullable=False)
    op.alter_column("callback_tasks", "comment", nullable=False, server_default="")
    op.alter_column("callback_tasks", "source", nullable=False, server_default="manual")

    op.create_foreign_key(
        "fk_callback_tasks_tenant_creator_memberships",
        "callback_tasks",
        "memberships",
        ["tenant_id", "created_by_user_id"],
        ["tenant_id", "user_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_callback_tasks_tenant_call_outcome",
        "callback_tasks",
        "call_outcomes",
        ["tenant_id", "call_outcome_id"],
        ["tenant_id", "id"],
        ondelete="SET NULL (call_outcome_id)",
    )
    op.create_unique_constraint(
        "uq_callback_tasks_tenant_id",
        "callback_tasks",
        ["tenant_id", "id"],
    )
    op.create_check_constraint(
        "task_type_valid",
        "callback_tasks",
        "task_type IN ('callback', 'follow_up', 'manual', 'system')",
    )
    op.create_check_constraint(
        "status_valid",
        "callback_tasks",
        "status IN ('pending', 'in_progress', 'completed', 'cancelled')",
    )
    op.create_check_constraint(
        "priority_valid",
        "callback_tasks",
        "priority IN ('low', 'normal', 'high', 'urgent')",
    )
    op.create_check_constraint(
        "source_valid",
        "callback_tasks",
        "source IN ('manual', 'call_result', 'system', 'call_flow', 'legacy_callback')",
    )
    op.create_check_constraint(
        "completed_at_matches_status",
        "callback_tasks",
        "(status = 'completed') = (completed_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "cancelled_at_matches_status",
        "callback_tasks",
        "(status = 'cancelled') = (cancelled_at IS NOT NULL)",
    )
    op.create_index(
        "ix_callback_tasks_call_outcome_id",
        "callback_tasks",
        ["call_outcome_id"],
    )
    op.create_index(
        "ix_callback_tasks_created_by_user_id",
        "callback_tasks",
        ["created_by_user_id"],
    )
    op.create_index(
        "ix_callback_tasks_priority",
        "callback_tasks",
        ["priority"],
    )
    op.create_index(
        "ix_callback_tasks_source",
        "callback_tasks",
        ["source"],
    )
    op.create_index(
        "ix_callback_tasks_project_type_status_due",
        "callback_tasks",
        ["tenant_id", "project_id", "task_type", "status", "due_at"],
    )
    op.create_index(
        "uq_callback_tasks_tenant_idempotency_key",
        "callback_tasks",
        ["tenant_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "task_events",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("safe_snapshot", sa.JSON(), nullable=False),
        sa.Column("correlation_id", sa.String(length=120), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["callback_tasks.tenant_id", "callback_tasks.id"],
            name="fk_task_events_tenant_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_task_events_actor_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_task_events_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_events")),
    )
    op.create_index(op.f("ix_task_events_tenant_id"), "task_events", ["tenant_id"])
    op.create_index(op.f("ix_task_events_task_id"), "task_events", ["task_id"])
    op.create_index(op.f("ix_task_events_actor_user_id"), "task_events", ["actor_user_id"])
    op.create_index("ix_task_events_task_created", "task_events", ["task_id", "created_at"])
    enable_tenant_rls("task_events")

    op.create_table(
        "task_command_submissions",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(length=40), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("response_payload", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["callback_tasks.tenant_id", "callback_tasks.id"],
            name="fk_task_command_submissions_tenant_task",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_task_command_submissions_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_command_submissions")),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_task_command_submissions_tenant_idempotency_key",
        ),
    )
    op.create_index(
        op.f("ix_task_command_submissions_tenant_id"),
        "task_command_submissions",
        ["tenant_id"],
    )
    op.create_index(
        op.f("ix_task_command_submissions_task_id"),
        "task_command_submissions",
        ["task_id"],
    )
    enable_tenant_rls("task_command_submissions")

    op.execute(
        """
        INSERT INTO task_events (
          id, tenant_id, task_id, event_type, actor_user_id,
          safe_snapshot, correlation_id, created_at, updated_at
        )
        SELECT
          gen_random_uuid(), task.tenant_id, task.id, 'created', task.created_by_user_id,
          json_build_object(
            'migration', 'legacy_callback',
            'status', task.status,
            'assigned_user_id', task.assigned_user_id,
            'due_at', task.due_at
          ),
          'migration:i81a6f2d0b74', task.created_at, task.created_at
        FROM callback_tasks AS task
        """
    )
    op.execute(
        """
        CREATE FUNCTION kline_prevent_task_event_mutation()
        RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'task_events are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER task_events_immutable
        BEFORE UPDATE OR DELETE ON task_events
        FOR EACH ROW EXECUTE FUNCTION kline_prevent_task_event_mutation()
        """
    )

    op.execute(
        """
        INSERT INTO permissions (id, code, description, created_at, updated_at)
        VALUES
          (gen_random_uuid(), 'tasks:read', 'Read permitted project tasks', now(), now()),
          (gen_random_uuid(), 'tasks:create', 'Create project tasks', now(), now()),
          (gen_random_uuid(), 'tasks:manage', 'Manage permitted project tasks', now(), now())
        ON CONFLICT (code) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS task_events_immutable ON task_events")
    op.execute("DROP FUNCTION IF EXISTS kline_prevent_task_event_mutation()")

    for table in ("task_command_submissions", "task_events"):
        op.execute(f'DROP POLICY IF EXISTS "{table}_tenant_isolation" ON "{table}"')
        op.execute(f'ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY')

    op.drop_index(op.f("ix_task_command_submissions_task_id"), table_name="task_command_submissions")
    op.drop_index(op.f("ix_task_command_submissions_tenant_id"), table_name="task_command_submissions")
    op.drop_table("task_command_submissions")
    op.drop_index("ix_task_events_task_created", table_name="task_events")
    op.drop_index(op.f("ix_task_events_actor_user_id"), table_name="task_events")
    op.drop_index(op.f("ix_task_events_task_id"), table_name="task_events")
    op.drop_index(op.f("ix_task_events_tenant_id"), table_name="task_events")
    op.drop_table("task_events")

    # Preserve the human-readable task payload in the legacy callback note before narrowing.
    op.execute(
        """
        UPDATE callback_tasks
        SET note = CASE
          WHEN comment <> '' THEN comment
          WHEN description <> '' THEN description
          ELSE note
        END,
        completed_at = CASE
          WHEN status = 'completed' THEN completed_at
          WHEN status = 'cancelled' THEN COALESCE(cancelled_at, updated_at, now())
          ELSE NULL
        END
        """
    )
    op.drop_index("uq_callback_tasks_tenant_idempotency_key", table_name="callback_tasks")
    op.drop_index("ix_callback_tasks_project_type_status_due", table_name="callback_tasks")
    op.drop_index("ix_callback_tasks_source", table_name="callback_tasks")
    op.drop_index("ix_callback_tasks_priority", table_name="callback_tasks")
    op.drop_index("ix_callback_tasks_created_by_user_id", table_name="callback_tasks")
    op.drop_index("ix_callback_tasks_call_outcome_id", table_name="callback_tasks")
    op.drop_constraint(
        op.f("ck_callback_tasks_cancelled_at_matches_status"),
        "callback_tasks",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_callback_tasks_completed_at_matches_status"),
        "callback_tasks",
        type_="check",
    )
    op.drop_constraint(op.f("ck_callback_tasks_source_valid"), "callback_tasks", type_="check")
    op.drop_constraint(op.f("ck_callback_tasks_priority_valid"), "callback_tasks", type_="check")
    op.drop_constraint(op.f("ck_callback_tasks_status_valid"), "callback_tasks", type_="check")
    op.drop_constraint(op.f("ck_callback_tasks_task_type_valid"), "callback_tasks", type_="check")
    op.drop_constraint("uq_callback_tasks_tenant_id", "callback_tasks", type_="unique")
    op.drop_constraint("fk_callback_tasks_tenant_call_outcome", "callback_tasks", type_="foreignkey")
    op.drop_constraint("fk_callback_tasks_tenant_creator_memberships", "callback_tasks", type_="foreignkey")
    for column in (
        "cancellation_reason",
        "cancelled_at",
        "idempotency_key",
        "source",
        "comment",
        "started_at",
        "created_by_user_id",
        "priority",
        "description",
        "title",
        "task_type",
        "call_outcome_id",
    ):
        op.drop_column("callback_tasks", column)

    op.execute("DELETE FROM permissions WHERE code IN ('tasks:read', 'tasks:create', 'tasks:manage')")
