export type LanguageCode = string;
export type LanguageReadiness = "production" | "beta" | "experimental";
export type ProviderStatus =
  "unavailable" | "development" | "configured" | "verified";

export type RealtimeSessionConfig = {
  callId: string;
  tenantId: string;
  projectId?: string | undefined;
  correlationId?: string | undefined;
  model: string;
  voice: string;
  instructions: string;
  language: LanguageCode;
  toolDefinitions: RealtimeToolDefinition[];
  vad?: RealtimeVadConfig | undefined;
  inputAudioFormat?: RealtimeAudioFormat | undefined;
  outputAudioFormat?: RealtimeAudioFormat | undefined;
  reasoningEffort?: "low" | "medium" | "high" | undefined;
  /** Privacy-preserving identifier used only on the trusted provider connection. */
  safetyIdentifier?: string | undefined;
};

export type RealtimeVadConfig =
  | {
      type: "server_vad";
      threshold: number;
      prefixPaddingMs: number;
      silenceDurationMs: number;
      idleTimeoutMs?: number | undefined;
    }
  | { type: "semantic_vad"; eagerness: "low" | "medium" | "high" | "auto" };

export type RealtimeAudioFormat = {
  type: "audio/pcm";
  rate: 24_000;
  channels: 1;
};

export type RealtimeToolDefinition = {
  name: ToolName;
  description: string;
  inputSchema: Record<string, unknown>;
  timeoutMs: number;
};

export type RealtimeProviderEvent =
  | {
      type: "session.ready";
      providerSessionId: string;
      eventId?: string | undefined;
    }
  | {
      type: "audio.output";
      audioBase64: string;
      itemId: string;
      eventId?: string | undefined;
    }
  | {
      type: "audio.interrupted";
      itemId?: string | undefined;
      eventId?: string | undefined;
    }
  | {
      type: "speech.started";
      audioStartMs?: number | undefined;
      eventId?: string | undefined;
    }
  | {
      type: "speech.stopped";
      audioEndMs?: number | undefined;
      eventId?: string | undefined;
    }
  | {
      type: "response.started";
      responseId: string;
      eventId?: string | undefined;
    }
  | {
      type: "response.completed";
      responseId: string;
      usage?: RealtimeUsage | undefined;
      eventId?: string | undefined;
    }
  | {
      type: "transcript.segment";
      speaker: "customer" | "ai";
      language: LanguageCode;
      text: string;
      itemId: string;
      final: boolean;
      eventId?: string | undefined;
    }
  | {
      type: "tool.requested";
      callId: string;
      name: ToolName;
      arguments: unknown;
      eventId?: string | undefined;
    }
  | {
      type: "rate_limits.updated";
      remaining: number;
      resetSeconds?: number | undefined;
      eventId?: string | undefined;
    }
  | {
      type: "session.error";
      code: string;
      retryable: boolean;
      eventId?: string | undefined;
    }
  | { type: "session.closed"; reason: string; eventId?: string | undefined };

export type RealtimeUsage = {
  inputAudioTokens?: number | undefined;
  outputAudioTokens?: number | undefined;
  inputTextTokens?: number | undefined;
  outputTextTokens?: number | undefined;
  cachedAudioTokens?: number | undefined;
  cachedTextTokens?: number | undefined;
  cachedTokens?: number | undefined;
  totalTokens?: number | undefined;
};

export type RealtimePlaybackPosition = {
  itemId: string;
  playedAudioMs: number;
};

export interface RealtimeVoiceSession {
  readonly providerSessionId: string;
  updateSession?(config: RealtimeSessionConfig): Promise<void>;
  sendText?(text: string): Promise<void>;
  appendAudio(audio: Uint8Array): Promise<void>;
  sendToolResult(callId: string, result: unknown): Promise<void>;
  interrupt(itemId?: string, playedAudioMs?: number): Promise<void>;
  interruptPlayback?(items: RealtimePlaybackPosition[]): Promise<void>;
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
  "advance_call_flow",
  "search_knowledge",
  "get_customer",
  "update_customer_field",
  "create_task",
  "create_callback",
  "submit_call_result",
  "request_human_transfer",
  "end_conversation",
  "wait_for_user",
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
