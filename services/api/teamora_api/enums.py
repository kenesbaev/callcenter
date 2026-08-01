from enum import StrEnum


class TenantStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class RoleName(StrEnum):
    PLATFORM_ADMIN = "platform_admin"
    TENANT_OWNER = "tenant_owner"
    TENANT_MANAGER = "tenant_manager"
    HUMAN_OPERATOR = "human_operator"
    ANALYST = "analyst"
    BILLING_ADMIN = "billing_admin"


class LanguageCode(StrEnum):
    RU = "ru"
    EN = "en"
    UZ = "uz"
    KAA = "kaa"


class LanguageReadiness(StrEnum):
    PRODUCTION = "production"
    BETA = "beta"
    EXPERIMENTAL = "experimental"


class OperatorVersionStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class CallStatus(StrEnum):
    QUEUED = "queued"
    INITIATED = "initiated"
    RINGING = "ringing"
    ACTIVE = "active"
    ON_HOLD = "on_hold"
    TRANSFER_REQUESTED = "transfer_requested"
    TRANSFERRING = "transferring"
    TRANSFERRED = "transferred"
    COMPLETED = "completed"
    BUSY = "busy"
    NO_ANSWER = "no_answer"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CallDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class CallerType(StrEnum):
    HUMAN_OPERATOR = "human_operator"
    AI_AGENT = "ai_agent"


class HangupCause(StrEnum):
    NORMAL = "normal"
    CALLER_HANGUP = "caller_hangup"
    OPERATOR_HANGUP = "operator_hangup"
    BUSY = "busy"
    NO_ANSWER = "no_answer"
    REJECTED = "rejected"
    NETWORK_ERROR = "network_error"
    PROVIDER_ERROR = "provider_error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class CallChannel(StrEnum):
    SIP = "sip"
    DEVELOPMENT_SIMULATOR = "development_simulator"


class TelephonyCommandName(StrEnum):
    ORIGINATE = "originate"
    ANSWER = "answer"
    HANGUP = "hangup"
    HOLD = "hold"
    RESUME = "resume"
    TRANSFER = "transfer"
    GET_CALL_STATE = "get_call_state"
    START_RECORDING = "start_recording"
    PAUSE_RECORDING = "pause_recording"
    RESUME_RECORDING = "resume_recording"
    STOP_RECORDING = "stop_recording"
    CREATE_EXTERNAL_MEDIA = "create_external_media"


class TelephonyCallState(StrEnum):
    QUEUED = "queued"
    INITIATED = "initiated"
    RINGING = "ringing"
    ACTIVE = "active"
    ON_HOLD = "on_hold"
    TRANSFER_REQUESTED = "transfer_requested"
    TRANSFERRING = "transferring"
    TRANSFERRED = "transferred"
    COMPLETED = "completed"
    BUSY = "busy"
    NO_ANSWER = "no_answer"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class CallResultCategory(StrEnum):
    SUCCESSFUL = "successful"
    INTERMEDIATE = "intermediate"
    UNREACHABLE = "unreachable"
    UNSUCCESSFUL = "unsuccessful"


class TaskType(StrEnum):
    CALLBACK = "callback"
    FOLLOW_UP = "follow_up"
    MANUAL = "manual"
    SYSTEM = "system"


class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class TaskSource(StrEnum):
    MANUAL = "manual"
    CALL_RESULT = "call_result"
    SYSTEM = "system"
    CALL_FLOW = "call_flow"
    LEGACY_CALLBACK = "legacy_callback"


class TaskEventType(StrEnum):
    CREATED = "created"
    ASSIGNED = "assigned"
    REASSIGNED = "reassigned"
    STARTED = "started"
    RESCHEDULED = "rescheduled"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    RESTORED = "restored"
    PRIORITY_CHANGED = "priority_changed"
    COMMENT_ADDED = "comment_added"
    UPDATED = "updated"


class TranscriptSpeaker(StrEnum):
    CUSTOMER = "customer"
    AI = "ai"
    HUMAN = "human"
    SYSTEM = "system"
    TOOL = "tool"


class ToolExecutionStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"
    TIMED_OUT = "timed_out"


class IntegrationStatus(StrEnum):
    UNAVAILABLE = "unavailable"
    DEVELOPMENT = "development"
    CONFIGURED = "configured"
    VERIFIED = "verified"


class QueueStatus(StrEnum):
    OFFLINE = "offline"
    AVAILABLE = "available"
    BUSY = "busy"
    WRAP_UP = "wrap_up"


class TransferStatus(StrEnum):
    REQUESTED = "requested"
    QUEUED = "queued"
    ASSIGNED = "assigned"
    COMPLETED = "completed"
    CALLBACK_REQUESTED = "callback_requested"
    FAILED = "failed"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
