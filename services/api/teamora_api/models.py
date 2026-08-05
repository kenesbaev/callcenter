from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
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
    CallDirection,
    CallerType,
    CallResultCategory,
    CallStatus,
    DeliveryStatus,
    HangupCause,
    IntegrationStatus,
    InvitationStatus,
    LanguageCode,
    LanguageReadiness,
    OperatorVersionStatus,
    QueueStatus,
    RoleName,
    TaskEventType,
    TaskPriority,
    TaskSource,
    TaskStatus,
    TaskType,
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
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id"),
        UniqueConstraint("tenant_id", "id", name="uq_memberships_tenant_id_id"),
        CheckConstraint("state_version >= 1", name="state_version_positive"),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[RoleName] = mapped_column(enum_type(RoleName), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32))
    job_title: Mapped[str | None] = mapped_column(String(120))
    interface_language: Mapped[str] = mapped_column(String(32), default="ru", nullable=False)
    timezone: Mapped[str | None] = mapped_column(String(64))
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    presence_last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    blocked_reason: Mapped[str | None] = mapped_column(String(500))
    state_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class Project(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("max_concurrent_calls > 0", name="max_concurrent_calls_positive"),
        CheckConstraint("max_attempts > 0", name="max_attempts_positive"),
        CheckConstraint(
            "status IN ('active', 'paused', 'archived')",
            name="status_valid",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "outbound_phone_number_id"],
            ["phone_numbers.tenant_id", "phone_numbers.id"],
            name="fk_projects_tenant_outbound_phone_number_phone_numbers",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["tenant_id", "id", "ai_operator_id"],
            ["ai_operators.tenant_id", "ai_operators.project_id", "ai_operators.id"],
            name="fk_projects_tenant_project_ai_operator_ai_operators",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["tenant_id", "knowledge_source_id"],
            ["knowledge_sources.tenant_id", "knowledge_sources.id"],
            name="fk_projects_tenant_knowledge_source_knowledge_sources",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["tenant_id", "id", "call_flow_id"],
            ["call_flows.tenant_id", "call_flows.project_id", "call_flows.id"],
            name="fk_projects_tenant_project_call_flow_call_flows",
            ondelete="RESTRICT",
            use_alter=True,
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
    default_language: Mapped[LanguageCode | None] = mapped_column(enum_type(LanguageCode), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64))
    outbound_number: Mapped[str | None] = mapped_column(String(32))
    outbound_phone_number_id: Mapped[UUID | None] = mapped_column(index=True)
    ai_operator_id: Mapped[UUID | None] = mapped_column(index=True)
    knowledge_source_id: Mapped[UUID | None] = mapped_column(index=True)
    call_flow_id: Mapped[UUID | None] = mapped_column(index=True)
    max_concurrent_calls: Mapped[int | None] = mapped_column(Integer, default=None)
    working_hours: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    recording_enabled: Mapped[bool | None] = mapped_column(Boolean)
    recording_disclosure_required: Mapped[bool | None] = mapped_column(Boolean)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    retry_intervals_minutes: Mapped[list[int]] = mapped_column(JSON, default=lambda: [15, 60], nullable=False)
    callback_rules: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "user_id",
            name="uq_project_users_tenant_project_user",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(index=True)
    user_id: Mapped[UUID] = mapped_column(index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ProjectInboundPhoneNumber(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "project_inbound_phone_numbers"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_project_inbound_numbers_tenant_project_projects",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "phone_number_id"],
            ["phone_numbers.tenant_id", "phone_numbers.id"],
            name="fk_project_inbound_numbers_tenant_phone_phone_numbers",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "phone_number_id",
            name="uq_project_inbound_numbers_project_phone",
        ),
        UniqueConstraint(
            "tenant_id",
            "phone_number_id",
            name="uq_project_inbound_numbers_tenant_phone",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(index=True)
    phone_number_id: Mapped[UUID] = mapped_column(index=True)


class CallResultCatalog(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "call_result_catalogs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_call_result_catalogs_tenant_project_projects",
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "project_id", name="uq_call_result_catalogs_tenant_project"),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "id",
            name="uq_call_result_catalogs_tenant_project_id",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(160), default="Результаты звонка", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class CallResultDefinition(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "call_result_definitions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "catalog_id"],
            [
                "call_result_catalogs.tenant_id",
                "call_result_catalogs.project_id",
                "call_result_catalogs.id",
            ],
            name="fk_call_result_definitions_tenant_project_catalog",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_call_result_definitions_tenant_project_projects",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "system_code",
            name="uq_call_result_definitions_tenant_project_code",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "id",
            name="uq_call_result_definitions_tenant_project_id",
        ),
        CheckConstraint(
            "category IN ('successful', 'intermediate', 'unreachable', 'unsuccessful')",
            name="category_valid",
        ),
        CheckConstraint("system_code ~ '^[a-z][a-z0-9_]{1,79}$'", name="system_code_valid"),
        CheckConstraint("color ~ '^#[0-9A-Fa-f]{6}$'", name="color_valid"),
        CheckConstraint("sort_order >= 0", name="sort_order_non_negative"),
        CheckConstraint(
            "NOT requires_callback_at OR requires_callback",
            name="callback_date_requires_callback",
        ),
        Index(
            "ix_call_result_definitions_project_active_order",
            "tenant_id",
            "project_id",
            "is_active",
            "sort_order",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(index=True)
    catalog_id: Mapped[UUID] = mapped_column(index=True)
    system_code: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[CallResultCategory] = mapped_column(enum_type(CallResultCategory), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    name_translations: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)
    description: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    color: Mapped[str] = mapped_column(String(7), default="#64748B", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    requires_comment: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    requires_callback: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    requires_callback_at: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    creates_task: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    next_customer_status: Mapped[str | None] = mapped_column(String(32))
    return_to_queue: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    completes_customer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    do_not_call: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    counts_as_success: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


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

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_invitations_tenant_id_id"),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'cancelled', 'expired')",
            name="status_valid",
        ),
        CheckConstraint("state_version >= 1", name="state_version_positive"),
        ForeignKeyConstraint(
            ["tenant_id", "invited_by_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_invitations_tenant_invited_by_membership",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "accepted_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_invitations_tenant_accepted_membership",
            ondelete="SET NULL",
        ),
        Index(
            "uq_invitations_active_email",
            "tenant_id",
            "email",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[RoleName] = mapped_column(enum_type(RoleName), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    invited_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    invited_by_membership_id: Mapped[UUID | None] = mapped_column(index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[InvitationStatus] = mapped_column(
        enum_type(InvitationStatus), default=InvitationStatus.PENDING, nullable=False, index=True
    )
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    accepted_membership_id: Mapped[UUID | None] = mapped_column(index=True)
    state_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class InvitationProject(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "invitation_projects"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "invitation_id"],
            ["invitations.tenant_id", "invitations.id"],
            name="fk_invitation_projects_tenant_invitation",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_invitation_projects_tenant_project",
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "invitation_id", "project_id", name="uq_invitation_projects_scope"),
    )

    invitation_id: Mapped[UUID] = mapped_column(index=True)
    project_id: Mapped[UUID] = mapped_column(index=True)


class TeamCommandSubmission(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "team_command_submissions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_team_commands_tenant_idempotency_key"),
    )

    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[UUID | None] = mapped_column()
    response_payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)


class SipTrunk(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "sip_trunks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_sip_trunks_tenant_id_id"),
        CheckConstraint(
            "auth_mode IN ('registration', 'ip')",
            name="sip_trunk_auth_mode_valid",
        ),
        CheckConstraint(
            "dtmf_mode IN ('auto', 'rfc4733', 'inband', 'info')",
            name="sip_trunk_dtmf_mode_valid",
        ),
        CheckConstraint(
            "channel_pool_mode IN ('shared', 'separate')",
            name="sip_trunk_channel_pool_mode_valid",
        ),
        CheckConstraint("max_channels > 0", name="sip_trunk_max_channels_positive"),
        CheckConstraint(
            "inbound_channel_limit IS NULL OR inbound_channel_limit > 0",
            name="sip_trunk_inbound_limit_positive",
        ),
        CheckConstraint(
            "outbound_channel_limit IS NULL OR outbound_channel_limit > 0",
            name="sip_trunk_outbound_limit_positive",
        ),
        CheckConstraint("lock_version >= 1", name="sip_trunk_lock_version_positive"),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_host: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_port: Mapped[int] = mapped_column(Integer, default=5061)
    transport: Mapped[str] = mapped_column(String(16), default="tls")
    allowed_ips: Mapped[list[str]] = mapped_column(JSON, default=list)
    auth_mode: Mapped[str] = mapped_column(String(16), default="registration", nullable=False)
    codecs: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    dtmf_mode: Mapped[str] = mapped_column(String(16), default="auto", nullable=False)
    max_channels: Mapped[int] = mapped_column(Integer, default=5)
    channel_pool_mode: Mapped[str] = mapped_column(String(16), default="shared", nullable=False)
    inbound_channel_limit: Mapped[int | None] = mapped_column(Integer)
    outbound_channel_limit: Mapped[int | None] = mapped_column(Integer)
    registration_status: Mapped[str] = mapped_column(String(32), default="not_configured", nullable=False)
    reachability_status: Mapped[str] = mapped_column(String(32), default="not_configured", nullable=False)
    last_status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_safe_error: Mapped[str | None] = mapped_column(String(240))
    lock_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
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
    __table_args__ = (
        UniqueConstraint("e164"),
        UniqueConstraint("tenant_id", "id", name="uq_phone_numbers_tenant_id_id"),
    )

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
        UniqueConstraint("tenant_id", "project_id", "id", name="uq_ai_operators_tenant_project_id"),
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
    description: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    default_language_code: Mapped[str] = mapped_column(String(32), default="ru", nullable=False)
    language_codes: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["ru"], nullable=False)
    active_version_id: Mapped[UUID | None] = mapped_column(index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_call_flows_tenant_project_projects",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "id", "active_version_id"],
            [
                "call_flow_versions.tenant_id",
                "call_flow_versions.project_id",
                "call_flow_versions.call_flow_id",
                "call_flow_versions.id",
            ],
            name="fk_call_flows_active_version_same_flow",
            ondelete="SET NULL (active_version_id)",
            use_alter=True,
        ),
        UniqueConstraint("tenant_id", "project_id", "id", name="uq_call_flows_tenant_project_id"),
        Index(
            "ix_call_flows_tenant_project_archived",
            "tenant_id",
            "project_id",
            "archived_at",
        ),
    )


class CallFlowVersion(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "call_flow_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "call_flow_id"],
            ["call_flows.tenant_id", "call_flows.project_id", "call_flows.id"],
            name="fk_call_flow_versions_tenant_project_flow",
            ondelete="CASCADE",
        ),
        UniqueConstraint("call_flow_id", "version"),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "id",
            name="uq_call_flow_versions_tenant_project_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "call_flow_id",
            "id",
            name="uq_call_flow_versions_tenant_project_flow_id",
        ),
        CheckConstraint("lock_version >= 1", name="lock_version_positive"),
        Index(
            "uq_call_flow_versions_single_draft",
            "tenant_id",
            "call_flow_id",
            unique=True,
            postgresql_where=text("status = 'draft'"),
        ),
        Index(
            "ix_call_flow_versions_flow_status_version",
            "tenant_id",
            "call_flow_id",
            "status",
            "version",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(index=True)
    call_flow_id: Mapped[UUID] = mapped_column(index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[OperatorVersionStatus] = mapped_column(
        enum_type(OperatorVersionStatus), default=OperatorVersionStatus.DRAFT
    )
    definition: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_from_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "call_flow_versions.id",
            ondelete="SET NULL",
            name="fk_call_flow_versions_created_from_version",
        ),
        index=True,
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BackgroundJob(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "background_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_background_jobs_tenant_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_background_jobs_tenant_creator",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_background_jobs_tenant_id_id"),
        CheckConstraint(
            "status IN ('pending','scheduled','running','retry_wait','completed','failed',"
            "'dead_letter','cancel_requested','cancelled')",
            name="background_job_status_valid",
        ),
        CheckConstraint("priority >= 0 AND priority <= 100", name="background_job_priority_valid"),
        CheckConstraint("attempt_count >= 0", name="background_job_attempt_count_non_negative"),
        CheckConstraint("max_attempts > 0", name="background_job_max_attempts_positive"),
        CheckConstraint("progress >= 0 AND progress <= 100", name="background_job_progress_valid"),
        CheckConstraint("lock_version >= 1", name="background_job_lock_version_positive"),
        Index(
            "uq_background_jobs_tenant_type_idempotency",
            "tenant_id",
            "type",
            "idempotency_key",
            unique=True,
        ),
        Index(
            "ix_background_jobs_claim",
            "queue",
            "status",
            "available_at",
            "priority",
            "created_at",
        ),
        Index("ix_background_jobs_tenant_status_type", "tenant_id", "status", "type"),
        Index("ix_background_jobs_lease_expiry", "status", "lease_expires_at"),
        Index("ix_background_jobs_creator", "tenant_id", "created_by_user_id", "created_at"),
    )

    project_id: Mapped[UUID | None] = mapped_column(index=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(index=True)
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    queue: Mapped[str] = mapped_column(String(40), default="default", nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    safe_payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(160), index=True)
    lease_token: Mapped[UUID | None] = mapped_column(index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(160), nullable=False)
    causation_id: Mapped[UUID | None] = mapped_column(index=True)
    result_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    safe_error_code: Mapped[str | None] = mapped_column(String(80))
    safe_error_message: Mapped[str | None] = mapped_column(String(500))
    lock_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class BackgroundJobAttempt(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "background_job_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_background_job_attempts_tenant_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["background_jobs.tenant_id", "background_jobs.id"],
            name="fk_background_job_attempts_tenant_job",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "job_id",
            "attempt_number",
            name="uq_background_job_attempts_job_number",
        ),
        CheckConstraint("attempt_number > 0", name="background_job_attempt_number_positive"),
        CheckConstraint(
            "status IN ('running','completed','failed','cancelled','lease_lost')",
            name="background_job_attempt_status_valid",
        ),
        Index("ix_background_job_attempts_job_started", "tenant_id", "job_id", "started_at"),
    )

    project_id: Mapped[UUID | None] = mapped_column(index=True)
    job_id: Mapped[UUID] = mapped_column(index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[str] = mapped_column(String(160), nullable=False)
    lease_token: Mapped[UUID] = mapped_column(nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), default="running", nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    safe_error_code: Mapped[str | None] = mapped_column(String(80))
    safe_error_message: Mapped[str | None] = mapped_column(String(500))
    result_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)


class BackgroundJobEvent(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "background_job_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_background_job_events_tenant_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["background_jobs.tenant_id", "background_jobs.id"],
            name="fk_background_job_events_tenant_job",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_background_job_events_tenant_actor",
            ondelete="RESTRICT",
        ),
        Index("ix_background_job_events_job_occurred", "tenant_id", "job_id", "occurred_at"),
        Index("ix_background_job_events_type_occurred", "tenant_id", "event_type", "occurred_at"),
    )

    project_id: Mapped[UUID | None] = mapped_column(index=True)
    job_id: Mapped[UUID] = mapped_column(index=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    safe_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(index=True)
    correlation_id: Mapped[str] = mapped_column(String(160), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )


class ScheduledJob(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "scheduled_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_scheduled_jobs_tenant_project",
            ondelete="CASCADE",
        ),
        CheckConstraint("priority >= 0 AND priority <= 100", name="scheduled_job_priority_valid"),
        CheckConstraint("interval_seconds > 0", name="scheduled_job_interval_positive"),
        CheckConstraint("lock_version >= 1", name="scheduled_job_lock_version_positive"),
        Index(
            "uq_scheduled_jobs_tenant_global_key",
            "tenant_id",
            "schedule_key",
            unique=True,
            postgresql_where=text("project_id IS NULL"),
        ),
        Index(
            "uq_scheduled_jobs_tenant_project_key",
            "tenant_id",
            "project_id",
            "schedule_key",
            unique=True,
            postgresql_where=text("project_id IS NOT NULL"),
        ),
        Index("ix_scheduled_jobs_due", "enabled", "next_run_at"),
    )

    project_id: Mapped[UUID | None] = mapped_column(index=True)
    job_type: Mapped[str] = mapped_column(String(80), nullable=False)
    queue: Mapped[str] = mapped_column(String(40), default="system", nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    safe_payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    schedule_key: Mapped[str] = mapped_column(String(160), nullable=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    last_enqueued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class JobCommandSubmission(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "job_command_submissions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_job_command_submissions_tenant_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "job_id"],
            ["background_jobs.tenant_id", "background_jobs.id"],
            name="fk_job_command_submissions_tenant_job",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "submitted_by_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_job_command_submissions_tenant_submitter",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_job_command_submissions_tenant_idempotency"
        ),
        CheckConstraint(
            "status IN ('accepted','completed','rejected')",
            name="job_command_submission_status_valid",
        ),
        Index("ix_job_command_submissions_job_created", "tenant_id", "job_id", "created_at"),
    )

    project_id: Mapped[UUID | None] = mapped_column(index=True)
    job_id: Mapped[UUID] = mapped_column(index=True)
    submitted_by_user_id: Mapped[UUID | None] = mapped_column(index=True)
    command: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="accepted", nullable=False)
    response_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)


