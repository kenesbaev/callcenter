import { createHash, randomUUID } from "node:crypto";
import WebSocket, { type RawData } from "ws";
import { z } from "zod";
import type {
  ProviderStatus,
  RealtimeProviderEvent,
  RealtimePlaybackPosition,
  RealtimeSessionConfig,
  RealtimeUsage,
  RealtimeVoiceProvider,
  RealtimeVoiceSession,
  ToolName,
} from "@teamora/contracts";

type OpenAiRealtimeOptions = {
  apiKey: string;
  url?: string;
  connectTimeoutMs?: number;
  maxMessageBytes?: number;
  maxQueueBytes?: number;
};

const providerEventSchema = z
  .object({
    type: z.string().min(1),
    event_id: z.string().max(200).optional(),
  })
  .passthrough();

class OpenAiRealtimeSession implements RealtimeVoiceSession {
  readonly providerSessionId = randomUUID();
  private closed = false;
  constructor(
    private readonly socket: WebSocket,
    private readonly maxQueueBytes: number,
  ) {}

  get isClosed(): boolean {
    return this.closed;
  }

  async updateSession(config: RealtimeSessionConfig): Promise<void> {
    this.send(sessionUpdate(config));
  }

  async appendAudio(audio: Uint8Array): Promise<void> {
    if (audio.byteLength === 0) return;
    this.send({
      type: "input_audio_buffer.append",
      event_id: randomUUID(),
      audio: Buffer.from(audio).toString("base64"),
    });
  }

  async sendText(text: string): Promise<void> {
    this.send({
      type: "response.create",
      event_id: randomUUID(),
      response: {
        instructions: `Say exactly this disclosure, without adding anything: ${text.slice(0, 500)}`,
        output_modalities: ["audio"],
      },
    });
  }

  async sendToolResult(callId: string, result: unknown): Promise<void> {
    this.send({
      type: "conversation.item.create",
      event_id: randomUUID(),
      item: {
        type: "function_call_output",
        call_id: callId,
        output: JSON.stringify(result),
      },
    });
    this.send({ type: "response.create", event_id: randomUUID() });
  }

  async interrupt(itemId?: string, playedAudioMs?: number): Promise<void> {
    await this.interruptPlayback(
      itemId && playedAudioMs !== undefined ? [{ itemId, playedAudioMs }] : [],
    );
  }

  async interruptPlayback(items: RealtimePlaybackPosition[]): Promise<void> {
    this.send({ type: "response.cancel", event_id: randomUUID() });
    const seen = new Set<string>();
    for (const item of items) {
      if (!item.itemId || seen.has(item.itemId)) continue;
      seen.add(item.itemId);
      this.send({
        type: "conversation.item.truncate",
        event_id: randomUUID(),
        item_id: item.itemId,
        content_index: 0,
        audio_end_ms: Math.max(0, Math.floor(item.playedAudioMs)),
      });
    }
  }

  async close(reason: string): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    this.socket.close(1000, reason.slice(0, 120));
  }

  private send(event: unknown): void {
    if (this.closed || this.socket.readyState !== WebSocket.OPEN)
      throw new Error("Realtime session is not open");
    if (this.socket.bufferedAmount > this.maxQueueBytes)
      throw new Error("Realtime provider backpressure limit exceeded");
    this.socket.send(JSON.stringify(event));
  }
}

export class OpenAiRealtimeProvider implements RealtimeVoiceProvider {
  readonly name = "openai-realtime";
  readonly status: ProviderStatus = "configured";
  private readonly sessions = new Set<OpenAiRealtimeSession>();
  constructor(private readonly options: OpenAiRealtimeOptions) {}

