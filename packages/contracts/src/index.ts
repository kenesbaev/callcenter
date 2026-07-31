export type LanguageCode = "ru" | "en" | "uz" | "kaa";
export type LanguageReadiness = "production" | "beta" | "experimental";
export type ProviderStatus =
  "unavailable" | "development" | "configured" | "verified";

export type RealtimeSessionConfig = {
  callId: string;
  tenantId: string;
  model: string;
  voice: string;
  instructions: string;
  language: LanguageCode;
  toolDefinitions: RealtimeToolDefinition[];
};

export type RealtimeToolDefinition = {
  name: ToolName;
  description: string;
  inputSchema: Record<string, unknown>;
  timeoutMs: number;
};

export type RealtimeProviderEvent =
  | { type: "session.ready"; providerSessionId: string }
  | { type: "audio.output"; audioBase64: string }
  | { type: "audio.interrupted" }
  | {
      type: "transcript.segment";
      speaker: "customer" | "ai";
      language: LanguageCode;
      text: string;
    }
  | {
      type: "tool.requested";
      callId: string;
      name: ToolName;
      arguments: unknown;
    }
  | { type: "session.error"; code: string; retryable: boolean }
  | { type: "session.closed"; reason: string };

export interface RealtimeVoiceSession {
  readonly providerSessionId: string;
  appendAudio(audio: Uint8Array): Promise<void>;
  sendToolResult(callId: string, result: unknown): Promise<void>;
  interrupt(): Promise<void>;
  close(reason: string): Promise<void>;
}

export interface RealtimeVoiceProvider {
  readonly name: string;
  readonly status: ProviderStatus;
  createSession(
    config: RealtimeSessionConfig,
    onEvent: (event: RealtimeProviderEvent) => Promise<void>,
  ): Promise<RealtimeVoiceSession>;
  shutdown(): Promise<void>;
}

export interface SpeechToTextProvider {
  readonly name: string;
  readonly status: ProviderStatus;
  transcribe(
    input: Uint8Array,
    language: LanguageCode,
  ): Promise<{ text: string; confidence?: number }>;
}

export interface TextToSpeechProvider {
  readonly name: string;
  readonly status: ProviderStatus;
  synthesize(
    text: string,
    language: LanguageCode,
    voice: string,
  ): Promise<Uint8Array>;
}

export const telephonyCommandNames = [
  "originate",
  "answer",
  "hangup",
  "hold",
  "resume",
  "transfer",
  "get_call_state",
  "start_recording",
  "pause_recording",
  "resume_recording",
  "stop_recording",
  "create_external_media",
] as const;

export type TelephonyCommandName = (typeof telephonyCommandNames)[number];
export type TelephonyCallState =
  | "queued"
  | "ringing"
  | "active"
  | "on_hold"
  | "transfer_requested"
  | "transferred"
  | "completed"
  | "busy"
  | "no_answer"
  | "failed"
  | "cancelled"
  | "unknown";

export type TelephonyCommandContext = {
  tenantId: string;
  projectId: string;
  callId: string;
  providerCallId?: string;
  commandId: string;
  idempotencyKey: string;
  timestamp: string;
  correlationId: string;
};

export type TelephonyCommand = TelephonyCommandContext & {
  version: "1";
  command: TelephonyCommandName;
  parameters: Record<string, boolean | number | string | undefined>;
};

export type TelephonyCommandResult = {
  commandId: string;
  provider: string;
  accepted: boolean;
  state: TelephonyCallState;
  providerCallId?: string;
  occurredAt: string;
  safeMetadata: Record<string, boolean | number | string>;
};

export type TelephonyProviderEvent = {
  version: "1";
  provider: string;
  providerEventId: string;
  eventType: string;
  tenantId: string;
  projectId: string;
  callId: string;
  externalCallId?: string;
  occurredAt: string;
  providerTimestamp?: string;
  correlationId: string;
  safePayload: Record<string, boolean | number | string>;
};

export interface TelephonyProvider {
  readonly name: string;
  readonly status: ProviderStatus;
  originate(
    context: TelephonyCommandContext,
    parameters: TelephonyCommand["parameters"],
  ): Promise<TelephonyCommandResult>;
  answer(context: TelephonyCommandContext): Promise<TelephonyCommandResult>;
  hangup(
    context: TelephonyCommandContext,
    reason: string,
  ): Promise<TelephonyCommandResult>;
  hold(context: TelephonyCommandContext): Promise<TelephonyCommandResult>;
  resume(context: TelephonyCommandContext): Promise<TelephonyCommandResult>;
  transfer(
    context: TelephonyCommandContext,
    destination: string,
    reason: string,
  ): Promise<TelephonyCommandResult>;
  getCallState(
    context: TelephonyCommandContext,
  ): Promise<TelephonyCommandResult>;
  startRecording(
    context: TelephonyCommandContext,
  ): Promise<TelephonyCommandResult>;
  pauseRecording(
    context: TelephonyCommandContext,
  ): Promise<TelephonyCommandResult>;
  resumeRecording(
    context: TelephonyCommandContext,
  ): Promise<TelephonyCommandResult>;
  stopRecording(
    context: TelephonyCommandContext,
  ): Promise<TelephonyCommandResult>;
  createExternalMedia(
    context: TelephonyCommandContext,
  ): Promise<TelephonyCommandResult>;
}

export interface CrmProvider {
  readonly name: string;
  readonly status: ProviderStatus;
  execute(
    operation: string,
    input: unknown,
    idempotencyKey: string,
  ): Promise<unknown>;
}

export interface ObjectStorageProvider {
  putPrivate(
    tenantId: string,
    key: string,
    value: Uint8Array,
    contentType: string,
  ): Promise<void>;
  createSignedReadUrl(
    tenantId: string,
    key: string,
    expiresSeconds: number,
  ): Promise<string>;
  delete(tenantId: string, key: string): Promise<void>;
}

export interface NotificationProvider {
  readonly name: string;
  readonly status: ProviderStatus;
  send(
    template: string,
    recipient: string,
    safeData: unknown,
    idempotencyKey: string,
  ): Promise<void>;
}

export const toolNames = [
  "search_knowledge",
  "find_customer",
  "create_customer",
  "create_lead",
  "create_support_ticket",
  "get_order_status",
  "book_appointment",
  "reschedule_appointment",
  "cancel_appointment",
  "send_confirmation",
  "request_human_operator",
  "transfer_call",
  "end_call",
] as const;

export type ToolName = (typeof toolNames)[number];
