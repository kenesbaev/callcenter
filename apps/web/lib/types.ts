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
  project_id: string;
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
  call_flow_version_id?: string | null;
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

export type CallFlowNodeType =
  | "start"
  | "operator_text"
  | "customer_question"
  | "info_hint"
  | "choice"
  | "value_input"
  | "update_customer_field"
  | "create_task"
  | "create_callback"
  | "transfer_request"
  | "end";

export type CallFlowAnswer = {
  id: string;
  key: string;
  label_by_language: Record<string, string>;
  next_node_id: string | null;
  is_required: boolean;
};

export type CallFlowNode = {
  id: string;
  system_key: string;
  name: string;
  node_type: CallFlowNodeType;
  text_by_language: Record<string, string>;
  hint_by_language: Record<string, string>;
  order: number;
  is_required: boolean;
  customer_field_definition_id: string | null;
  answers: CallFlowAnswer[];
  next_node_id: string | null;
  fallback_node_id: string | null;
  action_config: Record<string, unknown>;
};

export type CallFlowDefinition = {
  schema_version: 1;
  nodes: CallFlowNode[];
};

export type CallFlowVersionSummary = {
  id: string;
  version: number;
  status: "draft" | "published" | "archived";
  lock_version: number;
  created_from_version_id: string | null;
  published_at: string | null;
  created_at: string;
  updated_at: string;
};

export type CallFlow = {
  id: string;
  project_id: string;
  name: string;
  description: string;
  default_language_code: string;
  language_codes: string[];
  active_version_id: string | null;
  is_active: boolean;
  archived_at: string | null;
  versions: CallFlowVersionSummary[];
  created_at: string;
  updated_at: string;
};

export type CallFlowVersion = CallFlowVersionSummary & {
  call_flow_id: string;
  project_id: string;
  definition: CallFlowDefinition;
};

export type CallFlowValidationIssue = {
  code: string;
  message: string;
  node_id: string | null;
  node_name: string | null;
};

export type CallFlowValidation = {
  valid: boolean;
  errors: CallFlowValidationIssue[];
};

export type CallFlowPreviewNode = {
  id: string;
  system_key: string;
  name: string;
  node_type: CallFlowNodeType;
  text: string;
  hint: string;
  answers: Array<{ key: string; label: string }>;
  action_is_inert: boolean;
};

export type CallFlowPreview = {
  language_code: string;
  path: CallFlowPreviewNode[];
  current_node: CallFlowPreviewNode | null;
  completed: boolean;
  inert_actions: string[];
};

export type CustomerContact = {
  id: string;
  kind: "phone" | "email" | string;
  value: string;
  label: string | null;
  is_primary: boolean;
};

export type Customer = {
  id: string;
  project_id: string;
  display_name: string | null;
  external_reference: string | null;
  preferred_language: "ru" | "en" | "uz" | "kaa" | null;
  status: string;
  city: string | null;
  region: string | null;
  address: string | null;
  job_title: string | null;
  organization: string | null;
  tags: string[];
  description: string;
  source: string | null;
  assigned_user_id: string | null;
  custom_fields: Record<string, unknown>;
  contacts: CustomerContact[];
  locked_by_user_id: string | null;
  locked_until: string | null;
  last_call_at: string | null;
  next_call_at: string | null;
  next_contact_at: string | null;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
};

export type CustomerFieldType =
  | "text"
  | "textarea"
  | "number"
  | "boolean"
  | "date"
  | "datetime"
  | "select"
  | "multiselect";

export type CustomerFieldDefinition = {
  id: string;
  project_id: string;
  name: string;
  key: string;
  field_type: CustomerFieldType;
  is_required: boolean;
  sort_order: number;
  options: string[];
  default_value: unknown;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export type CustomerImportRow = {
  row_number: number;
  values: Record<string, unknown>;
  duplicate_fields: string[];
  errors: string[];
};

export type CustomerImportPreview = {
  id: string;
  project_id: string;
  file_name: string;
  file_type: string;
  sheet_names: string[];
  selected_sheet: string;
  headers: string[];
  mapping: Record<string, string>;
  update_rule: "skip" | "update";
  total_rows: number;
  valid_rows: number;
  duplicate_rows: number;
  error_rows: number;
  rows: CustomerImportRow[];
  expires_at: string;
};

export type CustomerImportReport = {
  import_id: string;
  created: number;
  updated: number;
  skipped: number;
  duplicates: number;
  errors: Array<{ row_number: number; messages: string[] }>;
  committed_at: string;
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
  default_language: "ru" | "en" | "uz" | "kaa" | null;
  timezone: string | null;
  outbound_number: string | null;
  outbound_phone_number_id: string | null;
  inbound_phone_number_ids: string[];
  ai_operator_id: string | null;
  knowledge_source_id: string | null;
  call_flow_id: string | null;
  call_result_catalog_id: string;
  max_concurrent_calls: number | null;
  effective_settings: ProjectEffectiveSettings;
  working_hours: ProjectWorkingHours;
  recording_enabled: boolean | null;
  recording_disclosure_required: boolean | null;
  max_attempts: number;
  retry_intervals_minutes: number[];
  callback_rules: ProjectCallbackRules;
  is_default: boolean;
  archived_at: string | null;
  operator_user_ids: string[];
  created_at: string;
  updated_at: string;
};

export type ProjectEffectiveSettings = {
  default_language: "ru" | "en" | "uz" | "kaa";
  timezone: string;
  max_concurrent_calls: number;
  recording_enabled: boolean;
  recording_disclosure_required: boolean;
};

export type ProjectWorkingDay = {
  enabled: boolean;
  start?: string | null;
  end?: string | null;
};

export type ProjectWorkingHours = Partial<
  Record<
    | "monday"
    | "tuesday"
    | "wednesday"
    | "thursday"
    | "friday"
    | "saturday"
    | "sunday",
    ProjectWorkingDay
  >
>;

export type ProjectCallbackRules = {
  default_delay_minutes: number;
  max_schedule_days: number;
  allow_operator_scheduling: boolean;
  require_assignee: boolean;
  overdue_first: boolean;
};

export type ProjectOptions = {
  tenant_defaults: ProjectEffectiveSettings;
  operators: Array<{
    user_id: string;
    display_name: string;
    email: string;
    role: Role;
  }>;
  phone_numbers: Array<{
    id: string;
    e164: string;
    label: string;
    is_active: boolean;
  }>;
  ai_operators: Array<{
    id: string;
    project_id: string;
    name: string;
    is_active: boolean;
  }>;
  knowledge_sources: Array<{
    id: string;
    name: string;
    source_type: string;
  }>;
  call_flows: Array<{
    id: string;
    project_id: string;
    name: string;
    is_active: boolean;
  }>;
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
