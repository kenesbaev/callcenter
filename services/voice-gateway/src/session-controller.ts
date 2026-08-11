import type {
  RealtimeProviderEvent,
  RealtimeSessionConfig,
  RealtimeUsage,
  RealtimeVoiceProvider,
  RealtimeVoiceSession,
  ToolName,
} from "@teamora/contracts";
import { ToolExecutionError, type ToolExecutor } from "./tool-executor.js";
import { activeCalls, failedTools, providerLatency } from "./metrics.js";

export type SessionState =
  | "pending"
  | "connecting"
  | "active"
  | "reconnecting"
  | "degraded"
  | "closing"
  | "closed"
  | "failed";

export type SafeSessionEvent = {
  type: string;
  callId: string;
  tenantId: string;
  projectId: string;
  providerEventId?: string | undefined;
  occurredAt: string;
  safeData?: Record<string, boolean | number | string> | undefined;
};

type ControllerOptions = {
  sessionId?: string;
  channelId?: string;
  telephony?: unknown;
  callId: string;
  tenantId: string;
  projectId: string;
  config: RealtimeSessionConfig;
  provider: RealtimeVoiceProvider;
  tools: ToolExecutor;
  emit: (event: SafeSessionEvent) => Promise<void>;
  writeAudio: (audio: Uint8Array) => Promise<void>;
  clearPlayback?: () => Promise<void>;
  maxReconnects?: number;
  reconnectBaseMs?: number;
  maxPlaybackBytes?: number;
};

export class VoiceSessionController {
  private currentState: SessionState = "pending";
  private providerSession?: RealtimeVoiceSession;
  private reconnects = 0;
  private readonly seenEvents = new Set<string>();
  private currentOutputItem: string | undefined;
  private playedAudioMs = 0;
  private queuedAudioBytes = 0;
  private responseStartedAt?: number;
  private speechStoppedAt?: number;
  private firstAudioAt: number | undefined;
  private countedActive = false;

  constructor(private readonly options: ControllerOptions) {}
  get state(): SessionState {
    return this.currentState;
  }

  async start(): Promise<void> {
    if (this.currentState !== "pending")
      throw new Error("Session can only start once");
    this.currentState = "connecting";
    await this.emit("ai.session_connecting");
    await this.connect();
  }

  async appendAudio(audio: Uint8Array): Promise<void> {
    if (this.currentState !== "active" || !this.providerSession) return;
    await this.providerSession.appendAudio(audio);
  }

  async playDisclosure(text: string): Promise<void> {
    if (this.currentState !== "active" || !this.providerSession)
      throw new Error("AI session is not active");
    if (!this.providerSession.sendText)
      throw new Error("Realtime provider does not support prompted speech");
    await this.providerSession.sendText(text);
    await this.emit("ai.disclosure_played", { disclosure: true });
  }

  async customerSpeechStarted(): Promise<void> {
    await this.interruptPlayback();
  }

  async transfer(_queue: string, reason: string): Promise<void> {
    await this.requestHumanTransfer(reason);
  }

  async requestHumanTransfer(reason: string): Promise<void> {
    const safeReason = reason.slice(0, 500);
    const allowed = new Set(
      this.options.config.toolDefinitions.map((definition) => definition.name),
    );
    const candidates: Array<{
      name: ToolName;
      arguments: Record<string, unknown>;
    }> = [
      { name: "request_human_transfer", arguments: { reason: safeReason } },
      {
        name: "create_callback",
        arguments: {
          title: "AI voice fallback",
          description: safeReason,
          confirmed: true,
        },
      },
      {
        name: "create_task",
        arguments: {
          title: "AI voice fallback",
          description: safeReason,
          confirmed: true,
        },
      },
    ];
    for (const candidate of candidates) {
      if (!allowed.has(candidate.name)) continue;
      try {
        await this.options.tools.execute(
          {
            tenantId: this.options.tenantId,
            projectId: this.options.projectId,
            callId: this.options.callId,
            sessionId: this.options.sessionId ?? this.options.callId,
            correlationId:
              this.options.config.correlationId ?? this.options.callId,
            allowedTools: [...allowed] as ToolName[],
          },
          candidate.name,
          candidate.arguments,
          `${this.options.callId}:fallback:${candidate.name}`,
        );
        await this.emit("ai.tool_completed", {
          tool: candidate.name,
          fallback: true,
        });
        return;
      } catch (error) {
        failedTools.labels(safeCode(error)).inc();
      }
    }
    await this.emit("transfer.requested", { reason: safeReason });
  }

