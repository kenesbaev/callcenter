"""expand project call results

Revision ID: g69e4d0b8f52
Revises: f58d3c9a7e41
Create Date: 2026-07-29 20:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "g69e4d0b8f52"
down_revision: str | None = "f58d3c9a7e41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def enable_rls(table: str) -> None:
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY "{table}_tenant_isolation" ON "{table}" '
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def upgrade() -> None:
    for table in ("call_result_catalogs", "call_outcomes"):
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')

    op.create_table(
        "call_result_definitions",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("catalog_id", sa.Uuid(), nullable=False),
        sa.Column("system_code", sa.String(length=80), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("name_translations", sa.JSON(), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("color", sa.String(length=7), nullable=False, server_default="#64748B"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("requires_comment", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("requires_callback", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("requires_callback_at", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("creates_task", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("next_customer_status", sa.String(length=32), nullable=True),
        sa.Column("return_to_queue", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("completes_customer", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("do_not_call", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("counts_as_success", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "category IN ('successful', 'intermediate', 'unreachable', 'unsuccessful')",
            name=op.f("ck_call_result_definitions_category_valid"),
        ),
        sa.CheckConstraint(
            "system_code ~ '^[a-z][a-z0-9_]{1,79}$'",
            name=op.f("ck_call_result_definitions_system_code_valid"),
        ),
        sa.CheckConstraint(
            "color ~ '^#[0-9A-Fa-f]{6}$'",
            name=op.f("ck_call_result_definitions_color_valid"),
        ),
        sa.CheckConstraint(
            "sort_order >= 0",
            name=op.f("ck_call_result_definitions_sort_order_non_negative"),
        ),
        sa.CheckConstraint(
            "NOT requires_callback_at OR requires_callback",
            name=op.f("ck_call_result_definitions_callback_date_requires_callback"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], ondelete="CASCADE", name=op.f("fk_call_result_definitions_tenant_id_tenants")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_call_result_definitions_tenant_project_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "catalog_id"],
            [
                "call_result_catalogs.tenant_id",
                "call_result_catalogs.project_id",
                "call_result_catalogs.id",
            ],
            name="fk_call_result_definitions_tenant_project_catalog",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_call_result_definitions")),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "system_code",
            name="uq_call_result_definitions_tenant_project_code",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "project_id",
            "id",
            name="uq_call_result_definitions_tenant_project_id",
        ),
    )
    op.create_index(op.f("ix_call_result_definitions_tenant_id"), "call_result_definitions", ["tenant_id"])
    op.create_index(op.f("ix_call_result_definitions_project_id"), "call_result_definitions", ["project_id"])
    op.create_index(op.f("ix_call_result_definitions_catalog_id"), "call_result_definitions", ["catalog_id"])
    op.create_index(op.f("ix_call_result_definitions_archived_at"), "call_result_definitions", ["archived_at"])
    op.create_index(
        "ix_call_result_definitions_project_active_order",
        "call_result_definitions",
        ["tenant_id", "project_id", "is_active", "sort_order"],
    )

    op.execute(
        """
        INSERT INTO call_result_catalogs
          (id, tenant_id, project_id, name, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), project.tenant_id, project.id, 'Результаты звонка', true, now(), now()
        FROM projects project
        WHERE NOT EXISTS (
          SELECT 1 FROM call_result_catalogs catalog
          WHERE catalog.tenant_id = project.tenant_id AND catalog.project_id = project.id
        )
        """
    )
    op.execute(
        """
        WITH defaults(
          system_code, category, name, translations, color, sort_order,
          requires_callback, requires_callback_at, do_not_call, counts_as_success,
          next_customer_status
        ) AS (
          VALUES
            ('success', 'successful', 'Успешно',
             '{"ru":"Успешно","uz":"Muvaffaqiyatli","en":"Success","kaa":"Tabıslı"}'::jsonb,
             '#16A34A', 10, false, false, false, true, 'completed'),
            ('callback', 'intermediate', 'Перезвонить',
             '{"ru":"Перезвонить","uz":"Qayta qo‘ng‘iroq","en":"Callback","kaa":"Qayta qońıraw"}'::jsonb,
             '#F59E0B', 20, true, true, false, false, 'callback'),
            ('other', 'intermediate', 'Другое',
             '{"ru":"Другое","uz":"Boshqa","en":"Other","kaa":"Basqa"}'::jsonb,
             '#64748B', 30, false, false, false, false, 'completed'),
            ('no_answer', 'unreachable', 'Нет ответа',
             '{"ru":"Нет ответа","uz":"Javob yo‘q","en":"No answer","kaa":"Juwap joq"}'::jsonb,
             '#64748B', 40, false, false, false, false, 'completed'),
            ('busy', 'unreachable', 'Занято',
             '{"ru":"Занято","uz":"Band","en":"Busy","kaa":"Bánt"}'::jsonb,
             '#F97316', 50, false, false, false, false, 'completed'),
            ('wrong_number', 'unsuccessful', 'Неверный номер',
             '{"ru":"Неверный номер","uz":"Noto‘g‘ri raqam","en":"Wrong number","kaa":"Qáte nomer"}'::jsonb,
             '#DC2626', 60, false, false, false, false, 'completed'),
            ('do_not_call', 'unsuccessful', 'Не звонить',
             '{"ru":"Не звонить","uz":"Qo‘ng‘iroq qilmang","en":"Do not call","kaa":"Qońıraw etpeń"}'::jsonb,
             '#991B1B', 70, false, false, true, false, 'do_not_call'),
            ('not_interested', 'unsuccessful', 'Не заинтересован',
             '{"ru":"Не заинтересован","uz":"Qiziqmaydi","en":"Not interested","kaa":"Qızıǵıwshılıq joq"}'::jsonb,
             '#DC2626', 80, false, false, false, false, 'completed'),
            ('failed', 'unsuccessful', 'Ошибка звонка',
             '{"ru":"Ошибка звонка","uz":"Qo‘ng‘iroq xatosi","en":"Call failed","kaa":"Qońıraw qáteligi"}'::jsonb,
             '#B91C1C', 90, false, false, false, false, 'completed')
        )
        INSERT INTO call_result_definitions (
          id, tenant_id, project_id, catalog_id, system_code, category, name,
          name_translations, description, color, sort_order, is_active,
          requires_comment, requires_callback, requires_callback_at, creates_task,
          next_customer_status, return_to_queue, completes_customer, do_not_call,
          counts_as_success, archived_at, created_at, updated_at
        )
        SELECT
          gen_random_uuid(), catalog.tenant_id, catalog.project_id, catalog.id,
          defaults.system_code, defaults.category, defaults.name, defaults.translations,
          '', defaults.color, defaults.sort_order, true, false,
          defaults.requires_callback, defaults.requires_callback_at, false,
          defaults.next_customer_status, false, NOT defaults.requires_callback,
          defaults.do_not_call, defaults.counts_as_success, NULL, now(), now()
        FROM call_result_catalogs catalog CROSS JOIN defaults
        ON CONFLICT (tenant_id, project_id, system_code) DO NOTHING
        """
    )

    op.add_column("call_outcomes", sa.Column("project_id", sa.Uuid(), nullable=True))
    op.add_column("call_outcomes", sa.Column("result_definition_id", sa.Uuid(), nullable=True))
    op.add_column("call_outcomes", sa.Column("category", sa.String(length=40), nullable=True))
    op.add_column("call_outcomes", sa.Column("color", sa.String(length=7), nullable=True))
    op.add_column(
        "call_outcomes",
        sa.Column("label_translations", sa.JSON(), nullable=True, server_default=sa.text("'{}'::json")),
    )
    op.add_column("call_outcomes", sa.Column("idempotency_key", sa.String(length=160), nullable=True))
    op.add_column("call_outcomes", sa.Column("request_fingerprint", sa.String(length=64), nullable=True))
    op.execute(
        """
        UPDATE call_outcomes outcome
        SET project_id = call.project_id
        FROM calls call
        WHERE call.id = outcome.call_id AND call.tenant_id = outcome.tenant_id
        """
    )
    op.execute(
        """
        WITH legacy AS (
          SELECT DISTINCT ON (outcome.tenant_id, outcome.project_id, outcome.code)
            outcome.tenant_id, outcome.project_id, outcome.code, outcome.label
          FROM call_outcomes outcome
          LEFT JOIN call_result_definitions definition
            ON definition.tenant_id = outcome.tenant_id
           AND definition.project_id = outcome.project_id
           AND definition.system_code = lower(outcome.code)
          WHERE definition.id IS NULL
          ORDER BY outcome.tenant_id, outcome.project_id, outcome.code, outcome.created_at, outcome.id
        )
        INSERT INTO call_result_definitions (
          id, tenant_id, project_id, catalog_id, system_code, category, name,
          name_translations, description, color, sort_order, is_active,
          requires_comment, requires_callback, requires_callback_at, creates_task,
          next_customer_status, return_to_queue, completes_customer, do_not_call,
          counts_as_success, archived_at, created_at, updated_at
        )
        SELECT
          gen_random_uuid(), legacy.tenant_id, legacy.project_id, catalog.id,
          'legacy_' || substr(md5(legacy.code), 1, 16), 'intermediate',
          COALESCE(NULLIF(legacy.label, ''), legacy.code),
          jsonb_build_object('ru', COALESCE(NULLIF(legacy.label, ''), legacy.code)),
          'Автоматически сохранённый legacy-результат', '#64748B', 1000,
          false, false, false, false, false, 'completed', false, true, false,
          false, now(), now(), now()
        FROM legacy
        JOIN call_result_catalogs catalog
          ON catalog.tenant_id = legacy.tenant_id AND catalog.project_id = legacy.project_id
        ON CONFLICT (tenant_id, project_id, system_code) DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE call_outcomes outcome
        SET result_definition_id = definition.id,
            category = definition.category,
            color = definition.color,
            label_translations = jsonb_build_object('ru', outcome.label)
        FROM call_result_definitions definition
        WHERE definition.tenant_id = outcome.tenant_id
          AND definition.project_id = outcome.project_id
          AND definition.system_code = CASE
            WHEN lower(outcome.code) IN (
              'success', 'no_answer', 'busy', 'callback', 'wrong_number',
              'do_not_call', 'not_interested', 'failed', 'other'
            ) THEN lower(outcome.code)
            ELSE 'legacy_' || substr(md5(outcome.code), 1, 16)
          END
        """
    )
    op.alter_column("call_outcomes", "project_id", nullable=False)
    op.alter_column("call_outcomes", "result_definition_id", nullable=False)
    op.alter_column("call_outcomes", "category", nullable=False)
    op.alter_column("call_outcomes", "color", nullable=False)
    op.alter_column("call_outcomes", "label_translations", nullable=False)
    op.create_index(op.f("ix_call_outcomes_project_id"), "call_outcomes", ["project_id"])
    op.create_index(op.f("ix_call_outcomes_result_definition_id"), "call_outcomes", ["result_definition_id"])
    op.create_index("ix_call_outcomes_project_category", "call_outcomes", ["project_id", "category"])
    op.create_index(
        "uq_call_outcomes_tenant_idempotency_key",
        "call_outcomes",
        ["tenant_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_check_constraint(
        "category_valid",
        "call_outcomes",
        "category IN ('successful', 'intermediate', 'unreachable', 'unsuccessful')",
    )
    op.create_foreign_key(
        "fk_call_outcomes_tenant_project_result_definition",
        "call_outcomes",
        "call_result_definitions",
        ["tenant_id", "project_id", "result_definition_id"],
        ["tenant_id", "project_id", "id"],
        ondelete="RESTRICT",
    )

    op.execute(
        """
        INSERT INTO permissions (id, code, description, created_at, updated_at)
        VALUES
          (gen_random_uuid(), 'call_results:read', 'Read project call result catalog', now(), now()),
          (gen_random_uuid(), 'call_results:manage', 'Manage project call result catalog', now(), now())
        ON CONFLICT (code) DO NOTHING
        """
    )
    enable_rls("call_result_definitions")
    for table in ("call_result_catalogs", "call_outcomes"):
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')


def downgrade() -> None:
    for table in ("call_result_catalogs", "call_outcomes", "call_result_definitions"):
        op.execute(f'ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY')

    op.execute("DELETE FROM permissions WHERE code IN ('call_results:read', 'call_results:manage')")
    op.drop_constraint(
        "fk_call_outcomes_tenant_project_result_definition",
        "call_outcomes",
        type_="foreignkey",
    )
    op.drop_constraint(op.f("ck_call_outcomes_category_valid"), "call_outcomes", type_="check")
    op.drop_index("uq_call_outcomes_tenant_idempotency_key", table_name="call_outcomes")
    op.drop_index("ix_call_outcomes_project_category", table_name="call_outcomes")
    op.drop_index(op.f("ix_call_outcomes_result_definition_id"), table_name="call_outcomes")
    op.drop_index(op.f("ix_call_outcomes_project_id"), table_name="call_outcomes")
    op.drop_column("call_outcomes", "request_fingerprint")
    op.drop_column("call_outcomes", "idempotency_key")
    op.drop_column("call_outcomes", "label_translations")
    op.drop_column("call_outcomes", "color")
    op.drop_column("call_outcomes", "category")
    op.drop_column("call_outcomes", "result_definition_id")
    op.drop_column("call_outcomes", "project_id")

    op.execute('DROP POLICY IF EXISTS "call_result_definitions_tenant_isolation" ON "call_result_definitions"')
    op.drop_index("ix_call_result_definitions_project_active_order", table_name="call_result_definitions")
    op.drop_index(op.f("ix_call_result_definitions_archived_at"), table_name="call_result_definitions")
    op.drop_index(op.f("ix_call_result_definitions_catalog_id"), table_name="call_result_definitions")
    op.drop_index(op.f("ix_call_result_definitions_project_id"), table_name="call_result_definitions")
    op.drop_index(op.f("ix_call_result_definitions_tenant_id"), table_name="call_result_definitions")
    op.drop_table("call_result_definitions")

    for table in ("call_result_catalogs", "call_outcomes"):
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