  async createSession(
    config: RealtimeSessionConfig,
    onEvent: (event: RealtimeProviderEvent) => Promise<void>,
  ): Promise<RealtimeVoiceSession> {
    const url =
      this.options.url ??
      `wss://api.openai.com/v1/realtime?model=${encodeURIComponent(config.model)}`;
    const socket = new WebSocket(url, {
      headers: {
        Authorization: `Bearer ${this.options.apiKey}`,
        "OpenAI-Safety-Identifier": safetyIdentifier(config),
      },
      maxPayload: this.options.maxMessageBytes ?? 1_048_576,
      handshakeTimeout: this.options.connectTimeoutMs ?? 10_000,
    });
    const session = new OpenAiRealtimeSession(
      socket,
      this.options.maxQueueBytes ?? 4_194_304,
    );
    this.sessions.add(session);
    let readySettled = false;
    let resolveReady!: () => void;
    let rejectReady!: (error: Error) => void;
    const ready = new Promise<void>((resolve, reject) => {
      resolveReady = resolve;
      rejectReady = reject;
    });
    const timeout = setTimeout(() => {
      if (readySettled) return;
      readySettled = true;
      socket.terminate();
      rejectReady(new Error("Realtime session update timed out"));
    }, this.options.connectTimeoutMs ?? 10_000);
    const settleReady = (): void => {
      if (readySettled) return;
      readySettled = true;
      clearTimeout(timeout);
      resolveReady();
    };
    const failReady = (error: Error): void => {
      if (readySettled) return;
      readySettled = true;
      clearTimeout(timeout);
      rejectReady(error);
    };
    let delivery = Promise.resolve();
    socket.on("message", (raw) => {
      delivery = delivery
        .then(async () => {
          const disposition = await this.handleMessage(
            raw,
            config,
            onEvent,
            session.providerSessionId,
          );
          if (disposition === "ready") settleReady();
          if (disposition === "error")
            failReady(new Error("Realtime rejected the session configuration"));
        })
        .catch(() => {
          if (!readySettled) {
            failReady(new Error("Realtime session event delivery failed"));
            return;
          }
          void onEvent({
            type: "session.error",
            code: "provider_event_delivery_failed",
            retryable: true,
          }).catch(() => undefined);
        });
    });
    socket.once("open", () => {
      void session
        .updateSession(config)
        .catch((error: unknown) =>
          failReady(
            error instanceof Error
              ? error
              : new Error("Realtime session update failed"),
          ),
        );
    });
    socket.on("close", (_code, reason) => {
      this.sessions.delete(session);
      if (!readySettled) {
        failReady(new Error("Realtime socket closed before session.updated"));
        return;
      }
      if (session.isClosed) {
        void onEvent({
          type: "session.closed",
          reason: reason.toString() || "provider_closed",
        }).catch(() => undefined);
      } else {
        void onEvent({
          type: "session.error",
          code: "provider_socket_closed",
          retryable: true,
        }).catch(() => undefined);
      }
    });
    socket.on("error", (error) => {
      if (!readySettled) {
        failReady(error);
        return;
      }
      void onEvent({
        type: "session.error",
        code: "provider_socket_error",
        retryable: true,
      }).catch(() => undefined);
    });
    try {
      await ready;
    } catch (error) {
      this.sessions.delete(session);
      if (socket.readyState !== WebSocket.CLOSED) socket.terminate();
      throw error;
    }
    return session;
  }

  async shutdown(): Promise<void> {
    await Promise.all(
      [...this.sessions].map((session) => session.close("gateway_shutdown")),
    );
  }

  private async handleMessage(
    raw: RawData,
    config: RealtimeSessionConfig,
    onEvent: (event: RealtimeProviderEvent) => Promise<void>,
    providerSessionId: string,
  ): Promise<"ready" | "error" | undefined> {
    let unknownEvent: unknown;
    try {
      unknownEvent = JSON.parse(raw.toString());
    } catch {
      await onEvent({
        type: "session.error",
        code: "invalid_provider_json",
        retryable: false,
      });
      return "error";
    }
    const parsed = providerEventSchema.safeParse(unknownEvent);
    if (!parsed.success) {
      await onEvent({
        type: "session.error",
        code: "invalid_provider_event",
        retryable: false,
      });
      return "error";
    }
    const event = parsed.data;
    const type = event.type;
    const eventId = stringValue(event.event_id);
    if (type === "session.updated") {
      await onEvent({
        type: "session.ready",
        providerSessionId,
        eventId,
      });
      return "ready";
    }
    if (type === "response.output_audio.delta") {
      const delta = stringValue(event.delta);
      const itemId = stringValue(event.item_id);
      if (delta && itemId)
        await onEvent({
          type: "audio.output",
          audioBase64: delta,
          itemId,
          eventId,
        });
      return;
    }
    if (type === "input_audio_buffer.speech_started") {
      await onEvent({
        type: "speech.started",
        audioStartMs: numberValue(event.audio_start_ms),
        eventId,
      });
      return;
    }
    if (type === "input_audio_buffer.speech_stopped") {
      await onEvent({
        type: "speech.stopped",
        audioEndMs: numberValue(event.audio_end_ms),
        eventId,
      });
      return;
    }
    if (type === "response.created") {
      const response = recordValue(event.response);
      await onEvent({
        type: "response.started",
        responseId: stringValue(response.id) ?? "unknown",
        eventId,
      });
      return;
    }
    if (type === "response.done") {
      const response = recordValue(event.response);
      await onEvent({
        type: "response.completed",
        responseId: stringValue(response.id) ?? "unknown",
        usage: parseUsage(recordValue(response.usage)),
        eventId,
      });
      return;
    }
    if (
      type === "conversation.item.input_audio_transcription.delta" ||
      type === "conversation.item.input_audio_transcription.completed"
    ) {
      const text = stringValue(event.delta) ?? stringValue(event.transcript);
      if (text)
        await onEvent({
          type: "transcript.segment",
          speaker: "customer",
          language: config.language,
          text,
          itemId: stringValue(event.item_id) ?? "unknown",
          final: type.endsWith("completed"),
          eventId,
        });
      return;
    }
    if (
      type === "response.output_audio_transcript.delta" ||
      type === "response.output_audio_transcript.done"
    ) {
      const text = stringValue(event.delta) ?? stringValue(event.transcript);
      if (text)
        await onEvent({
          type: "transcript.segment",
          speaker: "ai",
          language: config.language,
          text,
          itemId: stringValue(event.item_id) ?? "unknown",
          final: type.endsWith("done"),
          eventId,
        });
      return;
    }
    if (type === "response.function_call_arguments.done") {
      const name = stringValue(event.name);
      const callId = stringValue(event.call_id);
      if (!name || !callId) return;
      let argumentsValue: unknown = null;
      try {
        argumentsValue = JSON.parse(stringValue(event.arguments) ?? "{}");
      } catch {
        /* invalid args are rejected by ToolExecutor */
      }
      await onEvent({
        type: "tool.requested",
        callId,
        name: name as ToolName,
        arguments: argumentsValue,
        eventId,
      });
      return;
    }
    if (type === "rate_limits.updated") {
      const limits = Array.isArray(event.rate_limits) ? event.rate_limits : [];
      const first = recordValue(limits[0]);
      await onEvent({
        type: "rate_limits.updated",
        remaining: numberValue(first.remaining) ?? 0,
        resetSeconds: numberValue(first.reset_seconds),
        eventId,
      });
      return;
    }
    if (type === "error") {
      const detail = recordValue(event.error);
      const code = stringValue(detail.code) ?? "provider_error";
      await onEvent({
        type: "session.error",
        code: safeCode(code),
        retryable: [
          "rate_limit_exceeded",
          "server_error",
          "service_unavailable",
        ].includes(code),
        eventId,
      });
      return "error";
    }
    return undefined;
  }
}