  async close(reason: string): Promise<void> {
    if (["closed", "closing"].includes(this.currentState)) return;
    this.currentState = "closing";
    await this.providerSession?.close(reason);
    this.currentState = "closed";
    if (this.countedActive) {
      activeCalls.dec();
      this.countedActive = false;
    }
    await this.emit("ai.session_closed", { reason: reason.slice(0, 120) });
  }

  private async connect(): Promise<void> {
    try {
      this.providerSession = await this.options.provider.createSession(
        this.options.config,
        (event) => this.handleProviderEvent(event),
      );
      this.currentState = "active";
      if (!this.countedActive) {
        activeCalls.inc();
        this.countedActive = true;
      }
      await this.emit("ai.session_started", {
        provider: this.options.provider.name,
        provider_session_id: this.providerSession.providerSessionId,
      });
    } catch (error) {
      await this.handleConnectionFailure(error);
    }
  }

  private async handleConnectionFailure(error: unknown): Promise<void> {
    if (this.reconnects >= (this.options.maxReconnects ?? 2)) {
      this.currentState = "failed";
      if (this.countedActive) {
        activeCalls.dec();
        this.countedActive = false;
      }
      await this.emit("ai.session_failed", { code: safeCode(error) });
      await this.requestHumanTransfer("ai_provider_unavailable");
      return;
    }
    this.reconnects += 1;
    this.currentState = "reconnecting";
    await this.emit("ai.session_degraded", {
      attempt: this.reconnects,
      code: safeCode(error),
    });
    const base = this.options.reconnectBaseMs ?? 250;
    const delay = Math.min(5_000, base * 2 ** (this.reconnects - 1));
    await new Promise((resolve) => setTimeout(resolve, delay));
    await this.connect();
  }