class StorageObject(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "storage_objects"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_storage_objects_tenant_project",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_storage_objects_tenant_id_id"),
        UniqueConstraint("bucket", "object_key", name="uq_storage_objects_bucket_object_key"),
        CheckConstraint(
            "category IN ('knowledge_original','import_source','import_report','temporary_preview',"
            "'call_recording','generated_report','other')",
            name="storage_object_category_valid",
        ),
        CheckConstraint(
            "status IN ('uploading','active','archived','missing','orphan_candidate',"
            "'pending_purge','purged')",
            name="storage_object_status_valid",
        ),
        CheckConstraint(
            "retention_state IN ('retained','eligible','pending_purge','purged')",
            name="storage_object_retention_state_valid",
        ),
        CheckConstraint("size_bytes >= 0", name="storage_object_size_non_negative"),
        CheckConstraint("lock_version >= 1", name="storage_object_lock_version_positive"),
        Index(
            "ix_storage_objects_owner",
            "tenant_id",
            "owner_aggregate_type",
            "owner_aggregate_id",
        ),
        Index("ix_storage_objects_consistency", "status", "last_verified_at", "missing_at"),
        Index(
            "ix_storage_objects_retention",
            "tenant_id",
            "retention_state",
            "pending_purge_at",
        ),
        Index("ix_storage_objects_expiry", "expires_at", "status"),
    )

    project_id: Mapped[UUID | None] = mapped_column(index=True)
    bucket: Mapped[str] = mapped_column(String(63), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    owner_aggregate_type: Mapped[str | None] = mapped_column(String(80))
    owner_aggregate_id: Mapped[UUID | None] = mapped_column(index=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    content_type: Mapped[str] = mapped_column(String(160), default="application/octet-stream", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="uploading", nullable=False)
    retention_state: Mapped[str] = mapped_column(String(24), default="retained", nullable=False)
    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    missing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    pending_purge_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lock_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class StorageConsistencyIssue(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "storage_consistency_issues"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_storage_consistency_issues_tenant_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "storage_object_id"],
            ["storage_objects.tenant_id", "storage_objects.id"],
            name="fk_storage_consistency_issues_tenant_object",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "issue_type IN ('missing_object','orphan_object','checksum_mismatch','size_mismatch',"
            "'incomplete_multipart','expired_temporary')",
            name="storage_consistency_issue_type_valid",
        ),
        CheckConstraint(
            "status IN ('open','confirmed','resolved','ignored')",
            name="storage_consistency_issue_status_valid",
        ),
        CheckConstraint("lock_version >= 1", name="storage_consistency_issue_lock_version_positive"),
        Index(
            "uq_storage_consistency_issues_active",
            "tenant_id",
            "issue_type",
            "bucket",
            "object_key",
            unique=True,
            postgresql_where=text("status IN ('open','confirmed')"),
        ),
        Index("ix_storage_consistency_issues_status_grace", "status", "grace_until"),
        Index("ix_storage_consistency_issues_object", "tenant_id", "storage_object_id"),
    )

    project_id: Mapped[UUID | None] = mapped_column(index=True)
    storage_object_id: Mapped[UUID | None] = mapped_column(index=True)
    issue_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="open", nullable=False)
    bucket: Mapped[str] = mapped_column(String(63), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    safe_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    first_detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    last_detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grace_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lock_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class RetentionPolicy(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "retention_policies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_retention_policies_tenant_project",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "updated_by_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_retention_policies_tenant_updater",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_retention_policies_tenant_id_id"),
        CheckConstraint("policy_version >= 1", name="retention_policy_version_positive"),
        CheckConstraint("grace_period_days > 0", name="retention_policy_grace_positive"),
        CheckConstraint(
            "recording_days IS NULL OR recording_days > 0",
            name="retention_policy_recording_days_positive",
        ),
        CheckConstraint(
            "transcript_days IS NULL OR transcript_days > 0",
            name="retention_policy_transcript_days_positive",
        ),
        CheckConstraint(
            "temporary_import_days IS NULL OR temporary_import_days > 0",
            name="retention_policy_temporary_import_days_positive",
        ),
        CheckConstraint(
            "import_report_days IS NULL OR import_report_days > 0",
            name="retention_policy_import_report_days_positive",
        ),
        CheckConstraint(
            "archived_knowledge_days IS NULL OR archived_knowledge_days > 0",
            name="retention_policy_archived_knowledge_days_positive",
        ),
        CheckConstraint(
            "realtime_event_days IS NULL OR realtime_event_days > 0",
            name="retention_policy_realtime_event_days_positive",
        ),
        CheckConstraint(
            "completed_job_days IS NULL OR completed_job_days > 0",
            name="retention_policy_completed_job_days_positive",
        ),
        CheckConstraint(
            "failed_job_days IS NULL OR failed_job_days > 0",
            name="retention_policy_failed_job_days_positive",
        ),
        Index(
            "uq_retention_policies_tenant_default",
            "tenant_id",
            unique=True,
            postgresql_where=text("project_id IS NULL"),
        ),
        Index(
            "uq_retention_policies_tenant_project",
            "tenant_id",
            "project_id",
            unique=True,
            postgresql_where=text("project_id IS NOT NULL"),
        ),
    )

    project_id: Mapped[UUID | None] = mapped_column(index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    grace_period_days: Mapped[int] = mapped_column(Integer, default=7, nullable=False)
    recording_days: Mapped[int | None] = mapped_column(Integer)
    transcript_days: Mapped[int | None] = mapped_column(Integer)
    temporary_import_days: Mapped[int | None] = mapped_column(Integer)
    import_report_days: Mapped[int | None] = mapped_column(Integer)
    archived_knowledge_days: Mapped[int | None] = mapped_column(Integer)
    realtime_event_days: Mapped[int | None] = mapped_column(Integer)
    completed_job_days: Mapped[int | None] = mapped_column(Integer)
    failed_job_days: Mapped[int | None] = mapped_column(Integer)
    updated_by_user_id: Mapped[UUID | None] = mapped_column(index=True)


class RetentionCandidate(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "retention_candidates"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_retention_candidates_tenant_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            ["retention_policies.tenant_id", "retention_policies.id"],
            name="fk_retention_candidates_tenant_policy",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "storage_object_id"],
            ["storage_objects.tenant_id", "storage_objects.id"],
            name="fk_retention_candidates_tenant_object",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_retention_candidates_tenant_idempotency"),
        CheckConstraint(
            "status IN ('eligible','pending_purge','blocked','purged','cancelled')",
            name="retention_candidate_status_valid",
        ),
        CheckConstraint("policy_version >= 1", name="retention_candidate_policy_version_positive"),
        CheckConstraint("lock_version >= 1", name="retention_candidate_lock_version_positive"),
        Index("ix_retention_candidates_due", "status", "grace_until"),
        Index(
            "ix_retention_candidates_resource",
            "tenant_id",
            "resource_type",
            "resource_id",
        ),
    )

    project_id: Mapped[UUID | None] = mapped_column(index=True)
    policy_id: Mapped[UUID] = mapped_column(index=True)
    storage_object_id: Mapped[UUID | None] = mapped_column(index=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[UUID] = mapped_column(index=True)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="eligible", nullable=False)
    eligible_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    pending_purge_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grace_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(String(500))
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    safe_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class LegalHold(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "legal_holds"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_legal_holds_tenant_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_legal_holds_tenant_creator",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "released_by_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_legal_holds_tenant_releaser",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "scope_type IN ('tenant','project','call','customer','document')",
            name="legal_hold_scope_type_valid",
        ),
        CheckConstraint(
            "(scope_type = 'tenant' AND scope_id IS NULL) OR "
            "(scope_type <> 'tenant' AND scope_id IS NOT NULL)",
            name="legal_hold_scope_id_valid",
        ),
        CheckConstraint("lock_version >= 1", name="legal_hold_lock_version_positive"),
        Index(
            "uq_legal_holds_active_tenant_scope",
            "tenant_id",
            unique=True,
            postgresql_where=text("released_at IS NULL AND scope_type = 'tenant'"),
        ),
        Index(
            "uq_legal_holds_active_resource_scope",
            "tenant_id",
            "scope_type",
            "scope_id",
            unique=True,
            postgresql_where=text("released_at IS NULL AND scope_type <> 'tenant'"),
        ),
        Index("ix_legal_holds_project_active", "tenant_id", "project_id", "released_at"),
    )

    project_id: Mapped[UUID | None] = mapped_column(index=True)
    scope_type: Mapped[str] = mapped_column(String(24), nullable=False)
    scope_id: Mapped[UUID | None] = mapped_column(index=True)
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(index=True)
    released_by_user_id: Mapped[UUID | None] = mapped_column(index=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lock_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class KnowledgeSource(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "knowledge_sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_knowledge_sources_tenant_project",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_knowledge_sources_tenant_id_id"),
        UniqueConstraint("tenant_id", "project_id", "id", name="uq_knowledge_sources_tenant_project_id"),
        Index("ix_knowledge_sources_tenant_project_archived", "tenant_id", "project_id", "archived_at"),
        CheckConstraint("lock_version >= 1", name="knowledge_source_lock_version_positive"),
    )

    project_id: Mapped[UUID | None] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), default="text")
    status: Mapped[IntegrationStatus] = mapped_column(
        enum_type(IntegrationStatus), default=IntegrationStatus.CONFIGURED
    )
    default_language_code: Mapped[str] = mapped_column(String(32), default="ru", nullable=False)
    active_revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "knowledge_base_revisions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_knowledge_sources_active_revision",
        ),
        index=True,
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lock_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class KnowledgeBaseRevision(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "knowledge_base_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "knowledge_source_id"],
            ["knowledge_sources.tenant_id", "knowledge_sources.project_id", "knowledge_sources.id"],
            name="fk_kb_revisions_tenant_project_source",
            ondelete="CASCADE",
        ),
        UniqueConstraint("knowledge_source_id", "version", name="uq_kb_revisions_source_version"),
        UniqueConstraint("tenant_id", "project_id", "id", name="uq_kb_revisions_tenant_project_id"),
        CheckConstraint("status IN ('draft', 'published', 'archived')", name="kb_revision_status_valid"),
        CheckConstraint("lock_version >= 1", name="kb_revision_lock_version_positive"),
        CheckConstraint("embedding_dimension > 0", name="kb_revision_embedding_dimension_positive"),
        Index(
            "uq_kb_revisions_single_draft",
            "tenant_id",
            "knowledge_source_id",
            unique=True,
            postgresql_where=text("status = 'draft'"),
        ),
        Index(
            "ix_kb_revisions_source_status_version",
            "tenant_id",
            "knowledge_source_id",
            "status",
            "version",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(index=True)
    knowledge_source_id: Mapped[UUID] = mapped_column(index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    lock_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_from_revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("knowledge_base_revisions.id", ondelete="SET NULL"), index=True
    )
    embedding_provider: Mapped[str] = mapped_column(String(80), default="mock", nullable=False)
    embedding_model: Mapped[str] = mapped_column(
        String(120), default="kline-deterministic-v1", nullable=False
    )
    embedding_dimension: Mapped[int] = mapped_column(Integer, default=64, nullable=False)
    index_version: Mapped[str] = mapped_column(String(120), default="kb-index-v1", nullable=False)
    chunking_config: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeDocument(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "knowledge_documents"

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_knowledge_documents_tenant_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["knowledge_sources.tenant_id", "knowledge_sources.id"],
            name="fk_knowledge_documents_tenant_source",
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "project_id", "id", name="uq_knowledge_documents_tenant_project_id"),
        CheckConstraint("lock_version >= 1", name="knowledge_document_lock_version_positive"),
        Index("ix_knowledge_documents_source_archived", "tenant_id", "source_id", "archived_at"),
    )

    project_id: Mapped[UUID | None] = mapped_column(index=True)
    source_id: Mapped[UUID] = mapped_column(index=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    language: Mapped[LanguageCode] = mapped_column(enum_type(LanguageCode), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lock_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class KnowledgeDocumentVersion(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "knowledge_document_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "revision_id"],
            [
                "knowledge_base_revisions.tenant_id",
                "knowledge_base_revisions.project_id",
                "knowledge_base_revisions.id",
            ],
            name="fk_knowledge_document_versions_tenant_revision",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "document_id"],
            ["knowledge_documents.tenant_id", "knowledge_documents.project_id", "knowledge_documents.id"],
            name="fk_knowledge_document_versions_tenant_document",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "storage_object_id"],
            ["storage_objects.tenant_id", "storage_objects.id"],
            name="fk_knowledge_document_versions_tenant_storage_object",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("document_id", "version", name="uq_knowledge_document_versions_document_version"),
        UniqueConstraint("tenant_id", "project_id", "id", name="uq_kdv_tenant_project_id"),
        CheckConstraint(
            "status IN ('uploaded','queued','extracting','chunking','embedding','ready',"
            "'failed','needs_ocr','archived')",
            name="knowledge_document_version_status_valid",
        ),
        CheckConstraint("lock_version >= 1", name="knowledge_document_version_lock_version_positive"),
        Index("ix_kdv_revision_status", "tenant_id", "revision_id", "status"),
        Index("ix_kdv_checksum_scope", "tenant_id", "knowledge_source_id", "checksum_sha256"),
        Index(
            "uq_kdv_revision_active_document",
            "tenant_id",
            "revision_id",
            "document_id",
            unique=True,
            postgresql_where=text("status <> 'archived'"),
        ),
    )

    project_id: Mapped[UUID] = mapped_column(index=True)
    knowledge_source_id: Mapped[UUID] = mapped_column(index=True)
    revision_id: Mapped[UUID] = mapped_column(index=True)
    document_id: Mapped[UUID] = mapped_column(index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="uploaded", nullable=False)
    title_snapshot: Mapped[str] = mapped_column(String(240), nullable=False)
    language_code: Mapped[str] = mapped_column(String(32), default="ru", nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(12), nullable=False)
    content_type: Mapped[str] = mapped_column(String(160), nullable=False)
    object_key: Mapped[str | None] = mapped_column(String(640))
    storage_object_id: Mapped[UUID | None] = mapped_column(index=True)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_length: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    extracted_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    extraction_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    safe_error_code: Mapped[str | None] = mapped_column(String(80))
    safe_error_message: Mapped[str | None] = mapped_column(String(500))
    lock_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extraction_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    chunking_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    embedding_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeChunk(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "revision_id"],
            [
                "knowledge_base_revisions.tenant_id",
                "knowledge_base_revisions.project_id",
                "knowledge_base_revisions.id",
            ],
            name="fk_knowledge_chunks_tenant_revision",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "document_version_id"],
            [
                "knowledge_document_versions.tenant_id",
                "knowledge_document_versions.project_id",
                "knowledge_document_versions.id",
            ],
            name="fk_knowledge_chunks_tenant_document_version",
            ondelete="CASCADE",
        ),
        Index(
            "ix_knowledge_chunks_retrieval_scope",
            "tenant_id",
            "project_id",
            "revision_id",
            "language_code",
            "embedding_status",
        ),
    )

    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding_ref: Mapped[str | None] = mapped_column(String(255))
    project_id: Mapped[UUID | None] = mapped_column(index=True)
    revision_id: Mapped[UUID | None] = mapped_column(index=True)
    document_version_id: Mapped[UUID | None] = mapped_column(index=True)
    language_code: Mapped[str] = mapped_column(String(32), default="ru", nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(500))
    normalized_content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    content_checksum: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    character_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(VECTOR())
    embedding_status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    embedding_provider: Mapped[str | None] = mapped_column(String(80))
    embedding_model: Mapped[str | None] = mapped_column(String(120))
    embedding_dimension: Mapped[int | None] = mapped_column(Integer)
    index_version: Mapped[str | None] = mapped_column(String(120))


