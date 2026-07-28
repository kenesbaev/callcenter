from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column

from teamora_api.db import Base, TenantOwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from teamora_api.enums import (
    CallChannel,
    CallStatus,
    DeliveryStatus,
    IntegrationStatus,
    LanguageCode,
    LanguageReadiness,
    OperatorVersionStatus,
    QueueStatus,
    RoleName,
    TenantStatus,
    ToolExecutionStatus,
    TranscriptSpeaker,
    TransferStatus,
)


def enum_type(enum: type, length: int = 40) -> SAEnum:
    return SAEnum(
        enum,
        native_enum=False,
        length=length,
        values_callable=lambda enum_class: [item.value for item in enum_class],
    )


class Tenant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    status: Mapped[TenantStatus] = mapped_column(enum_type(TenantStatus), default=TenantStatus.ACTIVE)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class TenantSettings(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "tenant_settings"
    __table_args__ = (UniqueConstraint("tenant_id"),)

    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Tashkent")
    default_language: Mapped[LanguageCode] = mapped_column(enum_type(LanguageCode), default=LanguageCode.RU)
    recording_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    recording_disclosure_required: Mapped[bool] = mapped_column(Boolean, default=True)
    retention_days: Mapped[int] = mapped_column(Integer, default=90)
    max_concurrent_calls: Mapped[int] = mapped_column(Integer, default=5)


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Role(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "roles"

    name: Mapped[RoleName] = mapped_column(enum_type(RoleName), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)


class Permission(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)


class RolePermission(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "permission_id"),)

    role_id: Mapped[UUID] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"))
    permission_id: Mapped[UUID] = mapped_column(ForeignKey("permissions.id", ondelete="CASCADE"))


class Membership(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id"),)

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[RoleName] = mapped_column(enum_type(RoleName), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Project(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("max_concurrent_calls > 0", name="max_concurrent_calls_positive"),
        CheckConstraint(
            "status IN ('active', 'paused', 'archived')",
            name="status_valid",
        ),
        UniqueConstraint("tenant_id", "name", name="uq_projects_tenant_name"),
        UniqueConstraint("tenant_id", "id", name="uq_projects_tenant_id_id"),
        Index("ix_projects_tenant_status", "tenant_id", "status"),
        Index(
            "uq_projects_default_per_tenant",
            "tenant_id",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False, index=True)
    outbound_number: Mapped[str | None] = mapped_column(String(32))
    max_concurrent_calls: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    working_hours: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ProjectUser(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "project_users"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_project_users_tenant_project_projects",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_project_users_tenant_user_memberships",
            ondelete="CASCADE",
        ),
        UniqueConstraint("project_id", "user_id", name="uq_project_users_project_user"),
    )

    project_id: Mapped[UUID] = mapped_column(index=True)
    user_id: Mapped[UUID] = mapped_column(index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class RefreshToken(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "refresh_tokens"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    membership_id: Mapped[UUID] = mapped_column(ForeignKey("memberships.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rotated_from_id: Mapped[UUID | None] = mapped_column(ForeignKey("refresh_tokens.id", ondelete="SET NULL"))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Invitation(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "invitations"

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[RoleName] = mapped_column(enum_type(RoleName), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    invited_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SipTrunk(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "sip_trunks"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_host: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_port: Mapped[int] = mapped_column(Integer, default=5061)
    transport: Mapped[str] = mapped_column(String(16), default="tls")
    allowed_ips: Mapped[list[str]] = mapped_column(JSON, default=list)
    max_channels: Mapped[int] = mapped_column(Integer, default=5)
    status: Mapped[IntegrationStatus] = mapped_column(
        enum_type(IntegrationStatus), default=IntegrationStatus.CONFIGURED
    )


class SipCredentialReference(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "sip_credential_references"

    sip_trunk_id: Mapped[UUID] = mapped_column(ForeignKey("sip_trunks.id", ondelete="CASCADE"), unique=True)
    secret_provider: Mapped[str] = mapped_column(String(40), default="local_encrypted")
    secret_key: Mapped[str] = mapped_column(String(255), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PhoneNumber(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "phone_numbers"
    __table_args__ = (UniqueConstraint("e164"),)

    sip_trunk_id: Mapped[UUID | None] = mapped_column(ForeignKey("sip_trunks.id", ondelete="SET NULL"))
    e164: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class InboundRoute(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "inbound_routes"

    phone_number_id: Mapped[UUID] = mapped_column(
        ForeignKey("phone_numbers.id", ondelete="CASCADE"), unique=True
    )
    ai_operator_id: Mapped[UUID | None] = mapped_column(ForeignKey("ai_operators.id", ondelete="SET NULL"))
    queue_id: Mapped[UUID | None] = mapped_column(ForeignKey("operator_queues.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class OutboundRoute(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "outbound_routes"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    sip_trunk_id: Mapped[UUID] = mapped_column(ForeignKey("sip_trunks.id", ondelete="CASCADE"))
    dial_pattern: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)


class VoiceProfile(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "voice_profiles"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), default="openai")
    provider_voice_id: Mapped[str] = mapped_column(String(80), default="marin")
    speaking_rate: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=Decimal("1.00"))
    status: Mapped[IntegrationStatus] = mapped_column(
        enum_type(IntegrationStatus), default=IntegrationStatus.CONFIGURED
    )


class AiOperator(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "ai_operators"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_ai_operators_tenant_project_projects",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("project_id", "name", name="uq_ai_operators_project_name"),
    )

    project_id: Mapped[UUID] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(500), default="")
    active_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "ai_operator_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_ai_operators_active_version_id_ai_operator_versions",
        )
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class AiOperatorVersion(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "ai_operator_versions"
    __table_args__ = (UniqueConstraint("ai_operator_id", "version"),)

    ai_operator_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_operators.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[OperatorVersionStatus] = mapped_column(
        enum_type(OperatorVersionStatus), default=OperatorVersionStatus.DRAFT
    )
    system_instructions: Mapped[str] = mapped_column(Text, nullable=False)
    greeting_by_language: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    allowed_languages: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["ru"])
    allowed_tools: Mapped[list[str]] = mapped_column(JSON, default=list)
    transfer_policy: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    business_hours: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    voice_profile_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("voice_profiles.id", ondelete="SET NULL")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LanguageConfiguration(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "language_configurations"
    __table_args__ = (UniqueConstraint("tenant_id", "code"),)

    code: Mapped[LanguageCode] = mapped_column(enum_type(LanguageCode), nullable=False)
    readiness: Mapped[LanguageReadiness] = mapped_column(enum_type(LanguageReadiness), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    stt_provider: Mapped[str | None] = mapped_column(String(40))
    tts_provider: Mapped[str | None] = mapped_column(String(40))
    last_quality_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CallFlow(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "call_flows"

    project_id: Mapped[UUID] = mapped_column(index=True)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    active_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "call_flow_versions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_call_flows_active_version_id_call_flow_versions",
        )
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_call_flows_tenant_project_projects",
            ondelete="RESTRICT",
        ),
    )


class CallFlowVersion(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "call_flow_versions"
    __table_args__ = (UniqueConstraint("call_flow_id", "version"),)

    call_flow_id: Mapped[UUID] = mapped_column(ForeignKey("call_flows.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[OperatorVersionStatus] = mapped_column(enum_type(OperatorVersionStatus))
    definition: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeSource(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "knowledge_sources"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), default="text")
    status: Mapped[IntegrationStatus] = mapped_column(
        enum_type(IntegrationStatus), default=IntegrationStatus.CONFIGURED
    )


class KnowledgeDocument(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "knowledge_documents"

    source_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    language: Mapped[LanguageCode] = mapped_column(enum_type(LanguageCode), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class KnowledgeChunk(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "knowledge_chunks"

    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding_ref: Mapped[str | None] = mapped_column(String(255))


class KnowledgeSyncJob(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "knowledge_sync_jobs"

    source_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_sources.id", ondelete="CASCADE"))
    status: Mapped[DeliveryStatus] = mapped_column(enum_type(DeliveryStatus), default=DeliveryStatus.PENDING)
    error_code: Mapped[str | None] = mapped_column(String(80))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Customer(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "customers"

    project_id: Mapped[UUID] = mapped_column(index=True)

    display_name: Mapped[str | None] = mapped_column(String(160))
    external_reference: Mapped[str | None] = mapped_column(String(160))
    preferred_language: Mapped[LanguageCode | None] = mapped_column(enum_type(LanguageCode))
    is_anonymized: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="new", nullable=False, index=True)
    custom_fields: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    locked_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lock_token: Mapped[UUID | None] = mapped_column(index=True)
    last_call_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_call_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_customers_tenant_project_projects",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "id",
            name="uq_customers_tenant_project_id",
        ),
    )


class CustomerContact(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "customer_contacts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_customer_contacts_tenant_project_projects",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "customer_id"],
            ["customers.tenant_id", "customers.project_id", "customers.id"],
            name="fk_customer_contacts_tenant_project_customer",
            ondelete="CASCADE",
        ),
        Index(
            "uq_customer_contacts_project_phone",
            "project_id",
            "normalized_value",
            unique=True,
            postgresql_where=text("kind = 'phone'"),
        ),
    )

    project_id: Mapped[UUID] = mapped_column(index=True)
    customer_id: Mapped[UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(320), nullable=False)
    display_value: Mapped[str] = mapped_column(String(320), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)


class CustomerNote(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "customer_notes"

    customer_id: Mapped[UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    author_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    content: Mapped[str] = mapped_column(Text, nullable=False)


class Call(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "calls"

    project_id: Mapped[UUID] = mapped_column(index=True)
    external_call_id: Mapped[str | None] = mapped_column(String(160))
    channel: Mapped[CallChannel] = mapped_column(enum_type(CallChannel), nullable=False)
    status: Mapped[CallStatus] = mapped_column(enum_type(CallStatus), default=CallStatus.QUEUED, index=True)
    direction: Mapped[str] = mapped_column(String(16), default="inbound")
    phone_number_id: Mapped[UUID | None] = mapped_column(ForeignKey("phone_numbers.id", ondelete="SET NULL"))
    customer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), index=True
    )
    operator_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    ai_operator_id: Mapped[UUID | None] = mapped_column(ForeignKey("ai_operators.id", ondelete="SET NULL"))
    ai_operator_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_operator_versions.id", ondelete="SET NULL")
    )
    language: Mapped[LanguageCode | None] = mapped_column(enum_type(LanguageCode))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[str] = mapped_column(String(40), default="mock", nullable=False)
    from_number: Mapped[str | None] = mapped_column(String(32))
    to_number: Mapped[str | None] = mapped_column(String(32))
    transfer_reason: Mapped[str | None] = mapped_column(String(500))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_calls_tenant_project_projects",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "external_call_id"),
        Index("ix_calls_tenant_started", "tenant_id", "started_at"),
        Index("ix_calls_project_status", "project_id", "status"),
    )


class CallParticipant(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "call_participants"

    call_id: Mapped[UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"), index=True)
    participant_type: Mapped[str] = mapped_column(String(24), nullable=False)
    display_label: Mapped[str] = mapped_column(String(160), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CallEvent(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "call_events"

    call_id: Mapped[UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    safe_payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)

    __table_args__ = (UniqueConstraint("call_id", "sequence"),)


class TranscriptSegment(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "transcript_segments"

    call_id: Mapped[UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker: Mapped[TranscriptSpeaker] = mapped_column(enum_type(TranscriptSpeaker), nullable=False)
    language: Mapped[LanguageCode] = mapped_column(enum_type(LanguageCode), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    started_ms: Mapped[int | None] = mapped_column(Integer)
    ended_ms: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (UniqueConstraint("call_id", "sequence"),)


class CallSummary(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "call_summaries"
    __table_args__ = (UniqueConstraint("call_id"),)

    call_id: Mapped[UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"))
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    topics: Mapped[list[str]] = mapped_column(JSON, default=list)
    result: Mapped[str] = mapped_column(String(80), nullable=False)
    generated_by: Mapped[str] = mapped_column(String(80), nullable=False)


class CallRecording(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "call_recordings"

    call_id: Mapped[UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"), index=True)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    encryption_key_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    recording_paused_ranges: Mapped[list[dict[str, int]]] = mapped_column(JSON, default=list)
    delete_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CallTag(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "call_tags"
    __table_args__ = (UniqueConstraint("call_id", "tag"),)

    call_id: Mapped[UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"), index=True)
    tag: Mapped[str] = mapped_column(String(80), nullable=False)


class CallOutcome(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "call_outcomes"
    __table_args__ = (UniqueConstraint("call_id"),)

    call_id: Mapped[UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)


class CallbackTask(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "callback_tasks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_callback_tasks_tenant_project_projects",
            ondelete="RESTRICT",
        ),
        Index("ix_callback_tasks_tenant_status_due", "tenant_id", "status", "due_at"),
        Index("ix_callback_tasks_project_status_due", "project_id", "status", "due_at"),
    )

    project_id: Mapped[UUID] = mapped_column(index=True)
    customer_id: Mapped[UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    call_id: Mapped[UUID | None] = mapped_column(ForeignKey("calls.id", ondelete="SET NULL"), index=True)
    assigned_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False, index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HumanOperator(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "human_operators"
    __table_args__ = (UniqueConstraint("tenant_id", "membership_id"),)

    membership_id: Mapped[UUID] = mapped_column(ForeignKey("memberships.id", ondelete="CASCADE"))
    extension: Mapped[str | None] = mapped_column(String(32))
    max_concurrent_calls: Mapped[int] = mapped_column(Integer, default=1)


class OperatorStatus(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "operator_statuses"
    __table_args__ = (UniqueConstraint("tenant_id", "human_operator_id"),)

    human_operator_id: Mapped[UUID] = mapped_column(ForeignKey("human_operators.id", ondelete="CASCADE"))
    status: Mapped[QueueStatus] = mapped_column(enum_type(QueueStatus), default=QueueStatus.OFFLINE)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OperatorQueue(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "operator_queues"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    strategy: Mapped[str] = mapped_column(String(40), default="rrmemory")
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=30)
    callback_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class QueueMember(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "queue_members"
    __table_args__ = (UniqueConstraint("queue_id", "human_operator_id"),)

    queue_id: Mapped[UUID] = mapped_column(ForeignKey("operator_queues.id", ondelete="CASCADE"))
    human_operator_id: Mapped[UUID] = mapped_column(ForeignKey("human_operators.id", ondelete="CASCADE"))
    priority: Mapped[int] = mapped_column(Integer, default=0)


class TransferRequest(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "transfer_requests"

    call_id: Mapped[UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"), index=True)
    queue_id: Mapped[UUID | None] = mapped_column(ForeignKey("operator_queues.id", ondelete="SET NULL"))
    assigned_operator_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("human_operators.id", ondelete="SET NULL")
    )
    status: Mapped[TransferStatus] = mapped_column(
        enum_type(TransferStatus), default=TransferStatus.REQUESTED
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Integration(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "integrations"

    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[IntegrationStatus] = mapped_column(
        enum_type(IntegrationStatus), default=IntegrationStatus.UNAVAILABLE
    )
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IntegrationCredentialReference(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "integration_credential_references"

    integration_id: Mapped[UUID] = mapped_column(
        ForeignKey("integrations.id", ondelete="CASCADE"), unique=True
    )
    secret_provider: Mapped[str] = mapped_column(String(40), default="local_encrypted")
    secret_key: Mapped[str] = mapped_column(String(255), nullable=False)


class CrmFieldMapping(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "crm_field_mappings"
    __table_args__ = (UniqueConstraint("integration_id", "local_field"),)

    integration_id: Mapped[UUID] = mapped_column(ForeignKey("integrations.id", ondelete="CASCADE"))
    local_field: Mapped[str] = mapped_column(String(120), nullable=False)
    remote_field: Mapped[str] = mapped_column(String(120), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), default="both")


class WebhookEndpoint(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "webhook_endpoints"

    integration_id: Mapped[UUID | None] = mapped_column(ForeignKey("integrations.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    signing_secret_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    event_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)


class WebhookDelivery(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (UniqueConstraint("tenant_id", "provider_delivery_id"),)

    endpoint_id: Mapped[UUID | None] = mapped_column(ForeignKey("webhook_endpoints.id", ondelete="SET NULL"))
    provider_delivery_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[DeliveryStatus] = mapped_column(enum_type(DeliveryStatus), default=DeliveryStatus.PENDING)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    safe_error_code: Mapped[str | None] = mapped_column(String(80))


class ToolDefinition(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "tool_definitions"
    __table_args__ = (UniqueConstraint("tenant_id", "name", "version"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    input_schema: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    output_schema: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    timeout_ms: Mapped[int] = mapped_column(Integer, default=3000)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)


class ToolPermission(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "tool_permissions"
    __table_args__ = (UniqueConstraint("ai_operator_version_id", "tool_definition_id"),)

    ai_operator_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_operator_versions.id", ondelete="CASCADE")
    )
    tool_definition_id: Mapped[UUID] = mapped_column(ForeignKey("tool_definitions.id", ondelete="CASCADE"))
    allowed: Mapped[bool] = mapped_column(Boolean, default=False)


class ToolExecution(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "tool_executions"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)

    call_id: Mapped[UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"), index=True)
    tool_definition_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tool_definitions.id", ondelete="SET NULL")
    )
    tool_name: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    arguments_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    safe_arguments: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    safe_result: Mapped[dict[str, object] | None] = mapped_column(JSON)
    status: Mapped[ToolExecutionStatus] = mapped_column(
        enum_type(ToolExecutionStatus), default=ToolExecutionStatus.PENDING
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(80))


class UsageRecord(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "usage_records"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)

    call_id: Mapped[UUID | None] = mapped_column(ForeignKey("calls.id", ondelete="SET NULL"), index=True)
    metric: Mapped[str] = mapped_column(String(80), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    unit: Mapped[str] = mapped_column(String(24), nullable=False)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), default=Decimal("0"))
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UsageLimit(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "usage_limits"
    __table_args__ = (UniqueConstraint("tenant_id", "metric"),)

    metric: Mapped[str] = mapped_column(String(80), nullable=False)
    hard_limit: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    alert_threshold_percent: Mapped[int] = mapped_column(Integer, default=80)


class Plan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "plans"

    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    monthly_price_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    annual_price_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    included_ai_minutes: Mapped[int | None] = mapped_column(Integer)
    overage_per_minute_usd: Mapped[Decimal] = mapped_column(Numeric(8, 4), default=Decimal("0.08"))
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False)


class Subscription(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "subscriptions"

    plan_id: Mapped[UUID] = mapped_column(ForeignKey("plans.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(32), default="trial")
    provider: Mapped[str] = mapped_column(String(40), default="development_fake")
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Invoice(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "invoices"

    subscription_id: Mapped[UUID] = mapped_column(ForeignKey("subscriptions.id", ondelete="RESTRICT"))
    number: Mapped[str] = mapped_column(String(80), nullable=False)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Payment(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "payments"

    invoice_id: Mapped[UUID] = mapped_column(ForeignKey("invoices.id", ondelete="RESTRICT"))
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_reference: Mapped[str | None] = mapped_column(String(160))
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)


class Notification(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "notifications"

    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    channel: Mapped[str] = mapped_column(String(24), nullable=False)
    template_key: Mapped[str] = mapped_column(String(120), nullable=False)
    safe_payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    status: Mapped[DeliveryStatus] = mapped_column(enum_type(DeliveryStatus), default=DeliveryStatus.PENDING)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "audit_logs"

    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    action: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(120), nullable=False)
    resource_id: Mapped[UUID | None] = mapped_column()
    reason: Mapped[str | None] = mapped_column(String(500))
    correlation_id: Mapped[str] = mapped_column(String(80), nullable=False)
    safe_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)


class SystemIncident(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "system_incidents"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    severity: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    safe_summary: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FeatureFlag(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "feature_flags"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)

    key: Mapped[str] = mapped_column(String(120), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
