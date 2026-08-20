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
  project_id: string | null;
  title: string;
  language: string;
  content: string;
  archived_at: string | null;
  created_at: string;
  version: KnowledgeDocumentVersion | null;
};

export type KnowledgeDocumentVersion = {
  id: string;
  document_id: string;
  revision_id: string;
  version: number;
  status:
    | "uploaded"
    | "queued"
    | "extracting"
    | "chunking"
    | "embedding"
    | "ready"
    | "failed"
    | "needs_ocr"
    | "archived";
  title_snapshot: string;
  language_code: string;
  original_filename: string;
  has_original: boolean;
  file_type: "txt" | "md" | "pdf" | "docx";
  content_type: string;
  checksum_sha256: string;
  content_length: number;
  page_count: number | null;
  chunk_count: number;
  safe_error_code: string | null;
  safe_error_message: string | null;
  lock_version: number;
  created_at: string;
  background_job?: BackgroundJobSummary | null;
};

export type KnowledgeRevision = {
  id: string;
  knowledge_base_id: string;
  project_id: string;
  version: number;
  status: "draft" | "published" | "archived";
  lock_version: number;
  created_from_revision_id: string | null;
  embedding_provider: string;
  embedding_model: string;
  embedding_dimension: number;
  embedding_status: "development" | "configured" | "verified" | "unavailable";
  index_version: string;
  published_at: string | null;
  created_at: string;
};

export type KnowledgeBase = {
  id: string;
  project_id: string | null;
  name: string;
  description: string;
  default_language_code: string;
  active_revision_id: string | null;
  archived_at: string | null;
  lock_version: number;
  draft_revision: KnowledgeRevision | null;
  published_revision: KnowledgeRevision | null;
  document_count: number;
  created_at: string;
};

export type KnowledgeRetrievalHit = {
  chunk_id: string;
  document_id: string;
  document_version_id: string;
  document_version: number;
  title: string;
  language: string;
  page: number | null;
  section: string | null;
  excerpt: string;
  lexical_score: number;
  vector_score: number;
  combined_score: number;
  revision_id: string;
};

export type KnowledgeRetrieval = {
  revision_id: string;
  hits: KnowledgeRetrievalHit[];
  no_match: boolean;
  provider: string;
  model: string;
  provider_status: string;
  index_version: string;
  notice: string;
  usage: {
    input_items: number;
    input_characters: number;
    estimated_tokens: number;
  };
};

export type TranscriptSegment = {
  id: string;
  sequence: number;
  speaker: "customer" | "ai" | "human" | "system" | "tool";
  language: "ru" | "en" | "uz" | "kaa";
  language_code: string;
  text: string;
  provider_item_id: string | null;
  is_final: boolean;
  interrupted: boolean;
  created_at: string;
};

export type CallStatus =
  | "queued"
  | "initiated"
  | "ringing"
  | "active"
  | "on_hold"
  | "transfer_requested"
  | "transferring"
  | "transferred"
  | "completed"
  | "busy"
  | "no_answer"
  | "failed"
  | "cancelled";