class DocumentIngestionJob(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "document_ingestion_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "document_version_id"],
            [
                "knowledge_document_versions.tenant_id",
                "knowledge_document_versions.project_id",
                "knowledge_document_versions.id",
            ],
            name="fk_document_ingestion_jobs_tenant_version",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "background_job_id"],
            ["background_jobs.tenant_id", "background_jobs.id"],
            name="fk_document_ingestion_jobs_tenant_background_job",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "document_version_id", name="uq_ingestion_job_document_version"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_ingestion_job_idempotency"),
        CheckConstraint(
            "status IN ('pending','processing','succeeded','failed','cancelled')",
            name="ingestion_job_status_valid",
        ),
        CheckConstraint("attempts >= 0", name="ingestion_job_attempts_non_negative"),
        Index("ix_ingestion_jobs_claim", "status", "next_attempt_at", "lease_expires_at"),
    )

    project_id: Mapped[UUID] = mapped_column(index=True)
    document_version_id: Mapped[UUID] = mapped_column(index=True)
    background_job_id: Mapped[UUID | None] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    stage: Mapped[str] = mapped_column(String(24), default="queued", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    lease_token: Mapped[UUID | None] = mapped_column(index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    safe_error_code: Mapped[str | None] = mapped_column(String(80))
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeRetrievalExecution(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "knowledge_retrieval_executions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "revision_id"],
            [
                "knowledge_base_revisions.tenant_id",
                "knowledge_base_revisions.project_id",
                "knowledge_base_revisions.id",
            ],
            name="fk_knowledge_retrieval_tenant_revision",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_knowledge_retrieval_idempotency"),
        Index("ix_knowledge_retrieval_revision_created", "tenant_id", "revision_id", "created_at"),
    )

    project_id: Mapped[UUID] = mapped_column(index=True)
    revision_id: Mapped[UUID] = mapped_column(index=True)
    call_id: Mapped[UUID | None] = mapped_column(ForeignKey("calls.id", ondelete="SET NULL"), index=True)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    masked_query: Mapped[str] = mapped_column(String(1000), nullable=False)
    language_code: Mapped[str] = mapped_column(String(32), nullable=False)
    chunk_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    citations: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list, nullable=False)
    scores: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list, nullable=False)
    usage: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    index_version: Mapped[str] = mapped_column(String(120), nullable=False)
    no_match: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)


