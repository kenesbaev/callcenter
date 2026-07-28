export type Role =
  | "platform_admin"
  | "tenant_owner"
  | "tenant_manager"
  | "human_operator"
  | "analyst"
  | "billing_admin";

export type AuthResponse = {
  user: { id: string; email: string; display_name: string; role: Role };
  tenant: { id: string; name: string; slug: string };
  csrf_token: string;
};

export type Page<T> = {
  items: T[];
  total: number;
  limit: number;
  offset: number;
};

export type AiOperator = {
  id: string;
  name: string;
  description: string;
  is_active: boolean;
  active_version_id: string | null;
  created_at: string;
  version: {
    id: string;
    version: number;
    status: "draft" | "published" | "archived";
    allowed_languages: string[];
    allowed_tools: string[];
    published_at: string | null;
  };
};

export type KnowledgeDocument = {
  id: string;
  source_id: string;
  title: string;
  language: "ru" | "en" | "uz" | "kaa";
  content: string;
  created_at: string;
};

export type TranscriptSegment = {
  id: string;
  sequence: number;
  speaker: "customer" | "ai" | "human" | "system" | "tool";
  language: "ru" | "en" | "uz" | "kaa";
  text: string;
  created_at: string;
};

export type Call = {
  id: string;
  project_id: string;
  channel: "sip" | "development_simulator";
  status:
    "queued" | "ringing" | "active" | "transferring" | "completed" | "failed";
  language: string | null;
  customer_id: string | null;
  operator_user_id: string | null;
  ai_operator_id: string | null;
  direction: string;
  provider: string;
  from_number: string | null;
  to_number: string | null;
  started_at: string | null;
  answered_at: string | null;
  ended_at: string | null;
  duration_seconds: number;
  transfer_reason: string | null;
  is_demo: boolean;
};

export type CustomerContact = {
  kind: "phone" | "email" | string;
  value: string;
  is_primary: boolean;
};

export type Customer = {
  id: string;
  project_id: string;
  display_name: string | null;
  external_reference: string | null;
  preferred_language: "ru" | "en" | "uz" | "kaa" | null;
  status: string;
  custom_fields: Record<string, unknown>;
  contacts: CustomerContact[];
  locked_by_user_id: string | null;
  locked_until: string | null;
  last_call_at: string | null;
  next_call_at: string | null;
  created_at: string;
};

export type CallbackTask = {
  id: string;
  project_id: string;
  customer_id: string;
  customer_name: string | null;
  customer_phone: string | null;
  call_id: string | null;
  assigned_user_id: string | null;
  due_at: string;
  status: string;
  note: string;
  completed_at: string | null;
  created_at: string;
};

export type DialerAssignment = {
  customer: Customer;
  source: "callback" | "new";
  callback_task_id: string | null;
  lock_token: string;
};

export type Project = {
  id: string;
  name: string;
  description: string;
  status: "active" | "paused" | "archived";
  outbound_number: string | null;
  max_concurrent_calls: number;
  working_hours: Record<string, unknown>;
  is_default: boolean;
  operator_user_ids: string[];
  created_at: string;
  updated_at: string;
};

export type CallResultResponse = {
  call: Call;
  customer_status: string;
  callback_task_id: string | null;
};

export type CallDetail = Call & {
  transcript: TranscriptSegment[];
  summary: {
    summary: string;
    topics: string[];
    result: string;
    generated_by: string;
  } | null;
};

export type Dashboard = {
  active_calls: number;
  calls_today: number;
  completed_calls: number;
  transfers: number;
  average_duration_seconds: number;
  used_ai_minutes: string;
  estimated_cost_usd: string;
  language_breakdown: Record<string, number>;
  is_demo: boolean;
};

export type TeamMember = {
  membership_id: string;
  user_id: string;
  display_name: string;
  email: string;
  role: Role;
  is_active: boolean;
  operator_status: "offline" | "available" | "busy" | "wrap_up" | null;
  extension: string | null;
};

export type Integration = {
  integration_id: string | null;
  provider: string;
  display_name: string;
  status: "unavailable" | "development" | "configured" | "verified";
  capabilities: string[];
  verified_at: string | null;
  can_configure: boolean;
  note: string;
};

export type TenantSettings = {
  timezone: string;
  default_language: "ru" | "en" | "uz" | "kaa";
  recording_enabled: boolean;
  recording_disclosure_required: boolean;
  retention_days: number;
  max_concurrent_calls: number;
  updated_at: string;
};