function sessionUpdate(config: RealtimeSessionConfig): Record<string, unknown> {
  const vad = config.vad ?? {
    type: "server_vad" as const,
    threshold: 0.5,
    prefixPaddingMs: 300,
    silenceDurationMs: 700,
    idleTimeoutMs: 30_000,
  };
  const turnDetection =
    vad.type === "server_vad"
      ? {
          type: "server_vad",
          threshold: vad.threshold,
          prefix_padding_ms: vad.prefixPaddingMs,
          silence_duration_ms: vad.silenceDurationMs,
          idle_timeout_ms: vad.idleTimeoutMs,
          create_response: true,
          interrupt_response: true,
        }
      : {
          type: "semantic_vad",
          eagerness: vad.eagerness,
          create_response: true,
          interrupt_response: true,
        };
  return {
    type: "session.update",
    event_id: randomUUID(),
    session: {
      type: "realtime",
      model: config.model,
      instructions: config.instructions,
      output_modalities: ["audio"],
      audio: {
        input: {
          format: {
            type: config.inputAudioFormat?.type ?? "audio/pcm",
            rate: config.inputAudioFormat?.rate ?? 24_000,
          },
          turn_detection: turnDetection,
          transcription: {
            model: "gpt-4o-mini-transcribe",
            language: config.language,
          },
        },
        output: {
          format: {
            type: config.outputAudioFormat?.type ?? "audio/pcm",
            rate: config.outputAudioFormat?.rate ?? 24_000,
          },
          voice: config.voice,
        },
      },
      tools: config.toolDefinitions.map((tool) => ({
        type: "function",
        name: tool.name,
        description: tool.description,
        parameters: tool.inputSchema,
      })),
      tool_choice: "auto",
      ...(config.reasoningEffort
        ? { reasoning: { effort: config.reasoningEffort } }
        : {}),
    },
  };
}

function parseUsage(value: Record<string, unknown>): RealtimeUsage | undefined {
  if (!Object.keys(value).length) return undefined;
  const input = recordValue(value.input_token_details);
  const output = recordValue(value.output_token_details);
  const cached = recordValue(input.cached_tokens_details);
  return {
    inputAudioTokens: numberValue(input.audio_tokens),
    outputAudioTokens: numberValue(output.audio_tokens),
    inputTextTokens: numberValue(input.text_tokens),
    outputTextTokens: numberValue(output.text_tokens),
    cachedAudioTokens: numberValue(cached.audio_tokens),
    cachedTextTokens: numberValue(cached.text_tokens),
    cachedTokens: numberValue(input.cached_tokens),
    totalTokens: numberValue(value.total_tokens),
  };
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
function stringValue(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}
function numberValue(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : undefined;
}
function safeCode(value: string): string {
  return (
    value.replace(/[^a-zA-Z0-9_.-]/g, "_").slice(0, 80) || "provider_error"
  );
}

function safetyIdentifier(config: RealtimeSessionConfig): string {
  const configured = config.safetyIdentifier?.trim();
  if (configured) return configured;
  return `cc_${createHash("sha256")
    .update(`${config.tenantId}:${config.callId}`)
    .digest("hex")}`;
}