class KnowledgeSyncJob(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "knowledge_sync_jobs"

    source_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge_sources.id", ondelete="CASCADE"))
    status: Mapped[DeliveryStatus] = mapped_column(enum_type(DeliveryStatus), default=DeliveryStatus.PENDING)
    error_code: Mapped[str | None] = mapped_column(String(80))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Customer(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "customers"

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_customers_tenant_project_projects",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "assigned_user_id"],
            ["project_users.tenant_id", "project_users.project_id", "project_users.user_id"],
            name="fk_customers_tenant_project_assigned_user_project_users",
            ondelete="SET NULL (assigned_user_id)",
            use_alter=True,
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "id",
            name="uq_customers_tenant_project_id",
        ),
        Index(
            "uq_customers_tenant_project_external_reference",
            "tenant_id",
            "project_id",
            "external_reference_normalized",
            unique=True,
            postgresql_where=text("external_reference_normalized IS NOT NULL"),
        ),
        Index(
            "ix_customers_tenant_project_status_archived",
            "tenant_id",
            "project_id",
            "status",
            "archived_at",
        ),
        Index("ix_customers_tenant_assigned_user", "tenant_id", "assigned_user_id"),
        CheckConstraint(
            "dialer_assignment_source IS NULL OR dialer_assignment_source IN ('callback', 'retry', 'new')",
            name="dialer_assignment_source_valid",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(index=True)

    display_name: Mapped[str | None] = mapped_column(String(160))
    external_reference: Mapped[str | None] = mapped_column(String(160))
    external_reference_normalized: Mapped[str | None] = mapped_column(String(160))
    preferred_language: Mapped[LanguageCode | None] = mapped_column(enum_type(LanguageCode))
    is_anonymized: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), default="new", nullable=False, index=True)
    city: Mapped[str | None] = mapped_column(String(160))
    region: Mapped[str | None] = mapped_column(String(160))
    address: Mapped[str | None] = mapped_column(String(500))
    job_title: Mapped[str | None] = mapped_column(String(160))
    organization: Mapped[str | None] = mapped_column(String(200))
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source: Mapped[str | None] = mapped_column(String(120))
    assigned_user_id: Mapped[UUID | None] = mapped_column(index=True)
    custom_fields: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    locked_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lock_token: Mapped[UUID | None] = mapped_column(index=True)
    dialer_assignment_source: Mapped[str | None] = mapped_column(String(16))
    last_call_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_call_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


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
        CheckConstraint("kind IN ('phone', 'email')", name="kind_valid"),
        Index(
            "uq_customer_contacts_project_kind_value",
            "tenant_id",
            "project_id",
            "kind",
            "normalized_value",
            unique=True,
            postgresql_where=text("kind IN ('phone', 'email')"),
        ),
        Index(
            "uq_customer_contacts_primary_kind",
            "tenant_id",
            "project_id",
            "customer_id",
            "kind",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )

    project_id: Mapped[UUID] = mapped_column(index=True)
    customer_id: Mapped[UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(320), nullable=False)
    display_value: Mapped[str] = mapped_column(String(320), nullable=False)
    label: Mapped[str | None] = mapped_column(String(80))
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)