  private async handleProviderEvent(
    event: RealtimeProviderEvent,
  ): Promise<void> {
    if (event.eventId) {
      if (this.seenEvents.has(event.eventId)) return;
      this.seenEvents.add(event.eventId);
      if (this.seenEvents.size > 4_000)
        this.seenEvents.delete(this.seenEvents.values().next().value as string);
    }
    if (event.type === "audio.output") {
      const audio = Buffer.from(event.audioBase64, "base64");
      if (
        this.queuedAudioBytes + audio.byteLength >
        (this.options.maxPlaybackBytes ?? 4_194_304)
      ) {
        this.currentState = "degraded";
        await this.emit("ai.session_degraded", {
          code: "playback_backpressure",
        });
        return;
      }
      if (this.firstAudioAt === undefined) {
        this.firstAudioAt = Date.now();
        if (this.speechStoppedAt !== undefined) {
          providerLatency
            .labels("first_audio")
            .observe((this.firstAudioAt - this.speechStoppedAt) / 1000);
        }
      }
      this.currentOutputItem = event.itemId;
      this.queuedAudioBytes += audio.byteLength;
      try {
        await this.options.writeAudio(audio);
        this.playedAudioMs += audio.byteLength / 48;
      } finally {
        this.queuedAudioBytes -= audio.byteLength;
      }
      return;
    }
    if (event.type === "speech.started") {
      await this.interruptPlayback();
      await this.emit(
        "ai.listening",
        { audio_start_ms: event.audioStartMs ?? 0 },
        event.eventId,
      );
      return;
    }
    if (event.type === "speech.stopped") {
      this.speechStoppedAt = Date.now();
      await this.emit(
        "ai.listening",
        { stopped: true, audio_end_ms: event.audioEndMs ?? 0 },
        event.eventId,
      );
      return;
    }
    if (event.type === "response.started") {
      this.responseStartedAt = Date.now();
      this.firstAudioAt = undefined;
      await this.emit(
        "ai.speaking",
        { response_id: event.responseId },
        event.eventId,
      );
      return;
    }
    if (event.type === "response.completed") {
      if (this.responseStartedAt) {
        providerLatency
          .labels("response_complete")
          .observe((Date.now() - this.responseStartedAt) / 1000);
      }
      await this.emitUsage(event.usage, event.eventId);
      await this.emit(
        "ai.response_completed",
        {
          response_id: event.responseId,
          total_latency_ms: this.responseStartedAt
            ? Date.now() - this.responseStartedAt
            : 0,
          first_audio_latency_ms:
            this.speechStoppedAt !== undefined &&
            this.firstAudioAt !== undefined
              ? this.firstAudioAt - this.speechStoppedAt
              : 0,
        },
        event.eventId,
      );
      return;
    }
    if (event.type === "transcript.segment") {
      await this.emit(
        "ai.transcript_updated",
        {
          speaker: event.speaker,
          item_id: event.itemId,
          language: event.language,
          final: event.final,
          text: event.text.slice(0, 20_000),
        },
        event.eventId,
      );
      return;
    }
    if (event.type === "tool.requested" && this.providerSession) {
      await this.emit("ai.tool_started", { tool: event.name }, event.eventId);
      let result: unknown;
      try {
        result = await this.options.tools.execute(
          {
            tenantId: this.options.tenantId,
            projectId: this.options.projectId,
            callId: this.options.callId,
            sessionId: this.options.sessionId ?? this.options.callId,
            correlationId:
              this.options.config.correlationId ?? this.options.callId,
            allowedTools: this.options.config.toolDefinitions.map(
              (definition) => definition.name as ToolName,
            ),
          },
          event.name,
          event.arguments,
          `${this.options.callId}:${event.callId}`,
        );
      } catch (error) {
        const code =
          error instanceof ToolExecutionError ? error.code : "execution_failed";
        failedTools.labels(code).inc();
        result = { ok: false, error: code };
      }
      await this.providerSession.sendToolResult(event.callId, result);
      await this.emit("ai.tool_completed", { tool: event.name }, event.eventId);
      return;
    }
    if (event.type === "session.error") {
      if (event.retryable && this.currentState === "active") {
        await this.providerSession?.close("provider_reconnect");
        await this.handleConnectionFailure(new Error(event.code));
      } else {
        this.currentState = "failed";
        if (this.countedActive) {
          activeCalls.dec();
          this.countedActive = false;
        }
        await this.emit(
          "ai.session_failed",
          { code: event.code },
          event.eventId,
        );
        await this.requestHumanTransfer(event.code);
      }
      return;
    }
    if (event.type === "audio.interrupted") {
      await this.emit(
        "ai.interrupted",
        { item_id: event.itemId ?? this.currentOutputItem ?? "unknown" },
        event.eventId,
      );
    }
  }

  private async interruptPlayback(): Promise<void> {
    if (!this.providerSession || !this.currentOutputItem) return;
    const itemId = this.currentOutputItem;
    const played = this.playedAudioMs;
    this.currentOutputItem = undefined;
    this.playedAudioMs = 0;
    this.queuedAudioBytes = 0;
    await this.options.clearPlayback?.();
    await this.providerSession.interrupt(itemId, played);
    await this.emit("ai.interrupted", {
      item_id: itemId,
      played_audio_ms: Math.round(played),
    });
  }

  private async emitUsage(
    usage?: RealtimeUsage,
    providerEventId?: string,
  ): Promise<void> {
    if (!usage) return;
    await this.emit(
      "ai.usage_updated",
      {
        input_audio_tokens: usage.inputAudioTokens ?? 0,
        output_audio_tokens: usage.outputAudioTokens ?? 0,
        input_text_tokens: usage.inputTextTokens ?? 0,
        output_text_tokens: usage.outputTextTokens ?? 0,
        cached_tokens: usage.cachedTokens ?? 0,
        total_tokens: usage.totalTokens ?? 0,
      },
      providerEventId,
    );
  }

  private async emit(
    type: string,
    safeData?: Record<string, boolean | number | string>,
    providerEventId?: string,
  ): Promise<void> {
    await this.options.emit({
      type,
      callId: this.options.callId,
      tenantId: this.options.tenantId,
      projectId: this.options.projectId,
      providerEventId,
      occurredAt: new Date().toISOString(),
      safeData,
    });
  }
}

function safeCode(error: unknown): string {
  const value = error instanceof Error ? error.name : "connection_error";
  return (
    value.replace(/[^a-zA-Z0-9_.-]/g, "_").slice(0, 80) || "connection_error"
  );
}