export type Call = {
  id: string;
  project_id: string;
  channel: "sip" | "development_simulator";
  status: CallStatus;
  language: string | null;
  customer_id: string | null;
  operator_user_id: string | null;
  ai_operator_id: string | null;
  call_flow_version_id?: string | null;
  knowledge_base_revision_id?: string | null;
  direction: "inbound" | "outbound";
  caller_type: "human_operator" | "ai_agent";
  provider: string;
  provider_call_id: string | null;
  provider_state:
    | "queued"
    | "initiated"
    | "ringing"
    | "active"
    | "on_hold"
    | "transfer_requested"
    | "transferring"
    | "transferred"
    | "completed"
    | "busy"
    | "no_answer"
    | "failed"
    | "cancelled"
    | "unknown";
  recording_state: string;
  from_number: string | null;
  to_number: string | null;
  started_at: string | null;
  ringing_at: string | null;
  answered_at: string | null;
  held_at: string | null;
  ended_at: string | null;
  duration_seconds: number;
  transfer_reason: string | null;
  hangup_cause:
    | "normal"
    | "caller_hangup"
    | "operator_hangup"
    | "busy"
    | "no_answer"
    | "rejected"
    | "network_error"
    | "provider_error"
    | "timeout"
    | "cancelled"
    | "unknown"
    | null;
  raw_provider_cause: string | null;
  last_provider_event_at: string | null;
  state_version: number;
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

export type BackgroundJobStatus =
  | "pending"
  | "scheduled"
  | "running"
  | "retry_wait"
  | "completed"
  | "failed"
  | "dead_letter"
  | "cancel_requested"
  | "cancelled";

export type BackgroundJobSummary = {
  id: string;
  project_id: string | null;
  type: string;
  queue: string;
  priority: number;
  status: BackgroundJobStatus;
  progress: number;
  attempt_count: number;
  max_attempts: number;
  scheduled_at: string | null;
  available_at: string;
  started_at: string | null;
  completed_at: string | null;
  cancelled_at: string | null;
  safe_error_code: string | null;
  state_version: number;
  created_at: string;
  updated_at: string;
  can_cancel: boolean;
  can_retry: boolean;
};

export type BackgroundJobDetail = BackgroundJobSummary & {
  correlation_id: string | null;
  causation_id: string | null;
  payload_summary: Record<string, unknown>;
  result_metadata: Record<string, unknown>;
  safe_error_message: string | null;
};

export type BackgroundJobAttempt = {
  id: string;
  job_id: string;
  attempt: number;
  status: string;
  worker_id: string | null;
  started_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  safe_error_code: string | null;
  safe_error_message: string | null;
};

export type BackgroundJobEvent = {
  id: string;
  job_id: string;
  event_type: string;
  progress: number | null;
  safe_snapshot: Record<string, unknown>;
  occurred_at: string;
};

export type BackgroundJobStats = {
  queued: number;
  running: number;
  retry_wait: number;
  completed: number;
  failed: number;
  dead_letter: number;
  cancel_requested: number;
  oldest_pending_at: string | null;
  can_manage: boolean;
};

export type BackgroundImportStatus =
  | "uploading"
  | "previewing"
  | "preview_ready"
  | "queued"
  | "staging"
  | "ready_to_finalize"
  | "finalizing"
  | "completed"
  | "failed"
  | "expired"
  | "cancel_requested"
  | "cancelled";

export type BackgroundCustomerImport = {
  id: string;
  job_id: string | null;
  project_id: string;
  file_name: string;
  file_type: "csv" | "xlsx";
  status: BackgroundImportStatus;
  progress: number;
  sheet_names: string[];
  selected_sheet: string;
  headers: string[];
  mapping: Record<string, string>;
  update_rule: "skip" | "update";
  preview_rows: CustomerImportRow[];
  total_rows: number;
  valid_rows: number;
  invalid_rows: number;
  duplicate_rows: number;
  created: number;
  updated: number;
  skipped: number;
  error_count: number;
  safe_error_code: string | null;
  processing_duration_ms: number | null;
  state_version: number;
  can_commit: boolean;
  can_cancel: boolean;
  can_retry: boolean;
  report_available: boolean;
  created_at: string;
  completed_at: string | null;
};

export type StorageCategorySummary = {
  category: string;
  object_count: number;
  bytes: number;
};

export type StorageSummary = {
  object_count: number;
  total_bytes: number;
  issue_count: number;
  pending_purge_count: number;
  categories: StorageCategorySummary[];
  last_scan_at: string | null;
  can_scan: boolean;
};

export type StorageIssue = {
  id: string;
  project_id: string | null;
  object_id: string | null;
  issue_type:
    | "missing_object"
    | "orphan_object"
    | "checksum_mismatch"
    | "size_mismatch"
    | "expired_temporary"
    | "multipart_upload";
  status: "candidate" | "quarantined" | "confirmed" | "resolved";
  object_category: string;
  detected_at: string;
  last_verified_at: string | null;
  grace_expires_at: string | null;
};

export type RetentionPolicy = {
  id: string;
  automatic_purge_enabled: boolean;
  grace_period_days: number;
  call_recording_days: number | null;
  transcript_days: number | null;
  temporary_import_days: number | null;
  import_report_days: number | null;
  archived_knowledge_days: number | null;
  realtime_event_hours: number | null;
  completed_job_days: number | null;
  failed_job_days: number | null;
  state_version: number;
  updated_at: string;
  can_manage: boolean;
};

export type RetentionPreview = {
  id: string;
  policy_version: number;
  eligible_objects: number;
  eligible_bytes: number;
  excluded_by_legal_hold: number;
  excluded_by_active_reference: number;
  created_at: string;
};

export type RetentionCandidate = {
  id: string;
  project_id: string | null;
  object_category: string;
  owner_aggregate_type: string;
  owner_aggregate_id: string | null;
  status: "eligible" | "pending_purge" | "blocked" | "purged" | "cancelled";
  eligible_at: string;
  purge_after: string | null;
  bytes: number;
  legal_hold: boolean;
  can_cancel: boolean;
  state_version: number;
};

export type LegalHold = {
  id: string;
  project_id: string | null;
  scope_type: "tenant" | "project" | "call" | "customer" | "document";
  scope_id: string | null;
  reason: string;
  created_by_user_id: string;
  created_at: string;
  released_by_user_id: string | null;
  released_at: string | null;
  can_release: boolean;
  state_version: number;
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
  source: "callback" | "retry" | "new";
  callback_task_id: string | null;
  task: DialerTaskSummary | null;
  pending_tasks: DialerTaskSummary[];
  lock_token: string;
};

export type DialerFlowStep = {
  id: string;
  sequence: number;
  node_id: string;
  system_key: string;
  node_type: string;
  language_code: string;
  text_snapshot: string;
  hint_snapshot: string;
  selected_answer_key: string | null;
  selected_answer_label: string | null;
  input_value: unknown;
  next_node_id: string | null;
  action_status: string | null;
  occurred_at: string;
};

export type DialerFlowExecution = {
  id: string;
  call_id: string;
  call_flow_version_id: string;
  current_node: CallFlowNode | null;
  status: "active" | "completed" | "cancelled";
  language_code: string;
  language_codes: string[];
  state_version: number;
  values: Record<string, unknown>;
  steps: DialerFlowStep[];
  started_at: string;
  completed_at: string | null;
};

export type DialerHistoryItem = {
  call: Call;
  result_code: string | null;
  result_label: string | null;
  result_category: CallResultCategory | null;
  comment: string;
  operator_name: string | null;
  transcript: Array<{
    speaker: string;
    language: string;
    text: string;
    sequence: number;
  }>;
  summary: string | null;
};

export type DialerCompleteAndNextResponse = {
  result: CallResultResponse;
  next_assignment: DialerAssignment | null;
  queue_complete: boolean;
  replayed: boolean;
};

export type CallState = {
  call_id: string;
  status: CallStatus;
  state_version: number;
  direction: "inbound" | "outbound";
  provider: string;
  provider_call_id: string | null;
  state: CallStatus;
  recording_state: string;
  allowed_actions: string[];
  transfer_state: CallStatus | null;
  started_at: string | null;
  ringing_at: string | null;
  answered_at: string | null;
  held_at: string | null;
  ended_at: string | null;
  hangup_cause: Call["hangup_cause"];
  terminal: boolean;
  last_event: {
    event_type: string;
    sequence: number;
    occurred_at: string;
  } | null;
  updated_at: string;
};

export type TaskType = "callback" | "follow_up" | "manual" | "system";
export type TaskStatus = "pending" | "in_progress" | "completed" | "cancelled";
export type TaskPriority = "low" | "normal" | "high" | "urgent";
export type TaskSource =
  "manual" | "call_result" | "system" | "call_flow" | "legacy_callback";

export type DialerTaskSummary = {
  id: string;
  task_type: TaskType;
  title: string;
  priority: TaskPriority;
  status: TaskStatus;
  due_at: string;
  comment: string;
  assigned_user_id: string | null;
};

export type Task = {
  id: string;
  tenant_id: string;
  project_id: string;
  project_name: string;
  project_timezone: string;
  task_type: TaskType;
  title: string;
  description: string;
  priority: TaskPriority;
  status: TaskStatus;
  customer_id: string;
  customer_name: string | null;
  customer_phone: string | null;
  call_id: string | null;
  call_outcome_id: string | null;
  assigned_user_id: string | null;
  assigned_user_name: string | null;
  created_by_user_id: string;
  created_by_user_name: string;
  due_at: string;
  started_at: string | null;
  completed_at: string | null;
  cancelled_at: string | null;
  cancellation_reason: string | null;
  comment: string;
  source: TaskSource;
  is_overdue: boolean;
  created_at: string;
  updated_at: string;
};

export type TaskEvent = {
  id: string;
  task_id: string;
  event_type:
    | "created"
    | "assigned"
    | "reassigned"
    | "started"
    | "rescheduled"
    | "completed"
    | "cancelled"
    | "restored"
    | "priority_changed"
    | "comment_added"
    | "updated";
  actor_user_id: string | null;
  actor_name: string | null;
  safe_snapshot: Record<string, unknown>;
  created_at: string;
};

export type TaskOptions = {
  operators: Array<{ user_id: string; display_name: string }>;
  customers: Array<{
    id: string;
    display_name: string | null;
    phone: string | null;
  }>;
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
  task_ids: string[];
  outcome: {
    result_definition_id: string;
    code: string;
    label: string;
    category: CallResultCategory;
    color: string;
  };
};

export type CallResultCategory =
  "successful" | "intermediate" | "unreachable" | "unsuccessful";

export type CallResultDefinition = {
  id: string;
  project_id: string;
  catalog_id: string;
  system_code: string;
  category: CallResultCategory;
  name: string;
  name_translations: Record<string, string>;
  description: string;
  color: string;
  sort_order: number;
  is_active: boolean;
  requires_comment: boolean;
  requires_callback: boolean;
  requires_callback_at: boolean;
  creates_task: boolean;
  next_customer_status: string | null;
  return_to_queue: boolean;
  completes_customer: boolean;
  do_not_call: boolean;
  counts_as_success: boolean;
  archived_at: string | null;
  used_count: number;
  created_at: string;
  updated_at: string;
};

export type CallResultCatalog = {
  id: string;
  project_id: string;
  name: string;
  is_active: boolean;
  definitions: CallResultDefinition[];
};

export type CallDetail = Call & {
  transcript: TranscriptSegment[];
  summary: {
    summary: string;
    topics: string[];
    result: string;
    generated_by: string;
  } | null;
  transfers: Array<{
    id: string;
    status: LiveTransfer["status"];
    reason: string;
    requested_at: string;
    connected_at: string | null;
    resolved_at: string | null;
  }>;
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

export type AnalyticsMetric = {
  value: number | null;
  numerator: number | null;
  denominator: number | null;
  previous_value: number | null;
  absolute_change: number | null;
  percentage_change: number | null;
  is_available: boolean;
  data_quality_flags: string[];
};

export type AnalyticsSummary = {
  period: {
    date_from: string;
    date_to: string;
    timezone: string;
    bucket: "hour" | "day";
  };
  active_calls: AnalyticsMetric;
  attempted_calls: AnalyticsMetric;
  connected_calls: AnalyticsMetric;
  answer_rate: AnalyticsMetric;
  successful_calls: AnalyticsMetric;
  success_rate: AnalyticsMetric;
  success_rate_connected: AnalyticsMetric;
  average_duration_seconds: AnalyticsMetric;
  ai_calls: AnalyticsMetric;
  human_calls: AnalyticsMetric;
  simulator_calls: AnalyticsMetric;
  sip_calls: AnalyticsMetric;
  ai_minutes: AnalyticsMetric;
  ai_cost_usd: AnalyticsMetric;
  callbacks: AnalyticsMetric;
  transfers: {
    requested: AnalyticsMetric;
    successful: AnalyticsMetric;
    failed: AnalyticsMetric;
    success_rate: AnalyticsMetric;
  };
  data_quality_flags: string[];
  telephony_cost_included: boolean;
};

export type AnalyticsDistribution = {
  key: string;
  label: string;
  value: number;
  color: string | null;
};

export type AnalyticsTimePoint = {
  bucket_start: string;
  attempted: number;
  connected: number;
  successful: number;
  ai_calls: number;
  human_calls: number;
};

export type AnalyticsRecentCall = {
  id: string;
  occurred_at: string;
  project_id: string;
  project_name: string;
  customer_name: string | null;
  phone_masked: string | null;
  direction: string;
  caller_type: string;
  channel: string;
  language: string | null;
  duration_seconds: number;
  result_label: string | null;
  result_category: string | null;
  status: string;
  transferred: boolean;
  operator_name: string | null;
  is_test: boolean;
};

export type AnalyticsOverview = {
  summary: AnalyticsSummary;
  timeseries: AnalyticsTimePoint[];
  outcomes: AnalyticsDistribution[];
  languages: AnalyticsDistribution[];
  callers: AnalyticsDistribution[];
  channels: AnalyticsDistribution[];
  tasks: {
    overdue: number;
    today: number;
    future: number;
    completed: number;
    by_type: AnalyticsDistribution[];
  };
  operator_statuses: {
    available: number;
    busy: number;
    on_hold: number;
    away: number;
    on_break: number;
    offline: number;
    active_members: number;
    blocked_members: number;
  };
  recent_calls: {
    items: AnalyticsRecentCall[];
    total: number;
    limit: number;
    offset: number;
  };
};

export type AnalyticsFilterOptions = {
  projects: Array<{
    id: string;
    name: string;
    status: string;
    timezone: string;
  }>;
  operators: Array<{ id: string; name: string }>;
  default_timezone: string;
  max_period_days: number;
  financial_metrics_visible: boolean;
};

export type OperatorPerformancePage = {
  items: Array<{
    operator_id: string;
    operator_name: string;
    project_names: string[];
    attempted: number;
    connected: number;
    successful: number;
    answer_rate: number | null;
    success_rate: number | null;
    average_duration_seconds: number | null;
    transfers: number;
    callbacks: number;
    talk_time_seconds: number;
    is_active: boolean;
  }>;
  total: number;
  limit: number;
  offset: number;
};

export type ProjectPerformancePage = {
  items: Array<{
    project_id: string;
    project_name: string;
    project_status: string;
    attempted: number;
    connected: number;
    successful: number;
    ai_calls: number;
    human_calls: number;
    callbacks: number;
    ai_minutes: number;
    ai_cost_usd: number | null;
    cost_is_available: boolean;
  }>;
  total: number;
  limit: number;
  offset: number;
};

export type TeamMember = {
  membership_id: string;
  user_id: string;
  display_name: string;
  email: string;
  phone: string | null;
  job_title: string | null;
  role: Role;
  is_active: boolean;
  extension: string | null;
  interface_language: string;
  timezone: string | null;
  invited_at: string | null;
  activated_at: string | null;
  last_login_at: string | null;
  last_heartbeat_at: string | null;
  blocked_at: string | null;
  blocked_reason: string | null;
  manual_status: "available" | "away" | "on_break" | "offline";
  effective_status:
    "available" | "away" | "on_break" | "offline" | "busy" | "on_hold";
  is_transfer_available: boolean;
  current_call_id: string | null;
  projects: Array<{ id: string; name: string }>;
  state_version: number;
};

export type TeamInvitation = {
  id: string;
  email: string;
  role: Role;
  status: "pending" | "accepted" | "cancelled" | "expired";
  project_ids: string[];
  expires_at: string;
  issued_at: string;
  accepted_at: string | null;
  cancelled_at: string | null;
  state_version: number;
  created_at: string;
  acceptance_url: string | null;
  acceptance_token: string | null;
};

export type OperatorPresence = {
  manual_status: "available" | "away" | "on_break" | "offline";
  effective_status:
    "available" | "away" | "on_break" | "offline" | "busy" | "on_hold";
  last_heartbeat_at: string | null;
  heartbeat_ttl_seconds: number;
  current_call_id: string | null;
};

export type TransferCandidate = {
  membership_id: string;
  user_id: string;
  display_name: string;
  extension: string | null;
  project_id: string;
  effective_status: "available";
  is_transfer_available: boolean;
};

export type TeamAudit = {
  id: string;
  actor_user_id: string | null;
  action: string;
  reason: string | null;
  safe_metadata: Record<string, unknown>;
  created_at: string;
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

export type AIRealtimeStatus = {
  configured: boolean;
  enabled: boolean;
  provider: "mock" | "openai";
  model: string;
  voice: string;
  live_verification: string;
  last_session_state: string | null;
  last_safe_error: string | null;
  active_sessions: number;
  latency: {
    metric: "first_audio_latency_ms";
    samples: number;
    p50_ms: number | null;
    p95_ms: number | null;
    p99_ms: number | null;
    is_available: boolean;
  };
};

export type AIRealtimeDiagnostic = {
  status: string;
  provider: string;
  inputBytes: number;
  outputBytes: number;
  eventTypes: number;
  vadVerified: boolean;
  transcriptVerified: boolean;
  usageVerified: boolean;
  liveOpenAiVerified: boolean;
};

export type AIRealtimeSession = {
  id: string;
  call_id: string;
  project_id: string;
  state:
    | "pending"
    | "connecting"
    | "active"
    | "reconnecting"
    | "degraded"
    | "closing"
    | "closed"
    | "failed";
  state_version: number;
  provider: string;
  model: string;
  voice: string;
  language_code: string;
  provider_session_id: string | null;
  interruption_count: number;
  usage: Record<string, unknown>;
  latency: Record<string, unknown>;
  safe_error_code: string | null;
  created_at: string;
  connected_at: string | null;
  closed_at: string | null;
};

export type AIRealtimeSessionDetail = {
  session: AIRealtimeSession;
  events: Array<{
    id: string;
    event_type: string;
    aggregate_version: number;
    occurred_at: string;
  }>;
  tools: Array<{
    id: string;
    tool_name: string;
    status: string;
    safe_result: Record<string, unknown> | null;
    duration_ms: number | null;
    created_at: string;
  }>;
  citations: Array<{
    revision_id: string;
    citations: Array<Record<string, unknown>>;
    no_match: boolean;
    latency_ms: number;
    created_at: string;
  }>;
  usage: Array<{
    metric: string;
    quantity: string;
    unit: string;
    provider: string | null;
    model: string | null;
    pricing_available: boolean;
    occurred_at: string;
  }>;
};

export type TelephonyChannelUsage = {
  trunk_id: string;
  name: string;
  pool_mode: "shared" | "separate";
  limit: number;
  inbound_limit: number | null;
  outbound_limit: number | null;
  occupied: number;
  inbound_occupied: number;
  outbound_occupied: number;
  available: number;
};

export type TelephonyStatus = {
  status:
    | "not_configured"
    | "configured"
    | "local_test_passed"
    | "provider_unreachable"
    | "registered"
    | "reachable"
    | "live_signaling_verified"
    | "live_audio_verified"
    | "degraded"
    | "failed";
  deployment_mode: "direct" | "uz_edge";
  media_gateway_placement: "platform" | "edge";
  edge_connectivity: string;
  browser_webrtc: string;
  asterisk: string;
  ari: string;
  sip_trunk: string;
  registration: string;
  reachability: string;
  external_media: string;
  recording: string;
  dids: string[];
  transport: string | null;
  codecs: string[];
  channel_usage: TelephonyChannelUsage[];
  last_checked_at: string | null;
  last_safe_error: string | null;
  live_signaling_verified: boolean;
  live_audio_verified: boolean;
};

export type TelephonyDiagnostic = {
  id: string;
  mode: "local" | "live";
  status: string;
  destination_masked: string | null;
  signaling_verified: boolean;
  inbound_audio_verified: boolean;
  outbound_audio_verified: boolean;
  dtmf_verified: boolean;
  codec: string | null;
  media_statistics: Record<string, unknown>;
  safe_error_code: string | null;
  started_at: string;
  completed_at: string | null;
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

export type LiveTransferAttempt = {
  id: string;
  membership_id: string;
  attempt_number: number;
  destination_type: "browser" | "sip" | "mobile";
  status: string;
  offered_at: string;
  expires_at: string;
  claimed_at: string | null;
  answered_at: string | null;
  safe_error_code: string | null;
};

export type LiveTransfer = {
  id: string;
  call_id: string;
  project_id: string;
  status:
    | "requested"
    | "queued"
    | "offered"
    | "claimed"
    | "connecting"
    | "connected"
    | "declined"
    | "timed_out"
    | "assigned"
    | "completed"
    | "callback_requested"
    | "cancelled"
    | "failed";
  reason: string;
  summary: string;
  language_code: string;
  routing_strategy: string;
  destination_type: "browser" | "sip" | "mobile";
  claimed_membership_id: string | null;
  context: {
    customer_id?: string | null;
    customer_name?: string | null;
    project_id?: string;
    language?: string;
    flow_node_id?: string | null;
    summary?: string;
    transcript_excerpt?: string;
    tools?: string[];
    recent_calls?: Array<{
      id: string;
      status: string;
      started_at: string | null;
    }>;
    related_tasks?: Array<{
      id: string;
      type: string;
      status: string;
      due_at: string;
    }>;
  };
  attempt_count: number;
  max_attempts: number;
  lock_version: number;
  requested_at: string;
  offer_expires_at: string | null;
  claimed_at: string | null;
  connected_at: string | null;
  resolved_at: string | null;
  last_error_code: string | null;
  attempts: LiveTransferAttempt[];
};

export type OperatorWebRtcConfiguration = {
  websocket_url: string;
  sip_uri: string;
  authorization: string;
  expires_at: string;
  ice_servers: RTCIceServer[];
  dtls_srtp_required: boolean;
  register_required: boolean;
  live_verification: string;
};

export type OperatorTransferEndpoint = {
  id: string;
  endpoint_type: "browser" | "sip" | "mobile";
  display_hint: string;
  is_verified: boolean;
  is_enabled: boolean;
  lock_version: number;
};