class CustomerFieldDefinition(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "customer_field_definitions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_customer_field_definitions_tenant_project_projects",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "field_type IN ('text', 'textarea', 'number', 'boolean', 'date', 'datetime', "
            "'select', 'multiselect')",
            name="field_type_valid",
        ),
        CheckConstraint("sort_order >= 0", name="sort_order_non_negative"),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "key",
            name="uq_customer_field_definitions_project_key",
        ),
        Index(
            "ix_customer_field_definitions_project_active_order",
            "tenant_id",
            "project_id",
            "is_active",
            "sort_order",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    field_type: Mapped[str] = mapped_column(String(24), nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    options: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    default_value: Mapped[object | None] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class CustomerImport(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "customer_imports"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_customer_imports_tenant_project_projects",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_customer_imports_tenant_creator_memberships",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "background_job_id"],
            ["background_jobs.tenant_id", "background_jobs.id"],
            name="fk_customer_imports_tenant_background_job",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_storage_object_id"],
            ["storage_objects.tenant_id", "storage_objects.id"],
            name="fk_customer_imports_tenant_source_storage_object",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "report_storage_object_id"],
            ["storage_objects.tenant_id", "storage_objects.id"],
            name="fk_customer_imports_tenant_report_storage_object",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('preview', 'committed', 'expired', 'processing_preview', 'ready', "
            "'queued', 'running', 'finalizing', 'completed', 'failed', "
            "'cancel_requested', 'cancelled')",
            name="status_valid",
        ),
        CheckConstraint(
            "update_rule IN ('skip', 'update')",
            name="update_rule_valid",
        ),
        CheckConstraint(
            "progress IS NULL OR (progress >= 0 AND progress <= 100)",
            name="progress_valid",
        ),
        UniqueConstraint(
            "tenant_id",
            "project_id",
            "id",
            name="uq_customer_imports_tenant_project_id",
        ),
        Index(
            "uq_customer_imports_tenant_idempotency_key",
            "tenant_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_customer_imports_tenant_project_status", "tenant_id", "project_id", "status"),
    )

    project_id: Mapped[UUID] = mapped_column(index=True)
    created_by_user_id: Mapped[UUID] = mapped_column(index=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(8), nullable=False)
    sheet_names: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    selected_sheet: Mapped[str] = mapped_column(String(160), nullable=False)
    source_rows: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    mapping: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)
    update_rule: Mapped[str] = mapped_column(String(16), default="skip", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="preview", nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    report: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(160))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_mode: Mapped[str | None] = mapped_column(String(16))
    background_job_id: Mapped[UUID | None] = mapped_column(index=True)
    source_storage_object_id: Mapped[UUID | None] = mapped_column(index=True)
    report_storage_object_id: Mapped[UUID | None] = mapped_column(index=True)
    progress: Mapped[int | None] = mapped_column(Integer)
    total_rows: Mapped[int | None] = mapped_column(Integer)
    valid_rows: Mapped[int | None] = mapped_column(Integer)
    invalid_rows: Mapped[int | None] = mapped_column(Integer)
    duplicate_rows: Mapped[int | None] = mapped_column(Integer)
    created_count: Mapped[int | None] = mapped_column(Integer)
    updated_count: Mapped[int | None] = mapped_column(Integer)
    skipped_count: Mapped[int | None] = mapped_column(Integer)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mapping_snapshot: Mapped[dict[str, str] | None] = mapped_column(JSON)
    update_policy_snapshot: Mapped[str | None] = mapped_column(String(16))


