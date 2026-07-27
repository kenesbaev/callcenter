import { randomUUID } from "node:crypto";
import WebSocket, { type RawData } from "ws";
import type {
  ProviderStatus,
  RealtimeProviderEvent,
  RealtimeSessionConfig,
  RealtimeVoiceProvider,
  RealtimeVoiceSession,
  ToolName,
} from "@teamora/contracts";

type OpenAiRealtimeOptions = {
  apiKey: string;
  url?: string;
  connectTimeoutMs?: number;
};

class OpenAiRealtimeSession implements RealtimeVoiceSession {
  readonly providerSessionId = randomUUID();
  private closed = false;
  constructor(private readonly socket: WebSocket) {}

  async appendAudio(audio: Uint8Array): Promise<void> {
    this.send({
      type: "input_audio_buffer.append",
      audio: Buffer.from(audio).toString("base64"),
    });
  }
  async sendToolResult(callId: string, result: unknown): Promise<void> {
    this.send({
      type: "conversation.item.create",
      item: {
        type: "function_call_output",
        call_id: callId,
        output: JSON.stringify(result),
      },
    });
    this.send({ type: "response.create" });
  }
  async interrupt(): Promise<void> {
    this.send({ type: "response.cancel" });
    this.send({ type: "output_audio_buffer.clear" });
  }
  async close(reason: string): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    this.socket.close(1000, reason.slice(0, 120));
  }
  private send(event: unknown): void {
    if (this.closed || this.socket.readyState !== WebSocket.OPEN)
      throw new Error("Realtime session is not open");
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
      headers: { Authorization: `Bearer ${this.options.apiKey}` },
    });
    const session = new OpenAiRealtimeSession(socket);
    this.sessions.add(session);
    await new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => {
        socket.terminate();
        reject(new Error("Realtime connection timed out"));
      }, this.options.connectTimeoutMs ?? 10_000);
      socket.once("open", () => {
        clearTimeout(timeout);
        socket.send(
          JSON.stringify({
            type: "session.update",
            session: {
              type: "realtime",
              instructions: config.instructions,
              audio: {
                input: {
                  format: { type: "audio/pcm", rate: 24000 },
                  turn_detection: {
                    type: "server_vad",
                    create_response: true,
                    interrupt_response: true,
                  },
                  transcription: {
                    model: "gpt-4o-mini-transcribe",
                    language: config.language,
                  },
                },
                output: {
                  format: { type: "audio/pcm", rate: 24000 },
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
            },
          }),
        );
        void onEvent({
          type: "session.ready",
          providerSessionId: session.providerSessionId,
        });
        resolve();
      });
      socket.once("error", (error) => {
        clearTimeout(timeout);
        reject(error);
      });
    });
    socket.on(
      "message",
      (raw) => void this.handleMessage(raw, config, onEvent),
    );
    socket.on("close", (_code, reason) => {
      this.sessions.delete(session);
      void onEvent({
        type: "session.closed",
        reason: reason.toString() || "provider_closed",
      });
    });
    socket.on(
      "error",
      () =>
        void onEvent({
          type: "session.error",
          code: "provider_socket_error",
          retryable: true,
        }),
    );
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
  ): Promise<void> {
    let event: Record<string, unknown>;
    try {
      event = JSON.parse(raw.toString()) as Record<string, unknown>;
    } catch {
      await onEvent({
        type: "session.error",
        code: "invalid_provider_event",
        retryable: false,
      });
      return;
    }
    const type = String(event.type ?? "");
    if (
      type === "response.output_audio.delta" &&
      typeof event.delta === "string"
    )
      await onEvent({ type: "audio.output", audioBase64: event.delta });
    else if (type === "input_audio_buffer.speech_started")
      await onEvent({ type: "audio.interrupted" });
    else if (
      type === "conversation.item.input_audio_transcription.completed" &&
      typeof event.transcript === "string"
    )
      await onEvent({
        type: "transcript.segment",
        speaker: "customer",
        language: config.language,
        text: event.transcript,
      });
    else if (
      type === "response.output_audio_transcript.done" &&
      typeof event.transcript === "string"
    )
      await onEvent({
        type: "transcript.segment",
        speaker: "ai",
        language: config.language,
        text: event.transcript,
      });
    else if (
      type === "response.function_call_arguments.done" &&
      typeof event.name === "string" &&
      typeof event.call_id === "string"
    ) {
      let argumentsValue: unknown;
      try {
        argumentsValue = JSON.parse(String(event.arguments ?? "{}"));
      } catch {
        argumentsValue = null;
      }
      await onEvent({
        type: "tool.requested",
        callId: event.call_id,
        name: event.name as ToolName,
        arguments: argumentsValue,
      });
    } else if (type === "error")
      await onEvent({
        type: "session.error",
        code: "provider_error",
        retryable: false,
      });
  }
}
