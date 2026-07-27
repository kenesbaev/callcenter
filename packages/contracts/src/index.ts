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

export interface TelephonyProvider {
  readonly name: string;
  answer(channelId: string): Promise<void>;
  createExternalMedia(
    callId: string,
    channelId: string,
  ): Promise<{ mediaId: string }>;
  transfer(channelId: string, queue: string, reason: string): Promise<void>;
  hangup(channelId: string, reason: string): Promise<void>;
  pauseRecording(channelId: string): Promise<void>;
  resumeRecording(channelId: string): Promise<void>;
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