class CustomerImportStagingRow(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "customer_import_staging_rows"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_customer_import_staging_rows_tenant_project",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "import_id"],
            ["customer_imports.tenant_id", "customer_imports.project_id", "customer_imports.id"],
            name="fk_customer_import_staging_rows_tenant_import",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "target_customer_id"],
            ["customers.tenant_id", "customers.project_id", "customers.id"],
            name="fk_customer_import_staging_rows_tenant_customer",
            ondelete="SET NULL (target_customer_id)",
        ),
        UniqueConstraint(
            "tenant_id",
            "import_id",
            "row_number",
            name="uq_customer_import_staging_rows_import_row",
        ),
        CheckConstraint("row_number > 0", name="customer_import_staging_row_number_positive"),
        CheckConstraint(
            "status IN ('staged','valid','invalid','duplicate','merged','skipped')",
            name="customer_import_staging_status_valid",
        ),
        Index(
            "ix_customer_import_staging_rows_import_status",
            "tenant_id",
            "import_id",
            "status",
            "row_number",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(index=True)
    import_id: Mapped[UUID] = mapped_column(index=True)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="staged", nullable=False)
    normalized_payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    identity_hashes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    safe_errors: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list, nullable=False)
    target_customer_id: Mapped[UUID | None] = mapped_column(index=True)
    result_action: Mapped[str | None] = mapped_column(String(24))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    direction: Mapped[CallDirection] = mapped_column(
        enum_type(CallDirection, length=16), default=CallDirection.INBOUND, nullable=False
    )
    caller_type: Mapped[CallerType] = mapped_column(
        enum_type(CallerType, length=24), default=CallerType.HUMAN_OPERATOR, nullable=False
    )
    phone_number_id: Mapped[UUID | None] = mapped_column(ForeignKey("phone_numbers.id", ondelete="SET NULL"))
    sip_trunk_id: Mapped[UUID | None] = mapped_column(index=True)
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
    call_flow_version_id: Mapped[UUID | None] = mapped_column(index=True)
    knowledge_base_revision_id: Mapped[UUID | None] = mapped_column(index=True)
    language: Mapped[LanguageCode | None] = mapped_column(enum_type(LanguageCode))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ringing_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    held_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[str] = mapped_column(String(40), default="mock", nullable=False)
    provider_state: Mapped[str] = mapped_column(String(40), default="queued", nullable=False)
    recording_state: Mapped[str] = mapped_column(String(40), default="stopped", nullable=False)
    provider_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    last_provider_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    hangup_cause: Mapped[HangupCause | None] = mapped_column(enum_type(HangupCause, length=40), nullable=True)
    raw_provider_cause: Mapped[str | None] = mapped_column(String(160))
    from_number: Mapped[str | None] = mapped_column(String(32))
    to_number: Mapped[str | None] = mapped_column(String(32))
    transfer_reason: Mapped[str | None] = mapped_column(String(500))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_calls_tenant_call_id"),
        CheckConstraint(
            "status IN ('queued', 'initiated', 'ringing', 'active', 'on_hold', "
            "'transfer_requested', 'transferring', 'transferred', 'completed', "
            "'busy', 'no_answer', 'failed', 'cancelled')",
            name="call_status_valid",
        ),
        CheckConstraint(
            "direction IN ('inbound', 'outbound')",
            name="call_direction_valid",
        ),
        CheckConstraint(
            "caller_type IN ('human_operator', 'ai_agent')",
            name="call_caller_type_valid",
        ),
        CheckConstraint("state_version >= 0", name="call_state_version_non_negative"),
        Index(
            "ix_calls_tenant_status_last_event",
            "tenant_id",
            "status",
            "last_provider_event_at",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_calls_tenant_project_projects",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "sip_trunk_id"],
            ["sip_trunks.tenant_id", "sip_trunks.id"],
            name="fk_calls_tenant_sip_trunk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "call_flow_version_id"],
            [
                "call_flow_versions.tenant_id",
                "call_flow_versions.project_id",
                "call_flow_versions.id",
            ],
            name="fk_calls_tenant_project_call_flow_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "knowledge_base_revision_id"],
            [
                "knowledge_base_revisions.tenant_id",
                "knowledge_base_revisions.project_id",
                "knowledge_base_revisions.id",
            ],
            name="fk_calls_knowledge_base_revision",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "external_call_id"),
        Index("ix_calls_tenant_started", "tenant_id", "started_at"),
        Index("ix_calls_tenant_project_started", "tenant_id", "project_id", "started_at"),
        Index("ix_calls_tenant_operator_started", "tenant_id", "operator_user_id", "started_at"),
        Index("ix_calls_project_status", "project_id", "status"),
    )


