"""add versioned knowledge ingestion and pgvector retrieval

Revision ID: p58h3m9k7i41
Revises: o47g2l8j6h30
Create Date: 2026-08-03 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision: str = "p58h3m9k7i41"
down_revision: str | None = "o47g2l8j6h30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enable_rls(table: str) -> None:
    op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY "{table}_tenant_isolation" ON "{table}" '
        "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.add_column("knowledge_sources", sa.Column("project_id", sa.Uuid(), nullable=True))
    op.add_column(
        "knowledge_sources", sa.Column("description", sa.String(1000), server_default="", nullable=False)
    )
    op.add_column(
        "knowledge_sources",
        sa.Column("default_language_code", sa.String(32), server_default="ru", nullable=False),
    )
    op.add_column("knowledge_sources", sa.Column("active_revision_id", sa.Uuid(), nullable=True))
    op.add_column("knowledge_sources", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "knowledge_sources", sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False)
    )
    op.execute(
        """
        UPDATE knowledge_sources AS source
        SET project_id = COALESCE(
            (SELECT project.id FROM projects AS project
             WHERE project.tenant_id = source.tenant_id
               AND project.knowledge_source_id = source.id
             ORDER BY project.is_default DESC, project.created_at LIMIT 1),
            (SELECT project.id FROM projects AS project
             WHERE project.tenant_id = source.tenant_id
             ORDER BY project.is_default DESC, project.created_at LIMIT 1)
        )
        """
    )
    op.create_index("ix_knowledge_sources_project_id", "knowledge_sources", ["project_id"])
    op.create_index("ix_knowledge_sources_active_revision_id", "knowledge_sources", ["active_revision_id"])
    op.create_index("ix_knowledge_sources_archived_at", "knowledge_sources", ["archived_at"])
    op.create_index(
        "ix_knowledge_sources_tenant_project_archived",
        "knowledge_sources",
        ["tenant_id", "project_id", "archived_at"],
    )
    op.create_unique_constraint(
        "uq_knowledge_sources_tenant_project_id",
        "knowledge_sources",
        ["tenant_id", "project_id", "id"],
    )
    op.create_check_constraint(
        "knowledge_source_lock_version_positive", "knowledge_sources", "lock_version >= 1"
    )
    op.create_foreign_key(
        "fk_knowledge_sources_tenant_project",
        "knowledge_sources",
        "projects",
        ["tenant_id", "project_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "knowledge_base_revisions",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_source_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), server_default="draft", nullable=False),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_from_revision_id", sa.Uuid(), nullable=True),
        sa.Column("embedding_provider", sa.String(80), server_default="mock", nullable=False),
        sa.Column("embedding_model", sa.String(120), server_default="kline-deterministic-v1", nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), server_default="64", nullable=False),
        sa.Column("index_version", sa.String(120), server_default="kb-index-v1", nullable=False),
        sa.Column("chunking_config", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('draft','published','archived')", name="kb_revision_status_valid"),
        sa.CheckConstraint("lock_version >= 1", name="kb_revision_lock_version_positive"),
        sa.CheckConstraint("embedding_dimension > 0", name="kb_revision_embedding_dimension_positive"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "knowledge_source_id"],
            ["knowledge_sources.tenant_id", "knowledge_sources.project_id", "knowledge_sources.id"],
            name="fk_kb_revisions_tenant_project_source",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_from_revision_id"], ["knowledge_base_revisions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("knowledge_source_id", "version", name="uq_kb_revisions_source_version"),
        sa.UniqueConstraint("tenant_id", "project_id", "id", name="uq_kb_revisions_tenant_project_id"),
    )
    op.create_index("ix_knowledge_base_revisions_tenant_id", "knowledge_base_revisions", ["tenant_id"])
    op.create_index("ix_knowledge_base_revisions_project_id", "knowledge_base_revisions", ["project_id"])
    op.create_index(
        "ix_knowledge_base_revisions_knowledge_source_id", "knowledge_base_revisions", ["knowledge_source_id"]
    )
    op.create_index(
        "ix_knowledge_base_revisions_created_from_revision_id",
        "knowledge_base_revisions",
        ["created_from_revision_id"],
    )
    op.create_index(
        "ix_kb_revisions_source_status_version",
        "knowledge_base_revisions",
        ["tenant_id", "knowledge_source_id", "status", "version"],
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_kb_revisions_single_draft ON knowledge_base_revisions "
        "(tenant_id, knowledge_source_id) WHERE status = 'draft'"
    )
    _enable_rls("knowledge_base_revisions")

    op.execute(
        """
        INSERT INTO knowledge_base_revisions
            (id, tenant_id, project_id, knowledge_source_id, version, status, lock_version,
             embedding_provider, embedding_model, embedding_dimension, index_version,
             chunking_config, published_at, created_at, updated_at)
        SELECT gen_random_uuid(), tenant_id, project_id, id, 1, 'published', 1,
               'mock', 'kline-deterministic-v1', 64, 'legacy-v1',
               json_build_object('size_chars', 1800, 'overlap_chars', 220), now(), now(), now()
        FROM knowledge_sources WHERE project_id IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE knowledge_sources AS source
        SET active_revision_id = revision.id
        FROM knowledge_base_revisions AS revision
        WHERE revision.knowledge_source_id = source.id AND revision.version = 1
        """
    )
    op.create_foreign_key(
        "fk_knowledge_sources_active_revision",
        "knowledge_sources",
        "knowledge_base_revisions",
        ["active_revision_id"],
        ["id"],
        ondelete="SET NULL",
        use_alter=True,
    )

    op.add_column("knowledge_documents", sa.Column("project_id", sa.Uuid(), nullable=True))
    op.add_column("knowledge_documents", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "knowledge_documents", sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False)
    )
    op.execute(
        "UPDATE knowledge_documents AS document SET project_id = source.project_id "
        "FROM knowledge_sources AS source WHERE source.id = document.source_id "
        "AND source.tenant_id = document.tenant_id"
    )
    op.create_index("ix_knowledge_documents_project_id", "knowledge_documents", ["project_id"])
    op.create_index("ix_knowledge_documents_archived_at", "knowledge_documents", ["archived_at"])
    op.create_index(
        "ix_knowledge_documents_source_archived",
        "knowledge_documents",
        ["tenant_id", "source_id", "archived_at"],
    )
    op.create_unique_constraint(
        "uq_knowledge_documents_tenant_project_id",
        "knowledge_documents",
        ["tenant_id", "project_id", "id"],
    )
    op.create_check_constraint(
        "knowledge_document_lock_version_positive", "knowledge_documents", "lock_version >= 1"
    )
    op.create_foreign_key(
        "fk_knowledge_documents_tenant_project",
        "knowledge_documents",
        "projects",
        ["tenant_id", "project_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_knowledge_documents_tenant_source",
        "knowledge_documents",
        "knowledge_sources",
        ["tenant_id", "source_id"],
        ["tenant_id", "id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "fk_knowledge_documents_source_id_knowledge_sources", "knowledge_documents", type_="foreignkey"
    )

    op.create_table(
        "knowledge_document_versions",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_source_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), server_default="uploaded", nullable=False),
        sa.Column("title_snapshot", sa.String(240), nullable=False),
        sa.Column("language_code", sa.String(32), server_default="ru", nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("file_type", sa.String(12), nullable=False),
        sa.Column("content_type", sa.String(160), nullable=False),
        sa.Column("object_key", sa.String(640), nullable=True),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("content_length", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("extracted_text", sa.Text(), server_default="", nullable=False),
        sa.Column("normalized_text", sa.Text(), server_default="", nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("extraction_metadata", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("safe_error_code", sa.String(80), nullable=True),
        sa.Column("safe_error_message", sa.String(500), nullable=True),
        sa.Column("lock_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extraction_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("chunking_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("embedding_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('uploaded','queued','extracting','chunking','embedding','ready',"
            "'failed','needs_ocr','archived')",
            name="knowledge_document_version_status_valid",
        ),
        sa.CheckConstraint("lock_version >= 1", name="knowledge_document_version_lock_version_positive"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "revision_id"],
            [
                "knowledge_base_revisions.tenant_id",
                "knowledge_base_revisions.project_id",
                "knowledge_base_revisions.id",
            ],
            name="fk_knowledge_document_versions_tenant_revision",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "document_id"],
            ["knowledge_documents.tenant_id", "knowledge_documents.project_id", "knowledge_documents.id"],
            name="fk_knowledge_document_versions_tenant_document",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "version", name="uq_knowledge_document_versions_document_version"),
        sa.UniqueConstraint("tenant_id", "project_id", "id", name="uq_kdv_tenant_project_id"),
    )
    for column in ("tenant_id", "project_id", "knowledge_source_id", "revision_id", "document_id"):
        op.create_index(f"ix_knowledge_document_versions_{column}", "knowledge_document_versions", [column])
    op.create_index(
        "ix_kdv_revision_status", "knowledge_document_versions", ["tenant_id", "revision_id", "status"]
    )
    op.create_index(
        "ix_kdv_checksum_scope",
        "knowledge_document_versions",
        ["tenant_id", "knowledge_source_id", "checksum_sha256"],
    )
    op.create_index(
        "uq_kdv_revision_active_document",
        "knowledge_document_versions",
        ["tenant_id", "revision_id", "document_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'archived'"),
    )
    _enable_rls("knowledge_document_versions")

    op.execute(
        """
        INSERT INTO knowledge_document_versions
            (id, tenant_id, project_id, knowledge_source_id, revision_id, document_id, version,
             status, title_snapshot, language_code, original_filename, file_type, content_type,
             checksum_sha256,
             content_length, extracted_text, normalized_text, page_count, extraction_metadata,
             lock_version, uploaded_at, queued_at, ready_at, created_at, updated_at)
        SELECT gen_random_uuid(), document.tenant_id, document.project_id, document.source_id,
               revision.id, document.id, 1, 'ready', document.title, document.language,
               document.title || '.txt', 'txt', 'text/plain; charset=utf-8', document.content_hash,
               octet_length(document.content), document.content, document.content, 1,
               json_build_object('legacy', true), 1, document.created_at, document.created_at,
               document.created_at, document.created_at, document.updated_at
        FROM knowledge_documents AS document
        JOIN knowledge_base_revisions AS revision
          ON revision.tenant_id = document.tenant_id
         AND revision.knowledge_source_id = document.source_id
         AND revision.version = 1
        WHERE document.project_id IS NOT NULL
        """
    )

    for _name, column in (
        ("project_id", sa.Column("project_id", sa.Uuid(), nullable=True)),
        ("revision_id", sa.Column("revision_id", sa.Uuid(), nullable=True)),
        ("document_version_id", sa.Column("document_version_id", sa.Uuid(), nullable=True)),
        ("language_code", sa.Column("language_code", sa.String(32), server_default="ru", nullable=False)),
        ("page_number", sa.Column("page_number", sa.Integer(), nullable=True)),
        ("section", sa.Column("section", sa.String(500), nullable=True)),
        ("normalized_content", sa.Column("normalized_content", sa.Text(), server_default="", nullable=False)),
        ("content_checksum", sa.Column("content_checksum", sa.String(64), server_default="", nullable=False)),
        ("character_count", sa.Column("character_count", sa.Integer(), server_default="0", nullable=False)),
        ("embedding", sa.Column("embedding", Vector(), nullable=True)),
        (
            "embedding_status",
            sa.Column("embedding_status", sa.String(24), server_default="pending", nullable=False),
        ),
        ("embedding_provider", sa.Column("embedding_provider", sa.String(80), nullable=True)),
        ("embedding_model", sa.Column("embedding_model", sa.String(120), nullable=True)),
        ("embedding_dimension", sa.Column("embedding_dimension", sa.Integer(), nullable=True)),
        ("index_version", sa.Column("index_version", sa.String(120), nullable=True)),
    ):
        op.add_column("knowledge_chunks", column)
    op.execute(
        """
        UPDATE knowledge_chunks AS chunk
        SET project_id = document.project_id,
            revision_id = version.revision_id,
            document_version_id = version.id,
            language_code = version.language_code,
            page_number = 1,
            normalized_content = chunk.content,
            content_checksum = document.content_hash,
            character_count = char_length(chunk.content),
            embedding_status = 'pending'
        FROM knowledge_documents AS document
        JOIN knowledge_document_versions AS version ON version.document_id = document.id
        WHERE chunk.document_id = document.id AND chunk.tenant_id = document.tenant_id
        """
    )
    for column in ("project_id", "revision_id", "document_version_id"):
        op.create_index(f"ix_knowledge_chunks_{column}", "knowledge_chunks", [column])
    op.create_index(
        "ix_knowledge_chunks_retrieval_scope",
        "knowledge_chunks",
        ["tenant_id", "project_id", "revision_id", "language_code", "embedding_status"],
    )
    op.create_foreign_key(
        "fk_knowledge_chunks_tenant_revision",
        "knowledge_chunks",
        "knowledge_base_revisions",
        ["tenant_id", "project_id", "revision_id"],
        ["tenant_id", "project_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_knowledge_chunks_tenant_document_version",
        "knowledge_chunks",
        "knowledge_document_versions",
        ["tenant_id", "project_id", "document_version_id"],
        ["tenant_id", "project_id", "id"],
        ondelete="CASCADE",
    )

    op.create_table(
        "document_ingestion_jobs",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("stage", sa.String(24), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="4", nullable=False),
        sa.Column(
            "next_attempt_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("safe_error_code", sa.String(80), nullable=True),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending','processing','succeeded','failed','cancelled')",
            name="ingestion_job_status_valid",
        ),
        sa.CheckConstraint("attempts >= 0", name="ingestion_job_attempts_non_negative"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "document_version_id"],
            [
                "knowledge_document_versions.tenant_id",
                "knowledge_document_versions.project_id",
                "knowledge_document_versions.id",
            ],
            name="fk_document_ingestion_jobs_tenant_version",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "document_version_id", name="uq_ingestion_job_document_version"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_ingestion_job_idempotency"),
    )
    for column in ("tenant_id", "project_id", "document_version_id", "lease_token", "lease_expires_at"):
        op.create_index(f"ix_document_ingestion_jobs_{column}", "document_ingestion_jobs", [column])
    op.create_index(
        "ix_ingestion_jobs_claim",
        "document_ingestion_jobs",
        ["status", "next_attempt_at", "lease_expires_at"],
    )
    _enable_rls("document_ingestion_jobs")

    op.create_table(
        "knowledge_retrieval_executions",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=True),
        sa.Column("query_hash", sa.String(64), nullable=False),
        sa.Column("masked_query", sa.String(1000), nullable=False),
        sa.Column("language_code", sa.String(32), nullable=False),
        sa.Column("chunk_ids", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False),
        sa.Column("citations", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False),
        sa.Column("scores", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False),
        sa.Column("usage", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("latency_ms", sa.Integer(), server_default="0", nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("index_version", sa.String(120), nullable=False),
        sa.Column("no_match", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["call_id"], ["calls.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id", "revision_id"],
            [
                "knowledge_base_revisions.tenant_id",
                "knowledge_base_revisions.project_id",
                "knowledge_base_revisions.id",
            ],
            name="fk_knowledge_retrieval_tenant_revision",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_knowledge_retrieval_idempotency"),
    )
    for column in ("tenant_id", "project_id", "revision_id", "call_id"):
        op.create_index(
            f"ix_knowledge_retrieval_executions_{column}", "knowledge_retrieval_executions", [column]
        )
    op.create_index(
        "ix_knowledge_retrieval_revision_created",
        "knowledge_retrieval_executions",
        ["tenant_id", "revision_id", "created_at"],
    )
    _enable_rls("knowledge_retrieval_executions")

    op.add_column("calls", sa.Column("knowledge_base_revision_id", sa.Uuid(), nullable=True))
    op.create_index("ix_calls_knowledge_base_revision_id", "calls", ["knowledge_base_revision_id"])
    op.create_foreign_key(
        "fk_calls_knowledge_base_revision",
        "calls",
        "knowledge_base_revisions",
        ["tenant_id", "project_id", "knowledge_base_revision_id"],
        ["tenant_id", "project_id", "id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint("fk_calls_knowledge_base_revision", "calls", type_="foreignkey")
    op.drop_index("ix_calls_knowledge_base_revision_id", table_name="calls")
    op.drop_column("calls", "knowledge_base_revision_id")
    op.drop_table("knowledge_retrieval_executions")
    op.drop_table("document_ingestion_jobs")
    op.drop_constraint("fk_knowledge_chunks_tenant_document_version", "knowledge_chunks", type_="foreignkey")
    op.drop_constraint("fk_knowledge_chunks_tenant_revision", "knowledge_chunks", type_="foreignkey")
    op.drop_index("ix_knowledge_chunks_retrieval_scope", table_name="knowledge_chunks")
    for column in ("document_version_id", "revision_id", "project_id"):
        op.drop_index(f"ix_knowledge_chunks_{column}", table_name="knowledge_chunks")
    for column in (
        "index_version",
        "embedding_dimension",
        "embedding_model",
        "embedding_provider",
        "embedding_status",
        "embedding",
        "character_count",
        "content_checksum",
        "normalized_content",
        "section",
        "page_number",
        "language_code",
        "document_version_id",
        "revision_id",
        "project_id",
    ):
        op.drop_column("knowledge_chunks", column)
    op.drop_table("knowledge_document_versions")
    op.create_foreign_key(
        "fk_knowledge_documents_source_id_knowledge_sources",
        "knowledge_documents",
        "knowledge_sources",
        ["source_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("fk_knowledge_documents_tenant_source", "knowledge_documents", type_="foreignkey")
    op.drop_constraint("fk_knowledge_documents_tenant_project", "knowledge_documents", type_="foreignkey")
    op.drop_constraint("knowledge_document_lock_version_positive", "knowledge_documents", type_="check")
    op.drop_constraint("uq_knowledge_documents_tenant_project_id", "knowledge_documents", type_="unique")
    op.drop_index("ix_knowledge_documents_source_archived", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_archived_at", table_name="knowledge_documents")
    op.drop_index("ix_knowledge_documents_project_id", table_name="knowledge_documents")
    op.drop_column("knowledge_documents", "lock_version")
    op.drop_column("knowledge_documents", "archived_at")
    op.drop_column("knowledge_documents", "project_id")
    op.drop_constraint("fk_knowledge_sources_active_revision", "knowledge_sources", type_="foreignkey")
    op.drop_table("knowledge_base_revisions")
    op.drop_constraint("fk_knowledge_sources_tenant_project", "knowledge_sources", type_="foreignkey")
    op.drop_constraint("knowledge_source_lock_version_positive", "knowledge_sources", type_="check")
    op.drop_constraint("uq_knowledge_sources_tenant_project_id", "knowledge_sources", type_="unique")
    op.drop_index("ix_knowledge_sources_tenant_project_archived", table_name="knowledge_sources")
    op.drop_index("ix_knowledge_sources_archived_at", table_name="knowledge_sources")
    op.drop_index("ix_knowledge_sources_project_id", table_name="knowledge_sources")
    op.drop_index("ix_knowledge_sources_active_revision_id", table_name="knowledge_sources")
    for column in (
        "lock_version",
        "archived_at",
        "active_revision_id",
        "default_language_code",
        "description",
        "project_id",
    ):
        op.drop_column("knowledge_sources", column)
    # The shared vector extension is intentionally retained on downgrade. Removing a database
    # extension can break unrelated future users and is not required to restore the old schema.