class CallFlowExecution(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "call_flow_executions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "call_id"],
            ["calls.tenant_id", "calls.id"],
            name="fk_call_flow_executions_tenant_call",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "call_flow_version_id"],
            [
                "call_flow_versions.tenant_id",
                "call_flow_versions.project_id",
                "call_flow_versions.id",
            ],
            name="fk_call_flow_executions_tenant_project_version",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "call_id", name="uq_call_flow_executions_tenant_call"),
        UniqueConstraint("tenant_id", "id", name="uq_call_flow_executions_tenant_id"),
        CheckConstraint(
            "status IN ('active', 'completed', 'cancelled')",
            name="status_valid",
        ),
        CheckConstraint("state_version >= 1", name="state_version_positive"),
        Index(
            "ix_call_flow_executions_operator_status_updated",
            "tenant_id",
            "operator_user_id",
            "status",
            "updated_at",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(index=True)
    call_id: Mapped[UUID] = mapped_column(index=True)
    customer_id: Mapped[UUID] = mapped_column(ForeignKey("customers.id", ondelete="RESTRICT"), index=True)
    operator_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    call_flow_version_id: Mapped[UUID] = mapped_column(index=True)
    current_node_id: Mapped[UUID | None] = mapped_column(index=True)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False, index=True)
    language_code: Mapped[str] = mapped_column(String(32), default="ru", nullable=False)
    state_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    values: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CallFlowExecutionStep(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "call_flow_execution_steps"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "execution_id"],
            ["call_flow_executions.tenant_id", "call_flow_executions.id"],
            name="fk_call_flow_execution_steps_tenant_execution",
            ondelete="CASCADE",
        ),
        UniqueConstraint("execution_id", "sequence", name="uq_call_flow_execution_steps_sequence"),
        UniqueConstraint(
            "tenant_id",
            "execution_id",
            "idempotency_key",
            name="uq_call_flow_execution_steps_idempotency",
        ),
        Index("ix_call_flow_execution_steps_execution_created", "execution_id", "created_at"),
    )

    execution_id: Mapped[UUID] = mapped_column(index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    node_id: Mapped[UUID] = mapped_column(index=True)
    system_key: Mapped[str] = mapped_column(String(80), nullable=False)
    node_type: Mapped[str] = mapped_column(String(40), nullable=False)
    language_code: Mapped[str] = mapped_column(String(32), nullable=False)
    text_snapshot: Mapped[str] = mapped_column(Text, default="", nullable=False)
    hint_snapshot: Mapped[str] = mapped_column(Text, default="", nullable=False)
    selected_answer_key: Mapped[str | None] = mapped_column(String(80))
    selected_answer_label: Mapped[str | None] = mapped_column(String(240))
    input_value: Mapped[object | None] = mapped_column(JSON)
    next_node_id: Mapped[UUID | None] = mapped_column(index=True)
    action_status: Mapped[str | None] = mapped_column(String(24))
    actor_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DialerCompletionSubmission(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "dialer_completion_submissions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "call_id"],
            ["calls.tenant_id", "calls.id"],
            name="fk_dialer_completion_submissions_tenant_call",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_dialer_completion_submissions_tenant_key",
        ),
    )

    call_id: Mapped[UUID] = mapped_column(index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    response_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


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
    provider: Mapped[str | None] = mapped_column(String(40))
    provider_event_id: Mapped[str | None] = mapped_column(String(200))
    external_call_id: Mapped[str | None] = mapped_column(String(200))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    provider_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    correlation_id: Mapped[str | None] = mapped_column(String(160))

    __table_args__ = (
        UniqueConstraint("call_id", "sequence"),
        Index(
            "uq_call_events_provider_event",
            "tenant_id",
            "provider",
            "provider_event_id",
            unique=True,
            postgresql_where=text("provider_event_id IS NOT NULL"),
        ),
        Index("ix_call_events_tenant_type_occurred", "tenant_id", "event_type", "occurred_at"),
    )


class TelephonyCommandSubmission(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "telephony_command_submissions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_telephony_commands_tenant_project",
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_telephony_commands_tenant_key"),
        Index("ix_telephony_commands_call_created", "call_id", "created_at"),
    )

    project_id: Mapped[UUID] = mapped_column(index=True)
    call_id: Mapped[UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"), index=True)
    command_id: Mapped[UUID] = mapped_column(unique=True, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    command_name: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    response_payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    external_call_id: Mapped[str | None] = mapped_column(String(200))
    correlation_id: Mapped[str] = mapped_column(String(160), nullable=False)


class TelephonyChannelReservation(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "telephony_channel_reservations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_telephony_reservations_tenant_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "sip_trunk_id"],
            ["sip_trunks.tenant_id", "sip_trunks.id"],
            name="fk_telephony_reservations_tenant_trunk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "call_id"],
            ["calls.tenant_id", "calls.id"],
            name="fk_telephony_reservations_tenant_call",
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_telephony_reservations_tenant_id"),
        UniqueConstraint("tenant_id", "call_id", name="uq_telephony_reservations_tenant_call"),
        CheckConstraint("direction IN ('inbound', 'outbound')", name="telephony_reservation_direction_valid"),
        CheckConstraint(
            "status IN ('reserved', 'released', 'lost')",
            name="telephony_reservation_status_valid",
        ),
        CheckConstraint("lock_version >= 1", name="telephony_reservation_lock_version_positive"),
        Index(
            "ix_telephony_reservations_trunk_active",
            "tenant_id",
            "sip_trunk_id",
            "status",
            "direction",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(index=True)
    sip_trunk_id: Mapped[UUID] = mapped_column(index=True)
    call_id: Mapped[UUID] = mapped_column(index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    pool_key: Mapped[str] = mapped_column(String(24), default="shared", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="reserved", nullable=False, index=True)
    provider_channel_id: Mapped[str | None] = mapped_column(String(200), index=True)
    reserved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    release_reason: Mapped[str | None] = mapped_column(String(80))
    lock_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class TelephonyResource(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "telephony_resources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_telephony_resources_tenant_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "call_id"],
            ["calls.tenant_id", "calls.id"],
            name="fk_telephony_resources_tenant_call",
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_telephony_resources_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "resource_type",
            "provider_resource_id",
            name="uq_telephony_resources_provider_id",
        ),
        CheckConstraint(
            "resource_type IN ('channel', 'bridge', 'external_media', 'recording')",
            name="telephony_resource_type_valid",
        ),
        CheckConstraint(
            "status IN ('creating', 'active', 'stopping', 'released', 'orphaned', 'failed')",
            name="telephony_resource_status_valid",
        ),
        CheckConstraint("lock_version >= 1", name="telephony_resource_lock_version_positive"),
        Index("ix_telephony_resources_call_status", "tenant_id", "call_id", "status"),
    )

    project_id: Mapped[UUID] = mapped_column(index=True)
    call_id: Mapped[UUID] = mapped_column(index=True)
    resource_type: Mapped[str] = mapped_column(String(24), nullable=False)
    provider_resource_id: Mapped[str] = mapped_column(String(200), nullable=False)
    parent_provider_resource_id: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(24), default="creating", nullable=False, index=True)
    safe_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    lock_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class CallDetailRecord(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "call_detail_records"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_call_cdr_tenant_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "call_id"],
            ["calls.tenant_id", "calls.id"],
            name="fk_call_cdr_tenant_call",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "sip_trunk_id"],
            ["sip_trunks.tenant_id", "sip_trunks.id"],
            name="fk_call_cdr_tenant_trunk",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_call_cdr_tenant_id"),
        UniqueConstraint("tenant_id", "call_id", name="uq_call_cdr_tenant_call"),
        CheckConstraint("direction IN ('inbound', 'outbound')", name="call_cdr_direction_valid"),
        CheckConstraint("duration_seconds >= 0", name="call_cdr_duration_non_negative"),
        CheckConstraint(
            "billable_duration_seconds IS NULL OR billable_duration_seconds >= 0",
            name="call_cdr_billable_duration_non_negative",
        ),
        CheckConstraint("channels_used > 0", name="call_cdr_channels_used_positive"),
        Index("ix_call_cdr_tenant_started", "tenant_id", "started_at"),
    )

    project_id: Mapped[UUID] = mapped_column(index=True)
    call_id: Mapped[UUID] = mapped_column(index=True)
    sip_trunk_id: Mapped[UUID] = mapped_column(index=True)
    external_call_id: Mapped[str | None] = mapped_column(String(200), index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    did_masked: Mapped[str | None] = mapped_column(String(40))
    destination_masked: Mapped[str | None] = mapped_column(String(40))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disposition: Mapped[str] = mapped_column(String(40), nullable=False)
    hangup_cause: Mapped[str | None] = mapped_column(String(40))
    codec: Mapped[str | None] = mapped_column(String(40))
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    billable_duration_seconds: Mapped[int | None] = mapped_column(Integer)
    channels_used: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    safe_provider_metadata: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)


class TelephonyDiagnosticRun(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "telephony_diagnostic_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_telephony_diagnostics_tenant_project",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "sip_trunk_id"],
            ["sip_trunks.tenant_id", "sip_trunks.id"],
            name="fk_telephony_diagnostics_tenant_trunk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "call_id"],
            ["calls.tenant_id", "calls.id"],
            name="fk_telephony_diagnostics_tenant_call",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_telephony_diagnostics_tenant_requester",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_telephony_diagnostics_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "correlation_id",
            name="uq_telephony_diagnostics_tenant_correlation",
        ),
        CheckConstraint("mode IN ('local', 'live')", name="telephony_diagnostic_mode_valid"),
        CheckConstraint(
            "status IN ('pending', 'running', 'local_test_passed', 'provider_unreachable', "
            "'live_signaling_verified', 'live_audio_verified', 'degraded', 'failed', 'cancelled')",
            name="telephony_diagnostic_status_valid",
        ),
        CheckConstraint("lock_version >= 1", name="telephony_diagnostic_lock_version_positive"),
        Index("ix_telephony_diagnostics_tenant_started", "tenant_id", "started_at"),
    )

    project_id: Mapped[UUID] = mapped_column(index=True)
    sip_trunk_id: Mapped[UUID] = mapped_column(index=True)
    call_id: Mapped[UUID | None] = mapped_column(index=True)
    requested_by_user_id: Mapped[UUID] = mapped_column(index=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="pending", nullable=False, index=True)
    destination_hash: Mapped[str | None] = mapped_column(String(64))
    destination_masked: Mapped[str | None] = mapped_column(String(40))
    signaling_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    inbound_audio_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    outbound_audio_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    dtmf_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    codec: Mapped[str | None] = mapped_column(String(40))
    media_statistics: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    safe_error_code: Mapped[str | None] = mapped_column(String(80))
    correlation_id: Mapped[str] = mapped_column(String(160), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lock_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


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
    retention_redacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    __table_args__ = (
        UniqueConstraint("call_id", "sequence"),
        ForeignKeyConstraint(
            ["tenant_id", "call_id"],
            ["calls.tenant_id", "calls.id"],
            name="fk_transcript_segments_tenant_call",
            ondelete="CASCADE",
        ),
    )


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
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "call_id"],
            ["calls.tenant_id", "calls.id"],
            name="fk_call_recordings_tenant_call",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "storage_object_id"],
            ["storage_objects.tenant_id", "storage_objects.id"],
            name="fk_call_recordings_tenant_storage_object",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "retention_state IN ('retained', 'eligible', 'pending_purge', 'purged')",
            name="retention_state_valid",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_call_recordings_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "provider_recording_id",
            name="uq_call_recordings_tenant_provider_recording",
        ),
        Index(
            "ix_call_recordings_tenant_retention",
            "tenant_id",
            "retention_state",
            "delete_after",
        ),
    )

    call_id: Mapped[UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"), index=True)
    storage_object_id: Mapped[UUID | None] = mapped_column(index=True)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    provider_recording_id: Mapped[str | None] = mapped_column(String(200), index=True)
    encryption_key_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="pending_upload", nullable=False)
    recording_paused_ranges: Mapped[list[dict[str, int]]] = mapped_column(JSON, default=list)
    delete_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retention_state: Mapped[str] = mapped_column(String(24), default="retained", nullable=False)
    retention_eligible_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    pending_purge_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class CallTag(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "call_tags"
    __table_args__ = (UniqueConstraint("call_id", "tag"),)

    call_id: Mapped[UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"), index=True)
    tag: Mapped[str] = mapped_column(String(80), nullable=False)


class CallOutcome(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "call_outcomes"
    __table_args__ = (
        UniqueConstraint("call_id"),
        ForeignKeyConstraint(
            ["tenant_id", "project_id", "result_definition_id"],
            [
                "call_result_definitions.tenant_id",
                "call_result_definitions.project_id",
                "call_result_definitions.id",
            ],
            name="fk_call_outcomes_tenant_project_result_definition",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_call_outcomes_tenant_idempotency_key",
            "tenant_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_call_outcomes_project_category", "project_id", "category"),
        UniqueConstraint("tenant_id", "id", name="uq_call_outcomes_tenant_id"),
        CheckConstraint(
            "category IN ('successful', 'intermediate', 'unreachable', 'unsuccessful')",
            name="category_valid",
        ),
    )

    call_id: Mapped[UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"))
    project_id: Mapped[UUID] = mapped_column(index=True)
    result_definition_id: Mapped[UUID] = mapped_column(index=True)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    category: Mapped[CallResultCategory] = mapped_column(enum_type(CallResultCategory), nullable=False)
    color: Mapped[str] = mapped_column(String(7), nullable=False)
    label_translations: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(160))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))


class CallResultSubmission(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "call_result_submissions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "outcome_id"],
            ["call_outcomes.tenant_id", "call_outcomes.id"],
            name="fk_call_result_submissions_tenant_outcome",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_call_result_submissions_tenant_idempotency_key",
        ),
    )

    outcome_id: Mapped[UUID] = mapped_column(index=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    response_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


class CallbackTask(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "callback_tasks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_callback_tasks_tenant_project_projects",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["memberships.tenant_id", "memberships.user_id"],
            name="fk_callback_tasks_tenant_creator_memberships",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "call_outcome_id"],
            ["call_outcomes.tenant_id", "call_outcomes.id"],
            name="fk_callback_tasks_tenant_call_outcome",
            ondelete="SET NULL (call_outcome_id)",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_callback_tasks_tenant_id"),
        CheckConstraint(
            "task_type IN ('callback', 'follow_up', 'manual', 'system')",
            name="task_type_valid",
        ),
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'completed', 'cancelled')",
            name="status_valid",
        ),
        CheckConstraint(
            "priority IN ('low', 'normal', 'high', 'urgent')",
            name="priority_valid",
        ),
        CheckConstraint(
            "source IN ('manual', 'call_result', 'system', 'call_flow', 'legacy_callback')",
            name="source_valid",
        ),
        CheckConstraint(
            "(status = 'completed') = (completed_at IS NOT NULL)",
            name="completed_at_matches_status",
        ),
        CheckConstraint(
            "(status = 'cancelled') = (cancelled_at IS NOT NULL)",
            name="cancelled_at_matches_status",
        ),
        Index("ix_callback_tasks_tenant_status_due", "tenant_id", "status", "due_at"),
        Index("ix_callback_tasks_project_status_due", "project_id", "status", "due_at"),
        Index(
            "ix_callback_tasks_project_type_status_due",
            "tenant_id",
            "project_id",
            "task_type",
            "status",
            "due_at",
        ),
        Index(
            "uq_callback_tasks_tenant_idempotency_key",
            "tenant_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index("ix_callback_tasks_tenant_project_created", "tenant_id", "project_id", "created_at"),
    )

    project_id: Mapped[UUID] = mapped_column(index=True)
    customer_id: Mapped[UUID] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    call_id: Mapped[UUID | None] = mapped_column(ForeignKey("calls.id", ondelete="SET NULL"), index=True)
    call_outcome_id: Mapped[UUID | None] = mapped_column(index=True)
    task_type: Mapped[TaskType] = mapped_column(enum_type(TaskType), default=TaskType.CALLBACK)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    priority: Mapped[TaskPriority] = mapped_column(
        enum_type(TaskPriority), default=TaskPriority.NORMAL, nullable=False, index=True
    )
    assigned_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_by_user_id: Mapped[UUID] = mapped_column(index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[TaskStatus] = mapped_column(
        enum_type(TaskStatus, length=24), default=TaskStatus.PENDING, nullable=False, index=True
    )
    comment: Mapped[str] = mapped_column(Text, default="", nullable=False)
    source: Mapped[TaskSource] = mapped_column(
        enum_type(TaskSource), default=TaskSource.MANUAL, nullable=False, index=True
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(160))
    # Kept as a compatibility mirror for the legacy /callbacks contract and safe downgrade.
    note: Mapped[str] = mapped_column(Text, default="")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(String(1000))


class TaskEvent(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "task_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["callback_tasks.tenant_id", "callback_tasks.id"],
            name="fk_task_events_tenant_task",
            ondelete="CASCADE",
        ),
        Index("ix_task_events_task_created", "task_id", "created_at"),
    )

    task_id: Mapped[UUID] = mapped_column(index=True)
    event_type: Mapped[TaskEventType] = mapped_column(enum_type(TaskEventType), nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    safe_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(120), nullable=False)


class TaskCommandSubmission(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "task_command_submissions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["callback_tasks.tenant_id", "callback_tasks.id"],
            name="fk_task_command_submissions_tenant_task",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_task_command_submissions_tenant_idempotency_key",
        ),
    )

    task_id: Mapped[UUID] = mapped_column(index=True)
    operation: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    response_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)


class HumanOperator(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "human_operators"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_human_operators_tenant_membership",
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "membership_id"),
        UniqueConstraint("tenant_id", "id", name="uq_human_operators_tenant_id_id"),
    )

    membership_id: Mapped[UUID] = mapped_column(ForeignKey("memberships.id", ondelete="CASCADE"))
    extension: Mapped[str | None] = mapped_column(String(32))
    max_concurrent_calls: Mapped[int] = mapped_column(Integer, default=1)
    is_transfer_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class OperatorStatus(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "operator_statuses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "human_operator_id"],
            ["human_operators.tenant_id", "human_operators.id"],
            name="fk_operator_statuses_tenant_human_operator",
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "human_operator_id"),
    )

    human_operator_id: Mapped[UUID] = mapped_column(ForeignKey("human_operators.id", ondelete="CASCADE"))
    status: Mapped[QueueStatus] = mapped_column(enum_type(QueueStatus), default=QueueStatus.OFFLINE)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OperatorPresence(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    __tablename__ = "operator_presences"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_operator_presences_tenant_membership",
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "membership_id", "session_key", name="uq_operator_presences_session"),
        Index(
            "ix_operator_presences_active_heartbeat",
            "tenant_id",
            "membership_id",
            "heartbeat_at",
        ),
    )

    membership_id: Mapped[UUID] = mapped_column(index=True)
    session_key: Mapped[str] = mapped_column(String(80), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RealtimeEvent(UUIDPrimaryKeyMixin, TenantOwnedMixin, Base):
    """Durable, tenant-scoped signal used by the realtime delivery pipeline.

    The payload deliberately contains invalidation metadata only. PostgreSQL and
    the REST API remain the canonical source for complete aggregate state.
    """

    __tablename__ = "realtime_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["projects.tenant_id", "projects.id"],
            name="fk_realtime_events_tenant_project",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "target_membership_id"],
            ["memberships.tenant_id", "memberships.id"],
            name="fk_realtime_events_tenant_target_membership",
            ondelete="CASCADE",
        ),
        UniqueConstraint("cursor", name="uq_realtime_events_cursor"),
        CheckConstraint(
            "publish_status IN ('pending', 'published')",
            name="publish_status_valid",
        ),
        CheckConstraint("publish_attempts >= 0", name="publish_attempts_non_negative"),
        Index("ix_realtime_events_tenant_cursor", "tenant_id", "cursor"),
        Index("ix_realtime_events_tenant_project_cursor", "tenant_id", "project_id", "cursor"),
        Index(
            "ix_realtime_events_tenant_target_cursor",
            "tenant_id",
            "target_membership_id",
            "cursor",
        ),
        Index("ix_realtime_events_publish", "publish_status", "cursor"),
        Index("ix_realtime_events_expiry", "expires_at"),
    )

    cursor: Mapped[int] = mapped_column(BigInteger, Identity(), nullable=False)
    project_id: Mapped[UUID | None] = mapped_column(index=True)
    target_membership_id: Mapped[UUID | None] = mapped_column(index=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(80), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(nullable=False)
    aggregate_version: Mapped[int | None] = mapped_column(Integer)
    safe_payload: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    publish_status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    publish_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(160))
    causation_id: Mapped[UUID | None] = mapped_column()


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
    __table_args__ = (
        Index("ix_transfer_requests_tenant_status_requested", "tenant_id", "status", "requested_at"),
    )

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
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key"),
        Index("ix_usage_records_tenant_metric_occurred", "tenant_id", "metric", "occurred_at"),
    )

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
